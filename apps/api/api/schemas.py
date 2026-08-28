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
