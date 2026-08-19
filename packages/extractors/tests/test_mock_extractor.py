"""Golden-file test: fixture JSON in, expected ExtractionResult out. The
"file_bytes" here is a placeholder -- MockExtractor never inspects it, which
is the point: nothing in this suite touches a real document or the network.
"""

from decimal import Decimal

from extractors.base import ExtractionResult
from extractors.mock import MockExtractor
from extractors.normalize import parse_currency, parse_date


def test_clean_invoice_fixture_matches_expected_result() -> None:
    result = MockExtractor("clean_invoice").extract(b"placeholder", "application/pdf")

    assert isinstance(result, ExtractionResult)
    assert result.backend == "mock"
    assert result.header.invoice_number == "INV-2026-00417"
    assert result.header.vendor_name == "Acme Supply Co."
    assert len(result.line_items) == 3
    assert result.line_items[0].description == "Steel bracket, 4in, zinc-plated"
    assert result.confidence["header.total"] == 0.99
    assert result.bbox["header.total"].page == 1


def test_clean_invoice_fixture_reconciles_after_normalization() -> None:
    result = MockExtractor("clean_invoice").extract(b"placeholder", "application/pdf")

    subtotal = parse_currency(result.header.subtotal)
    tax = parse_currency(result.header.tax)
    total = parse_currency(result.header.total)
    assert subtotal + tax == total

    line_total_sum = sum(
        (parse_currency(line.line_total) for line in result.line_items), start=Decimal("0")
    )
    assert line_total_sum == subtotal

    assert parse_date(result.header.invoice_date).isoformat() == "2026-08-12"


def test_low_confidence_fixture_has_low_scores() -> None:
    result = MockExtractor("low_confidence").extract(b"placeholder", "image/jpeg")

    assert all(score < 0.75 for score in result.confidence.values())
    assert result.bbox == {}


def test_different_fixtures_are_independent() -> None:
    clean = MockExtractor("clean_invoice").extract(b"x", "application/pdf")
    low = MockExtractor("low_confidence").extract(b"x", "application/pdf")

    assert clean.header.invoice_number != low.header.invoice_number
