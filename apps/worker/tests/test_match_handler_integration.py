"""Integration tests for the 'match' handler -- require a live Postgres with
all migrations applied. Set both TEST_DATABASE_URL and
QUEUE_CLAIMER_TEST_DATABASE_URL to run these; skipped otherwise, same
convention as test_extract_handler_integration.py.
"""

import hashlib
import os
import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime

import psycopg
import pytest
from core.models import InvoiceStatus, JobStatus, JobType
from core.queue.models import Job
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from worker.db import JobQueue
from worker.match_handler import handle_match

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
QUEUE_CLAIMER_DATABASE_URL = os.environ.get("QUEUE_CLAIMER_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not QUEUE_CLAIMER_DATABASE_URL,
    reason=(
        "TEST_DATABASE_URL and QUEUE_CLAIMER_TEST_DATABASE_URL must both be "
        "set -- skipping live Postgres tests"
    ),
)

VALID_RULES = {
    "price_variance_pct": 2.0,
    "qty_tolerance_pct": 0.0,
    "auto_approve_below": 5000,
    "dual_approval_above": 100000,
    "min_field_confidence": 0.85,
    "duplicate_window_days": 90,
}


@pytest.fixture
def admin_conn() -> Generator[psycopg.Connection, None, None]:
    assert TEST_DATABASE_URL is not None
    conn = psycopg.connect(TEST_DATABASE_URL, autocommit=False)
    yield conn
    conn.close()


@pytest.fixture
def handler_conn() -> Generator[psycopg.Connection, None, None]:
    assert TEST_DATABASE_URL is not None
    conn = psycopg.connect(TEST_DATABASE_URL, autocommit=False)
    yield conn
    conn.close()


@pytest.fixture
def queue_conn() -> Generator[psycopg.Connection, None, None]:
    assert QUEUE_CLAIMER_DATABASE_URL is not None
    conn = psycopg.connect(QUEUE_CLAIMER_DATABASE_URL, autocommit=False)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _clean_tables(admin_conn: psycopg.Connection) -> Generator[None, None, None]:
    with admin_conn.cursor() as cur:
        cur.execute(
            "TRUNCATE TABLE match_exceptions, match_runs, field_confidences, invoice_lines, "
            "goods_receipt_lines, goods_receipts, purchase_order_lines, purchase_orders, "
            "tolerance_policies, vendors, users, audit_log, jobs, invoices, tenants CASCADE"
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


def _make_policy(conn: psycopg.Connection, tenant_id: uuid.UUID, **rule_overrides: object) -> None:
    rules = dict(VALID_RULES)
    rules.update(rule_overrides)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tolerance_policies (tenant_id, name, is_active, rules, version) "
            "VALUES (%s, 'default', true, %s, 1)",
            (tenant_id, Jsonb(rules)),
        )
    conn.commit()


def _make_vendor(conn: psycopg.Connection, tenant_id: uuid.UUID) -> uuid.UUID:
    vendor_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO vendors (id, tenant_id, name, normalized_name) VALUES (%s, %s, %s, %s)",
            (vendor_id, tenant_id, "Acme Corp.", "acme"),
        )
    conn.commit()
    return vendor_id


def _make_po_with_grn(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    vendor_id: uuid.UUID,
    *,
    unit_price: str = "5.00",
    qty_received: str = "10",
) -> tuple[uuid.UUID, uuid.UUID]:
    po_id = uuid.uuid4()
    po_line_id = uuid.uuid4()
    grn_id = uuid.uuid4()
    receiver_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (id, tenant_id, email, role) VALUES (%s, %s, %s, 'approver')",
            (receiver_id, tenant_id, f"{receiver_id}@example.com"),
        )
        cur.execute(
            """
            INSERT INTO purchase_orders
                (id, tenant_id, vendor_id, po_number, issued_at, currency, subtotal, tax, total)
            VALUES (%s, %s, %s, 'PO-1', %s, 'USD', 50.00, 0.00, 50.00)
            """,
            (po_id, tenant_id, vendor_id, datetime(2026, 1, 1, tzinfo=UTC)),
        )
        cur.execute(
            """
            INSERT INTO purchase_order_lines
                (id, tenant_id, po_id, line_no, sku, description, normalized_description,
                 qty_ordered, unit_price, line_total)
            VALUES (%s, %s, %s, 1, 'WID-1', 'Widget', 'widget', 10, %s, %s)
            """,
            (po_line_id, tenant_id, po_id, unit_price, str(float(unit_price) * 10)),
        )
        cur.execute(
            """
            INSERT INTO goods_receipts (id, tenant_id, po_id, grn_number, received_at, received_by)
            VALUES (%s, %s, %s, 'GRN-1', %s, %s)
            """,
            (grn_id, tenant_id, po_id, datetime(2026, 1, 2, tzinfo=UTC), receiver_id),
        )
        cur.execute(
            """
            INSERT INTO goods_receipt_lines
                (tenant_id, grn_id, po_line_id, qty_received, condition)
            VALUES (%s, %s, %s, %s, 'good')
            """,
            (tenant_id, grn_id, po_line_id, qty_received),
        )
    conn.commit()
    return po_id, po_line_id


def _make_invoice(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    vendor_id: uuid.UUID,
    po_id: uuid.UUID,
    *,
    total: str = "50.00",
    confidence: float = 0.95,
    unit_price: str = "5.00",
) -> tuple[uuid.UUID, uuid.UUID]:
    invoice_id = uuid.uuid4()
    invoice_line_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO invoices
                (id, tenant_id, vendor_id, po_id, invoice_number, invoice_date, currency,
                 subtotal, tax, total, source_channel, source_file_path, content_hash,
                 status, overall_confidence)
            VALUES (%s, %s, %s, %s, 'INV-1', %s, 'USD', %s, 0.00, %s, 'upload',
                    'invoices/1.pdf', %s, 'EXTRACTED', %s)
            """,
            (
                invoice_id,
                tenant_id,
                vendor_id,
                po_id,
                date(2026, 1, 5),
                total,
                total,
                "a" * 64,
                confidence,
            ),
        )
        cur.execute(
            """
            INSERT INTO invoice_lines
                (id, tenant_id, invoice_id, line_no, description, qty, unit_price, line_total)
            VALUES (%s, %s, %s, 1, 'WID-1 replacement part', 10, %s, %s)
            """,
            (invoice_line_id, tenant_id, invoice_id, unit_price, total),
        )
    conn.commit()
    return invoice_id, invoice_line_id


def _make_job(tenant_id: uuid.UUID, invoice_id: uuid.UUID) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        job_type=JobType.MATCH,
        payload={"invoice_id": str(invoice_id)},
        status=JobStatus.RUNNING,
        attempts=0,
        max_attempts=3,
        idempotency_key=hashlib.sha256(f"{tenant_id}:{invoice_id}:match".encode()).hexdigest(),
        run_after=now,
        created_at=now,
        updated_at=now,
    )


def test_clean_high_confidence_small_invoice_auto_posts(
    tenant_id: uuid.UUID,
    handler_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
) -> None:
    _make_policy(admin_conn, tenant_id)
    vendor_id = _make_vendor(admin_conn, tenant_id)
    po_id, _ = _make_po_with_grn(admin_conn, tenant_id, vendor_id)
    invoice_id, _ = _make_invoice(admin_conn, tenant_id, vendor_id, po_id, total="50.00")
    job = _make_job(tenant_id, invoice_id)
    queue = JobQueue(queue_conn)

    handle_match(handler_conn, queue, job)

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        invoice = cur.fetchone()
    assert invoice is not None
    assert invoice["status"] == InvoiceStatus.AUTO_POSTED.value

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM match_runs WHERE invoice_id = %s", (invoice_id,))
        match_run = cur.fetchone()
    assert match_run is not None
    assert match_run["result"] == "clean"
    assert match_run["policy_version"] == 1

    with admin_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM match_exceptions WHERE invoice_id = %s", (invoice_id,))
        (count,) = cur.fetchone()
    assert count == 0

    with admin_conn.cursor() as cur:
        cur.execute("SELECT job_type FROM jobs WHERE tenant_id = %s", (tenant_id,))
        job_types = {row[0] for row in cur.fetchall()}
    assert "notify" not in job_types


def test_price_variance_beyond_tolerance_blocks_and_notifies(
    tenant_id: uuid.UUID,
    handler_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
) -> None:
    _make_policy(admin_conn, tenant_id)
    vendor_id = _make_vendor(admin_conn, tenant_id)
    po_id, po_line_id = _make_po_with_grn(admin_conn, tenant_id, vendor_id, unit_price="5.00")
    # Priced far beyond the 2% tolerance -> PRICE_VARIANCE, severity block.
    invoice_id, _ = _make_invoice(
        admin_conn, tenant_id, vendor_id, po_id, total="100.00", unit_price="10.00"
    )
    job = _make_job(tenant_id, invoice_id)
    queue = JobQueue(queue_conn)

    handle_match(handler_conn, queue, job)

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        invoice = cur.fetchone()
    assert invoice is not None
    assert invoice["status"] == InvoiceStatus.PENDING_APPROVAL.value

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM match_exceptions WHERE invoice_id = %s", (invoice_id,))
        exceptions = cur.fetchall()
    assert len(exceptions) == 1
    assert exceptions[0]["exception_type"] == "PRICE_VARIANCE"
    assert exceptions[0]["severity"] == "block"
    assert exceptions[0]["po_line_id"] == po_line_id
    assert exceptions[0]["detail"] != ""

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT payload FROM jobs WHERE tenant_id = %s AND job_type = 'notify'", (tenant_id,)
        )
        notify_job = cur.fetchone()
    assert notify_job is not None
    assert notify_job["payload"]["invoice_id"] == str(invoice_id)
    assert "reason" in notify_job["payload"]


def test_low_confidence_needs_verification_and_does_not_notify(
    tenant_id: uuid.UUID,
    handler_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
) -> None:
    _make_policy(admin_conn, tenant_id)
    vendor_id = _make_vendor(admin_conn, tenant_id)
    po_id, _ = _make_po_with_grn(admin_conn, tenant_id, vendor_id)
    invoice_id, _ = _make_invoice(
        admin_conn, tenant_id, vendor_id, po_id, total="50.00", confidence=0.40
    )
    job = _make_job(tenant_id, invoice_id)
    queue = JobQueue(queue_conn)

    handle_match(handler_conn, queue, job)

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        invoice = cur.fetchone()
    assert invoice is not None
    assert invoice["status"] == InvoiceStatus.NEEDS_VERIFICATION.value

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM jobs WHERE tenant_id = %s AND job_type = 'notify'", (tenant_id,)
        )
        (count,) = cur.fetchone()
    assert count == 0


def test_audit_log_records_the_full_transition_chain(
    tenant_id: uuid.UUID,
    handler_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
) -> None:
    _make_policy(admin_conn, tenant_id)
    vendor_id = _make_vendor(admin_conn, tenant_id)
    po_id, _ = _make_po_with_grn(admin_conn, tenant_id, vendor_id)
    invoice_id, _ = _make_invoice(admin_conn, tenant_id, vendor_id, po_id, total="50.00")
    job = _make_job(tenant_id, invoice_id)
    queue = JobQueue(queue_conn)

    handle_match(handler_conn, queue, job)

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT after FROM audit_log WHERE entity_id = %s ORDER BY created_at", (invoice_id,)
        )
        entries = cur.fetchall()
    statuses = [entry["after"]["status"] for entry in entries]
    assert statuses == ["MATCHING", "MATCHED_CLEAN", "AUTO_POSTED"]


def test_invalid_policy_raises_before_any_transition(
    tenant_id: uuid.UUID,
    handler_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
) -> None:
    # dual_approval_above <= auto_approve_below is invalid -- load_tolerance_policy
    # must reject it, and the handler must not have transitioned the invoice
    # past MATCHING before that failure surfaces.
    _make_policy(admin_conn, tenant_id, auto_approve_below=5000, dual_approval_above=1)
    vendor_id = _make_vendor(admin_conn, tenant_id)
    po_id, _ = _make_po_with_grn(admin_conn, tenant_id, vendor_id)
    invoice_id, _ = _make_invoice(admin_conn, tenant_id, vendor_id, po_id, total="50.00")
    job = _make_job(tenant_id, invoice_id)
    queue = JobQueue(queue_conn)

    with pytest.raises(Exception):  # noqa: B017 -- PolicyViolation, re-raised by design
        handle_match(handler_conn, queue, job)

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        invoice = cur.fetchone()
    assert invoice is not None
    assert invoice["status"] == InvoiceStatus.MATCHING.value
