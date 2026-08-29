"""Pydantic v2 boundary models for the API -- CLAUDE.md: "Pydantic v2 models
for all boundary data."
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, model_validator


class UploadResponse(BaseModel):
    invoice_id: uuid.UUID
    job_id: uuid.UUID


class WebhookInvoicePayload(BaseModel):
    """POST /v1/webhooks/invoices' JSON body -- see docs/WEBHOOKS.md for the
    full contract. Exactly one of file_base64/file_url must be present;
    filename is optional either way (a fetched URL's path is used as a
    fallback, a base64 payload with no filename gets a generic one)."""

    tenant_id: uuid.UUID
    filename: str | None = None
    file_base64: str | None = None
    file_url: str | None = None

    @model_validator(mode="after")
    def _exactly_one_file_source(self) -> "WebhookInvoicePayload":
        if bool(self.file_base64) == bool(self.file_url):
            raise ValueError("exactly one of file_base64 or file_url must be provided")
        return self


class DeliveryResponse(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID | None
    exception_id: uuid.UUID | None
    channel: str
    recipient: str
    status: str
    attempts: int
    next_retry_at: datetime | None
    provider_message_id: str | None
    error: str | None
    sent_at: datetime | None
    created_at: datetime


class DuplicateInvoiceDetail(BaseModel):
    invoice_id: uuid.UUID
    detail: str = "duplicate invoice: this file has already been uploaded"


class InvoiceLineResponse(BaseModel):
    id: uuid.UUID
    line_no: int
    description: str
    qty: Decimal
    unit_price: Decimal
    line_total: Decimal
    matched_po_line_id: uuid.UUID | None
    match_method: str | None


class FieldConfidenceResponse(BaseModel):
    field_path: str
    confidence: Decimal
    bbox: dict[str, Any] | None
    raw_text: str | None
    human_corrected: bool


class TelegramChat(BaseModel):
    id: int


class TelegramMessage(BaseModel):
    message_id: int
    chat: TelegramChat


class TelegramCallbackQuery(BaseModel):
    id: str
    data: str | None = None
    message: TelegramMessage | None = None


class TelegramUpdate(BaseModel):
    """Only the piece of Telegram's webhook Update object this codebase
    actually reads -- extra fields on the real payload are ignored, not
    rejected, per Telegram's own forward-compatibility expectations for
    webhook consumers."""

    callback_query: TelegramCallbackQuery | None = None


class InvoiceResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str
    subtotal: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    status: str
    extraction_backend: str | None
    overall_confidence: Decimal | None
    file_url: str
    lines: list[InvoiceLineResponse]
    field_confidences: list[FieldConfidenceResponse]
    policy_min_field_confidence: Decimal | None
    created_at: datetime
    updated_at: datetime


class FieldCorrectionRequest(BaseModel):
    field_path: str
    value: str


class FieldCorrectionResponse(BaseModel):
    field_path: str
    value: str


class RerunMatchResponse(BaseModel):
    job_id: uuid.UUID
    status: str = "MATCHING"


class MatchViewInvoice(BaseModel):
    id: str
    invoice_number: str | None
    total: str | None
    currency: str
    status: str


class MatchViewPO(BaseModel):
    id: str
    po_number: str
    total: str | None


class MatchViewPOLine(BaseModel):
    id: str
    line_no: int
    sku: str | None
    description: str
    qty_ordered: str | None
    qty_received: str | None
    unit_price: str | None
    line_total: str | None


class MatchViewInvoiceLine(BaseModel):
    id: str
    line_no: int
    description: str
    qty: str | None
    unit_price: str | None
    line_total: str | None
    matched_po_line_id: str | None
    match_method: str | None
    match_confidence: str | None


class MatchViewException(BaseModel):
    id: str
    exception_type: str
    severity: str
    detail: str
    po_line_id: str | None
    invoice_line_id: str | None
    expected_value: str | None
    actual_value: str | None
    delta: str | None
    delta_pct: str | None
    status: str


class MatchViewRun(BaseModel):
    id: str
    result: str
    reason: str | None
    policy_version: int
    executed_at: str


class MatchViewResponse(BaseModel):
    invoice: MatchViewInvoice
    po: MatchViewPO | None
    po_lines: list[MatchViewPOLine]
    invoice_lines: list[MatchViewInvoiceLine]
    exceptions: list[MatchViewException]
    match_run: MatchViewRun | None


class DecideRequest(BaseModel):
    decision: str
    actor_user_id: uuid.UUID


class DecideResponse(BaseModel):
    status: str
    approvals_received: int
    approvals_required: int


class ExceptionCardResponse(BaseModel):
    id: str
    exception_type: str
    severity: str
    detail: str
    status: str
    created_at: str
    invoice_id: str
    invoice_number: str | None
    invoice_total: str | None
    currency: str
    vendor_id: str | None
    vendor_name: str | None
    expected_value: str | None
    actual_value: str | None
    delta: str | None
    delta_pct: str | None


class VendorOption(BaseModel):
    id: str
    name: str


class ExceptionsListResponse(BaseModel):
    exceptions: list[ExceptionCardResponse]
    auto_posted_this_week: int
    vendors: list[VendorOption]


class ResolveExceptionRequest(BaseModel):
    actor_user_id: uuid.UUID
    note: str | None = None


class ResolveExceptionResponse(BaseModel):
    status: str = "resolved"


class InvoiceListItem(BaseModel):
    id: str
    invoice_number: str | None
    invoice_date: str | None
    currency: str
    total: str | None
    status: str
    created_at: str
    vendor_id: str | None
    vendor_name: str | None


class InvoiceListResponse(BaseModel):
    items: list[InvoiceListItem]
    total: int
    page: int
    page_size: int
    status_counts: dict[str, int]


class AnalyticsSummaryResponse(BaseModel):
    period_days: int
    invoices_processed: int
    invoices_processed_delta: int
    auto_post_rate_pct: str | None
    mean_extraction_confidence: str | None
    exceptions_by_severity: dict[str, int]
    value_at_risk: str
    mean_seconds_to_decision: float | None


class VolumePoint(BaseModel):
    day: str
    outcome: str
    count: int


class ExceptionTypeCount(BaseModel):
    exception_type: str
    count: int


class ConfidenceBucket(BaseModel):
    bucket_start: float
    bucket_end: float
    count: int


class LatencyPercentiles(BaseModel):
    p50: float | None
    p95: float | None
    p99: float | None


class LatencyResponse(BaseModel):
    extraction: LatencyPercentiles
    matching: LatencyPercentiles
    notification: LatencyPercentiles


class AutoPostTrendPoint(BaseModel):
    day: str
    auto_posted: int
    settled: int
    rate_pct: str | None


class VendorAnalyticsRow(BaseModel):
    vendor_id: str
    vendor_name: str
    invoice_count: int
    exception_rate_pct: str
    mean_price_variance_pct: str | None


class DeliveryHealthResponse(BaseModel):
    total_deliveries: int
    sent_deliveries: int
    success_rate_pct: str | None
    mean_attempts: str
    max_attempts: int
    dead_letter_count: int


class EvalRunSummary(BaseModel):
    id: str
    dataset: str
    backend: str
    model_version: str | None
    sample_count: int
    started_at: str
    finished_at: str | None


class EvalFieldMetrics(BaseModel):
    field_path: str
    n: int
    precision: str | None
    recall: str | None
    f1: str | None
    exact_match_rate: str | None
    mean_confidence: str | None
    mean_absolute_error: str | None
    within_tolerance_rate: str | None


class EvalCalibrationBucket(BaseModel):
    bucket_low: float
    bucket_high: float
    n: int
    mean_confidence: str | None
    actual_accuracy: str | None


class EvalRunDetail(BaseModel):
    id: str
    dataset: str
    backend: str
    model_version: str | None
    sample_count: int
    started_at: str
    finished_at: str | None
    overall_exact_match_rate: float | None
    mean_latency_ms: str | None
    latency_p50_ms: str | None
    latency_p95_ms: str | None
    latency_p99_ms: str | None
    total_estimated_cost_usd: str | None
    cost_per_1000_usd: float | None
    line_item_precision: str | None
    line_item_recall: str | None
    line_item_f1: str | None
    fields: list[EvalFieldMetrics]
    calibration: list[EvalCalibrationBucket]


class EvalFailureDocument(BaseModel):
    doc_id: str
    ground_truth: dict[str, Any]
    extraction_result: dict[str, Any]
    mismatch_count: int
    thumbnail_url: str | None
    mime_type: str | None
