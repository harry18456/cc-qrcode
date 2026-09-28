"""Measurements on a rendered raster: module reads, error budget, structural integrity,
finder geometry, and color contrast. These are the hard gates; none of them is a judgment call."""
import io

import numpy as np
from PIL import Image

from .raster import svg_to_png

GRAY_FORMULAS = {
    'bt601': (0.299, 0.587, 0.114),
    'bt709': (0.2126, 0.7152, 0.0722),
    'mean': (1 / 3, 1 / 3, 1 / 3),
    'zxing': (0.25, 0.5, 0.25),
}


def hex_rgb(h):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(ch * 2 for ch in h)
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=float)


def rasterize(svg, canvas_modules, px):
    size = int(round(canvas_modules * px))
    return png_rgb(svg_to_png(svg, size, size))


def png_rgb(png):
    """RGB array of PNG bytes, transparency composited on white (how a page or screen shows it)."""
    img = Image.open(io.BytesIO(png)).convert('RGBA')
    bg = Image.new('RGBA', img.size, (255, 255, 255, 255))
    return np.array(Image.alpha_composite(bg, img).convert('RGB'))


def luma(rgb, formula='bt601'):
    w = GRAY_FORMULAS[formula]
    return rgb[..., 0] * w[0] + rgb[..., 1] * w[1] + rgb[..., 2] * w[2]


def sample_cores(Y, canvas_modules, px, core):
    """Mean luminance of the central `core` fraction of every module cell (quiet zone included)."""
    ii = np.pad(Y, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    W = canvas_modules
    out = np.zeros((W, W))
    half = core * px / 2
    for r in range(W):
        y0 = int(round((r + 0.5) * px - half))
        y1 = max(int(round((r + 0.5) * px + half)), y0 + 1)
        for c in range(W):
            x0 = int(round((c + 0.5) * px - half))
            x1 = max(int(round((c + 0.5) * px + half)), x0 + 1)
            s = ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]
            out[r, c] = s / ((y1 - y0) * (x1 - x0))
    return out


def analyze_modules(struct, rgb, qz, px, th, cleared, overlay, center_cleared, geometric_roles=()):
    """Read every module like an ideal decoder and turn misreads into per-block codeword errors."""
    n = struct.n
    W = n + 2 * qz
    Y = luma(rgb)
    cores = sample_cores(Y, W, px, th['sampling_core'])
    sym = cores[qz:qz + n, qz:qz + n]
    untouched = ~(cleared | overlay)
    dm = struct.dark & untouched & (struct.role == 'data')
    lm = ~struct.dark & untouched & (struct.role == 'data')
    dark_mean = float(sym[dm].mean()) if dm.any() else 0.0
    light_mean = float(sym[lm].mean()) if lm.any() else 255.0
    T = (dark_mean + light_mean) / 2
    span = max(light_mean - dark_mean, 1e-6)
    read_dark = sym < T
    data = struct.role == 'data'
    wrong = (read_dark != struct.dark) & data
    errors = struct.block_errors(wrong)
    weak = (np.abs(sym - T) < th['weak_margin'] * span) & data & ~wrong
    err_cws = set(np.unique(struct.cw_of[wrong & (struct.cw_of >= 0)]).tolist())
    weak_cws = set(np.unique(struct.cw_of[weak & (struct.cw_of >= 0)]).tolist()) - err_cws
    at_risk = np.zeros_like(errors)
    for cw in weak_cws:
        at_risk[struct.block_of_cw[cw]] += 1
    cap = struct.capacity
    usage = errors / cap
    risk_usage = (errors + at_risk) / cap

    # structural integrity (finder 7x7 is judged geometrically, not per module)
    violations = {}
    waived = {}
    checked = np.isin(struct.role, [r for r in ['separator', 'timing', 'alignment', 'format', 'version']
                                    if r not in geometric_roles])
    bad = (read_dark != struct.dark) & checked
    central = struct.central_alignment_mask() & center_cleared
    if th.get('allow_center_alignment_cover', True) and (bad & central).any():
        waived['central_alignment'] = int((bad & central).sum())
        bad &= ~central
    for role in ['separator', 'timing', 'alignment', 'format', 'version']:
        k = int((bad & (struct.role == role)).sum())
        if k:
            violations[role] = k
    qz_mask = np.ones((W, W), bool)
    qz_mask[qz:qz + n, qz:qz + n] = False
    qz_dark = int(((cores < T) & qz_mask).sum())
    if qz_dark:
        violations['quiet_zone'] = qz_dark

    return {
        'threshold': T, 'dark_mean': dark_mean, 'light_mean': light_mean,
        'measured_contrast': span / 255.0,
        'block_errors': errors.tolist(), 'block_at_risk': at_risk.tolist(), 'capacity': cap,
        'max_usage': float(usage.max()), 'max_usage_with_risk': float(risk_usage.max()),
        'violations': violations, 'waived': waived,
        'misread_data_modules': int(wrong.sum()),
    }


def _bilinear(Y, xs, ys):
    h, w = Y.shape
    xs = np.clip(xs, 0, w - 1.001)
    ys = np.clip(ys, 0, h - 1.001)
    x0, y0 = np.floor(xs).astype(int), np.floor(ys).astype(int)
    fx, fy = xs - x0, ys - y0
    return (Y[y0, x0] * (1 - fx) * (1 - fy) + Y[y0, x0 + 1] * fx * (1 - fy)
            + Y[y0 + 1, x0] * (1 - fx) * fy + Y[y0 + 1, x0 + 1] * fx * fy)


def finder_geometry(struct, rgb_hi, qz, px, T, th):
    """Scan each finder through its center horizontally, vertically and along both diagonals.

    Decoders locate finders by the 1:1:3:1:1 dark/light ratio along such lines (ZXing checks the
    diagonal too). Tolerance is stricter than the decoders' own, to keep a safety margin."""
    Y = luma(rgb_hi)
    n = struct.n
    tol = th['finder_ratio_tolerance']
    step = 0.02
    ts = np.arange(0, 6.5, step)
    results = []
    ok_all = True
    for name, (r0, c0) in zip(['top-left', 'top-right', 'bottom-left'], [(0, 0), (0, n - 7), (n - 7, 0)]):
        cx, cy = (qz + c0 + 3.5) * px, (qz + r0 + 3.5) * px
        for dname, (vx, vy) in {'horizontal': (1, 0), 'vertical': (0, 1),
                                'diagonal': (1, 1), 'anti-diagonal': (1, -1)}.items():
            norm = np.hypot(vx, vy)
            ux, uy = vx / norm, vy / norm
            sides = []
            for sgn in (1, -1):
                prof = _bilinear(Y, cx + sgn * ts * ux * px, cy + sgn * ts * uy * px) < T
                runs = []
                i = 0
                for want in (True, False, True, False):
                    L = 0
                    while i < len(prof) and prof[i] == want:
                        L += 1
                        i += 1
                    runs.append(L * step)
                sides.append(runs)
            (a1, b1, c1, d1), (a2, b2, c2, d2) = sides
            seq = [c2, b2, a1 + a2, b1, c1]
            total = sum(seq)
            ok = a1 > 0 and a2 > 0 and total > 0
            ratios = [0.0] * 5
            if ok:
                unit = total / 7
                ratios = [s / unit for s in seq]
                t = tol if dname in ('horizontal', 'vertical') else th.get('finder_diagonal_tolerance', tol)
                ok = all(abs(rt - e) <= t * e for rt, e in zip(ratios, [1, 1, 3, 1, 1]))
                ok = ok and min(d1, d2) >= 0.5 * unit
                if dname in ('horizontal', 'vertical'):
                    ok = ok and abs(total - 7) <= 7 * 0.2
            ok_all &= ok
            results.append({'finder': name, 'direction': dname, 'ratios': [round(x, 2) for x in ratios],
                            'pass': bool(ok)})
    return ok_all, results


def alignment_geometry(struct, rgb_hi, qz, px, T, th, skip):
    """Solid alignment patterns are judged like decoders see them: along horizontal and vertical lines
    through the center the sequence must be dark (1 module) / light (1) / dark (>= ~1) on both sides."""
    Y = luma(rgb_hi)
    tol = th['finder_ratio_tolerance']
    step = 0.02
    ts = np.arange(0, 3.2, step)
    out, ok_all = [], True
    for (r, c) in struct.alignment_centers():
        if skip[r, c]:
            continue
        cx, cy = (qz + c + 0.5) * px, (qz + r + 0.5) * px
        for dname, (ux, uy) in {'horizontal': (1, 0), 'vertical': (0, 1)}.items():
            sides = []
            for sgn in (1, -1):
                prof = _bilinear(Y, cx + sgn * ts * ux * px, cy + sgn * ts * uy * px) < T
                runs, i = [], 0
                for want in (True, False, True):
                    L = 0
                    while i < len(prof) and prof[i] == want:
                        L += 1
                        i += 1
                    runs.append(L * step)
                sides.append(runs)
            (a1, b1, c1), (a2, b2, c2) = sides
            ok = (abs(a1 + a2 - 1) <= tol and abs(b1 - 1) <= tol and abs(b2 - 1) <= tol
                  and c1 >= 1 - tol and c2 >= 1 - tol)
            ok_all &= ok
            out.append({'alignment': [int(r), int(c)], 'direction': dname,
                        'runs': [round(x, 2) for x in (c2, b2, a1 + a2, b1, c1)], 'pass': bool(ok)})
    return ok_all, out


def color_contrast(dark_colors, light_color):
    """Worst-case symbol contrast over several grayscale conversions used by real decoders."""
    L = hex_rgb(light_color)
    worst = 1.0
    detail = []
    inverted = False
    for dc in dark_colors:
        D = hex_rgb(dc)
        for name, w in GRAY_FORMULAS.items():
            yd, yl = float(D @ np.array(w)), float(L @ np.array(w))
            c = (yl - yd) / 255.0
            if c < 0:
                inverted = True
            worst = min(worst, c)
            detail.append({'dark': dc, 'formula': name, 'contrast': round(c, 3)})
    return worst, inverted, detail


def adjust_lightness(hex_color, factor):
    """Scale lightness in HLS space, keeping hue and saturation (factor < 1 darkens)."""
    import colorsys
    r, g, b = hex_rgb(hex_color) / 255.0
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = min(max(l * factor, 0.0), 1.0)
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return '#%02X%02X%02X' % (round(r * 255), round(g * 255), round(b * 255))
