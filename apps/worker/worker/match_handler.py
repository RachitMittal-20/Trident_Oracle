"""The 'match' job handler: loads an invoice's full matching context (its
lines, linked PO/GRN, active tolerance policy, and the recent-invoice
candidate set for duplicate detection), runs the three-way match
(core.matching.three_way), persists the match_runs/match_exceptions rows,
runs the approval decision (core.decision), transitions the invoice per the
outcome, and enqueues a 'notify' job when a human approver is needed.

Every invoice status change goes through core.state_machine.validate_transition
and writes to audit_log -- CLAUDE.md principles 4 and 5, no exceptions. This
handler makes either one or two such transitions per run:

    MATCHING -> NEEDS_VERIFICATION                                  (confidence too low)
    MATCHING -> MATCHED_CLEAN      -> AUTO_POSTED                    (clean, small enough)
    MATCHING -> MATCHED_CLEAN      -> PENDING_APPROVAL                (clean, but large)
    MATCHING -> EXCEPTIONS_RAISED  -> PENDING_APPROVAL                (warn/block findings)

A 'notify' job is enqueued only for the PENDING_APPROVAL outcome -- that is
the one outcome that actually needs a human approver to act through a
Telegram/email link. NEEDS_VERIFICATION routes to the in-app verification
screen instead, which is polled/opened directly, not paged.

Every DB row this handler reads has tenant_id denormalized onto it directly
(db/migrations/0011_rls_child_tables.sql) -- purchase_order_lines,
goods_receipt_lines, invoice_lines, match_runs, and match_exceptions all
carry their own tenant_id column for RLS, so none of the _row_to_* helpers
below need to borrow tenant_id from a parent row.
"""

import hashlib
import uuid
from datetime import date
from typing import Any

import psycopg
import structlog
from core.decision import Decision, decide
from core.errors import MatchingError, PolicyViolation
from core.matching.duplicates import InvoiceSummary
from core.matching.three_way import MatchFinding, ThreeWayMatchResult, run_three_way_match
from core.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    JobType,
    MatchMethod,
    PurchaseOrder,
    PurchaseOrderLine,
    Vendor,
)
from core.policy import load_tolerance_policy
from core.queue.models import Job
from core.state_machine import validate_transition
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb

from worker.db import JobQueue

log = structlog.get_logger()


def _transition(
    conn: psycopg.Connection[Any],
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID,
    from_status: InvoiceStatus,
    to_status: InvoiceStatus,
    extra_audit: dict[str, Any] | None = None,
) -> None:
    validate_transition(from_status, to_status)
    after: dict[str, Any] = {"status": to_status.value}
    if extra_audit:
        after.update(extra_audit)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE invoices SET status = %s, updated_at = now() WHERE id = %s",
            (to_status.value, invoice_id),
        )
        cur.execute(
            """
            INSERT INTO audit_log
                (tenant_id, actor_type, action, entity_type, entity_id, before, after)
            VALUES (%s, 'system', 'status_transition', 'invoice', %s, %s, %s)
            """,
            (
                tenant_id,
                invoice_id,
                Jsonb({"status": from_status.value}),
                Jsonb(after),
            ),
        )
    conn.commit()


# --- Row -> domain object -----------------------------------------------------


def _row_to_invoice(row: DictRow) -> Invoice:
    return Invoice(
        id=row["id"],
        tenant_id=row["tenant_id"],
        currency=row["currency"],
        source_channel=row["source_channel"],
        source_file_path=row["source_file_path"],
        content_hash=row["content_hash"],
        status=InvoiceStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        invoice_number=row["invoice_number"],
        invoice_date=row["invoice_date"],
        subtotal=row["subtotal"],
        tax=row["tax"],
        total=row["total"],
        vendor_id=row["vendor_id"],
        po_id=row["po_id"],
        due_date=row["due_date"],
        extraction_backend=row["extraction_backend"],
        overall_confidence=row["overall_confidence"],
    )


def _row_to_invoice_line(row: DictRow) -> InvoiceLine:
    return InvoiceLine(
        id=row["id"],
        tenant_id=row["tenant_id"],
        invoice_id=row["invoice_id"],
        line_no=row["line_no"],
        description=row["description"],
        qty=row["qty"],
        unit_price=row["unit_price"],
        line_total=row["line_total"],
        created_at=row["created_at"],
        normalized_description=row["normalized_description"],
        matched_po_line_id=row["matched_po_line_id"],
        match_method=MatchMethod(row["match_method"]) if row["match_method"] else None,
    )


def _row_to_vendor(row: DictRow) -> Vendor:
    return Vendor(
        id=row["id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        normalized_name=row["normalized_name"],
        created_at=row["created_at"],
        tax_id=row["tax_id"],
        email=row["email"],
    )


def _row_to_po(row: DictRow) -> PurchaseOrder:
    return PurchaseOrder(
        id=row["id"],
        tenant_id=row["tenant_id"],
        vendor_id=row["vendor_id"],
        po_number=row["po_number"],
        issued_at=row["issued_at"],
        currency=row["currency"],
        subtotal=row["subtotal"],
        tax=row["tax"],
        total=row["total"],
        status=row["status"],
        created_at=row["created_at"],
    )


def _row_to_po_line(row: DictRow) -> PurchaseOrderLine:
    return PurchaseOrderLine(
        id=row["id"],
        tenant_id=row["tenant_id"],
        po_id=row["po_id"],
        line_no=row["line_no"],
        description=row["description"],
        normalized_description=row["normalized_description"],
        qty_ordered=row["qty_ordered"],
        unit_price=row["unit_price"],
        tax_rate=row["tax_rate"],
        line_total=row["line_total"],
        created_at=row["created_at"],
        sku=row["sku"],
    )


def _row_to_grn(row: DictRow) -> GoodsReceipt:
    return GoodsReceipt(
        id=row["id"],
        tenant_id=row["tenant_id"],
        po_id=row["po_id"],
        grn_number=row["grn_number"],
        received_at=row["received_at"],
        received_by=row["received_by"],
        created_at=row["created_at"],
    )


def _row_to_grn_line(row: DictRow) -> GoodsReceiptLine:
    return GoodsReceiptLine(
        id=row["id"],
        tenant_id=row["tenant_id"],
        grn_id=row["grn_id"],
        po_line_id=row["po_line_id"],
        qty_received=row["qty_received"],
        condition=row["condition"],
        created_at=row["created_at"],
        notes=row["notes"],
    )


# --- Persistence ---------------------------------------------------------


def _persist_match_run(
    conn: psycopg.Connection[Any],
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    policy_version: int,
    match_result: ThreeWayMatchResult,
    duration_ms: int,
) -> uuid.UUID:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO match_runs (tenant_id, invoice_id, policy_version, result, duration_ms)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (tenant_id, invoice_id, policy_version, match_result.result, duration_ms),
        )
        row = cur.fetchone()
    conn.commit()
    if row is None:
        raise RuntimeError("match_runs INSERT ... RETURNING produced no row")
    return uuid.UUID(str(row["id"]))


def _persist_match_exceptions(
    conn: psycopg.Connection[Any],
    tenant_id: uuid.UUID,
    match_run_id: uuid.UUID,
    invoice_id: uuid.UUID,
    findings: tuple[MatchFinding, ...],
) -> None:
    with conn.cursor() as cur:
        for finding in findings:
            cur.execute(
                """
                INSERT INTO match_exceptions
                    (tenant_id, match_run_id, invoice_id, exception_type, severity, po_line_id,
                     invoice_line_id, expected_value, actual_value, delta, delta_pct, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    match_run_id,
                    invoice_id,
                    finding.exception_type.value,
                    finding.severity.value,
                    finding.po_line_id,
                    finding.invoice_line_id,
                    finding.expected_value,
                    finding.actual_value,
                    finding.delta,
                    finding.delta_pct,
                    finding.detail,
                ),
            )
    conn.commit()


def _apply_decision(
    conn: psycopg.Connection[Any],
    queue: JobQueue,
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID,
    decision: Decision,
    match_result: ThreeWayMatchResult,
) -> None:
    if decision.outcome == "NEEDS_VERIFICATION":
        _transition(
            conn,
            invoice_id,
            tenant_id,
            InvoiceStatus.MATCHING,
            InvoiceStatus.NEEDS_VERIFICATION,
            extra_audit={"reason": decision.reason},
        )
        return

    # AUTO_POST and PENDING_APPROVAL both pass through an intermediate
    # status first -- MATCHED_CLEAN for a clean match, EXCEPTIONS_RAISED
    # otherwise -- because that's what core.state_machine's allowed
    # transitions require (PENDING_APPROVAL and AUTO_POSTED are not
    # reachable directly from MATCHING).
    intermediate = (
        InvoiceStatus.MATCHED_CLEAN
        if match_result.result == "clean"
        else InvoiceStatus.EXCEPTIONS_RAISED
    )
    _transition(conn, invoice_id, tenant_id, InvoiceStatus.MATCHING, intermediate)

    if decision.outcome == "AUTO_POST":
        _transition(
            conn,
            invoice_id,
            tenant_id,
            intermediate,
            InvoiceStatus.AUTO_POSTED,
            extra_audit={"reason": decision.reason},
        )
        return

    _transition(
        conn,
        invoice_id,
        tenant_id,
        intermediate,
        InvoiceStatus.PENDING_APPROVAL,
        extra_audit={"reason": decision.reason, "required_approvers": decision.required_approvers},
    )
    notify_idempotency_key = hashlib.sha256(
        f"{tenant_id}:{invoice_id}:notify:pending_approval".encode()
    ).hexdigest()
    queue.enqueue(
        JobType.NOTIFY,
        {
            "invoice_id": str(invoice_id),
            "reason": decision.reason,
            "required_approvers": decision.required_approvers,
        },
        tenant_id,
        notify_idempotency_key,
    )


# --- The handler ---------------------------------------------------------


def handle_match(conn: psycopg.Connection[Any], queue: JobQueue, job: Job) -> None:
    invoice_id = uuid.UUID(job.payload["invoice_id"])
    tenant_id = job.tenant_id

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
        invoice_row = cur.fetchone()
    if invoice_row is None:
        raise MatchingError(f"invoice {invoice_id} not found")
    if invoice_row["vendor_id"] is None:
        raise MatchingError(f"invoice {invoice_id} has no vendor_id; cannot run three-way match")

    current_status = InvoiceStatus(invoice_row["status"])
    _transition(conn, invoice_id, tenant_id, current_status, InvoiceStatus.MATCHING)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM vendors WHERE id = %s", (invoice_row["vendor_id"],))
        vendor_row = cur.fetchone()
    if vendor_row is None:
        raise MatchingError(f"vendor {invoice_row['vendor_id']} not found")

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM invoice_lines WHERE invoice_id = %s ORDER BY line_no", (invoice_id,)
        )
        invoice_line_rows = cur.fetchall()

        po_row: DictRow | None = None
        po_line_rows: list[DictRow] = []
        if invoice_row["po_id"] is not None:
            cur.execute("SELECT * FROM purchase_orders WHERE id = %s", (invoice_row["po_id"],))
            po_row = cur.fetchone()
            cur.execute(
                "SELECT * FROM purchase_order_lines WHERE po_id = %s ORDER BY line_no",
                (invoice_row["po_id"],),
            )
            po_line_rows = cur.fetchall()

        grn_row: DictRow | None = None
        grn_line_rows: list[DictRow] = []
        if po_row is not None:
            cur.execute(
                "SELECT * FROM goods_receipts WHERE po_id = %s ORDER BY received_at",
                (po_row["id"],),
            )
            grn_rows = cur.fetchall()
            if grn_rows:
                grn_row = grn_rows[0]
                grn_ids = [row["id"] for row in grn_rows]
                cur.execute(
                    "SELECT * FROM goods_receipt_lines WHERE grn_id = ANY(%s)", (grn_ids,)
                )
                grn_line_rows = cur.fetchall()

        cur.execute(
            """
            SELECT * FROM tolerance_policies
            WHERE tenant_id = %s AND is_active = true
            ORDER BY version DESC LIMIT 1
            """,
            (tenant_id,),
        )
        policy_row = cur.fetchone()
        if policy_row is None:
            raise PolicyViolation(f"no active tolerance policy for tenant {tenant_id}")

        cur.execute(
            """
            SELECT id, invoice_number, invoice_date, total, content_hash
            FROM invoices
            WHERE tenant_id = %s AND vendor_id = %s AND id != %s
            """,
            (tenant_id, vendor_row["id"], invoice_id),
        )
        prior_invoice_rows = cur.fetchall()

        prior_lines_by_invoice: dict[uuid.UUID, list[str]] = {}
        prior_ids = [row["id"] for row in prior_invoice_rows]
        if prior_ids:
            cur.execute(
                "SELECT invoice_id, description FROM invoice_lines WHERE invoice_id = ANY(%s)",
                (prior_ids,),
            )
            for line_row in cur.fetchall():
                prior_lines_by_invoice.setdefault(line_row["invoice_id"], []).append(
                    line_row["description"]
                )

    policy = load_tolerance_policy(
        id=policy_row["id"],
        tenant_id=policy_row["tenant_id"],
        name=policy_row["name"],
        is_active=policy_row["is_active"],
        version=policy_row["version"],
        rules=policy_row["rules"],
        created_at=policy_row["created_at"],
    )

    recent_invoices = tuple(
        InvoiceSummary(
            id=row["id"],
            vendor_id=vendor_row["id"],
            vendor_name=vendor_row["name"],
            content_hash=row["content_hash"],
            invoice_number=row["invoice_number"],
            invoice_date=row["invoice_date"],
            total=row["total"],
            line_descriptions=tuple(prior_lines_by_invoice.get(row["id"], ())),
        )
        for row in prior_invoice_rows
    )

    invoice = _row_to_invoice(invoice_row)
    match_result = run_three_way_match(
        invoice=invoice,
        invoice_lines=[_row_to_invoice_line(r) for r in invoice_line_rows],
        vendor=_row_to_vendor(vendor_row),
        po=_row_to_po(po_row) if po_row is not None else None,
        po_lines=[_row_to_po_line(r) for r in po_line_rows],
        grn=_row_to_grn(grn_row) if grn_row is not None else None,
        grn_lines=[_row_to_grn_line(r) for r in grn_line_rows],
        policy=policy,
        recent_invoices=recent_invoices,
        today=date.today(),
    )

    duration_ms = int(sum(match_result.stage_timings_ms.values()))
    match_run_id = _persist_match_run(
        conn, tenant_id, invoice_id, policy.version, match_result, duration_ms
    )
    _persist_match_exceptions(conn, tenant_id, match_run_id, invoice_id, match_result.findings)

    decision = decide(match_result, invoice.overall_confidence, invoice, policy)
    _apply_decision(conn, queue, invoice_id, tenant_id, decision, match_result)

    log.info(
        "match_completed",
        invoice_id=str(invoice_id),
        match_run_id=str(match_run_id),
        result=match_result.result,
        outcome=decision.outcome,
        exception_count=len(match_result.findings),
    )
