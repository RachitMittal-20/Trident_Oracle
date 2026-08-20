"""Integration tests for the 'extract' handler -- require a live Postgres
with all migrations applied. Set both TEST_DATABASE_URL and
QUEUE_CLAIMER_TEST_DATABASE_URL to run these; skipped otherwise, same
convention as test_queue_integration.py.

Uses MockExtractor (via EXTRACTOR_BACKEND=mock) and MemoryStorage -- no
network, no real Gemini/Tesseract call, no real Supabase bucket. This is
"upload flow with the mock extractor": the handler's own logic (state
transitions, persistence, min-confidence, follow-up enqueue) is what's under
test, not any particular extraction backend's accuracy.
"""

import hashlib
import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime

import psycopg
import pytest
from core.models import InvoiceStatus, JobStatus, JobType
from core.queue.models import Job
from psycopg.rows import dict_row
from storage.memory import MemoryStorage
from worker.db import JobQueue
from worker.extract_handler import make_extract_handler

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
QUEUE_CLAIMER_DATABASE_URL = os.environ.get("QUEUE_CLAIMER_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not QUEUE_CLAIMER_DATABASE_URL,
    reason=(
        "TEST_DATABASE_URL and QUEUE_CLAIMER_TEST_DATABASE_URL must both be "
        "set -- skipping live Postgres tests"
    ),
)


@pytest.fixture(autouse=True)
def _use_mock_extractor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTRACTOR_BACKEND", "mock")


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
            "TRUNCATE TABLE field_confidences, invoice_lines, audit_log, "
            "jobs, invoices, tenants CASCADE"
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


def _make_invoice_row(
    conn: psycopg.Connection, tenant_id: uuid.UUID, storage_path: str
) -> uuid.UUID:
    invoice_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO invoices
                (id, tenant_id, currency, source_channel, source_file_path,
                 content_hash, status)
            VALUES (%s, %s, 'USD', 'upload', %s, %s, 'RECEIVED')
            """,
            (invoice_id, tenant_id, storage_path, "a" * 64),
        )
    conn.commit()
    return invoice_id


def _make_job(tenant_id: uuid.UUID, invoice_id: uuid.UUID, attempts: int = 0) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        job_type=JobType.EXTRACT,
        payload={"invoice_id": str(invoice_id)},
        status=JobStatus.RUNNING,
        attempts=attempts,
        max_attempts=3,
        idempotency_key=hashlib.sha256(f"{tenant_id}:{invoice_id}".encode()).hexdigest(),
        run_after=now,
        created_at=now,
        updated_at=now,
    )


def test_extract_success_transitions_to_extracted_and_enqueues_match(
    tenant_id: uuid.UUID,
    handler_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
) -> None:
    storage = MemoryStorage()
    storage_path = f"{tenant_id}/inv/upload.pdf"
    storage.upload(storage_path, b"%PDF-1.4 fake pdf bytes", "application/pdf")
    invoice_id = _make_invoice_row(handler_conn, tenant_id, storage_path)
    job = _make_job(tenant_id, invoice_id)
    queue = JobQueue(queue_conn)

    handler = make_extract_handler(storage)
    handler(handler_conn, queue, job)

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
        invoice = cur.fetchone()
    assert invoice is not None
    assert invoice["status"] == InvoiceStatus.EXTRACTED.value
    assert invoice["invoice_number"] == "INV-2026-00417"  # from fixtures/clean_invoice.json
    assert invoice["extraction_backend"] == "mock"
    assert invoice["overall_confidence"] is not None

    with admin_conn.cursor() as cur:
        cur.execute("SELECT job_type FROM jobs WHERE tenant_id = %s", (tenant_id,))
        job_types = {row[0] for row in cur.fetchall()}
    assert "match" in job_types


def test_extract_persists_lines_and_field_confidences_with_bbox(
    tenant_id: uuid.UUID,
    handler_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
) -> None:
    storage = MemoryStorage()
    storage_path = f"{tenant_id}/inv/upload.pdf"
    storage.upload(storage_path, b"%PDF-1.4 fake pdf bytes", "application/pdf")
    invoice_id = _make_invoice_row(handler_conn, tenant_id, storage_path)
    job = _make_job(tenant_id, invoice_id)
    queue = JobQueue(queue_conn)

    handler = make_extract_handler(storage)
    handler(handler_conn, queue, job)

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM invoice_lines WHERE invoice_id = %s ORDER BY line_no", (invoice_id,)
        )
        lines = cur.fetchall()
    assert len(lines) == 3  # fixtures/clean_invoice.json has 3 line items
    assert lines[0]["description"] == "Steel bracket, 4in, zinc-plated"
    assert lines[0]["qty"] == 40

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM field_confidences WHERE invoice_id = %s", (invoice_id,))
        confidences = cur.fetchall()
    by_path = {row["field_path"]: row for row in confidences}
    assert "header.total" in by_path
    # fixtures/clean_invoice.json sets a real bbox on header.total.
    assert by_path["header.total"]["bbox"] is not None
    assert by_path["header.total"]["bbox"]["page"] == 1


def test_overall_confidence_persisted_is_the_minimum(
    tenant_id: uuid.UUID,
    handler_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
) -> None:
    storage = MemoryStorage()
    storage_path = f"{tenant_id}/inv/upload.pdf"
    storage.upload(storage_path, b"%PDF-1.4 fake pdf bytes", "application/pdf")
    invoice_id = _make_invoice_row(handler_conn, tenant_id, storage_path)
    job = _make_job(tenant_id, invoice_id)
    queue = JobQueue(queue_conn)

    handler = make_extract_handler(storage)
    handler(handler_conn, queue, job)

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT overall_confidence FROM invoices WHERE id = %s", (invoice_id,))
        row = cur.fetchone()
        cur.execute("SELECT confidence FROM field_confidences WHERE invoice_id = %s", (invoice_id,))
        all_confidences = [r["confidence"] for r in cur.fetchall()]

    assert row is not None
    expected_min = min(all_confidences)
    expected_mean = sum(all_confidences) / len(all_confidences)
    assert row["overall_confidence"] == expected_min
    assert row["overall_confidence"] != expected_mean
    assert row["overall_confidence"] < expected_mean


def test_extract_writes_audit_log_for_every_transition(
    tenant_id: uuid.UUID,
    handler_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
) -> None:
    storage = MemoryStorage()
    storage_path = f"{tenant_id}/inv/upload.pdf"
    storage.upload(storage_path, b"%PDF-1.4 fake pdf bytes", "application/pdf")
    invoice_id = _make_invoice_row(handler_conn, tenant_id, storage_path)
    job = _make_job(tenant_id, invoice_id)
    queue = JobQueue(queue_conn)

    handler = make_extract_handler(storage)
    handler(handler_conn, queue, job)

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT after FROM audit_log WHERE entity_id = %s ORDER BY created_at",
            (invoice_id,),
        )
        entries = cur.fetchall()
    statuses = [entry["after"]["status"] for entry in entries]
    assert statuses == ["EXTRACTING", "EXTRACTED"]


def test_extraction_failure_on_last_attempt_marks_extraction_failed(
    tenant_id: uuid.UUID,
    handler_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
) -> None:
    storage = MemoryStorage()
    storage_path = f"{tenant_id}/inv/upload.pdf"
    # Bytes that don't match any recognized magic-byte signature -- the
    # handler will fail to determine a mime type and raise.
    storage.upload(storage_path, b"not a real file at all", "application/octet-stream")
    invoice_id = _make_invoice_row(handler_conn, tenant_id, storage_path)
    job = _make_job(tenant_id, invoice_id, attempts=2)  # attempts+1 == max_attempts (3): last try
    queue = JobQueue(queue_conn)

    handler = make_extract_handler(storage)
    with pytest.raises(Exception):  # noqa: B017 -- deliberately broad, re-raised by design
        handler(handler_conn, queue, job)

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        invoice = cur.fetchone()
    assert invoice is not None
    assert invoice["status"] == InvoiceStatus.EXTRACTION_FAILED.value

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT after FROM audit_log WHERE entity_id = %s ORDER BY created_at",
            (invoice_id,),
        )
        entries = cur.fetchall()
    assert entries[-1]["after"]["status"] == "EXTRACTION_FAILED"
    assert "error" in entries[-1]["after"]


def test_extraction_failure_not_on_last_attempt_leaves_invoice_extracting(
    tenant_id: uuid.UUID,
    handler_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
) -> None:
    storage = MemoryStorage()
    storage_path = f"{tenant_id}/inv/upload.pdf"
    storage.upload(storage_path, b"not a real file at all", "application/octet-stream")
    invoice_id = _make_invoice_row(handler_conn, tenant_id, storage_path)
    job = _make_job(tenant_id, invoice_id, attempts=0)  # first attempt, not the last of 3
    queue = JobQueue(queue_conn)

    handler = make_extract_handler(storage)
    with pytest.raises(Exception):  # noqa: B017
        handler(handler_conn, queue, job)

    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        invoice = cur.fetchone()
    assert invoice is not None
    # Not EXTRACTION_FAILED -- that's terminal, and this job still has
    # retries left, so a future retry must still be able to re-enter
    # EXTRACTING without hitting InvalidStateTransition.
    assert invoice["status"] == InvoiceStatus.EXTRACTING.value
