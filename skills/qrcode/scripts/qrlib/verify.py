"""Decoder ensemble and degradation sweeps.

A single pass/fail on one decoder hides how close a design is to failing, so every axis is swept
until the decoder breaks and the breaking point is compared with a plain rendering of the same
matrix. Decoders are split into gating (must pass) and informational (reported only)."""
import cv2
import numpy as np
import zxingcpp

try:
    from pyzbar import pyzbar
    from pyzbar.pyzbar import ZBarSymbol
    HAVE_ZBAR = True
except Exception:  # libzbar missing
    HAVE_ZBAR = False


def _zxing(img):
    try:
        r = zxingcpp.read_barcodes(img, formats=zxingcpp.BarcodeFormat.QRCode)
    except Exception:
        r = zxingcpp.read_barcodes(img)
    return r[0].text if r else None


def _zbar(img):
    r = pyzbar.decode(img, symbols=[ZBarSymbol.QRCODE])
    return r[0].data.decode('utf-8', 'replace') if r else None


def _opencv(img):
    try:
        t, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
        return t or None
    except cv2.error:
        return None


def _opencv_aruco(img):
    try:
        t, _, _ = cv2.QRCodeDetectorAruco().detectAndDecode(img)
        return t or None
    except (cv2.error, AttributeError):
        return None


ALL_DECODERS = {'zxing': _zxing, 'opencv': _opencv, 'opencv_aruco': _opencv_aruco}
if HAVE_ZBAR:
    ALL_DECODERS['zbar'] = _zbar


def available(names):
    return [n for n in names if n in ALL_DECODERS]


def decode_all(img, names=None):
    names = names or list(ALL_DECODERS)
    return {n: ALL_DECODERS[n](img) for n in names if n in ALL_DECODERS}


# ---- degradations (grayscale uint8, `px` pixels per module) ---------------
def _pad(g, frac=0.25):
    p = int(g.shape[0] * frac)
    return cv2.copyMakeBorder(g, p, p, p, p, cv2.BORDER_CONSTANT, value=255)


def d_blur(g, px, s):
    return cv2.GaussianBlur(g, (0, 0), s * px) if s > 0 else g


def d_downscale(g, px, target):
    size = max(int(round(g.shape[0] * target / px)), 8)
    small = cv2.resize(g, (size, size), interpolation=cv2.INTER_AREA)
    return cv2.copyMakeBorder(small, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=255)


def d_tilt(g, px, deg):
    g = _pad(g)
    h, w = g.shape
    th = np.radians(deg)
    D = 2.5 * w
    cx, cy = w / 2, h / 2
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = []
    for x, y in src:
        X, Y = x - cx, y - cy
        Xr, Z = X * np.cos(th), X * np.sin(th)
        dst.append([cx + D * Xr / (D + Z), cy + D * Y / (D + Z)])
    M = cv2.getPerspectiveTransform(src, np.float32(dst))
    return cv2.warpPerspective(g, M, (w, h), borderValue=255)


def d_cylinder(g, px, a):
    if a <= 0:
        return g
    g = _pad(g, 0.1)
    h, w = g.shape
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    xn = (xs - w / 2) / (w / 2)
    s = np.arcsin(np.clip(xn * np.sin(a), -1, 1)) / a
    return cv2.remap(g, (s * w / 2 + w / 2).astype(np.float32), ys, cv2.INTER_LINEAR, borderValue=255)


def d_jpeg(g, px, q):
    small = d_downscale(g, px, 4.0)
    ok, buf = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, int(q)])
    return cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)


_RNG_SEED = 1234


def d_contrast(g, px, c):
    """Faded print seen by a camera: paper white ~225 (not clipped), ink span 200*c, sensor noise."""
    rng = np.random.default_rng(_RNG_SEED)
    faded = 225 - (255 - g.astype(np.float32)) / 255 * 200 * c
    noisy = faded + rng.normal(0, 6, g.shape)
    return np.clip(noisy, 0, 255).astype(np.uint8)


AXES = {
    'blur_sigma': (d_blur, [round(x, 2) for x in np.arange(0.0, 1.21, 0.05)], 'higher',
                   'Gaussian blur sigma the code survives (in modules)'),
    'min_px_per_module': (d_downscale, [round(4.0 - 0.1 * i, 1) for i in range(26)], 'lower',
                          'smallest image resolution that still decodes (pixels per module)'),
    'tilt_deg': (d_tilt, list(range(0, 80, 5)), 'higher', 'perspective tilt the code survives (degrees)'),
    'cylinder_rad': (d_cylinder, [round(x, 1) for x in np.arange(0.0, 1.41, 0.1)], 'higher',
                     'curvature on a cylinder the code survives (half-angle, radians)'),
    'jpeg_quality': (d_jpeg, list(range(95, 9, -5)), 'lower',
                     'lowest JPEG quality that still decodes (at 4 px/module)'),
    'contrast': (d_contrast, [round(1.0 - 0.05 * i, 2) for i in range(19)], 'lower',
                 'lowest remaining print contrast (with sensor noise) that still decodes'),
}


def sweep(g, px, payload, decoders, axis):
    fn, values, better, _ = AXES[axis]
    best = {d: None for d in decoders}
    fails = {d: 0 for d in decoders}
    alive = set(decoders)
    for v in values:
        if not alive:
            break
        img = fn(g, px, v)
        for d in list(alive):
            if ALL_DECODERS[d](img) == payload:
                best[d] = v
                fails[d] = 0
            else:
                fails[d] += 1
                if fails[d] >= 2 or best[d] is None:
                    alive.discard(d)
    return best


def ratio(axis, styled, base):
    better = AXES[axis][2]
    if base is None:
        return None
    if styled is None:
        return 0.0
    if better == 'higher':
        return 1.0 if base == 0 else min(styled / base, 1.0) if styled < base else 1.0
    return 1.0 if styled <= base else base / styled


def robustness(g_styled, g_base, px, payload, gating, informational, axes=None):
    axes = axes or list(AXES)
    rows = []
    for axis in axes:
        s = sweep(g_styled, px, payload, gating, axis)
        b = sweep(g_base, px, payload, gating, axis)
        for d in gating:
            rows.append({'axis': axis, 'decoder': d, 'styled': s[d], 'baseline': b[d],
                         'ratio': ratio(axis, s[d], b[d])})
    info = {d: ALL_DECODERS[d](g_styled) == payload for d in informational}
    return rows, info
