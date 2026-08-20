"""Pydantic v2 boundary models for the API -- CLAUDE.md: "Pydantic v2 models
for all boundary data."
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class UploadResponse(BaseModel):
    invoice_id: uuid.UUID
    job_id: uuid.UUID


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
