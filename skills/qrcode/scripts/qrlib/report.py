"""Write deliverables and the JSON/Markdown report."""
import copy
import json
import math
import os

import numpy as np
import yaml

from . import analyze as A
from .payload import describe
from .raster import MM_PER_INCH, png_to_pdf, svg_to_png
from .verify import AXES

ENGINE_VERSION = '1.3.0'


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _center_info(c, n, width_mm, W):
    x0, y0, x1, y1 = c['logo_box']
    info = {'fraction': c['fraction'], 'plate': c['plate_kind'], 'padding': c['padding'],
            'logo_w_modules': x1 - x0, 'logo_h_modules': y1 - y0}
    if width_mm:
        info['logo_w_mm'] = (x1 - x0) * width_mm / W
        info['logo_h_mm'] = (y1 - y0) * width_mm / W
    return info


MAX_PDF_SIDE_PX = 6000
DECODE_PX = 16  # px per module for decode checks of large rasters: a size the display-scale gate covers


def _decodes(png, W, payload, decoders):
    """Which decoders read the PNG as exactly the payload. A raster above DECODE_PX per module is read
    at DECODE_PX, the detail a phone photo gives; smaller ones are read as they are."""
    from .verify import decode_all
    g = np.round(A.luma(A.png_rgb(png))).astype(np.uint8)
    if g.shape[1] > W * DECODE_PX:
        import cv2
        g = cv2.resize(g, (W * DECODE_PX, W * DECODE_PX), interpolation=cv2.INTER_AREA)
    return {k: v == payload for k, v in decode_all(g, decoders).items()}


def _pdf(svg, W, width_mm, png_module_px):
    """(PDF bytes, the raster it holds, px per module). Printed: the page is the print width and the
    raster reaches 1200 dpi, its side capped at MAX_PDF_SIDE_PX. Not printed: the PNG deliverable's
    pixels on a 96 dpi page."""
    if width_mm:
        px = max(1, min(math.ceil(1200 * (width_mm / W) / MM_PER_INCH), MAX_PDF_SIDE_PX // W))
        page_mm = width_mm
    else:
        px = png_module_px
        page_mm = W * px * MM_PER_INCH / 96
    png = svg_to_png(svg, W * px, W * px)
    return png_to_pdf(png, page_mm, page_mm), png, px


def write_outputs(res, spec, th, layers, outdir, spec_path):
    os.makedirs(outdir, exist_ok=True)
    r = res['result']
    st, att, style = r['struct'], r['attempt'], r['style']
    qz = res['qz']
    W = st.n + 2 * qz
    o = spec['output']
    width_mm = spec['print']['width_mm']
    payload, gating = res['payload'], res['gating']
    from .render import render_svg
    size_attr = (width_mm, 'mm') if width_mm else (W * o['png_module_px'], 'px')
    svg_export = render_svg(st, style, r['layout'], qz, size_attr=size_attr, transparent=o['transparent'])
    extra = [w for w in (spec['print'].get('extra_widths_mm') or []) if w != width_mm]
    extra_svg = {w: render_svg(st, style, r['layout'], qz, size_attr=(w, 'mm'), transparent=o['transparent'])
                 for w in extra}

    # every raster that leaves the engine is decoded before any file is written; a miss fails the build
    png = svg_to_png(svg_export, W * o['png_module_px'], W * o['png_module_px'])
    check_png = png if not o['transparent'] else svg_to_png(r['svg'], W * o['png_module_px'], W * o['png_module_px'])
    final_decode = _decodes(check_png, W, payload, gating + res['informational'])
    delivery_failures = [f"delivered PNG ({o['png_module_px']} px/module) does not decode with {d}"
                         for d in gating if not final_decode.get(d)]
    pdfs = {}
    if 'pdf' in o['formats']:
        for key, w, sv in [('pdf', width_mm, svg_export)] + [(f'pdf_{w:g}mm', w, extra_svg[w]) for w in extra]:
            data, raster, px = _pdf(sv, W, w, o['png_module_px'])
            dec = _decodes(raster, W, payload, gating)
            pdfs[key] = (data, {'px_per_module': px, 'dpi': round(px * W / w * MM_PER_INCH) if w else 96,
                                'decodes': dec})
            delivery_failures += [f'{key} does not decode with {d}' for d in gating if not dec.get(d)]
    status = 'FAIL' if delivery_failures else res['status']
    base = o['basename'] + ('' if status == 'PASS' else '.FAILED')

    files = {}
    if 'svg' in o['formats']:
        files['svg'] = os.path.join(outdir, base + '.svg')
        with open(files['svg'], 'w', encoding='utf-8') as f:
            f.write(svg_export)
    png_path = os.path.join(outdir, base + '.png')
    with open(png_path, 'wb') as f:
        f.write(png)
    if 'png' in o['formats']:
        files['png'] = png_path
    else:
        files['preview_png'] = png_path
    if 'pdf' in pdfs:
        files['pdf'] = os.path.join(outdir, base + '.pdf')
        with open(files['pdf'], 'wb') as f:
            f.write(pdfs['pdf'][0])

    # the same design at additional print widths (only physical checks change)
    sizes = []
    factor = att.get('distance_factor') or 1.0
    for w in ([width_mm] if width_mm else []) + extra:
        mm = w / W
        entry = {'width_mm': w, 'module_mm': round(mm, 3), 'module_ok': mm >= th['min_module_mm'],
                 'est_max_distance_mm': round(res['distance_k'] * mm * factor)}
        if w != width_mm:
            tag = f"{o['basename']}_{w:g}mm" + ('' if status == 'PASS' else '.FAILED')
            if 'svg' in o['formats']:
                files[f'svg_{w:g}mm'] = os.path.join(outdir, tag + '.svg')
                with open(files[f'svg_{w:g}mm'], 'w', encoding='utf-8') as f:
                    f.write(extra_svg[w])
            if f'pdf_{w:g}mm' in pdfs:
                files[f'pdf_{w:g}mm'] = os.path.join(outdir, tag + '.pdf')
                with open(files[f'pdf_{w:g}mm'], 'wb') as f:
                    f.write(pdfs[f'pdf_{w:g}mm'][0])
        sizes.append(entry)

    # final measurements on exactly what is delivered
    rgb = A.rasterize(r['svg'], W, 12)
    geo = ('alignment',) if style['alignment']['style'] == 'solid' else ()
    final_mod = A.analyze_modules(st, rgb, qz, 12, th, r['layout']['cleared'], r['layout']['overlay'],
                                  r['layout']['center_cleared'], geo)

    resolved = copy.deepcopy(spec)
    resolved.update({'ec': st.ec, 'version': st.version, 'mask': st.mask})
    resolved['modules']['size'] = style['modules']['size']
    resolved['colors']['dark'] = res['colors']['dark']
    if res['colors']['finder']:
        resolved['finder']['color'] = res['colors']['finder']
    if r['layout'].get('center'):
        c = r['layout']['center']
        resolved['center'].update({'size': c['fraction'], 'plate': c['plate_kind'], 'padding': c['padding']})
    if res['background_wash'] is not None:
        resolved['background']['wash'] = res['background_wash']
    for i, d in enumerate(r['layout'].get('decorations', [])):
        if i < len(resolved['decorations']):
            resolved['decorations'][i]['position'] = [round(d['position'][0], 2), round(d['position'][1], 2)]
            resolved['decorations'][i]['size'] = d['size']
    files['resolved_spec'] = os.path.join(outdir, 'resolved_spec.yaml')
    with open(files['resolved_spec'], 'w', encoding='utf-8') as f:
        yaml.safe_dump(_clean(resolved), f, allow_unicode=True, sort_keys=False)

    module_mm = width_mm / W if width_mm else None
    rep = {
        'status': status,
        'engine_version': ENGINE_VERSION,
        'spec': os.path.abspath(spec_path),
        'layers': layers,
        'quick_mode': res['quick'],
        'content': {'type': next(iter(spec['content'])), 'payload': res['payload'],
                    'bytes': len(res['payload'].encode('utf-8'))},
        'symbol': {'version': st.version, 'ec': st.ec, 'mask': st.mask, 'modules': st.n, 'canvas_modules': W,
                   'quiet_zone': qz, 'blocks': len(st.block_data), 'ec_codewords_per_block': st.ec_per_block,
                   'correctable_codewords_per_block': st.capacity},
        'style': {'modules': {k: style['modules'][k] for k in ('shape', 'size', 'radius')},
                  'finder': {k: style['finder'].get(k) for k in ('outer', 'inner', 'color')},
                  'alignment': style['alignment']},
        'center': (_center_info(r['layout']['center'], st.n, width_mm, W) if r['layout'].get('center') else None),
        'decorations': [{'position': d['position'], 'size': d['size'], 'mode': d['mode']}
                        for d in r['layout'].get('decorations', [])],
        'background_wash': res['background_wash'],
        'colors': {'dark': res['colors']['dark'], 'light': res['colors']['light'],
                   'finder': res['colors']['finder'], 'worst_contrast': round(res['worst_contrast'], 3),
                   'min_required': th['min_symbol_contrast'], 'notes': res['color_notes']},
        'budget': final_mod,
        'finder_geometry': att.get('finder'),
        'alignment_geometry': att.get('alignment'),
        'warnings': att.get('warnings', []) + (
            [f"gating decoders unavailable: {res['missing_decoders']}"] if res['missing_decoders'] else []),
        'decoders': {'gating': res['gating'], 'informational': res['informational'],
                     'missing_gating': res['missing_decoders'], 'final_png': final_decode,
                     'pdf': {k: v[1] for k, v in pdfs.items()}},
        'robustness': att.get('robustness', []),
        'display_scales': att.get('display_scales'),
        'physical': {'width_mm': width_mm, 'module_mm': module_mm,
                     'distance_mm_requested': spec['print']['distance_mm'],
                     'est_max_distance_mm': att.get('est_max_distance_mm'),
                     'distance_k': res['distance_k'], 'distance_k_label': res['distance_k_label'],
                     'distance_factor': att.get('distance_factor')},
        'repairs': att.get('repairs', []),
        'diagnosis': att.get('diagnosis'),
        'attempts': [{k: a.get(k) for k in ('version', 'ec', 'mask', 'center', 'module_size', 'failures', 'warnings')}
                     for a in res['attempts']],
        'sizes': sizes,
        'tradeoffs': res['relaxations'].pop('_tradeoffs', None) if res['relaxations'] else None,
        'failures': (att['failures'] if res['status'] == 'FAIL' else []) + delivery_failures,
        'relaxations': res['relaxations'],
        'thresholds': {k: v for k, v in th.items() if not k.startswith('_')},
        'files': files,
    }
    files['report_json'] = os.path.join(outdir, 'report.json')
    files['report_md'] = os.path.join(outdir, 'report.md')
    with open(files['report_json'], 'w', encoding='utf-8') as f:
        json.dump(_clean(rep), f, indent=2, ensure_ascii=False)
    with open(files['report_md'], 'w', encoding='utf-8') as f:
        f.write(markdown(_clean(rep)))
    return rep


def _pct(x):
    return f'{x:.0%}'


def markdown(rep):
    L = []
    s = rep['symbol']
    L.append(f"# QR build report: {rep['status']}")
    L.append('')
    if rep['status'] != 'PASS':
        L.append('**Not deliverable.** Failed gates:')
        for f in rep['failures']:
            L.append(f'- {f}')
        if rep['relaxations']:
            L.append('')
            L.append('Smallest changes that would pass (computed):')
            for k, v in rep['relaxations'].items():
                val = v.get('value')
                extra = f" (current {v['current']})" if 'current' in v else ''
                L.append(f"- `{k}`: {val if val is not None else '-'}{extra} - {v['note']}")
        L.append('')
    if rep['warnings']:
        L.append('Warnings (not gating):')
        for w in rep['warnings']:
            L.append(f'- {w}')
        L.append('')
    if rep.get('diagnosis'):
        L.append('Diagnosis (one element reverted at a time): ' +
                 '; '.join(f'{k}: {v}' for k, v in rep['diagnosis'].items()))
        L.append('')
    if rep['quick_mode']:
        L.append('> Quick mode: robustness sweeps skipped. Run a full build before delivering.')
        L.append('')
    L.append(f"- Content ({rep['content']['type']}, {rep['content']['bytes']} bytes): `{describe(rep['content']['payload'])}`")
    L.append(f"- Symbol: version {s['version']}-{s['ec']}, mask {s['mask']}, {s['modules']}x{s['modules']} modules "
             f"+ {s['quiet_zone']}-module quiet zone; {s['blocks']} RS blocks, "
             f"{s['correctable_codewords_per_block']} correctable codewords each")
    st = rep['style']
    L.append(f"- Style: modules {st['modules']['shape']} size {st['modules']['size']}; finder "
             f"{st['finder']['outer']}/{st['finder']['inner']}")
    if rep['center']:
        ci = rep['center']
        L.append(f"- Center image: {ci['fraction']:.0%} of symbol width (equivalent square), plate {ci['plate']}, "
                 f"padding {ci['padding']} module; logo box {ci['logo_w_modules']:.1f} x {ci['logo_h_modules']:.1f} modules"
                 + (f" = {ci['logo_w_mm']:.1f} x {ci['logo_h_mm']:.1f} mm printed" if ci.get('logo_w_mm') else ''))
    for d in rep['decorations']:
        L.append(f"- Decoration at row/col {d['position']} size {d['size']} ({d['mode']})")
    c = rep['colors']
    L.append(f"- Colors: dark {c['dark']}, light {c['light']}"
             + (f", finder {c['finder']}" if c['finder'] else '')
             + f"; worst-case contrast {c['worst_contrast']:.2f} (min {c['min_required']})")
    for n_ in c['notes']:
        L.append(f'  - {n_}')
    if rep['repairs']:
        L.append('- Automatic repairs: ' + '; '.join(rep['repairs']))
    L.append('')
    b = rep['budget']
    L.append('## Error budget (per Reed-Solomon block)')
    L.append('')
    L.append('| block | wrong codewords | at risk | capacity | used |')
    L.append('|---|---|---|---|---|')
    for i, (e, r) in enumerate(zip(b['block_errors'], b['block_at_risk'])):
        L.append(f"| {i + 1} | {e} | {r} | {b['capacity']} | {_pct(e / b['capacity'])} |")
    L.append('')
    L.append(f"Worst block: {_pct(b['max_usage'])} used (limit {_pct(rep['thresholds']['max_block_usage'])}); "
             f"{_pct(b['max_usage_with_risk'])} counting at-risk codewords. "
             f"Structure violations: {b['violations'] or 'none'}"
             + (f"; waived: {b['waived']}" if b['waived'] else '') + '.')
    L.append('')
    fg = rep['finder_geometry'] or []
    bad = [f"{x['finder']} {x['direction']} {x['ratios']}" for x in fg if not x['pass']]
    L.append('## Finder geometry')
    L.append('')
    L.append('All 12 scans within tolerance.' if fg and not bad else ('Failed: ' + '; '.join(bad) if bad else 'n/a'))
    ag = rep.get('alignment_geometry') or []
    if ag:
        badag = [f"{x['alignment']} {x['direction']} {x['runs']}" for x in ag if not x['pass']]
        L.append('')
        L.append(f'Alignment patterns ({len(ag) // 2}): ' + ('all scans within tolerance.' if not badag
                                                              else 'failed: ' + '; '.join(badag)))
    L.append('')
    d = rep['decoders']
    L.append('## Decoders')
    L.append('')
    L.append('- Final PNG: ' + ', '.join(f"{k} {'OK' if v else 'FAIL'}" + ('' if k in d['gating'] else ' (informational)')
                                         for k, v in d['final_png'].items()))
    for key, info in (d.get('pdf') or {}).items():
        L.append(f"- {key}: raster {info['px_per_module']} px/module ({info['dpi']} dpi), "
                 + ', '.join(f"{k} {'OK' if v else 'FAIL'}" for k, v in info['decodes'].items()))
    if d['missing_gating']:
        L.append(f"Missing gating decoders (install them): {d['missing_gating']}")
    L.append('')
    if rep['robustness']:
        L.append('## Robustness vs. plain rendering of the same matrix')
        L.append('')
        L.append('| axis | decoder | this design | plain | ratio |')
        L.append('|---|---|---|---|---|')
        for r in rep['robustness']:
            ratio = '-' if r['ratio'] is None else f"{r['ratio']:.2f}"
            L.append(f"| {r['axis']} | {r['decoder']} | {r['styled']} | {r['baseline']} | {ratio} |")
        L.append('')
        L.append('Axes: ' + '; '.join(f'{k} = {v[3]}' for k, v in AXES.items()) + '.')
        L.append('')
    ds = rep.get('display_scales')
    if ds:
        bad = ds['failures']
        L.append(f"Display scales {ds['tested_px']} px/module: " + (
            'decoded at every scale.' if not bad else
            'FAILED ' + ', '.join(f"{b['decoder']} at {b['px_per_module']}" for b in bad) + '.'))
        L.append('')
    p = rep['physical']
    L.append('## Physical size')
    L.append('')
    if rep.get('sizes'):
        L.append(f"Scan-distance factor {p['distance_k']:.0f} mm per mm of module ({p['distance_k_label']}), "
                 f"style factor {p['distance_factor'] or 1.0:.2f}. Minimum module {rep['thresholds']['min_module_mm']} mm.")
        L.append('')
        L.append('| printed width | module | module OK | est. max scan distance |')
        L.append('|---|---|---|---|')
        for z in rep['sizes']:
            L.append(f"| {z['width_mm']:g} mm | {z['module_mm']:.2f} mm | {'yes' if z['module_ok'] else 'NO'} | "
                     f"about {z['est_max_distance_mm'] / 10:.0f} cm |")
        if p['distance_mm_requested']:
            L.append('')
            L.append(f"Requested scan distance: {p['distance_mm_requested'] / 10:g} cm. The estimate is a heuristic "
                     'until calibrated with real phones (assets/calibration.yaml).')
    else:
        L.append('- No print width given: physical size and scan-distance checks were skipped.')
    L.append('')
    t = rep.get('tradeoffs')
    if t and t.get('rows'):
        L.append('## Trade-offs (static, computed)')
        L.append('')
        combos = sorted({k for row in t['rows'] for k in row['center']})
        head = '| symbol | canvas modules | width for min module |' + ''.join(f' max image {c} |' for c in combos)
        L.append(head)
        L.append('|' + '---|' * (3 + len(combos)))
        for row in t['rows']:
            cells = ''.join(f" {row['center'][c]:.0%} |" if row['center'].get(c) is not None else ' - |'
                            for c in combos)
            L.append(f"| {row['symbol']} | {row['canvas_modules']} | {row['width_for_min_module_mm']} mm |{cells}")
        if t.get('content_limits'):
            L.append('')
            L.append('Content that fits the requested width (bytes, by EC level): ' + ', '.join(
                f"{ec} {v['max_bytes']}" for ec, v in t['content_limits'].items()) + f" (current {t['payload_bytes']}).")
        L.append('')
    if len(rep['attempts']) > 1 or rep['status'] != 'PASS':
        L.append('## Candidates tried')
        L.append('')
        for a in rep['attempts']:
            cdesc = (f"center {a['center']['fraction']:.0%} {a['center']['plate']}/pad {a['center']['padding']}"
                     if a.get('center') else 'no center image')
            L.append(f"- {a['version']}-{a['ec']} mask {a['mask']} {cdesc}: "
                     + ('passed' if not a['failures'] else '; '.join(a['failures'])))
        L.append('')
    L.append('## Real-device check (required before printing)')
    L.append('')
    L.append('- Print or display at the real size; scan with the iOS camera, an Android camera/Google Lens, '
             'and the messaging apps the audience uses.')
    L.append('- Test at the intended distance, a 30-45 degree angle, and under the actual lighting.')
    L.append('- Confirm the opened content matches exactly (tracking parameters, Wi-Fi credentials, etc.).')
    L.append('')
    L.append('Files: ' + ', '.join(f'{k}: `{os.path.basename(v)}`' for k, v in rep['files'].items()))
    return '\n'.join(L) + '\n'
