"""job_type -> handler function registry. A handler receives the connection
the worker set app.tenant_id on for this job, plus the claimed Job itself,
and returns nothing -- raising is how a handler signals failure (the poll
loop converts that into queue.fail(), see worker/main.py)."""

from collections.abc import Callable
from typing import Any

import psycopg
from core.models import JobType
from core.queue.models import Job

Handler = Callable[[psycopg.Connection[Any], Job], None]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[JobType, Handler] = {}

    def register(self, job_type: JobType, handler: Handler) -> None:
        self._handlers[job_type] = handler

    def get(self, job_type: JobType) -> Handler | None:
        return self._handlers.get(job_type)
