"""Postgres access layer for the API -- connects as app_role, with
app.tenant_id set per request so RLS scopes every query to that request's
tenant (db/README.md's "Security model").

enqueue_job() mirrors apps/worker/worker/db.py's JobQueue.enqueue() (same
idempotent-upsert SQL) rather than importing it -- apps/api and apps/worker
are separate deployables that don't depend on each other. Unlike the
worker's queue_claimer connection, this runs as app_role: enqueuing is
always for a specific, already-known tenant (this request's), so it's
naturally RLS-scoped rather than needing cross-tenant visibility.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg
from core.models import InvoiceStatus
from core.pipeline_stage import stage_for_status
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb


def set_tenant(conn: psycopg.Connection[Any], tenant_id: uuid.UUID) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))


def find_invoice_by_content_hash(
    conn: psycopg.Connection[Any], tenant_id: uuid.UUID, content_hash: str
) -> uuid.UUID | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM invoices WHERE tenant_id = %s AND content_hash = %s",
            (tenant_id, content_hash),
        )
        row = cur.fetchone()
    return row[0] if row is not None else None


def insert_invoice(
    conn: psycopg.Connection[Any],
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID,
    source_file_path: str,
    content_hash: str,
    currency: str = "USD",
    source_channel: str = "upload",
) -> None:
    # currency defaults to USD, a real (if provisional) value rather than a
    # null-like sentinel -- unlike invoice_number/date/amounts, which are
    # genuinely unknowable pre-extraction, a platform-wide default currency
    # is a reasonable placeholder that the extract handler overwrites once
    # the real value is known.
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO invoices
                (id, tenant_id, currency, source_channel, source_file_path,
                 content_hash, status)
            VALUES (%(id)s, %(tenant_id)s, %(currency)s, %(source_channel)s, %(source_file_path)s,
                    %(content_hash)s, 'RECEIVED')
            """,
            {
                "id": invoice_id,
                "tenant_id": tenant_id,
                "currency": currency,
                "source_channel": source_channel,
                "source_file_path": source_file_path,
                "content_hash": content_hash,
            },
        )


def enqueue_job(
    conn: psycopg.Connection[Any],
    tenant_id: uuid.UUID,
    job_type: str,
    payload: dict[str, Any],
    idempotency_key: str,
    run_after: datetime | None = None,
) -> uuid.UUID:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO jobs (tenant_id, job_type, payload, idempotency_key, run_after)
            VALUES (%(tenant_id)s, %(job_type)s, %(payload)s, %(idempotency_key)s,
                    coalesce(%(run_after)s, now()))
            ON CONFLICT (idempotency_key)
                DO UPDATE SET idempotency_key = jobs.idempotency_key
            RETURNING id
            """,
            {
                "tenant_id": tenant_id,
                "job_type": job_type,
                "payload": Jsonb(payload),
                "idempotency_key": idempotency_key,
                "run_after": run_after,
            },
        )
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("enqueue_job INSERT ... RETURNING produced no row")
    job_id: uuid.UUID = row["id"]
    return job_id


def insert_audit_log(
    conn: psycopg.Connection[Any],
    tenant_id: uuid.UUID,
    actor_type: str,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    actor_id: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_log
                (tenant_id, actor_type, actor_id, action, entity_type, entity_id, before, after)
            VALUES (%(tenant_id)s, %(actor_type)s, %(actor_id)s, %(action)s, %(entity_type)s,
                    %(entity_id)s, %(before)s, %(after)s)
            """,
            {
                "tenant_id": tenant_id,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "before": Jsonb(before) if before is not None else None,
                "after": Jsonb(after) if after is not None else None,
            },
        )


def get_invoice(conn: psycopg.Connection[Any], invoice_id: uuid.UUID) -> DictRow | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
        return cur.fetchone()


def get_active_policy_min_confidence(
    conn: psycopg.Connection[Any], tenant_id: uuid.UUID
) -> Decimal | None:
    """The active tolerance_policies row's min_field_confidence, for the
    verification screen's below-threshold styling and count -- None if
    this tenant has no active policy (mirrors apps/worker/worker/
    match_handler.py's own "no active tolerance policy" lookup, but read
    -only and non-raising: a missing policy here just means the frontend
    can't color-code confidence yet, not a failed match run)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT rules ->> 'min_field_confidence' AS min_field_confidence
            FROM tolerance_policies
            WHERE tenant_id = %s AND is_active = true
            ORDER BY version DESC LIMIT 1
            """,
            (tenant_id,),
        )
        row = cur.fetchone()
    if row is None or row["min_field_confidence"] is None:
        return None
    return Decimal(row["min_field_confidence"])


def get_invoice_lines(conn: psycopg.Connection[Any], invoice_id: uuid.UUID) -> list[DictRow]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM invoice_lines WHERE invoice_id = %s ORDER BY line_no",
            (invoice_id,),
        )
        return cur.fetchall()


def get_field_confidences(conn: psycopg.Connection[Any], invoice_id: uuid.UUID) -> list[DictRow]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM field_confidences WHERE invoice_id = %s", (invoice_id,))
        return cur.fetchall()


def list_notification_deliveries(
    conn: psycopg.Connection[Any],
    *,
    status: str | None = None,
    channel: str | None = None,
    invoice_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DictRow]:
    """RLS (tenant_isolation on notification_deliveries) does the actual
    tenant scoping here -- set_tenant() must already have been called on
    `conn` by the caller, same as every other query in this module. Filters
    are appended as plain AND clauses; each is parameterized, never
    string-interpolated, regardless of how few are actually supplied.
    """
    clauses = ["true"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status is not None:
        clauses.append("status = %(status)s")
        params["status"] = status
    if channel is not None:
        clauses.append("channel = %(channel)s")
        params["channel"] = channel
    if invoice_id is not None:
        clauses.append("invoice_id = %(invoice_id)s")
        params["invoice_id"] = invoice_id

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT * FROM notification_deliveries
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,  # noqa: S608 -- clauses are fixed strings chosen from a closed set above, never user input
            params,
        )
        return cur.fetchall()


_PIPELINE_TERMINAL_STATUSES = ("POSTED", "REJECTED", "AUTO_POSTED")

_PIPELINE_CARD_SELECT = """
    SELECT i.id AS invoice_id, i.invoice_number, i.total, i.currency, i.status,
           i.created_at, i.updated_at, v.name AS vendor_name
    FROM invoices i
    LEFT JOIN vendors v ON v.id = i.vendor_id
"""


def _pipeline_card(row: DictRow) -> dict[str, Any]:
    # Money is Decimal end to end -- str(), never float(), so a JSON-encoded
    # amount can't silently lose precision on the wire (CLAUDE.md: "Money is
    # Decimal, never float").
    return {
        "invoice_id": str(row["invoice_id"]),
        "invoice_number": row["invoice_number"],
        "amount": str(row["total"]) if row["total"] is not None else None,
        "currency": row["currency"],
        "status": row["status"],
        "stage": stage_for_status(InvoiceStatus(row["status"])).value,
        "vendor_name": row["vendor_name"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def list_pipeline_invoices(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    """Snapshot sent as the first SSE message on every /v1/events/stream
    connection (including a client's reconnect) -- current occupants of the
    rail, the PENDING_APPROVAL holding column, and the EXTRACTION_FAILED
    failures tray, for this request's tenant (set_tenant() must already have
    been called; RLS does the actual scoping). Excludes AUTO_POSTED, POSTED,
    and REJECTED: those are terminal, already animated out on every client
    that was connected live, and a reconnecting client does not need that
    history replayed."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            _PIPELINE_CARD_SELECT
            + f" WHERE i.status NOT IN {tuple(_PIPELINE_TERMINAL_STATUSES)!r} ORDER BY i.created_at"  # noqa: S608
        )
        rows = cur.fetchall()
    return [_pipeline_card(row) for row in rows]


def get_pipeline_card(
    conn: psycopg.Connection[Any], invoice_id: uuid.UUID
) -> dict[str, Any] | None:
    """Fetches display fields (vendor, amount, invoice number) for one
    invoice at live-event-delivery time. Called from the SSE endpoint's own
    per-request, already-tenant-scoped connection -- not from the shared
    LISTEN bridge -- so RLS applies exactly as it does to every other query
    in this module; nothing here needs a BYPASSRLS role."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_PIPELINE_CARD_SELECT + " WHERE i.id = %s", (invoice_id,))
        row = cur.fetchone()
    return _pipeline_card(row) if row is not None else None
