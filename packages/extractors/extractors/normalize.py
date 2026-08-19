"""Post-processing applied to every backend's raw extraction output.

Pure functions only -- no I/O, safe to unit test exhaustively. Extraction
backends report values exactly as they appear on the document (see
extractors.base.InvoiceHeader); these functions turn that raw text into the
clean types the rest of the system expects (Decimal, date) or a canonical
form suitable for fuzzy matching (normalized vendor/description strings).

Currency parsing assumes US-style formatting (period as decimal separator,
comma as thousands separator) and date parsing defaults to month-first when
a date is ambiguous (e.g. "01/02/2026") -- neither attempts locale detection.
That's a deliberate scope limit, not an oversight.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from core.errors import ExtractionError
from dateutil import parser as dateutil_parser

_CURRENCY_STRIP_RE = re.compile(r"[^\d.,]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")

LEGAL_SUFFIXES = frozenset(
    {
        "co",
        "company",
        "corp",
        "corporation",
        "gmbh",
        "inc",
        "incorporated",
        "limited",
        "llc",
        "ltd",
        "plc",
        "pvt",
    }
)

# Deliberately small -- expand as real extraction output surfaces more cases.
ABBREVIATIONS = {
    "asy": "assembly",
    "ea": "each",
    "ft": "feet",
    "gal": "gallon",
    "in": "inch",
    "lb": "pound",
    "lbs": "pound",
    "no": "number",
    "pc": "piece",
    "pcs": "piece",
    "pkg": "package",
    "qty": "quantity",
    "std": "standard",
    "ss": "stainless",
    "w": "with",
}


def parse_date(raw: str | None) -> date:
    """Parse a date in any common format to a date object.

    Raises ExtractionError on empty, missing, or unparseable input.
    """
    if raw is None:
        raise ExtractionError("cannot parse empty date value")
    text = raw.strip()
    if not text:
        raise ExtractionError("cannot parse empty date value")
    try:
        parsed = dateutil_parser.parse(text, dayfirst=False)
    except (ValueError, OverflowError) as exc:
        raise ExtractionError(f"could not parse date value: {raw!r}") from exc
    return parsed.date()


def parse_currency(raw: str | None) -> Decimal:
    """Parse a currency string to a Decimal.

    Handles thousands separators ("1,234.56"), trailing-minus negatives
    ("1234.56-"), parentheses negatives ("(1,234.56)"), and currency symbols
    ("$1,234.56"). Raises ExtractionError on empty or unparseable input.
    """
    if raw is None:
        raise ExtractionError("cannot parse empty currency value")
    text = raw.strip()
    if not text:
        raise ExtractionError("cannot parse empty currency value")

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    if text.endswith("-"):
        negative = True
        text = text[:-1].strip()
    if text.startswith("-"):
        negative = True
        text = text[1:].strip()

    cleaned = _CURRENCY_STRIP_RE.sub("", text).replace(",", "")
    if not cleaned:
        raise ExtractionError(f"could not parse currency value: {raw!r}")

    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ExtractionError(f"could not parse currency value: {raw!r}") from exc

    return -value if negative else value


def normalize_vendor_name(raw: str) -> str:
    """Lowercase, strip punctuation and legal suffixes, collapse whitespace.

    "ACME Corp.", "Acme Corporation", and "ACME CORP" all normalize to "acme".
    """
    if not raw:
        return ""
    cleaned = _NON_ALNUM_RE.sub(" ", raw.lower())
    tokens = [t for t in cleaned.split() if t not in LEGAL_SUFFIXES]
    return " ".join(tokens)


def normalize_description(raw: str) -> str:
    """Lowercase, strip punctuation, expand known abbreviations, sort tokens.

    Token order shouldn't matter for the fuzzy line-item matcher (matching
    engine stage 3), so sorting makes two differently-worded but
    same-content descriptions compare equal.
    """
    if not raw:
        return ""
    cleaned = _NON_ALNUM_RE.sub(" ", raw.lower())
    tokens = [ABBREVIATIONS.get(token, token) for token in cleaned.split()]
    return " ".join(sorted(tokens))
