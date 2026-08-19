"""Runs every Extractor implementation and asserts the interface contract
(ExtractionResult's shape and invariants) holds identically across backends
-- this is the actual point of having an Extractor interface at all
("extractors are swappable", CLAUDE.md principle 2). GeminiExtractor needs a
live API and isn't included here (see test_gemini_extractor.py for its own
fully-mocked coverage); MockExtractor stands in as the third implementation
of the same interface.
"""

from collections.abc import Callable

import pytest
from extractors.base import BoundingBox, ExtractionResult, Extractor, InvoiceHeader, LineItem
from extractors.mock import MockExtractor
from extractors.tesseract import TesseractExtractor
from synth import make_invoice_png

EXTRACTOR_FACTORIES: dict[str, Callable[[], Extractor]] = {
    "mock": lambda: MockExtractor("clean_invoice"),
    "tesseract": lambda: TesseractExtractor(),
}

# Each backend gets input appropriate to how it actually works -- Mock
# ignores its input entirely, Tesseract needs a real image to OCR. The
# contract under test is the *output* shape, not identical input handling.
INPUTS: dict[str, tuple[bytes, str]] = {
    "mock": (b"placeholder", "application/pdf"),
    "tesseract": (make_invoice_png(), "image/png"),
}


@pytest.fixture(params=sorted(EXTRACTOR_FACTORIES), scope="module")
def result(request: pytest.FixtureRequest) -> ExtractionResult:
    backend_name = request.param
    extractor = EXTRACTOR_FACTORIES[backend_name]()
    file_bytes, mime_type = INPUTS[backend_name]
    return extractor.extract(file_bytes, mime_type)


def test_result_is_extraction_result(result: ExtractionResult) -> None:
    assert isinstance(result, ExtractionResult)


def test_header_is_invoice_header(result: ExtractionResult) -> None:
    assert isinstance(result.header, InvoiceHeader)


def test_line_items_are_line_item_tuples(result: ExtractionResult) -> None:
    assert isinstance(result.line_items, tuple)
    for item in result.line_items:
        assert isinstance(item, LineItem)
        assert isinstance(item.description, str)
        assert isinstance(item.qty, str)
        assert isinstance(item.unit_price, str)
        assert isinstance(item.line_total, str)


def test_confidence_values_are_bounded_floats(result: ExtractionResult) -> None:
    assert isinstance(result.confidence, dict)
    for path, score in result.confidence.items():
        assert isinstance(path, str)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0


def test_bbox_values_are_bounding_boxes(result: ExtractionResult) -> None:
    assert isinstance(result.bbox, dict)
    for path, box in result.bbox.items():
        assert isinstance(path, str)
        assert isinstance(box, BoundingBox)
        assert box.page >= 1
        assert 0.0 <= box.x <= 1.0
        assert 0.0 <= box.y <= 1.0


def test_metadata_fields_present_and_well_typed(result: ExtractionResult) -> None:
    assert isinstance(result.backend, str) and result.backend
    assert isinstance(result.model_version, str) and result.model_version
    assert isinstance(result.latency_ms, int) and result.latency_ms >= 0
    assert isinstance(result.estimated_tokens, int) and result.estimated_tokens >= 0


def test_result_is_frozen(result: ExtractionResult) -> None:
    with pytest.raises(Exception):  # noqa: B017 -- pydantic ValidationError, frozen model
        result.backend = "tampered"  # type: ignore[misc]


def test_backends_report_their_own_distinct_name() -> None:
    results = {
        name: factory().extract(*INPUTS[name]) for name, factory in EXTRACTOR_FACTORIES.items()
    }
    assert results["mock"].backend == "mock"
    assert results["tesseract"].backend == "tesseract"
