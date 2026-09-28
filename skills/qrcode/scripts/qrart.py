#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = [
#     "segno>=1.6",
#     "numpy>=1.24",
#     "pillow>=10.0",
#     "opencv-python-headless>=4.8",
#     "zxing-cpp>=2.2",
#     "pyzbar>=0.1.9",
#     "pyyaml>=6.0",
#     "resvg-py>=0.5",
#     "pymupdf>=1.24",
# ]
# [tool.uv]
# exclude-newer = "2026-09-28T00:00:00Z"
# ///
"""qrcode engine CLI. Two ways to run it, same pinned packages either way:

- `uv run qrart.py ...` (preferred): uv reads the dependency block above and qrart.py.lock and keeps
  the packages in its own cache.
- `python qrart.py ...` with Python 3.12-3.14: bootstrap.py builds a private environment in the
  user's cache folder on the first run (pip, hash-checked wheels from requirements.lock.txt) and
  runs every command there. The system Python stays untouched.

qrart.py.lock pins every version (re-lock with `uv lock --script qrart.py`, then export
requirements.lock.txt as bootstrap.py describes); exclude-newer keeps a re-lock from picking up
releases after that date. The Python range stops before a release whose wheels the pinned packages
do not ship yet, so no install ever falls back to building numpy or Pillow from source.

  qrart.py build SPEC.yaml [--profile PROFILE.yaml] [--out DIR] [--quick] [--thresholds FILE]
  qrart.py explore SPEC.yaml [--profile PROFILE.yaml]      (static what-if table, no rendering)
  qrart.py card CARD.yaml [--out DIR]                        (layout: verified code + text on a canvas)
  qrart.py verify-image IMAGE --expect TEXT [--out DIR]
  qrart.py check-deps
  qrart.py themes
  qrart.py selftest                                          (engine regression tests, about 2 minutes)

Exit codes: 0 PASS, 1 FAIL (report explains why and what to relax), 2 spec/usage error, 3 missing deps.
"""
import argparse
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PIP = {'segno': 'segno', 'numpy': 'numpy', 'PIL': 'pillow', 'cv2': 'opencv-python-headless',
       'zxingcpp': 'zxing-cpp', 'yaml': 'pyyaml', 'pyzbar': 'pyzbar', 'resvg_py': 'resvg-py',
       'pymupdf': 'pymupdf'}
RUN_HINT = ('run the engine with `uv run qrart.py ...`, or with Python 3.12-3.14 so it sets up its private '
            'environment')
ZBAR_HINT = {
    'win32': "pyzbar's Windows wheel bundles zbar; if it still fails to load, install the "
             'Microsoft Visual C++ 2013 Redistributable (x64)',
    'darwin': 'brew install zbar; on Apple Silicon Homebrew uses /opt/homebrew/lib, which the loader does '
              'not search: export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib',
}


def check_deps():
    missing = []
    for mod, pkg in PIP.items():
        try:
            importlib.import_module(mod)
        except Exception:
            missing.append(pkg)
    zbar_ok = True
    try:
        from pyzbar import pyzbar  # noqa: F401
    except Exception:
        zbar_ok = False
    return missing, zbar_ok


def cmd_check(args):
    missing, zbar_ok = check_deps()
    if missing:
        print('missing python packages: ' + ' '.join(missing))
        print(RUN_HINT)
    if not zbar_ok and 'pyzbar' not in missing:  # installed, but its zbar library does not load
        print('zbar decoder unavailable (' + ZBAR_HINT.get(sys.platform, 'apt-get install -y libzbar0') + '); '
              'builds still run with the remaining gating decoders and the report says so')
    if not missing:
        print('ok: all required packages present' + ('' if zbar_ok else ' (without zbar)') +
              f'; Python {sys.version.split()[0]} at {sys.prefix}')
    return 3 if missing else 0


def cmd_selftest(args):
    missing, _ = check_deps()
    if missing:
        print('missing packages: ' + ' '.join(missing) + ' -> ' + RUN_HINT, file=sys.stderr)
        return 3
    import selftest
    return selftest.main()


def cmd_themes(args):
    from qrlib.config import ASSETS, list_themes, load_yaml
    for t in list_themes():
        data = load_yaml(os.path.join(ASSETS, 'themes', t + '.yaml'))
        print(f"{t}: {data.pop('description', '')}")
    return 0


def cmd_build(args):
    missing, _ = check_deps()
    if missing:
        print('missing packages: ' + ' '.join(missing) + ' -> ' + RUN_HINT, file=sys.stderr)
        return 3
    from qrlib.config import SpecError, load_spec, load_thresholds
    from qrlib.report import write_outputs
    from qrlib.resolve import build
    import segno
    try:
        spec, layers = load_spec(args.spec, args.profile)
        th = load_thresholds(args.thresholds)
        th['_calibration_path'] = args.calibration
        res = build(spec, th, quick=args.quick)
    except (SpecError, segno.DataOverflowError, ValueError) as e:
        print(f'SPEC ERROR: {e}', file=sys.stderr)
        return 2
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.spec)), 'qr_out')
    rep = write_outputs(res, spec, th, layers, out, args.spec)
    s = rep['symbol']
    print(f"{rep['status']}: version {s['version']}-{s['ec']} mask {s['mask']}, "
          f"worst block {rep['budget']['max_usage']:.0%} of budget, "
          f"final PNG decoders {rep['decoders']['final_png']}")
    for f in rep['failures']:
        print(f'  FAIL: {f}')
    for k, v in rep['relaxations'].items():
        print(f"  RELAX {k}: {v.get('value')} - {v['note']}")
    print(f"report: {rep['files']['report_md']}")
    for k in ('svg', 'png', 'pdf', 'preview_png'):
        if k in rep['files']:
            print(f'{k}: {rep["files"][k]}')
    return 0 if rep['status'] == 'PASS' else 1


def cmd_card(args):
    from qrlib.card import build_card, load_card
    from qrlib.config import SpecError, load_thresholds
    try:
        spec = load_card(args.card)
        th = load_thresholds(args.thresholds)
        out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.card)), 'card_out')
        rep = build_card(spec, th, out)
    except SpecError as e:
        print(f'SPEC ERROR: {e}', file=sys.stderr)
        return 2
    print(f"{rep['status']}: code {rep['code_size_px']} px on the canvas")
    for c in rep['checks']:
        print(f"  {'OK  ' if c['pass'] else 'FAIL'} {c['name']}: {c['detail']}")
    for w in rep['warnings']:
        print(f'  warning: {w}')
    for k, v in rep['files'].items():
        print(f'{k}: {v}')
    return 0 if rep['status'] == 'PASS' else 1


def cmd_explore(args):
    """Static what-if analysis without rendering: which symbol sizes, center-image sizes per plate
    style, print widths and content lengths are feasible. Seconds, not a full build."""
    from qrlib.config import SpecError, load_spec, load_thresholds
    from qrlib.resolve import prepare_assets, tradeoffs
    from qrlib.payload import make_payload
    try:
        spec, _ = load_spec(args.spec, args.profile)
        th = load_thresholds(args.thresholds)
        th['_calibration_path'] = args.calibration
        payload = make_payload(spec['content'])
        assets = prepare_assets(spec)
        t = tradeoffs(payload, spec, th, assets)
    except (SpecError, ValueError) as e:  # like build: unreadable images and oversized content are spec errors
        print(f'SPEC ERROR: {e}', file=sys.stderr)
        return 2
    width = spec['print']['width_mm']
    print(f"content {t['payload_bytes']} bytes; min module {th['min_module_mm']} mm; "
          f"distance factor {t['distance_k']:.0f} mm/mm")
    combos = sorted({k for row in t['rows'] for k in row['center']})
    head = ['symbol', 'canvas', 'module@width', 'width for min module', 'width for distance'] + \
           [f'max image {c}' for c in combos]
    print('| ' + ' | '.join(head) + ' |')
    print('|' + '---|' * len(head))
    for row in t['rows']:
        cells = [row['symbol'], str(row['canvas_modules']),
                 f"{row['module_mm']:.2f} mm" if row['module_mm'] else '-',
                 f"{row['width_for_min_module_mm']} mm",
                 f"{row['width_for_distance_mm']} mm" if row['width_for_distance_mm'] else '-']
        cells += [f"{row['center'][c]:.0%}" if row['center'].get(c) is not None else '-' for c in combos]
        print('| ' + ' | '.join(cells) + ' |')
    if t['content_limits']:
        print(f"content that fits {width} mm (bytes): " +
              ', '.join(f"{ec} {v['max_bytes']} (v{v['max_version']})" for ec, v in t['content_limits'].items()))
    return 0


def cmd_verify_image(args):
    """Check an existing QR image (made elsewhere) against the same decoders and sweeps."""
    import cv2
    import numpy as np
    import zxingcpp
    from qrlib import verify as V
    from qrlib.config import load_thresholds
    from qrlib.analyze import rasterize, luma
    from qrlib.render import baseline_svg
    from qrlib.structure import Structure
    th = load_thresholds(args.thresholds)
    img = cv2.imread(args.image, cv2.IMREAD_GRAYSCALE)
    if img is None:
        print(f'cannot read image {args.image}', file=sys.stderr)
        return 2
    res = zxingcpp.read_barcodes(img)
    clean = V.decode_all(img)
    out = {'image': args.image, 'expected': args.expect,
           'clean': {k: v == args.expect for k, v in clean.items()}}
    if not res:
        out['status'] = 'FAIL'
        out['note'] = 'zxing cannot locate the symbol'
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 1
    r = res[0]
    ec = (r.ec_level or 'M')[0] if r.ec_level else 'M'
    st = Structure(args.expect, ec if ec in 'LMQH' else 'M')
    p = r.position
    width_px = np.hypot(p.top_right.x - p.top_left.x, p.top_right.y - p.top_left.y)
    px = width_px / st.n
    scale = 12 / px
    g = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    # baseline in the image's own dark/light colors (color is judged separately), like build does
    color = cv2.imread(args.image, cv2.IMREAD_COLOR)
    xs = [int(q.x) for q in (p.top_left, p.top_right, p.bottom_left, p.bottom_right)]
    ys = [int(q.y) for q in (p.top_left, p.top_right, p.bottom_left, p.bottom_right)]
    crop = color[max(min(ys), 0):max(ys), max(min(xs), 0):max(xs)].reshape(-1, 3)[:, ::-1]
    lum = crop @ np.array([0.299, 0.587, 0.114])
    thr = (np.percentile(lum, 5) + np.percentile(lum, 95)) / 2
    to_hex = lambda a: '#%02X%02X%02X' % tuple(int(v) for v in np.median(a, axis=0))
    cols = {'dark': to_hex(crop[lum < thr]), 'light': to_hex(crop[lum >= thr])}
    base = np.round(luma(rasterize(baseline_svg(st, 4, colors=cols), st.n + 8, 12))).astype(np.uint8)
    gating = V.available(th['gating_decoders'])
    rows, _ = V.robustness(g, base, 12, args.expect, gating, [])
    out.update({'estimated_version': st.version, 'ec_from_decoder': r.ec_level, 'px_per_module_input': round(px, 2),
                'baseline_colors': cols,
                'robustness': rows})
    weak = [x for x in rows if x['ratio'] is not None and
            x['ratio'] < th['boundary_ratio'].get(x['axis'], th['boundary_ratio']['default'])]
    ok = all(out['clean'].get(d) for d in gating) and not weak
    out['status'] = 'PASS' if ok else 'FAIL'
    out['note'] = ('baseline assumes the smallest version for this content and EC; if the image uses a larger '
                   'version the ratios are approximate')
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, 'verify_report.json'), 'w', encoding='utf-8') as f:
            f.write(text)
    print(text)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('spec')
    b.add_argument('--profile')
    b.add_argument('--out')
    b.add_argument('--quick', action='store_true', help='skip robustness sweeps (drafts only)')
    b.add_argument('--thresholds')
    b.add_argument('--calibration', help='real-phone scan tests (YAML); default: assets/calibration.yaml')
    v = sub.add_parser('verify-image')
    v.add_argument('image')
    v.add_argument('--expect', required=True, help='exact text the code must decode to')
    v.add_argument('--out')
    v.add_argument('--thresholds')
    e = sub.add_parser('explore')
    e.add_argument('spec')
    e.add_argument('--profile')
    e.add_argument('--thresholds')
    e.add_argument('--calibration')
    k = sub.add_parser('card')
    k.add_argument('card')
    k.add_argument('--out')
    k.add_argument('--thresholds')
    sub.add_parser('check-deps')
    sub.add_parser('themes')
    sub.add_parser('selftest')
    args = ap.parse_args()
    import bootstrap
    bootstrap.ensure()  # without uv: runs this command again in the private environment and exits
    return {'build': cmd_build, 'verify-image': cmd_verify_image, 'check-deps': cmd_check, 'explore': cmd_explore,
            'card': cmd_card, 'themes': cmd_themes, 'selftest': cmd_selftest}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
