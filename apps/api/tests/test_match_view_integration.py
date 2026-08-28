"""Integration tests for apps/api/api/match_view.py's decide_invoice --
require a live Postgres with all migrations applied. Set both
TEST_DATABASE_URL and APP_ROLE_TEST_DATABASE_URL to run these; skipped
otherwise, same convention as test_upload_integration.py.

The dual-approval tests are the point of this file: decide_invoice must
settle exactly one approver's own approval_requests row per call, and the
invoice must only transition once every row required by
Decision.required_approvers has independently been marked approved -- not
on the first caller's decision. See match_view.py's module docstring for
the full reasoning.
"""

import hashlib
import os
import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from api import match_view
from api.db import set_tenant
from psycopg.rows import dict_row

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
APP_ROLE_DATABASE_URL = os.environ.get("APP_ROLE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not APP_ROLE_DATABASE_URL,
    reason=(
        "TEST_DATABASE_URL and APP_ROLE_TEST_DATABASE_URL must both be set "
        "-- skipping live Postgres tests"
    ),
)


def _admin_connect() -> psycopg.Connection:
    assert TEST_DATABASE_URL is not None
    return psycopg.connect(TEST_DATABASE_URL, autocommit=False)


def _app_role_connect() -> psycopg.Connection:
    assert APP_ROLE_DATABASE_URL is not None
    return psycopg.connect(APP_ROLE_DATABASE_URL, autocommit=False)


@pytest.fixture
def admin_conn() -> Generator[psycopg.Connection, None, None]:
    conn = _admin_connect()
    yield conn
    conn.close()


@pytest.fixture
def app_conn(tenant_id: uuid.UUID) -> Generator[psycopg.Connection, None, None]:
    conn = _app_role_connect()
    set_tenant(conn, tenant_id)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _clean_tables(admin_conn: psycopg.Connection) -> Generator[None, None, None]:
    with admin_conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE match_exceptions, match_runs, approval_requests, users, "
            "audit_log, invoices, tenants CASCADE"
        )
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


def _make_user(
    conn: psycopg.Connection, tenant_id: uuid.UUID, *, email: str, role: str = "approver"
) -> uuid.UUID:
    user_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, tenant_id, email, role) VALUES (%s, %s, %s, %s)",
            (user_id, tenant_id, email, role),
        )
    conn.commit()
    return user_id


def _make_invoice(
    conn: psycopg.Connection, tenant_id: uuid.UUID, *, status: str = "PENDING_APPROVAL"
) -> uuid.UUID:
    invoice_id = uuid.uuid4()
    content_hash = hashlib.sha256(f"{invoice_id}".encode()).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO invoices
                (id, tenant_id, invoice_number, invoice_date, currency, subtotal, tax, total,
                 source_channel, source_file_path, content_hash, status)
            VALUES (%s, %s, 'INV-1', %s, 'USD', 100.00, 0.00, 100.00,
                    'upload', 'p', %s, %s)
            """,
            (invoice_id, tenant_id, date(2026, 1, 1), content_hash, status),
        )
    conn.commit()
    return invoice_id


def _make_open_exception(
    conn: psycopg.Connection, tenant_id: uuid.UUID, invoice_id: uuid.UUID
) -> uuid.UUID:
    match_run_id = uuid.uuid4()
    exception_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO match_runs (id, tenant_id, invoice_id, policy_version, result, duration_ms)
            VALUES (%s, %s, %s, 1, 'blocked', 0)
            """,
            (match_run_id, tenant_id, invoice_id),
        )
        cur.execute(
            """
            INSERT INTO match_exceptions
                (id, tenant_id, match_run_id, invoice_id, exception_type, severity, detail)
            VALUES (%s, %s, %s, %s, 'QTY_OVER', 'block', 'test exception')
            """,
            (exception_id, tenant_id, match_run_id, invoice_id),
        )
    conn.commit()
    return exception_id


def _make_approval_request(
    conn: psycopg.Connection, tenant_id: uuid.UUID, invoice_id: uuid.UUID, *, recipient: str
) -> uuid.UUID:
    request_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO approval_requests
                (id, tenant_id, invoice_id, token_hash, channel, recipient, expires_at)
            VALUES (%s, %s, %s, %s, 'email', %s, %s)
            """,
            (
                request_id,
                tenant_id,
                invoice_id,
                hashlib.sha256(f"{request_id}".encode()).hexdigest(),
                recipient,
                datetime.now(UTC) + timedelta(days=1),
            ),
        )
    conn.commit()
    return request_id


# --- dual approval: the point of this file ----------------------------------


def test_one_of_two_approvals_leaves_invoice_pending(
    app_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    approver_1 = _make_user(admin_conn, tenant_id, email="approver1@example.com")
    _make_user(admin_conn, tenant_id, email="approver2@example.com")
    _make_approval_request(admin_conn, tenant_id, invoice_id, recipient="approver1@example.com")
    _make_approval_request(admin_conn, tenant_id, invoice_id, recipient="approver2@example.com")

    result = match_view.decide_invoice(app_conn, tenant_id, invoice_id, "approved", approver_1)
    app_conn.commit()

    assert result.status == "pending"
    assert result.approvals_received == 1
    assert result.approvals_required == 2

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        assert cur.fetchone()["status"] == "PENDING_APPROVAL"

        cur.execute("SELECT status FROM match_exceptions WHERE id = %s", (exception_id,))
        assert cur.fetchone()["status"] == "open"

        cur.execute(
            "SELECT recipient, consumed_at, decision FROM approval_requests "
            "WHERE invoice_id = %s ORDER BY recipient",
            (invoice_id,),
        )
        rows = {row["recipient"]: row for row in cur.fetchall()}
        assert rows["approver1@example.com"]["consumed_at"] is not None
        assert rows["approver1@example.com"]["decision"] == "approved"
        assert rows["approver2@example.com"]["consumed_at"] is None
        assert rows["approver2@example.com"]["decision"] is None


def test_second_of_two_approvals_settles_invoice(
    app_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    approver_1 = _make_user(admin_conn, tenant_id, email="approver1@example.com")
    approver_2 = _make_user(admin_conn, tenant_id, email="approver2@example.com")
    _make_approval_request(admin_conn, tenant_id, invoice_id, recipient="approver1@example.com")
    _make_approval_request(admin_conn, tenant_id, invoice_id, recipient="approver2@example.com")

    match_view.decide_invoice(app_conn, tenant_id, invoice_id, "approved", approver_1)
    app_conn.commit()
    result = match_view.decide_invoice(app_conn, tenant_id, invoice_id, "approved", approver_2)
    app_conn.commit()

    assert result.status == "approved"
    assert result.approvals_received == 2
    assert result.approvals_required == 2

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        assert cur.fetchone()["status"] == "APPROVED"

        cur.execute("SELECT status FROM match_exceptions WHERE id = %s", (exception_id,))
        assert cur.fetchone()["status"] == "resolved"


def test_same_approver_cannot_decide_twice(
    app_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    _make_open_exception(admin_conn, tenant_id, invoice_id)
    approver_1 = _make_user(admin_conn, tenant_id, email="approver1@example.com")
    _make_user(admin_conn, tenant_id, email="approver2@example.com")
    _make_approval_request(admin_conn, tenant_id, invoice_id, recipient="approver1@example.com")
    _make_approval_request(admin_conn, tenant_id, invoice_id, recipient="approver2@example.com")

    match_view.decide_invoice(app_conn, tenant_id, invoice_id, "approved", approver_1)
    app_conn.commit()

    with pytest.raises(match_view.NoPendingApprovalForActor):
        match_view.decide_invoice(app_conn, tenant_id, invoice_id, "approved", approver_1)


def test_uninvited_approver_cannot_decide(
    app_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    _make_open_exception(admin_conn, tenant_id, invoice_id)
    _make_user(admin_conn, tenant_id, email="approver1@example.com")
    outsider = _make_user(admin_conn, tenant_id, email="outsider@example.com")
    _make_approval_request(admin_conn, tenant_id, invoice_id, recipient="approver1@example.com")

    with pytest.raises(match_view.NoPendingApprovalForActor):
        match_view.decide_invoice(app_conn, tenant_id, invoice_id, "approved", outsider)


def test_one_reject_settles_invoice_and_closes_the_other_open_request(
    app_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    approver_1 = _make_user(admin_conn, tenant_id, email="approver1@example.com")
    _make_user(admin_conn, tenant_id, email="approver2@example.com")
    _make_approval_request(admin_conn, tenant_id, invoice_id, recipient="approver1@example.com")
    _make_approval_request(admin_conn, tenant_id, invoice_id, recipient="approver2@example.com")

    result = match_view.decide_invoice(app_conn, tenant_id, invoice_id, "rejected", approver_1)
    app_conn.commit()

    assert result.status == "rejected"

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        assert cur.fetchone()["status"] == "REJECTED"

        cur.execute("SELECT status FROM match_exceptions WHERE id = %s", (exception_id,))
        assert cur.fetchone()["status"] == "dismissed"

        cur.execute(
            "SELECT recipient, consumed_at, decided_by FROM approval_requests "
            "WHERE invoice_id = %s ORDER BY recipient",
            (invoice_id,),
        )
        rows = {row["recipient"]: row for row in cur.fetchall()}
        assert rows["approver1@example.com"]["consumed_at"] is not None
        assert rows["approver1@example.com"]["decided_by"] == approver_1
        # Closed out as moot, but never personally decided by anyone.
        assert rows["approver2@example.com"]["consumed_at"] is not None
        assert rows["approver2@example.com"]["decided_by"] is None
