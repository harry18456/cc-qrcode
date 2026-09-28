---
name: qrcode
description: >-
  Design, build and verify QR codes that are guaranteed to scan - plain or styled (logo or picture
  in the middle, brand colors or gradients, dot/rounded/leaf modules, custom corner shapes or icons,
  small decorations, background images) - for print (flyers, posters, business cards, table cards,
  stickers, signs) or screens, with URL, text, Wi-Fi, vCard, email, SMS, phone or location content.
  Also checks whether an existing QR image scans and answers size questions (how small it can be
  printed, how big the logo can be). Use this whenever the deliverable is a QR code, even a plain
  one. Triggers on casual phrasings too: "做一個 QR code", "QR code 中間放 logo", "幫我做 Wi-Fi 的
  QR", "這個 QR 掃得到嗎", "QR 印 3 公分夠嗎", "make me a nice QR code".
---

# qrcode — you write the spec, the engine decides and verifies

`scripts/qrart.py` is a deterministic engine. It makes every decision that can be checked:
encoding, error-correction level, version, mask, the largest center image, where decorations go,
color contrast, finder shapes, and how much robustness the design loses against a plain code. Your
part is what cannot be checked: turning the request into a spec, supplying the images, judging the
looks, and explaining trade-offs to the user.

The split exists because a QR code that looks right can still fail on phones, for reasons nobody
sees: error correction is spent per Reed-Solomon block in 8-module codewords (a logo on 9% of the
area can already use half of a block's budget), and decoders disagree about rounded finders and
icons. The engine measures all of this; eyeballing does not.

## Preflight

```bash
if command -v uv >/dev/null; then echo "RUN=uv run"; else for p in python3 python; do "$p" -c "import sys; sys.exit(not (3, 12) <= sys.version_info[:2] < (3, 15))" 2>/dev/null && { echo "RUN=$p"; break; }; done; fi
```
The printed value is `<run>`, the runner for every engine command: `uv run` when uv is installed,
otherwise a Python 3.12–3.14. Nothing printed → stop and tell the user: "qrcode needs uv
(recommended: https://docs.astral.sh/uv/getting-started/installation/) or Python 3.12–3.14."

Run every engine command as `<run> "<skill-dir>/scripts/qrart.py" <command> ...` with Bash
`timeout: 600000`. The first run installs the engine's pinned packages (about 230 MB, 1–3 minutes):
uv keeps them in its cache; a plain Python gets a private environment in the user's cache folder.
After that a build takes 10–60 seconds.

## Workflow

1. **Write the spec.** Read `references/spec.md` for every field. Fill in what the user said and
   leave the rest at its default or `auto`: the engine chooses EC level, version, mask, image size
   and module size, so none of those becomes a question. Ask only what you cannot infer and what
   changes the result: the exact content (URL, Wi-Fi name and password), the print width when the
   code will be printed and no size is given, and the images of step 2. Put all questions in one
   message.
2. **Fill the image slots.** A slot is the center image, a finder icon, a decoration, or the
   background. For each slot the design uses:
   - The user attached or named a file → use that file.
   - Otherwise → before the first build, ask once whether they want to send one ("Do you have a
     logo file? If not, I'll draw a simple icon"), in the same message as the step 1 questions.
   - No file comes (they decline, have none, or nobody can answer because you run unattended) →
     draw an SVG yourself; read `references/drawing.md` first. When nobody could answer, the
     delivery says which image you drew and offers to rebuild with their own file.

   The engine checks every image the same way, whoever made it.
3. **Build.**
   ```bash
   <run> "<skill-dir>/scripts/qrart.py" build spec.yaml --out <outdir> [--profile brand.yaml]
   ```
   For a what-if question (bigger logo? smaller print? shorter link? which plate?), run
   `qrart.py explore spec.yaml` first: in seconds and without rendering, it prints the largest
   center image per symbol size and plate, the width each symbol needs, and how much content fits a
   width. Build only the option you will deliver. For the same design at several sizes, list them
   in `print.extra_widths_mm`: one build writes every size. `--quick` skips the robustness sweeps
   and may pick a different symbol, so it serves layout previews only; every delivered file comes
   from a full build.
4. **Read `report.md`** (`report.json` for numbers). Exit code 0 = PASS, 1 = FAIL, 2 = spec error
   (the message names the field), 3 = missing packages.
5. **Look at the PNG** and judge balance, logo legibility and color. Every change (color, shape,
   theme, image, decoration anchor) goes into the spec, followed by a rebuild. The spec is the only
   way in: hand-editing the SVG/PNG, re-rendering with your own code, or pasting a logo onto the
   output afterwards would ship a code no gate has checked.
6. **On FAIL**, the `*.FAILED.*` files are diagnostics, not deliverables. Under "Smallest changes
   that would pass", the report computes relaxations: the largest center image that fits, the print
   width needed, how many bytes the content must lose, or which style element the diagnosis blames.
   Offer the user those options with their numbers. When their intent already settles it ("the
   logo size is flexible"), apply the relaxation in the spec and rebuild. The thresholds stay as
   they are.
7. **Deliver** the files the user needs: SVG for print shops and designers (the vector master),
   PNG for screens and chat, PDF when asked (it embeds the verified raster at print size). A code
   shown on its own (a tablet, a table card, a door sign) also needs words: what it does ("Scan for
   the menu") and, for Wi-Fi, the network name and password for devices that cannot scan. Lay that
   out with `qrart.py card card.yaml` (`references/spec.md#cards`): it places the verified SVG
   unchanged, keeps text out of the quiet zone, and decodes the finished card again. Its `style`
   options (accent color, title rule, text pill, panel) are where your taste goes; take colors from
   the logo or brand. Then summarize:
   - symbol (version, EC level), center-image size, and any automatic repair (for example "dark
     color darkened from X to Y for contrast");
   - warnings from the report, such as the heuristic scan-distance estimate;
   - the phone check that stays with the user: print or display at the real size, then scan with an
     iPhone camera, an Android camera or Google Lens, and the messaging apps the audience uses, at
     the intended distance and angle.

## Reference

- Gates and their evidence: `references/rules.md`. Read it before explaining a result or a
  trade-off to the user. Every threshold lives in `assets/thresholds.yaml`.
- Recurring brand settings (logo, colors, theme, formats) go in a profile passed with `--profile`
  (`references/spec.md#profile-files`). Layer order: engine defaults < theme < profile < spec.
- Phone test results feed the distance estimate: record them as in
  `references/spec.md#calibration` and pass `--calibration <file>`.
- A threshold changes only on the user's explicit request ("allow 60% budget"): copy
  `assets/thresholds.yaml` next to the user's files, edit the copy, pass `--thresholds <copy>`, and
  state in the delivery which threshold changed and what it costs in reliability.

## Other commands

- `qrart.py verify-image IMAGE --expect "exact text"` checks a QR image made elsewhere with the same
  decoders and sweeps.
- `qrart.py themes` lists the built-in themes (classic, soft, dots, fluid, leaf, crisp).
- `qrart.py check-deps` reports missing packages and whether the zbar decoder loads.
- `qrart.py selftest` runs the engine's regression tests (about 2 minutes) after a code or
  threshold change.
