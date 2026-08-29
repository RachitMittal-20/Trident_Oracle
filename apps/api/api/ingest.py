"""Shared invoice-ingestion path: content-hash dedupe, magic-byte sniffing,
storage upload, invoice row insert, extract-job enqueue, audit log. Used by
both POST /v1/invoices/upload (api/main.py) and POST /v1/webhooks/invoices
(api/webhooks.py) -- the two entry points differ only in how they obtain
`data` and `filename` (a multipart upload vs. a webhook's base64 payload or
fetched URL) and which `source_channel`/audit `action` to record, not in
what happens to the bytes once they have them. Business logic belongs here,
not duplicated across both route handlers -- CLAUDE.md: "Do not put business
logic in API route handlers."
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg
from core.errors import TridentOracleError
from core.magic_bytes import sniff_mime_type
from core.models import Invoice, InvoiceStatus
from storage.base import Storage

from api import db

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


class UnsupportedFileType(TridentOracleError):
    """The bytes don't sniff as a supported format (PDF/PNG/JPEG)."""


class FileTooLarge(TridentOracleError):
    """Exceeds MAX_UPLOAD_BYTES."""


@dataclass(frozen=True, slots=True)
class DuplicateInvoice:
    """Returned instead of IngestResult when content_hash already exists for
    this tenant -- a hard duplicate never reaches extraction."""

    invoice_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class IngestResult:
    invoice_id: uuid.UUID
    job_id: uuid.UUID


def ingest_invoice(
    conn: psycopg.Connection,
    storage: Storage,
    tenant_id: uuid.UUID,
    data: bytes,
    filename: str,
    *,
    source_channel: str,
    audit_action: str,
) -> IngestResult | DuplicateInvoice:
    """Runs the full ingestion pipeline for one invoice's raw bytes.
    Callers commit the connection themselves once satisfied with the
    result -- this function only executes statements, matching every other
    function in api/db.py.

    Raises FileTooLarge or UnsupportedFileType before touching the
    database at all.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise FileTooLarge(f"file exceeds the {MAX_UPLOAD_BYTES} byte limit")

    mime_type = sniff_mime_type(data)
    if mime_type is None:
        raise UnsupportedFileType("unsupported file type -- must be a PDF, PNG, or JPEG")

    content_hash = hashlib.sha256(data).hexdigest()
    db.set_tenant(conn, tenant_id)

    existing_id = db.find_invoice_by_content_hash(conn, tenant_id, content_hash)
    if existing_id is not None:
        return DuplicateInvoice(invoice_id=existing_id)

    invoice_id = uuid.uuid4()
    extension = mime_type.split("/")[-1]
    safe_filename = filename or f"upload.{extension}"
    storage_path = f"{tenant_id}/{invoice_id}/{safe_filename}"

    storage.upload(storage_path, data, mime_type)

    # Constructs the core Invoice dataclass -- even though only its
    # RECEIVED-state subset of fields is known yet -- so its own validation
    # (content_hash shape, currency shape) runs before anything is written.
    now = datetime.now(UTC)
    Invoice(
        id=invoice_id,
        tenant_id=tenant_id,
        currency="USD",
        source_channel=source_channel,  # type: ignore[arg-type]
        source_file_path=storage_path,
        content_hash=content_hash,
        status=InvoiceStatus.RECEIVED,
        created_at=now,
        updated_at=now,
    )

    db.insert_invoice(
        conn, invoice_id, tenant_id, storage_path, content_hash, source_channel=source_channel
    )

    idempotency_key = hashlib.sha256(f"{tenant_id}{content_hash}".encode()).hexdigest()
    job_id = db.enqueue_job(
        conn,
        tenant_id=tenant_id,
        job_type="extract",
        payload={"invoice_id": str(invoice_id)},
        idempotency_key=idempotency_key,
    )

    db.insert_audit_log(
        conn,
        tenant_id=tenant_id,
        actor_type="system" if source_channel == "webhook" else "user",
        action=audit_action,
        entity_type="invoice",
        entity_id=invoice_id,
        after={"status": "RECEIVED", "content_hash": content_hash},
    )

    return IngestResult(invoice_id=invoice_id, job_id=job_id)
