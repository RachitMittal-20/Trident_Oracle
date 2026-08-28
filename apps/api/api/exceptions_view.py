"""Read model and resolve-action for the /exceptions work queue.

Deliberately separate from apps/api/api/match_view.py::decide_invoice:
resolving one exception here does not approve or reject the invoice it
belongs to -- that stays match_view's job, the only place an invoice's
status actually transitions. This is a lighter review action ("I looked at
this finding, it's handled") a clerk or approver can take on one exception
without formally deciding the whole invoice -- exactly what
match_exceptions.status/resolved_by/resolved_at/resolution_note were
already shaped to record (db/migrations/0006_matching.sql), independent of
approval_requests entirely. Unlike decide_invoice, this has no role gate:
triaging a finding isn't a financial approval.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

import psycopg
from core.errors import TridentOracleError
from psycopg.rows import dict_row

from api import db

SortField = Literal["severity", "age", "amount"]
SortOrder = Literal["asc", "desc"]

_SEVERITY_RANK_SQL = "CASE e.severity WHEN 'block' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END"
_SORT_EXPRESSIONS: dict[str, str] = {
    "severity": _SEVERITY_RANK_SQL,
    "age": "e.created_at",
    "amount": "i.total",
}


class ExceptionNotFound(TridentOracleError):
    """No match_exceptions row with this id under the current tenant."""


class ExceptionAlreadySettled(TridentOracleError):
    """The exception is no longer open -- either a clerk already resolved it
    from this queue, or match_view.py::decide_invoice already resolved/
    dismissed it (and every open exception on that invoice) as a side
    effect of a formal approve/reject decision. Either way, re-resolving it
    here would silently overwrite resolved_by/resolved_at/resolution_note
    -- e.g. clobbering "rejected in-app on the match screen" with "resolved
    from the exceptions queue" -- so this is raised instead, same principle
    as core.state_machine.validate_transition never letting an illegal
    invoice transition silently no-op."""


def _decimal_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def list_exceptions(
    conn: psycopg.Connection[Any],
    *,
    status: str = "open",
    severity: str | None = None,
    exception_type: str | None = None,
    vendor_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: SortField = "age",
    order: SortOrder = "desc",
) -> dict[str, Any]:
    clauses = ["e.status = %(status)s"]
    params: dict[str, Any] = {"status": status}
    if severity is not None:
        clauses.append("e.severity = %(severity)s")
        params["severity"] = severity
    if exception_type is not None:
        clauses.append("e.exception_type = %(exception_type)s")
        params["exception_type"] = exception_type
    if vendor_id is not None:
        clauses.append("i.vendor_id = %(vendor_id)s")
        params["vendor_id"] = vendor_id
    if date_from is not None:
        clauses.append("e.created_at >= %(date_from)s")
        params["date_from"] = date_from
    if date_to is not None:
        clauses.append("e.created_at < %(date_to)s + interval '1 day'")
        params["date_to"] = date_to

    sort_expr = _SORT_EXPRESSIONS[sort]
    direction = "ASC" if order == "asc" else "DESC"

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT
                e.id, e.exception_type, e.severity, e.detail, e.status, e.created_at,
                e.invoice_id, e.expected_value, e.actual_value, e.delta, e.delta_pct,
                i.invoice_number, i.total AS invoice_total, i.currency, i.vendor_id,
                v.name AS vendor_name
            FROM match_exceptions e
            JOIN invoices i ON i.id = e.invoice_id
            LEFT JOIN vendors v ON v.id = i.vendor_id
            WHERE {" AND ".join(clauses)}
            ORDER BY {sort_expr} {direction}, e.created_at DESC
            """,  # noqa: S608 -- sort_expr/direction are from the fixed maps above, never user input
            params,
        )
        rows = cur.fetchall()

        cur.execute(
            """
            SELECT count(*) AS n FROM invoices
            WHERE status = 'AUTO_POSTED' AND updated_at >= now() - interval '7 days'
            """
        )
        auto_posted_row = cur.fetchone()
        auto_posted_this_week = auto_posted_row["n"] if auto_posted_row else 0

        cur.execute("SELECT DISTINCT id, name FROM vendors ORDER BY name")
        vendor_rows = cur.fetchall()

    return {
        "exceptions": [
            {
                "id": str(row["id"]),
                "exception_type": row["exception_type"],
                "severity": row["severity"],
                "detail": row["detail"],
                "status": row["status"],
                "created_at": row["created_at"].isoformat(),
                "invoice_id": str(row["invoice_id"]),
                "invoice_number": row["invoice_number"],
                "invoice_total": _decimal_str(row["invoice_total"]),
                "currency": row["currency"],
                "vendor_id": str(row["vendor_id"]) if row["vendor_id"] else None,
                "vendor_name": row["vendor_name"],
                "expected_value": _decimal_str(row["expected_value"]),
                "actual_value": _decimal_str(row["actual_value"]),
                "delta": _decimal_str(row["delta"]),
                "delta_pct": _decimal_str(row["delta_pct"]),
            }
            for row in rows
        ],
        "auto_posted_this_week": auto_posted_this_week,
        "vendors": [{"id": str(row["id"]), "name": row["name"]} for row in vendor_rows],
    }


def resolve_exception(
    conn: psycopg.Connection[Any],
    tenant_id: uuid.UUID,
    exception_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    note: str | None,
) -> None:
    now = datetime.now(UTC)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, invoice_id, status FROM match_exceptions WHERE id = %s FOR UPDATE",
            (exception_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise ExceptionNotFound(f"exception {exception_id} not found")
    if row["status"] != "open":
        raise ExceptionAlreadySettled(
            f"exception {exception_id} is already {row['status']}, not open"
        )

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE match_exceptions
            SET status = 'resolved', resolved_by = %(actor)s, resolved_at = %(now)s,
                resolution_note = %(note)s
            WHERE id = %(id)s AND status = 'open'
            """,
            {
                "actor": actor_user_id,
                "now": now,
                "note": note or "resolved from the exceptions queue",
                "id": exception_id,
            },
        )

    db.insert_audit_log(
        conn,
        tenant_id=tenant_id,
        actor_type="user",
        actor_id=str(actor_user_id),
        action="exception_resolved",
        entity_type="match_exception",
        entity_id=exception_id,
        before={"status": row["status"]},
        after={"status": "resolved"},
    )
