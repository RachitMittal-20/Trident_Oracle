"""TesseractExtractor tests: real OCR against a synthetic invoice image (no
network -- Tesseract runs entirely locally), plus unit tests for the pure
helper functions the pipeline is built from.
"""

import io
import re

import pytest
from extractors.base import BoundingBox, ExtractionResult
from extractors.tesseract import (
    TesseractExtractor,
    _avg_conf,
    _cluster_columns,
    _extract_labelled_field,
    _is_numeric_cell,
    _Line,
    _union_bbox,
    _Word,
)
from PIL import Image
from synth import (
    EXPECTED_INVOICE_DATE,
    EXPECTED_INVOICE_NUMBER,
    EXPECTED_LINE_ITEMS,
    EXPECTED_TAX,
    EXPECTED_TOTAL,
    make_invoice_png,
)


def _digits_only(text: str) -> str:
    return re.sub(r"[^\d]", "", text)


@pytest.fixture(scope="module")
def invoice_result() -> ExtractionResult:
    return TesseractExtractor().extract(make_invoice_png(), "image/png")


# --- End-to-end OCR ------------------------------------------------------


def test_header_fields_are_exact(invoice_result: ExtractionResult) -> None:
    assert invoice_result.header.invoice_number == EXPECTED_INVOICE_NUMBER
    assert invoice_result.header.invoice_date == EXPECTED_INVOICE_DATE
    assert invoice_result.header.tax == EXPECTED_TAX
    assert invoice_result.header.total == EXPECTED_TOTAL


def test_header_fields_not_attempted_are_none(invoice_result: ExtractionResult) -> None:
    # vendor_name/currency/due_date/subtotal have no labelled-regex synonyms
    # configured -- see the module docstring on why. They must come back
    # None, not a guess.
    assert invoice_result.header.vendor_name is None
    assert invoice_result.header.currency is None
    assert invoice_result.header.due_date is None
    assert invoice_result.header.subtotal is None


def test_header_confidence_and_bbox_present(invoice_result: ExtractionResult) -> None:
    assert 0.0 < invoice_result.confidence["header.invoice_number"] <= 1.0
    box = invoice_result.bbox["header.invoice_number"]
    assert isinstance(box, BoundingBox)
    assert box.page == 1
    assert 0.0 <= box.x <= 1.0


def test_all_expected_line_items_found(invoice_result: ExtractionResult) -> None:
    # OCR occasionally misreads a decimal separator on this backend (a real,
    # documented limitation) -- compare digit sequences rather than exact
    # formatting so the test isn't flaky on font-rendering noise.
    found_descriptions = [li.description for li in invoice_result.line_items]

    for description, qty, unit_price, line_total in EXPECTED_LINE_ITEMS:
        prefix = description.split()[0]
        matches = [li for li in invoice_result.line_items if li.description.startswith(prefix)]
        assert matches, f"no line item found starting with {prefix!r} in {found_descriptions}"
        item = matches[0]
        assert item.qty == qty
        assert _digits_only(item.unit_price) == _digits_only(unit_price)
        assert _digits_only(item.line_total) == _digits_only(line_total)


def test_line_item_confidence_and_bbox_present(invoice_result: ExtractionResult) -> None:
    real_item_index = next(
        i
        for i, li in enumerate(invoice_result.line_items)
        if li.description.startswith("Steel")
    )
    assert invoice_result.confidence[f"lines[{real_item_index}].qty"] > 0.5
    assert f"lines[{real_item_index}].qty" in invoice_result.bbox


def test_backend_and_model_version(invoice_result: ExtractionResult) -> None:
    assert invoice_result.backend == "tesseract"
    assert invoice_result.model_version  # non-empty tesseract version string
    assert invoice_result.estimated_tokens == 0  # no LLM tokens spent locally


def test_pdf_mime_type_is_handled() -> None:
    png_bytes = make_invoice_png()
    pdf_buffer = io.BytesIO()
    Image.open(io.BytesIO(png_bytes)).convert("RGB").save(pdf_buffer, "PDF")

    result = TesseractExtractor().extract(pdf_buffer.getvalue(), "application/pdf")
    assert result.header.invoice_number == EXPECTED_INVOICE_NUMBER


# --- Pure helper functions -------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("123", True),
        ("1,234.56", True),
        ("$1,234.56", True),
        ("(123.45)", True),
        ("123.45-", True),
        ("Widget", False),
        ("INV-2026-00417", False),
        ("", False),
    ],
)
def test_is_numeric_cell(text: str, expected: bool) -> None:
    assert _is_numeric_cell(text) is expected


def test_avg_conf_ignores_negative_confidence_words() -> None:
    words = [
        _Word(text="a", conf=90.0, x=0.0, y=0.0, w=0.1, h=0.1, page=1),
        _Word(text="b", conf=-1.0, x=0.0, y=0.0, w=0.1, h=0.1, page=1),
        _Word(text="c", conf=70.0, x=0.0, y=0.0, w=0.1, h=0.1, page=1),
    ]
    assert _avg_conf(words) == pytest.approx(0.8)


def test_avg_conf_empty_words_is_zero() -> None:
    assert _avg_conf([]) == 0.0


def test_union_bbox_covers_all_words() -> None:
    words = [
        _Word(text="a", conf=90.0, x=0.1, y=0.2, w=0.05, h=0.02, page=1),
        _Word(text="b", conf=90.0, x=0.2, y=0.2, w=0.05, h=0.02, page=1),
    ]
    box = _union_bbox(words)
    assert box is not None
    assert box.x == pytest.approx(0.1)
    assert box.w == pytest.approx(0.15)  # spans from 0.1 to 0.25


def test_union_bbox_empty_words_is_none() -> None:
    assert _union_bbox([]) is None


def test_cluster_columns_finds_gaps() -> None:
    row = _Line(
        page=1,
        words=[
            _Word(text="Widget", conf=90, x=0.05, y=0.1, w=0.1, h=0.02, page=1),
            _Word(text="10", conf=90, x=0.5, y=0.1, w=0.02, h=0.02, page=1),
            _Word(text="99.00", conf=90, x=0.8, y=0.1, w=0.05, h=0.02, page=1),
        ],
    )
    boundaries = _cluster_columns([row])
    assert len(boundaries) == 3


def test_extract_labelled_field_same_line() -> None:
    line = _Line(
        page=1,
        words=[
            _Word(text="Invoice", conf=90, x=0.0, y=0.0, w=0.05, h=0.02, page=1),
            _Word(text="Number:", conf=90, x=0.06, y=0.0, w=0.05, h=0.02, page=1),
            _Word(text="INV-1", conf=95, x=0.12, y=0.0, w=0.05, h=0.02, page=1),
        ],
    )
    used: set[int] = set()
    result = _extract_labelled_field([line], ("invoice number",), used)
    assert result is not None
    value, confidence, _ = result
    assert value == "INV-1"
    assert confidence == pytest.approx(0.95)
    assert id(line) in used


def test_extract_labelled_field_next_line() -> None:
    label_line = _Line(
        page=1,
        words=[_Word(text="Total:", conf=90, x=0.0, y=0.0, w=0.05, h=0.02, page=1)],
    )
    value_line = _Line(
        page=1,
        words=[_Word(text="$107.00", conf=88, x=0.0, y=0.05, w=0.06, h=0.02, page=1)],
    )
    used: set[int] = set()
    result = _extract_labelled_field([label_line, value_line], ("total",), used)
    assert result is not None
    value, _, _ = result
    assert value == "$107.00"
    assert id(label_line) in used
    assert id(value_line) in used


def test_extract_labelled_field_does_not_match_subtotal_as_total() -> None:
    line = _Line(
        page=1,
        words=[
            _Word(text="Subtotal:", conf=90, x=0.0, y=0.0, w=0.08, h=0.02, page=1),
            _Word(text="$100.00", conf=90, x=0.1, y=0.0, w=0.06, h=0.02, page=1),
        ],
    )
    used: set[int] = set()
    result = _extract_labelled_field([line], ("total",), used)
    assert result is None


def test_extract_labelled_field_not_found_returns_none() -> None:
    line = _Line(
        page=1,
        words=[_Word(text="Nothing", conf=90, x=0.0, y=0.0, w=0.05, h=0.02, page=1)],
    )
    used: set[int] = set()
    assert _extract_labelled_field([line], ("total",), used) is None
    assert used == set()
