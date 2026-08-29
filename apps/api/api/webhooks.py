"""POST /v1/webhooks/invoices -- external systems submitting invoices
without going through the upload UI. See docs/WEBHOOKS.md for the full
signed-request contract, a signature example in Python and curl, and the
complete list of error responses.

Route handlers here validate and call api.ingest/api.db; the actual
ingestion logic lives in api.ingest, shared with POST /v1/invoices/upload --
CLAUDE.md: "Do not put business logic in API route handlers."

Security-critical section -- the signature is verified against the RAW
request body, before any JSON parsing happens. Parsing first and verifying
second would mean a byte-for-byte tampering check runs against bytes
FastAPI/Pydantic re-serializes from its own parsed model, not the bytes the
caller actually sent and signed -- subtly defeating the entire point of a
body signature. webhook_invoice's very first line is `await request.body()`;
the signature check happens immediately after, and nothing touches the body
as JSON until it passes.
"""

import base64
import binascii
import hmac
import ipaddress
import os
import socket
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlparse

import httpx
import structlog
from core.errors import StorageError
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from psycopg import Connection
from storage.base import Storage

from api.config import get_connection, get_storage
from api.ingest import DuplicateInvoice, FileTooLarge, UnsupportedFileType, ingest_invoice
from api.ratelimit import rate_limit_dependency, webhook_rate_limiter
from api.schemas import DuplicateInvoiceDetail, UploadResponse, WebhookInvoicePayload

log = structlog.get_logger()

router = APIRouter()

REPLAY_WINDOW_SECONDS = 300  # 5 minutes, either direction
MAX_FETCH_BYTES = 10 * 1024 * 1024  # matches api.ingest.MAX_UPLOAD_BYTES


def compute_signature(secret: str, timestamp: str, raw_body: bytes) -> str:
    """HMAC-SHA256 over "{timestamp}.{raw_body}", hex-encoded. The exact
    function docs/WEBHOOKS.md's Python example calls -- keep both in sync."""
    signing_input = f"{timestamp}.".encode() + raw_body
    return hmac.new(secret.encode(), signing_input, "sha256").hexdigest()


def verify_signature(
    raw_body: bytes,
    *,
    timestamp_header: str | None,
    signature_header: str | None,
    secret: str,
    now: datetime,
) -> None:
    """Raises HTTPException(401) on any failure. Distinct messages per
    failure mode are fine here (unlike the approval-token flow) -- knowing
    "your clock is skewed" vs. "wrong secret" is ordinary webhook-integration
    feedback, not something that helps an attacker who doesn't have the
    secret in the first place.
    """
    if not timestamp_header or not signature_header:
        raise HTTPException(status_code=401, detail="missing X-Timestamp or X-Signature header")

    try:
        timestamp_int = int(timestamp_header)
    except ValueError:
        raise HTTPException(status_code=401, detail="X-Timestamp is not a valid integer") from None

    request_time = datetime.fromtimestamp(timestamp_int, tz=UTC)
    if abs((now - request_time).total_seconds()) > REPLAY_WINDOW_SECONDS:
        raise HTTPException(
            status_code=401,
            detail=f"X-Timestamp is outside the {REPLAY_WINDOW_SECONDS}-second allowed window",
        )

    expected = compute_signature(secret, timestamp_header, raw_body)
    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="signature mismatch")


def _is_unsafe_address(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def fetch_file_url(url: str, *, client: httpx.Client | None = None) -> bytes:
    """Fetches `file_url` with basic SSRF hardening: only http(s), reject a
    hostname that resolves to any private/loopback/link-local/reserved
    address, and never follow redirects (a redirect could point somewhere
    unsafe after the pre-check already passed on the original host --
    re-validating every hop is out of scope here, so redirects are rejected
    outright instead of silently followed).

    Residual gap, stated plainly rather than glossed over: this resolves the
    hostname once to check it, then lets httpx resolve it again to connect --
    a DNS answer that changes between those two lookups (DNS rebinding) could
    still slip through. Closing that fully would mean pinning the connection
    to the specific IP already checked, which needs a custom transport; not
    implemented here.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported URL scheme: {parsed.scheme!r}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname")

    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve host: {hostname}") from exc
    for _family, _type, _proto, _canonname, sockaddr in addrinfo:
        ip_str = str(sockaddr[0])
        if _is_unsafe_address(ip_str):
            raise ValueError(f"URL resolves to a private/reserved address: {ip_str}")

    http_client = (
        client if client is not None else httpx.Client(timeout=15.0, follow_redirects=False)
    )
    response = http_client.get(url)
    if response.status_code >= 400:
        raise ValueError(f"fetch failed: HTTP {response.status_code}")
    if len(response.content) > MAX_FETCH_BYTES:
        raise ValueError(f"fetched file exceeds the {MAX_FETCH_BYTES} byte limit")
    return response.content


@router.post(
    "/v1/webhooks/invoices",
    status_code=202,
    response_model=UploadResponse,
    dependencies=[Depends(rate_limit_dependency(webhook_rate_limiter))],
)
async def webhook_invoice(
    request: Request,
    conn: Annotated[Connection, Depends(get_connection)],
    storage: Annotated[Storage, Depends(get_storage)],
    x_signature: Annotated[str | None, Header()] = None,
    x_timestamp: Annotated[str | None, Header()] = None,
) -> UploadResponse:
    raw_body = await request.body()

    secret = os.environ.get("WEBHOOK_SIGNING_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="WEBHOOK_SIGNING_SECRET is not configured")

    verify_signature(
        raw_body,
        timestamp_header=x_timestamp,
        signature_header=x_signature,
        secret=secret,
        now=datetime.now(UTC),
    )

    try:
        payload = WebhookInvoicePayload.model_validate_json(raw_body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if payload.file_base64 is not None:
        try:
            data = base64.b64decode(payload.file_base64, validate=True)
        except binascii.Error as exc:
            raise HTTPException(status_code=400, detail="file_base64 is not valid base64") from exc
    else:
        assert payload.file_url is not None
        try:
            data = fetch_file_url(payload.file_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    filename = payload.filename or (
        urlparse(payload.file_url).path.rsplit("/", 1)[-1] if payload.file_url else ""
    )

    try:
        outcome = ingest_invoice(
            conn,
            storage,
            payload.tenant_id,
            data,
            filename,
            source_channel="webhook",
            audit_action="invoice_received_via_webhook",
        )
    except FileTooLarge as exc:
        raise HTTPException(status_code=413, detail="file exceeds the 10 MB limit") from exc
    except UnsupportedFileType as exc:
        raise HTTPException(
            status_code=415, detail="unsupported file type -- must be a PDF, PNG, or JPEG"
        ) from exc
    except StorageError as exc:
        # Same reasoning as POST /v1/invoices/upload (api/main.py): a
        # distinct, mapped failure mode, real detail logged server-side only.
        log.error(
            "webhook_invoice_storage_failed", tenant_id=str(payload.tenant_id), error=str(exc)
        )
        raise HTTPException(status_code=502, detail="storage backend unavailable") from exc

    if isinstance(outcome, DuplicateInvoice):
        raise HTTPException(
            status_code=409,
            detail=DuplicateInvoiceDetail(invoice_id=outcome.invoice_id).model_dump(mode="json"),
        )

    conn.commit()
    return UploadResponse(invoice_id=outcome.invoice_id, job_id=outcome.job_id)
