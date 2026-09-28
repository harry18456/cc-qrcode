"""Center-image geometry: logo box (aspect-aware), footprint-based clearing, and plate shapes.

The modules that must be cleared are decided by the logo's actual opaque footprint (dilated by the
padding), not by its bounding square, so round logos and wide wordmarks cost only what they cover.

`size` is the equivalent-square fraction: sqrt(logo_w * logo_h) / symbol width. For a square logo it
is simply the width fraction; a 3:1 wordmark at the same size is wider and flatter but costs about
the same area.
"""
import math

import cv2
import numpy as np

R = 8          # working resolution (px per module) for footprints
R_PATH = 16    # resolution for the vector outline of 'hug' plates

PLATES = ('rounded', 'square', 'circle', 'hug', 'none')


def _alpha(asset):
    if not hasattr(asset, '_alpha_cache'):
        a = np.array(asset.pil)[..., 3].astype(np.float32) / 255.0
        ys, xs = np.where(a > 0.1)
        asset.trim_box = (0, 0, a.shape[1], a.shape[0])
        if len(xs):
            asset.trim_box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
            a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]  # trim transparent margins
        asset._alpha_cache = a
        h, w = a.shape
        asset.trim_aspect = w / h
        asset.fill_ratio = float((a > 0.1).mean())
    return asset._alpha_cache


def placement(asset, box):
    """Where to draw the full image so that its opaque (trimmed) part fills the logo box exactly."""
    _alpha(asset)
    x0t, y0t, x1t, y1t = asset.trim_box
    W, H = asset.pil.size
    lx0, ly0, lx1, ly1 = box
    sc = (lx1 - lx0) / (x1t - x0t)
    return lx0 - x0t * sc, ly0 - y0t * sc, W * sc, H * sc


def is_roundish(asset):
    _alpha(asset)
    return 0.85 <= asset.trim_aspect <= 1.18 and 0.70 <= asset.fill_ratio <= 0.86


def logo_box(n, asset, frac):
    _alpha(asset)
    a = asset.trim_aspect
    s = frac * n
    w, h = s * math.sqrt(a), s / math.sqrt(a)
    c = n / 2
    return (c - w / 2, c - h / 2, c + w / 2, c + h / 2)


def _footprint(n, asset, box, padding, res):
    """Dilated opaque footprint of the logo on an (n*res)^2 raster of the symbol area."""
    alpha = _alpha(asset)
    x0, y0, x1, y1 = box
    w_px = max(int(round((x1 - x0) * res)), 1)
    h_px = max(int(round((y1 - y0) * res)), 1)
    a = cv2.resize(alpha, (w_px, h_px), interpolation=cv2.INTER_AREA) > 0.1
    canvas = np.zeros((n * res, n * res), np.uint8)
    ox, oy = int(round(x0 * res)), int(round(y0 * res))
    ys0, xs0 = max(oy, 0), max(ox, 0)
    ys1, xs1 = min(oy + h_px, n * res), min(ox + w_px, n * res)
    if ys1 > ys0 and xs1 > xs0:
        canvas[ys0:ys1, xs0:xs1] = a[ys0 - oy:ys1 - oy, xs0 - ox:xs1 - ox]
    rad = int(round(padding * res))
    if rad > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rad + 1, 2 * rad + 1))
        canvas = cv2.dilate(canvas, k)
    return canvas


def _cells_from_raster(canvas, n, res):
    return canvas.reshape(n, res, n, res).max(axis=(1, 3)).astype(bool)


def _cells_rect(n, box):
    x0, y0, x1, y1 = box
    m = np.zeros((n, n), bool)
    r0, r1 = max(int(math.floor(y0 + 1e-6)), 0), min(int(math.ceil(y1 - 1e-6)), n)
    c0, c1 = max(int(math.floor(x0 + 1e-6)), 0), min(int(math.ceil(x1 - 1e-6)), n)
    if r1 > r0 and c1 > c0:
        m[r0:r1, c0:c1] = True
    return m


def _cells_circle(n, cx, cy, rad):
    rr, cc = np.mgrid[0:n, 0:n]
    dx = np.maximum.reduce([cc - cx, np.zeros_like(cc, float), cx - (cc + 1)])
    dy = np.maximum.reduce([rr - cy, np.zeros_like(rr, float), cy - (rr + 1)])
    return dx * dx + dy * dy < rad * rad - 1e-9


def geometry(n, asset, frac, plate, padding):
    """Return {'logo_box', 'plate': {...} or None, 'cleared': n x n bool}."""
    box = logo_box(n, asset, frac)
    fp = _footprint(n, asset, box, padding, R)
    if not fp.any():
        return {'logo_box': box, 'plate': None, 'cleared': np.zeros((n, n), bool)}
    ys, xs = np.where(fp > 0)
    if plate in ('rounded', 'square'):
        rect = (xs.min() / R, ys.min() / R, (xs.max() + 1) / R, (ys.max() + 1) / R)
        cleared = _cells_rect(n, rect)
        rad = 0.18 * min(rect[2] - rect[0], rect[3] - rect[1]) if plate == 'rounded' else 0.0
        pl = {'type': 'rect', 'box': rect, 'radius': rad}
    elif plate == 'circle':
        c = n / 2
        rad = float(np.sqrt(((xs + 0.5) / R - c) ** 2 + ((ys + 0.5) / R - c) ** 2).max()) + 0.5 / R
        cleared = _cells_circle(n, c, c, rad)
        pl = {'type': 'circle', 'cx': c, 'cy': c, 'r': rad}
    else:  # hug / none
        cleared = _cells_from_raster(fp, n, R)
        pl = None
        if plate == 'hug':
            hi = _footprint(n, asset, box, padding, R_PATH)
            contours, _ = cv2.findContours(hi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            parts = []
            for cnt in contours:
                cnt = cv2.approxPolyDP(cnt, 0.6, True)
                if len(cnt) < 3:
                    continue
                pts = ' '.join(f'{p[0][0] / R_PATH:.3f},{p[0][1] / R_PATH:.3f}' for p in cnt)
                parts.append(f'M{pts}Z')
            pl = {'type': 'path', 'd': ''.join(parts)}
    return {'logo_box': box, 'plate': pl, 'cleared': cleared}


def combos(asset, plate, padding):
    """Ordered (plate, padding) candidates: user-fixed values are kept, 'auto' ones are searched."""
    if plate == 'auto':
        plates = ['circle', 'rounded', 'hug'] if is_roundish(asset) else ['rounded', 'hug']
    else:
        plates = [plate]
    pads = [1, 0.5] if padding == 'auto' else [padding]
    out = [(p, d) for d in pads for p in plates]
    if plate == 'auto' and padding == 'auto':
        out.append(('hug', 0.25))
    return out
