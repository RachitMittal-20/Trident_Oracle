"""Read model for the /invoices full-list screen -- server-side paginated,
sortable, status-filterable, plus the status distribution counts the
list's header bar animates in from.
"""

from decimal import Decimal
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row

SortField = Literal["invoice_date", "total", "status", "created_at", "invoice_number"]
SortOrder = Literal["asc", "desc"]

_SORT_COLUMNS: dict[str, str] = {
    "invoice_date": "i.invoice_date",
    "total": "i.total",
    "status": "i.status",
    "created_at": "i.created_at",
    "invoice_number": "i.invoice_number",
}

# Mirrors core.models.InvoiceStatus exactly -- see components/status-pill.tsx's own
# copy of this list on the frontend for why it's duplicated rather than imported.
_ALL_STATUSES = (
    "RECEIVED", "EXTRACTING", "EXTRACTION_FAILED", "EXTRACTED", "MATCHING",
    "MATCHED_CLEAN", "NEEDS_VERIFICATION", "EXCEPTIONS_RAISED", "AUTO_POSTED",
    "PENDING_APPROVAL", "APPROVED", "REJECTED", "POSTED",
)


def _decimal_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def list_invoices(
    conn: psycopg.Connection[Any],
    *,
    status: str | None = None,
    sort: SortField = "created_at",
    order: SortOrder = "desc",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    clauses = ["true"]
    params: dict[str, Any] = {}
    if status is not None:
        clauses.append("i.status = %(status)s")
        params["status"] = status

    sort_column = _SORT_COLUMNS[sort]
    direction = "ASC" if order == "asc" else "DESC"
    offset = max(0, (page - 1) * page_size)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT count(*) AS n FROM invoices i WHERE {' AND '.join(clauses)}",  # noqa: S608
            params,
        )
        total_row = cur.fetchone()
        total = total_row["n"] if total_row else 0

        cur.execute(
            f"""
            SELECT i.id, i.invoice_number, i.invoice_date, i.currency, i.total, i.status,
                   i.created_at, i.vendor_id, v.name AS vendor_name
            FROM invoices i
            LEFT JOIN vendors v ON v.id = i.vendor_id
            WHERE {" AND ".join(clauses)}
            ORDER BY {sort_column} {direction}, i.created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,  # noqa: S608 -- sort_column/direction are from the fixed map above, never user input
            {**params, "limit": page_size, "offset": offset},
        )
        rows = cur.fetchall()

        cur.execute("SELECT status, count(*) AS n FROM invoices GROUP BY status")
        status_counts = {row["status"]: row["n"] for row in cur.fetchall()}

    return {
        "items": [
            {
                "id": str(row["id"]),
                "invoice_number": row["invoice_number"],
                "invoice_date": row["invoice_date"].isoformat() if row["invoice_date"] else None,
                "currency": row["currency"],
                "total": _decimal_str(row["total"]),
                "status": row["status"],
                "created_at": row["created_at"].isoformat(),
                "vendor_id": str(row["vendor_id"]) if row["vendor_id"] else None,
                "vendor_name": row["vendor_name"],
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "status_counts": {s: status_counts.get(s, 0) for s in _ALL_STATUSES},
    }
