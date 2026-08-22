import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from core.decision import Decision, decide
from core.errors import MatchingError
from core.matching.three_way import MatchFinding, ThreeWayMatchResult
from core.models import ExceptionType, Invoice, InvoiceStatus, Severity, TolerancePolicy

TENANT_ID = uuid.uuid4()
INVOICE_ID = uuid.uuid4()
NOW = datetime(2026, 1, 1, tzinfo=UTC)

MIN_FIELD_CONFIDENCE = Decimal("0.85")
AUTO_APPROVE_BELOW = Decimal("5000")
DUAL_APPROVAL_ABOVE = Decimal("100000")


def make_policy(**overrides: object) -> TolerancePolicy:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        name="default",
        is_active=True,
        version=1,
        price_variance_pct=Decimal("2"),
        qty_tolerance_pct=Decimal("0"),
        auto_approve_below=AUTO_APPROVE_BELOW,
        dual_approval_above=DUAL_APPROVAL_ABOVE,
        min_field_confidence=MIN_FIELD_CONFIDENCE,
        duplicate_window_days=90,
        created_at=NOW,
    )
    fields.update(overrides)
    return TolerancePolicy(**fields)  # type: ignore[arg-type]


def make_invoice(total: Decimal, **overrides: object) -> Invoice:
    fields = dict(
        id=INVOICE_ID,
        tenant_id=TENANT_ID,
        currency="USD",
        source_channel="upload",
        source_file_path="invoices/1.pdf",
        content_hash="a" * 64,
        status=InvoiceStatus.MATCHING,
        created_at=NOW,
        updated_at=NOW,
        subtotal=total,
        tax=Decimal("0"),
        total=total,
    )
    fields.update(overrides)
    return Invoice(**fields)  # type: ignore[arg-type]


def make_finding(severity: Severity) -> MatchFinding:
    exception_type = {
        Severity.INFO: ExceptionType.QTY_SHORT,
        Severity.WARN: ExceptionType.TAX_MISMATCH,
        Severity.BLOCK: ExceptionType.UNMATCHED_LINE,
    }[severity]
    return MatchFinding(exception_type=exception_type, severity=severity, detail="test finding")


def make_result(
    outcome: str, findings: tuple[MatchFinding, ...] = ()
) -> ThreeWayMatchResult:
    return ThreeWayMatchResult(
        invoice_id=INVOICE_ID,
        result=outcome,  # type: ignore[arg-type]
        findings=findings,
        line_matches=None,
        stage_timings_ms={},
    )


CLEAN = make_result("clean")
WARN_ONLY = make_result("exceptions", (make_finding(Severity.WARN),))
ANY_BLOCK = make_result("blocked", (make_finding(Severity.BLOCK),))

HIGH_CONF = Decimal("0.95")
LOW_CONF = Decimal("0.50")


# --- Matrix: no exceptions, high confidence ---------------------------------


def test_clean_high_confidence_below_auto_approve_is_auto_post() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("3200"))

    decision = decide(CLEAN, HIGH_CONF, invoice, policy)

    assert decision.outcome == "AUTO_POST"
    assert decision.required_approvers == 0


def test_clean_high_confidence_at_or_above_auto_approve_is_pending_approval() -> None:
    policy = make_policy()
    invoice = make_invoice(total=AUTO_APPROVE_BELOW)  # exactly at threshold, not below it

    decision = decide(CLEAN, HIGH_CONF, invoice, policy)

    assert decision.outcome == "PENDING_APPROVAL"
    assert decision.required_approvers == 1


# --- Matrix: no exceptions, low confidence -----------------------------------


def test_clean_low_confidence_is_needs_verification() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("100"))  # would otherwise auto-post

    decision = decide(CLEAN, LOW_CONF, invoice, policy)

    assert decision.outcome == "NEEDS_VERIFICATION"
    assert decision.required_approvers == 0


def test_clean_no_recorded_confidence_is_needs_verification() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("100"))

    decision = decide(CLEAN, None, invoice, policy)

    assert decision.outcome == "NEEDS_VERIFICATION"
    assert "no field confidence" in decision.reason


# --- Matrix: warn only ------------------------------------------------------


def test_warn_only_high_confidence_is_pending_approval() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("100"))

    decision = decide(WARN_ONLY, HIGH_CONF, invoice, policy)

    assert decision.outcome == "PENDING_APPROVAL"
    assert decision.required_approvers == 1


def test_warn_only_low_confidence_is_needs_verification() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("100"))

    decision = decide(WARN_ONLY, LOW_CONF, invoice, policy)

    assert decision.outcome == "NEEDS_VERIFICATION"


# --- Matrix: any block -------------------------------------------------------


def test_any_block_high_confidence_below_dual_threshold_is_one_approver() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("100"))

    decision = decide(ANY_BLOCK, HIGH_CONF, invoice, policy)

    assert decision.outcome == "PENDING_APPROVAL"
    assert decision.required_approvers == 1


def test_any_block_high_confidence_above_dual_threshold_is_two_approvers() -> None:
    policy = make_policy()
    invoice = make_invoice(total=DUAL_APPROVAL_ABOVE + Decimal("0.01"))

    decision = decide(ANY_BLOCK, HIGH_CONF, invoice, policy)

    assert decision.outcome == "PENDING_APPROVAL"
    assert decision.required_approvers == 2


def test_any_block_low_confidence_is_needs_verification() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("100"))

    decision = decide(ANY_BLOCK, LOW_CONF, invoice, policy)

    assert decision.outcome == "NEEDS_VERIFICATION"


# --- Confidence override: always beats a clean match ------------------------


def test_low_confidence_overrides_an_otherwise_auto_postable_invoice() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("1"))  # trivially small, would auto-post

    decision = decide(CLEAN, LOW_CONF, invoice, policy)

    assert decision.outcome == "NEEDS_VERIFICATION"


def test_confidence_exactly_at_threshold_is_not_low() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("100"))

    decision = decide(CLEAN, MIN_FIELD_CONFIDENCE, invoice, policy)

    assert decision.outcome == "AUTO_POST"


def test_confidence_just_under_threshold_is_low() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("100"))

    decision = decide(CLEAN, MIN_FIELD_CONFIDENCE - Decimal("0.01"), invoice, policy)

    assert decision.outcome == "NEEDS_VERIFICATION"


# --- Dual approval threshold boundary ---------------------------------------


def test_dual_approval_boundary_exactly_at_threshold_is_one_approver() -> None:
    policy = make_policy()
    invoice = make_invoice(total=DUAL_APPROVAL_ABOVE)

    decision = decide(ANY_BLOCK, HIGH_CONF, invoice, policy)

    assert decision.required_approvers == 1


def test_dual_approval_boundary_just_over_threshold_is_two_approvers() -> None:
    policy = make_policy()
    invoice = make_invoice(total=DUAL_APPROVAL_ABOVE + Decimal("0.01"))

    decision = decide(ANY_BLOCK, HIGH_CONF, invoice, policy)

    assert decision.required_approvers == 2


def test_dual_approval_applies_to_a_large_clean_invoice_too() -> None:
    # Not just the "any block" row -- a large clean invoice above the
    # dual-approval threshold is exactly as financially risky.
    policy = make_policy()
    invoice = make_invoice(total=DUAL_APPROVAL_ABOVE + Decimal("0.01"))

    decision = decide(CLEAN, HIGH_CONF, invoice, policy)

    assert decision.outcome == "PENDING_APPROVAL"
    assert decision.required_approvers == 2


# --- Reason string generation ------------------------------------------------


def test_auto_post_reason_names_exceptions_confidence_and_threshold() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("3200"))

    decision = decide(CLEAN, HIGH_CONF, invoice, policy)

    assert "0 exceptions" in decision.reason
    assert "0.95" in decision.reason
    assert "3200" in decision.reason
    assert "5000" in decision.reason
    assert "below" in decision.reason


def test_needs_verification_reason_names_the_confidence_and_threshold() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("100"))

    decision = decide(CLEAN, LOW_CONF, invoice, policy)

    assert "0.50" in decision.reason
    assert "0.85" in decision.reason


def test_pending_approval_with_block_reason_mentions_exception_count() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("100"))
    result = make_result("blocked", (make_finding(Severity.BLOCK), make_finding(Severity.BLOCK)))

    decision = decide(result, HIGH_CONF, invoice, policy)

    assert "2" in decision.reason
    assert "blocking" in decision.reason


def test_pending_approval_dual_approver_reason_mentions_the_threshold() -> None:
    policy = make_policy()
    invoice = make_invoice(total=DUAL_APPROVAL_ABOVE + Decimal("0.01"))

    decision = decide(ANY_BLOCK, HIGH_CONF, invoice, policy)

    assert "2 approver" in decision.reason
    assert str(DUAL_APPROVAL_ABOVE) in decision.reason


# --- Input validation / Decision invariants ----------------------------------


def test_decide_raises_when_invoice_total_is_missing() -> None:
    policy = make_policy()
    invoice = make_invoice(total=Decimal("100"))
    # Invoice.total is only None pre-extraction; decide() must reject that
    # rather than silently comparing against nothing. Bypass the frozen
    # dataclass to simulate it without hand-rolling every other field.
    object.__setattr__(invoice, "total", None)

    with pytest.raises(MatchingError):
        decide(CLEAN, HIGH_CONF, invoice, policy)


def test_decision_rejects_non_pending_outcome_with_approvers() -> None:
    with pytest.raises(MatchingError):
        Decision(outcome="AUTO_POST", reason="bad", required_approvers=1)


def test_decision_rejects_blank_reason() -> None:
    with pytest.raises(MatchingError):
        Decision(outcome="AUTO_POST", reason="   ", required_approvers=0)


def test_decision_rejects_unrecognized_outcome() -> None:
    with pytest.raises(MatchingError):
        Decision(outcome="BOGUS", reason="x", required_approvers=0)  # type: ignore[arg-type]
