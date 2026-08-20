"""FastAPI app -- REST + webhooks. Route handlers validate, call api.db /
core, and serialize; the actual reads/writes live in api/db.py, not here --
CLAUDE.md: "Do not put business logic in API route handlers."
"""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated

import psycopg
from core.magic_bytes import sniff_mime_type
from core.models import Invoice, InvoiceStatus
from fastapi import Depends, FastAPI, Form, HTTPException, UploadFile
from storage.base import Storage

from api import __version__, db
from api.config import MAX_UPLOAD_BYTES, get_connection, get_storage
from api.schemas import (
    DuplicateInvoiceDetail,
    FieldConfidenceResponse,
    InvoiceLineResponse,
    InvoiceResponse,
    UploadResponse,
)

app = FastAPI(title="Trident Oracle API")

SIGNED_URL_EXPIRES_IN_SECONDS = 300


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/v1/invoices/upload", status_code=202, response_model=UploadResponse)
async def upload_invoice(
    file: UploadFile,
    tenant_id: Annotated[uuid.UUID, Form()],
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> UploadResponse:
    # tenant_id as an explicit form field is a placeholder for real
    # authentication (a JWT claim, typically) -- no auth system exists yet
    # in this project. Whatever eventually determines the caller's tenant,
    # this is the point where it must be resolved before anything else runs.
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file exceeds the 10 MB limit")

    mime_type = sniff_mime_type(data)
    if mime_type is None:
        raise HTTPException(
            status_code=415, detail="unsupported file type -- must be a PDF, PNG, or JPEG"
        )

    content_hash = hashlib.sha256(data).hexdigest()

    db.set_tenant(conn, tenant_id)

    existing_id = db.find_invoice_by_content_hash(conn, tenant_id, content_hash)
    if existing_id is not None:
        # A hard duplicate never reaches extraction -- reject before any
        # storage upload or job enqueue happens.
        raise HTTPException(
            status_code=409,
            detail=DuplicateInvoiceDetail(invoice_id=existing_id).model_dump(mode="json"),
        )

    invoice_id = uuid.uuid4()
    extension = mime_type.split("/")[-1]
    filename = file.filename or f"upload.{extension}"
    storage_path = f"{tenant_id}/{invoice_id}/{filename}"

    storage.upload(storage_path, data, mime_type)

    # Constructs the core Invoice dataclass -- even though only its
    # RECEIVED-state subset of fields is known yet -- so its own validation
    # (content_hash shape, currency shape) runs before anything is written.
    now = datetime.now(UTC)
    Invoice(
        id=invoice_id,
        tenant_id=tenant_id,
        currency="USD",
        source_channel="upload",
        source_file_path=storage_path,
        content_hash=content_hash,
        status=InvoiceStatus.RECEIVED,
        created_at=now,
        updated_at=now,
    )

    db.insert_invoice(conn, invoice_id, tenant_id, storage_path, content_hash)

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
        actor_type="user",
        action="invoice_uploaded",
        entity_type="invoice",
        entity_id=invoice_id,
        after={"status": "RECEIVED", "content_hash": content_hash},
    )

    conn.commit()

    return UploadResponse(invoice_id=invoice_id, job_id=job_id)


@app.get("/v1/invoices/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID,
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
    storage: Annotated[Storage, Depends(get_storage)],
) -> InvoiceResponse:
    # tenant_id as a query param carries the same auth-placeholder caveat as
    # the upload endpoint above.
    db.set_tenant(conn, tenant_id)

    invoice = db.get_invoice(conn, invoice_id)
    if invoice is None:
        # RLS already filters out other tenants' rows, so an ID that exists
        # but belongs to a different tenant reads identically to one that
        # doesn't exist at all -- never leaking existence across tenants.
        raise HTTPException(status_code=404, detail="invoice not found")

    lines = db.get_invoice_lines(conn, invoice_id)
    confidences = db.get_field_confidences(conn, invoice_id)
    # Generated per request -- never a public/cached path.
    file_url = storage.signed_url(invoice["source_file_path"], SIGNED_URL_EXPIRES_IN_SECONDS)

    return InvoiceResponse(
        id=invoice["id"],
        tenant_id=invoice["tenant_id"],
        invoice_number=invoice["invoice_number"],
        invoice_date=invoice["invoice_date"],
        due_date=invoice["due_date"],
        currency=invoice["currency"],
        subtotal=invoice["subtotal"],
        tax=invoice["tax"],
        total=invoice["total"],
        status=invoice["status"],
        extraction_backend=invoice["extraction_backend"],
        overall_confidence=invoice["overall_confidence"],
        file_url=file_url,
        lines=[InvoiceLineResponse(**line) for line in lines],
        field_confidences=[FieldConfidenceResponse(**c) for c in confidences],
        created_at=invoice["created_at"],
        updated_at=invoice["updated_at"],
    )
