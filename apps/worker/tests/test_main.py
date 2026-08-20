"""Unit tests for the worker loop's orchestration logic -- run_one_job's
handler dispatch/error handling and GracefulShutdown's flag semantics.
No DB: JobQueue is a scripted fake, so these run in the default DB-free
`pytest` invocation. See test_queue_integration.py for the real-Postgres
tests of JobQueue itself.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
from core.models import JobStatus, JobType
from core.queue.models import Job
from worker.main import GracefulShutdown, run_one_job
from worker.registry import HandlerRegistry

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_job(**overrides: object) -> Job:
    fields: dict[str, Any] = dict(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        job_type=JobType.EXTRACT,
        payload={},
        status=JobStatus.RUNNING,
        attempts=0,
        max_attempts=3,
        idempotency_key="key",
        run_after=NOW,
        created_at=NOW,
        updated_at=NOW,
        locked_at=NOW,
        locked_by="worker-1",
    )
    fields.update(overrides)
    return Job(**fields)  # type: ignore[arg-type]


class _FakeQueue:
    def __init__(self, job: Job | None) -> None:
        self._job = job
        self.completed: list[uuid.UUID] = []
        self.failed: list[tuple[uuid.UUID, str]] = []

    def claim_next(self, worker_id: str) -> Job | None:
        return self._job

    def complete(self, job_id: uuid.UUID) -> None:
        self.completed.append(job_id)

    def fail(self, job_id: uuid.UUID, error: str) -> Job:
        self.failed.append((job_id, error))
        assert self._job is not None
        return self._job


class _FakeConnection:
    """Stands in for psycopg.Connection: records set_config calls and
    commits, never touches a real database."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.commits = 0

    def cursor(self) -> "_FakeCursor":
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1


class _FakeCursor:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self._conn.executed.append((query, params))


# --- run_one_job -----------------------------------------------------------


def test_run_one_job_returns_false_when_queue_empty() -> None:
    queue = _FakeQueue(None)
    conn = _FakeConnection()
    registry = HandlerRegistry()

    claimed = run_one_job(queue, conn, registry, "worker-1")  # type: ignore[arg-type]

    assert claimed is False


def test_run_one_job_sets_tenant_before_running_handler() -> None:
    job = make_job()
    queue = _FakeQueue(job)
    conn = _FakeConnection()
    registry = HandlerRegistry()
    seen_tenant: list[str] = []
    registry.register(JobType.EXTRACT, lambda c, q, j: seen_tenant.append(str(j.tenant_id)))

    run_one_job(queue, conn, registry, "worker-1")  # type: ignore[arg-type]

    assert conn.executed  # set_config was issued
    assert "set_config" in conn.executed[0][0]
    assert conn.executed[0][1] == (str(job.tenant_id),)
    assert seen_tenant == [str(job.tenant_id)]


def test_run_one_job_completes_on_handler_success() -> None:
    job = make_job()
    queue = _FakeQueue(job)
    conn = _FakeConnection()
    registry = HandlerRegistry()
    registry.register(JobType.EXTRACT, lambda c, q, j: None)

    claimed = run_one_job(queue, conn, registry, "worker-1")  # type: ignore[arg-type]

    assert claimed is True
    assert queue.completed == [job.id]
    assert queue.failed == []


def test_run_one_job_fails_job_when_handler_raises() -> None:
    job = make_job()
    queue = _FakeQueue(job)
    conn = _FakeConnection()
    registry = HandlerRegistry()

    def handler(c: psycopg.Connection, q: object, j: Job) -> None:
        raise ValueError("handler exploded")

    registry.register(JobType.EXTRACT, handler)

    claimed = run_one_job(queue, conn, registry, "worker-1")  # type: ignore[arg-type]

    assert claimed is True
    assert queue.completed == []
    assert len(queue.failed) == 1
    failed_id, error = queue.failed[0]
    assert failed_id == job.id
    assert "handler exploded" in error


def test_run_one_job_fails_job_when_no_handler_registered() -> None:
    job = make_job(job_type=JobType.NOTIFY)
    queue = _FakeQueue(job)
    conn = _FakeConnection()
    registry = HandlerRegistry()  # nothing registered for NOTIFY

    claimed = run_one_job(queue, conn, registry, "worker-1")  # type: ignore[arg-type]

    assert claimed is True
    assert len(queue.failed) == 1
    _, error = queue.failed[0]
    assert "no handler registered" in error


# --- GracefulShutdown --------------------------------------------------


def test_graceful_shutdown_starts_unrequested() -> None:
    shutdown = GracefulShutdown()
    assert shutdown.requested is False


def test_graceful_shutdown_request_sets_flag() -> None:
    shutdown = GracefulShutdown()
    shutdown.request(15, None)
    assert shutdown.requested is True


def test_graceful_shutdown_wait_returns_immediately_once_requested() -> None:
    shutdown = GracefulShutdown()
    shutdown.request(15, None)
    start = datetime.now(UTC)
    shutdown.wait(5.0)  # would block 5s if the flag weren't already set
    elapsed = (datetime.now(UTC) - start).total_seconds()
    assert elapsed < 1.0
