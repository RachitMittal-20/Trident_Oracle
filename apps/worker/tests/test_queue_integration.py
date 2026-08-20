"""Integration tests for worker/db.py's JobQueue -- require a live Postgres
with migrations 0001-0012 applied. Set both env vars below to run these;
they are skipped otherwise, so the repo's default `pytest` run stays DB-free
like every other package here.

TEST_DATABASE_URL: an admin/superuser connection, used only for test setup
(creating tenant rows, truncating tables between tests) -- things
queue_claimer deliberately cannot do.

QUEUE_CLAIMER_TEST_DATABASE_URL: a connection authenticated as the
queue_claimer role (db/migrations/0012_queue_claimer_role.sql). JobQueue is
constructed with *this* connection in every test here -- that's the point:
these tests prove the queue_claimer role itself (not an admin/superuser
standing in for it) can see and claim jobs across every tenant, while being
unable to touch anything outside jobs/dead_letters. See db/README.md's
"Security model" section for the full two-role picture.
"""

import concurrent.futures
import os
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from core.models import JobStatus, JobType
from psycopg.rows import DictRow, dict_row
from worker.db import JobQueue

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
QUEUE_CLAIMER_DATABASE_URL = os.environ.get("QUEUE_CLAIMER_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not QUEUE_CLAIMER_DATABASE_URL,
    reason=(
        "TEST_DATABASE_URL and QUEUE_CLAIMER_TEST_DATABASE_URL must both be "
        "set -- skipping live Postgres tests"
    ),
)


def _admin_connect() -> psycopg.Connection:
    assert TEST_DATABASE_URL is not None
    return psycopg.connect(TEST_DATABASE_URL, autocommit=False)


def _connect() -> psycopg.Connection:
    """Connects as queue_claimer -- the role JobQueue actually runs as in
    production. Using it here, not an admin connection, is what proves the
    0012 role resolution works rather than just asserting it in a docstring."""
    assert QUEUE_CLAIMER_DATABASE_URL is not None
    return psycopg.connect(QUEUE_CLAIMER_DATABASE_URL, autocommit=False)


def _fetch_row(conn: psycopg.Connection, job_id: uuid.UUID) -> DictRow:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
    assert row is not None
    return row


@pytest.fixture
def admin_conn() -> Generator[psycopg.Connection, None, None]:
    connection = _admin_connect()
    yield connection
    connection.close()


@pytest.fixture
def conn() -> Generator[psycopg.Connection, None, None]:
    connection = _connect()
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def _clean_tables(admin_conn: psycopg.Connection) -> Generator[None, None, None]:
    # TRUNCATE requires a privilege queue_claimer deliberately doesn't have.
    with admin_conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE dead_letters, jobs, tenants CASCADE")
    admin_conn.commit()
    yield


@pytest.fixture
def tenant_id(admin_conn: psycopg.Connection) -> uuid.UUID:
    # INSERT INTO tenants requires a privilege queue_claimer doesn't have --
    # a job's tenant is created by application code, not queue plumbing.
    tid = uuid.uuid4()
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, %s, %s)",
            (tid, "Test Tenant", f"test-{tid}"),
        )
    admin_conn.commit()
    return tid


@pytest.fixture
def queue(conn: psycopg.Connection) -> JobQueue:
    return JobQueue(conn, base_delay=timedelta(seconds=60))


# --- queue_claimer's scoping itself (the actual security claim) ------------


def test_queue_claimer_cannot_select_business_tables(
    conn: psycopg.Connection, tenant_id: uuid.UUID
) -> None:
    """The whole safety argument for BYPASSRLS on this role: it has no
    grant on any table holding tenant business data, so bypassing RLS
    cannot leak anything. Prove it directly, not just in a docstring."""
    for table in ("vendors", "purchase_orders", "invoices", "users"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {table} LIMIT 1")  # noqa: S608
        conn.rollback()


def test_queue_claimer_can_select_jobs_and_dead_letters(
    queue: JobQueue, tenant_id: uuid.UUID, conn: psycopg.Connection
) -> None:
    job = queue.enqueue(JobType.EXTRACT, {}, tenant_id, "key-grant-check")
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM jobs WHERE id = %s", (job.id,))
        assert cur.fetchone() is not None
        cur.execute("SELECT count(*) FROM dead_letters")
        cur.fetchone()  # no error -- SELECT is granted even with zero rows


# --- enqueue -----------------------------------------------------------


def test_enqueue_duplicate_idempotency_key_returns_existing_no_duplicate(
    queue: JobQueue, tenant_id: uuid.UUID, conn: psycopg.Connection
) -> None:
    first = queue.enqueue(JobType.EXTRACT, {"a": 1}, tenant_id, "dup-key")
    second = queue.enqueue(JobType.EXTRACT, {"a": 2}, tenant_id, "dup-key")

    assert second.id == first.id
    assert second.payload == {"a": 1}  # original payload preserved, not overwritten

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM jobs WHERE idempotency_key = %s", ("dup-key",))
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 1


def test_enqueue_different_keys_creates_separate_jobs(
    queue: JobQueue, tenant_id: uuid.UUID
) -> None:
    first = queue.enqueue(JobType.EXTRACT, {}, tenant_id, "key-a")
    second = queue.enqueue(JobType.EXTRACT, {}, tenant_id, "key-b")
    assert first.id != second.id


# --- claim_next ------------------------------------------------------------


def test_claim_next_returns_none_when_empty(queue: JobQueue) -> None:
    assert queue.claim_next("worker-1") is None


def test_claim_next_claims_a_due_job(queue: JobQueue, tenant_id: uuid.UUID) -> None:
    job = queue.enqueue(JobType.EXTRACT, {}, tenant_id, "key-1")

    claimed = queue.claim_next("worker-1")

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.RUNNING
    assert claimed.locked_by == "worker-1"
    assert claimed.locked_at is not None


def test_claim_next_skips_jobs_not_yet_due(queue: JobQueue, tenant_id: uuid.UUID) -> None:
    future = datetime.now(UTC) + timedelta(hours=1)
    queue.enqueue(JobType.EXTRACT, {}, tenant_id, "key-future", run_after=future)

    assert queue.claim_next("worker-1") is None


def test_claim_next_ignores_already_running_jobs(queue: JobQueue, tenant_id: uuid.UUID) -> None:
    queue.enqueue(JobType.EXTRACT, {}, tenant_id, "key-1")
    queue.claim_next("worker-1")  # now running

    assert queue.claim_next("worker-2") is None


def test_claim_next_sees_jobs_across_every_tenant(admin_conn: psycopg.Connection) -> None:
    """The actual point of queue_claimer: claim_next() has no tenant filter,
    and must see jobs belonging to *different* tenants in one call stream --
    something an RLS-restricted, single-app.tenant_id connection could
    never do."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenants (id, name, slug) VALUES (%s, 'A', %s), (%s, 'B', %s)",
            (tenant_a, f"a-{tenant_a}", tenant_b, f"b-{tenant_b}"),
        )
    admin_conn.commit()

    setup_conn = _connect()
    setup_queue = JobQueue(setup_conn)
    job_a = setup_queue.enqueue(JobType.EXTRACT, {}, tenant_a, "cross-tenant-a")
    job_b = setup_queue.enqueue(JobType.EXTRACT, {}, tenant_b, "cross-tenant-b")
    setup_conn.close()

    claim_conn = _connect()
    try:
        claim_queue = JobQueue(claim_conn)
        claimed_tenants = {
            claim_queue.claim_next("worker-1").tenant_id,  # type: ignore[union-attr]
            claim_queue.claim_next("worker-1").tenant_id,  # type: ignore[union-attr]
        }
    finally:
        claim_conn.close()

    assert claimed_tenants == {tenant_a, tenant_b}
    assert {job_a.tenant_id, job_b.tenant_id} == {tenant_a, tenant_b}


def test_two_concurrent_workers_never_claim_same_job(tenant_id: uuid.UUID) -> None:
    setup_conn = _connect()
    setup_queue = JobQueue(setup_conn)
    n = 20
    job_ids = {
        setup_queue.enqueue(JobType.EXTRACT, {"i": i}, tenant_id, f"concurrent-{i}").id
        for i in range(n)
    }
    setup_conn.close()

    claimed: dict[str, list[uuid.UUID]] = {"w1": [], "w2": []}

    def worker_loop(worker_id: str) -> None:
        wconn = _connect()
        try:
            wqueue = JobQueue(wconn)
            while True:
                job = wqueue.claim_next(worker_id)
                if job is None:
                    break
                claimed[worker_id].append(job.id)
        finally:
            wconn.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker_loop, worker_id) for worker_id in ("w1", "w2")]
        for future in futures:
            future.result()

    all_claimed = claimed["w1"] + claimed["w2"]
    assert len(all_claimed) == len(set(all_claimed)), "a job was claimed more than once"
    assert set(all_claimed) == job_ids, "not every job was claimed exactly once"
    # Not a hard requirement, but if only one thread ever got the GIL/socket
    # scheduled this test wouldn't actually be proving concurrency held.
    assert claimed["w1"] and claimed["w2"], "both workers should have claimed at least one job"


# --- complete ------------------------------------------------------------


def test_complete_marks_job_done(
    queue: JobQueue, tenant_id: uuid.UUID, conn: psycopg.Connection
) -> None:
    job = queue.enqueue(JobType.EXTRACT, {}, tenant_id, "key-complete")
    queue.claim_next("worker-1")

    queue.complete(job.id)

    row = _fetch_row(conn, job.id)
    assert row["status"] == "done"


# --- fail / backoff / dead-lettering ------------------------------------


def test_fail_reschedules_with_backoff(queue: JobQueue, tenant_id: uuid.UUID) -> None:
    job = queue.enqueue(JobType.EXTRACT, {}, tenant_id, "key-fail")
    queue.claim_next("worker-1")
    before = datetime.now(UTC)

    updated = queue.fail(job.id, "boom")

    assert updated.status == JobStatus.QUEUED
    assert updated.attempts == 1
    assert updated.last_error == "boom"
    assert updated.locked_at is None
    assert updated.locked_by is None
    assert updated.run_after > before


def test_dead_letters_at_exactly_max_attempts(
    queue: JobQueue, tenant_id: uuid.UUID, conn: psycopg.Connection
) -> None:
    # max_attempts defaults to 3 in the DB schema.
    job = queue.enqueue(JobType.EXTRACT, {"x": 1}, tenant_id, "key-dead")

    for expected_attempts in (1, 2):
        queue.claim_next("worker-1")
        updated = queue.fail(job.id, f"error {expected_attempts}")
        assert updated.status == JobStatus.QUEUED
        assert updated.attempts == expected_attempts

        # fail() reschedules into the future; force it claimable again for
        # the next iteration/final failure rather than waiting out backoff.
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET run_after = now() WHERE id = %s", (job.id,))
        conn.commit()

    queue.claim_next("worker-1")
    final = queue.fail(job.id, "final error")

    assert final.status == JobStatus.DEAD
    assert final.attempts == 3

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM dead_letters WHERE job_id = %s", (job.id,))
        dead_letter = cur.fetchone()
    assert dead_letter is not None
    assert dead_letter["final_error"] == "final error"
    assert dead_letter["payload"] == {"x": 1}
    assert dead_letter["tenant_id"] == tenant_id


def test_dead_letter_not_created_before_max_attempts(
    queue: JobQueue, tenant_id: uuid.UUID, conn: psycopg.Connection
) -> None:
    job = queue.enqueue(JobType.EXTRACT, {}, tenant_id, "key-not-dead-yet")
    queue.claim_next("worker-1")
    queue.fail(job.id, "first failure")  # attempts=1, max_attempts=3

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM dead_letters WHERE job_id = %s", (job.id,))
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 0


# --- reap_stale_locks --------------------------------------------------


def test_reap_stale_locks_recovers_abandoned_job(
    queue: JobQueue, tenant_id: uuid.UUID, conn: psycopg.Connection
) -> None:
    job = queue.enqueue(JobType.EXTRACT, {}, tenant_id, "key-stale")
    queue.claim_next("worker-1")

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET locked_at = now() - interval '11 minutes' WHERE id = %s",
            (job.id,),
        )
    conn.commit()

    recovered = queue.reap_stale_locks()

    assert recovered == 1
    row = _fetch_row(conn, job.id)
    assert row["status"] == "queued"
    assert row["attempts"] == 1
    assert row["locked_at"] is None
    assert row["locked_by"] is None


def test_reap_stale_locks_ignores_recent_locks(queue: JobQueue, tenant_id: uuid.UUID) -> None:
    queue.enqueue(JobType.EXTRACT, {}, tenant_id, "key-fresh")
    queue.claim_next("worker-1")  # locked_at = now(), well within the window

    assert queue.reap_stale_locks() == 0


def test_reap_stale_locks_ignores_queued_jobs(queue: JobQueue, tenant_id: uuid.UUID) -> None:
    queue.enqueue(JobType.EXTRACT, {}, tenant_id, "key-never-claimed")
    assert queue.reap_stale_locks() == 0


def test_reaped_job_is_claimable_again(
    queue: JobQueue, tenant_id: uuid.UUID, conn: psycopg.Connection
) -> None:
    job = queue.enqueue(JobType.EXTRACT, {}, tenant_id, "key-reclaim")
    queue.claim_next("worker-1")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET locked_at = now() - interval '11 minutes' WHERE id = %s",
            (job.id,),
        )
    conn.commit()
    queue.reap_stale_locks()

    reclaimed = queue.claim_next("worker-2")

    assert reclaimed is not None
    assert reclaimed.id == job.id
    assert reclaimed.locked_by == "worker-2"
