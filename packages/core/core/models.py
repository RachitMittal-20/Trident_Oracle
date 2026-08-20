"""Pure domain objects for Trident Oracle.

No I/O, no side effects, no framework dependencies -- packages/core has none of
those, per CLAUDE.md. Every object here is a frozen dataclass; construction
validates the object's own invariants (non-negative quantities, reconciling
totals, confidence in [0, 1]) and raises TridentOracleError on violation.

No floats anywhere in this module. Every fractional value -- money, quantity,
rate, or confidence score -- is a Decimal, so nothing here is ever subject to
binary floating-point rounding error.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from core.errors import TridentOracleError


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise TridentOracleError(message)


# --- Enums -----------------------------------------------------------------


class InvoiceStatus(StrEnum):
    RECEIVED = "RECEIVED"
    EXTRACTING = "EXTRACTING"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    EXTRACTED = "EXTRACTED"
    MATCHING = "MATCHING"
    MATCHED_CLEAN = "MATCHED_CLEAN"
    NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
    EXCEPTIONS_RAISED = "EXCEPTIONS_RAISED"
    AUTO_POSTED = "AUTO_POSTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    POSTED = "POSTED"


class ExceptionType(StrEnum):
    NO_PO = "NO_PO"
    NO_GRN = "NO_GRN"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    SUSPECTED_DUPLICATE = "SUSPECTED_DUPLICATE"
    PRICE_VARIANCE = "PRICE_VARIANCE"
    QTY_SHORT = "QTY_SHORT"
    QTY_OVER = "QTY_OVER"
    UNMATCHED_LINE = "UNMATCHED_LINE"
    ARITHMETIC_ERROR = "ARITHMETIC_ERROR"
    TAX_MISMATCH = "TAX_MISMATCH"
    DATE_ANOMALY = "DATE_ANOMALY"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


class MatchMethod(StrEnum):
    SKU = "sku"
    FUZZY = "fuzzy"
    LLM = "llm"
    UNMATCHED = "unmatched"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


class JobType(StrEnum):
    EXTRACT = "extract"
    MATCH = "match"
    NOTIFY = "notify"
    POST = "post"


# --- Tenancy -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tenant:
    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime

    def __post_init__(self) -> None:
        _check(bool(self.name.strip()), "Tenant.name must not be blank")
        _check(bool(self.slug.strip()), "Tenant.slug must not be blank")


# --- Procurement ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Vendor:
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    normalized_name: str
    created_at: datetime
    tax_id: str | None = None
    email: str | None = None

    def __post_init__(self) -> None:
        _check(bool(self.name.strip()), "Vendor.name must not be blank")
        _check(bool(self.normalized_name.strip()), "Vendor.normalized_name must not be blank")


@dataclass(frozen=True, slots=True)
class PurchaseOrder:
    id: uuid.UUID
    tenant_id: uuid.UUID
    vendor_id: uuid.UUID
    po_number: str
    issued_at: datetime
    currency: str
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    status: Literal["open", "partially_received", "closed", "cancelled"]
    created_at: datetime

    def __post_init__(self) -> None:
        _check(bool(self.po_number.strip()), "PurchaseOrder.po_number must not be blank")
        _check(len(self.currency) == 3, "PurchaseOrder.currency must be a 3-letter code")
        _check(self.subtotal >= 0, "PurchaseOrder.subtotal must not be negative")
        _check(self.tax >= 0, "PurchaseOrder.tax must not be negative")
        _check(self.total >= 0, "PurchaseOrder.total must not be negative")
        _check(
            self.subtotal + self.tax == self.total,
            "PurchaseOrder.subtotal + tax must equal total",
        )
        _check(
            self.status in ("open", "partially_received", "closed", "cancelled"),
            f"PurchaseOrder.status {self.status!r} is not a recognized status",
        )


@dataclass(frozen=True, slots=True)
class PurchaseOrderLine:
    id: uuid.UUID
    tenant_id: uuid.UUID
    po_id: uuid.UUID
    line_no: int
    description: str
    normalized_description: str
    qty_ordered: Decimal
    unit_price: Decimal
    tax_rate: Decimal
    line_total: Decimal
    created_at: datetime
    sku: str | None = None

    def __post_init__(self) -> None:
        _check(self.line_no > 0, "PurchaseOrderLine.line_no must be positive")
        _check(bool(self.description.strip()), "PurchaseOrderLine.description must not be blank")
        _check(self.qty_ordered > 0, "PurchaseOrderLine.qty_ordered must be positive")
        _check(self.unit_price >= 0, "PurchaseOrderLine.unit_price must not be negative")
        _check(self.tax_rate >= 0, "PurchaseOrderLine.tax_rate must not be negative")
        _check(
            self.line_total == self.qty_ordered * self.unit_price,
            "PurchaseOrderLine.line_total must equal qty_ordered * unit_price",
        )


@dataclass(frozen=True, slots=True)
class GoodsReceipt:
    id: uuid.UUID
    tenant_id: uuid.UUID
    po_id: uuid.UUID
    grn_number: str
    received_at: datetime
    received_by: uuid.UUID
    created_at: datetime

    def __post_init__(self) -> None:
        _check(bool(self.grn_number.strip()), "GoodsReceipt.grn_number must not be blank")


@dataclass(frozen=True, slots=True)
class GoodsReceiptLine:
    id: uuid.UUID
    tenant_id: uuid.UUID
    grn_id: uuid.UUID
    po_line_id: uuid.UUID
    qty_received: Decimal
    condition: Literal["good", "damaged", "partial"]
    created_at: datetime
    notes: str | None = None

    def __post_init__(self) -> None:
        _check(self.qty_received >= 0, "GoodsReceiptLine.qty_received must not be negative")
        _check(
            self.condition in ("good", "damaged", "partial"),
            f"GoodsReceiptLine.condition {self.condition!r} is not recognized",
        )


# --- Ingestion & extraction ------------------------------------------------


@dataclass(frozen=True, slots=True)
class Invoice:
    """invoice_number/invoice_date/subtotal/tax/total are None at RECEIVED --
    they don't exist yet until extraction reads them off the document. Every
    other field is required from the moment the row is created at upload."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    currency: str
    source_channel: Literal["upload", "email", "webhook"]
    source_file_path: str
    content_hash: str
    status: InvoiceStatus
    created_at: datetime
    updated_at: datetime
    invoice_number: str | None = None
    invoice_date: date | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    vendor_id: uuid.UUID | None = None
    po_id: uuid.UUID | None = None
    due_date: date | None = None
    extraction_backend: Literal["gemini", "tesseract"] | None = None
    overall_confidence: Decimal | None = None

    def __post_init__(self) -> None:
        if self.invoice_number is not None:
            _check(bool(self.invoice_number.strip()), "Invoice.invoice_number must not be blank")
        _check(len(self.currency) == 3, "Invoice.currency must be a 3-letter code")
        _check(
            len(self.content_hash) == 64,
            "Invoice.content_hash must be a 64-character SHA-256 hex digest",
        )
        if self.subtotal is not None:
            _check(self.subtotal >= 0, "Invoice.subtotal must not be negative")
        if self.tax is not None:
            _check(self.tax >= 0, "Invoice.tax must not be negative")
        if self.total is not None:
            _check(self.total >= 0, "Invoice.total must not be negative")
        if self.subtotal is not None and self.tax is not None and self.total is not None:
            _check(
                self.subtotal + self.tax == self.total, "Invoice.subtotal + tax must equal total"
            )
        if self.overall_confidence is not None:
            _check(
                Decimal("0") <= self.overall_confidence <= Decimal("1"),
                "Invoice.overall_confidence must be between 0 and 1",
            )


@dataclass(frozen=True, slots=True)
class InvoiceLine:
    id: uuid.UUID
    tenant_id: uuid.UUID
    invoice_id: uuid.UUID
    line_no: int
    description: str
    qty: Decimal
    unit_price: Decimal
    line_total: Decimal
    created_at: datetime
    normalized_description: str | None = None
    matched_po_line_id: uuid.UUID | None = None
    match_method: MatchMethod | None = None

    def __post_init__(self) -> None:
        _check(self.line_no > 0, "InvoiceLine.line_no must be positive")
        _check(bool(self.description.strip()), "InvoiceLine.description must not be blank")
        _check(self.qty > 0, "InvoiceLine.qty must be positive")
        _check(self.unit_price >= 0, "InvoiceLine.unit_price must not be negative")
        _check(
            self.line_total == self.qty * self.unit_price,
            "InvoiceLine.line_total must equal qty * unit_price",
        )


@dataclass(frozen=True, slots=True)
class FieldConfidence:
    id: uuid.UUID
    tenant_id: uuid.UUID
    invoice_id: uuid.UUID
    field_path: str
    confidence: Decimal
    created_at: datetime
    bbox: Mapping[str, Decimal] | None = None
    raw_text: str | None = None

    def __post_init__(self) -> None:
        _check(bool(self.field_path.strip()), "FieldConfidence.field_path must not be blank")
        _check(
            Decimal("0") <= self.confidence <= Decimal("1"),
            "FieldConfidence.confidence must be between 0 and 1",
        )


# --- Matching & approvals ------------------------------------------------


@dataclass(frozen=True, slots=True)
class TolerancePolicy:
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    is_active: bool
    version: int
    price_variance_pct: Decimal
    qty_tolerance_pct: Decimal
    auto_approve_below: Decimal
    dual_approval_above: Decimal
    min_field_confidence: Decimal
    duplicate_window_days: int
    created_at: datetime

    def __post_init__(self) -> None:
        _check(bool(self.name.strip()), "TolerancePolicy.name must not be blank")
        _check(self.version > 0, "TolerancePolicy.version must be positive")
        _check(
            self.price_variance_pct >= 0, "TolerancePolicy.price_variance_pct must not be negative"
        )
        _check(
            self.qty_tolerance_pct >= 0, "TolerancePolicy.qty_tolerance_pct must not be negative"
        )
        _check(
            self.auto_approve_below >= 0,
            "TolerancePolicy.auto_approve_below must not be negative",
        )
        _check(
            self.dual_approval_above >= self.auto_approve_below,
            "TolerancePolicy.dual_approval_above must not be less than auto_approve_below",
        )
        _check(
            Decimal("0") <= self.min_field_confidence <= Decimal("1"),
            "TolerancePolicy.min_field_confidence must be between 0 and 1",
        )
        _check(
            self.duplicate_window_days >= 0,
            "TolerancePolicy.duplicate_window_days must not be negative",
        )


@dataclass(frozen=True, slots=True)
class MatchException:
    id: uuid.UUID
    tenant_id: uuid.UUID
    match_run_id: uuid.UUID
    invoice_id: uuid.UUID
    exception_type: ExceptionType
    severity: Severity
    status: Literal["open", "resolved", "dismissed"]
    created_at: datetime
    po_line_id: uuid.UUID | None = None
    invoice_line_id: uuid.UUID | None = None
    expected_value: Decimal | None = None
    actual_value: Decimal | None = None
    delta: Decimal | None = None
    delta_pct: Decimal | None = None
    resolved_by: uuid.UUID | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None

    def __post_init__(self) -> None:
        _check(
            self.status in ("open", "resolved", "dismissed"),
            f"MatchException.status {self.status!r} is not recognized",
        )
        if self.status == "resolved":
            _check(
                self.resolved_by is not None and self.resolved_at is not None,
                "MatchException.status 'resolved' requires resolved_by and resolved_at",
            )


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Return type of packages/core/matching's run_three_way_match."""

    invoice_id: uuid.UUID
    result: Literal["clean", "exceptions", "blocked"]
    exceptions: tuple[MatchException, ...] = ()

    def __post_init__(self) -> None:
        _check(
            self.result in ("clean", "exceptions", "blocked"),
            f"MatchResult.result {self.result!r} is not recognized",
        )
        if self.result == "clean":
            _check(len(self.exceptions) == 0, "MatchResult.result 'clean' must have no exceptions")
        else:
            _check(
                len(self.exceptions) > 0,
                f"MatchResult.result {self.result!r} must have at least one exception",
            )
