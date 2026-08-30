"""Shared invoice-image renderer for demo fixtures. Not imported by the app --
demo-only tooling. Renders a clean, computer-generated invoice as a PNG,
readable by TesseractExtractor's real OCR + label/table-parsing pipeline.

Design notes learned empirically against packages/extractors/extractors/tesseract.py:
- A visible border rectangle around the page is required: TesseractExtractor's
  _deskew() estimates rotation from cv2.minAreaRect() over all foreground pixels,
  and a sparse page of scattered text glyphs (no page-edge silhouette) produces a
  bogus ~10 degree "correction" that garbles everything. A full-page border gives
  minAreaRect a real rectangle to find, so the computed correction is ~0.
- Full-width horizontal rule lines break Tesseract's own OCR entirely for
  short/sparse tokens in the band between them (observed: "Qty", "Price", "10",
  "25.00" silently vanish from image_to_string when a rule line spans the full
  content width above the table row) -- so this renderer never draws one across
  a column with real data in it.
- Column x-positions must be moderate: gaps that are too wide cause Tesseract's
  own line-grouping to split one table row into multiple unrelated "lines"
  before this codebase's own column-clustering logic ever sees it.
"""

from PIL import Image, ImageDraw, ImageFont

_FONT_REGULAR = "/System/Library/Fonts/Supplemental/Arial.ttf"
_FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(_FONT_BOLD if bold else _FONT_REGULAR, size)
    except OSError:
        return ImageFont.load_default()


def render_invoice(
    path: str,
    vendor: str,
    invoice_number: str,
    invoice_date: str,
    rows: list[tuple[str, str, str, str]],
    subtotal: str,
    tax: str,
    total: str,
    width: int = 1400,
    height: int = 1000,
) -> None:
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((20, 20, width - 20, height - 20), outline="black", width=3)

    f_big = _load_font(42, bold=True)
    f_label = _load_font(30)
    f_table = _load_font(28)

    y = 60
    d.text((80, y), vendor, font=f_big, fill="black")
    y += 80
    d.text((80, y), f"Invoice Number: {invoice_number}", font=f_label, fill="black")
    y += 48
    d.text((80, y), f"Invoice Date: {invoice_date}", font=f_label, fill="black")
    y += 75

    col_x = [80, 480, 650, 830]
    # No column-header labels row: TesseractExtractor's line-item table
    # detection (_extract_line_items) clusters columns from every candidate
    # row with >=3 words together, and a label row ("Description Qty Price
    # Amount") is itself such a row -- it pollutes the same column
    # clustering the real data row needs, confirmed empirically. A single
    # short caption stays under that 3-word threshold instead.
    d.text((80, y), "Line Items:", font=f_label, fill="black")
    y += 60

    for row in rows:
        for x, v in zip(col_x, row, strict=True):
            d.text((x, y), str(v), font=f_table, fill="black")
        y += 55

    y += 40
    d.text((760, y), f"Subtotal: {subtotal}", font=f_label, fill="black")
    y += 48
    d.text((760, y), f"Sales Tax: {tax}", font=f_label, fill="black")
    y += 48
    d.text((760, y), f"Total Due: {total}", font=f_big, fill="black")

    img.save(path)
