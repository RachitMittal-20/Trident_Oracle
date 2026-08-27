"""SSE bridge for the /pipeline screen (GET /v1/events/stream in main.py).

The worker and apps/api/api/approvals.py never talk to this module, or to
each other, directly -- they only ever UPDATE invoices.status (or INSERT a
new invoices row), exactly as they always have. A DB trigger
(db/migrations/0022_pipeline_events.sql) turns each of those writes into a
`pg_notify` on the `trident_pipeline_events` channel. This module is the
only thing that LISTENs on it. That is the whole point of a NOTIFY bridge:
whichever process publishes an event never has to know this SSE endpoint,
or apps/api as a whole, exists.

listen_for_events() runs once for the lifetime of the app (see main.py's
lifespan) on its own long-lived async connection, and fans each notification
out to per-tenant subscriber queues via EventBroadcaster. It deliberately
does NOT resolve invoice display fields (vendor, amount, invoice number)
itself -- that would require either an unscoped cross-tenant connection
(a BYPASSRLS role, the thing CLAUDE.md's RLS principle warns against
introducing in a request-adjacent path) or re-deriving tenant auth here.
Instead it forwards only what the trigger payload already carries
(invoice_id, tenant_id, from/to status, timestamp); the SSE endpoint
resolves the rest per-event on its own already-tenant-scoped connection
(api/db.py::get_pipeline_card).

A deliberate scope decision: there is no separate "job queued" / "job
started" event distinct from an invoice status write. RECEIVED (at INSERT)
stands in for "job queued", and a transition into EXTRACTING or MATCHING
stands in for "job started" -- both are real, already-existing writes
(apps/api/api/db.py::insert_invoice, and the `_transition` helper in
apps/worker/worker/extract_handler.py / match_handler.py), not synthesized
ones. The consequence: the /pipeline screen can't distinguish "a job row
exists in the queue, unclaimed" from "a worker just claimed it and hasn't
written EXTRACTING yet" -- there's a brief window, bounded by the queue's
own poll interval, where a queued job produces no visible change on this
screen. For this demo's latency profile (single worker, short poll
interval) that window is small enough not to matter, and instrumenting
`jobs` directly (a second trigger, a second NOTIFY channel) would add
real complexity for a distinction the UI doesn't currently need. Revisit
this if a future prompt needs queue-depth visibility independent of
invoice status.
"""

import asyncio
import json
import uuid
from typing import Any

import psycopg
import structlog
from core.errors import UnmappedPipelineStage
from core.models import InvoiceStatus
from core.pipeline_stage import stage_for_status

log = structlog.get_logger()

_CHANNEL = "trident_pipeline_events"
_RECONNECT_DELAY_SECONDS = 2.0


class EventBroadcaster:
    """In-process fan-out of pipeline events to subscribed SSE connections,
    scoped per tenant so one tenant's stream can never observe another's
    rows. RLS governs every *query* in this app; a pg_notify payload is not
    a query result, so this class is what re-establishes that same boundary
    for the one channel that necessarily crosses tenants in a single
    process-wide LISTEN."""

    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, tenant_id: uuid.UUID) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.setdefault(tenant_id, set()).add(queue)
        return queue

    async def unsubscribe(
        self, tenant_id: uuid.UUID, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        async with self._lock:
            subs = self._subscribers.get(tenant_id)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    del self._subscribers[tenant_id]

    async def publish(self, tenant_id: uuid.UUID, event: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(tenant_id, ()))
        for queue in queues:
            # A slow or stalled subscriber must never block delivery to
            # every other subscriber, and must never wedge the single
            # shared LISTEN loop -- drop that one connection's oldest
            # queued event rather than awaiting a full queue.
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)


def _parse_notify_payload(raw_payload: str) -> tuple[uuid.UUID, dict[str, Any]]:
    raw = json.loads(raw_payload)
    to_status = InvoiceStatus(raw["to_status"])
    event = {
        "invoice_id": raw["invoice_id"],
        "from_status": raw["from_status"],
        "to_status": raw["to_status"],
        "stage": stage_for_status(to_status).value,
        "occurred_at": raw["occurred_at"],
    }
    return uuid.UUID(raw["tenant_id"]), event


async def listen_for_events(broadcaster: EventBroadcaster, database_url: str) -> None:
    """Never returns except via cancellation (app shutdown) -- reconnects
    and re-issues LISTEN on any connection loss, since a single bounced
    connection must not silently stop pipeline event delivery for every
    tenant until the next deploy."""
    while True:
        try:
            conn = await psycopg.AsyncConnection.connect(database_url, autocommit=True)
            try:
                await conn.execute(f"LISTEN {_CHANNEL}")
                log.info("pipeline_events_listening")
                async for notification in conn.notifies():
                    try:
                        tenant_id, event = _parse_notify_payload(notification.payload)
                    except (
                        json.JSONDecodeError,
                        KeyError,
                        ValueError,
                        UnmappedPipelineStage,
                    ) as exc:
                        # UnmappedPipelineStage should be unreachable --
                        # test_pipeline_stage.py asserts every InvoiceStatus
                        # maps to a stage -- but if that invariant is ever
                        # violated, one bad event must drop and log, not
                        # crash this loop and stop delivery for every
                        # tenant on the shared LISTEN connection.
                        log.error("pipeline_event_malformed", error=str(exc))
                        continue
                    await broadcaster.publish(tenant_id, event)
            finally:
                await conn.close()
        except psycopg.Error as exc:
            log.warning("pipeline_events_connection_lost", error=str(exc))
            await asyncio.sleep(_RECONNECT_DELAY_SECONDS)
