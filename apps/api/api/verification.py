"""Field-correction and re-run-match flow for the /invoices/[id]/verify
screen. Route handlers in main.py validate the request shape and call
straight into these two functions; the actual reads/writes live here and
in api/db.py, never in the route handler itself (CLAUDE.md: "Do not put
business logic in API route handlers").

field_path format mirrors field_confidences.field_path exactly (see
packages/extractors/extractors/base.py's docstring and
db/migrations/0004_invoices.sql's comment on it): `header.<column>` for an
invoice-level field, `lines[<n>].<column>` for a line item, `<n>` being the
extractor's own 0-based index -- one higher than invoice_lines.line_no,
which extract_handler.py assigns starting at 1.
"""

import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
from core.errors import TridentOracleError
from core.models import InvoiceStatus
from core.state_machine import validate_transition
from psycopg.rows import dict_row

from api import db

_HEADER_COLUMNS = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "currency",
    "subtotal",
    "tax",
    "total",
)
_LINE_COLUMNS = ("description", "qty", "unit_price", "line_total")
_HEADER_FIELD_RE = re.compile(r"^header\.(" + "|".join(_HEADER_COLUMNS) + r")$")
_LINE_FIELD_RE = re.compile(r"^lines\[(\d+)\]\.(" + "|".join(_LINE_COLUMNS) + r")$")

_DECIMAL_COLUMNS = {"subtotal", "tax", "total", "qty", "unit_price", "line_total"}
_DATE_COLUMNS = {"invoice_date", "due_date"}


class InvalidFieldPath(TridentOracleError):
    """field_path does not match header.<column> or lines[<n>].<column>,
    or its value doesn't parse as that column's type."""


class FieldNotFound(TridentOracleError):
    """field_path parses correctly but no matching invoice or line row
    exists (e.g. a line index beyond the invoice's actual line count)."""


def _coerce(column: str, raw_value: str) -> Any:
    if column in _DECIMAL_COLUMNS:
        try:
            return Decimal(raw_value)
        except InvalidOperation as exc:
            raise InvalidFieldPath(f"{column!r} must be a valid number, got {raw_value!r}") from exc
    if column in _DATE_COLUMNS:
        try:
            return date.fromisoformat(raw_value)
        except ValueError as exc:
            raise InvalidFieldPath(f"{column!r} must be an ISO date, got {raw_value!r}") from exc
    return raw_value


def correct_field(
    conn: psycopg.Connection[Any],
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    field_path: str,
    raw_value: str,
    actor_id: str | None,
) -> dict[str, Any]:
    """Writes the corrected value to invoices or invoice_lines, marks the
    matching field_confidences row human_corrected (confidence set to 1.0
    -- a human just confirmed it, so the extractor's original score is no
    longer a meaningful description of this field), and writes one
    audit_log row (action='invoice_corrected', per audit_log's own column
    comment in 0008_audit.sql). All three writes share the caller's
    transaction; the caller commits.
    """
    header_match = _HEADER_FIELD_RE.match(field_path)
    line_match = _LINE_FIELD_RE.match(field_path)

    if header_match:
        column = header_match.group(1)
        value = _coerce(column, raw_value)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {column} FROM invoices WHERE id = %s", (invoice_id,))  # noqa: S608
            row = cur.fetchone()
            if row is None:
                raise FieldNotFound(f"invoice {invoice_id} not found")
            before = row[column]
            cur.execute(
                f"UPDATE invoices SET {column} = %s, updated_at = now() WHERE id = %s",  # noqa: S608
                (value, invoice_id),
            )
    elif line_match:
        line_no = int(line_match.group(1)) + 1
        column = line_match.group(2)
        value = _coerce(column, raw_value)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT id, {column} FROM invoice_lines "  # noqa: S608
                "WHERE invoice_id = %s AND line_no = %s",
                (invoice_id, line_no),
            )
            row = cur.fetchone()
            if row is None:
                raise FieldNotFound(f"no line {line_no} on invoice {invoice_id}")
            before = row[column]
            cur.execute(
                f"UPDATE invoice_lines SET {column} = %s WHERE id = %s",  # noqa: S608
                (value, row["id"]),
            )
    else:
        raise InvalidFieldPath(f"unrecognized field_path {field_path!r}")

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE field_confidences
            SET confidence = 1.0, human_corrected = true, corrected_at = now()
            WHERE invoice_id = %(invoice_id)s AND field_path = %(field_path)s
            """,
            {"invoice_id": invoice_id, "field_path": field_path},
        )

    db.insert_audit_log(
        conn,
        tenant_id=tenant_id,
        actor_type="user",
        actor_id=actor_id,
        action="invoice_corrected",
        entity_type="invoice",
        entity_id=invoice_id,
        before={field_path: str(before) if before is not None else None},
        after={field_path: str(value)},
    )
    return {"field_path": field_path, "value": str(value)}


def rerun_match(
    conn: psycopg.Connection[Any],
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    actor_id: str | None,
) -> uuid.UUID:
    """NEEDS_VERIFICATION -> MATCHING, then enqueues a fresh match job.

    The idempotency key includes a fresh uuid4 rather than the fixed
    sha256(tenant:invoice:match) key extract_handler.py's own first match
    job uses (apps/worker/worker/extract_handler.py) -- that fixed key is
    right for "don't double-enqueue the one match job this extraction
    triggers", but wrong here: a re-run is deliberately a new, distinct
    unit of work, and reusing the original key would just return the
    original (already 'done') job row unchanged via enqueue_job's
    on-conflict-do-nothing-but-return semantics, silently not re-running
    anything.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s", (invoice_id,))
        row = cur.fetchone()
    if row is None:
        raise FieldNotFound(f"invoice {invoice_id} not found")

    current_status = InvoiceStatus(row["status"])
    validate_transition(current_status, InvoiceStatus.MATCHING)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE invoices SET status = %s, updated_at = now() WHERE id = %s",
            (InvoiceStatus.MATCHING.value, invoice_id),
        )
    db.insert_audit_log(
        conn,
        tenant_id=tenant_id,
        actor_type="user",
        actor_id=actor_id,
        action="status_transition",
        entity_type="invoice",
        entity_id=invoice_id,
        before={"status": current_status.value},
        after={"status": InvoiceStatus.MATCHING.value},
    )

    idempotency_key = f"{tenant_id}:{invoice_id}:match:{uuid.uuid4()}"
    return db.enqueue_job(
        conn,
        tenant_id=tenant_id,
        job_type="match",
        payload={"invoice_id": str(invoice_id)},
        idempotency_key=idempotency_key,
    )
