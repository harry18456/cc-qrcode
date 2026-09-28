# Why each rule exists

Use this file to explain results to the user. Numbers come from tests with zxing-cpp, zbar and
OpenCV on rendered images; they are starting values to be recalibrated with real phones.

## Error correction is counted in codewords per block, not in area

QR data is split into 8-module codewords and spread over several Reed-Solomon blocks. Each block can
repair about half of its error-correction codewords; if any block gets more errors than that, the whole
code fails. A logo edge cuts through many codewords, and almost every codeword it touches becomes an
error. Level H's "30%" therefore never means 30% of the area.

Measured on clean images with a centered square cleared area:

| symbol | cleared area | worst block uses | result |
|---|---|---|---|
| 5-H | 8.8% | 55% | reads |
| 5-H | 16.4% | 91% | reads |
| 5-H | 21.1% | 109% | fails on all decoders |
| 10-H | 22.4% | 100% | reads |
| 10-H | 25.9% | 107% | fails on all decoders |

The engine's static prediction matched every decoder at the boundary, which is why sizing happens
before rendering. Clean images are the best case; real photos add blur, glare and perspective errors,
so the gate reserves half of every block (`max_block_usage: 0.5`).

Symbol size matters too: higher EC or longer content means a bigger version and smaller modules at
the same print width. For a 35-character URL: M -> version 3 (29x29), Q -> 4 (33x33), H -> 5 (37x37);
at 3 cm printed width that is 0.81 / 0.73 / 0.67 mm per module. L and M give the same version for that
length, which is why EC is raised as far as a version allows.

Where the image sits matters as much as its size. Clearing follows the logo's outline, so a round
logo with a 1-module ring costs noticeably less than the same logo on a square plate. Larger versions
can hold proportionally larger images: for a 24-byte URL, version 3-H allows about 11% while 6-H
allows 20%. With a 176-byte URL the best symbol for a big logo was 11-Q (30%), not 13-H (21%), because
block structure differs per version and level. These effects are not guessable; `explore` computes
them.

## Decoders disagree

| change | zxing | zbar | OpenCV |
|---|---|---|---|
| rounded finder corners | reads | reads | fails |
| timing lines erased | reads | reads | fails |
| drop-shaped finder center touching the ring | fails | reads | fails |
| rays across the finder's light ring (diagonal) | fails clean, reads when blurred | reads | mixed |
| rays across the light ring (horizontal/vertical) | fails | reads | fails |

zxing checks the 1:1:3:1:1 ratio through the finder center horizontally, vertically and diagonally;
zbar accepts off-center scan lines; OpenCV needs square finder corners and reads the timing lines.
Passing one decoder is therefore not evidence for the others. Gating decoders are zxing and zbar;
OpenCV is informational because it rejects rounded finders that phone scanners read. Phone scanners
(iOS, Android/Google Lens, messaging apps) are proprietary, so the final check is always a real phone.

Sizes matter too: zbar decoded a design with circular finder rings at 4-24 pixels per module but
not at 28-40, the size such a code has full-screen on a tablet. Rounded-square rings decode at every
size. The build therefore decodes the clean code at 4-48 px/module, and the `dots` theme uses rounded
rings with round centers.

## Pass/fail hides the margin

Round dots read fine at normal resolution; the cost only shows when degradation is swept until
failure (zxing):

| modules | blur it survives (sigma, modules) | min pixels per module |
|---|---|---|
| square | 0.60 | 1.2 |
| dot 0.8 | 0.60 | 1.8 |
| dot 0.6 | 0.50 | 2.2 |
| dot 0.45 | 0.35 | 3.0 |

Dots of 0.45 need 2.5x the pixels, i.e. about 40% of the scanning distance. That is why the engine
compares every axis against a plain rendering of the same matrix and colors and requires 70% (60% for
JPEG and faded contrast), and why `modules.size: auto` grows the modules when the ratio falls short.

## Alignment patterns

Covering only the central alignment pattern (versions 7+) made no measurable difference under barrel
or cylinder distortion. Covering the four edge alignment patterns cut zxing's barrel tolerance from
0.16 to 0.06 and zbar's from 0.16 to 0.04. Decorations are therefore kept off all alignment patterns;
only a center image may cover the central one.

## Rendering seams

Shapes filled as separate SVG paths leave anti-aliasing seams where they touch; scaled onto a card,
a solid alignment pattern next to square modules showed a hairline. The renderer puts modules and
alignment patterns into one path (holes drawn with reversed winding), so touching shapes merge.

## Finder geometry

Decoders find the code by the 1:1:3:1:1 dark/light pattern through each finder center. Stars, letters
and thin shapes shrink the center run or widen the light ring and fail; solid shapes that fill the
3x3 center (circle, rounded square, leaf, drop) pass. Horizontal and vertical scans allow 0.4
relative deviation, diagonals 0.6 (decoders treat the diagonal as a looser secondary check).

## Contrast

Decoders convert color to gray differently (BT.601, BT.709, plain mean, zxing's (R+2G+B)/4). The gate
takes the worst of these. 0.60 sits between ISO/IEC 15415 symbol-contrast grades B (0.55) and A
(0.70), leaving room for print. Examples on white: black 1.00, navy #1B3A6B 0.75, coffee #6F4E37
0.67, red #D32F2F about 0.60, orange #F28C28 0.38 (fails; auto-repair darkens it).

## Physical size

Module size = printed width / (modules + 8). Below 0.4 mm printing and focusing get unreliable. The
distance estimate (330 x module mm x style factor) reproduces the common "about 10x the code width"
rule for small codes and is stricter for dense ones; it is uncalibrated, so it warns by default.

## Who decides what

| Decision | Who | How |
|---|---|---|
| Content string (URL, Wi-Fi, vCard...) | user | spec `content` |
| Shortening a long URL | user (needs their link service) | report gives the byte limit that would fit |
| EC level, version, mask | engine | smallest symbol first, EC raised as far as that symbol allows; mask that costs the least budget |
| Image yes/no, which image | user; you draw an SVG when they have none | spec image fields |
| Center image size, plate, padding | engine (unless the user fixes them) | largest size up to the preferred 22% that keeps every block at or below 50% budget; plates tried in a fixed order (circle for round logos, rounded, then `hug`), padding 1 then 0.5; larger versions tried when the logo needs room |
| Colors | user, theme, profile, or `auto_from_logo` | contrast gate; darkens automatically when allowed |
| Module and finder shapes | user or theme | shape library; size auto-enlarged if robustness drops |
| Decoration placement | engine near the requested anchor | positions that avoid structure and keep budget |
| Background wash strength | engine | smallest wash that keeps contrast and resolution robustness |
| What to do after FAIL | user, informed by computed relaxations | never by loosening thresholds |
| Looks, layout around the code | you and the user | spec and card changes only |
| Real-world scanning | user | checklist in the report; results go into a calibration file |
