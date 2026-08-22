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
    created_at: datetime
    updated_at: datetime
