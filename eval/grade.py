# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = ["numpy>=1.24", "pillow>=10.0", "opencv-python-headless>=4.8", "zxing-cpp>=2.2",
#                 "pyzbar>=0.1.9", "pyyaml>=6.0", "pymupdf>=1.24", "resvg-py>=0.5", "segno>=1.6"]
# [tool.uv]
# exclude-newer = "2026-09-28T00:00:00Z"
# ///
"""Programmatic assertions for the eval runs: uv run eval/grade.py eval/workspace/iteration-1

Each run's delivered files (outputs/) are decoded with the engine's gating decoders, so an assertion
checks what the user would receive. Writes grading_auto.json next to each run's outputs/."""
import glob
import io
import json
import os
import re
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'skills', 'qrcode', 'scripts'))
from qrlib import verify as V  # noqa: E402

GATING = ['zxing', 'zbar']
URLS = {
    1: 'https://example.org/events/2026/autumn-fair',
    3: 'https://www.example-shop.com/collections/autumn-sale/products/merino-wool-cardigan-limited-edition',
    4: 'https://example.org/bistro-nova/menu',
    5: 'https://example.org/bookclub/join',
}


def gray(img):
    rgb = np.array(img.convert('RGBA'))
    a = rgb[..., 3:4] / 255.0
    rgb = rgb[..., :3] * a + 255 * (1 - a)
    return np.round(rgb @ np.array([0.299, 0.587, 0.114])).astype(np.uint8)


def decode_png(path):
    return V.decode_all(gray(Image.open(path)), GATING)


def pdf_pages(path):
    import pymupdf
    with pymupdf.open(path) as doc:
        page = doc[0]
        w_mm, h_mm = page.rect.width / 72 * 25.4, page.rect.height / 72 * 25.4
        z = min(2400 / page.rect.width, 8.0)
        png = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False).tobytes('png')
    return w_mm, h_mm, V.decode_all(gray(Image.open(io.BytesIO(png))), GATING)


def svg_module_mm(path):
    s = open(path, encoding='utf-8').read(4000)
    m = re.search(r'width="([\d.]+)mm"', s)
    vb = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', s)
    if not (m and vb):
        return None
    return float(m.group(1)) / float(vb.group(1))


def grade_run(run_dir, eval_id):
    out = os.path.join(run_dir, 'outputs')
    files = sorted(glob.glob(os.path.join(out, '**', '*'), recursive=True))
    files = [f for f in files if os.path.isfile(f)]
    names = [os.path.relpath(f, out).replace('\\', '/') for f in files]
    res = []

    def add(text, passed, evidence):
        res.append({'text': text, 'passed': bool(passed), 'evidence': evidence})

    add('outputs/response.md exists', os.path.exists(os.path.join(out, 'response.md')), ', '.join(names))
    failed = [n for n in names if '.FAILED.' in n]
    add('no *.FAILED.* file is delivered', not failed, ', '.join(failed) or 'none')

    pngs = [f for f in files if f.lower().endswith('.png')]
    pdfs = [f for f in files if f.lower().endswith('.pdf')]
    svgs = [f for f in files if f.lower().endswith('.svg')]
    decodes = {}
    for f in pngs:
        decodes[os.path.relpath(f, out)] = decode_png(f)
    pdfinfo = {}
    for f in pdfs:
        pdfinfo[os.path.relpath(f, out)] = pdf_pages(f)

    def ok_text(t):
        if t is None:
            return False
        if eval_id == 2:
            return 'S:CafeGuest' in t and 'P:latte2026' in t and t.startswith('WIFI:')
        if eval_id == 3:
            return t.startswith(URLS[3])
        return t == URLS[eval_id]

    all_codes = {k: v for k, v in decodes.items()}
    all_codes.update({k: v[2] for k, v in pdfinfo.items()})
    bad = {k: v for k, v in all_codes.items() if not all(ok_text(v.get(d)) for d in GATING)}
    add('every delivered PNG/PDF decodes to the requested content with zxing and zbar',
        all_codes and not bad, json.dumps({k: {d: (v.get(d) or '')[:60] for d in GATING} for k, v in all_codes.items()},
                                          ensure_ascii=False))

    mods = {os.path.relpath(f, out): svg_module_mm(f) for f in svgs}
    mods = {k: v for k, v in mods.items() if v is not None}
    if eval_id in (1, 3, 5):
        small = {k: round(v, 3) for k, v in mods.items() if v < 0.4}
        add('every delivered print SVG has modules >= 0.4 mm', mods and not small,
            json.dumps({k: round(v, 3) for k, v in mods.items()}))
    if eval_id == 4:
        a6 = {k: (round(v[0], 1), round(v[1], 1)) for k, v in pdfinfo.items()}
        add('a delivered PDF is A6 (105 x 148 mm)',
            any(abs(w - 105) < 1 and abs(h - 148) < 1 for w, h, _ in pdfinfo.values()), json.dumps(a6))
        texts = ''.join(open(f, encoding='utf-8').read() for f in svgs)
        add('the card SVG carries both text lines', '掃描看菜單' in texts and 'BistroGuest' in texts and 'nova2026' in texts,
            f'found: 掃描看菜單={"掃描看菜單" in texts}, BistroGuest={"BistroGuest" in texts}, nova2026={"nova2026" in texts}')
    if eval_id == 2:
        # a tablet layout ships as PNG; its text lives in the card spec (YAML) and any card SVG
        cards = [f for f in glob.glob(os.path.join(run_dir, '**', '*'), recursive=True)
                 if f.lower().endswith(('.svg', '.yaml', '.yml'))]
        texts = ''.join(open(f, encoding='utf-8').read() for f in cards)
        layout_png = [n for n in names if n.lower().endswith('.png') and n not in ('response.md',)]
        add('a layout shows network name and password as text', 'CafeGuest' in texts and 'latte2026' in texts,
            f'svg files searched: {[os.path.relpath(f, run_dir) for f in cards]}; delivered PNGs: {layout_png}')
    if eval_id == 5:
        specs = glob.glob(os.path.join(run_dir, '**', 'resolved_spec.yaml'), recursive=True)
        import yaml
        centers = []
        for s in specs:
            c = (yaml.safe_load(open(s, encoding='utf-8')) or {}).get('center') or {}
            centers.append(str(c.get('image')))
        add('the center image is an SVG file (drawn by the agent)', any(c.lower().endswith('.svg') for c in centers),
            json.dumps(centers, ensure_ascii=False))
    return res


def main():
    root = sys.argv[1]
    for d in sorted(glob.glob(os.path.join(root, 'eval-*', 'with_skill', 'run-*'))):
        eid = int(re.search(r'eval-(\d+)', d).group(1))
        res = grade_run(d, eid)
        with open(os.path.join(d, 'grading_auto.json'), 'w', encoding='utf-8') as f:
            json.dump(res, f, indent=2, ensure_ascii=False)
        passed = sum(r['passed'] for r in res)
        print(f'{os.path.relpath(d, root)}: {passed}/{len(res)}')
        for r in res:
            if not r['passed']:
                print(f"   FAIL {r['text']}: {r['evidence'][:300]}")


if __name__ == '__main__':
    main()
