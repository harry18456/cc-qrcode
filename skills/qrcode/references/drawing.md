# Drawing image assets

Read this before drawing any image for a spec. A drawn asset is a single-color SVG silhouette:
`viewBox="0 0 100 100"`, one or a few filled paths, no strokes, no text, no gradients, no parts
thinner than about a tenth of the box. Save it next to the spec and reference it by relative path.
The engine checks it exactly like a user's file.

## Center image (`center.image`)

Draw a compact symbol, not a wordmark. Size is an equivalent square (sqrt of width x height), so a
long wordmark becomes a thin strip at the same budget; the report warns when the short side is under
2.5 modules or 3 mm. A transparent background looks best: modules are cleared along the drawing's
actual outline, so a round symbol costs less than a square one.

## Finder icon (`finder.inner: icon`)

It replaces the 3x3 center of the three corner squares. The shape must be solid and fill most of its
box along the horizontal and vertical center lines (about 85% or more): circle, rounded square,
drop, leaf. Stars, letters and thin bars fail the finder-geometry gate. Hearts pass that gate but can
fail robustness for zbar; the report says so, so check it before promising one.

## Decoration (`decorations`)

Decorations sit in the data area; the default `mode: clear` removes the modules under them. Keep
them small (3-5 modules). Each one costs error budget, and the engine places it where it costs least
near the anchor.

## Background (`background.image`)

A drawn background is a simple pattern or a few large, soft shapes in light tints of the brand
colors, covering the whole square. Keep every detail larger than a module so it does not read as
modules. The engine lays a light wash over it until contrast and resolution hold
(`background.wash: auto`).
