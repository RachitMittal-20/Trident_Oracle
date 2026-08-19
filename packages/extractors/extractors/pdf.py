"""PDF-to-image rendering, used to hand multi-page PDFs to a vision model one
page-image at a time (Gemini takes images, not PDF page structure)."""

import io

import pypdfium2 as pdfium


def render_pdf_pages(pdf_bytes: bytes, scale: float = 2.0) -> list[bytes]:
    """Render every page of a PDF to a PNG image, in page order.

    `scale` is a multiplier on the PDF's native 72dpi -- 2.0 gives ~144dpi,
    enough detail for a vision model to read small print without producing
    unnecessarily large images.
    """
    pages: list[bytes] = []
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        for page in pdf:
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            pages.append(buffer.getvalue())
    finally:
        pdf.close()
    return pages
