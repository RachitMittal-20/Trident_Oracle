import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from core.errors import MatchingError
from core.matching.duplicates import InvoiceSummary
from core.matching.three_way import run_three_way_match
from core.models import (
    ExceptionType,
    GoodsReceipt,
    GoodsReceiptLine,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    PurchaseOrder,
    PurchaseOrderLine,
    Severity,
    TolerancePolicy,
    Vendor,
)

TENANT_ID = uuid.uuid4()
VENDOR_ID = uuid.uuid4()
PO_ID = uuid.uuid4()
GRN_ID = uuid.uuid4()
INVOICE_ID = uuid.uuid4()

PO_ISSUED = datetime(2026, 1, 1, tzinfo=UTC)
INVOICE_DATE = date(2026, 1, 5)
TODAY = date(2026, 1, 15)

HASH_A = "a" * 64
HASH_B = "b" * 64


def make_vendor(**overrides: object) -> Vendor:
    fields = dict(
        id=VENDOR_ID,
        tenant_id=TENANT_ID,
        name="Acme Corp.",
        normalized_name="acme",
        created_at=PO_ISSUED,
    )
    fields.update(overrides)
    return Vendor(**fields)  # type: ignore[arg-type]


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
        created_at=PO_ISSUED,
    )
    fields.update(overrides)
    return TolerancePolicy(**fields)  # type: ignore[arg-type]


def make_po(**overrides: object) -> PurchaseOrder:
    fields = dict(
        id=PO_ID,
        tenant_id=TENANT_ID,
        vendor_id=VENDOR_ID,
        po_number="PO-1",
        issued_at=PO_ISSUED,
        currency="USD",
        subtotal=Decimal("50.00"),
        tax=Decimal("0.00"),
        total=Decimal("50.00"),
        status="open",
        created_at=PO_ISSUED,
    )
    fields.update(overrides)
    return PurchaseOrder(**fields)  # type: ignore[arg-type]


def make_po_line(**overrides: object) -> PurchaseOrderLine:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        po_id=PO_ID,
        line_no=1,
        description="Widget",
        normalized_description="widget",
        qty_ordered=Decimal("10"),
        unit_price=Decimal("5.00"),
        tax_rate=Decimal("0"),
        line_total=Decimal("50.00"),
        created_at=PO_ISSUED,
        sku="WID-1",
    )
    fields.update(overrides)
    return PurchaseOrderLine(**fields)  # type: ignore[arg-type]


def make_grn(**overrides: object) -> GoodsReceipt:
    fields = dict(
        id=GRN_ID,
        tenant_id=TENANT_ID,
        po_id=PO_ID,
        grn_number="GRN-1",
        received_at=PO_ISSUED,
        received_by=uuid.uuid4(),
        created_at=PO_ISSUED,
    )
    fields.update(overrides)
    return GoodsReceipt(**fields)  # type: ignore[arg-type]


def make_grn_line(**overrides: object) -> GoodsReceiptLine:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        grn_id=GRN_ID,
        qty_received=Decimal("10"),
        condition="good",
        created_at=PO_ISSUED,
    )
    fields.update(overrides)
    return GoodsReceiptLine(**fields)  # type: ignore[arg-type]


def make_invoice(**overrides: object) -> Invoice:
    fields = dict(
        id=INVOICE_ID,
        tenant_id=TENANT_ID,
        currency="USD",
        source_channel="upload",
        source_file_path="invoices/1.pdf",
        content_hash=HASH_A,
        status=InvoiceStatus.EXTRACTED,
        created_at=PO_ISSUED,
        updated_at=PO_ISSUED,
        invoice_number="INV-1",
        invoice_date=INVOICE_DATE,
        subtotal=Decimal("50.00"),
        tax=Decimal("0.00"),
        total=Decimal("50.00"),
        vendor_id=VENDOR_ID,
        po_id=PO_ID,
    )
    fields.update(overrides)
    return Invoice(**fields)  # type: ignore[arg-type]


def make_invoice_line(**overrides: object) -> InvoiceLine:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        invoice_id=INVOICE_ID,
        line_no=1,
        description="WID-1 replacement part",
        qty=Decimal("10"),
        unit_price=Decimal("5.00"),
        line_total=Decimal("50.00"),
        created_at=PO_ISSUED,
    )
    fields.update(overrides)
    return InvoiceLine(**fields)  # type: ignore[arg-type]


@dataclass
class Scenario:
    vendor: Vendor
    po: PurchaseOrder
    po_line: PurchaseOrderLine
    grn: GoodsReceipt
    grn_line: GoodsReceiptLine
    invoice: Invoice
    invoice_line: InvoiceLine
    policy: TolerancePolicy


def baseline() -> Scenario:
    """A fully reconciling invoice: SKU-matched line, price and quantity
    exactly matching the PO/GRN, arithmetic exact, tax rate 0% (an expected
    slab), dated between the PO and today. Every test starts here and
    overrides only what it means to test."""
    po_line = make_po_line()
    grn_line = make_grn_line(po_line_id=po_line.id, qty_received=Decimal("10"), condition="good")
    invoice_line = make_invoice_line()
    return Scenario(
        vendor=make_vendor(),
        po=make_po(),
        po_line=po_line,
        grn=make_grn(),
        grn_line=grn_line,
        invoice=make_invoice(),
        invoice_line=invoice_line,
        policy=make_policy(),
    )


def run(s: Scenario, **kwargs: object) -> object:
    defaults = dict(
        invoice=s.invoice,
        invoice_lines=[s.invoice_line],
        vendor=s.vendor,
        po=s.po,
        po_lines=[s.po_line],
        grn=s.grn,
        grn_lines=[s.grn_line],
        policy=s.policy,
        recent_invoices=(),
        today=TODAY,
    )
    defaults.update(kwargs)
    return run_three_way_match(**defaults)  # type: ignore[arg-type]


# --- Fully clean ---------------------------------------------------------


def test_fully_clean_invoice_produces_zero_exceptions() -> None:
    s = baseline()
    result = run(s)

    assert result.result == "clean"
    assert result.findings == ()
    assert result.line_matches is not None
    assert result.line_matches.unmatched_invoice_line_ids == ()


# --- Stage 1: duplicates, hard/exact short-circuit ------------------------


def test_hard_duplicate_short_circuits_before_any_further_stage() -> None:
    s = baseline()
    prior = InvoiceSummary(
        id=uuid.uuid4(),
        vendor_id=s.vendor.id,
        vendor_name=s.vendor.name,
        content_hash=s.invoice.content_hash,
        invoice_number="INV-OTHER",
        invoice_date=date(2025, 1, 1),
        total=Decimal("1.00"),
    )

    result = run(s, recent_invoices=[prior])

    assert result.result == "blocked"
    assert len(result.findings) == 1
    assert result.findings[0].exception_type == ExceptionType.DUPLICATE_INVOICE
    assert result.findings[0].severity == Severity.BLOCK
    assert result.line_matches is None
    assert set(result.stage_timings_ms.keys()) == {"duplicates"}


def test_exact_duplicate_also_short_circuits() -> None:
    s = baseline()
    prior = InvoiceSummary(
        id=uuid.uuid4(),
        vendor_id=s.vendor.id,
        vendor_name=s.vendor.name,
        content_hash=HASH_B,
        invoice_number=s.invoice.invoice_number,
        invoice_date=date(2025, 1, 1),
        total=Decimal("1.00"),
    )

    result = run(s, recent_invoices=[prior])

    assert result.result == "blocked"
    assert result.findings[0].exception_type == ExceptionType.DUPLICATE_INVOICE
    assert result.line_matches is None


def test_suspected_duplicate_does_not_short_circuit() -> None:
    s = baseline()
    prior = InvoiceSummary(
        id=uuid.uuid4(),
        vendor_id=s.vendor.id,
        vendor_name=s.vendor.name,
        content_hash=HASH_B,
        invoice_number="INV-OTHER",
        invoice_date=s.invoice.invoice_date,
        total=s.invoice.total,
        line_descriptions=(s.invoice_line.description,),
    )

    result = run(s, recent_invoices=[prior])

    types = {f.exception_type for f in result.findings}
    assert ExceptionType.SUSPECTED_DUPLICATE in types
    assert result.result == "exceptions"
    # Processing continued past stage 1 -- line matching actually ran.
    assert result.line_matches is not None
    assert "line_matching" in result.stage_timings_ms


# --- Stage 2: linkage -------------------------------------------------------


def test_no_po_blocks_and_skips_all_further_stages() -> None:
    s = baseline()

    result = run(s, po=None, po_lines=[])

    assert result.result == "blocked"
    assert len(result.findings) == 1
    assert result.findings[0].exception_type == ExceptionType.NO_PO
    assert result.line_matches is None
    assert "line_matching" not in result.stage_timings_ms


def test_no_po_and_no_grn_detail_says_cannot_match_at_all() -> None:
    s = baseline()

    result = run(s, po=None, po_lines=[], grn=None, grn_lines=[])

    types = {f.exception_type for f in result.findings}
    assert types == {ExceptionType.NO_PO, ExceptionType.NO_GRN}
    for finding in result.findings:
        assert "cannot be matched at all" in finding.detail


def test_no_grn_alone_still_runs_line_matching_and_price_but_skips_quantity() -> None:
    s = baseline()
    # Grossly mismatched quantity that would be a screaming QTY_OVER if a
    # GRN were present -- must NOT surface without one to check against.
    s.invoice_line = make_invoice_line(qty=Decimal("999"), line_total=Decimal("4995.00"))
    s.invoice = make_invoice(subtotal=Decimal("4995.00"), total=Decimal("4995.00"))

    result = run(s, grn=None, grn_lines=[])

    types = [f.exception_type for f in result.findings]
    assert ExceptionType.NO_GRN in types
    assert ExceptionType.QTY_OVER not in types
    assert ExceptionType.QTY_SHORT not in types
    assert result.line_matches is not None
    assert result.line_matches.unmatched_invoice_line_ids == ()


# --- Stage 4a: QUANTITY -----------------------------------------------------


def test_qty_over_grn_received_beyond_tolerance_is_block() -> None:
    s = baseline()
    s.grn_line = make_grn_line(po_line_id=s.po_line.id, qty_received=Decimal("8"), condition="good")

    result = run(s)

    findings = [f for f in result.findings if f.exception_type == ExceptionType.QTY_OVER]
    assert len(findings) == 1
    assert findings[0].severity == Severity.BLOCK
    assert findings[0].expected_value == Decimal("8")
    assert findings[0].actual_value == Decimal("10")
    assert result.result == "blocked"


def test_qty_short_beyond_tolerance_is_info() -> None:
    s = baseline()
    s.grn_line = make_grn_line(
        po_line_id=s.po_line.id, qty_received=Decimal("12"), condition="good"
    )

    result = run(s)

    findings = [f for f in result.findings if f.exception_type == ExceptionType.QTY_SHORT]
    assert len(findings) == 1
    assert findings[0].severity == Severity.INFO
    assert result.result == "exceptions"


def test_qty_tolerance_boundary_exactly_at_is_not_flagged() -> None:
    s = baseline()
    s.policy = make_policy(qty_tolerance_pct=Decimal("20"))
    s.grn_line = make_grn_line(
        po_line_id=s.po_line.id, qty_received=Decimal("10"), condition="good"
    )
    s.invoice_line = make_invoice_line(qty=Decimal("12"), line_total=Decimal("60.00"))
    s.invoice = make_invoice(subtotal=Decimal("60.00"), total=Decimal("60.00"))

    result = run(s)

    types = [f.exception_type for f in result.findings]
    assert ExceptionType.QTY_OVER not in types
    assert ExceptionType.QTY_SHORT not in types


def test_qty_tolerance_boundary_just_under_is_not_flagged() -> None:
    s = baseline()
    s.policy = make_policy(qty_tolerance_pct=Decimal("20"))
    s.grn_line = make_grn_line(
        po_line_id=s.po_line.id, qty_received=Decimal("10"), condition="good"
    )
    s.invoice_line = make_invoice_line(qty=Decimal("11.9"), line_total=Decimal("59.50"))
    s.invoice = make_invoice(subtotal=Decimal("59.50"), total=Decimal("59.50"))

    result = run(s)

    types = [f.exception_type for f in result.findings]
    assert ExceptionType.QTY_OVER not in types
    assert ExceptionType.QTY_SHORT not in types


def test_qty_tolerance_boundary_just_over_is_flagged() -> None:
    s = baseline()
    s.policy = make_policy(qty_tolerance_pct=Decimal("20"))
    s.grn_line = make_grn_line(
        po_line_id=s.po_line.id, qty_received=Decimal("10"), condition="good"
    )
    s.invoice_line = make_invoice_line(qty=Decimal("12.1"), line_total=Decimal("60.50"))
    s.invoice = make_invoice(subtotal=Decimal("60.50"), total=Decimal("60.50"))

    result = run(s)

    types = [f.exception_type for f in result.findings]
    assert ExceptionType.QTY_OVER in types


def test_qty_over_when_grn_received_nothing_for_this_line() -> None:
    # received_qty == 0 exercises the division-by-zero guard in
    # _qty_finding's delta_pct calc (100% over by definition, not a ZeroDivisionError).
    s = baseline()
    other_po_line = make_po_line(id=uuid.uuid4())
    s.grn_line = make_grn_line(
        po_line_id=other_po_line.id, qty_received=Decimal("5"), condition="good"
    )

    result = run(s)

    findings = [f for f in result.findings if f.exception_type == ExceptionType.QTY_OVER]
    assert len(findings) == 1
    assert findings[0].expected_value == Decimal("0")
    assert findings[0].delta_pct == Decimal("100")
    assert result.result == "blocked"


def test_damaged_receipt_lines_excluded_from_qty_received() -> None:
    s = baseline()
    damaged = make_grn_line(
        po_line_id=s.po_line.id, qty_received=Decimal("10"), condition="damaged"
    )
    good = make_grn_line(po_line_id=s.po_line.id, qty_received=Decimal("8"), condition="good")
    s.invoice_line = make_invoice_line(qty=Decimal("8"), line_total=Decimal("40.00"))
    s.invoice = make_invoice(subtotal=Decimal("40.00"), total=Decimal("40.00"))

    result = run_three_way_match(
        invoice=s.invoice,
        invoice_lines=[s.invoice_line],
        vendor=s.vendor,
        po=s.po,
        po_lines=[s.po_line],
        grn=s.grn,
        grn_lines=[damaged, good],
        policy=s.policy,
        recent_invoices=(),
        today=TODAY,
    )

    # Received (excluding damaged) is 8, invoice bills 8 -- exact match. If
    # the damaged 10 had counted, this would register as a huge QTY_SHORT.
    types = [f.exception_type for f in result.findings]
    assert ExceptionType.QTY_OVER not in types
    assert ExceptionType.QTY_SHORT not in types


# --- Stage 4b: PRICE ---------------------------------------------------------


def _price_scenario(unit_price: Decimal) -> Scenario:
    s = baseline()
    s.po_line = make_po_line(
        qty_ordered=Decimal("1"), unit_price=Decimal("100.00"), line_total=Decimal("100.00")
    )
    s.grn_line = make_grn_line(po_line_id=s.po_line.id, qty_received=Decimal("1"), condition="good")
    s.invoice_line = make_invoice_line(
        qty=Decimal("1"), unit_price=unit_price, line_total=unit_price
    )
    s.invoice = make_invoice(subtotal=unit_price, total=unit_price)
    s.policy = make_policy(price_variance_pct=Decimal("2"))
    return s


def test_price_variance_at_tolerance_boundary_is_info() -> None:
    s = _price_scenario(Decimal("102.00"))  # exactly 2% over

    result = run(s)

    findings = [f for f in result.findings if f.exception_type == ExceptionType.PRICE_VARIANCE]
    assert len(findings) == 1
    assert findings[0].severity == Severity.INFO
    assert result.result == "exceptions"


def test_price_variance_just_over_tolerance_is_warn() -> None:
    s = _price_scenario(Decimal("102.50"))  # 2.5%, over 2% tolerance

    result = run(s)

    findings = [f for f in result.findings if f.exception_type == ExceptionType.PRICE_VARIANCE]
    assert findings[0].severity == Severity.WARN


def test_price_variance_at_double_tolerance_boundary_is_warn() -> None:
    s = _price_scenario(Decimal("104.00"))  # exactly 4%, exactly 2x tolerance

    result = run(s)

    findings = [f for f in result.findings if f.exception_type == ExceptionType.PRICE_VARIANCE]
    assert findings[0].severity == Severity.WARN


def test_price_variance_beyond_double_tolerance_is_block() -> None:
    s = _price_scenario(Decimal("104.50"))  # 4.5%, over 2x tolerance

    result = run(s)

    findings = [f for f in result.findings if f.exception_type == ExceptionType.PRICE_VARIANCE]
    assert findings[0].severity == Severity.BLOCK
    assert result.result == "blocked"


def test_price_variance_records_delta_and_delta_pct() -> None:
    s = _price_scenario(Decimal("110.00"))

    result = run(s)

    finding = next(f for f in result.findings if f.exception_type == ExceptionType.PRICE_VARIANCE)
    assert finding.expected_value == Decimal("100.00")
    assert finding.actual_value == Decimal("110.00")
    assert finding.delta == Decimal("10.00")
    assert finding.delta_pct == Decimal("10.00")


def test_price_variance_when_po_line_unit_price_is_zero() -> None:
    # expected == 0 exercises the division-by-zero guard in _price_finding's
    # delta_pct calc (100% variance by definition, not a ZeroDivisionError).
    s = baseline()
    s.po_line = make_po_line(
        qty_ordered=Decimal("1"), unit_price=Decimal("0.00"), line_total=Decimal("0.00")
    )
    s.grn_line = make_grn_line(po_line_id=s.po_line.id, qty_received=Decimal("1"), condition="good")
    s.invoice_line = make_invoice_line(
        qty=Decimal("1"), unit_price=Decimal("5.00"), line_total=Decimal("5.00")
    )
    s.invoice = make_invoice(subtotal=Decimal("5.00"), total=Decimal("5.00"))

    result = run(s)

    findings = [f for f in result.findings if f.exception_type == ExceptionType.PRICE_VARIANCE]
    assert len(findings) == 1
    assert findings[0].delta_pct == Decimal("100")
    assert findings[0].severity == Severity.BLOCK


def test_exact_price_match_produces_no_finding() -> None:
    s = _price_scenario(Decimal("100.00"))

    result = run(s)

    types = [f.exception_type for f in result.findings]
    assert ExceptionType.PRICE_VARIANCE not in types


# --- Stage 5: unmatched lines ------------------------------------------------


def test_unmatched_invoice_line_is_block() -> None:
    s = baseline()
    extra = make_invoice_line(
        line_no=2,
        description="Completely unrelated gadget XYZ",
        qty=Decimal("1"),
        unit_price=Decimal("10.00"),
        line_total=Decimal("10.00"),
    )
    s.invoice = make_invoice(subtotal=Decimal("60.00"), total=Decimal("60.00"))

    result = run(s, invoice_lines=[s.invoice_line, extra])

    findings = [f for f in result.findings if f.exception_type == ExceptionType.UNMATCHED_LINE]
    assert len(findings) == 1
    assert findings[0].severity == Severity.BLOCK
    assert findings[0].invoice_line_id == extra.id
    assert extra.id in result.line_matches.unmatched_invoice_line_ids
    assert result.result == "blocked"


# --- Stage 6: document arithmetic -------------------------------------------


def test_arithmetic_error_caught_on_an_otherwise_clean_looking_invoice() -> None:
    s = baseline()
    # subtotal + tax == total holds (Invoice's own invariant), and price/qty
    # match exactly -- but the line items don't actually sum to the subtotal.
    s.invoice = make_invoice(subtotal=Decimal("55.00"), tax=Decimal("0.00"), total=Decimal("55.00"))

    result = run(s)

    findings = [f for f in result.findings if f.exception_type == ExceptionType.ARITHMETIC_ERROR]
    assert len(findings) == 1
    assert findings[0].severity == Severity.BLOCK
    assert findings[0].expected_value == Decimal("55.00")
    assert findings[0].actual_value == Decimal("50.00")
    assert result.result == "blocked"


# --- Stage 7: TAX_MISMATCH and DATE_ANOMALY ---------------------------------


def test_tax_mismatch_on_implausible_effective_rate() -> None:
    s = baseline()
    s.invoice = make_invoice(
        subtotal=Decimal("100.00"), tax=Decimal("7.00"), total=Decimal("107.00")
    )
    s.po_line = make_po_line(
        qty_ordered=Decimal("10"), unit_price=Decimal("10.00"), line_total=Decimal("100.00")
    )
    s.grn_line = make_grn_line(
        po_line_id=s.po_line.id, qty_received=Decimal("10"), condition="good"
    )
    s.invoice_line = make_invoice_line(
        qty=Decimal("10"), unit_price=Decimal("10.00"), line_total=Decimal("100.00")
    )

    result = run(s)

    findings = [f for f in result.findings if f.exception_type == ExceptionType.TAX_MISMATCH]
    assert len(findings) == 1
    assert findings[0].severity == Severity.WARN


def test_no_tax_mismatch_for_an_expected_slab_rate() -> None:
    s = baseline()
    s.invoice = make_invoice(
        subtotal=Decimal("100.00"), tax=Decimal("18.00"), total=Decimal("118.00")
    )
    s.po_line = make_po_line(
        qty_ordered=Decimal("10"), unit_price=Decimal("10.00"), line_total=Decimal("100.00")
    )
    s.grn_line = make_grn_line(
        po_line_id=s.po_line.id, qty_received=Decimal("10"), condition="good"
    )
    s.invoice_line = make_invoice_line(
        qty=Decimal("10"), unit_price=Decimal("10.00"), line_total=Decimal("100.00")
    )

    result = run(s)

    types = [f.exception_type for f in result.findings]
    assert ExceptionType.TAX_MISMATCH not in types


def test_tax_rate_exactly_at_epsilon_boundary_is_not_flagged() -> None:
    # effective rate 18.5% is exactly DEFAULT_TAX_RATE_EPSILON_PCT (0.5pp)
    # away from the 18% slab -- must still count as a match, not a mismatch.
    s = baseline()
    s.invoice = make_invoice(
        subtotal=Decimal("100.00"), tax=Decimal("18.50"), total=Decimal("118.50")
    )
    s.po_line = make_po_line(
        qty_ordered=Decimal("10"), unit_price=Decimal("10.00"), line_total=Decimal("100.00")
    )
    s.grn_line = make_grn_line(
        po_line_id=s.po_line.id, qty_received=Decimal("10"), condition="good"
    )
    s.invoice_line = make_invoice_line(
        qty=Decimal("10"), unit_price=Decimal("10.00"), line_total=Decimal("100.00")
    )

    result = run(s)

    types = [f.exception_type for f in result.findings]
    assert ExceptionType.TAX_MISMATCH not in types


def test_tax_rate_just_over_epsilon_boundary_is_flagged() -> None:
    s = baseline()
    s.invoice = make_invoice(
        subtotal=Decimal("100.00"), tax=Decimal("18.51"), total=Decimal("118.51")
    )
    s.po_line = make_po_line(
        qty_ordered=Decimal("10"), unit_price=Decimal("10.00"), line_total=Decimal("100.00")
    )
    s.grn_line = make_grn_line(
        po_line_id=s.po_line.id, qty_received=Decimal("10"), condition="good"
    )
    s.invoice_line = make_invoice_line(
        qty=Decimal("10"), unit_price=Decimal("10.00"), line_total=Decimal("100.00")
    )

    result = run(s)

    types = [f.exception_type for f in result.findings]
    assert ExceptionType.TAX_MISMATCH in types


def test_date_anomaly_invoice_precedes_po() -> None:
    s = baseline()
    s.po = make_po(issued_at=datetime(2026, 1, 10, tzinfo=UTC))
    s.invoice = make_invoice(invoice_date=date(2026, 1, 5))

    result = run(s, today=date(2026, 1, 20))

    findings = [f for f in result.findings if f.exception_type == ExceptionType.DATE_ANOMALY]
    assert len(findings) == 1
    assert findings[0].severity == Severity.WARN


def test_date_anomaly_invoice_postdates_today() -> None:
    s = baseline()
    s.invoice = make_invoice(invoice_date=date(2026, 1, 5))

    result = run(s, today=date(2026, 1, 1))

    findings = [f for f in result.findings if f.exception_type == ExceptionType.DATE_ANOMALY]
    assert len(findings) == 1


def test_date_anomaly_boundary_invoice_dated_exactly_today_is_not_flagged() -> None:
    s = baseline()
    s.invoice = make_invoice(invoice_date=date(2026, 1, 15))

    result = run(s, today=date(2026, 1, 15))

    types = [f.exception_type for f in result.findings]
    assert ExceptionType.DATE_ANOMALY not in types


def test_date_anomaly_boundary_invoice_dated_exactly_po_issue_date_is_not_flagged() -> None:
    s = baseline()
    s.po = make_po(issued_at=datetime(2026, 1, 5, tzinfo=UTC))
    s.invoice = make_invoice(invoice_date=date(2026, 1, 5))

    result = run(s)

    types = [f.exception_type for f in result.findings]
    assert ExceptionType.DATE_ANOMALY not in types


# --- Result classification ---------------------------------------------------


def test_result_is_exceptions_when_only_non_block_findings_present() -> None:
    s = baseline()
    s.grn_line = make_grn_line(
        po_line_id=s.po_line.id, qty_received=Decimal("12"), condition="good"
    )

    result = run(s)

    assert result.result == "exceptions"
    assert all(f.severity != Severity.BLOCK for f in result.findings)


def test_result_is_blocked_when_any_block_finding_present() -> None:
    s = baseline()
    s.grn_line = make_grn_line(po_line_id=s.po_line.id, qty_received=Decimal("8"), condition="good")

    result = run(s)

    assert result.result == "blocked"


# --- Input validation ---------------------------------------------------------


def test_raises_when_vendor_does_not_match_invoice_vendor_id() -> None:
    s = baseline()
    other_vendor = make_vendor(id=uuid.uuid4())

    with pytest.raises(MatchingError):
        run(s, vendor=other_vendor)


def test_raises_when_invoice_is_not_yet_extracted() -> None:
    s = baseline()
    s.invoice = make_invoice(subtotal=None, tax=None, total=None, invoice_number=None)

    with pytest.raises(MatchingError):
        run(s)
