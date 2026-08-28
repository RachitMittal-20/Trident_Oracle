"""FastAPI app -- REST + webhooks. Route handlers validate, call api.db /
core, and serialize; the actual reads/writes live in api/db.py, not here --
CLAUDE.md: "Do not put business logic in API route handlers."
"""

import asyncio
import contextlib
import hmac
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Any, Literal

import psycopg
import structlog
from core.errors import InvalidStateTransition, TokenError
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from notifiers.telegram import TelegramNotifier
from storage.base import Storage

from api import (
    __version__,
    approvals,
    db,
    exceptions_view,
    invoices_list,
    match_view,
    verification,
    webhooks,
)
from api.config import (
    get_approval_redeemer_connection,
    get_connection,
    get_database_url,
    get_storage,
)
from api.events import EventBroadcaster, listen_for_events
from api.ingest import DuplicateInvoice, FileTooLarge, UnsupportedFileType, ingest_invoice
from api.schemas import (
    DecideRequest,
    DecideResponse,
    DeliveryResponse,
    DuplicateInvoiceDetail,
    ExceptionsListResponse,
    FieldConfidenceResponse,
    FieldCorrectionRequest,
    FieldCorrectionResponse,
    InvoiceLineResponse,
    InvoiceListResponse,
    InvoiceResponse,
    MatchViewResponse,
    RerunMatchResponse,
    ResolveExceptionRequest,
    ResolveExceptionResponse,
    TelegramUpdate,
    UploadResponse,
)

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    broadcaster = EventBroadcaster()
    app.state.broadcaster = broadcaster
    listener_task = asyncio.create_task(listen_for_events(broadcaster, get_database_url()))
    try:
        yield
    finally:
        listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener_task


app = FastAPI(title="Trident Oracle API", lifespan=lifespan)
app.include_router(webhooks.router)

# apps/web's /pipeline screen (a browser origin, e.g. localhost:3000) needs
# CORS for both its EventSource connection to /v1/events/stream and its
# upload fetch() to /v1/invoices/upload. WEB_ORIGIN is the one place that
# choice is configured -- no wildcard, since the SSE stream sends live
# tenant data.
_web_origin = os.environ.get("WEB_ORIGIN", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_web_origin],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)

SIGNED_URL_EXPIRES_IN_SECONDS = 300
_SSE_KEEPALIVE_SECONDS = 15.0


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
    filename = file.filename or ""

    try:
        outcome = ingest_invoice(
            conn,
            storage,
            tenant_id,
            data,
            filename,
            source_channel="upload",
            audit_action="invoice_uploaded",
        )
    except FileTooLarge as exc:
        raise HTTPException(status_code=413, detail="file exceeds the 10 MB limit") from exc
    except UnsupportedFileType as exc:
        raise HTTPException(
            status_code=415, detail="unsupported file type -- must be a PDF, PNG, or JPEG"
        ) from exc

    if isinstance(outcome, DuplicateInvoice):
        # A hard duplicate never reaches extraction -- reject before any
        # storage upload or job enqueue happens.
        raise HTTPException(
            status_code=409,
            detail=DuplicateInvoiceDetail(invoice_id=outcome.invoice_id).model_dump(mode="json"),
        )

    conn.commit()
    return UploadResponse(invoice_id=outcome.invoice_id, job_id=outcome.job_id)


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
    policy_min_field_confidence = db.get_active_policy_min_confidence(conn, tenant_id)
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
        policy_min_field_confidence=policy_min_field_confidence,
        created_at=invoice["created_at"],
        updated_at=invoice["updated_at"],
    )


@app.patch("/v1/invoices/{invoice_id}/fields", response_model=FieldCorrectionResponse)
def correct_invoice_field(
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID,
    body: FieldCorrectionRequest,
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
) -> FieldCorrectionResponse:
    db.set_tenant(conn, tenant_id)
    try:
        result = verification.correct_field(
            conn, tenant_id, invoice_id, body.field_path, body.value, actor_id=None
        )
    except verification.InvalidFieldPath as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except verification.FieldNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    conn.commit()
    return FieldCorrectionResponse(**result)


@app.post("/v1/invoices/{invoice_id}/rerun-match", response_model=RerunMatchResponse)
def rerun_invoice_match(
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID,
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
) -> RerunMatchResponse:
    db.set_tenant(conn, tenant_id)
    try:
        job_id = verification.rerun_match(conn, tenant_id, invoice_id, actor_id=None)
    except verification.FieldNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidStateTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    conn.commit()
    return RerunMatchResponse(job_id=job_id)


@app.get("/v1/invoices/{invoice_id}/match", response_model=MatchViewResponse)
def get_invoice_match_view(
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID,
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
) -> MatchViewResponse:
    db.set_tenant(conn, tenant_id)
    try:
        view = match_view.get_match_view(conn, invoice_id)
    except match_view.InvoiceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MatchViewResponse(**view)


@app.post("/v1/invoices/{invoice_id}/decide", response_model=DecideResponse)
def decide_invoice_match(
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID,
    body: DecideRequest,
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
) -> DecideResponse:
    db.set_tenant(conn, tenant_id)
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=422, detail="decision must be 'approved' or 'rejected'")
    try:
        result = match_view.decide_invoice(
            conn, tenant_id, invoice_id, body.decision, body.actor_user_id
        )
    except match_view.NotAuthorizedToDecide as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except match_view.NoPendingApprovalForActor as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except match_view.InvoiceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidStateTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    conn.commit()
    return DecideResponse(
        status=result.status,
        approvals_received=result.approvals_received,
        approvals_required=result.approvals_required,
    )


@app.get("/v1/exceptions", response_model=ExceptionsListResponse)
def get_exceptions(
    tenant_id: uuid.UUID,
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
    status: str = "open",
    severity: Literal["info", "warn", "block"] | None = None,
    exception_type: str | None = None,
    vendor_id: uuid.UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: Literal["severity", "age", "amount"] = "age",
    order: Literal["asc", "desc"] = "desc",
) -> ExceptionsListResponse:
    db.set_tenant(conn, tenant_id)
    result = exceptions_view.list_exceptions(
        conn,
        status=status,
        severity=severity,
        exception_type=exception_type,
        vendor_id=vendor_id,
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
        sort=sort,
        order=order,
    )
    return ExceptionsListResponse(**result)


@app.post("/v1/exceptions/{exception_id}/resolve", response_model=ResolveExceptionResponse)
def resolve_exception(
    exception_id: uuid.UUID,
    tenant_id: uuid.UUID,
    body: ResolveExceptionRequest,
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
) -> ResolveExceptionResponse:
    db.set_tenant(conn, tenant_id)
    try:
        exceptions_view.resolve_exception(
            conn, tenant_id, exception_id, body.actor_user_id, body.note
        )
    except exceptions_view.ExceptionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except exceptions_view.ExceptionAlreadySettled as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    conn.commit()
    return ResolveExceptionResponse()


@app.get("/v1/invoices", response_model=InvoiceListResponse)
def get_invoices(
    tenant_id: uuid.UUID,
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
    status: str | None = None,
    sort: Literal["invoice_date", "total", "status", "created_at", "invoice_number"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    page: int = 1,
    page_size: int = 25,
) -> InvoiceListResponse:
    db.set_tenant(conn, tenant_id)
    result = invoices_list.list_invoices(
        conn, status=status, sort=sort, order=order, page=page, page_size=page_size
    )
    return InvoiceListResponse(**result)


@app.get("/v1/deliveries", response_model=list[DeliveryResponse])
def list_deliveries(
    tenant_id: uuid.UUID,
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
    status: Literal["pending", "sent", "failed", "dead"] | None = None,
    channel: Literal["telegram", "email", "whatsapp"] | None = None,
    invoice_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DeliveryResponse]:
    # tenant_id as a query param carries the same auth-placeholder caveat as
    # every other endpoint here -- RLS (tenant_isolation on
    # notification_deliveries) is what actually scopes the result, not this
    # filter alone.
    db.set_tenant(conn, tenant_id)
    rows = db.list_notification_deliveries(
        conn, status=status, channel=channel, invoice_id=invoice_id, limit=limit, offset=offset
    )
    return [DeliveryResponse(**row) for row in rows]


def _sse_message(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/v1/events/stream")
async def stream_pipeline_events(
    request: Request,
    tenant_id: uuid.UUID,
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
) -> StreamingResponse:
    # tenant_id as a query param carries the same auth-placeholder caveat as
    # every other endpoint here (see upload_invoice's docstring) -- RLS is
    # what actually scopes every query this connection makes, both the
    # snapshot below and every get_pipeline_card() lookup as events arrive.
    db.set_tenant(conn, tenant_id)
    snapshot = db.list_pipeline_invoices(conn)

    broadcaster: EventBroadcaster = request.app.state.broadcaster
    queue = await broadcaster.subscribe(tenant_id)

    async def event_source() -> AsyncIterator[str]:
        try:
            yield _sse_message("snapshot", {"invoices": snapshot})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=_SSE_KEEPALIVE_SECONDS
                    )
                except TimeoutError:
                    # SSE comment line -- ignored by EventSource, keeps
                    # intermediary proxies/load balancers from idling out
                    # the connection during a quiet pipeline.
                    yield ": keep-alive\n\n"
                    continue
                card = db.get_pipeline_card(conn, uuid.UUID(event["invoice_id"]))
                yield _sse_message("invoice_event", {**event, "card": card})
        finally:
            await broadcaster.unsubscribe(tenant_id, queue)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --- Approval endpoints -----------------------------------------------------
#
# All three below share one rule, security-critical: the raw token (a path
# param here, a Telegram callback_data value below) is never written to a
# log line by this codebase's own logging, and never echoed back in a
# response body -- only structural facts (which TokenError subtype, an
# invoice id, a decision) are logged. See api/approvals.py's module
# docstring and core.errors.TokenError's docstring for the full reasoning,
# including why all three TokenError subtypes must render identically to
# the client rather than letting a response distinguish "expired" from
# "already used" from "not found".


@app.get("/v1/approvals/{token}", response_class=HTMLResponse)
def get_approval_page(
    token: str,
    conn: Annotated[psycopg.Connection, Depends(get_approval_redeemer_connection)],
) -> HTMLResponse:
    try:
        preview = approvals.preview_approval_token(conn, token)
    except TokenError as exc:
        log.info("approval_preview_rejected", reason=type(exc).__name__)
        return HTMLResponse(approvals.render_failure_page(), status_code=410)
    return HTMLResponse(approvals.render_approval_page(preview))


@app.post("/v1/approvals/{token}", response_class=HTMLResponse)
def post_approval_decision(
    token: str,
    decision: Annotated[str, Form()],
    conn: Annotated[psycopg.Connection, Depends(get_approval_redeemer_connection)],
) -> HTMLResponse:
    if decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'rejected'")

    # actor=None: this demo has no login-gated approval flow yet (see
    # api/main.py's upload_invoice docstring on the same auth placeholder) --
    # decided_by stays NULL, which approval_requests' schema already allows.
    try:
        result = approvals.redeem_approval_token(conn, token, decision, actor=None)  # type: ignore[arg-type]
    except TokenError as exc:
        log.info("approval_redeem_rejected", reason=type(exc).__name__)
        return HTMLResponse(approvals.render_failure_page(), status_code=410)
    return HTMLResponse(approvals.render_confirmation_page(result.decision))


@app.post("/v1/approvals/telegram/callback")
async def telegram_approval_callback(
    request: Request,
    conn: Annotated[psycopg.Connection, Depends(get_approval_redeemer_connection)],
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    expected_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if not expected_secret or not hmac.compare_digest(
        x_telegram_bot_api_secret_token or "", expected_secret
    ):
        # Never distinguish "no header" from "wrong header" from "server
        # misconfigured" -- all three read identically to whoever's asking.
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    payload = await request.json()
    update = TelegramUpdate.model_validate(payload)
    callback = update.callback_query
    if callback is None or callback.data is None or ":" not in callback.data:
        # Not a callback_query this endpoint handles (Telegram sends other
        # update types too) -- ack with 200 regardless, per Telegram's
        # webhook contract, so it doesn't retry-storm us over something
        # that will never resolve differently.
        return {"ok": True}

    decision_word, raw_token = callback.data.split(":", 1)
    decision = {"approve": "approved", "reject": "rejected"}.get(decision_word)
    notifier = TelegramNotifier()

    if decision is None:
        log.warning("telegram_callback_unknown_decision_word", decision_word=decision_word)
        notifier.answer_callback_query(callback.id, approvals.GENERIC_TOKEN_FAILURE_MESSAGE)
        return {"ok": True}

    try:
        result = approvals.redeem_approval_token(conn, raw_token, decision, actor=None)  # type: ignore[arg-type]
    except TokenError as exc:
        log.info("telegram_approval_redeem_rejected", reason=type(exc).__name__)
        if callback.message is not None:
            notifier.edit_message(
                str(callback.message.chat.id),
                str(callback.message.message_id),
                approvals.GENERIC_TOKEN_FAILURE_MESSAGE,
            )
        notifier.answer_callback_query(callback.id, approvals.GENERIC_TOKEN_FAILURE_MESSAGE)
        return {"ok": True}

    if callback.message is not None:
        notifier.edit_message(
            str(callback.message.chat.id),
            str(callback.message.message_id),
            f"Decision recorded: **{result.decision}**.",
        )
    notifier.answer_callback_query(callback.id, f"Recorded: {result.decision}")
    return {"ok": True}
