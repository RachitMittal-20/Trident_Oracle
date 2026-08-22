"""Integration tests for GET /v1/deliveries -- require a live Postgres with
all migrations applied. Set both TEST_DATABASE_URL and
APP_ROLE_TEST_DATABASE_URL to run these; skipped otherwise, same convention
as test_upload_integration.py.
"""

import hashlib
import os
import uuid
from collections.abc import Generator
from datetime import date

import psycopg
import pytest
from api.config import get_connection
from api.main import app
from fastapi.testclient import TestClient

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
APP_ROLE_DATABASE_URL = os.environ.get("APP_ROLE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not APP_ROLE_DATABASE_URL,
    reason=(
        "TEST_DATABASE_URL and APP_ROLE_TEST_DATABASE_URL must both be set "
        "-- skipping live Postgres tests"
    ),
)


@pytest.fixture
def admin_conn() -> Generator[psycopg.Connection, None, None]:
    assert TEST_DATABASE_URL is not None
    conn = psycopg.connect(TEST_DATABASE_URL, autocommit=False)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _clean_tables(admin_conn: psycopg.Connection) -> Generator[None, None, None]:
    with admin_conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE notification_deliveries, invoices, tenants CASCADE")
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
def invoice_id(admin_conn: psycopg.Connection, tenant_id: uuid.UUID) -> uuid.UUID:
    iid = uuid.uuid4()
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO invoices
                (id, tenant_id, invoice_number, invoice_date, currency, subtotal, tax, total,
                 source_channel, source_file_path, content_hash, status)
            VALUES (%s, %s, 'INV-1', %s, 'USD', 100.00, 0.00, 100.00, 'upload', 'p', %s,
                    'PENDING_APPROVAL')
            """,
            (iid, tenant_id, date(2026, 1, 1), hashlib.sha256(str(iid).encode()).hexdigest()),
        )
    admin_conn.commit()
    return iid


def _make_delivery(
    admin_conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    *,
    channel: str = "telegram",
    status: str = "pending",
    recipient: str = "chat-1",
) -> uuid.UUID:
    did = uuid.uuid4()
    idempotency_key = hashlib.sha256(f"{did}".encode()).hexdigest()
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO notification_deliveries
                (id, tenant_id, invoice_id, channel, recipient, idempotency_key, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (did, tenant_id, invoice_id, channel, recipient, idempotency_key, status),
        )
    admin_conn.commit()
    return did


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    assert APP_ROLE_DATABASE_URL is not None

    def _get_connection() -> Generator[psycopg.Connection, None, None]:
        conn = psycopg.connect(APP_ROLE_DATABASE_URL)  # type: ignore[arg-type]
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_connection] = _get_connection
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_lists_all_deliveries_for_the_tenant(
    client: TestClient, admin_conn: psycopg.Connection, tenant_id: uuid.UUID, invoice_id: uuid.UUID
) -> None:
    _make_delivery(admin_conn, tenant_id, invoice_id, status="sent")
    _make_delivery(admin_conn, tenant_id, invoice_id, status="pending")

    response = client.get("/v1/deliveries", params={"tenant_id": str(tenant_id)})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2


def test_filters_by_status(
    client: TestClient, admin_conn: psycopg.Connection, tenant_id: uuid.UUID, invoice_id: uuid.UUID
) -> None:
    _make_delivery(admin_conn, tenant_id, invoice_id, status="sent")
    dead_id = _make_delivery(admin_conn, tenant_id, invoice_id, status="dead")

    response = client.get(
        "/v1/deliveries", params={"tenant_id": str(tenant_id), "status": "dead"}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(dead_id)
    assert body[0]["status"] == "dead"


def test_filters_by_channel(
    client: TestClient, admin_conn: psycopg.Connection, tenant_id: uuid.UUID, invoice_id: uuid.UUID
) -> None:
    _make_delivery(admin_conn, tenant_id, invoice_id, channel="telegram")
    _make_delivery(admin_conn, tenant_id, invoice_id, channel="email")

    response = client.get(
        "/v1/deliveries", params={"tenant_id": str(tenant_id), "channel": "email"}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["channel"] == "email"


def test_filters_by_invoice_id(
    client: TestClient, admin_conn: psycopg.Connection, tenant_id: uuid.UUID, invoice_id: uuid.UUID
) -> None:
    _make_delivery(admin_conn, tenant_id, invoice_id)

    with admin_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO invoices
                (id, tenant_id, invoice_number, invoice_date, currency, subtotal, tax, total,
                 source_channel, source_file_path, content_hash, status)
            VALUES (%s, %s, 'INV-2', %s, 'USD', 50.00, 0.00, 50.00, 'upload', 'p', %s,
                    'PENDING_APPROVAL')
            """,
            (
                (other_invoice_id := uuid.uuid4()),
                tenant_id,
                date(2026, 1, 2),
                hashlib.sha256(str(other_invoice_id).encode()).hexdigest(),
            ),
        )
    admin_conn.commit()
    _make_delivery(admin_conn, tenant_id, other_invoice_id)

    response = client.get(
        "/v1/deliveries", params={"tenant_id": str(tenant_id), "invoice_id": str(invoice_id)}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["invoice_id"] == str(invoice_id)


def test_invalid_status_value_is_rejected(client: TestClient, tenant_id: uuid.UUID) -> None:
    response = client.get(
        "/v1/deliveries", params={"tenant_id": str(tenant_id), "status": "not-a-real-status"}
    )
    assert response.status_code == 422


def test_other_tenants_deliveries_are_not_visible(
    client: TestClient, admin_conn: psycopg.Connection, tenant_id: uuid.UUID, invoice_id: uuid.UUID
) -> None:
    _make_delivery(admin_conn, tenant_id, invoice_id)

    other_tenant_id = uuid.uuid4()
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s)",
            (other_tenant_id, "Other Tenant", f"other-{other_tenant_id}"),
        )
    admin_conn.commit()

    response = client.get("/v1/deliveries", params={"tenant_id": str(other_tenant_id)})

    assert response.status_code == 200
    assert response.json() == []
