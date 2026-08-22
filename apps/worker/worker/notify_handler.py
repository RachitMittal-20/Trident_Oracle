"""The 'notify' job handler: sends an approval/exception notification
through whichever channel notifiers.factory resolves, tracking delivery
status in notification_deliveries (db/migrations/0005_queue.sql,
0020_notification_deliveries_invoice_context.sql) so a retried send can
never produce a second message.

job.payload contract -- whoever enqueues a 'notify' job must supply:
    invoice_id:   str (uuid)
    exception_id: str (uuid) | null
    recipient:    str   -- channel-shaped: a Telegram chat id, an email address
    channel:      "telegram" | "email" | "whatsapp"
    title:        str
    body:         str   -- markdown, this codebase's own **bold**-only convention
    actions:      list[{"label": str, "action_id": str, "style": str}]  -- optional

apps/worker/worker/match_handler.py's notify-enqueue call is the real
producer of this payload: it resolves decision.required_approvers actual
approver contacts (users.telegram_chat_id if set, else users.email --
db/migrations/0021_users_telegram_chat_id.sql), mints one approval token per
approver (its own _issue_approval_token, not imported from
apps/api/api/approvals.py -- see match_handler.py's module docstring for
why), and enqueues one notify job per approver with max_attempts=5.

Idempotency: the INSERT into notification_deliveries happens BEFORE the
notifier is ever called, via the same idempotent-upsert trick
JobQueue.enqueue uses for `jobs` (ON CONFLICT (idempotency_key) DO UPDATE
SET idempotency_key = ... RETURNING *) -- one atomic statement, no
SELECT-then-INSERT race window. If that row already has status 'sent' or
'dead', this handler returns immediately without calling the notifier at
all -- "the idempotency key is checked first and a completed delivery
short-circuits" is exactly this early return.

Retry/dead-letter split:
  - RetryableNotificationError: record the failure (status stays 'pending'
    unless this was the job's last allowed attempt, in which case status
    becomes 'dead'), then re-raise so worker/main.py's run_one_job ->
    queue.fail() retries the underlying JOB with its own backoff, up to
    job.max_attempts (5, for notify jobs).
  - NotificationError (not retryable) or NotImplementedError (WhatsAppNotifier's
    documented stub): a permanent failure. Marked 'dead' immediately,
    regardless of how many job attempts remain -- retrying something that
    will never succeed wastes attempts. If the queue still re-invokes this
    job before its own attempts exhaust, the idempotency short-circuit above
    makes every further invocation a cheap no-op.
  - Either way, 'dead' is the visible-alert mechanism GET /v1/deliveries
    (api/main.py) surfaces to the dashboard -- no separate alerting system
    exists or is needed for this.
"""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
import structlog
from core.errors import NotificationError
from core.queue.backoff import compute_backoff
from core.queue.models import Job
from notifiers.base import NotificationAction, NotificationMessage
from notifiers.errors import RetryableNotificationError
from notifiers.factory import get_notifier
from psycopg.rows import DictRow, dict_row

from worker.db import DEFAULT_BASE_DELAY, JobQueue

log = structlog.get_logger()


def _idempotency_key(
    tenant_id: uuid.UUID, exception_id: uuid.UUID | None, recipient: str, channel: str
) -> str:
    return hashlib.sha256(f"{tenant_id}:{exception_id}:{recipient}:{channel}".encode()).hexdigest()


def _get_or_create_delivery(
    conn: psycopg.Connection[Any],
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    exception_id: uuid.UUID | None,
    channel: str,
    recipient: str,
    idempotency_key: str,
) -> DictRow:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO notification_deliveries
                (tenant_id, invoice_id, exception_id, channel, recipient, idempotency_key, status)
            VALUES (%(tenant_id)s, %(invoice_id)s, %(exception_id)s, %(channel)s, %(recipient)s,
                    %(idempotency_key)s, 'pending')
            ON CONFLICT (idempotency_key)
                DO UPDATE SET idempotency_key = notification_deliveries.idempotency_key
            RETURNING *
            """,
            {
                "tenant_id": tenant_id,
                "invoice_id": invoice_id,
                "exception_id": exception_id,
                "channel": channel,
                "recipient": recipient,
                "idempotency_key": idempotency_key,
            },
        )
        row = cur.fetchone()
    conn.commit()
    if row is None:
        raise RuntimeError("notification_deliveries INSERT ... RETURNING produced no row")
    return row


def _mark_sent(
    conn: psycopg.Connection[Any], delivery_id: uuid.UUID, provider_message_id: str | None
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE notification_deliveries
            SET status = 'sent', provider_message_id = %s, sent_at = now()
            WHERE id = %s
            """,
            (provider_message_id, delivery_id),
        )
    conn.commit()


def _mark_failure(
    conn: psycopg.Connection[Any],
    delivery_id: uuid.UUID,
    error: str,
    *,
    dead: bool,
    attempts: int,
    next_retry_at: datetime | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE notification_deliveries
            SET status = %s, attempts = %s, error = %s, next_retry_at = %s
            WHERE id = %s
            """,
            ("dead" if dead else "pending", attempts, error, next_retry_at, delivery_id),
        )
    conn.commit()


def handle_notify(conn: psycopg.Connection[Any], queue: JobQueue, job: Job) -> None:
    tenant_id = job.tenant_id
    payload = job.payload

    invoice_id = uuid.UUID(payload["invoice_id"])
    exception_id = uuid.UUID(payload["exception_id"]) if payload.get("exception_id") else None
    recipient = payload["recipient"]
    channel = payload["channel"]
    title = payload["title"]
    body = payload["body"]
    actions = tuple(
        NotificationAction(
            label=action["label"],
            action_id=action["action_id"],
            style=action.get("style", "secondary"),
        )
        for action in payload.get("actions", [])
    )

    idempotency_key = _idempotency_key(tenant_id, exception_id, recipient, channel)
    delivery = _get_or_create_delivery(
        conn, tenant_id, invoice_id, exception_id, channel, recipient, idempotency_key
    )

    if delivery["status"] in ("sent", "dead"):
        log.info(
            "notify_short_circuited",
            invoice_id=str(invoice_id),
            status=delivery["status"],
        )
        return

    message = NotificationMessage(title=title, body=body, actions=actions)
    notifier = get_notifier(channel)
    is_last_attempt = job.attempts + 1 >= job.max_attempts

    try:
        result = notifier.send(recipient, message, idempotency_key)
    except RetryableNotificationError as exc:
        next_retry_at = (
            None
            if is_last_attempt
            else datetime.now(UTC) + compute_backoff(job.attempts + 1, DEFAULT_BASE_DELAY)
        )
        _mark_failure(
            conn,
            delivery["id"],
            str(exc),
            dead=is_last_attempt,
            attempts=delivery["attempts"] + 1,
            next_retry_at=next_retry_at,
        )
        log.warning(
            "notify_retryable_failure",
            invoice_id=str(invoice_id),
            channel=channel,
            is_last_attempt=is_last_attempt,
            error=str(exc),
        )
        raise
    except (NotificationError, NotImplementedError) as exc:
        # Permanent failure -- dead-letter this delivery immediately rather
        # than let it occupy up to job.max_attempts retries for something
        # that will never succeed.
        _mark_failure(
            conn,
            delivery["id"],
            str(exc),
            dead=True,
            attempts=delivery["attempts"] + 1,
            next_retry_at=None,
        )
        log.error(
            "notify_permanent_failure", invoice_id=str(invoice_id), channel=channel, error=str(exc)
        )
        raise

    _mark_sent(conn, delivery["id"], result.provider_message_id)
    log.info(
        "notify_sent",
        invoice_id=str(invoice_id),
        channel=channel,
        provider_message_id=result.provider_message_id,
    )
