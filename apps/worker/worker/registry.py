"""job_type -> handler function registry. A handler receives the connection
the worker set app.tenant_id on for this job (app_role, RLS-scoped), the
queue_claimer-backed JobQueue (so a handler can enqueue a follow-up job,
e.g. extract -> match, using the same idempotent-enqueue path everything
else does), and the claimed Job itself. Returns nothing -- raising is how a
handler signals failure (the poll loop converts that into queue.fail(), see
worker/main.py)."""

from collections.abc import Callable
from typing import Any

import psycopg
from core.models import JobType
from core.queue.models import Job

from worker.db import JobQueue

Handler = Callable[[psycopg.Connection[Any], JobQueue, Job], None]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[JobType, Handler] = {}

    def register(self, job_type: JobType, handler: Handler) -> None:
        self._handlers[job_type] = handler

    def get(self, job_type: JobType) -> Handler | None:
        return self._handlers.get(job_type)
