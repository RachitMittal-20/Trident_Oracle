import pytest
from core.matching.vendor_normalize import normalize_vendor_name

# Groups of real-world name variants that must all normalize identically.
_EQUIVALENT_GROUPS: dict[str, list[str]] = {
    "acme": [
        "Acme Corp.",
        "Acme Corporation",
        "ACME CORP",
        "Acme, Inc.",
        "ACME INCORPORATED",
    ],
    "global traders": [
        "Global Traders Pvt Ltd",
        "Global Traders Private Limited",
        "GLOBAL TRADERS PVT. LTD.",
    ],
    "kumar sons": [
        "Kumar & Sons LLP",
        "KUMAR & SONS, LLP.",
        "Kumar & Sons Llp",
    ],
    "bosch": [
        "Bosch GmbH",
        "BOSCH GMBH",
    ],
    "nordic solutions": [
        "Nordic Solutions SA",
        "NORDIC SOLUTIONS SA",
    ],
    "techcorp": [
        "TechCorp BV",
        "  TechCorp   BV  ",
    ],
}


_VARIANT_CASES = [
    (expected, variant) for expected, variants in _EQUIVALENT_GROUPS.items() for variant in variants
]


@pytest.mark.parametrize(("expected", "variant"), _VARIANT_CASES)
def test_variants_normalize_to_the_expected_canonical_form(expected: str, variant: str) -> None:
    assert normalize_vendor_name(variant) == expected


def test_all_variants_within_a_group_normalize_identically() -> None:
    for variants in _EQUIVALENT_GROUPS.values():
        normalized = {normalize_vendor_name(v) for v in variants}
        assert len(normalized) == 1


def test_strips_punctuation_and_collapses_whitespace() -> None:
    assert normalize_vendor_name("O'Brien & Sons Co.") == "o brien sons"


def test_genuinely_different_vendors_do_not_collapse_together() -> None:
    assert normalize_vendor_name("Acme Corp.") != normalize_vendor_name("Acme Industries Inc.")
