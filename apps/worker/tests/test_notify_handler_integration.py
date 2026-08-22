"""Integration tests for the 'notify' handler -- require a live Postgres
with all migrations applied. Set both TEST_DATABASE_URL and
QUEUE_CLAIMER_TEST_DATABASE_URL to run these; skipped otherwise, same
convention as test_match_handler_integration.py (handler_conn connects via
TEST_DATABASE_URL, an admin connection that bypasses RLS as a superuser
always does -- these tests are about handler logic, not RLS enforcement,
same simplification test_match_handler_integration.py makes).
"""

import hashlib
import os
import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime

import psycopg
import pytest
from core.errors import NotificationError
from core.models import JobStatus, JobType
from core.queue.models import Job
from notifiers.base import DeliveryResult, NotificationMessage
from notifiers.errors import RetryableNotificationError
from psycopg.rows import dict_row
from worker import notify_handler
from worker.db import JobQueue
from worker.notify_handler import handle_notify

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
QUEUE_CLAIMER_DATABASE_URL = os.environ.get("QUEUE_CLAIMER_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not QUEUE_CLAIMER_DATABASE_URL,
    reason=(
        "TEST_DATABASE_URL and QUEUE_CLAIMER_TEST_DATABASE_URL must both be "
        "set -- skipping live Postgres tests"
    ),
)


class FlakyNotifier:
    """Fails with RetryableNotificationError on its first `fail_times`
    calls, then succeeds -- simulates a transient timeout that clears up on
    retry. `sent` records only genuinely successful sends, so "exactly one
    message" can be asserted directly against it regardless of how many
    times handle_notify was invoked in total.
    """

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls = 0
        self.sent: list[tuple[str, NotificationMessage, str]] = []

    def send(
        self, recipient: str, message: NotificationMessage, idempotency_key: str
    ) -> DeliveryResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RetryableNotificationError("simulated timeout")
        self.sent.append((recipient, message, idempotency_key))
        return DeliveryResult(provider_message_id=f"msg-{self.calls}", channel="telegram")


class AlwaysPermanentFailureNotifier:
    def send(
        self, recipient: str, message: NotificationMessage, idempotency_key: str
    ) -> DeliveryResult:
        raise NotificationError("recipient rejected -- bad chat id")


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
        cur.execute("TRUNCATE TABLE notification_deliveries, invoices, jobs, tenants CASCADE")
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


def _make_job(
    tenant_id: uuid.UUID, invoice_id: uuid.UUID, *, attempts: int = 0, max_attempts: int = 5
) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        job_type=JobType.NOTIFY,
        payload={
            "invoice_id": str(invoice_id),
            "exception_id": None,
            "recipient": "chat-1",
            "channel": "telegram",
            "title": "Invoice needs approval",
            "body": "Total **$100.00** needs a decision.",
            "actions": [{"label": "Approve", "action_id": "tok-1", "style": "primary"}],
        },
        status=JobStatus.RUNNING,
        attempts=attempts,
        max_attempts=max_attempts,
        idempotency_key=hashlib.sha256(f"notify:{invoice_id}:{attempts}".encode()).hexdigest(),
        run_after=now,
        created_at=now,
        updated_at=now,
    )


def _get_delivery(admin_conn: psycopg.Connection, invoice_id: uuid.UUID) -> dict:
    with admin_conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM notification_deliveries WHERE invoice_id = %s", (invoice_id,))
        row = cur.fetchone()
    assert row is not None
    return row


# --- insert-before-send idempotency ------------------------------------------


def test_delivery_row_inserted_before_send_is_attempted(
    monkeypatch: pytest.MonkeyPatch,
    handler_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> None:
    notifier = FlakyNotifier(fail_times=0)
    monkeypatch.setattr(notify_handler, "get_notifier", lambda channel: notifier)

    handle_notify(handler_conn, JobQueue(queue_conn), _make_job(tenant_id, invoice_id))

    delivery = _get_delivery(admin_conn, invoice_id)
    assert delivery["status"] == "sent"
    assert delivery["provider_message_id"] == "msg-1"
    assert delivery["sent_at"] is not None
    assert notifier.calls == 1


# --- retry after a simulated timeout sends exactly one message --------------


def test_retry_after_simulated_timeout_sends_exactly_one_message(
    monkeypatch: pytest.MonkeyPatch,
    handler_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> None:
    notifier = FlakyNotifier(fail_times=1)  # first call times out, second succeeds
    monkeypatch.setattr(notify_handler, "get_notifier", lambda channel: notifier)
    queue = JobQueue(queue_conn)

    # Attempt 1: simulated timeout -- must be retryable and re-raised.
    with pytest.raises(RetryableNotificationError):
        handle_notify(handler_conn, queue, _make_job(tenant_id, invoice_id, attempts=0))

    delivery = _get_delivery(admin_conn, invoice_id)
    assert delivery["status"] == "pending"
    assert delivery["attempts"] == 1
    assert delivery["next_retry_at"] is not None
    assert notifier.sent == []

    # Attempt 2 (the retry, as the job queue itself would drive it -- same
    # idempotency_key, job.attempts now 1): succeeds this time.
    handle_notify(handler_conn, queue, _make_job(tenant_id, invoice_id, attempts=1))

    delivery = _get_delivery(admin_conn, invoice_id)
    assert delivery["status"] == "sent"
    assert delivery["provider_message_id"] == "msg-2"
    assert len(notifier.sent) == 1, "exactly one message was ever actually sent"

    # Attempt 3: even if the job were somehow invoked again, the idempotency
    # short-circuit means the notifier is never called a third time.
    handle_notify(handler_conn, queue, _make_job(tenant_id, invoice_id, attempts=2))
    assert notifier.calls == 2
    assert len(notifier.sent) == 1


# --- permanent failure dead-letters immediately ------------------------------


def test_permanent_failure_dead_letters_immediately_even_with_attempts_left(
    monkeypatch: pytest.MonkeyPatch,
    handler_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> None:
    monkeypatch.setattr(
        notify_handler, "get_notifier", lambda channel: AlwaysPermanentFailureNotifier()
    )
    queue = JobQueue(queue_conn)

    with pytest.raises(NotificationError):
        handle_notify(
            handler_conn, queue, _make_job(tenant_id, invoice_id, attempts=0, max_attempts=5)
        )

    delivery = _get_delivery(admin_conn, invoice_id)
    assert delivery["status"] == "dead"  # dead despite 4 attempts still nominally available
    assert delivery["next_retry_at"] is None
    assert "bad chat id" in delivery["error"]


def test_retryable_failure_on_last_attempt_marks_dead_not_pending(
    monkeypatch: pytest.MonkeyPatch,
    handler_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> None:
    notifier = FlakyNotifier(fail_times=999)  # never succeeds
    monkeypatch.setattr(notify_handler, "get_notifier", lambda channel: notifier)
    queue = JobQueue(queue_conn)

    with pytest.raises(RetryableNotificationError):
        handle_notify(
            handler_conn, queue, _make_job(tenant_id, invoice_id, attempts=4, max_attempts=5)
        )

    delivery = _get_delivery(admin_conn, invoice_id)
    assert delivery["status"] == "dead"
    # This delivery row is fresh (its own attempts counter starts at 0,
    # independent of the JOB's attempts=4) -- what marks it dead is
    # is_last_attempt (job.attempts + 1 >= job.max_attempts), not this
    # row having failed 5 times itself.
    assert delivery["attempts"] == 1
    assert delivery["next_retry_at"] is None


# --- delivery status transitions recorded correctly --------------------------


def test_delivery_status_transition_sequence(
    monkeypatch: pytest.MonkeyPatch,
    handler_conn: psycopg.Connection,
    admin_conn: psycopg.Connection,
    queue_conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
) -> None:
    notifier = FlakyNotifier(fail_times=2)
    monkeypatch.setattr(notify_handler, "get_notifier", lambda channel: notifier)
    queue = JobQueue(queue_conn)

    statuses: list[str] = []

    with pytest.raises(RetryableNotificationError):
        handle_notify(handler_conn, queue, _make_job(tenant_id, invoice_id, attempts=0))
    statuses.append(_get_delivery(admin_conn, invoice_id)["status"])

    with pytest.raises(RetryableNotificationError):
        handle_notify(handler_conn, queue, _make_job(tenant_id, invoice_id, attempts=1))
    statuses.append(_get_delivery(admin_conn, invoice_id)["status"])

    handle_notify(handler_conn, queue, _make_job(tenant_id, invoice_id, attempts=2))
    statuses.append(_get_delivery(admin_conn, invoice_id)["status"])

    assert statuses == ["pending", "pending", "sent"]

    final = _get_delivery(admin_conn, invoice_id)
    assert final["attempts"] == 2  # only incremented on the two failures, not the success
    assert final["sent_at"] is not None
