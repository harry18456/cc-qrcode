"""SVG renderer. Geometry is expressed in module units; one SVG is the single source of truth
for every deliverable (SVG, PNG, PDF) and for all verification rasters."""
import math

import numpy as np


def _n(v):
    s = f'{v:.4f}'.rstrip('0').rstrip('.')
    return s if s not in ('-0', '') else '0'


def rrect_d(x, y, w, h, tl=0.0, tr=0.0, br=0.0, bl=0.0, rev=False):
    """Rounded rectangle, clockwise; rev=True draws it counter-clockwise (a hole under nonzero fill)."""
    if rev:
        p = [f'M{_n(x + w - tr)},{_n(y)}', f'H{_n(x + tl)}']
        if tl:
            p.append(f'A{_n(tl)},{_n(tl)} 0 0 0 {_n(x)},{_n(y + tl)}')
        p.append(f'V{_n(y + h - bl)}')
        if bl:
            p.append(f'A{_n(bl)},{_n(bl)} 0 0 0 {_n(x + bl)},{_n(y + h)}')
        p.append(f'H{_n(x + w - br)}')
        if br:
            p.append(f'A{_n(br)},{_n(br)} 0 0 0 {_n(x + w)},{_n(y + h - br)}')
        p.append(f'V{_n(y + tr)}')
        if tr:
            p.append(f'A{_n(tr)},{_n(tr)} 0 0 0 {_n(x + w - tr)},{_n(y)}')
        p.append('Z')
        return ''.join(p)
    p = [f'M{_n(x + tl)},{_n(y)}', f'H{_n(x + w - tr)}']
    if tr:
        p.append(f'A{_n(tr)},{_n(tr)} 0 0 1 {_n(x + w)},{_n(y + tr)}')
    p.append(f'V{_n(y + h - br)}')
    if br:
        p.append(f'A{_n(br)},{_n(br)} 0 0 1 {_n(x + w - br)},{_n(y + h)}')
    p.append(f'H{_n(x + bl)}')
    if bl:
        p.append(f'A{_n(bl)},{_n(bl)} 0 0 1 {_n(x)},{_n(y + h - bl)}')
    p.append(f'V{_n(y + tl)}')
    if tl:
        p.append(f'A{_n(tl)},{_n(tl)} 0 0 1 {_n(x + tl)},{_n(y)}')
    p.append('Z')
    return ''.join(p)


def circle_d(cx, cy, r, rev=False):
    # clockwise, like rrect_d: overlapping shapes in one nonzero-filled path must share a winding
    f = '0' if rev else '1'
    return (f'M{_n(cx - r)},{_n(cy)}A{_n(r)},{_n(r)} 0 1 {f} {_n(cx + r)},{_n(cy)}'
            f'A{_n(r)},{_n(r)} 0 1 {f} {_n(cx - r)},{_n(cy)}Z')


def shape_d(kind, x, y, w, h, rev=False):
    """Closed shape inside a box (finder/alignment parts). rev=True gives the counter-clockwise outline."""
    if kind == 'square':
        return rrect_d(x, y, w, h, rev=rev)
    if kind == 'rounded':
        r = min(w, h) * 0.28
        return rrect_d(x, y, w, h, r, r, r, r, rev=rev)
    if kind == 'circle':
        return circle_d(x + w / 2, y + h / 2, min(w, h) / 2, rev=rev)
    if kind == 'leaf':
        r = min(w, h) * 0.45
        return rrect_d(x, y, w, h, tl=r, br=r, rev=rev)
    raise ValueError(kind)


def module_d(shape, x, y, size, radius):
    o = (1 - size) / 2
    s = size
    if shape == 'square':
        return rrect_d(x + o, y + o, s, s)
    if shape in ('rounded', 'squircle'):
        r = (radius if shape == 'rounded' else 0.7) * s / 2
        return rrect_d(x + o, y + o, s, s, r, r, r, r)
    if shape in ('dot', 'connected'):
        return circle_d(x + 0.5, y + 0.5, s / 2)
    if shape == 'diamond':
        return (f'M{_n(x + 0.5)},{_n(y + o)}L{_n(x + 1 - o)},{_n(y + 0.5)}'
                f'L{_n(x + 0.5)},{_n(y + 1 - o)}L{_n(x + o)},{_n(y + 0.5)}Z')
    if shape == 'leaf':
        r = s * 0.5
        return rrect_d(x + o, y + o, s, s, tl=r, br=r)
    raise ValueError(shape)


def finder_boxes(n):
    return [(0, 0), (0, n - 7), (n - 7, 0)]


def _gradient_def(dark, qz, n):
    ang = math.radians(dark.get('angle', 0))
    cx = cy = qz + n / 2
    dx, dy = math.cos(ang) * n / 2, math.sin(ang) * n / 2
    stops = dark['gradient']
    st = ''.join(f'<stop offset="{_n(i / (len(stops) - 1))}" stop-color="{c}"/>' for i, c in enumerate(stops))
    return (f'<linearGradient id="qrDark" gradientUnits="userSpaceOnUse" x1="{_n(cx - dx)}" y1="{_n(cy - dy)}" '
            f'x2="{_n(cx + dx)}" y2="{_n(cy + dy)}">{st}</linearGradient>')


def _image(asset, x, y, w, h, slice_=False):
    par = 'xMidYMid slice' if slice_ else 'xMidYMid meet'
    return (f'<image x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" preserveAspectRatio="{par}" '
            f'href="{asset.data_uri()}"/>')


def render_svg(struct, style, layout, qz, size_attr=None, transparent=False):
    """Build the SVG document.

    style: resolved style dict (modules/finder/alignment/colors); layout: resolved placements.
    size_attr: (value, unit) for the root width/height, e.g. (30, 'mm') or (720, 'px')."""
    n = struct.n
    W = n + 2 * qz
    col = style['colors']
    dark_fill = 'url(#qrDark)' if isinstance(col['dark'], dict) else col['dark']
    finder_fill = style['finder'].get('color') or dark_fill
    defs = _gradient_def(col['dark'], qz, n) if isinstance(col['dark'], dict) else ''
    size_attr = size_attr or (W * 16, 'px')
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'width="{_n(size_attr[0])}{size_attr[1]}" height="{_n(size_attr[0])}{size_attr[1]}" '
           f'viewBox="0 0 {W} {W}" shape-rendering="geometricPrecision">']
    if defs:
        out.append(f'<defs>{defs}</defs>')

    # background
    bg = layout.get('background')
    if not transparent:
        out.append(f'<rect width="{W}" height="{W}" fill="{col["light"]}"/>')
    if bg and bg.get('asset'):
        out.append(_image(bg['asset'], 0, 0, W, W, slice_=True))
        if bg['wash'] > 0:
            out.append(f'<rect width="{W}" height="{W}" fill="{col["light"]}" opacity="{_n(bg["wash"])}"/>')

    cleared = layout['cleared']
    m = style['modules']
    shape, size, radius = m['shape'], m['size'], m['radius']
    in_finder = np.zeros((n, n), bool)
    for r0, c0 in finder_boxes(n):
        in_finder[r0:r0 + 7, c0:c0 + 7] = True
    solid_align = style['alignment']['style'] == 'solid'
    drawable = struct.dark & ~in_finder & ~cleared
    if solid_align:
        drawable &= struct.role != 'alignment'

    d = []
    for r in range(n):
        for c in range(n):
            if drawable[r, c]:
                d.append(module_d(shape, qz + c, qz + r, size, radius))
    if shape == 'connected':
        h = size
        o = (1 - size) / 2
        for r in range(n):
            for c in range(n):
                if not drawable[r, c]:
                    continue
                if c + 1 < n and drawable[r, c + 1]:
                    d.append(rrect_d(qz + c + 0.5, qz + r + o, 1, h))
                if r + 1 < n and drawable[r + 1, c]:
                    d.append(rrect_d(qz + c + o, qz + r + 0.5, h, 1))
    # alignment patterns (solid) go into the same path as the modules: shapes drawn in separate
    # paths leave anti-aliasing seams where they touch, visible as hairlines when scaled
    if solid_align:
        outer = style['finder']['outer']
        inner = 'circle' if style['finder']['inner'] == 'circle' else ('rounded' if outer != 'square' else 'square')
        for r, c in struct.alignment_centers():
            if cleared[r - 2:r + 3, c - 2:c + 3].any():
                continue
            x, y = qz + c - 2, qz + r - 2
            d.append(shape_d(outer, x, y, 5, 5) + shape_d(outer, x + 1, y + 1, 3, 3, rev=True))
            d.append(shape_d(inner, x + 2, y + 2, 1, 1))
    out.append(f'<path fill="{dark_fill}" d="{"".join(d)}"/>')

    # finder patterns
    f = style['finder']
    for r0, c0 in finder_boxes(n):
        x, y = qz + c0, qz + r0
        ring = shape_d(f['outer'], x, y, 7, 7) + shape_d(f['outer'], x + 1, y + 1, 5, 5)
        out.append(f'<path fill="{finder_fill}" fill-rule="evenodd" d="{ring}"/>')
        if f['inner'] == 'icon':
            s = 3 * f.get('icon_scale', 1.0)
            w, h = f['icon_asset'].fit(s, s)
            out.append(_image(f['icon_asset'], x + 3.5 - w / 2, y + 3.5 - h / 2, w, h))
        else:
            out.append(f'<path fill="{finder_fill}" d="{shape_d(f["inner"], x + 2, y + 2, 3, 3)}"/>')

    # center image
    ce = layout.get('center')
    if ce:
        from .center import placement
        pl = ce.get('plate')
        if pl:
            if pl['type'] == 'rect':
                x0, y0, x1, y1 = pl['box']
                r = pl['radius']
                out.append(f'<path fill="{col["light"]}" d="{rrect_d(qz + x0, qz + y0, x1 - x0, y1 - y0, r, r, r, r)}"/>')
            elif pl['type'] == 'circle':
                out.append(f'<path fill="{col["light"]}" d="{circle_d(qz + pl["cx"], qz + pl["cy"], pl["r"])}"/>')
            elif pl['type'] == 'path' and pl['d']:
                out.append(f'<path fill="{col["light"]}" transform="translate({qz},{qz})" d="{pl["d"]}"/>')
        x, y, w, h = placement(ce['asset'], ce['logo_box'])
        out.append(_image(ce['asset'], qz + x, qz + y, w, h))

    # decorations
    for dec in layout.get('decorations', []):
        x0, y0, x1, y1 = dec['box']
        w, h = dec['asset'].fit(x1 - x0, y1 - y0)
        out.append(_image(dec['asset'], qz + (x0 + x1) / 2 - w / 2, qz + (y0 + y1) / 2 - h / 2, w, h))

    out.append('</svg>')
    return ''.join(out)


def baseline_svg(struct, qz, size_attr=None, colors=None):
    """Plain rendering of the same matrix (square modules and finders, no images) in the same colors:
    the reference for robustness ratios. Color itself is judged by the contrast gate, so using the
    design's colors here isolates the effect of shapes, finders, images and backgrounds."""
    style = {'modules': {'shape': 'square', 'size': 1.0, 'radius': 0},
             'finder': {'outer': 'square', 'inner': 'square', 'color': None},
             'alignment': {'style': 'modules'},
             'colors': colors or {'dark': '#000000', 'light': '#FFFFFF'}}
    layout = {'cleared': np.zeros((struct.n, struct.n), bool)}
    return render_svg(struct, style, layout, qz, size_attr)
