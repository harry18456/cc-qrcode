"""Resolve every `auto` value, apply the fixed repair order, and run all gates.

Decision policy (fixed at design time, never improvised at run time):
  1. Colors: check worst-case contrast; if allowed, darken dark colors (then lighten the light color).
  2. Symbol: try (version, EC) candidates smallest-version first, EC raised as far as that version allows.
  3. Within a candidate: size the center image (largest that fits the budget), place decorations,
     pick the mask that costs the least budget, then re-grow the center image for that mask.
  4. Gates: budget, structure, finder geometry, contrast, clean decode, physical size, robustness.
  5. Robustness repair: if modules.size is auto, enlarge modules toward 1.0 and re-verify.
  6. No candidate passes -> FAIL with the smallest relaxations that would make it pass.
"""
import math

import numpy as np

from . import analyze as A
from . import center as C
from . import verify as V
from .assets import Asset
from .payload import make_payload
from .render import baseline_svg, render_svg
from .structure import EC_ORDER, Structure, candidates, max_payload_bytes

SHAPE_DEFAULT_SIZE = {'square': 1.0, 'rounded': 0.92, 'squircle': 0.92, 'dot': 0.86, 'connected': 0.86,
                      'diamond': 1.0, 'leaf': 0.92}
FORBIDDEN = ['finder', 'separator', 'timing', 'format', 'version', 'alignment']
ANALYSIS_PX = 12
FINDER_PX = 24


# --------------------------------------------------------------------------- colors
def resolve_colors(spec, center_asset, th):
    col = spec['colors']
    notes = []
    minc = th['min_symbol_contrast']
    light = col['light'].upper()

    def fix(c, label):
        worst, inv, _ = A.color_contrast([c], light)
        if inv and not col['allow_inverted']:
            return c, False, f'{label} {c} is lighter than the light color {light} (inverted code)'
        if worst >= minc:
            return c, True, None
        if not col['auto_repair']:
            return c, False, f'{label} {c}: contrast {worst:.2f} < {minc}'
        orig = c
        for _ in range(60):
            c = A.adjust_lightness(c, 0.93)
            if A.color_contrast([c], light)[0] >= minc:
                notes.append(f'{label} darkened {orig} -> {c} to reach contrast {minc}')
                return c, True, None
        return c, False, f'{label} {orig}: cannot reach contrast {minc} against {light}'

    problems = []
    dark = col['dark']
    if dark == 'auto_from_logo':
        cands = center_asset.dominant_colors() if center_asset else []
        chosen = None
        for c in cands:
            if A.color_contrast([c], light)[0] >= minc:
                chosen = c
                break
        if chosen is None and cands:
            chosen, ok, msg = fix(cands[0], 'dark (from logo)')
            if not ok:
                problems.append(msg)
        chosen = chosen or '#000000'
        notes.append(f'dark color taken from the center image: {chosen} (candidates {cands})')
        dark = chosen
    if isinstance(dark, dict):
        stops = []
        for i, c in enumerate(dark['gradient']):
            c2, ok, msg = fix(c, f'gradient stop {i}')
            stops.append(c2)
            if not ok:
                problems.append(msg)
        dark = {'gradient': stops, 'angle': dark.get('angle', 0)}
        dark_list = stops
    else:
        dark, ok, msg = fix(dark, 'dark')
        if not ok:
            problems.append(msg)
        dark_list = [dark]
    finder_color = spec['finder'].get('color')
    if finder_color:
        finder_color, ok, msg = fix(finder_color, 'finder color')
        if not ok:
            problems.append(msg)
        dark_list = dark_list + [finder_color]
    worst, inverted, detail = A.color_contrast(dark_list, light)
    return ({'dark': dark, 'light': light, 'finder': finder_color}, dark_list, worst,
            problems, notes, detail)


# --------------------------------------------------------------------------- layout
def _cells_intersecting_rect(n, box):
    x0, y0, x1, y1 = box
    m = np.zeros((n, n), bool)
    r0, r1 = max(int(math.floor(y0 + 1e-6)), 0), min(int(math.ceil(y1 - 1e-6)), n)
    c0, c1 = max(int(math.floor(x0 + 1e-6)), 0), min(int(math.ceil(x1 - 1e-6)), n)
    if r1 > r0 and c1 > c0:
        m[r0:r1, c0:c1] = True
    return m


def forbidden_hit(struct, mask, allow_central=False):
    hit = mask & np.isin(struct.role, FORBIDDEN)
    if allow_central:
        hit &= ~struct.central_alignment_mask()
    return bool(hit.any())


ANCHOR_POINTS = {'top-right': (0.2, 0.8), 'bottom-left': (0.8, 0.2), 'bottom-right': (0.82, 0.82),
                 'top': (0.18, 0.5), 'bottom': (0.82, 0.5), 'left': (0.5, 0.18), 'right': (0.5, 0.82)}


def place_decorations(struct, decs, base_cleared, base_overlay, reserved, th):
    """Explicit positions are validated; 'auto' positions minimize budget cost near the anchor."""
    n = struct.n
    cleared, overlay = base_cleared.copy(), base_overlay.copy()
    placed, problems = [], []
    for i, d in enumerate(decs):
        s = d['size']
        mode = d.get('mode', 'clear')
        pos = d.get('position', 'auto')
        if pos != 'auto':
            r, c = pos
            box = (c - s / 2, r - s / 2, c + s / 2, r + s / 2)
            m = _cells_intersecting_rect(n, box)
            if forbidden_hit(struct, m) or (m & reserved).any():
                problems.append(f'decorations[{i}] at {pos} covers structural modules or the center image')
        else:
            ar, ac = ANCHOR_POINTS[d.get('anchor') or 'bottom-right']
            best = None
            span = int(math.ceil(s))
            for i0 in range(0, n - span + 1):
                for j0 in range(0, n - span + 1):
                    box = (j0, i0, j0 + s, i0 + s)
                    m = _cells_intersecting_rect(n, box)
                    if forbidden_hit(struct, m) or (m & (reserved | cleared | overlay)).any():
                        continue
                    if mode == 'clear':
                        _, u = struct.static_budget(cleared | m, overlay)
                    else:
                        _, u = struct.static_budget(cleared, overlay | m)
                    r, c = i0 + s / 2, j0 + s / 2
                    dist = math.hypot(r / n - ar, c / n - ac)
                    # nearest to the anchor among positions that keep the budget; if none keeps it,
                    # the cheapest position (the gate will then report the overrun)
                    feasible = u <= th['max_block_usage']
                    key = (0, round(dist, 3), round(u, 3)) if feasible else (1, round(u, 3), round(dist, 3))
                    if best is None or key < best[0]:
                        best = (key, (r, c), box, m)
            if best is None:
                problems.append(f'decorations[{i}]: no free position for size {s}')
                continue
            (_, (r, c), box, m) = best
        if mode == 'clear':
            cleared |= m
        else:
            overlay |= m
        placed.append({'asset': d['asset'], 'box': box, 'mode': mode, 'position': [r, c], 'size': s})
    return placed, cleared, overlay, problems


def plan(struct, spec, assets, th, choice):
    """Geometry for a given symbol and center-image choice {fraction, plate, padding}."""
    n = struct.n
    cleared = np.zeros((n, n), bool)
    overlay = np.zeros((n, n), bool)
    center, problems = None, []
    center_cleared = np.zeros((n, n), bool)
    if assets['center'] is not None and choice:
        g = C.geometry(n, assets['center'], choice['fraction'], choice['plate'], choice['padding'])
        cm = g['cleared']
        if forbidden_hit(struct, cm, allow_central=th.get('allow_center_alignment_cover', True)):
            problems.append('center image covers structural modules (alignment/timing)')
        cleared |= cm
        center_cleared = cm.copy()
        center = {'asset': assets['center'], 'logo_box': g['logo_box'], 'plate': g['plate'],
                  'plate_kind': choice['plate'], 'padding': choice['padding'], 'fraction': choice['fraction']}
    decs, cleared, overlay, p2 = place_decorations(struct, assets['decorations'], cleared, overlay,
                                                   center_cleared, th)
    problems += p2
    return {'center': center, 'decorations': decs, 'cleared': cleared, 'overlay': overlay,
            'center_cleared': center_cleared, 'background': assets['background']}, problems


def _max_fraction(struct, spec, assets, th, plate, padding, hi, lo):
    """Largest fraction in [lo, hi] (0.01 steps) that keeps the static budget; None if even lo fails."""
    f = hi
    while f >= lo - 1e-9:
        ch = {'fraction': round(f, 3), 'plate': plate, 'padding': padding}
        lay, probs = plan(struct, spec, assets, th, ch)
        _, u = struct.static_budget(lay['cleared'], lay['overlay'])
        if not probs and u <= th['max_block_usage']:
            return round(f, 3)
        f -= 0.01
    return None


def choose_center(struct, spec, assets, th):
    """Pick (fraction, plate, padding): the first combo, in aesthetic order, that reaches the preferred
    size; otherwise the combo that allows the largest image. User-fixed values are never changed."""
    ce = spec['center']
    if assets['center'] is None:
        return None
    combos = C.combos(assets['center'], ce['plate'], ce['padding'])
    if ce['size'] != 'auto':
        # fixed size: first combo (in order) that fits the budget, else the first combo
        for pl, pd in combos:
            if _max_fraction(struct, spec, assets, th, pl, pd, ce['size'], ce['size']) is not None:
                return {'fraction': ce['size'], 'plate': pl, 'padding': pd}
        return {'fraction': ce['size'], 'plate': combos[0][0], 'padding': combos[0][1]}
    pref, lo = th['center']['preferred_fraction'], th['center']['min_fraction']
    found = []
    for pl, pd in combos:
        f = _max_fraction(struct, spec, assets, th, pl, pd, pref, lo)
        if f is None:
            continue
        if f >= pref - 1e-9:
            return {'fraction': f, 'plate': pl, 'padding': pd}
        found.append({'fraction': f, 'plate': pl, 'padding': pd})
    if not found:
        return {'fraction': lo, 'plate': combos[0][0], 'padding': combos[0][1]}
    # largest image wins, but a 0.01 difference is not worth giving up an earlier (preferred) look
    top = max(c['fraction'] for c in found)
    return next(c for c in found if c['fraction'] >= top - 0.01 - 1e-9)


def size_center(payload, v, ec, mask, spec, assets, th):
    struct = Structure(payload, ec, version=v, mask=mask)
    return struct, choose_center(struct, spec, assets, th)


def choose_mask(payload, v, ec, spec, assets, th, choice):
    if spec['mask'] != 'auto':
        return int(spec['mask'])
    default = Structure(payload, ec, version=v).mask
    best = None
    for m in range(8):
        st = Structure(payload, ec, version=v, mask=m)
        lay, _ = plan(st, spec, assets, th, choice)
        per, u = st.static_budget(lay['cleared'], lay['overlay'])
        key = (round(u, 4), int(per.sum()), m != default)
        if best is None or key < best[0]:
            best = (key, m)
    return best[1]


# --------------------------------------------------------------------------- main
def extended_candidates(payload, ec, cands, extra):
    """With a center image, larger versions leave room for a larger image: add up to `extra` versions
    above the smallest one, each with the highest EC level that fits (or the fixed level)."""
    import segno
    v0 = min(v for v, _ in cands)
    out = {v: e for v, e in cands}
    for v in range(v0, min(v0 + extra, 40) + 1):
        levels = ['H', 'Q', 'M', 'L'] if ec == 'auto' else [ec]
        for lv in levels:
            try:
                s = Structure(payload, lv, version=v)
            except (segno.DataOverflowError, ValueError):
                continue
            if v not in out or EC_ORDER.index(s.ec) > EC_ORDER.index(out[v]):
                out[v] = s.ec
            break
    return sorted(out.items())


def prepare_assets(spec):
    """Load every image named in the spec into the common asset contract."""
    ce = spec['center']
    center_asset = Asset(ce['image'], recolor=ce.get('recolor')) if ce.get('image') else None
    decs = []
    for d in spec['decorations']:
        size = d.get('size', 5)
        decs.append({**d, 'asset': Asset(d['image']), 'size': 5 if size == 'auto' else size})
    bg = spec['background']
    background = None
    if bg.get('image'):
        background = {'asset': Asset(bg['image']), 'wash': 0.0 if bg['wash'] == 'auto' else bg['wash'],
                      'auto': bg['wash'] == 'auto'}
    return {'center': center_asset, 'decorations': decs, 'background': background}


def build(spec, th, quick=False, log=print):
    payload = make_payload(spec['content'])
    qz = spec['quiet_zone']
    gating = V.available(th['gating_decoders'])
    informational = V.available(th['informational_decoders'])
    missing = [d for d in th['gating_decoders'] if d not in gating]

    ce = spec['center']
    assets = prepare_assets(spec)
    center_asset = assets['center']
    background = assets['background']
    colors, dark_list, worst_contrast, color_problems, color_notes, color_detail = \
        resolve_colors(spec, center_asset, th)
    f = spec['finder']
    icon_asset = None
    if f['inner'] == 'icon':
        rc = (colors['finder'] or (dark_list[0])) if f.get('icon_recolor', True) else None
        icon_asset = Asset(f['icon'], recolor=rc)

    msize = spec['modules']['size']
    size_auto = msize == 'auto'
    base_size = SHAPE_DEFAULT_SIZE[spec['modules']['shape']] if size_auto else msize
    style = {'modules': {**spec['modules'], 'size': base_size},
             'finder': {**f, 'icon_asset': icon_asset, 'color': colors['finder']},
             'alignment': spec['alignment'],
             'colors': {'dark': colors['dark'], 'light': colors['light']}}

    cands = candidates(payload, spec['ec'], spec['version'])
    attempts = []
    result = None
    width_mm = spec['print']['width_mm']
    distance_mm = spec['print']['distance_mm']

    # Static pre-sizing of every candidate, then a fixed ordering policy:
    # with an auto-sized center image, candidates that reach (almost) the preferred size come first,
    # smallest symbol first; otherwise the one allowing the largest image. Without one: smallest first.
    if center_asset is not None and spec['version'] == 'auto':
        cands = extended_candidates(payload, spec['ec'], cands, th['center'].get('extra_versions', 4))
    planned = []
    for (v, ec) in cands:
        _, ch0 = size_center(payload, v, ec, None, spec, assets, th)
        mask = choose_mask(payload, v, ec, spec, assets, th, ch0)
        struct, ch = size_center(payload, v, ec, mask, spec, assets, th)
        planned.append((v, ec, mask, struct, ch))
    if center_asset is not None and ce['size'] == 'auto':
        pref = th['center']['preferred_fraction'] - 0.02

        def order(t):
            too_small = bool(width_mm) and width_mm / (17 + 4 * t[0] + 2 * qz) < th['min_module_mm']
            near = t[4]['fraction'] >= pref
            return (too_small, 0 if near else 1, t[0] if near else -t[4]['fraction'], t[0])
        planned.sort(key=order)

    geo_roles = ('alignment',) if spec['alignment']['style'] == 'solid' else ()
    distance_gate = th.get('distance_check', 'warn') == 'gate'
    K, K_label = distance_k(th)
    for (v, ec, mask, struct, ch) in planned:
        style['modules']['size'] = base_size
        W = struct.n + 2 * qz
        ch = dict(ch) if ch else None
        att = {'version': v, 'ec': ec, 'mask': mask, 'center': ch, 'failures': [], 'module_size': base_size}
        layout, lay_probs = plan(struct, spec, assets, th, ch)
        if background and background['auto']:
            background['wash'] = _auto_wash(struct, style, layout, qz, th, payload, gating)
        # the center image may still need shrinking after measurement
        for _ in range(12):
            layout, lay_probs = plan(struct, spec, assets, th, ch)
            svg = render_svg(struct, style, layout, qz)
            rgb = A.rasterize(svg, W, ANALYSIS_PX)
            mod = A.analyze_modules(struct, rgb, qz, ANALYSIS_PX, th, layout['cleared'], layout['overlay'],
                                    layout['center_cleared'], geo_roles)
            if (ch and ce['size'] == 'auto' and mod['max_usage'] > th['max_block_usage']
                    and ch['fraction'] - 0.01 >= th['center']['min_fraction'] - 1e-9):
                ch['fraction'] = round(ch['fraction'] - 0.01, 3)
                continue
            break
        att['center'] = ch
        if ch and layout.get('center'):
            x0, y0, x1, y1 = layout['center']['logo_box']
            short = min(x1 - x0, y1 - y0)
            short_mm = short * width_mm / W if width_mm else None
            if short < 2.5 or (short_mm is not None and short_mm < 3):
                att.setdefault('warnings', []).append(
                    f'center image is only {short:.1f} modules' + (f' ({short_mm:.1f} mm)' if short_mm else '') +
                    ' on its short side and may be illegible; a compact mark (symbol without wordmark) works better')
        if lay_probs:
            att['failures'] += lay_probs
        rgb_hi = A.rasterize(svg, W, FINDER_PX)
        fok, fres = A.finder_geometry(struct, rgb_hi, qz, FINDER_PX, mod['threshold'], th)
        att['finder'] = fres
        if not fok:
            bad = sorted({(x['finder'], x['direction']) for x in fres if not x['pass']})
            att['failures'].append(f'finder geometry outside 1:1:3:1:1 tolerance: {bad}')
        if geo_roles:
            aok, ares = A.alignment_geometry(struct, rgb_hi, qz, FINDER_PX, mod['threshold'], th,
                                             layout['center_cleared'])
            att['alignment'] = ares
            if not aok:
                bad = sorted({(tuple(x['alignment']), x['direction']) for x in ares if not x['pass']})
                att['failures'].append(f'alignment pattern geometry outside tolerance: {bad}')
        if color_problems:
            att['failures'] += color_problems
        module_mm = width_mm / W if width_mm else None
        att['module_mm'] = module_mm
        if module_mm is not None and module_mm < th['min_module_mm']:
            att['failures'].append(f"module {module_mm:.2f} mm < minimum {th['min_module_mm']} mm at "
                                   f"{width_mm} mm print width")

        def gates(svg_, rgb_):
            m_ = A.analyze_modules(struct, rgb_, qz, ANALYSIS_PX, th, layout['cleared'], layout['overlay'],
                                   layout['center_cleared'], geo_roles)
            g_ = np.round(A.luma(rgb_)).astype(np.uint8)
            clean_ = V.decode_all(g_, gating + informational)
            fl = []
            if m_['max_usage'] > th['max_block_usage']:
                fl.append(f"error budget: worst block uses {m_['max_usage']:.0%} (limit {th['max_block_usage']:.0%})")
            if m_['violations']:
                fl.append(f"structure damaged: {m_['violations']}")
            bad_ = [d for d in gating if clean_.get(d) != payload]
            if bad_:
                fl.append(f'clean image not decoded by {bad_}')
            return m_, g_, {k: (val == payload) for k, val in clean_.items()}, fl

        mod, gray, clean, fl = gates(svg, rgb)
        att['budget'], att['clean_decode'] = mod, clean
        att['failures'] += fl
        if att['failures']:
            attempts.append(att)
            if bool(color_problems) or not fok:
                break  # these do not depend on the symbol candidate
            continue
        # ---- robustness (with module-size repair) --------------------------------------
        base_rgb = A.rasterize(baseline_svg(struct, qz, colors=style['colors']), W, ANALYSIS_PX)
        base_gray = np.round(A.luma(base_rgb)).astype(np.uint8)
        while True:
            rows = [] if quick else V.robustness(gray, base_gray, ANALYSIS_PX, payload, gating, [])[0]
            weak = [r for r in rows if r['ratio'] is not None and
                    r['ratio'] < th['boundary_ratio'].get(r['axis'], th['boundary_ratio']['default'])]
            if weak and size_auto and style['modules']['size'] < 1.0 - 1e-9:
                style['modules']['size'] = round(min(style['modules']['size'] + 0.04, 1.0), 2)
                att.setdefault('repairs', []).append(f"modules.size -> {style['modules']['size']} "
                                                     f"(robustness below ratio on {sorted({r['axis'] for r in weak})})")
                svg = render_svg(struct, style, layout, qz)
                rgb = A.rasterize(svg, W, ANALYSIS_PX)
                mod, gray, clean, fl = gates(svg, rgb)
                att['budget'], att['clean_decode'] = mod, clean
                if fl:
                    break
                continue
            break
        att['failures'] += fl
        att['module_size'] = style['modules']['size']
        att['robustness'] = rows
        if not quick:
            scale_fails = display_scale_failures(svg, W, payload, gating, th.get('display_scales_px', []))
            att['display_scales'] = {'tested_px': th.get('display_scales_px', []),
                                     'failures': [{'decoder': d, 'px_per_module': px} for d, px in scale_fails]}
            if scale_fails:
                att['failures'].append('not decoded at display scale(s): ' +
                                       ', '.join(f'{d} at {px} px/module' for d, px in scale_fails))
                weak = weak + [{'axis': 'display_scale', 'decoder': d, 'styled': None, 'baseline': px, 'ratio': 0.0}
                               for d, px in scale_fails]
        px_rows = [r for r in rows if r['axis'] == 'min_px_per_module' and r['styled'] and r['baseline']]
        factor = min([r['baseline'] / r['styled'] for r in px_rows], default=1.0)
        att['distance_factor'] = factor
        est = K * module_mm * factor if module_mm else None
        att['est_max_distance_mm'] = est
        sweep_weak = [r for r in weak if r['axis'] != 'display_scale']
        if sweep_weak:
            att['failures'].append('robustness below baseline ratio on: ' + ', '.join(
                sorted({f"{r['axis']}({r['decoder']} {r['ratio']:.2f})" for r in sweep_weak})))
        if weak:
            att['diagnosis'] = diagnose(struct, style, layout, qz, weak, base_gray, payload, th)
        if est is not None and distance_mm and est < distance_mm:
            need_w = distance_mm / (K * factor) * W
            msg = (f'estimated max scan distance {est:.0f} mm < requested {distance_mm} mm '
                   f'(heuristic; about {need_w:.0f} mm print width would reach it)')
            if distance_gate:
                att['failures'].append(msg)
            else:
                att.setdefault('warnings', []).append(msg)
        attempts.append(att)
        if not att['failures']:
            result = {'svg': svg, 'struct': struct, 'layout': layout, 'style': style, 'attempt': att,
                      'base_gray': base_gray}
            break

    status = 'PASS' if result else 'FAIL'
    if result is None:
        # best effort = attempt with the fewest failures (latest wins ties: highest EC)
        best = min(attempts, key=lambda a: (len(a['failures']), -EC_ORDER.index(a['ec'])))
        st = Structure(payload, best['ec'], version=best['version'], mask=best['mask'])
        layout, _ = plan(st, spec, assets, th, best['center'])
        style['modules']['size'] = best.get('module_size', style['modules']['size'])
        result = {'svg': render_svg(st, style, layout, qz), 'struct': st, 'layout': layout,
                  'style': style, 'attempt': best, 'base_gray': None}
    relax = relaxations(payload, spec, th, assets, result, attempts) if status == 'FAIL' else {}
    return {
        'status': status, 'payload': payload, 'qz': qz, 'result': result, 'attempts': attempts,
        'colors': colors, 'color_notes': color_notes, 'worst_contrast': worst_contrast,
        'color_detail': color_detail, 'gating': gating, 'informational': informational,
        'missing_decoders': missing, 'relaxations': relax, 'quick': quick,
        'background_wash': background['wash'] if background else None,
        'distance_k': K, 'distance_k_label': K_label,
    }


def display_scale_failures(svg, W, payload, gating, scales):
    """Decoders can fail at particular display sizes (e.g. zbar with circular finders at 28-40 px per
    module), which blur/downscale sweeps never reach. Decode the clean render at each listed scale."""
    fails = []
    for px in scales:
        g = np.round(A.luma(A.rasterize(svg, W, px))).astype(np.uint8)
        for d in gating:
            if V.ALL_DECODERS[d](g) != payload:
                fails.append((d, px))
    return fails


def diagnose(struct, style, layout, qz, weak, base_gray, payload, th):
    """Revert one style element at a time and re-measure only the failing axes: the element whose
    reversal restores the ratios is the cause. Deterministic, so the agent does not have to guess."""
    import copy
    W = struct.n + 2 * qz
    variants = {}
    if style['finder']['inner'] != 'square':
        st = copy.copy(style)
        st['finder'] = {**style['finder'], 'inner': 'square'}
        variants['finder.inner'] = (st, layout)
    if style['finder']['outer'] != 'square':
        st = copy.copy(style)
        st['finder'] = {**style['finder'], 'outer': 'square'}
        variants['finder.outer'] = (st, layout)
    if style['modules']['shape'] != 'square' or style['modules']['size'] < 1.0:
        st = copy.copy(style)
        st['modules'] = {**style['modules'], 'shape': 'square', 'size': 1.0}
        variants['modules'] = (st, layout)
    if layout.get('background'):
        variants['background'] = (style, {**layout, 'background': None})
    if layout.get('decorations'):
        variants['decorations'] = (style, {**layout, 'decorations': []})
    col = style['colors']
    if col['dark'] != '#000000' or col['light'] != '#FFFFFF' or style['finder'].get('color'):
        st = copy.copy(style)
        st['colors'] = {'dark': '#000000', 'light': '#FFFFFF'}
        st['finder'] = {**style['finder'], 'color': None}
        variants['colors'] = (st, layout)
    out = {}
    for name, (st, lay) in variants.items():
        vsvg = render_svg(struct, st, lay, qz)
        g = np.round(A.luma(A.rasterize(vsvg, W, ANALYSIS_PX))).astype(np.uint8)
        fixed = 0
        for r in weak:
            if r['axis'] == 'display_scale':
                fixed += not display_scale_failures(vsvg, W, payload, [r['decoder']], [r['baseline']])
                continue
            val = V.sweep(g, ANALYSIS_PX, payload, [r['decoder']], r['axis'])[r['decoder']]
            rt = V.ratio(r['axis'], val, r['baseline'])
            need = th['boundary_ratio'].get(r['axis'], th['boundary_ratio']['default'])
            fixed += rt is not None and rt >= need
        out[name] = f'{fixed}/{len(weak)} failing checks recovered when reverted to plain'
    return out


def _auto_wash(struct, style, layout, qz, th, payload, gating):
    """Smallest white wash over a background image that meets the robustness gates on the axes a
    busy background hurts most (contrast and resolution), using the plain rendering as reference."""
    W = struct.n + 2 * qz
    px = ANALYSIS_PX
    base = np.round(A.luma(A.rasterize(baseline_svg(struct, qz, colors=style['colors']), W, px))).astype(np.uint8)
    targets = {}
    for axis in ('contrast', 'min_px_per_module'):
        b = V.sweep(base, px, payload, gating, axis)
        need = th['boundary_ratio'].get(axis, th['boundary_ratio']['default'])
        for d, val in b.items():
            if val is not None:
                targets[(axis, d)] = val / need  # lower-is-better axes: styled must reach base/ratio
    for wash in [x / 20 for x in range(0, 19)]:
        layout['background']['wash'] = wash
        g = np.round(A.luma(A.rasterize(render_svg(struct, style, layout, qz), W, px))).astype(np.uint8)
        ok = all(V.ALL_DECODERS[d](g) == payload for d in gating)
        for (axis, d), val in targets.items():
            if not ok:
                break
            fn = V.AXES[axis][0]
            level = min(max(val, 0.1), 1.0) if axis == 'contrast' else max(val, 1.6)
            ok = V.ALL_DECODERS[d](fn(g, px, level)) == payload
        if ok:
            return wash
    return 0.9


def distance_k(th):
    """Distance factor: median of real-phone observations when there are at least 3, else the default."""
    from .config import load_calibration
    obs = [o for o in (load_calibration(th.get('_calibration_path')).get('observations') or [])
           if o.get('module_mm') and o.get('max_distance_mm')]
    if len(obs) >= 3:
        ks = sorted(o['max_distance_mm'] / (o['module_mm'] * (o.get('style_factor') or 1.0)) for o in obs)
        return float(ks[len(ks) // 2]), f'calibrated from {len(obs)} phone tests'
    return float(th['distance_mm_per_module_mm']), 'heuristic (uncalibrated)'


def tradeoffs(payload, spec, th, assets, factor=1.0):
    """Static what-if table: per symbol candidate, the largest center image each plate allows, module
    size at the requested width, and the width needed for the minimum module / requested distance."""
    qz = spec['quiet_zone']
    width, dist = spec['print']['width_mm'], spec['print']['distance_mm']
    K, _ = distance_k(th)
    rows = []
    ec_mode = spec['ec']
    for v, ec in candidates(payload, ec_mode, spec['version']):
        st = Structure(payload, ec, version=v)
        W = st.n + 2 * qz
        row = {'symbol': f'{v}-{ec}', 'modules': st.n, 'canvas_modules': W,
               'module_mm': round(width / W, 3) if width else None,
               'width_for_min_module_mm': round(th['min_module_mm'] * W, 1),
               'width_for_distance_mm': round(dist / (K * factor) * W, 1) if dist else None,
               'center': {}}
        if assets['center'] is not None:
            ce = spec['center']
            for pl, pd in C.combos(assets['center'], ce['plate'], ce['padding']) + [('hug', 0.25)]:
                key = f'{pl}/pad {pd}'
                if key in row['center']:
                    continue
                row['center'][key] = _max_fraction(st, spec, assets, th, pl, pd, 0.40, 0.05)
        rows.append(row)
    content = {}
    if width:
        need = th['min_module_mm']
        if dist and th.get('distance_check', 'warn') == 'gate':
            need = max(need, dist / (K * factor))
        for ec in EC_ORDER:
            fits = None
            for v in range(1, 41):
                if width / (17 + 4 * v + 2 * qz) >= need:
                    fits = v
                else:
                    break
            content[ec] = {'max_version': fits, 'max_bytes': max_payload_bytes(fits, ec) if fits else 0}
    return {'rows': rows, 'content_limits': content, 'payload_bytes': len(payload.encode('utf-8')),
            'distance_k': K}


def relaxations(payload, spec, th, assets, result, attempts):
    """Smallest changes that would let the design pass, computed rather than guessed."""
    out = {}
    fails = ' '.join(' '.join(a['failures']) for a in attempts)
    qz = spec['quiet_zone']
    factor = result['attempt'].get('distance_factor', 1.0) or 1.0
    tr = tradeoffs(payload, spec, th, assets, factor)
    # center image: best fraction over all candidates and plates
    if assets['center'] is not None and ('error budget' in fails or 'center image covers' in fails):
        best = None
        for row in tr['rows']:
            for combo, f in row['center'].items():
                if f is not None and (best is None or f > best[0]):
                    best = (f, row['symbol'], combo)
        out['center_max_fraction'] = {
            'value': best[0] if best else None, 'at': f'{best[1]} with plate {best[2]}' if best else None,
            'note': 'largest center image (equivalent-square fraction of symbol width) that keeps the budget'}
    # physical size: the smallest symbol that can hold the requested center image decides the width
    width = spec['print']['width_mm']
    if width and ('minimum' in fails or 'scan distance' in fails):
        want = spec['center']['size'] if spec['center']['size'] != 'auto' else th['center']['min_fraction']
        target = None
        for row in tr['rows']:
            if assets['center'] is None or any(f is not None and f >= want - 1e-9 for f in row['center'].values()):
                target = row
                break
        if target is None and tr['rows']:
            # the requested image fits no symbol: size for the symbol that holds the largest image
            target = max(tr['rows'], key=lambda row: max([f or 0 for f in row['center'].values()] or [0]))
        if target:
            need = target['width_for_min_module_mm']
            if th.get('distance_check', 'warn') == 'gate' and target['width_for_distance_mm']:
                need = max(need, target['width_for_distance_mm'])
            if width < need:
                best_img = max([f or 0 for f in target['center'].values()] or [0])
                out['min_width_mm'] = {'value': need, 'at': target['symbol'],
                                       'note': (f"(holds a center image up to {best_img:.0%}) " if best_img else '') +
                                               f"print width that gives {target['symbol']} "
                                               f"({target['canvas_modules']} modules with quiet zone) "
                                               f"{th['min_module_mm']} mm modules"}
        ec_need = 'H' if assets['center'] is not None else 'M'
        lim = tr['content_limits'].get(ec_need)
        if lim is not None:
            out['max_content_bytes'] = {'value': lim['max_bytes'], 'current': tr['payload_bytes'],
                                        'note': f"bytes that fit at {width} mm with EC {ec_need} "
                                                f"(version {lim['max_version']})" if lim['max_version'] else
                                                f'no symbol fits {width} mm at the minimum module size'}
    out['_tradeoffs'] = tr
    if 'finder geometry' in fails:
        out['finder'] = {'note': 'keep 1:1:3:1:1 along horizontal, vertical and both diagonals; '
                                 'use a built-in finder style or an icon that stays inside the 3x3 center'}
    if 'robustness' in fails or 'display scale' in fails:
        diag = {}
        for a in attempts:
            diag.update(a.get('diagnosis') or {})
        ranked = sorted(diag.items(), key=lambda kv: -int(kv[1].split('/')[0]))
        cause = [k for k, v in ranked if v.split('/')[0] == v.split('/')[1].split(' ')[0]]
        out['robustness'] = {'value': cause[0] if cause else None,
                             'note': ('reverting this element alone restores robustness: ' + ', '.join(cause)
                                      if cause else 'no single element explains the loss; see diagnosis: '
                                      + '; '.join(f'{k}: {v}' for k, v in ranked))}
    return out
