import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from core.matching.duplicates import (
    LINE_OVERLAP_THRESHOLD,
    TOTAL_VARIANCE_PCT,
    InvoiceSummary,
    find_duplicates,
)
from core.models import ExceptionType, Severity, TolerancePolicy

TENANT_ID = uuid.uuid4()
VENDOR_ID = uuid.uuid4()
NOW = datetime(2026, 1, 1, tzinfo=UTC)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64

LINE_ITEMS = [f"Widget model {n}" for n in range(10)]


def make_policy(**overrides: object) -> TolerancePolicy:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        name="default",
        is_active=True,
        version=1,
        price_variance_pct=Decimal("2"),
        qty_tolerance_pct=Decimal("0"),
        auto_approve_below=Decimal("5000"),
        dual_approval_above=Decimal("100000"),
        min_field_confidence=Decimal("0.85"),
        duplicate_window_days=90,
        created_at=NOW,
    )
    fields.update(overrides)
    return TolerancePolicy(**fields)  # type: ignore[arg-type]


def make_summary(**overrides: object) -> InvoiceSummary:
    fields = dict(
        id=uuid.uuid4(),
        vendor_id=VENDOR_ID,
        vendor_name="Acme Corp.",
        content_hash=HASH_A,
        invoice_number="INV-1001",
        invoice_date=date(2026, 1, 1),
        total=Decimal("10000.00"),
        line_descriptions=tuple(LINE_ITEMS),
    )
    fields.update(overrides)
    return InvoiceSummary(**fields)  # type: ignore[arg-type]


POLICY = make_policy()


# --- Tier 1: hard duplicate (identical content_hash) ------------------------


def test_hard_duplicate_identical_content_hash() -> None:
    prior = make_summary(content_hash=HASH_A)
    candidate = make_summary(
        id=uuid.uuid4(), content_hash=HASH_A, invoice_number="INV-9999", total=Decimal("1.00")
    )

    findings = find_duplicates(candidate, [prior], POLICY)

    assert len(findings) == 1
    assert findings[0].exception_type == ExceptionType.DUPLICATE_INVOICE
    assert findings[0].severity == Severity.BLOCK
    assert findings[0].prior_invoice_id == prior.id


def test_no_hard_duplicate_when_content_hash_differs() -> None:
    prior = make_summary(content_hash=HASH_A)
    candidate = make_summary(content_hash=HASH_B, invoice_number="INV-2002", total=Decimal("1.00"))

    findings = find_duplicates(candidate, [prior], POLICY)

    assert findings == ()


# --- Tier 2: exact duplicate (vendor_id + invoice_number) -------------------


def test_exact_duplicate_same_vendor_and_invoice_number() -> None:
    prior = make_summary(content_hash=HASH_A, invoice_number="INV-1001")
    candidate = make_summary(content_hash=HASH_B, invoice_number="INV-1001")

    findings = find_duplicates(candidate, [prior], POLICY)

    assert len(findings) == 1
    assert findings[0].exception_type == ExceptionType.DUPLICATE_INVOICE
    assert findings[0].severity == Severity.BLOCK


def test_exact_duplicate_is_case_insensitive_and_whitespace_stripped() -> None:
    prior = make_summary(content_hash=HASH_A, invoice_number="  inv-1001  ")
    candidate = make_summary(content_hash=HASH_B, invoice_number="INV-1001")

    findings = find_duplicates(candidate, [prior], POLICY)

    assert len(findings) == 1
    assert findings[0].exception_type == ExceptionType.DUPLICATE_INVOICE


def test_exact_duplicate_requires_same_vendor() -> None:
    # Different vendor_id AND different vendor_name, so tier 3's
    # normalized-name comparison can't accidentally pick this up either --
    # this test isolates tier 2's vendor_id requirement.
    prior = make_summary(
        content_hash=HASH_A,
        invoice_number="INV-1001",
        vendor_id=uuid.uuid4(),
        vendor_name="Acme Corp.",
    )
    candidate = make_summary(
        content_hash=HASH_B,
        invoice_number="INV-1001",
        vendor_id=uuid.uuid4(),
        vendor_name="Globex Industries",
    )

    findings = find_duplicates(candidate, [prior], POLICY)

    assert findings == ()


# --- Tier 3: suspected duplicate ---------------------------------------------


def test_suspected_duplicate_all_conditions_met() -> None:
    prior = make_summary(
        content_hash=HASH_A,
        invoice_number="INV-1001",
        total=Decimal("10000.00"),
        invoice_date=date(2026, 1, 1),
        line_descriptions=tuple(LINE_ITEMS),
    )
    candidate = make_summary(
        content_hash=HASH_B,
        invoice_number="INV-1002",
        total=Decimal("10040.00"),
        invoice_date=date(2026, 1, 10),
        line_descriptions=tuple(LINE_ITEMS[:8]),
    )

    findings = find_duplicates(candidate, [prior], POLICY)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.exception_type == ExceptionType.SUSPECTED_DUPLICATE
    assert finding.severity == Severity.WARN
    assert finding.prior_invoice_id == prior.id
    # A reviewer must see which invoice and why -- not just a bare flag.
    assert str(prior.id) in finding.detail
    assert "INV-1001" in finding.detail


def test_suspected_duplicate_matches_across_vendor_name_variants() -> None:
    prior = make_summary(
        content_hash=HASH_A,
        vendor_name="Acme Corp.",
        invoice_number="INV-1001",
    )
    candidate = make_summary(
        content_hash=HASH_B,
        vendor_name="ACME CORPORATION",
        invoice_number="INV-1002",
    )

    findings = find_duplicates(candidate, [prior], POLICY)

    assert len(findings) == 1
    assert findings[0].exception_type == ExceptionType.SUSPECTED_DUPLICATE


def test_total_variance_boundary_exactly_half_percent_triggers() -> None:
    assert TOTAL_VARIANCE_PCT == Decimal("0.005")
    prior = make_summary(content_hash=HASH_A, invoice_number="INV-1001", total=Decimal("10000.00"))
    candidate = make_summary(
        content_hash=HASH_B, invoice_number="INV-1002", total=Decimal("10050.00")
    )

    findings = find_duplicates(candidate, [prior], POLICY)

    assert len(findings) == 1
    assert findings[0].exception_type == ExceptionType.SUSPECTED_DUPLICATE


def test_total_variance_just_over_half_percent_does_not_trigger() -> None:
    prior = make_summary(content_hash=HASH_A, invoice_number="INV-1001", total=Decimal("10000.00"))
    candidate = make_summary(
        content_hash=HASH_B, invoice_number="INV-1002", total=Decimal("10051.00")
    )

    findings = find_duplicates(candidate, [prior], POLICY)

    assert findings == ()


def test_line_overlap_boundary_exactly_seventy_percent_triggers() -> None:
    assert LINE_OVERLAP_THRESHOLD == Decimal("0.70")
    prior = make_summary(
        content_hash=HASH_A, invoice_number="INV-1001", line_descriptions=tuple(LINE_ITEMS)
    )
    # 7 of 10 candidate lines match a prior line exactly -- exactly 70%.
    candidate_lines = tuple(LINE_ITEMS[:7]) + (
        "Totally unrelated part A",
        "Totally unrelated part B",
        "Totally unrelated part C",
    )
    candidate = make_summary(
        content_hash=HASH_B,
        invoice_number="INV-1002",
        line_descriptions=candidate_lines,
    )

    findings = find_duplicates(candidate, [prior], POLICY)

    assert len(findings) == 1
    assert findings[0].exception_type == ExceptionType.SUSPECTED_DUPLICATE


def test_line_overlap_just_under_seventy_percent_does_not_trigger() -> None:
    prior = make_summary(
        content_hash=HASH_A, invoice_number="INV-1001", line_descriptions=tuple(LINE_ITEMS)
    )
    # 6 of 10 candidate lines match -- 60%, below the 70% bar.
    candidate_lines = tuple(LINE_ITEMS[:6]) + (
        "Totally unrelated part A",
        "Totally unrelated part B",
        "Totally unrelated part C",
        "Totally unrelated part D",
    )
    candidate = make_summary(
        content_hash=HASH_B,
        invoice_number="INV-1002",
        line_descriptions=candidate_lines,
    )

    findings = find_duplicates(candidate, [prior], POLICY)

    assert findings == ()


def test_invoice_date_exactly_at_duplicate_window_boundary_triggers() -> None:
    policy = make_policy(duplicate_window_days=30)
    prior = make_summary(
        content_hash=HASH_A, invoice_number="INV-1001", invoice_date=date(2026, 1, 1)
    )
    candidate = make_summary(
        content_hash=HASH_B,
        invoice_number="INV-1002",
        invoice_date=date(2026, 1, 31),  # exactly 30 days apart
        total=Decimal("10000.00"),
    )

    findings = find_duplicates(candidate, [prior], policy)

    assert len(findings) == 1
    assert findings[0].exception_type == ExceptionType.SUSPECTED_DUPLICATE


def test_invoice_date_outside_duplicate_window_does_not_trigger() -> None:
    policy = make_policy(duplicate_window_days=30)
    prior = make_summary(
        content_hash=HASH_A, invoice_number="INV-1001", invoice_date=date(2026, 1, 1)
    )
    candidate = make_summary(
        content_hash=HASH_B,
        invoice_number="INV-1002",
        invoice_date=date(2026, 3, 1),
        total=Decimal("10000.00"),
    )

    findings = find_duplicates(candidate, [prior], policy)

    assert findings == ()


# --- No false positives ------------------------------------------------------


def test_genuinely_different_invoice_same_vendor_same_day_is_not_flagged() -> None:
    prior = make_summary(
        content_hash=HASH_A,
        invoice_number="INV-1001",
        invoice_date=date(2026, 1, 1),
        total=Decimal("10000.00"),
        line_descriptions=("Consulting services, January", "Travel reimbursement"),
    )
    candidate = make_summary(
        content_hash=HASH_B,
        invoice_number="INV-1002",
        invoice_date=date(2026, 1, 1),
        total=Decimal("450.00"),
        line_descriptions=("Office chair", "Standing desk"),
    )

    findings = find_duplicates(candidate, [prior], POLICY)

    assert findings == ()


def test_ignores_self_when_candidate_id_matches_a_prior_entry() -> None:
    shared_id = uuid.uuid4()
    prior = make_summary(id=shared_id, content_hash=HASH_A)
    candidate = make_summary(id=shared_id, content_hash=HASH_A)

    findings = find_duplicates(candidate, [prior], POLICY)

    assert findings == ()


def test_multiple_prior_invoices_each_produce_their_own_finding() -> None:
    exact_prior = make_summary(content_hash=HASH_A, invoice_number="INV-1001")
    hard_prior = make_summary(content_hash=HASH_C, invoice_number="INV-3003")
    candidate = make_summary(content_hash=HASH_C, invoice_number="INV-1001")

    findings = find_duplicates(candidate, [exact_prior, hard_prior], POLICY)

    assert len(findings) == 2
    types = {f.exception_type for f in findings}
    assert types == {ExceptionType.DUPLICATE_INVOICE}


def test_empty_prior_invoices_yields_no_findings() -> None:
    candidate = make_summary()
    assert find_duplicates(candidate, [], POLICY) == ()
