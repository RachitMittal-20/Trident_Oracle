"""Integration tests for apps/api/api/exceptions_view.py's resolve_exception --
require a live Postgres with all migrations applied. Set both
TEST_DATABASE_URL and APP_ROLE_TEST_DATABASE_URL to run these; skipped
otherwise, same convention as test_match_view_integration.py.

Two things this file exists to prove:

1. resolve_exception refuses to re-settle an exception that's no longer
   open -- in particular one that match_view.py::decide_invoice's
   _settle_invoice already resolved/dismissed as a side effect of a formal
   approve/reject decision. Before this file, resolve_exception's UPDATE had
   no WHERE status = 'open' guard, so a bulk-resolve call racing (or simply
   arriving late) against a decision would silently overwrite
   resolved_by/resolved_at/resolution_note -- e.g. clobbering "rejected
   in-app on the match screen" with "resolved from the exceptions queue" --
   with no error and no sign anything had gone wrong.

2. RLS (match_exceptions' tenant_isolation policy, apps/api/api/db.py's
   set_tenant()) is what actually scopes resolve_exception to one tenant --
   the function's own SELECT has no explicit tenant_id filter in its WHERE
   clause at all. A bulk-resolve call naming an exception ID that belongs to
   a different tenant must fail per-row (ExceptionNotFound, since RLS makes
   the row invisible to the SELECT), not silently succeed against someone
   else's data.
"""

import hashlib
import os
import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from api import exceptions_view, match_view
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


# --- resolving a still-open exception: the happy path ------------------------


def test_resolve_open_exception_marks_it_resolved(
    app_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id, status="EXCEPTIONS_RAISED")
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    clerk = _make_user(admin_conn, tenant_id, email="clerk@example.com", role="clerk")

    exceptions_view.resolve_exception(app_conn, tenant_id, exception_id, clerk, "looked fine")
    app_conn.commit()

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT status, resolved_by, resolution_note FROM match_exceptions WHERE id = %s",
            (exception_id,),
        )
        row = cur.fetchone()
        assert row["status"] == "resolved"
        assert row["resolved_by"] == clerk
        assert row["resolution_note"] == "looked fine"

        # Untouched by a bare exception-level resolve -- only decide_invoice
        # ever transitions the invoice itself.
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        assert cur.fetchone()["status"] == "EXCEPTIONS_RAISED"


# --- the bug this file exists to catch ---------------------------------------


def test_resolve_after_invoice_already_settled_is_rejected(
    app_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    approver = _make_user(admin_conn, tenant_id, email="approver@example.com")
    _make_approval_request(admin_conn, tenant_id, invoice_id, recipient="approver@example.com")
    clerk = _make_user(admin_conn, tenant_id, email="clerk@example.com", role="clerk")

    # decide_invoice settles the invoice (APPROVED) and, as a side effect,
    # resolves every open exception on it via _settle_invoice.
    match_view.decide_invoice(app_conn, tenant_id, invoice_id, "approved", approver)
    app_conn.commit()

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT status, resolved_by, resolution_note FROM match_exceptions WHERE id = %s",
            (exception_id,),
        )
        settled = cur.fetchone()
        assert settled["status"] == "resolved"
        assert settled["resolved_by"] == approver
        assert settled["resolution_note"] == "approved in-app on the match screen"

    # A late (or racing) bulk-resolve from the /exceptions queue must be
    # rejected outright, not silently re-write the decision's own record.
    with pytest.raises(exceptions_view.ExceptionAlreadySettled):
        exceptions_view.resolve_exception(app_conn, tenant_id, exception_id, clerk, "clerk note")
    app_conn.rollback()

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT status, resolved_by, resolution_note FROM match_exceptions WHERE id = %s",
            (exception_id,),
        )
        unchanged = cur.fetchone()
        assert unchanged["resolved_by"] == approver
        assert unchanged["resolution_note"] == "approved in-app on the match screen"


def test_resolve_already_resolved_exception_twice_is_rejected(
    app_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id, status="EXCEPTIONS_RAISED")
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    clerk_1 = _make_user(admin_conn, tenant_id, email="clerk1@example.com", role="clerk")
    clerk_2 = _make_user(admin_conn, tenant_id, email="clerk2@example.com", role="clerk")

    exceptions_view.resolve_exception(app_conn, tenant_id, exception_id, clerk_1, None)
    app_conn.commit()

    with pytest.raises(exceptions_view.ExceptionAlreadySettled):
        exceptions_view.resolve_exception(app_conn, tenant_id, exception_id, clerk_2, None)


# --- tenant scoping: RLS, not client-side filtering, does the real work -----


def test_resolve_exception_from_another_tenant_is_not_found(
    admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id, status="EXCEPTIONS_RAISED")
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)

    other_tenant_id = uuid.uuid4()
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s)",
            (other_tenant_id, "Other Tenant", f"other-{other_tenant_id}"),
        )
    admin_conn.commit()
    other_user = _make_user(admin_conn, other_tenant_id, email="clerk@other.example.com")

    other_conn = _app_role_connect()
    set_tenant(other_conn, other_tenant_id)
    try:
        with pytest.raises(exceptions_view.ExceptionNotFound):
            exceptions_view.resolve_exception(
                other_conn, other_tenant_id, exception_id, other_user, None
            )
    finally:
        other_conn.close()

    # Untouched -- the cross-tenant call must fail per-row, not silently
    # succeed or no-op against the real tenant's data.
    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM match_exceptions WHERE id = %s", (exception_id,))
        assert cur.fetchone()["status"] == "open"
