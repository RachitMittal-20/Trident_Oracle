"""Local Tesseract OCR extraction backend. No network call, no API key --
this is the free-tier-quota fallback (docs/ARCHITECTURE.md section 9).
pytesseract, cv2, and numpy are imported only in this module, same discipline
as gemini.py and the google-genai SDK.

Confidence honesty: the confidence this backend reports is Tesseract's own
per-word OCR confidence (how sure the engine is about the characters it
read), averaged across the words composing a field. That is a fundamentally
weaker and differently-shaped signal than a vision-language model's
calibrated confidence. Tesseract confidence answers "did I read these
characters correctly?" -- it has no idea whether it found the *right* text
for the field at all (a mislabeled regex match can read cleanly at 95%
confidence and still be the wrong value). When both backends' confidence
scores feed the same decision matrix (docs/ARCHITECTURE.md section 6),
Tesseract's numbers should be treated as noisier and less trustworthy at
the same nominal value, not compared like-for-like.

Header fields are found by labelled-regex extraction: search OCR'd text lines
for a configurable set of label synonyms (e.g. "Invoice No.", "Inv #") and
take the text after the label (same line, or the next line if the label
sits alone). invoice_number, invoice_date, subtotal, total, and tax are
attempted this way -- vendor_name and currency have no reliable label to
search for on a typical invoice, and due_date is left for a later pass.
Those fields come back None, honestly, rather than guessed.

subtotal specifically: a documented earlier version of this module deferred
it deliberately ("left for a later pass"), same reasoning as due_date. That
deferral had a consequence more serious than "one missing nice-to-have
field" -- core.matching.three_way.run_three_way_match requires subtotal,
tax, and total all non-None before it will run at all, so an invoice
extracted purely by this backend could never reach the matching engine: the
'match' job would raise MatchingError every attempt, retry with backoff,
and eventually dead-letter, leaving the invoice stuck at MATCHING forever
with no user-visible error. Discovered by actually running a real invoice
image through this backend end to end, not by reading the code. Fixed the
same way total/tax already are -- a label synonym, not a guess.

Line items are found by column-position clustering: OCR word left-edges are
clustered into columns, columns are classified numeric vs. text, and the
rightmost numeric column is treated as line_total (the standard invoice
convention), working leftward through unit_price and qty. This is a
heuristic layout detector, not a trained table model -- it works well on
clean, simple tables and degrades on anything unusual.
"""

import io
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import cv2
import numpy as np
import pytesseract
import structlog
from core.errors import ExtractionError
from PIL import Image
from pytesseract import Output

from extractors.base import BoundingBox, ExtractionResult, Extractor, InvoiceHeader, LineItem
from extractors.pdf import render_pdf_pages

log = structlog.get_logger()

# Deliberately scoped to the four fields the labelled-regex approach can find
# reliably. "date" alone is intentionally excluded from invoice_date's
# synonyms -- it would also match "Due Date:" (via \bdate\b), silently
# grabbing the wrong field. Better to miss a plain "Date:" label than
# confidently report the wrong date.
DEFAULT_LABEL_SYNONYMS: dict[str, tuple[str, ...]] = {
    "invoice_number": ("invoice number", "invoice no", "invoice #", "inv no", "inv#"),
    "invoice_date": ("invoice date", "inv date", "date of invoice"),
    "subtotal": ("subtotal", "sub-total", "sub total", "net amount"),
    "total": ("total due", "amount due", "grand total", "balance due", "total"),
    "tax": ("sales tax", "vat", "gst", "tax"),
}

MIN_TABLE_ROW_WORDS = 3
COLUMN_GAP_THRESHOLD = 0.03  # fraction of page width between column clusters
NUMERIC_CELL_RE = re.compile(r"^[(\$€£]?-?[\d,]+(\.\d+)?\)?-?$")


@dataclass
class _Word:
    text: str
    conf: float  # 0-100, Tesseract's scale; -1 means "no confidence reported"
    x: float  # left, normalized 0-1 to the page image's width
    y: float  # top, normalized 0-1 to the page image's height
    w: float
    h: float
    page: int


@dataclass
class _Line:
    """One line of text as Tesseract's own layout analysis grouped it
    (block/paragraph/line), not a line we invented by re-clustering boxes."""

    page: int
    words: list[_Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def word_spans(self) -> list[tuple[_Word, int, int]]:
        spans: list[tuple[_Word, int, int]] = []
        pos = 0
        for word in self.words:
            start = pos
            end = start + len(word.text)
            spans.append((word, start, end))
            pos = end + 1  # +1 for the joining space in .text
        return spans


class TesseractExtractor(Extractor):
    def __init__(self, label_synonyms: dict[str, tuple[str, ...]] | None = None) -> None:
        self._label_synonyms = label_synonyms or DEFAULT_LABEL_SYNONYMS

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        start = time.monotonic()
        images = self._load_images(file_bytes, mime_type)

        lines_by_page: dict[int, list[_Line]] = {}
        for page, image in enumerate(images, start=1):
            lines_by_page[page] = _ocr_page(image, page)
        all_lines = [line for page in sorted(lines_by_page) for line in lines_by_page[page]]

        header, header_confidence, header_bbox, used_line_ids = self._extract_header(all_lines)
        line_items, line_confidence, line_bbox = _extract_line_items(lines_by_page, used_line_ids)

        latency_ms = int((time.monotonic() - start) * 1000)

        return ExtractionResult(
            header=header,
            line_items=tuple(line_items),
            confidence={**header_confidence, **line_confidence},
            bbox={**header_bbox, **line_bbox},
            backend="tesseract",
            model_version=_tesseract_version(),
            latency_ms=latency_ms,
            estimated_tokens=0,
        )

    def _load_images(self, file_bytes: bytes, mime_type: str) -> list[Image.Image]:
        if mime_type == "application/pdf":
            pages = render_pdf_pages(file_bytes)
            return [Image.open(io.BytesIO(page)) for page in pages]
        return [Image.open(io.BytesIO(file_bytes))]

    def _extract_header(
        self, lines: list[_Line]
    ) -> tuple[InvoiceHeader, dict[str, float], dict[str, BoundingBox], set[int]]:
        confidence: dict[str, float] = {}
        bbox: dict[str, BoundingBox] = {}
        used_line_ids: set[int] = set()
        values: dict[str, str | None] = {
            "invoice_number": None,
            "invoice_date": None,
            "due_date": None,
            "vendor_name": None,
            "currency": None,
            "subtotal": None,
            "tax": None,
            "total": None,
        }

        for field_name, synonyms in self._label_synonyms.items():
            if field_name not in values or not synonyms:
                continue
            result = _extract_labelled_field(lines, synonyms, used_line_ids)
            if result is None:
                continue
            value, field_confidence, field_bbox = result
            values[field_name] = value
            path = f"header.{field_name}"
            confidence[path] = field_confidence
            if field_bbox is not None:
                bbox[path] = field_bbox

        return InvoiceHeader(**values), confidence, bbox, used_line_ids


def _tesseract_version() -> str:
    return _cached_tesseract_version()


@lru_cache(maxsize=1)
def _cached_tesseract_version() -> str:
    try:
        return str(pytesseract.get_tesseract_version())
    except pytesseract.TesseractNotFoundError as exc:
        raise ExtractionError("tesseract binary not found on PATH") from exc


def _ocr_page(image: Image.Image, page: int) -> list[_Line]:
    preprocessed = _preprocess(image)
    page_height, page_width = preprocessed.shape[:2]
    try:
        # --psm 6 ("assume a single uniform block of text"), not Tesseract's
        # default PSM 3 (automatic page segmentation with orientation/script
        # detection). Confirmed empirically against a real invoice-table
        # layout: PSM 3's automatic column/segmentation analysis silently
        # drops short tokens that sit alone in a column surrounded by
        # whitespace on all sides (a bare "Qty"/"10"/"25.00" cell, exactly
        # the shape of a real line-item table) -- not misread, not merged,
        # just absent from image_to_data's output entirely, with no error.
        # PSM 6 does not do that: same preprocessing, same image, every
        # token recognized. This is the standard recommended PSM for
        # invoice/receipt-shaped documents (mostly one column, no multi-
        # article newspaper-style layout for PSM 3's segmentation to earn
        # its keep on) and directly explains part of this backend's poor
        # measured line-item recall in docs/BENCHMARKS.md.
        data = pytesseract.image_to_data(preprocessed, output_type=Output.DICT, config="--psm 6")
    except (pytesseract.TesseractError, pytesseract.TesseractNotFoundError) as exc:
        raise ExtractionError(f"tesseract OCR failed: {exc}") from exc
    return _words_to_lines(data, page, page_width, page_height)


def _preprocess(image: Image.Image) -> np.ndarray:
    """Greyscale -> deskew -> adaptive threshold -> denoise. Targets phone
    photos: uneven lighting, a few degrees of rotation, and JPEG noise are
    the norm for this backend's input, not the exception."""
    rgb = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = _deskew(gray)
    thresholded = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )
    return cv2.medianBlur(thresholded, 3)


def _deskew(gray: np.ndarray, max_correction_degrees: float = 15.0) -> np.ndarray:
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    coords = cv2.findNonZero(otsu)
    if coords is None or len(coords) < 20:
        return gray  # too little foreground to estimate an angle reliably

    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.1 or abs(angle) > max_correction_degrees:
        return gray  # not worth correcting, or too large a correction to trust

    height, width = gray.shape
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    rotated: np.ndarray = cv2.warpAffine(
        gray, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return rotated


def _words_to_lines(
    data: dict[str, Any], page: int, page_width: int, page_height: int
) -> list[_Line]:
    """Tesseract's own layout analysis groups detections into a
    page/block/paragraph/line/word hierarchy. This keeps only word-level
    entries (level 5) and regroups them by (block, paragraph, line) -- i.e.
    it trusts Tesseract's line grouping rather than re-deriving it."""
    groups: dict[tuple[int, int, int], list[_Word]] = {}
    count = len(data["level"])
    for i in range(count):
        if data["level"][i] != 5:
            continue
        text = data["text"][i]
        if not text or not text.strip():
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups.setdefault(key, []).append(
            _Word(
                text=text,
                conf=conf,
                x=data["left"][i] / page_width,
                y=data["top"][i] / page_height,
                w=data["width"][i] / page_width,
                h=data["height"][i] / page_height,
                page=page,
            )
        )

    lines = [
        _Line(page=page, words=sorted(words, key=lambda w: w.x)) for words in groups.values()
    ]
    lines.sort(key=lambda line: line.words[0].y if line.words else 0.0)
    return lines


def _avg_conf(words: list[_Word]) -> float:
    valid = [w.conf for w in words if w.conf >= 0]
    if not valid:
        return 0.0
    return round((sum(valid) / len(valid)) / 100.0, 4)


def _union_bbox(words: list[_Word]) -> BoundingBox | None:
    if not words:
        return None
    left = min(w.x for w in words)
    top = min(w.y for w in words)
    right = max(w.x + w.w for w in words)
    bottom = max(w.y + w.h for w in words)
    return BoundingBox(
        page=words[0].page,
        x=max(0.0, min(1.0, left)),
        y=max(0.0, min(1.0, top)),
        w=max(0.0, min(1.0, right - left)),
        h=max(0.0, min(1.0, bottom - top)),
    )


def _extract_labelled_field(
    lines: list[_Line], synonyms: tuple[str, ...], used_line_ids: set[int]
) -> tuple[str, float, BoundingBox | None] | None:
    for i, line in enumerate(lines):
        lowered = line.text.lower()
        for synonym in synonyms:
            match = re.search(rf"\b{re.escape(synonym)}\b", lowered)
            if match is None:
                continue

            remainder = line.text[match.end() :].strip(" :#=-\t")
            if remainder:
                used_line_ids.add(id(line))
                value_words = [
                    word
                    for word, span_start, _ in line.word_spans()
                    if span_start >= match.end() and word.text.strip(" :#=-")
                ]
                if value_words:
                    return remainder, _avg_conf(value_words), _union_bbox(value_words)
                return remainder, 0.0, None

            # Label found but nothing follows it on this line -- the value is
            # likely stacked on the next line (common on narrow phone photos).
            used_line_ids.add(id(line))
            if i + 1 < len(lines) and lines[i + 1].page == line.page and lines[i + 1].words:
                next_line = lines[i + 1]
                used_line_ids.add(id(next_line))
                return next_line.text.strip(), _avg_conf(next_line.words), _union_bbox(
                    next_line.words
                )
            return None
    return None


def _is_numeric_cell(text: str) -> bool:
    return bool(NUMERIC_CELL_RE.match(text.strip()))


def _cluster_columns(rows: list[_Line]) -> list[float]:
    """Column-position clustering: sort every word's left edge, start a new
    column whenever the gap to the previous value exceeds the threshold."""
    xs = sorted(word.x for row in rows for word in row.words)
    if not xs:
        return []
    clusters: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] > COLUMN_GAP_THRESHOLD:
            clusters.append([x])
        else:
            clusters[-1].append(x)
    return [min(cluster) for cluster in clusters]


def _assign_column(x: float, boundaries: list[float]) -> int:
    column = 0
    for i, boundary in enumerate(boundaries):
        if x >= boundary - 1e-6:
            column = i
        else:
            break
    return column


def _extract_line_items(
    lines_by_page: dict[int, list[_Line]], used_line_ids: set[int]
) -> tuple[list[LineItem], dict[str, float], dict[str, BoundingBox]]:
    line_items: list[LineItem] = []
    confidence: dict[str, float] = {}
    bbox: dict[str, BoundingBox] = {}
    row_index = 0

    for page in sorted(lines_by_page):
        candidate_rows = [
            line
            for line in lines_by_page[page]
            if id(line) not in used_line_ids and len(line.words) >= MIN_TABLE_ROW_WORDS
        ]
        if not candidate_rows:
            continue

        boundaries = _cluster_columns(candidate_rows)
        if len(boundaries) < 2:
            continue  # nothing tabular found on this page

        candidate_rows.sort(key=lambda line: min(w.y for w in line.words))

        n_cols = len(boundaries)
        grid: list[dict[int, list[_Word]]] = []
        for line in candidate_rows:
            cells: dict[int, list[_Word]] = {}
            for word in line.words:
                cells.setdefault(_assign_column(word.x, boundaries), []).append(word)
            grid.append(cells)

        numeric_cols: list[int] = []
        text_cols: list[int] = []
        for col in range(n_cols):
            texts = [" ".join(w.text for w in cells[col]) for cells in grid if col in cells]
            if not texts:
                continue
            numeric_count = sum(1 for t in texts if _is_numeric_cell(t))
            (numeric_cols if numeric_count / len(texts) >= 0.6 else text_cols).append(col)

        description_col = (
            text_cols[0] if text_cols else (min(numeric_cols) if numeric_cols else None)
        )
        chosen_numeric = numeric_cols[-3:] if len(numeric_cols) >= 3 else numeric_cols
        qty_col = chosen_numeric[-3] if len(chosen_numeric) >= 3 else None
        unit_price_col = chosen_numeric[-2] if len(chosen_numeric) >= 2 else None
        line_total_col = chosen_numeric[-1] if len(chosen_numeric) >= 1 else None

        for cells in grid:
            desc_words = cells.get(description_col, []) if description_col is not None else []
            description = " ".join(w.text for w in desc_words)
            if not description.strip():
                continue  # no description -- not a real line item, likely stray noise

            qty_words = cells.get(qty_col, []) if qty_col is not None else []
            price_words = cells.get(unit_price_col, []) if unit_price_col is not None else []
            total_words = cells.get(line_total_col, []) if line_total_col is not None else []

            line_items.append(
                LineItem(
                    description=description,
                    qty=" ".join(w.text for w in qty_words),
                    unit_price=" ".join(w.text for w in price_words),
                    line_total=" ".join(w.text for w in total_words),
                )
            )

            for field_name, words in (
                ("description", desc_words),
                ("qty", qty_words),
                ("unit_price", price_words),
                ("line_total", total_words),
            ):
                path = f"lines[{row_index}].{field_name}"
                confidence[path] = _avg_conf(words)
                field_bbox = _union_bbox(words)
                if field_bbox is not None:
                    bbox[path] = field_bbox
            row_index += 1

    return line_items, confidence, bbox
