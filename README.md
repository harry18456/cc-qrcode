English | [繁體中文](README.zh-TW.md)

# qrcode

Lets Claude Code design QR codes that are **guaranteed to scan**: a logo in the middle, brand colors or gradients, dot / rounded / leaf modules, custom corner shapes, small decorations, a card with text around the code. A **deterministic engine** makes every decision that can be checked; Claude only writes the spec, supplies the images and judges the looks.

Once installed, natural-language requests trigger it — no commands to memorize:

- "Make a QR code for our event page with the logo in the middle, printed 3 cm wide"
- "Wi-Fi QR code in round dots, coffee brown, for the tablet at the counter"
- "Will this QR code still scan?" (an image made elsewhere)
- "How big can the logo be if I print it on a business card?"

Highlights:

- **Error budget counted like a decoder does**: per Reed-Solomon block in 8-module codewords, not by area — a logo on 9% of the area can already use half of a block's budget. The largest safe logo is computed before rendering.
- **Engine-made decisions**: EC level, version, mask, logo size, plate shape, decoration placement, contrast repair — the same spec gives the same result every time.
- **Gates on every build**: ≤ 50% of each block's budget, intact structure, finder ratio 1:1:3:1:1, contrast ≥ 0.60, zxing and zbar both decode, robustness ≥ 70% of a plain code under blur / downscale / tilt / curvature / JPEG / fading, decodes at every 4–48 px per module, printed module ≥ 0.4 mm.
- **Failures come with numbers**: the report computes the smallest change that would pass (largest logo that fits, print width needed, bytes to cut) and which style element broke it.
- **`explore`**: what-if tables in seconds, without rendering. **`card`**: table cards and tablet screens with title, text and Wi-Fi details — the finished card is decoded again.
- **Images**: your own files, or simple SVG icons that Claude draws when you have none; Claude asks first. Every image passes the same gates.

Cross-platform (Windows / macOS / Linux): all rendering goes through `resvg`, installed as a wheel — no cairo or other system graphics library. Runs with uv or with a plain Python; both get the same pinned packages and render byte-identical files.

## Requirements

- One of:
  - [uv](https://docs.astral.sh/uv/getting-started/installation/) (recommended; it fetches Python and the engine's packages by itself)
  - Python 3.12–3.14 with `venv` (on Debian/Ubuntu: `python3-venv`)
- Claude Code

## Installation

```
/plugin marketplace add harry18456/cc
/plugin install qrcode@harry18456
```

Restart Claude Code to take effect.

### Use locally right away (no push, no marketplace)

Link the skill directory into the skills directory and the next session loads it:

```powershell
# Windows
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\qrcode" -Target "<path to this repo>\skills\qrcode"
```

```bash
# macOS / Linux
ln -s "<path to this repo>/skills/qrcode" ~/.claude/skills/qrcode
```

## First-use notes

- The first run installs the engine's packages (segno, numpy, opencv, zxing-cpp, pyzbar, resvg, PyMuPDF — about 230 MB unpacked, 1–3 minutes). Your system Python is never touched:
  - with uv, they live in **uv's own cache**;
  - without uv, the engine builds a **private environment** with pip from hash-checked wheels, in `%USERPROFILE%\.cache\qrcode-skill\` (Windows), `~/Library/Caches/qrcode-skill/` (macOS) or `~/.cache/qrcode-skill/` (Linux). Delete that folder to reclaim the space; the next run rebuilds it.
- Every version is pinned in `qrart.py.lock` (exported to `requirements.lock.txt` for the no-uv path), so new package releases cannot change how a spec renders.
- A build takes 10–60 seconds; the self-test takes about 2 minutes.
- zbar ships inside pyzbar's Windows wheel. If it still fails to load, install the Microsoft Visual C++ 2013 Redistributable (x64); macOS: `brew install zbar` (on Apple Silicon also `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`); Linux: `apt-get install libzbar0`. Without zbar the build runs with zxing alone and the report says so.
- Cards with Chinese / Japanese text need a CJK font (Windows and macOS have one built in; Linux: Noto Sans CJK).

## How it works

`skills/qrcode/scripts/qrart.py` is the engine; Claude drives it through a YAML spec:

1. Claude turns the request into a spec — content, colors, theme, images, print width — and leaves everything the engine decides at `auto`.
2. The engine encodes the content, maps every module to its codeword and Reed-Solomon block, and searches symbol size, EC level, mask, logo size and decoration spots for the design that keeps the most margin.
3. It renders one SVG — the single source for the SVG, PNG and PDF files and for every verification raster — then runs the gates above and writes `report.md` / `report.json`.
4. On FAIL it reports the smallest relaxations; Claude presents them to you instead of loosening thresholds. Every threshold lives in `assets/thresholds.yaml`.

The PDF embeds a raster at print size (1200 dpi), rendered from the same SVG and decoded again before it is written, like the PNG. The SVG is the vector master for print shops and designers.

## Limits

- Phone scanners (iOS camera, Google Lens, messaging apps) are proprietary: the final check — print it and scan it at the real distance — stays with you. The report includes a checklist.
- The scan-distance estimate is a heuristic until you record real phone tests in a calibration file (`--calibration`).
- The 50% / 70% / 0.4 mm thresholds are the engine's own conservative starting values from decoder tests, not an external standard.
- AI-generated "art" QR codes (ControlNet style) are out of scope: they cannot guarantee structure.

## Local development / testing

No need to push to GitHub first — load this folder directly:

```
claude --plugin-dir <path to this repo>
```

Engine regression tests (run after changing code or thresholds):

```
uv run skills/qrcode/scripts/qrart.py selftest      # or: python skills/qrcode/scripts/qrart.py selftest
```

After changing dependencies, lock them again and export the pins for the no-uv path (the self-test checks that the two agree):

```
cd skills/qrcode/scripts
uv lock --script qrart.py
uv export --script qrart.py --format requirements-txt --no-header --quiet -o requirements.lock.txt
```

## License

MIT
