import uuid
from datetime import UTC, datetime

import pytest
from core.errors import TridentOracleError
from core.models import JobStatus, JobType
from core.queue.models import DeadLetter, Job

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_job(**overrides: object) -> Job:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        job_type=JobType.EXTRACT,
        payload={},
        status=JobStatus.QUEUED,
        attempts=0,
        max_attempts=3,
        idempotency_key="key-1",
        run_after=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    fields.update(overrides)
    return Job(**fields)  # type: ignore[arg-type]


def make_dead_letter(**overrides: object) -> DeadLetter:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        payload={},
        final_error="boom",
        created_at=NOW,
    )
    fields.update(overrides)
    return DeadLetter(**fields)  # type: ignore[arg-type]


def test_valid_job_constructs() -> None:
    make_job()


def test_valid_dead_letter_constructs() -> None:
    make_dead_letter()


def test_job_rejects_negative_attempts() -> None:
    with pytest.raises(TridentOracleError):
        make_job(attempts=-1)


def test_job_rejects_zero_max_attempts() -> None:
    with pytest.raises(TridentOracleError):
        make_job(max_attempts=0)


def test_job_rejects_blank_idempotency_key() -> None:
    with pytest.raises(TridentOracleError):
        make_job(idempotency_key="   ")


def test_dead_letter_rejects_blank_final_error() -> None:
    with pytest.raises(TridentOracleError):
        make_dead_letter(final_error="")


def test_job_is_frozen() -> None:
    job = make_job()
    with pytest.raises(AttributeError):
        job.status = JobStatus.DONE  # type: ignore[misc]
