"""Job and DeadLetter value objects. Mirror the jobs/dead_letters DB schema
(db/migrations/0005_queue.sql) exactly, but are pure data -- no ORM, no
session, no query methods. apps/worker's DB access layer constructs these
from query results and never the other way around.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.errors import TridentOracleError
from core.models import JobStatus, JobType


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise TridentOracleError(message)


@dataclass(frozen=True, slots=True)
class Job:
    id: uuid.UUID
    tenant_id: uuid.UUID
    job_type: JobType
    payload: dict[str, Any]
    status: JobStatus
    attempts: int
    max_attempts: int
    idempotency_key: str
    run_after: datetime
    created_at: datetime
    updated_at: datetime
    locked_at: datetime | None = None
    locked_by: str | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        _check(self.attempts >= 0, "Job.attempts must not be negative")
        _check(self.max_attempts > 0, "Job.max_attempts must be positive")
        _check(bool(self.idempotency_key.strip()), "Job.idempotency_key must not be blank")


@dataclass(frozen=True, slots=True)
class DeadLetter:
    id: uuid.UUID
    tenant_id: uuid.UUID
    job_id: uuid.UUID
    payload: dict[str, Any]
    final_error: str
    created_at: datetime

    def __post_init__(self) -> None:
        _check(bool(self.final_error.strip()), "DeadLetter.final_error must not be blank")
