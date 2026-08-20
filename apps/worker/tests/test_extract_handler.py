"""Pure unit tests for extract_handler.py's standalone helper functions --
no DB, no network. See test_extract_handler_integration.py for the full
download-extract-persist-transition flow against a live Postgres.
"""

import pytest
from core.errors import ExtractionError
from worker.extract_handler import (
    _safe_parse_currency,
    _safe_parse_date,
    compute_overall_confidence,
)

# --- compute_overall_confidence: min, not mean ------------------------


def test_overall_confidence_is_the_minimum() -> None:
    confidence = {"header.total": 0.99, "header.tax": 0.95, "lines[0].qty": 0.4}
    assert compute_overall_confidence(confidence) == 0.4


def test_overall_confidence_is_not_the_mean() -> None:
    confidence = {"header.total": 0.99, "header.tax": 0.95, "lines[0].qty": 0.4}
    mean = sum(confidence.values()) / len(confidence)
    result = compute_overall_confidence(confidence)
    assert result != pytest.approx(mean)
    assert result < mean  # the whole point: the weak field pulls it down, not up


def test_overall_confidence_single_field_is_that_field() -> None:
    assert compute_overall_confidence({"header.total": 0.73}) == 0.73


def test_overall_confidence_all_equal_confidences() -> None:
    assert compute_overall_confidence({"a": 0.8, "b": 0.8, "c": 0.8}) == 0.8


def test_overall_confidence_empty_is_none_not_zero() -> None:
    # No confidence scores at all means "we don't know", not "zero
    # confidence everywhere" -- those should not read the same.
    assert compute_overall_confidence({}) is None


def test_overall_confidence_one_weak_field_among_many_strong_ones() -> None:
    confidence = {f"lines[{i}].description": 0.98 for i in range(9)}
    confidence["header.total"] = 0.2
    assert compute_overall_confidence(confidence) == 0.2


# --- _safe_parse_date / _safe_parse_currency: degrade, don't crash -----


def test_safe_parse_date_none_input_returns_none() -> None:
    assert _safe_parse_date(None) is None


def test_safe_parse_date_valid_input() -> None:
    from datetime import date

    assert _safe_parse_date("2026-08-12") == date(2026, 8, 12)


def test_safe_parse_date_unparseable_returns_none_not_raise() -> None:
    assert _safe_parse_date("not a date at all") is None


def test_safe_parse_currency_none_input_returns_none() -> None:
    assert _safe_parse_currency(None) is None


def test_safe_parse_currency_valid_input() -> None:
    from decimal import Decimal

    assert _safe_parse_currency("$1,234.56") == Decimal("1234.56")


def test_safe_parse_currency_unparseable_returns_none_not_raise() -> None:
    assert _safe_parse_currency("garbled ###") is None


def test_safe_parse_helpers_never_raise_extraction_error() -> None:
    # The whole point of the "safe" wrapper: normalize.py's parsers raise
    # ExtractionError on bad input, but a single unparseable field must not
    # crash the entire extraction persistence step.
    try:
        _safe_parse_date("garbage")
        _safe_parse_currency("garbage")
    except ExtractionError:
        pytest.fail("_safe_parse_* must not propagate ExtractionError")
