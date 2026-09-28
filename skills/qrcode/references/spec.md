# Spec reference

A spec is a YAML file. Unknown keys are rejected (typos fail loudly). Relative image paths are
resolved against the file that declares them. Anything marked `auto` is decided by the engine and
written back to `resolved_spec.yaml` in the output folder.

## Contents

- [content](#content)
- [symbol](#symbol-ec-version-mask-quiet_zone)
- [theme](#theme)
- [modules](#modules)
- [finder](#finder)
- [alignment](#alignment)
- [colors](#colors)
- [center](#center)
- [decorations](#decorations)
- [background](#background)
- [print](#print)
- [output](#output)
- [profile files](#profile-files)
- [cards](#cards)
- [calibration](#calibration)
- [examples](#examples)

## content

Exactly one of:

| key | value | encoded as |
|---|---|---|
| `url` | `"https://..."` | as is |
| `text` | any string (UTF-8 fine) | as is |
| `wifi` | `{ssid, password, security: WPA|WEP|nopass, hidden: false}` | `WIFI:T:...;S:...;P:...;;` (escaped) |
| `vcard` | `{name: "Last;First", displayname, email, phone, org, url, title, street, city, zipcode, country, ...}` | vCard 3.0 |
| `mecard` | `{name, email, phone, url, ...}` | MECARD (shorter than vCard) |
| `email` | `{to, subject, body}` | `mailto:` |
| `tel` | `"+886..."` | `tel:` |
| `sms` | `{number, body}` | `SMSTO:` |
| `geo` | `{lat, lng}` | `geo:` |

Shorter content means a smaller symbol and bigger modules. Tracking parameters on URLs cost size; the
report tells the byte limit when print size is the problem.

## symbol: ec, version, mask, quiet_zone

| key | default | values |
|---|---|---|
| `ec` | `auto` | `L`, `M`, `Q`, `H`. A fixed value is used exactly (no boosting). |
| `version` | `auto` | 1-40 (must fit the content) |
| `mask` | `auto` | 0-7 |
| `quiet_zone` | `4` | 4-20 modules (the standard requires at least 4) |

`auto`: candidates are tried smallest symbol first, each with the highest EC level that fits the same
size. With an auto-sized center image, candidates that can hold the preferred image size come first.

## theme

`classic`, `soft`, `dots`, `fluid`, `leaf`, `crisp` (`qrart.py themes` prints descriptions). A theme
only sets `modules` and `finder`; everything else comes from the profile/spec.

## modules

| key | default | values |
|---|---|---|
| `shape` | `square` | `square`, `rounded`, `dot`, `diamond`, `squircle`, `leaf`, `connected` |
| `size` | `auto` | 0.5-1.0 of the module cell. `auto` starts at the shape's default and grows toward 1.0 if robustness falls below the gate. |
| `radius` | `0.5` | corner radius for `rounded` (0 = square, 1 = circle) |

`connected` draws dots joined into strokes with their neighbours.

## finder

The three corner squares. Outer ring and 3x3 center are styled separately.

| key | default | values |
|---|---|---|
| `outer` | `square` | `square`, `rounded`, `circle`, `leaf` |
| `inner` | `square` | `square`, `rounded`, `circle`, `leaf`, `icon` |
| `icon` | - | image path, required when `inner: icon` |
| `icon_recolor` | `true` | paint the icon in the finder color (uses its alpha as the shape) |
| `icon_scale` | `1.0` | 0.5-1.0 of the 3x3 box |
| `color` | dark color | separate finder color (also contrast-checked) |

## alignment

| key | default | values |
|---|---|---|
| `style` | `solid` | `solid` draws each alignment pattern as one ring+dot in the finder's outer shape; `modules` draws it with the module shape |

## colors

| key | default | values |
|---|---|---|
| `dark` | `#000000` | `#RRGGBB`, `auto_from_logo` (darkest logo color that passes contrast; darkened if none does), or `{gradient: ["#..", "#.."], angle: 45}` |
| `light` | `#FFFFFF` | `#RRGGBB` |
| `auto_repair` | `true` | darken colors that fail the contrast gate (hue kept); the report lists every change |
| `allow_inverted` | `false` | light modules on a dark background; many scanners cannot read these |

## center

| key | default | values |
|---|---|---|
| `image` | - | PNG, JPG, WEBP or SVG (transparent background recommended) |
| `size` | `auto` | equivalent-square fraction of the symbol width: sqrt(logo width x logo height) / symbol width (0.05-0.4). For a square logo this is its width fraction. `auto` = largest up to 0.22 that keeps the budget; never below 0.14 (then the build fails and the report says what fits). |
| `plate` | `auto` | `rounded`, `square`, `circle`, `hug` (light outline that follows the logo shape), `none` (modules cleared along the logo outline, no plate). `auto` tries, in order: `circle` (round logos only), `rounded`, `hug`. |
| `padding` | `auto` | modules of clear space around the logo's opaque outline (0-3). `auto` tries 1, then 0.5 (then 0.25 with `hug`). |
| `recolor` | - | `#RRGGBB` to paint a single-color logo |

Clearing follows the logo's opaque footprint: transparent margins and corners cost nothing. The
first plate/padding combination that reaches the preferred size wins; if none does, the one that
allows the largest image (a difference of 0.01 does not override the preferred order). When the
version is `auto`, up to 4 larger versions are tried as well, because a bigger symbol has room for a
bigger image; candidates whose modules would fall below the print minimum come last.

## decorations

List of small images placed in the data area.

```yaml
decorations:
  - {image: sparkle.svg, size: 4, anchor: bottom-right}          # engine picks the spot
  - {image: leaf.svg, size: 3, position: [20.5, 12.5], mode: overlay}   # explicit center (row, col)
```

| key | default | values |
|---|---|---|
| `image` | required | image path |
| `size` | `5` | modules (1.5-15) |
| `position` | `auto` | `[row, col]` of the image center in module units (0 = top/left edge of the symbol) |
| `anchor` | `bottom-right` | `top-right`, `bottom-left`, `bottom-right`, `top`, `bottom`, `left`, `right` (for `auto`) |
| `mode` | `clear` | `clear` removes modules under the image; `overlay` draws on top of them (costs more budget) |

## background

| key | default | values |
|---|---|---|
| `image` | - | photo or texture behind the code (covers the whole canvas) |
| `wash` | `auto` | 0-0.95 white veil over the image. `auto` = smallest veil that keeps contrast and resolution robustness. |

## print

| key | default | values |
|---|---|---|
| `width_mm` | - | printed width of the whole code including the quiet zone. Enables the module-size gate (>= 0.4 mm) and a scan-distance estimate. |
| `distance_mm` | - | intended scanning distance; warns (or fails, if `distance_check: gate`) when the estimate is shorter |
| `extra_widths_mm` | `[]` | more print widths for the same design, e.g. `[40, 50]`; each gets its own SVG/PDF and a row in the size table (module size, distance estimate) |

## output

| key | default | values |
|---|---|---|
| `formats` | `[svg, png]` | any of `svg`, `png`, `pdf`. SVG is the vector master. PDF embeds a raster losslessly: with a print width, on a page of that width at 1200 dpi (at most 6000 px per side); without one, the PNG on a 96 dpi page. Every PNG and PDF raster is decoded again before it is written; a miss fails the build |
| `png_module_px` | `16` | pixels per module in the PNG (4-64) |
| `transparent` | `false` | omit the light background in the exported files (checks still assume the light color) |
| `basename` | `qr` | output file name stem |

## Profile files

Same format as a spec, minus `content`. Passed with `--profile`. Layer order:
defaults < theme < profile < spec.

```yaml
# brand.yaml
theme: soft
colors: {dark: "#1B3A6B"}
center: {image: brand/logo.svg, plate: circle}
output: {formats: [svg, png, pdf]}
```

## Cards

`qrart.py card card.yaml [--out DIR]` places a passed build on a canvas with text. The code is the
build's own SVG, only scaled; the card is decoded again and checked for module size (print) or pixels
per module (screen, at least 4).

```yaml
qr: build_out                  # folder of a PASS build (needs svg in output.formats)
canvas: {width_px: 1640, height_px: 2360}          # screen / tablet
# canvas: {width_mm: 105, height_mm: 148, dpi: 300} # print (A6)
background: "#FFF8F0"
text_color: "#4A3426"
title: "Guest Wi-Fi"
subtitle: "Scan to connect"
lines: ["Network: CafeGuest", "Password: latte2026"]
qr_size: 0.62                  # code width as a fraction of the canvas width (shrinks to fit text)
margin: 0.07
style:                         # optional finishing, all off by default
  accent: "#D65A31"            # accent color for the rule and pill (default: text_color)
  title_rule: true             # short rounded rule under the title
  lines_pill: true             # soft rounded background behind the text lines
  panel: true                  # rounded light panel with a hairline border behind the code
font: "Microsoft JhengHei, PingFang TC, Noto Sans CJK TC, sans-serif"   # default: a longer list of the same kind
output: {formats: [png, pdf, svg], basename: card}
```

Text is centered: title and subtitle above the code, lines below. Long lines shrink to fit. `font` is
a CSS family list and the first installed family wins; the default tries Traditional Chinese first
(Microsoft JhengHei, PingFang TC, Noto Sans CJK TC), then Simplified Chinese and Japanese fonts. For
Simplified Chinese or Japanese text, put that language's font first. The card report warns when no
CJK font is installed. The card PDF carries the same PNG the card check decoded.

## Calibration

Scan-distance estimates use `distance_mm_per_module_mm` (330) until real tests exist. Keep a file
like this with the user's files and pass it with `--calibration`:

```yaml
observations:
  - {module_mm: 0.67, max_distance_mm: 260, style_factor: 1.0, device: "phone + app", date: 2026-10-01}
```

`module_mm` and `style_factor` come from the report's physical-size section; `max_distance_mm` is the
farthest distance that scanned 3 out of 3 times. With 3 or more entries the median ratio replaces
the default.

## Examples

URL with logo for a printed flyer:

```yaml
content: {url: "https://example.org/events/2026"}
theme: soft
colors: {dark: "#1B3A6B"}
center: {image: logo.png}
print: {width_mm: 30, distance_mm: 300}
output: {formats: [svg, png, pdf]}
```

Wi-Fi on a screen, dots in a brand color:

```yaml
content: {wifi: {ssid: CafeGuest, password: latte2026, security: WPA}}
theme: dots
colors: {dark: "#6F4E37"}
output: {png_module_px: 24}
```

Gradient, connected modules, a small decoration:

```yaml
content: {url: "https://example.org/shop?campaign=spring"}
theme: fluid
colors: {dark: {gradient: ["#0B3D91", "#6A1B9A"], angle: 45}}
decorations:
  - {image: sparkle.svg, size: 4, anchor: bottom-right}
```

Custom finder icon:

```yaml
content: {url: "https://example.org/x"}
theme: soft
finder: {inner: icon, icon: drop.svg}
```
