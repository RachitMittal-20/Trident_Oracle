"""Worker poll loop.

Usage:
    DATABASE_URL=postgresql://...              (app_role, handler queries)
    QUEUE_CLAIMER_DATABASE_URL=postgresql://... (queue_claimer, queue ops)
    uv run python -m worker.main

Configurable via environment:
    WORKER_ID                     default: "<hostname>-<pid>"
    WORKER_POLL_INTERVAL_SECONDS  default: 5
    WORKER_CONCURRENCY            default: 1 (number of poll-loop threads)
    WORKER_STALE_LOCK_MINUTES     default: 10
    WORKER_BASE_DELAY_SECONDS     default: 60 (backoff base)

Two connections, two roles (db/migrations/0012_queue_claimer_role.sql; full
rationale in worker/db.py's docstring and db/README.md's "Security model"):

- QUEUE_CLAIMER_DATABASE_URL authenticates as queue_claimer (BYPASSRLS,
  grants scoped to exactly `jobs` and `dead_letters`). JobQueue -- enqueue,
  claim_next, complete, fail, reap_stale_locks -- always uses this
  connection. claim_next() has no tenant_id filter by design, so it needs
  to see every tenant's queued jobs; queue_claimer can do that without
  weakening RLS anywhere it matters, because it has no grant on any table
  holding tenant business data.
- DATABASE_URL authenticates as the ordinary app_role. Per claimed job,
  app.tenant_id is set on *this* connection (never the queue_claimer one)
  before the handler runs, so RLS fully restricts the handler's own
  business-table queries to that job's tenant -- unchanged from how this
  was built and verified before 0012.
"""

import os
import signal
import socket
import threading
import time
import uuid
from datetime import timedelta
from types import FrameType

import psycopg
import structlog
from core.models import JobType
from core.queue.models import Job
from storage.base import Storage
from storage.supabase_storage import SupabaseStorage

from worker.db import DEFAULT_BASE_DELAY, JobQueue
from worker.extract_handler import make_extract_handler
from worker.registry import HandlerRegistry

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)
log = structlog.get_logger()

DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_CONCURRENCY = 1


class GracefulShutdown:
    """Tracks a shutdown request from SIGTERM/SIGINT. The poll loop checks
    this between jobs, never mid-job -- an in-flight job always finishes
    (complete or fail) before its thread exits."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self, signum: int, _frame: FrameType | None) -> None:
        log.info("worker_shutdown_requested", signal=signum)
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> None:
        self._event.wait(timeout)

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.request)
        signal.signal(signal.SIGINT, self.request)


def _set_tenant(conn: psycopg.Connection, tenant_id: uuid.UUID) -> None:
    """Sets app.tenant_id on the (app_role) handler connection so RLS scopes
    every subsequent query on it to this job's tenant -- CLAUDE.md: "The
    worker sets app.tenant_id per job.\" Never call this on the
    queue_claimer connection -- it has no tenant-scoped policies to
    restrict in the first place, and doing so would be misleading."""
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
    conn.commit()


def run_one_job(
    queue: JobQueue,
    handler_conn: psycopg.Connection,
    registry: HandlerRegistry,
    worker_id: str,
) -> bool:
    """Claims (via `queue`, i.e. the queue_claimer connection) and runs at
    most one job on `handler_conn` (the app_role connection). Returns True
    if a job was claimed (whether or not it then succeeded), False if the
    queue had none due."""
    job: Job | None = queue.claim_next(worker_id)
    if job is None:
        return False

    _set_tenant(handler_conn, job.tenant_id)
    start = time.monotonic()
    bound_log = log.bind(
        tenant_id=str(job.tenant_id),
        job_id=str(job.id),
        job_type=str(job.job_type),
        attempt=job.attempts + 1,
    )

    handler = registry.get(job.job_type)
    try:
        if handler is None:
            raise ValueError(f"no handler registered for job_type {job.job_type!r}")
        handler(handler_conn, queue, job)
    except Exception as exc:
        # Deliberately broad: a handler's own exception must feed the
        # retry/dead-letter system, not crash the worker process or vanish
        # silently. This is the one place that's appropriate -- the
        # handler's own code/tests are where "fail loudly" applies.
        duration_ms = int((time.monotonic() - start) * 1000)
        queue.fail(job.id, str(exc))
        bound_log.warning("job_failed", duration_ms=duration_ms, error=str(exc))
        return True

    duration_ms = int((time.monotonic() - start) * 1000)
    queue.complete(job.id)
    bound_log.info("job_completed", duration_ms=duration_ms)
    return True


def poll_loop(
    queue_database_url: str,
    handler_database_url: str,
    registry: HandlerRegistry,
    worker_id: str,
    shutdown: GracefulShutdown,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    base_delay: timedelta = DEFAULT_BASE_DELAY,
) -> None:
    queue_conn = psycopg.connect(queue_database_url)
    handler_conn = psycopg.connect(handler_database_url)
    queue = JobQueue(queue_conn, base_delay=base_delay)
    log.info("worker_thread_started", worker_id=worker_id)
    try:
        while not shutdown.requested:
            claimed = run_one_job(queue, handler_conn, registry, worker_id)
            if not claimed:
                shutdown.wait(poll_interval)
    finally:
        queue_conn.close()
        handler_conn.close()
        log.info("worker_thread_stopped", worker_id=worker_id)


def build_default_registry(storage: Storage) -> HandlerRegistry:
    """extract is the only handler wired up so far -- match/notify/post are
    later phases. Real handlers register themselves here as those land."""
    registry = HandlerRegistry()
    registry.register(JobType.EXTRACT, make_extract_handler(storage))
    return registry


def _build_storage_from_env() -> Storage:
    base_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not base_url or not service_key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must both be set")
    bucket = os.environ.get("SUPABASE_STORAGE_BUCKET", "invoices")
    return SupabaseStorage(base_url, service_key, bucket)


def main() -> None:
    queue_database_url = os.environ.get("QUEUE_CLAIMER_DATABASE_URL")
    handler_database_url = os.environ.get("DATABASE_URL")
    if not queue_database_url:
        raise SystemExit("QUEUE_CLAIMER_DATABASE_URL is not set")
    if not handler_database_url:
        raise SystemExit("DATABASE_URL is not set")

    worker_id_base = os.environ.get("WORKER_ID", f"{socket.gethostname()}-{os.getpid()}")
    poll_interval = float(
        os.environ.get("WORKER_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
    )
    concurrency = int(os.environ.get("WORKER_CONCURRENCY", DEFAULT_CONCURRENCY))
    stale_after = timedelta(minutes=float(os.environ.get("WORKER_STALE_LOCK_MINUTES", "10")))
    base_delay = timedelta(seconds=float(os.environ.get("WORKER_BASE_DELAY_SECONDS", "60")))

    registry = build_default_registry(_build_storage_from_env())
    shutdown = GracefulShutdown()
    shutdown.install_signal_handlers()

    # The stale-lock reaper is queue plumbing, not a handler -- it always
    # uses the queue_claimer connection, same as the rest of JobQueue.
    reaper_conn = psycopg.connect(queue_database_url)
    reaper_queue = JobQueue(reaper_conn, stale_after=stale_after)

    threads = [
        threading.Thread(
            target=poll_loop,
            args=(
                queue_database_url,
                handler_database_url,
                registry,
                f"{worker_id_base}-{i}",
                shutdown,
            ),
            kwargs={"poll_interval": poll_interval, "base_delay": base_delay},
        )
        for i in range(concurrency)
    ]
    for thread in threads:
        thread.start()

    log.info(
        "worker_started",
        worker_id=worker_id_base,
        concurrency=concurrency,
        poll_interval=poll_interval,
    )
    try:
        while not shutdown.requested:
            recovered = reaper_queue.reap_stale_locks()
            if recovered:
                log.warning("stale_locks_reaped", count=recovered)
            shutdown.wait(poll_interval)
    finally:
        for thread in threads:
            thread.join()
        reaper_conn.close()
        log.info("worker_stopped")


if __name__ == "__main__":
    main()
