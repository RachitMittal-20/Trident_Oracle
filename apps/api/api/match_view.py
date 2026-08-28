"""Read model and decide-action for the /invoices/[id]/match screen.

get_match_view assembles PO lines, received (GRN) quantities, invoice
lines, match exceptions, and the latest match run into one response --
read-only, no business logic beyond aggregating goods_receipt_lines by
purchase_order_line (a GRN can have multiple partial-receipt lines against
the same PO line; the screen wants one "received" number per PO line, not
one per receipt).

decide_invoice is the in-app counterpart to apps/api/api/approvals.py's
token-based redeem_approval_token -- this screen shows an already-known
invoice to an already-identified user, so there is no token to redeem.
But it settles exactly one approver's own slot per call, the same way
clicking a token link does -- it does NOT collapse every open
approval_requests row on the first call. apps/worker/worker/
match_handler.py's _apply_decision creates one approval_requests row per
`Decision.required_approvers` (a dual-approval invoice gets two, each
addressed to a different approver's contact). Consuming all of them on one
person's decision would make dual approval meaningless: a single approver
could unilaterally post a large invoice a policy specifically required two
people to sign off on.

Since approval_requests has no approver_id/user_id column (only
`recipient`, a channel address -- telegram_chat_id or email), "which row
is this caller's own" is resolved by matching the acting user's own
contact fields against `recipient`. A caller with no live (unconsumed) row
addressed to them -- because they were never one of the invited approvers,
or because they already decided -- cannot decide again; see
NoPendingApprovalForActor.

Reject and approve are deliberately asymmetric, same as most real
approval workflows: a single reject is a veto and settles the invoice
immediately (every other still-open row for it becomes moot and is closed
out too), but approve only settles the invoice once *every* required
approver's row has independently been marked approved.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import psycopg
from core.errors import TridentOracleError
from core.models import InvoiceStatus
from core.state_machine import validate_transition
from psycopg.rows import DictRow, dict_row

from api import db

DecisionValue = str  # "approved" | "rejected"

_APPROVER_ROLES = ("admin", "approver")


class NotAuthorizedToDecide(TridentOracleError):
    """The acting user's role does not permit deciding on approvals
    (users.role must be 'admin' or 'approver' -- db/migrations/0002_tenancy.sql)."""


class InvoiceNotFound(TridentOracleError):
    """No invoice with this id under the current tenant (RLS-scoped)."""


class NoPendingApprovalForActor(TridentOracleError):
    """The acting user has no open (unconsumed) approval_requests row for
    this invoice -- either they were never one of the invited approvers
    for this decision round, or they already decided and are trying to
    decide again."""


@dataclass(frozen=True, slots=True)
class DecideResult:
    status: Literal["approved", "rejected", "pending"]
    approvals_received: int
    approvals_required: int


def _decimal_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def get_match_view(conn: psycopg.Connection[Any], invoice_id: uuid.UUID) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
        invoice_row = cur.fetchone()
        if invoice_row is None:
            raise InvoiceNotFound(f"invoice {invoice_id} not found")

        cur.execute(
            "SELECT * FROM invoice_lines WHERE invoice_id = %s ORDER BY line_no",
            (invoice_id,),
        )
        invoice_line_rows = cur.fetchall()

        po_row = None
        po_line_rows: list[Any] = []
        received_by_po_line: dict[uuid.UUID, Decimal] = {}
        if invoice_row["po_id"] is not None:
            cur.execute("SELECT * FROM purchase_orders WHERE id = %s", (invoice_row["po_id"],))
            po_row = cur.fetchone()
            cur.execute(
                "SELECT * FROM purchase_order_lines WHERE po_id = %s ORDER BY line_no",
                (invoice_row["po_id"],),
            )
            po_line_rows = cur.fetchall()

            cur.execute(
                "SELECT id FROM goods_receipts WHERE po_id = %s", (invoice_row["po_id"],)
            )
            grn_ids = [row["id"] for row in cur.fetchall()]
            if grn_ids:
                cur.execute(
                    "SELECT po_line_id, qty_received FROM goods_receipt_lines "
                    "WHERE grn_id = ANY(%s)",
                    (grn_ids,),
                )
                for row in cur.fetchall():
                    received_by_po_line[row["po_line_id"]] = received_by_po_line.get(
                        row["po_line_id"], Decimal(0)
                    ) + row["qty_received"]

        cur.execute(
            "SELECT * FROM match_exceptions WHERE invoice_id = %s ORDER BY created_at",
            (invoice_id,),
        )
        exception_rows = cur.fetchall()

        cur.execute(
            "SELECT * FROM match_runs WHERE invoice_id = %s ORDER BY executed_at DESC LIMIT 1",
            (invoice_id,),
        )
        match_run_row = cur.fetchone()

    return {
        "invoice": {
            "id": str(invoice_row["id"]),
            "invoice_number": invoice_row["invoice_number"],
            "total": _decimal_str(invoice_row["total"]),
            "currency": invoice_row["currency"],
            "status": invoice_row["status"],
        },
        "po": (
            None
            if po_row is None
            else {
                "id": str(po_row["id"]),
                "po_number": po_row["po_number"],
                "total": _decimal_str(po_row["total"]),
            }
        ),
        "po_lines": [
            {
                "id": str(row["id"]),
                "line_no": row["line_no"],
                "sku": row["sku"],
                "description": row["description"],
                "qty_ordered": _decimal_str(row["qty_ordered"]),
                "qty_received": _decimal_str(received_by_po_line.get(row["id"], Decimal(0))),
                "unit_price": _decimal_str(row["unit_price"]),
                "line_total": _decimal_str(row["line_total"]),
            }
            for row in po_line_rows
        ],
        "invoice_lines": [
            {
                "id": str(row["id"]),
                "line_no": row["line_no"],
                "description": row["description"],
                "qty": _decimal_str(row["qty"]),
                "unit_price": _decimal_str(row["unit_price"]),
                "line_total": _decimal_str(row["line_total"]),
                "matched_po_line_id": (
                    str(row["matched_po_line_id"]) if row["matched_po_line_id"] else None
                ),
                "match_method": row["match_method"],
                "match_confidence": _decimal_str(row["match_confidence"]),
            }
            for row in invoice_line_rows
        ],
        "exceptions": [
            {
                "id": str(row["id"]),
                "exception_type": row["exception_type"],
                "severity": row["severity"],
                "detail": row["detail"],
                "po_line_id": str(row["po_line_id"]) if row["po_line_id"] else None,
                "invoice_line_id": (
                    str(row["invoice_line_id"]) if row["invoice_line_id"] else None
                ),
                "expected_value": _decimal_str(row["expected_value"]),
                "actual_value": _decimal_str(row["actual_value"]),
                "delta": _decimal_str(row["delta"]),
                "delta_pct": _decimal_str(row["delta_pct"]),
                "status": row["status"],
            }
            for row in exception_rows
        ],
        "match_run": (
            None
            if match_run_row is None
            else {
                "id": str(match_run_row["id"]),
                "result": match_run_row["result"],
                "reason": match_run_row["reason"],
                "policy_version": match_run_row["policy_version"],
                "executed_at": match_run_row["executed_at"].isoformat(),
            }
        ),
    }


def _settle_invoice(
    conn: psycopg.Connection[Any],
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    current_status: InvoiceStatus,
    new_status: InvoiceStatus,
    actor_user_id: uuid.UUID,
    decision: DecisionValue,
    now: datetime,
) -> None:
    """The invoice-level side effects of a *final* decision -- only called
    once the required approver count is actually satisfied (or on a
    reject, which needs just one). Never called for a still-pending
    partial approval."""
    validate_transition(current_status, new_status)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE invoices SET status = %s, updated_at = now() WHERE id = %s",
            (new_status.value, invoice_id),
        )
        exception_status = "resolved" if decision == "approved" else "dismissed"
        cur.execute(
            """
            UPDATE match_exceptions
            SET status = %(status)s, resolved_by = %(actor)s,
                resolved_at = %(now)s, resolution_note = %(note)s
            WHERE invoice_id = %(invoice_id)s AND status = 'open'
            """,
            {
                "status": exception_status,
                "actor": actor_user_id,
                "now": now,
                "note": f"{decision} in-app on the match screen",
                "invoice_id": invoice_id,
            },
        )
    db.insert_audit_log(
        conn,
        tenant_id=tenant_id,
        actor_type="user",
        actor_id=str(actor_user_id),
        action="approval_decided",
        entity_type="invoice",
        entity_id=invoice_id,
        before={"status": current_status.value},
        after={"status": new_status.value, "decision": decision},
    )


def decide_invoice(
    conn: psycopg.Connection[Any],
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    decision: DecisionValue,
    actor_user_id: uuid.UUID,
) -> DecideResult:
    if decision not in ("approved", "rejected"):
        raise ValueError(f"decision must be 'approved' or 'rejected', got {decision!r}")

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT role, email, telegram_chat_id FROM users WHERE id = %s", (actor_user_id,)
        )
        user_row = cur.fetchone()
    if user_row is None or user_row["role"] not in _APPROVER_ROLES:
        raise NotAuthorizedToDecide(
            f"user {actor_user_id} is not authorized to decide on approvals"
        )

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status FROM invoices WHERE id = %s FOR UPDATE", (invoice_id,))
        invoice_row = cur.fetchone()
    if invoice_row is None:
        raise InvoiceNotFound(f"invoice {invoice_id} not found")
    current_status = InvoiceStatus(invoice_row["status"])

    # Lock every approval_requests row for this invoice up front. This is
    # what makes "did exactly one of two required approvers act" and "have
    # all required approvers acted" race-free against a second, concurrent
    # decide() call for the same invoice -- the second call blocks here
    # until the first transaction commits, then sees its committed result.
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM approval_requests WHERE invoice_id = %s FOR UPDATE",
            (invoice_id,),
        )
        rows: list[DictRow] = cur.fetchall()

    recipients = {r for r in (user_row["email"], user_row["telegram_chat_id"]) if r}
    my_row = next(
        (row for row in rows if row["recipient"] in recipients and row["consumed_at"] is None),
        None,
    )
    if my_row is None:
        raise NoPendingApprovalForActor(
            f"user {actor_user_id} has no open approval request for invoice {invoice_id}"
        )

    now = datetime.now(UTC)
    approvals_required = len(rows)

    if decision == "rejected":
        # A single veto settles the invoice -- every other still-open row
        # for it becomes moot (nobody else's decision can change a
        # rejected outcome) and is closed out alongside this one, but
        # left un-attributed (decided_by NULL): nobody actually consumed
        # those tokens themselves.
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE approval_requests
                SET consumed_at = %(now)s, decision = 'rejected',
                    decided_by = %(actor)s, decided_at = %(now)s
                WHERE id = %(id)s
                """,
                {"now": now, "actor": actor_user_id, "id": my_row["id"]},
            )
            other_open_ids = [
                row["id"]
                for row in rows
                if row["id"] != my_row["id"] and row["consumed_at"] is None
            ]
            if other_open_ids:
                cur.execute(
                    """
                    UPDATE approval_requests
                    SET consumed_at = %(now)s, decision = 'rejected'
                    WHERE id = ANY(%(ids)s)
                    """,
                    {"now": now, "ids": other_open_ids},
                )
        _settle_invoice(
            conn, tenant_id, invoice_id, current_status, InvoiceStatus.REJECTED,
            actor_user_id, decision, now,
        )
        return DecideResult(
            status="rejected", approvals_received=0, approvals_required=approvals_required
        )

    # decision == "approved": settle only this caller's own row.
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE approval_requests
            SET consumed_at = %(now)s, decision = 'approved',
                decided_by = %(actor)s, decided_at = %(now)s
            WHERE id = %(id)s
            """,
            {"now": now, "actor": actor_user_id, "id": my_row["id"]},
        )

    approvals_received = 1 + sum(
        1 for row in rows if row["id"] != my_row["id"] and row["decision"] == "approved"
    )

    if approvals_received < approvals_required:
        # Still waiting on at least one more required approver -- the
        # invoice itself, and its exceptions, stay exactly as they are.
        return DecideResult(
            status="pending",
            approvals_received=approvals_received,
            approvals_required=approvals_required,
        )

    _settle_invoice(
        conn, tenant_id, invoice_id, current_status, InvoiceStatus.APPROVED,
        actor_user_id, decision, now,
    )
    return DecideResult(
        status="approved",
        approvals_received=approvals_received,
        approvals_required=approvals_required,
    )
