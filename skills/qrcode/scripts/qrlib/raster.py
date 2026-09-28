"""SVG -> PNG / PDF. Every raster the engine measures and every file it delivers comes from here.

resvg draws all pixels. It ships as a wheel for Windows, macOS and Linux, so the engine needs no
system graphics library, and it renders everything the engine writes: gradients, embedded PNG/SVG
images, nested SVG documents, and CJK text with system fonts.

A PDF holds the same resvg raster on a page of the physical size. A vector conversion was tried
and dropped: PyMuPDF's SVG interpreter painted gradients black and skipped SVG images, including
the whole code on a card, so it would have shipped a PDF that no gate had checked. The SVG stays
the vector deliverable."""
import codecs
import gzip
import io
import re

from PIL import Image

MM_PER_INCH = 25.4
PT_PER_INCH = 72.0

_ROOT = re.compile(r'<svg\b[^>]*>')
_SIZE_ATTRS = re.compile(r'\s(?:width|height|preserveAspectRatio)\s*=\s*("[^"]*"|\'[^\']*\')')
_XML_ENCODING = re.compile(rb'^<\?xml[^>]*encoding\s*=\s*["\']([A-Za-z0-9._-]+)["\']')


def svg_text(data):
    """SVG source as text. Bytes may be gzip-compressed (.svgz) and in any encoding named by a BOM or
    the XML declaration; anything else is read as UTF-8. Unreadable input raises ValueError."""
    if isinstance(data, str):
        return data
    try:
        if data[:2] == b'\x1f\x8b':
            data = gzip.decompress(data)
        for bom, enc in ((codecs.BOM_UTF8, 'utf-8-sig'), (codecs.BOM_UTF16_LE, 'utf-16'),
                         (codecs.BOM_UTF16_BE, 'utf-16')):
            if data.startswith(bom):
                return data.decode(enc)
        m = _XML_ENCODING.match(data.lstrip())
        return data.decode(m.group(1).decode('ascii') if m else 'utf-8')
    except (OSError, LookupError, UnicodeDecodeError) as e:
        raise ValueError(f'cannot read the SVG ({e})') from e


def _fit_root(svg, width, height):
    """Give the root element exactly width x height pixels. resvg rounds a physical size (30mm =
    113.386 px) to whole pixels before scaling, which shifts and crops the content by a fraction of
    a module; with the root in target pixels the raster matches the verified one pixel for pixel."""
    m = _ROOT.search(svg)
    if not m:
        raise ValueError('not an SVG document (no <svg> element)')
    tag = _SIZE_ATTRS.sub('', m.group(0))
    end = -2 if tag.endswith('/>') else -1
    tag = f'{tag[:end]} width="{width}" height="{height}" preserveAspectRatio="none"{tag[end:]}'
    return svg[:m.start()] + tag + svg[m.end():]


def svg_to_png(svg, width, height=None):
    """PNG bytes of the SVG. With a height, the result is exactly width x height pixels; without one,
    the height follows the SVG's own aspect ratio (used for image assets)."""
    import resvg_py
    text = svg_text(svg)
    if height is not None:
        text = _fit_root(text, int(width), int(height))
    # dpi must be set: resvg-py's default of 0 turns physical units (width="30mm") into a zero size
    png = bytes(resvg_py.svg_to_bytes(svg_string=text, width=int(width),
                                      height=int(height) if height else None, dpi=96))
    if height is None:
        return png
    img = Image.open(io.BytesIO(png))
    if img.size == (int(width), int(height)):
        return png
    b = io.BytesIO()  # not expected after _fit_root; stretch rather than leave a transparent band
    img.convert('RGBA').resize((int(width), int(height)), Image.LANCZOS).save(b, 'PNG')
    return b.getvalue()


def png_to_pdf(png, width_mm, height_mm):
    """One-page PDF of the given physical size showing the PNG edge to edge, losslessly. An opaque
    PNG goes in as RGB so print preflight sees no transparency."""
    import pymupdf
    img = Image.open(io.BytesIO(png))
    if img.mode in ('RGBA', 'LA') and img.getchannel('A').getextrema()[0] == 255:
        b = io.BytesIO()
        img.convert('RGB').save(b, 'PNG')
        png = b.getvalue()
    with pymupdf.open() as doc:
        page = doc.new_page(width=width_mm / MM_PER_INCH * PT_PER_INCH, height=height_mm / MM_PER_INCH * PT_PER_INCH)
        page.insert_image(page.rect, stream=png, keep_proportion=False)
        # no dates and no random file ID: the same spec gives a byte-identical PDF
        doc.set_metadata({})
        return doc.tobytes(deflate=True, garbage=3, no_new_id=True)


def pdf_first_page_png(pdf, width_px):
    """Raster of a PDF's first page at width_px (used by the self-test to decode delivered PDFs)."""
    import pymupdf
    with pymupdf.open(stream=pdf, filetype='pdf') as doc:
        page = doc[0]
        z = width_px / page.rect.width
        return page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False).tobytes('png')
