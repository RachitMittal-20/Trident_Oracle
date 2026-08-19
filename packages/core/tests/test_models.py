import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from core.errors import TridentOracleError
from core.models import (
    ExceptionType,
    FieldConfidence,
    GoodsReceipt,
    GoodsReceiptLine,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    MatchException,
    MatchResult,
    PurchaseOrder,
    PurchaseOrderLine,
    Severity,
    Tenant,
    TolerancePolicy,
    Vendor,
)

TENANT_ID = uuid.uuid4()
VENDOR_ID = uuid.uuid4()
PO_ID = uuid.uuid4()
INVOICE_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_tenant(**overrides: object) -> Tenant:
    fields = dict(id=uuid.uuid4(), name="Doritech", slug="doritech", created_at=NOW)
    fields.update(overrides)
    return Tenant(**fields)  # type: ignore[arg-type]


def make_vendor(**overrides: object) -> Vendor:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        name="Acme Corp.",
        normalized_name="acme corp",
        created_at=NOW,
    )
    fields.update(overrides)
    return Vendor(**fields)  # type: ignore[arg-type]


def make_purchase_order(**overrides: object) -> PurchaseOrder:
    fields = dict(
        id=PO_ID,
        tenant_id=TENANT_ID,
        vendor_id=VENDOR_ID,
        po_number="PO-1",
        issued_at=NOW,
        currency="USD",
        subtotal=Decimal("100.00"),
        tax=Decimal("7.00"),
        total=Decimal("107.00"),
        status="open",
        created_at=NOW,
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
        unit_price=Decimal("10.00"),
        tax_rate=Decimal("7.00"),
        line_total=Decimal("100.00"),
        created_at=NOW,
    )
    fields.update(overrides)
    return PurchaseOrderLine(**fields)  # type: ignore[arg-type]


def make_goods_receipt(**overrides: object) -> GoodsReceipt:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        po_id=PO_ID,
        grn_number="GRN-1",
        received_at=NOW,
        received_by=USER_ID,
        created_at=NOW,
    )
    fields.update(overrides)
    return GoodsReceipt(**fields)  # type: ignore[arg-type]


def make_grn_line(**overrides: object) -> GoodsReceiptLine:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        grn_id=uuid.uuid4(),
        po_line_id=uuid.uuid4(),
        qty_received=Decimal("10"),
        condition="good",
        created_at=NOW,
    )
    fields.update(overrides)
    return GoodsReceiptLine(**fields)  # type: ignore[arg-type]


def make_invoice(**overrides: object) -> Invoice:
    fields = dict(
        id=INVOICE_ID,
        tenant_id=TENANT_ID,
        invoice_number="INV-1",
        invoice_date=date(2026, 1, 1),
        currency="USD",
        subtotal=Decimal("100.00"),
        tax=Decimal("7.00"),
        total=Decimal("107.00"),
        source_channel="upload",
        source_file_path="/f.pdf",
        content_hash="a" * 64,
        status=InvoiceStatus.RECEIVED,
        created_at=NOW,
        updated_at=NOW,
    )
    fields.update(overrides)
    return Invoice(**fields)  # type: ignore[arg-type]


def make_invoice_line(**overrides: object) -> InvoiceLine:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        invoice_id=INVOICE_ID,
        line_no=1,
        description="Widget",
        qty=Decimal("10"),
        unit_price=Decimal("10.00"),
        line_total=Decimal("100.00"),
        created_at=NOW,
    )
    fields.update(overrides)
    return InvoiceLine(**fields)  # type: ignore[arg-type]


def make_field_confidence(**overrides: object) -> FieldConfidence:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        invoice_id=INVOICE_ID,
        field_path="header.total",
        confidence=Decimal("0.99"),
        created_at=NOW,
    )
    fields.update(overrides)
    return FieldConfidence(**fields)  # type: ignore[arg-type]


def make_tolerance_policy(**overrides: object) -> TolerancePolicy:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        name="Default Policy",
        is_active=True,
        version=1,
        price_variance_pct=Decimal("2.0"),
        qty_tolerance_pct=Decimal("0.0"),
        auto_approve_below=Decimal("5000"),
        dual_approval_above=Decimal("100000"),
        min_field_confidence=Decimal("0.85"),
        duplicate_window_days=90,
        created_at=NOW,
    )
    fields.update(overrides)
    return TolerancePolicy(**fields)  # type: ignore[arg-type]


def make_match_exception(**overrides: object) -> MatchException:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        match_run_id=uuid.uuid4(),
        invoice_id=INVOICE_ID,
        exception_type=ExceptionType.QTY_OVER,
        severity=Severity.BLOCK,
        status="open",
        created_at=NOW,
    )
    fields.update(overrides)
    return MatchException(**fields)  # type: ignore[arg-type]


# --- Valid construction sanity checks --------------------------------------


def test_valid_tenant_constructs() -> None:
    make_tenant()


def test_valid_purchase_order_constructs() -> None:
    make_purchase_order()


def test_valid_po_line_constructs() -> None:
    make_po_line()


def test_valid_goods_receipt_constructs() -> None:
    make_goods_receipt()


def test_valid_goods_receipt_line_constructs() -> None:
    make_grn_line()


def test_valid_invoice_constructs() -> None:
    make_invoice()


def test_valid_invoice_line_constructs() -> None:
    make_invoice_line()


def test_valid_match_exception_constructs() -> None:
    make_match_exception()


def test_valid_match_result_clean_constructs() -> None:
    MatchResult(invoice_id=INVOICE_ID, result="clean", exceptions=())


def test_valid_match_result_exceptions_constructs() -> None:
    MatchResult(invoice_id=INVOICE_ID, result="exceptions", exceptions=(make_match_exception(),))


# --- Blank / whitespace fields -----------------------------------------


def test_tenant_rejects_blank_name() -> None:
    with pytest.raises(TridentOracleError):
        make_tenant(name="   ")


def test_vendor_rejects_blank_normalized_name() -> None:
    with pytest.raises(TridentOracleError):
        make_vendor(normalized_name="")


# --- Negative quantities -------------------------------------------------


def test_po_line_rejects_negative_qty_ordered() -> None:
    with pytest.raises(TridentOracleError):
        make_po_line(qty_ordered=Decimal("-1"), line_total=Decimal("-10.00"))


def test_po_line_rejects_zero_qty_ordered() -> None:
    with pytest.raises(TridentOracleError):
        make_po_line(qty_ordered=Decimal("0"), line_total=Decimal("0.00"))


def test_grn_line_rejects_negative_qty_received() -> None:
    with pytest.raises(TridentOracleError):
        make_grn_line(qty_received=Decimal("-5"))


def test_invoice_line_rejects_negative_qty() -> None:
    with pytest.raises(TridentOracleError):
        make_invoice_line(qty=Decimal("-1"), line_total=Decimal("-10.00"))


def test_po_line_rejects_negative_unit_price() -> None:
    with pytest.raises(TridentOracleError):
        make_po_line(unit_price=Decimal("-10.00"), line_total=Decimal("-100.00"))


# --- Totals that don't reconcile -----------------------------------------


def test_purchase_order_rejects_non_reconciling_total() -> None:
    with pytest.raises(TridentOracleError):
        make_purchase_order(
            subtotal=Decimal("100.00"), tax=Decimal("7.00"), total=Decimal("999.00")
        )


def test_invoice_rejects_non_reconciling_total() -> None:
    with pytest.raises(TridentOracleError):
        make_invoice(subtotal=Decimal("100.00"), tax=Decimal("7.00"), total=Decimal("999.00"))


def test_po_line_rejects_line_total_mismatch() -> None:
    with pytest.raises(TridentOracleError):
        make_po_line(
            qty_ordered=Decimal("10"), unit_price=Decimal("10.00"), line_total=Decimal("50.00")
        )


def test_invoice_line_rejects_line_total_mismatch() -> None:
    with pytest.raises(TridentOracleError):
        make_invoice_line(
            qty=Decimal("10"), unit_price=Decimal("10.00"), line_total=Decimal("50.00")
        )


# --- Enum-like / recognized-value fields ----------------------------------


def test_purchase_order_rejects_unrecognized_status() -> None:
    with pytest.raises(TridentOracleError):
        make_purchase_order(status="bogus")


def test_grn_line_rejects_unrecognized_condition() -> None:
    with pytest.raises(TridentOracleError):
        make_grn_line(condition="pristine")


def test_match_exception_rejects_unrecognized_status() -> None:
    with pytest.raises(TridentOracleError):
        make_match_exception(status="archived")


def test_match_result_rejects_unrecognized_result() -> None:
    with pytest.raises(TridentOracleError):
        MatchResult(invoice_id=INVOICE_ID, result="bogus", exceptions=())  # type: ignore[arg-type]


# --- Confidence bounds -----------------------------------------------------


def test_field_confidence_rejects_out_of_range_high() -> None:
    with pytest.raises(TridentOracleError):
        make_field_confidence(confidence=Decimal("1.5"))


def test_field_confidence_rejects_out_of_range_low() -> None:
    with pytest.raises(TridentOracleError):
        make_field_confidence(confidence=Decimal("-0.1"))


def test_invoice_rejects_out_of_range_confidence() -> None:
    with pytest.raises(TridentOracleError):
        make_invoice(overall_confidence=Decimal("1.01"))


def test_tolerance_policy_rejects_out_of_range_min_field_confidence() -> None:
    with pytest.raises(TridentOracleError):
        make_tolerance_policy(min_field_confidence=Decimal("1.2"))


# --- Cross-field invariants --------------------------------------------


def test_tolerance_policy_rejects_dual_approval_below_auto_approve() -> None:
    with pytest.raises(TridentOracleError):
        make_tolerance_policy(
            auto_approve_below=Decimal("10000"), dual_approval_above=Decimal("5000")
        )


def test_match_exception_resolved_requires_resolved_by_and_at() -> None:
    with pytest.raises(TridentOracleError):
        make_match_exception(status="resolved")


def test_match_exception_resolved_with_fields_constructs() -> None:
    make_match_exception(status="resolved", resolved_by=USER_ID, resolved_at=NOW)


def test_match_result_clean_rejects_nonempty_exceptions() -> None:
    with pytest.raises(TridentOracleError):
        MatchResult(invoice_id=INVOICE_ID, result="clean", exceptions=(make_match_exception(),))


def test_match_result_exceptions_rejects_empty_exceptions() -> None:
    with pytest.raises(TridentOracleError):
        MatchResult(invoice_id=INVOICE_ID, result="exceptions", exceptions=())


# --- Frozen / immutable -----------------------------------------------


def test_dataclasses_are_frozen() -> None:
    tenant = make_tenant()
    with pytest.raises(AttributeError):
        tenant.name = "New Name"  # type: ignore[misc]
