"""Integration tests for POST /v1/webhooks/invoices -- require a live
Postgres with all migrations applied. Set both TEST_DATABASE_URL and
APP_ROLE_TEST_DATABASE_URL to run these; skipped otherwise, same convention
as test_upload_integration.py (which this endpoint shares its ingestion
path with).
"""

import base64
import hashlib
import json
import os
import time
import uuid
from collections.abc import Generator

import psycopg
import pytest
from api import webhooks
from api.config import get_connection, get_storage
from api.main import app
from api.webhooks import compute_signature
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from storage.memory import MemoryStorage

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
APP_ROLE_DATABASE_URL = os.environ.get("APP_ROLE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not APP_ROLE_DATABASE_URL,
    reason=(
        "TEST_DATABASE_URL and APP_ROLE_TEST_DATABASE_URL must both be set "
        "-- skipping live Postgres tests"
    ),
)

SECRET = "test-webhook-secret"
PDF_BYTES = b"%PDF-1.4\n%fake pdf content for testing\n"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRET", SECRET)


@pytest.fixture
def admin_conn() -> Generator[psycopg.Connection, None, None]:
    assert TEST_DATABASE_URL is not None
    conn = psycopg.connect(TEST_DATABASE_URL, autocommit=False)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _clean_tables(admin_conn: psycopg.Connection) -> Generator[None, None, None]:
    with admin_conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE audit_log, jobs, invoices, tenants CASCADE")
    admin_conn.commit()
    yield


@pytest.fixture
def tenant_id(admin_conn: psycopg.Connection) -> uuid.UUID:
    tid = uuid.uuid4()
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s)",
            (tid, "Test Tenant", f"test-{tid}"),
        )
    admin_conn.commit()
    return tid


@pytest.fixture
def memory_storage() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
def client(memory_storage: MemoryStorage) -> Generator[TestClient, None, None]:
    assert APP_ROLE_DATABASE_URL is not None

    def _get_connection() -> Generator[psycopg.Connection, None, None]:
        conn = psycopg.connect(APP_ROLE_DATABASE_URL)  # type: ignore[arg-type]
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_connection] = _get_connection
    app.dependency_overrides[get_storage] = lambda: memory_storage
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def _signed_headers(body: bytes, *, ts: str | None = None) -> dict[str, str]:
    timestamp = ts or str(int(time.time()))
    return {
        "X-Timestamp": timestamp,
        "X-Signature": compute_signature(SECRET, timestamp, body),
        "Content-Type": "application/json",
    }


def _base64_payload(tenant_id: uuid.UUID, data: bytes = PDF_BYTES) -> bytes:
    return json.dumps(
        {
            "tenant_id": str(tenant_id),
            "filename": "invoice.pdf",
            "file_base64": base64.b64encode(data).decode(),
        }
    ).encode()


# --- valid signature accepted -------------------------------------------


def test_valid_signature_with_base64_payload_returns_202(
    client: TestClient, tenant_id: uuid.UUID, admin_conn: psycopg.Connection
) -> None:
    body = _base64_payload(tenant_id)
    response = client.post("/v1/webhooks/invoices", content=body, headers=_signed_headers(body))

    assert response.status_code == 202
    result = response.json()
    assert uuid.UUID(result["invoice_id"])
    assert uuid.UUID(result["job_id"])

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM invoices WHERE id = %s", (result["invoice_id"],))
        invoice = cur.fetchone()
    assert invoice is not None
    assert invoice["source_channel"] == "webhook"
    assert invoice["content_hash"] == hashlib.sha256(PDF_BYTES).hexdigest()

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (result["job_id"],))
        job = cur.fetchone()
    assert job is not None
    assert job["job_type"] == "extract"


def test_valid_signature_writes_webhook_specific_audit_action(
    client: TestClient, tenant_id: uuid.UUID, admin_conn: psycopg.Connection
) -> None:
    body = _base64_payload(tenant_id)
    response = client.post("/v1/webhooks/invoices", content=body, headers=_signed_headers(body))
    invoice_id = response.json()["invoice_id"]

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM audit_log WHERE entity_id = %s", (invoice_id,))
        entry = cur.fetchone()
    assert entry is not None
    assert entry["action"] == "invoice_received_via_webhook"
    assert entry["actor_type"] == "system"


# --- wrong signature rejected -------------------------------------------


def test_wrong_signature_is_rejected(client: TestClient, tenant_id: uuid.UUID) -> None:
    body = _base64_payload(tenant_id)
    headers = _signed_headers(body)
    headers["X-Signature"] = "0" * 64

    response = client.post("/v1/webhooks/invoices", content=body, headers=headers)
    assert response.status_code == 401


# --- stale timestamp rejected --------------------------------------------


def test_stale_timestamp_is_rejected(client: TestClient, tenant_id: uuid.UUID) -> None:
    body = _base64_payload(tenant_id)
    old_ts = str(int(time.time()) - 3600)
    response = client.post(
        "/v1/webhooks/invoices", content=body, headers=_signed_headers(body, ts=old_ts)
    )
    assert response.status_code == 401


# --- body tampering rejected ----------------------------------------------


def test_tampered_body_is_rejected(client: TestClient, tenant_id: uuid.UUID) -> None:
    original_body = _base64_payload(tenant_id)
    headers = _signed_headers(original_body)  # signed the ORIGINAL body

    tampered_body = _base64_payload(tenant_id, data=PDF_BYTES + b"tampered")
    response = client.post("/v1/webhooks/invoices", content=tampered_body, headers=headers)
    assert response.status_code == 401


# --- payload validation ----------------------------------------------------


def test_neither_file_base64_nor_file_url_is_rejected(
    client: TestClient, tenant_id: uuid.UUID
) -> None:
    body = json.dumps({"tenant_id": str(tenant_id)}).encode()
    response = client.post("/v1/webhooks/invoices", content=body, headers=_signed_headers(body))
    assert response.status_code == 422


def test_both_file_base64_and_file_url_is_rejected(
    client: TestClient, tenant_id: uuid.UUID
) -> None:
    body = json.dumps(
        {
            "tenant_id": str(tenant_id),
            "file_base64": base64.b64encode(PDF_BYTES).decode(),
            "file_url": "https://example.com/invoice.pdf",
        }
    ).encode()
    response = client.post("/v1/webhooks/invoices", content=body, headers=_signed_headers(body))
    assert response.status_code == 422


def test_invalid_base64_is_rejected(client: TestClient, tenant_id: uuid.UUID) -> None:
    body = json.dumps({"tenant_id": str(tenant_id), "file_base64": "not-valid-base64!!!"}).encode()
    response = client.post("/v1/webhooks/invoices", content=body, headers=_signed_headers(body))
    assert response.status_code == 400


# --- content-hash dedupe path, same as upload -------------------------------


def test_duplicate_content_hash_returns_409(
    client: TestClient, tenant_id: uuid.UUID, admin_conn: psycopg.Connection
) -> None:
    first_body = _base64_payload(tenant_id)
    first = client.post(
        "/v1/webhooks/invoices", content=first_body, headers=_signed_headers(first_body)
    )
    assert first.status_code == 202

    second_body = _base64_payload(tenant_id)  # identical bytes -- new signed request
    second = client.post(
        "/v1/webhooks/invoices", content=second_body, headers=_signed_headers(second_body)
    )
    assert second.status_code == 409
    assert second.json()["detail"]["invoice_id"] == first.json()["invoice_id"]

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM invoices WHERE tenant_id = %s AND content_hash = %s",
            (tenant_id, hashlib.sha256(PDF_BYTES).hexdigest()),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 1


# --- file_url path (fetch_file_url monkeypatched -- no real network) -------


def test_file_url_payload_is_fetched_and_ingested(
    client: TestClient,
    tenant_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    admin_conn: psycopg.Connection,
) -> None:
    monkeypatch.setattr(webhooks, "fetch_file_url", lambda url: PDF_BYTES)

    body = json.dumps(
        {"tenant_id": str(tenant_id), "file_url": "https://vendor.example/invoices/1.pdf"}
    ).encode()
    response = client.post("/v1/webhooks/invoices", content=body, headers=_signed_headers(body))

    assert response.status_code == 202
    invoice_id = response.json()["invoice_id"]
    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT content_hash FROM invoices WHERE id = %s", (invoice_id,))
        invoice = cur.fetchone()
    assert invoice is not None
    assert invoice["content_hash"] == hashlib.sha256(PDF_BYTES).hexdigest()
