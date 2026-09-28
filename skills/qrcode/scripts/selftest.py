#!/usr/bin/env python3
"""Regression tests for the engine. Run after changing any code or threshold:

    uv run scripts/qrart.py selftest

1. Structure: module -> codeword -> block mapping reproduces valid Reed-Solomon blocks for random
   payloads across versions, EC levels and masks.
2. Budget model: the static prediction (worst block <= capacity) agrees with real decoders on a
   centered clear area, on both sides of the failure boundary.
3. Gates: a set of specs with known expected outcomes (PASS / FAIL for a specific reason).
4. Tools and deliverables: explore, extra print widths, cards, calibration, and PDFs that decode.
"""
import os
import random
import string
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from qrlib import analyze as A  # noqa: E402
from qrlib import verify as V  # noqa: E402
from qrlib.config import load_spec, load_thresholds  # noqa: E402
from qrlib.render import baseline_svg, render_svg  # noqa: E402
from qrlib.resolve import build  # noqa: E402
from qrlib.structure import Structure  # noqa: E402

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def test_structure():
    print('structure mapping')
    import segno
    rnd = random.Random(11)
    n = ok = 0
    for _ in range(120):
        L = rnd.choice([3, 20, 60, 150, 400, 900, 1600])
        txt = ''.join(rnd.choice(string.ascii_letters + string.digits + ':/?.=&-_') for _ in range(L))
        try:
            s = Structure(txt, rnd.choice('LMQH'), mask=rnd.randrange(8))
        except segno.DataOverflowError:
            continue
        n += 1
        ok += s.self_check()
    check(ok == n, f'RS self-check {ok}/{n}')


def test_budget_model():
    print('budget model vs decoders')
    url = 'https://example.org/budget-model-check/0001'
    for ec in ('H', 'M'):
        st = Structure(url, ec)
        c = st.n // 2
        seen_pass = seen_fail = False
        agree = True
        for k in range(3, st.n, 2):
            cleared = np.zeros((st.n, st.n), bool)
            cleared[c - k // 2:c + k // 2 + 1, c - k // 2:c + k // 2 + 1] = True
            if (cleared & np.isin(st.role, ['alignment', 'timing', 'finder', 'format', 'version'])
                    & ~st.central_alignment_mask()).any():
                break
            per, u = st.static_budget(cleared)
            predicted = per.max() <= st.capacity
            style = {'modules': {'shape': 'square', 'size': 1.0, 'radius': 0},
                     'finder': {'outer': 'square', 'inner': 'square', 'color': None},
                     'alignment': {'style': 'modules'}, 'colors': {'dark': '#000000', 'light': '#FFFFFF'}}
            svg = render_svg(st, style, {'cleared': cleared}, 4)
            g = np.round(A.luma(A.rasterize(svg, st.n + 8, 10))).astype(np.uint8)
            actual = all(V.ALL_DECODERS[d](g) == url for d in V.available(['zxing', 'zbar']))
            agree &= predicted == actual
            seen_pass |= predicted
            seen_fail |= not predicted
            if not predicted and not actual:
                break
        check(agree and seen_pass and seen_fail, f'{st.version}-{ec}: prediction matches decoders across the boundary')


def _assets(tmp):
    from PIL import Image, ImageDraw
    im = Image.new('RGBA', (300, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([10, 10, 290, 290], fill=(30, 60, 120, 255))
    im.save(os.path.join(tmp, 'logo.png'))
    wide = Image.new('RGBA', (600, 150), (0, 0, 0, 0))
    dw = ImageDraw.Draw(wide)
    for i in range(5):
        dw.rounded_rectangle([10 + i * 118, 10, 110 + i * 118, 140], radius=30, fill=(14, 94, 111, 255))
    wide.save(os.path.join(tmp, 'wide.png'))
    with open(os.path.join(tmp, 'bar.svg'), 'w') as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                '<rect x="42" y="0" width="16" height="100"/></svg>')
    with open(os.path.join(tmp, 'sparkle.svg'), 'w') as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><path fill="#2E7D32" '
                'd="M50 5C55 40 60 45 95 50C60 55 55 60 50 95C45 60 40 55 5 50C40 45 45 40 50 5Z"/></svg>')


CASES = [
    ('plain', 'content: {url: "https://example.org/a"}\n', 'PASS', None),
    ('logo+soft', 'content: {url: "https://example.org/events/2026"}\ntheme: soft\n'
                  'center: {image: logo.png}\ncolors: {dark: "#1B3A6B"}\n', 'PASS', None),
    ('dots', 'content: {wifi: {ssid: Guest, password: pw123456}}\ntheme: dots\n', 'PASS', None),
    ('thin finder icon', 'content: {url: "https://example.org/a"}\n'
                         'finder: {inner: icon, icon: bar.svg}\n', 'FAIL', 'finder geometry'),
    ('low contrast', 'content: {url: "https://example.org/a"}\n'
                     'colors: {dark: "#F28C28", auto_repair: false}\n', 'FAIL', 'contrast'),
    ('contrast repaired', 'content: {url: "https://example.org/a"}\ncolors: {dark: "#F28C28"}\n', 'PASS', None),
    ('oversized logo', 'content: {url: "https://example.org/events/2026"}\n'
                       'center: {image: logo.png, size: 0.38}\nec: M\n', 'FAIL', 'error budget'),
    ('wide logo', 'content: {url: "https://example.org/menu"}\ntheme: soft\ncenter: {image: wide.png}\n', 'PASS', None),
    ('gradient + svg decoration', 'content: {url: "https://example.org/events/2026"}\ntheme: soft\n'
                                  'colors: {dark: {gradient: ["#0B3D91", "#6A1B9A"], angle: 45}}\n'
                                  'decorations:\n  - {image: sparkle.svg, size: 4, anchor: bottom-right}\n',
     'PASS', None),
    ('fixed square plate', 'content: {url: "https://example.org/events/2026"}\n'
                           'center: {image: logo.png, plate: square, padding: 1}\n', 'PASS', None),
    ('circular finder rings', 'content: {wifi: {ssid: Guest, password: pw123456}}\n'
                              'modules: {shape: dot}\nfinder: {outer: circle, inner: circle}\n', 'FAIL', 'display scale'),
    ('too small print', 'content: {url: "https://example.org/a/very/long/path/that/needs/a/bigger/symbol/'
                        '?utm_source=x&utm_medium=y&utm_campaign=z"}\nprint: {width_mm: 12}\n',
     'FAIL', 'minimum'),
]


def test_gates():
    print('gates')
    th = load_thresholds()
    with tempfile.TemporaryDirectory() as tmp:
        _assets(tmp)
        for name, yml, want, reason in CASES:
            p = os.path.join(tmp, name.replace(' ', '_') + '.yaml')
            with open(p, 'w') as f:
                f.write(yml)
            spec, _ = load_spec(p)
            res = build(spec, th)
            fails = ' '.join(' '.join(a['failures']) for a in res['attempts'])
            ok = res['status'] == want and (reason is None or reason in fails)
            check(ok, f"{name}: expected {want}{' (' + reason + ')' if reason else ''}, got {res['status']}"
                      + ('' if ok else f' [{fails[:160]}]'))


def test_tools():
    print('explore, card, calibration')
    import yaml
    from qrlib.card import build_card, load_card
    from qrlib.config import SpecError
    from qrlib.payload import make_payload
    from qrlib.report import write_outputs
    from qrlib.resolve import distance_k, prepare_assets, tradeoffs
    th = load_thresholds()
    with tempfile.TemporaryDirectory() as tmp:
        _assets(tmp)
        sp = os.path.join(tmp, 'e.yaml')
        with open(sp, 'w') as f:
            f.write('content: {url: "https://example.org/events/2026"}\ncenter: {image: logo.png}\n'
                    'print: {width_mm: 30, extra_widths_mm: [40]}\noutput: {formats: [svg, png, pdf]}\n')
        spec, layers = load_spec(sp)
        t = tradeoffs(make_payload(spec['content']), spec, th, prepare_assets(spec))
        fr = [f for row in t['rows'] for f in row['center'].values() if f]
        check(len(t['rows']) >= 2 and fr and max(fr) >= 0.2, f'explore: {len(t["rows"])} symbols, best image {max(fr):.0%}')
        res = build(spec, th)
        out = os.path.join(tmp, 'b')
        rep = write_outputs(res, spec, th, layers, out, sp)
        check(rep['status'] == 'PASS' and len(rep['sizes']) == 2 and 'svg_40mm' in rep['files'],
              'build with extra print width writes both sizes')
        payload = rep['content']['payload']
        gating = V.available(th['gating_decoders'])
        n = rep['symbol']['canvas_modules']
        for key, mm in (('pdf', 30), ('pdf_40mm', 40)):
            width_pt = _pdf_width_pt(rep['files'][key])
            check(abs(width_pt - mm / 25.4 * 72) < 0.1 and _pdf_decodes(rep['files'][key], n * 12, payload, gating),
                  f'{key}: page is {mm} mm wide and decodes')
        cp = os.path.join(tmp, 'card.yaml')
        with open(cp, 'w') as f:
            yaml.safe_dump({'qr': 'b', 'canvas': {'width_px': 1200, 'height_px': 1600}, 'title': 'Scan me',
                            'lines': ['line one', 'line two']}, f)
        crep = build_card(load_card(cp), th, os.path.join(tmp, 'c'))
        check(crep['status'] == 'PASS', f"screen card decodes after layout: {crep['checks']}")
        check(_pdf_decodes(crep['files']['pdf'], 1200, payload, gating), 'card PDF contains the code and decodes')
        with open(cp, 'w') as f:
            yaml.safe_dump({'qr': 'b', 'canvas': {'width_mm': 30, 'height_mm': 40}, 'qr_size': 0.3}, f)
        crep = build_card(load_card(cp), th, os.path.join(tmp, 'c2'))
        check(crep['status'] == 'FAIL', 'print card with too small a code fails the module-size check')
        with open(cp, 'w') as f:
            yaml.safe_dump({'qr': 'b', 'canvas': {'width_mm': 85, 'height_mm': 55, 'dpi': 300}, 'background': '#F3E9DC',
                            'title': 'Scan me', 'qr_size': 0.4}, f)
        crep = build_card(load_card(cp), th, os.path.join(tmp, 'c3'))
        from PIL import Image
        img = Image.open(crep['files']['png'])
        exact = img.size == (round(85 / 25.4 * 300), round(55 / 25.4 * 300))
        opaque = img.mode != 'RGBA' or img.getchannel('A').getextrema()[0] == 255
        check(crep['status'] == 'PASS' and exact and opaque and abs(_pdf_width_pt(crep['files']['pdf']) - 85 / 25.4 * 72) < 0.1,
              f'print card: exact {img.size} px, no transparent edge, 85 mm PDF page')
        cal = os.path.join(tmp, 'cal.yaml')
        with open(cal, 'w') as f:
            yaml.safe_dump({'observations': [{'module_mm': 0.5, 'max_distance_mm': d} for d in (200, 250, 300)]}, f)
        th2 = dict(th, _calibration_path=cal)
        k, label = distance_k(th2)
        check(abs(k - 500) < 1e-6 and 'calibrated' in label, f'calibration median -> {k:.0f} ({label})')


def test_setup():
    print('setup (uv and no-uv paths)')
    import re
    import tomllib
    import bootstrap
    with open(os.path.join(HERE, 'qrart.py.lock'), 'rb') as f:
        locked = {bootstrap._canon(p['name']): p['version'] for p in tomllib.load(f)['package']}
    pins = bootstrap.pinned()
    check(pins and {k: v for k, (v, _) in pins.items()} == locked and not any(c for _, c in pins.values()),
          f'requirements.lock.txt pins the same {len(locked)} packages as qrart.py.lock, none platform-conditional')
    check(bootstrap.matches_lock(), 'this run uses exactly the pinned versions')
    with tempfile.TemporaryDirectory() as tmp:
        py = bootstrap.env_python(tmp)
        os.makedirs(os.path.dirname(py))
        for name, text in ((py, ''), (os.path.join(tmp, bootstrap.READY), 'x')):
            with open(name, 'w') as f:
                f.write(text)
        states = []
        for home in (sys.base_prefix, os.path.join(tmp, 'removed-python')):
            with open(os.path.join(tmp, 'pyvenv.cfg'), 'w') as f:
                f.write(f'home = {home}\n')
            states.append(bootstrap._healthy(tmp))
        check(states == [True, False], 'a private environment whose base Python is gone gets rebuilt')
    with open(os.path.join(HERE, 'qrart.py'), encoding='utf-8') as f:
        m = re.search(r'requires-python = ">=(\d+)\.(\d+),<(\d+)\.(\d+)"', f.read(800))
    rng = m and ((int(m.group(1)), int(m.group(2))), (int(m.group(3)), int(m.group(4))))
    check(rng == (bootstrap.PY_MIN, bootstrap.PY_END), f'no-uv Python range equals requires-python {rng}')


def test_rendering():
    print('rendering, inputs, thresholds')
    import gzip
    import io
    from PIL import Image
    from qrlib.raster import svg_text, svg_to_png
    st = Structure('https://example.org/events/2026', 'H')
    W = st.n + 8
    same = True
    for mm in (12, 30, 33.3):
        for px in (16, 48):
            a = svg_to_png(baseline_svg(st, 4, size_attr=(W * 16, 'px')), W * px, W * px)
            b = svg_to_png(baseline_svg(st, 4, size_attr=(mm, 'mm')), W * px, W * px)
            same &= np.array_equal(np.array(Image.open(io.BytesIO(a))), np.array(Image.open(io.BytesIO(b))))
    check(same, 'a print-size (mm) SVG rasterizes pixel-identical to the verified px raster')
    svg = '<?xml version="1.0" encoding="{enc}"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><title>café</title></svg>'
    variants = [gzip.compress(svg.format(enc='utf-8').encode('utf-8')), svg.format(enc='utf-16').encode('utf-16'),
                svg.format(enc='ISO-8859-1').encode('latin-1')]
    check(all('café' in svg_text(v) for v in variants), 'SVG text from .svgz, UTF-16 and ISO-8859-1 files')
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, 'th.yaml')
        with open(p, 'w') as f:
            f.write('max_block_usage: 0.6\n')
        th = load_thresholds(p)
        check(th['max_block_usage'] == 0.6 and th['display_scales_px'] and th['boundary_ratio']['default'] == 0.7,
              'a partial --thresholds file keeps every other built-in value')


def _pdf_width_pt(path):
    import pymupdf
    with pymupdf.open(path) as doc:
        return doc[0].rect.width


def _pdf_decodes(path, width_px, payload, gating):
    import io
    from PIL import Image
    from qrlib.raster import pdf_first_page_png
    with open(path, 'rb') as f:
        png = pdf_first_page_png(f.read(), width_px)
    rgb = np.array(Image.open(io.BytesIO(png)).convert('RGB')).astype(float)
    dec = V.decode_all(np.round(A.luma(rgb)).astype(np.uint8), gating)
    return bool(gating) and all(dec.get(d) == payload for d in gating)


def main():
    test_setup()
    test_structure()
    test_budget_model()
    test_gates()
    test_rendering()
    test_tools()
    print('\nALL OK' if not FAILS else f'\n{len(FAILS)} FAILED')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
