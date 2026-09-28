"""Image assets: every image, whatever its source (user file or agent-drawn SVG), is normalized
into the same contract: a data URI for embedding, an aspect ratio, and an alpha footprint."""
import base64
import io
import os

import numpy as np
from PIL import Image

from .raster import svg_to_png

_MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
         '.svg': 'image/svg+xml'}


def _hex_rgb(h):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(ch * 2 for ch in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class Asset:
    def __init__(self, path, recolor=None):
        self.path = path
        ext = os.path.splitext(path)[1].lower()
        if ext not in _MIME:
            raise ValueError(f'{path}: unsupported image type {ext}; use PNG, JPG, WEBP or SVG')
        self.is_svg = ext == '.svg'
        with open(path, 'rb') as f:
            raw = f.read()
        self.pil = self._to_pil(raw)  # RGBA raster used for analysis, colors, aspect
        self.aspect = self.pil.width / self.pil.height
        if recolor:
            self.pil = recolor_alpha(self.pil, recolor)
            self.mime, self.data = 'image/png', _png_bytes(self.pil)
        elif ext == '.webp':
            self.mime, self.data = 'image/png', _png_bytes(self.pil)
        else:
            self.mime, self.data = _MIME[ext], raw

    def _to_pil(self, raw):
        try:
            if self.is_svg:
                return Image.open(io.BytesIO(svg_to_png(raw, 1024))).convert('RGBA')
            return Image.open(io.BytesIO(raw)).convert('RGBA')
        except Exception as e:  # resvg, PIL and decoding errors all mean the same thing to the user
            raise ValueError(f'{self.path}: cannot read the image ({e})') from e

    def data_uri(self):
        return f'data:{self.mime};base64,{base64.b64encode(self.data).decode()}'

    def fit(self, box_w, box_h):
        """Size of the image fitted inside a box, preserving aspect ratio."""
        if box_w / box_h > self.aspect:
            return box_h * self.aspect, box_h
        return box_w, box_w / self.aspect

    def dominant_colors(self, k=6):
        """Deterministic palette (median cut) of opaque, non-near-white pixels, most frequent first."""
        arr = np.array(self.pil)
        mask = arr[..., 3] > 128
        px = arr[mask][:, :3]
        if len(px) == 0:
            return []
        lum = px @ np.array([0.299, 0.587, 0.114])
        px = px[lum < 235]
        if len(px) == 0:
            return []
        img = Image.fromarray(px.reshape(-1, 1, 3).astype(np.uint8), 'RGB')
        q = img.quantize(colors=k, method=Image.Quantize.MEDIANCUT)
        pal = q.getpalette()[:3 * k]
        counts = sorted(q.getcolors(), reverse=True)
        return ['#%02X%02X%02X' % tuple(pal[3 * i:3 * i + 3]) for _, i in counts]


def recolor_alpha(img, color):
    rgb = _hex_rgb(color)
    arr = np.array(img.convert('RGBA'))
    out = np.zeros_like(arr)
    out[..., 0], out[..., 1], out[..., 2] = rgb
    out[..., 3] = arr[..., 3]
    return Image.fromarray(out, 'RGBA')


def _png_bytes(img):
    b = io.BytesIO()
    img.save(b, 'PNG')
    return b.getvalue()
