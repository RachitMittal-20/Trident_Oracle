"""Integration tests for approval-token issuance/redemption -- require a
live Postgres with all migrations applied. Set both TEST_DATABASE_URL and
APPROVAL_REDEEMER_TEST_DATABASE_URL to run these; skipped otherwise, same
convention as test_upload_integration.py and test_queue_integration.py.

TEST_DATABASE_URL: an admin/superuser connection, used only for test setup
and for reading back results to assert on (things approval_redeemer
deliberately cannot do, like TRUNCATE or reading arbitrary tables).

APPROVAL_REDEEMER_TEST_DATABASE_URL: a connection authenticated as the
approval_redeemer role (db/migrations/0019_approval_redeemer_role.sql).
api.approvals is exercised through *this* role in every test here -- the
same reasoning as test_queue_integration.py using queue_claimer directly:
proving the role itself works, not an admin connection standing in for it.

The concurrent-redemption test follows test_queue_integration.py's
test_two_concurrent_workers_never_claim_same_job pattern exactly: real
threads, each with its own real psycopg connection, no mocking.
"""

import concurrent.futures
import hashlib
import os
import threading
import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
import structlog.testing
from api import approvals
from api.config import get_approval_redeemer_connection
from api.main import app
from approval_tokens import issue_approval_token
from core.errors import TokenAlreadyUsed, TokenExpired, TokenNotFound
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
APPROVAL_REDEEMER_DATABASE_URL = os.environ.get("APPROVAL_REDEEMER_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not APPROVAL_REDEEMER_DATABASE_URL,
    reason=(
        "TEST_DATABASE_URL and APPROVAL_REDEEMER_TEST_DATABASE_URL must both "
        "be set -- skipping live Postgres tests"
    ),
)


def _admin_connect() -> psycopg.Connection:
    assert TEST_DATABASE_URL is not None
    return psycopg.connect(TEST_DATABASE_URL, autocommit=False)


def _redeemer_connect() -> psycopg.Connection:
    assert APPROVAL_REDEEMER_DATABASE_URL is not None
    return psycopg.connect(APPROVAL_REDEEMER_DATABASE_URL, autocommit=False)


@pytest.fixture
def admin_conn() -> Generator[psycopg.Connection, None, None]:
    conn = _admin_connect()
    yield conn
    conn.close()


@pytest.fixture
def redeemer_conn() -> Generator[psycopg.Connection, None, None]:
    conn = _redeemer_connect()
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _clean_tables(admin_conn: psycopg.Connection) -> Generator[None, None, None]:
    with admin_conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE match_exceptions, match_runs, approval_requests, jobs, "
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


def _make_invoice(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    *,
    status: str = "PENDING_APPROVAL",
    total: str = "100.00",
) -> uuid.UUID:
    invoice_id = uuid.uuid4()
    content_hash = hashlib.sha256(f"{invoice_id}".encode()).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO invoices
                (id, tenant_id, invoice_number, invoice_date, currency, subtotal, tax, total,
                 source_channel, source_file_path, content_hash, status)
            VALUES (%s, %s, 'INV-1', %s, 'USD', %s, 0.00, %s, 'upload', 'p', %s, %s)
            """,
            (invoice_id, tenant_id, date(2026, 1, 1), total, total, content_hash, status),
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


def _issue(
    conn: psycopg.Connection, tenant_id: uuid.UUID, invoice_id: uuid.UUID, exception_id: uuid.UUID
) -> str:
    return issue_approval_token(
        conn,
        tenant_id=tenant_id,
        invoice_id=invoice_id,
        exception_id=exception_id,
        recipient="chat-1",
        channel="telegram",
        ttl=timedelta(hours=1),
    )


# --- happy path --------------------------------------------------------------


def test_approve_happy_path_transitions_invoice_and_resolves_exception(
    redeemer_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    raw_token = _issue(redeemer_conn, tenant_id, invoice_id, exception_id)

    result = approvals.redeem_approval_token(redeemer_conn, raw_token, "approved", actor=None)

    assert result.decision == "approved"
    assert result.invoice_id == invoice_id

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        assert cur.fetchone()["status"] == "APPROVED"

        cur.execute(
            "SELECT status, resolution_note FROM match_exceptions WHERE id = %s", (exception_id,)
        )
        exc_row = cur.fetchone()
        assert exc_row["status"] == "resolved"
        assert "approved" in exc_row["resolution_note"]

        cur.execute(
            "SELECT * FROM audit_log WHERE entity_id = %s AND action = 'approval_decided'",
            (invoice_id,),
        )
        audit_row = cur.fetchone()
        assert audit_row is not None
        assert audit_row["after"]["decision"] == "approved"

        cur.execute("SELECT job_type, payload FROM jobs WHERE tenant_id = %s", (tenant_id,))
        jobs = cur.fetchall()
        assert len(jobs) == 1
        assert jobs[0]["job_type"] == "post"
        assert jobs[0]["payload"]["invoice_id"] == str(invoice_id)


def test_reject_happy_path_transitions_invoice_and_dismisses_exception_no_post_job(
    redeemer_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    raw_token = _issue(redeemer_conn, tenant_id, invoice_id, exception_id)

    result = approvals.redeem_approval_token(redeemer_conn, raw_token, "rejected", actor=None)

    assert result.decision == "rejected"

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        assert cur.fetchone()["status"] == "REJECTED"

        cur.execute("SELECT status FROM match_exceptions WHERE id = %s", (exception_id,))
        assert cur.fetchone()["status"] == "dismissed"

        cur.execute("SELECT count(*) AS n FROM jobs WHERE tenant_id = %s", (tenant_id,))
        assert cur.fetchone()["n"] == 0


def test_issued_token_persists_only_the_hash(
    redeemer_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    raw_token = _issue(redeemer_conn, tenant_id, invoice_id, exception_id)

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT token_hash FROM approval_requests WHERE invoice_id = %s", (invoice_id,))
        row = cur.fetchone()
    assert row is not None
    assert row["token_hash"] != raw_token
    assert row["token_hash"] == hashlib.sha256(raw_token.encode()).hexdigest()


# --- failure modes -----------------------------------------------------------


def test_replay_of_consumed_token_fails(
    redeemer_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    raw_token = _issue(redeemer_conn, tenant_id, invoice_id, exception_id)

    approvals.redeem_approval_token(redeemer_conn, raw_token, "approved", actor=None)

    with pytest.raises(TokenAlreadyUsed):
        approvals.redeem_approval_token(redeemer_conn, raw_token, "approved", actor=None)


def test_expired_token_fails(
    redeemer_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    raw_token = _issue(redeemer_conn, tenant_id, invoice_id, exception_id)

    with admin_conn.cursor() as cur:
        cur.execute(
            "UPDATE approval_requests SET expires_at = %s WHERE invoice_id = %s",
            (datetime.now(UTC) - timedelta(hours=1), invoice_id),
        )
    admin_conn.commit()

    with pytest.raises(TokenExpired):
        approvals.redeem_approval_token(redeemer_conn, raw_token, "approved", actor=None)

    # A failed redemption must not have transitioned anything.
    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        assert cur.fetchone()["status"] == "PENDING_APPROVAL"


def test_tampered_token_fails(
    redeemer_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    raw_token = _issue(redeemer_conn, tenant_id, invoice_id, exception_id)
    tampered = raw_token[:-1] + ("A" if raw_token[-1] != "A" else "B")

    with pytest.raises(TokenNotFound):
        approvals.redeem_approval_token(redeemer_conn, tampered, "approved", actor=None)


def test_unknown_token_fails() -> None:
    conn = _redeemer_connect()
    try:
        with pytest.raises(TokenNotFound):
            approvals.redeem_approval_token(conn, "not-a-real-token-at-all", "approved", actor=None)
    finally:
        conn.close()


# --- concurrent redemption: real threads, real connections -------------------


def test_concurrent_redemption_of_the_same_token_succeeds_exactly_once(
    admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)

    setup_conn = _redeemer_connect()
    raw_token = _issue(setup_conn, tenant_id, invoice_id, exception_id)
    setup_conn.close()

    n = 10
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def attempt(_: int) -> None:
        conn = _redeemer_connect()
        try:
            try:
                approvals.redeem_approval_token(conn, raw_token, "approved", actor=None)
                outcome = "success"
            except TokenAlreadyUsed:
                outcome = "already_used"
        finally:
            conn.close()
        with outcomes_lock:
            outcomes.append(outcome)

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
        futures = [executor.submit(attempt, i) for i in range(n)]
        for future in futures:
            future.result()

    assert outcomes.count("success") == 1, "exactly one concurrent redemption must succeed"
    assert outcomes.count("already_used") == n - 1

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        assert cur.fetchone()["status"] == "APPROVED"
        cur.execute("SELECT count(*) AS n FROM jobs WHERE tenant_id = %s", (tenant_id,))
        assert cur.fetchone()["n"] == 1  # not enqueued more than once


# --- raw token never appears in logs or API responses -----------------------


def test_raw_token_never_appears_in_structlog_output_for_issue_and_redeem(
    redeemer_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)

    with structlog.testing.capture_logs() as captured:
        raw_token = _issue(redeemer_conn, tenant_id, invoice_id, exception_id)
        approvals.redeem_approval_token(redeemer_conn, raw_token, "approved", actor=None)

    rendered = repr(captured)
    assert raw_token not in rendered


def test_raw_token_never_appears_in_structlog_output_on_failure(
    redeemer_conn: psycopg.Connection, admin_conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    raw_token = _issue(redeemer_conn, tenant_id, invoice_id, exception_id)
    approvals.redeem_approval_token(redeemer_conn, raw_token, "approved", actor=None)

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(TokenAlreadyUsed):
            approvals.redeem_approval_token(redeemer_conn, raw_token, "approved", actor=None)

    assert raw_token not in repr(captured)


# --- the actual FastAPI endpoints, via TestClient ----------------------------


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    def _get_approval_redeemer_connection() -> Generator[psycopg.Connection, None, None]:
        conn = _redeemer_connect()
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_approval_redeemer_connection] = _get_approval_redeemer_connection
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_get_approval_page_renders_invoice_context(
    client: TestClient,
    redeemer_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
    tenant_id: uuid.UUID,
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id, total="3200.00")
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    raw_token = _issue(redeemer_conn, tenant_id, invoice_id, exception_id)

    response = client.get(f"/v1/approvals/{raw_token}")

    assert response.status_code == 200
    assert "INV-1" in response.text
    assert "3200.00" in response.text
    assert raw_token not in response.text


def test_post_approval_decision_redeems_and_confirms(
    client: TestClient,
    redeemer_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
    tenant_id: uuid.UUID,
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    raw_token = _issue(redeemer_conn, tenant_id, invoice_id, exception_id)

    response = client.post(f"/v1/approvals/{raw_token}", data={"decision": "approved"})

    assert response.status_code == 200
    assert "approved" in response.text
    assert raw_token not in response.text

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        assert cur.fetchone()["status"] == "APPROVED"


def test_get_approval_page_generic_message_for_every_failure_mode(
    client: TestClient,
    redeemer_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
    tenant_id: uuid.UUID,
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)

    unknown_token_response = client.get("/v1/approvals/not-a-real-token")

    consumed_raw_token = _issue(redeemer_conn, tenant_id, invoice_id, exception_id)
    approvals.redeem_approval_token(redeemer_conn, consumed_raw_token, "approved", actor=None)
    replay_response = client.get(f"/v1/approvals/{consumed_raw_token}")

    invoice_id_2 = _make_invoice(admin_conn, tenant_id)
    exception_id_2 = _make_open_exception(admin_conn, tenant_id, invoice_id_2)
    expired_raw_token = _issue(redeemer_conn, tenant_id, invoice_id_2, exception_id_2)
    with admin_conn.cursor() as cur:
        cur.execute(
            "UPDATE approval_requests SET expires_at = %s WHERE invoice_id = %s",
            (datetime.now(UTC) - timedelta(hours=1), invoice_id_2),
        )
    admin_conn.commit()
    expired_response = client.get(f"/v1/approvals/{expired_raw_token}")

    bodies = {
        unknown_token_response.text,
        replay_response.text,
        expired_response.text,
    }
    assert len(bodies) == 1, "all three failure modes must render an identical response body"
    for response in (unknown_token_response, replay_response, expired_response):
        assert response.status_code == 410


def test_post_approval_decision_invalid_decision_value_rejected(
    client: TestClient,
    redeemer_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
    tenant_id: uuid.UUID,
) -> None:
    invoice_id = _make_invoice(admin_conn, tenant_id)
    exception_id = _make_open_exception(admin_conn, tenant_id, invoice_id)
    raw_token = _issue(redeemer_conn, tenant_id, invoice_id, exception_id)

    response = client.post(f"/v1/approvals/{raw_token}", data={"decision": "maybe"})

    assert response.status_code == 400
