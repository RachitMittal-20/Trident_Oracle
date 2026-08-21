"""Vendor name normalization.

Vendors are entered by humans across invoices, POs, and vendor records, so the
same company shows up spelled a dozen ways -- "ACME Corp.", "Acme Corporation",
"ACME CORP". Duplicate detection (duplicates.py) depends on all of those
collapsing to one identical string before comparison, so this is factored out
on its own: lowercase, strip punctuation, drop legal-entity suffixes, collapse
whitespace.

Pure function, no I/O -- CLAUDE.md's most important rule.
"""

import re

# Legal-entity suffixes to drop, compared as whole lowercase tokens after
# punctuation stripping (so "Corp." and "Corp" both become the token "corp").
_LEGAL_SUFFIXES = frozenset(
    {
        "ltd",
        "limited",
        "pvt",
        "private",
        "llp",
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "gmbh",
        "sa",
        "bv",
    }
)

_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_vendor_name(name: str) -> str:
    """Collapse a vendor name to a comparison-safe canonical form.

    "ACME Corp.", "Acme Corporation", and "ACME CORP" all normalize to
    "acme". Not guaranteed unique across genuinely different companies that
    happen to share a normalized form -- callers combine this with other
    signals (see duplicates.py) rather than treating it as an identity key
    on its own.
    """
    lowered = name.lower()
    stripped = _PUNCTUATION_RE.sub(" ", lowered)
    tokens = [tok for tok in _WHITESPACE_RE.split(stripped.strip()) if tok]
    kept = [tok for tok in tokens if tok not in _LEGAL_SUFFIXES]
    return " ".join(kept)
