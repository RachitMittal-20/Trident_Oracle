"""Postgres-backed job queue -- the I/O layer packages/core/queue is not
allowed to have. Every method here does exactly the database work
packages/core's pure Job/DeadLetter models and compute_backoff() cannot.

Connection privilege: claim_next()'s query has no tenant_id filter by design
-- it claims the globally-next queued job across every tenant. JobQueue is
therefore meant to be constructed with a connection authenticated as the
queue_claimer role (db/migrations/0012_queue_claimer_role.sql), which has
BYPASSRLS plus grants scoped to exactly `jobs` and `dead_letters` -- nothing
else. That combination is what makes claim_next() see across tenants without
weakening RLS as the authorization boundary anywhere it matters: queue_claimer
has no grant on any table holding tenant business data, so there is nothing
for it to leak by bypassing RLS. A job's *handler*, once claimed, runs on a
separate, ordinary app-role connection with app.tenant_id set to that job's
own tenant (see worker/main.py) -- RLS fully applies there. See
db/README.md's "Security model" section for the two-role picture end to end.

Every method commits its own transaction -- these are self-contained
operations, not designed to be composed into a larger externally-managed
transaction.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

import psycopg
from core.models import JobStatus, JobType
from core.queue.backoff import compute_backoff
from core.queue.models import Job
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb

DEFAULT_BASE_DELAY = timedelta(seconds=60)
DEFAULT_STALE_AFTER = timedelta(minutes=10)


def _row_to_job(row: DictRow) -> Job:
    return Job(
        id=row["id"],
        tenant_id=row["tenant_id"],
        job_type=JobType(row["job_type"]),
        payload=row["payload"],
        status=JobStatus(row["status"]),
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        idempotency_key=row["idempotency_key"],
        run_after=row["run_after"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        locked_at=row["locked_at"],
        locked_by=row["locked_by"],
        last_error=row["last_error"],
    )


class JobQueue:
    def __init__(
        self,
        conn: psycopg.Connection[Any],
        base_delay: timedelta = DEFAULT_BASE_DELAY,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> None:
        self._conn = conn
        self._base_delay = base_delay
        self._stale_after = stale_after

    def enqueue(
        self,
        job_type: JobType | str,
        payload: dict[str, Any],
        tenant_id: uuid.UUID,
        idempotency_key: str,
        run_after: datetime | None = None,
        max_attempts: int | None = None,
    ) -> Job:
        """Insert a new job. On idempotency_key conflict, returns the
        existing row unchanged -- never raises, never duplicates.

        The upsert-that-always-returns-a-row trick (DO UPDATE SET a no-op
        self-assignment, rather than DO NOTHING) avoids a second
        SELECT-on-conflict round trip and the race window that would open
        between it and the INSERT.

        `max_attempts` defaults to the jobs table's own DEFAULT (3) when
        omitted -- the `coalesce(..., 3)` below mirrors that column default
        rather than replacing it, since supplying the column at all (even
        as NULL) bypasses the table's DEFAULT clause. Notify jobs pass 5
        explicitly (worker/match_handler.py) -- CLAUDE.md's notification
        pipeline calls for up to 5 delivery attempts, more than the queue's
        general-purpose default.
        """
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO jobs (tenant_id, job_type, payload, idempotency_key, run_after,
                                   max_attempts)
                VALUES (%(tenant_id)s, %(job_type)s, %(payload)s, %(idempotency_key)s,
                        coalesce(%(run_after)s, now()), coalesce(%(max_attempts)s, 3))
                ON CONFLICT (idempotency_key)
                    DO UPDATE SET idempotency_key = jobs.idempotency_key
                RETURNING *
                """,
                {
                    "tenant_id": tenant_id,
                    "job_type": str(job_type),
                    "payload": Jsonb(payload),
                    "idempotency_key": idempotency_key,
                    "run_after": run_after,
                    "max_attempts": max_attempts,
                },
            )
            row = cur.fetchone()
        self._conn.commit()
        if row is None:
            raise RuntimeError("enqueue INSERT ... RETURNING produced no row")
        return _row_to_job(row)

    def claim_next(self, worker_id: str) -> Job | None:
        """Atomically claim and lock the next queued, due job, or None if
        the queue is empty. FOR UPDATE SKIP LOCKED means any number of
        workers can call this concurrently without ever claiming the same
        row -- see apps/worker/tests for the concurrency proof."""
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE jobs SET status='running', locked_at=now(), locked_by=%(worker_id)s
                WHERE id = (
                    SELECT id FROM jobs
                    WHERE status='queued' AND run_after <= now()
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING *
                """,
                {"worker_id": worker_id},
            )
            row = cur.fetchone()
        self._conn.commit()
        return _row_to_job(row) if row is not None else None

    def complete(self, job_id: uuid.UUID) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status='done', updated_at=now() WHERE id = %(job_id)s",
                {"job_id": job_id},
            )
        self._conn.commit()

    def fail(self, job_id: uuid.UUID, error: str) -> Job:
        """Increment attempts and either reschedule with exponential
        backoff + jitter, or -- once attempts reaches max_attempts -- copy
        the row to dead_letters and mark it dead."""
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE jobs
                SET attempts = attempts + 1, last_error = %(error)s, updated_at = now()
                WHERE id = %(job_id)s
                RETURNING *
                """,
                {"job_id": job_id, "error": error},
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"no job with id {job_id}")
            job = _row_to_job(row)

            if job.attempts >= job.max_attempts:
                cur.execute(
                    """
                    INSERT INTO dead_letters (tenant_id, job_id, payload, final_error)
                    VALUES (%(tenant_id)s, %(job_id)s, %(payload)s, %(final_error)s)
                    """,
                    {
                        "tenant_id": job.tenant_id,
                        "job_id": job.id,
                        "payload": Jsonb(job.payload),
                        "final_error": error,
                    },
                )
                cur.execute(
                    """
                    UPDATE jobs SET status='dead', updated_at=now()
                    WHERE id = %(job_id)s
                    RETURNING *
                    """,
                    {"job_id": job_id},
                )
            else:
                delay = compute_backoff(job.attempts, self._base_delay)
                cur.execute(
                    """
                    UPDATE jobs
                    SET status='queued', run_after = now() + %(delay)s,
                        locked_at = NULL, locked_by = NULL, updated_at = now()
                    WHERE id = %(job_id)s
                    RETURNING *
                    """,
                    {"job_id": job_id, "delay": delay},
                )
            final_row = cur.fetchone()
        self._conn.commit()
        if final_row is None:
            raise RuntimeError(f"fail() UPDATE produced no row for job {job_id}")
        return _row_to_job(final_row)

    def reap_stale_locks(self) -> int:
        """Any 'running' job whose lock is older than stale_after returns to
        'queued', attempts incremented -- a crashed worker must not strand
        its claimed work forever. Returns the number of jobs recovered."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status = 'queued', attempts = attempts + 1,
                    locked_at = NULL, locked_by = NULL, updated_at = now()
                WHERE status = 'running' AND locked_at < now() - %(stale_after)s
                """,
                {"stale_after": self._stale_after},
            )
            count = cur.rowcount
        self._conn.commit()
        return count
