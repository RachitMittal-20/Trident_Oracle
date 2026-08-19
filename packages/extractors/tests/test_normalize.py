from datetime import date
from decimal import Decimal

import pytest
from core.errors import ExtractionError
from extractors.normalize import (
    normalize_description,
    normalize_vendor_name,
    parse_currency,
    parse_date,
)

# --- parse_date --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-08-12", date(2026, 8, 12)),
        ("2026/08/12", date(2026, 8, 12)),
        ("08/12/2026", date(2026, 8, 12)),  # month-first (US) default
        ("August 12, 2026", date(2026, 8, 12)),
        ("12 August 2026", date(2026, 8, 12)),
        ("Aug 12, 2026", date(2026, 8, 12)),
        (" 2026-08-12 ", date(2026, 8, 12)),
    ],
)
def test_parse_date_common_formats(raw: str, expected: date) -> None:
    assert parse_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "not a date",
        "13/45/2026",
        "2026-13-45",
        "yesterday-ish",
    ],
)
def test_parse_date_rejects_malformed(raw: str | None) -> None:
    with pytest.raises(ExtractionError):
        parse_date(raw)


# --- parse_currency ------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("100", Decimal("100")),
        ("100.50", Decimal("100.50")),
        ("$100.50", Decimal("100.50")),
        ("1,234.56", Decimal("1234.56")),
        ("$1,234.56", Decimal("1234.56")),
        ("  $1,234.56  ", Decimal("1234.56")),
        ("(1,234.56)", Decimal("-1234.56")),
        ("1234.56-", Decimal("-1234.56")),
        ("-1234.56", Decimal("-1234.56")),
        ("(100)", Decimal("-100")),
        ("0", Decimal("0")),
        ("0.00", Decimal("0.00")),
        ("USD 1,234.56", Decimal("1234.56")),
        ("€1234.56", Decimal("1234.56")),
    ],
)
def test_parse_currency_valid(raw: str, expected: Decimal) -> None:
    assert parse_currency(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "abc",
        "$",
        "12.34.56",
        "()",
        "-",
    ],
)
def test_parse_currency_rejects_malformed(raw: str | None) -> None:
    with pytest.raises(ExtractionError):
        parse_currency(raw)


def test_parse_currency_never_returns_float() -> None:
    result = parse_currency("$1,234.56")
    assert isinstance(result, Decimal)


# --- normalize_vendor_name -------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ACME Corp.", "acme"),
        ("Acme Corporation", "acme"),
        ("ACME CORP", "acme"),
        ("Northwind Traders LLC", "northwind traders"),
        ("Northwind Traders, LLC.", "northwind traders"),
        ("BlueSky Logistics", "bluesky logistics"),
        ("Bluesky Logistics Pvt. Ltd.", "bluesky logistics"),
        ("", ""),
    ],
)
def test_normalize_vendor_name(raw: str, expected: str) -> None:
    assert normalize_vendor_name(raw) == expected


def test_normalize_vendor_name_variants_collapse_to_same_value() -> None:
    variants = ["ACME Corp.", "Acme Corporation", "ACME CORP"]
    normalized = {normalize_vendor_name(v) for v in variants}
    assert len(normalized) == 1


# --- normalize_description -----------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Widget", "widget"),
        ("", ""),
    ],
)
def test_normalize_description_basic(raw: str, expected: str) -> None:
    assert normalize_description(raw) == expected


def test_normalize_description_expands_abbreviations() -> None:
    assert normalize_description("10 ft cable") == "10 cable feet"


def test_normalize_description_sorts_tokens() -> None:
    assert normalize_description("blue widget") == normalize_description("widget blue")


def test_normalize_description_strips_punctuation() -> None:
    assert normalize_description("Steel bracket, zinc-plated") == normalize_description(
        "steel bracket zinc plated"
    )
