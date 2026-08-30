"""Generates the four demo invoice images in this directory. Demo-only
tooling, not imported by the app.

Usage: uv run python demo/fixtures/generate.py

Design constraints on the rendered layout are documented in _gen_lib.py --
learned empirically by running real output through the real
TesseractExtractor (packages/extractors/extractors/tesseract.py) until every
field it's supposed to read actually came back correct, not by assumption.
"""

import io
import sys
from pathlib import Path

from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).parent))
from _gen_lib import render_invoice  # noqa: E402

FIXTURES_DIR = Path(__file__).parent

if __name__ == "__main__":
    # 1. Clean invoice -- matches demo/seed_demo.py's PO-3001/GRN-3001
    # exactly (qty ordered == qty received == qty billed). Auto-posts.
    render_invoice(
        str(FIXTURES_DIR / "01-clean-invoice.png"),
        vendor="Northwind Traders",
        invoice_number="INV-4001",
        invoice_date="2026-08-20",
        rows=[("Bracket", "8", "40.00", "320.00")],
        subtotal="320.00",
        tax="0.00",
        total="320.00",
    )

    # 2. Over-billed -- matches PO-3002 (12 ordered) but GRN-3002 only
    # received 9. Bills for the full ordered quantity, not what arrived --
    # QTY_OVER, block severity, PENDING_APPROVAL.
    render_invoice(
        str(FIXTURES_DIR / "02-overbilled-invoice.png"),
        vendor="Acme Supply",
        invoice_number="INV-4002",
        invoice_date="2026-08-21",
        rows=[("Gasket", "12", "15.00", "180.00")],
        subtotal="180.00",
        tax="0.00",
        total="180.00",
    )

    # 3. Same content as a clean, matchable invoice (PO-3003/GRN-3003), but
    # rendered as a genuinely degraded phone-photo simulation -- gaussian
    # blur + heavy JPEG recompression -- so TesseractExtractor's own
    # confidence comes out low for real, not a canned "low confidence"
    # fixture. Verified this actually produces per-field confidence below
    # 0.85 (this project's default policy.min_field_confidence) before
    # finalizing -- see docs/DEMO.md.
    render_invoice(
        str(FIXTURES_DIR / "_03-source.png"),
        vendor="Northwind Traders",
        invoice_number="INV-4003",
        invoice_date="2026-08-22",
        rows=[("Cable", "5", "20.00", "100.00")],
        subtotal="100.00",
        tax="0.00",
        total="100.00",
    )
    # radius=1.0/quality=60 chosen empirically: enough to drop
    # TesseractExtractor's real per-field confidence below this project's
    # default policy.min_field_confidence (0.85) on every field (verified:
    # ~0.71 min), without garbling any field past the point of parsing --
    # a heavier blur was tried first and produced literally unparseable
    # line items (empty qty/price strings), which crashes match_handler's
    # domain-object reconstruction (InvoiceLine requires qty > 0) instead
    # of reaching the intended NEEDS_VERIFICATION outcome -- see
    # docs/DEMO.md and docs/ROADMAP.md for that real robustness gap.
    clean = Image.open(FIXTURES_DIR / "_03-source.png")
    blurred = clean.filter(ImageFilter.GaussianBlur(radius=1.0))
    buf = io.BytesIO()
    blurred.convert("RGB").save(buf, format="JPEG", quality=60)
    buf.seek(0)
    Image.open(buf).save(FIXTURES_DIR / "03-blurry-photo.jpg", format="JPEG", quality=60)
    (FIXTURES_DIR / "_03-source.png").unlink()

    # 4. Literal byte-copy of fixture 1 -- same content_hash, caught by
    # invoices.content_hash's UNIQUE(tenant_id, content_hash) constraint at
    # ingestion (apps/api/api/ingest.py::find_invoice_by_content_hash),
    # before extraction or matching ever runs. See docs/DEMO.md for exactly
    # which mechanism this demonstrates and which one it doesn't.
    (FIXTURES_DIR / "04-duplicate-of-01.png").write_bytes(
        (FIXTURES_DIR / "01-clean-invoice.png").read_bytes()
    )

    print("Generated demo/fixtures/01-clean-invoice.png")
    print("Generated demo/fixtures/02-overbilled-invoice.png")
    print("Generated demo/fixtures/03-blurry-photo.jpg")
    print("Generated demo/fixtures/04-duplicate-of-01.png")
