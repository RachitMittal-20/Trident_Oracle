"""Integration tests for POST /v1/invoices/upload and GET /v1/invoices/{id}
-- require a live Postgres with all migrations applied.

TEST_DATABASE_URL: an admin/superuser connection, used only for test setup
(creating tenant rows, truncating tables) -- a superuser bypasses RLS
entirely, so it must never be the connection the API itself uses, or a
tenant-isolation test would pass for the wrong reason (no RLS enforcement
happened at all, the superuser just doesn't have any to bypass in the
first place).

APP_ROLE_TEST_DATABASE_URL: a connection authenticated as app_role
(db/migrations/0013_app_role.sql). This is what api.config.get_connection
is overridden to return for the TestClient -- exercising the actual
RLS-restricted role the API runs as in production, not a stand-in that
happens to see everything regardless of policy.

Both must be set to run these; skipped otherwise, same convention as
apps/worker's integration tests.

Storage is overridden to MemoryStorage via FastAPI's dependency_overrides --
no real Supabase bucket is touched.
"""

import hashlib
import os
import uuid
from collections.abc import Generator

import psycopg
import pytest
from api.config import get_connection, get_storage
from api.main import app
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

PDF_BYTES = b"%PDF-1.4\n%fake pdf content for testing\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfakepngbytes"


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


# --- upload ---------------------------------------------------------------


def test_upload_returns_202_with_invoice_and_job_id(
    client: TestClient, tenant_id: uuid.UUID
) -> None:
    response = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_id)},
        files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
    )

    assert response.status_code == 202
    body = response.json()
    assert uuid.UUID(body["invoice_id"])
    assert uuid.UUID(body["job_id"])


def test_upload_creates_invoice_row_at_received(
    client: TestClient, tenant_id: uuid.UUID, admin_conn: psycopg.Connection
) -> None:
    response = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_id)},
        files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
    )
    invoice_id = response.json()["invoice_id"]

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
        invoice = cur.fetchone()
    assert invoice is not None
    assert invoice["status"] == "RECEIVED"
    assert invoice["tenant_id"] == tenant_id
    assert invoice["content_hash"] == hashlib.sha256(PDF_BYTES).hexdigest()


def test_upload_enqueues_extract_job_with_correct_idempotency_key(
    client: TestClient, tenant_id: uuid.UUID, admin_conn: psycopg.Connection
) -> None:
    response = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_id)},
        files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
    )
    job_id = response.json()["job_id"]
    content_hash = hashlib.sha256(PDF_BYTES).hexdigest()
    expected_key = hashlib.sha256(f"{tenant_id}{content_hash}".encode()).hexdigest()

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        job = cur.fetchone()
    assert job is not None
    assert job["job_type"] == "extract"
    assert job["status"] == "queued"
    assert job["idempotency_key"] == expected_key
    assert job["payload"]["invoice_id"] == response.json()["invoice_id"]


def test_upload_writes_audit_log_entry(
    client: TestClient, tenant_id: uuid.UUID, admin_conn: psycopg.Connection
) -> None:
    response = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_id)},
        files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
    )
    invoice_id = response.json()["invoice_id"]

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM audit_log WHERE entity_id = %s", (invoice_id,))
        entries = cur.fetchall()
    assert len(entries) == 1
    assert entries[0]["action"] == "invoice_uploaded"
    assert entries[0]["entity_type"] == "invoice"


def test_upload_stores_file_in_storage(
    client: TestClient, tenant_id: uuid.UUID, memory_storage: MemoryStorage
) -> None:
    response = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_id)},
        files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
    )
    invoice_id = response.json()["invoice_id"]

    expected_path = f"{tenant_id}/{invoice_id}/invoice.pdf"
    assert memory_storage.download(expected_path) == PDF_BYTES


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("invoice.pdf", PDF_BYTES, "application/pdf"),
        ("invoice.png", PNG_BYTES, "image/png"),
    ],
)
def test_upload_accepts_all_allowed_formats(
    client: TestClient,
    tenant_id: uuid.UUID,
    filename: str,
    content: bytes,
    content_type: str,
) -> None:
    response = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_id)},
        files={"file": (filename, content, content_type)},
    )
    assert response.status_code == 202


def test_upload_rejects_disguised_file_by_magic_bytes(
    client: TestClient, tenant_id: uuid.UUID
) -> None:
    # Named and content-typed as a PDF, but the actual bytes are not --
    # magic-byte sniffing must catch this, not trust the filename/header.
    response = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_id)},
        files={"file": ("invoice.pdf", b"not actually a pdf", "application/pdf")},
    )
    assert response.status_code == 415


def test_upload_rejects_oversized_file(client: TestClient, tenant_id: uuid.UUID) -> None:
    oversized = b"%PDF-1.4\n" + b"0" * (10 * 1024 * 1024 + 1)
    response = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_id)},
        files={"file": ("big.pdf", oversized, "application/pdf")},
    )
    assert response.status_code == 413


def test_duplicate_upload_returns_409_with_existing_invoice_id(
    client: TestClient, tenant_id: uuid.UUID, admin_conn: psycopg.Connection
) -> None:
    first = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_id)},
        files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
    )
    assert first.status_code == 202
    first_invoice_id = first.json()["invoice_id"]

    second = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_id)},
        files={"file": ("invoice-again.pdf", PDF_BYTES, "application/pdf")},
    )

    assert second.status_code == 409
    assert second.json()["detail"]["invoice_id"] == first_invoice_id

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM invoices WHERE tenant_id = %s AND content_hash = %s",
            (tenant_id, hashlib.sha256(PDF_BYTES).hexdigest()),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 1  # never duplicated


def test_duplicate_upload_does_not_enqueue_a_second_job(
    client: TestClient, tenant_id: uuid.UUID, admin_conn: psycopg.Connection
) -> None:
    client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_id)},
        files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
    )
    client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_id)},
        files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
    )

    with admin_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM jobs WHERE tenant_id = %s", (tenant_id,))
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 1


def test_different_tenants_can_upload_the_same_file(
    client: TestClient, admin_conn: psycopg.Connection
) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, 'A', %s), (%s, 'B', %s)",
            (tenant_a, f"a-{tenant_a}", tenant_b, f"b-{tenant_b}"),
        )
    admin_conn.commit()

    first = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_a)},
        files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
    )
    second = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_b)},
        files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
    )

    # Hard-duplicate detection is per-tenant, not global -- the unique
    # constraint is on (tenant_id, content_hash).
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["invoice_id"] != second.json()["invoice_id"]


# --- get -------------------------------------------------------------------


def test_get_invoice_returns_full_invoice_with_signed_url(
    client: TestClient, tenant_id: uuid.UUID
) -> None:
    upload = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_id)},
        files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
    )
    invoice_id = upload.json()["invoice_id"]

    response = client.get(f"/v1/invoices/{invoice_id}", params={"tenant_id": str(tenant_id)})

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == invoice_id
    assert body["status"] == "RECEIVED"
    assert body["file_url"].startswith("memory://")
    assert body["lines"] == []
    assert body["field_confidences"] == []


def test_get_invoice_not_found_returns_404(client: TestClient, tenant_id: uuid.UUID) -> None:
    response = client.get(
        f"/v1/invoices/{uuid.uuid4()}", params={"tenant_id": str(tenant_id)}
    )
    assert response.status_code == 404


def test_get_invoice_from_wrong_tenant_returns_404_not_leaked(
    client: TestClient, admin_conn: psycopg.Connection
) -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, 'A', %s), (%s, 'B', %s)",
            (tenant_a, f"a-{tenant_a}", tenant_b, f"b-{tenant_b}"),
        )
    admin_conn.commit()

    upload = client.post(
        "/v1/invoices/upload",
        data={"tenant_id": str(tenant_a)},
        files={"file": ("invoice.pdf", PDF_BYTES, "application/pdf")},
    )
    invoice_id = upload.json()["invoice_id"]

    # tenant_b asking for tenant_a's invoice -- RLS must hide it, not just
    # application-level filtering.
    response = client.get(f"/v1/invoices/{invoice_id}", params={"tenant_id": str(tenant_b)})
    assert response.status_code == 404
