"""Layouts: put a verified code on a canvas with text (table cards, signs, screens, tablets).

The code is embedded unchanged (the build's own SVG, only scaled), text never enters its quiet zone,
and the finished card is decoded again, so a layout cannot silently break a verified code."""
import base64
import html
import io
import json
import os
import subprocess

import numpy as np
from PIL import Image

from . import analyze as A
from . import verify as V
from .config import SpecError, deep_merge, load_yaml
from .raster import png_to_pdf, svg_to_png

CARD_DEFAULTS = {
    'qr': None,
    'canvas': {'width_px': None, 'height_px': None, 'width_mm': None, 'height_mm': None, 'dpi': 300},
    'background': '#FFFFFF',
    'text_color': '#111111',
    # first installed family wins: Windows, macOS, then Linux names; Traditional Chinese first
    'font': ('Microsoft JhengHei, PingFang TC, Noto Sans CJK TC, Microsoft YaHei, PingFang SC, '
             'Noto Sans CJK SC, Yu Gothic, Hiragino Sans, Noto Sans CJK JP, Segoe UI, Helvetica Neue, '
             'Noto Sans, DejaVu Sans, sans-serif'),
    'title': None,
    'subtitle': None,
    'lines': [],
    'qr_size': 0.62,
    'margin': 0.07,
    'style': {'accent': None, 'title_rule': False, 'lines_pill': False, 'panel': False},
    'output': {'formats': ['png', 'pdf', 'svg'], 'basename': 'card'},
}
MIN_SCREEN_PX_PER_MODULE = 4


def _is_wide(ch):
    o = ord(ch)
    return o >= 0x2E80 and not (0xFF61 <= o <= 0xFFDC)


def _text_width(text, size):
    return sum(size * (1.0 if _is_wide(ch) else 0.58) for ch in text)


_CJK_FONT_FILES = ('msjh', 'msyh', 'mingliu', 'simsun', 'simhei', 'yugoth', 'meiryo', 'msgothic', 'malgun',
                   'pingfang', 'hiragino', 'songti', 'stheiti', 'notosanscjk', 'notoserifcjk', 'sourcehansans',
                   'wqy', 'droidsansfallback')


def _has_cjk_font():
    """Windows and macOS: look for a known CJK font file; Linux: ask fontconfig."""
    dirs = [os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts'),
            '/System/Library/Fonts', '/System/Library/Fonts/Supplemental', '/Library/Fonts',
            os.path.expanduser('~/Library/Fonts')]
    if os.environ.get('LOCALAPPDATA'):
        dirs.append(os.path.join(os.environ['LOCALAPPDATA'], 'Microsoft', 'Windows', 'Fonts'))
    scanned = False
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        scanned = True
        if any(n.lower().replace(' ', '').replace('-', '').startswith(_CJK_FONT_FILES) for n in names):
            return True
    try:
        out = subprocess.run(['fc-list', ':lang=zh'], capture_output=True, text=True, timeout=10).stdout
        return bool(out.strip())
    except Exception:
        return not scanned  # font folders listed without a match: warn; nothing to inspect: stay quiet


def load_card(path):
    raw = load_yaml(path)
    extra = set(raw) - set(CARD_DEFAULTS)
    if extra:
        raise SpecError(f'card: unknown key(s) {sorted(extra)}; allowed: {sorted(CARD_DEFAULTS)}')
    spec = deep_merge(CARD_DEFAULTS, raw)
    base = os.path.dirname(os.path.abspath(path))
    if not spec['qr']:
        raise SpecError('card.qr: path to a build output folder is required')
    spec['qr'] = os.path.normpath(os.path.join(base, spec['qr']))
    cv = spec['canvas']
    if not ((cv['width_px'] and cv['height_px']) or (cv['width_mm'] and cv['height_mm'])):
        raise SpecError('card.canvas: give width_px + height_px (screens) or width_mm + height_mm (print)')
    if not isinstance(spec['lines'], list):
        raise SpecError('card.lines: expected a list of strings')
    extra = set(spec['style']) - set(CARD_DEFAULTS['style'])
    if extra:
        raise SpecError(f"card.style: unknown key(s) {sorted(extra)}; allowed: {sorted(CARD_DEFAULTS['style'])}")
    return spec


def build_card(spec, th, outdir):
    rep_path = os.path.join(spec['qr'], 'report.json')
    if not os.path.exists(rep_path):
        raise SpecError(f'card.qr: no report.json in {spec["qr"]} (run a build first)')
    with open(rep_path, encoding='utf-8') as f:
        rep = json.load(f)
    if rep['status'] != 'PASS':
        raise SpecError('card.qr: the build did not pass; fix the code before laying it out')
    svg_path = rep['files'].get('svg')
    if not svg_path or not os.path.exists(svg_path):
        raise SpecError('card.qr: the build has no SVG output; add svg to output.formats')
    with open(svg_path, 'rb') as f:
        qr_svg = f.read()
    payload = rep['content']['payload']
    canvas_modules = rep['symbol']['canvas_modules']
    light = rep['colors']['light']

    cv = spec['canvas']
    physical = bool(cv['width_mm'])
    if physical:
        W = cv['width_mm'] / 25.4 * cv['dpi']
        H = cv['height_mm'] / 25.4 * cv['dpi']
    else:
        W, H = float(cv['width_px']), float(cv['height_px'])
    m = spec['margin'] * min(W, H)
    avail_w = W - 2 * m

    blocks = []  # (kind, text, size)
    if spec['title']:
        blocks.append(('title', spec['title'], 0.085 * min(W, H)))
    if spec['subtitle']:
        blocks.append(('subtitle', spec['subtitle'], 0.05 * min(W, H)))
    lines = [('line', t, 0.045 * min(W, H)) for t in spec['lines']]
    fitted = []
    for kind, text, size in blocks + lines:
        w = _text_width(text, size)
        if w > avail_w:
            size *= avail_w / w
        fitted.append((kind, text, size))
    top = [b for b in fitted if b[0] in ('title', 'subtitle')]
    bottom = [b for b in fitted if b[0] == 'line']
    sty = spec['style']
    accent = sty.get('accent') or spec['text_color']
    unit = min(W, H)
    gap = 0.04 * unit
    rule_h = 0.007 * unit if (sty.get('title_rule') and top) else 0
    rule_block = rule_h + (0.025 * unit if rule_h else 0)
    pill_pad = 0.022 * unit if (sty.get('lines_pill') and bottom) else 0
    panel_pad = 0 if not sty.get('panel') else 0.035 * unit
    top_h = sum(b[2] * 1.35 for b in top) + rule_block
    bot_h = sum(b[2] * 1.35 for b in bottom) + 2 * pill_pad
    q = min(spec['qr_size'] * W, avail_w - 2 * panel_pad,
            H - 2 * m - top_h - bot_h - 2 * panel_pad - (gap if top else 0) - (gap if bottom else 0))
    if q <= 0:
        raise SpecError('card: not enough room for the code; shorten the text or enlarge the canvas')
    total = top_h + (gap if top else 0) + q + 2 * panel_pad + (gap if bottom else 0) + bot_h
    y = (H - total) / 2
    font = html.escape(spec['font'], quote=True)
    color = spec['text_color']
    parts = [f'<rect width="{W:.2f}" height="{H:.2f}" fill="{spec["background"]}"/>']
    for kind, text, size in top:
        weight = '700' if kind == 'title' else '400'
        parts.append(f'<text x="{W / 2:.2f}" y="{y + size * 1.02:.2f}" font-family="{font}" font-size="{size:.2f}" '
                     f'font-weight="{weight}" fill="{color}" text-anchor="middle">{html.escape(text)}</text>')
        y += size * 1.35
        if kind == 'title' and rule_h:
            rw = 0.12 * W
            parts.append(f'<rect x="{(W - rw) / 2:.2f}" y="{y + 0.006 * unit:.2f}" width="{rw:.2f}" '
                         f'height="{rule_h:.2f}" rx="{rule_h / 2:.2f}" fill="{accent}"/>')
            y += rule_block
    if top:
        y += gap
    qx = (W - q) / 2
    if panel_pad:
        pw = q + 2 * panel_pad
        parts.append(f'<rect x="{qx - panel_pad:.2f}" y="{y:.2f}" width="{pw:.2f}" height="{pw:.2f}" '
                     f'rx="{0.06 * pw:.2f}" fill="{light}" stroke="#000000" stroke-opacity="0.10" '
                     f'stroke-width="{0.002 * unit:.2f}"/>')
        y += panel_pad
    qy = y
    parts.append(f'<rect x="{qx:.2f}" y="{qy:.2f}" width="{q:.2f}" height="{q:.2f}" fill="{light}"/>')
    parts.append(f'<image x="{qx:.2f}" y="{qy:.2f}" width="{q:.2f}" height="{q:.2f}" '
                 f'href="data:image/svg+xml;base64,{base64.b64encode(qr_svg).decode()}"/>')
    y += q + panel_pad + (gap if bottom else 0)
    if pill_pad:
        tw = max(_text_width(t, sz) for _, t, sz in bottom)
        ph = sum(sz * 1.35 for _, _, sz in bottom) + 2 * pill_pad
        pw = min(tw + 4 * pill_pad, avail_w)
        parts.append(f'<rect x="{(W - pw) / 2:.2f}" y="{y:.2f}" width="{pw:.2f}" height="{ph:.2f}" '
                     f'rx="{min(ph / 2, 0.04 * unit):.2f}" fill="{accent}" fill-opacity="0.12"/>')
        y += pill_pad
    for kind, text, size in bottom:
        parts.append(f'<text x="{W / 2:.2f}" y="{y + size * 1.02:.2f}" font-family="{font}" font-size="{size:.2f}" '
                     f'fill="{color}" text-anchor="middle">{html.escape(text)}</text>')
        y += size * 1.35
    if physical:
        size_attr = f'width="{cv["width_mm"]}mm" height="{cv["height_mm"]}mm"'
    else:
        size_attr = f'width="{W:.0f}px" height="{H:.0f}px"'
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" {size_attr} '
           f'viewBox="0 0 {W:.2f} {H:.2f}">' + ''.join(parts) + '</svg>')

    os.makedirs(outdir, exist_ok=True)
    base = spec['output']['basename']
    files = {}
    png = svg_to_png(svg, int(round(W)), int(round(H)))
    if 'svg' in spec['output']['formats']:
        files['svg'] = os.path.join(outdir, base + '.svg')
        with open(files['svg'], 'w', encoding='utf-8') as f:
            f.write(svg)
    if 'png' in spec['output']['formats']:
        files['png'] = os.path.join(outdir, base + '.png')
        with open(files['png'], 'wb') as f:
            f.write(png)
    if 'pdf' in spec['output']['formats']:
        # the PDF carries the PNG decoded below, so the checked card is exactly the delivered one
        files['pdf'] = os.path.join(outdir, base + '.pdf')
        page_w, page_h = (cv['width_mm'], cv['height_mm']) if physical else (W * 25.4 / 96, H * 25.4 / 96)
        with open(files['pdf'], 'wb') as f:
            f.write(png_to_pdf(png, page_w, page_h))

    # checks on the finished card
    img = Image.open(io.BytesIO(png)).convert('RGB')
    gray = np.round(A.luma(np.array(img).astype(float))).astype(np.uint8)
    gating = V.available(th['gating_decoders'])
    dec = V.decode_all(gray, gating)
    decode_ok = {d: dec.get(d) == payload for d in gating}
    checks, warnings = [], []
    px_per_module = q / canvas_modules
    if physical:
        qr_mm = q / W * cv['width_mm']
        module_mm = qr_mm / canvas_modules
        checks.append(('module size', module_mm >= th['min_module_mm'],
                       f'{module_mm:.2f} mm (minimum {th["min_module_mm"]} mm); code printed {qr_mm:.1f} mm wide'))
        factor = rep['physical'].get('distance_factor') or 1.0
        k = rep['physical'].get('distance_k') or th['distance_mm_per_module_mm']
        warnings.append(f'estimated max scan distance about {k * module_mm * factor / 10:.0f} cm (heuristic)')
    else:
        checks.append(('screen resolution', px_per_module >= MIN_SCREEN_PX_PER_MODULE,
                       f'{px_per_module:.1f} px per module (minimum {MIN_SCREEN_PX_PER_MODULE})'))
    checks.append(('decodes after layout', all(decode_ok.values()), f'{decode_ok}'))
    text = ' '.join(t for _, t, _ in fitted)
    if any(_is_wide(ch) for ch in text) and not _has_cjk_font():
        warnings.append('no CJK font found on this system: CJK text may render as empty boxes')
    status = 'PASS' if all(c[1] for c in checks) else 'FAIL'
    card_rep = {'status': status, 'qr_build': spec['qr'], 'payload': payload,
                'canvas': cv, 'code_size_px': round(q, 1), 'checks': [
                    {'name': n, 'pass': bool(ok), 'detail': d} for n, ok, d in checks],
                'warnings': warnings, 'files': files}
    files['report_json'] = os.path.join(outdir, base + '_report.json')
    with open(files['report_json'], 'w', encoding='utf-8') as f:
        json.dump(card_rep, f, indent=2, ensure_ascii=False)
    md = [f'# Card report: {status}', '']
    md += [f"- {'OK  ' if c['pass'] else 'FAIL'} {c['name']}: {c['detail']}" for c in card_rep['checks']]
    md += [f'- warning: {w}' for w in warnings]
    md += ['', 'Files: ' + ', '.join(os.path.basename(v) for v in files.values())]
    files['report_md'] = os.path.join(outdir, base + '_report.md')
    with open(files['report_md'], 'w', encoding='utf-8') as f:
        f.write('\n'.join(md) + '\n')
    return card_rep
