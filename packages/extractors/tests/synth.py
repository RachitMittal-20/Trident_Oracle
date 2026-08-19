"""Generates a synthetic invoice image for OCR tests, in-process.

Uses Pillow's bundled default font (ImageFont.load_default(size=...)) rather
than a system font path, so this works identically on any machine/CI runner
without depending on which fonts happen to be installed.

Line spacing is deliberately generous (80px between rows at a 22px font) --
tight spacing causes Tesseract's own line-segmentation to merge adjacent rows
into one detected line, which silently corrupts label matching. Real invoices
almost always have more visual separation than a naive first attempt at this
fixture did; this mirrors that rather than fighting the OCR engine.
"""

import io

from PIL import Image, ImageDraw, ImageFont

EXPECTED_INVOICE_NUMBER = "INV-2026-00417"
EXPECTED_INVOICE_DATE = "2026-08-12"
EXPECTED_TAX = "$29.47"
EXPECTED_TOTAL = "$450.47"
EXPECTED_LINE_ITEMS = [
    ("Steel bracket 4in", "40", "3.25", "130.00"),
    ("Hydraulic hose 10ft", "12", "18.75", "225.00"),
    ("Rubber gasket 6in", "60", "1.10", "66.00"),
]

ROW_HEIGHT = 80


def make_invoice_png() -> bytes:
    font = ImageFont.load_default(size=22)
    rows_text = [
        "Acme Supply Co.",
        f"Invoice Number: {EXPECTED_INVOICE_NUMBER}",
        f"Invoice Date: {EXPECTED_INVOICE_DATE}",
        f"Sales Tax: {EXPECTED_TAX}",
        f"Total Due: {EXPECTED_TOTAL}",
        "Description              Qty    Unit Price    Line Total",
    ]
    for description, qty, unit_price, line_total in EXPECTED_LINE_ITEMS:
        rows_text.append(
            f"{description:<25} {qty:>5} {unit_price:>10}  {line_total:>10}"
        )

    image = Image.new("RGB", (900, ROW_HEIGHT * (len(rows_text) + 1)), "white")
    draw = ImageDraw.Draw(image)
    for i, text in enumerate(rows_text):
        draw.text((40, 40 + i * ROW_HEIGHT), text, fill="black", font=font)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
