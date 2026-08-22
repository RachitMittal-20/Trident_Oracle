"""Approval-token issuance and redemption -- the DB-backed half of
core.tokens (packages/core/core/tokens.py), which is pure and cannot do this
itself (CLAUDE.md principle 1: "packages/core performs no I/O... This is the
most important rule in the repo"). core.tokens provides every pure building
block used here: generate/hash/verify/expiry-check. This module is where
those get wired into one real transaction against approval_requests,
invoices, match_exceptions, jobs, and audit_log.

Every function here takes its connection as an argument rather than opening
one itself -- the caller (api/main.py's route handlers, via FastAPI's
Depends) decides which role connects, and for these functions that is
always approval_redeemer (db/migrations/0019_approval_redeemer_role.sql).
Unlike queue_claimer, approval_redeemer is NOT a BYPASSRLS role: it has
IDENTICAL grants to app_role on approval_requests/invoices/
match_exceptions/jobs/audit_log, and is subject to tenant_isolation on all
five exactly as app_role is. The one narrow, additive exception is a second,
permissive SELECT-only policy on approval_requests alone (see the migration)
-- the one thing this whole mechanism structurally cannot do without: find
which tenant a bare, presented token belongs to before app.tenant_id can be
set. See db/README.md's "Security model" section for the full argument.

Both preview_approval_token and redeem_approval_token share the same first
two steps -- find the row tenant-blind, then set_config('app.tenant_id', ...)
on this connection/transaction immediately -- but for two DIFFERENT reasons,
worth keeping distinct rather than treating as one blanket mechanism:

  - redeem_approval_token needs a THIRD step after that: a second, locking
    `SELECT ... FOR UPDATE` by id, now under ordinary tenant_isolation. The
    first read there is deliberately not a locking read: Postgres requires
    a `FOR UPDATE`/`FOR SHARE` SELECT's rows to satisfy both the SELECT
    policy's AND the UPDATE policy's USING clause, and tenant_isolation's
    UPDATE policy on approval_requests cannot pass until app.tenant_id is
    set -- so taking the row lock has to wait for step 2 to happen first.
    The single-use guarantee lives entirely in that second, locking read.
  - preview_approval_token never takes a lock at all -- it's read-only.
    Its two-step split exists for an unrelated reason: it also needs to
    read invoices (for the invoice_number/total/currency a confirmation
    page shows), and invoices has no permissive exception -- only
    approval_requests does. A single query joining the two together would
    apply tenant_isolation's invoices-side USING clause with whatever
    app.tenant_id happens to be set to at that moment, which is nothing,
    since the tenant isn't known until the approval_requests side of the
    join resolves -- that join would return zero rows every time,
    regardless of whether the token is valid. Splitting into "find the
    approval_requests row, then set the tenant, then query invoices
    separately" is the fix for that, not an echo of redeem's locking
    concern.

issue_approval_token needs neither treatment: its caller already knows
tenant_id (it isn't resolving one from a bare token), so it does exactly
one set_config, then one INSERT -- no unlocked/locked split, no multi-table
read to sequence. It's no longer defined in this module at all --
apps/worker/worker/match_handler.py needs the exact same logic (it mints one
token per resolved approver before enqueueing their notify job), so it now
lives in packages/approval_tokens, a shared library both deployables depend
on, the same way both already depend on packages/notifiers. See that
package's docstring for the full reasoning.

Security-critical section -- the raw token must never be logged. Every log
call in this module logs only exception_type names, invoice ids, and
decisions -- never `raw_token`, and never the SQL parameters that carry it.
"""

import hashlib
import html
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import psycopg
import structlog
from core.errors import TokenAlreadyUsed, TokenExpired, TokenNotFound
from core.models import InvoiceStatus
from core.state_machine import validate_transition
from core.tokens import hash_token, is_expired, tokens_match
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

log = structlog.get_logger()

Decision = Literal["approved", "rejected"]

# Shown to every client (Telegram message edit, email confirmation page, the
# POST JSON response) for all three TokenError subtypes alike -- see
# core.errors.TokenError's docstring for why the specific failure reason
# must never be distinguishable from the outside.
GENERIC_TOKEN_FAILURE_MESSAGE = "This approval link is no longer valid."


@dataclass(frozen=True, slots=True)
class ApprovalPreview:
    """What GET /v1/approvals/{token} needs to render a confirmation page --
    read-only, does not consume the token."""

    invoice_id: uuid.UUID
    invoice_number: str | None
    total: Decimal | None
    currency: str


@dataclass(frozen=True, slots=True)
class RedeemedApproval:
    """The result of a successful redemption."""

    invoice_id: uuid.UUID
    exception_id: uuid.UUID | None
    decision: Decision
    decided_by: uuid.UUID | None
    decided_at: datetime


def preview_approval_token(conn: psycopg.Connection, raw_token: str) -> ApprovalPreview:
    """Read-only lookup for GET /v1/approvals/{token} -- does not lock or
    consume the token. Raises TokenNotFound/TokenExpired/TokenAlreadyUsed,
    same as redeem_approval_token; the caller must map all three to
    GENERIC_TOKEN_FAILURE_MESSAGE.
    """
    computed_hash = hash_token(raw_token)
    with conn.cursor(row_factory=dict_row) as cur:
        # Step 1: tenant-blind, via approval_redeemer_token_lookup's
        # permissive SELECT-only policy. Plain SELECT -- no join against
        # invoices yet, since invoices has no such exception and is still
        # fully tenant_isolation-scoped; app.tenant_id isn't set yet.
        cur.execute(
            """
            SELECT token_hash, expires_at, consumed_at, tenant_id, invoice_id
            FROM approval_requests
            WHERE token_hash = %s
            """,
            (computed_hash,),
        )
        row = cur.fetchone()

        if row is None or not tokens_match(raw_token, row["token_hash"]):
            conn.rollback()
            raise TokenNotFound("approval token not found")
        if row["consumed_at"] is not None:
            conn.rollback()
            raise TokenAlreadyUsed("approval token already used")
        if is_expired(row["expires_at"], now=datetime.now(UTC)):
            conn.rollback()
            raise TokenExpired("approval token expired")

        # Step 2: now that the tenant is known, every remaining statement
        # in this transaction runs under ordinary tenant_isolation.
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(row["tenant_id"]),))
        cur.execute(
            "SELECT invoice_number, total, currency FROM invoices WHERE id = %s",
            (row["invoice_id"],),
        )
        invoice_row = cur.fetchone()

    if invoice_row is None:
        # Real token, but its invoice is gone -- not a distinction a client
        # should be able to make from a plain "not found" either.
        conn.rollback()
        raise TokenNotFound("approval token not found")

    conn.commit()
    return ApprovalPreview(
        invoice_id=row["invoice_id"],
        invoice_number=invoice_row["invoice_number"],
        total=invoice_row["total"],
        currency=invoice_row["currency"],
    )


def redeem_approval_token(
    conn: psycopg.Connection,
    raw_token: str,
    decision: Decision,
    actor: uuid.UUID | None,
) -> RedeemedApproval:
    """Atomically redeems `raw_token` for `decision`.

    Single-use is enforced by the database, not by this function's control
    flow: `SELECT ... FOR UPDATE` takes a row lock on the matched
    approval_requests row for the rest of this transaction. A second,
    concurrent call for the SAME token blocks on that lock until the first
    transaction commits or rolls back, then re-reads the row and correctly
    observes consumed_at already set -- there is no window in which two
    concurrent redemptions can both see an unconsumed token, no matter how
    they're interleaved by the scheduler.

    Raises TokenNotFound, TokenExpired, or TokenAlreadyUsed on failure --
    all three are TokenError, and every caller of this function must map
    all three to the SAME generic client-facing message
    (GENERIC_TOKEN_FAILURE_MESSAGE) -- never let a client distinguish which
    one occurred.
    """
    if decision not in ("approved", "rejected"):
        raise ValueError(f"decision must be 'approved' or 'rejected', got {decision!r}")

    computed_hash = hash_token(raw_token)
    now = datetime.now(UTC)

    with conn.cursor(row_factory=dict_row) as cur:
        # Step 1: tenant-blind, via approval_redeemer_token_lookup's
        # permissive SELECT-only policy. Not a locking read -- see this
        # module's docstring for why FOR UPDATE can't be used yet here.
        cur.execute(
            "SELECT id, token_hash, tenant_id FROM approval_requests WHERE token_hash = %s",
            (computed_hash,),
        )
        lookup_row = cur.fetchone()

        if lookup_row is None or not tokens_match(raw_token, lookup_row["token_hash"]):
            conn.rollback()
            log.info("approval_redeem_failed", reason="TokenNotFound")
            raise TokenNotFound("approval token not found")

        # Step 2: tenant now known -- every remaining statement in this
        # transaction runs under ordinary tenant_isolation, identical to
        # app_role.
        cur.execute(
            "SELECT set_config('app.tenant_id', %s, false)", (str(lookup_row["tenant_id"]),)
        )

        # Step 3: the actual locking read. This is what serializes
        # concurrent redemptions of the same token -- a second, concurrent
        # call blocks here until the first transaction commits or rolls
        # back, then re-reads the row's latest committed version (still
        # matched by id, still passing tenant_isolation since app.tenant_id
        # was just set to this exact row's own tenant) and correctly
        # observes consumed_at already set.
        cur.execute(
            "SELECT * FROM approval_requests WHERE id = %s FOR UPDATE", (lookup_row["id"],)
        )
        row = cur.fetchone()

        if row is None or not tokens_match(raw_token, row["token_hash"]):
            # Unreachable in practice -- tenant_isolation now matches by
            # construction -- but never trust that blindly.
            conn.rollback()
            log.info("approval_redeem_failed", reason="TokenNotFound")
            raise TokenNotFound("approval token not found")
        if row["consumed_at"] is not None:
            conn.rollback()
            log.info(
                "approval_redeem_failed",
                reason="TokenAlreadyUsed",
                invoice_id=str(row["invoice_id"]),
            )
            raise TokenAlreadyUsed("approval token already used")
        if is_expired(row["expires_at"], now=now):
            conn.rollback()
            log.info(
                "approval_redeem_failed", reason="TokenExpired", invoice_id=str(row["invoice_id"])
            )
            raise TokenExpired("approval token expired")

        cur.execute(
            """
            UPDATE approval_requests
            SET consumed_at = %(now)s, decision = %(decision)s,
                decided_by = %(actor)s, decided_at = %(now)s
            WHERE id = %(id)s
            """,
            {"now": now, "decision": decision, "actor": actor, "id": row["id"]},
        )

        cur.execute("SELECT status FROM invoices WHERE id = %s FOR UPDATE", (row["invoice_id"],))
        invoice_row = cur.fetchone()
        if invoice_row is None:
            # The token is real but its invoice is gone -- outside this
            # module's control, and not a distinction a client should be
            # able to make from a plain "not found" either.
            conn.rollback()
            log.warning("approval_redeem_invoice_missing", invoice_id=str(row["invoice_id"]))
            raise TokenNotFound("approval token not found")

        current_status = InvoiceStatus(invoice_row["status"])
        new_status = InvoiceStatus.APPROVED if decision == "approved" else InvoiceStatus.REJECTED
        validate_transition(current_status, new_status)

        cur.execute(
            "UPDATE invoices SET status = %s, updated_at = now() WHERE id = %s",
            (new_status.value, row["invoice_id"]),
        )

        exception_status = "resolved" if decision == "approved" else "dismissed"
        resolution_note = f"{decision} via approval token redemption"
        if row["exception_id"] is not None:
            cur.execute(
                """
                UPDATE match_exceptions
                SET status = %(status)s, resolved_by = %(actor)s,
                    resolved_at = %(now)s, resolution_note = %(note)s
                WHERE id = %(exception_id)s AND status = 'open'
                """,
                {
                    "status": exception_status,
                    "actor": actor,
                    "now": now,
                    "note": resolution_note,
                    "exception_id": row["exception_id"],
                },
            )
        else:
            cur.execute(
                """
                UPDATE match_exceptions
                SET status = %(status)s, resolved_by = %(actor)s,
                    resolved_at = %(now)s, resolution_note = %(note)s
                WHERE invoice_id = %(invoice_id)s AND status = 'open'
                """,
                {
                    "status": exception_status,
                    "actor": actor,
                    "now": now,
                    "note": resolution_note,
                    "invoice_id": row["invoice_id"],
                },
            )

        cur.execute(
            """
            INSERT INTO audit_log
                (tenant_id, actor_type, actor_id, action, entity_type, entity_id, before, after)
            VALUES (%(tenant_id)s, 'user', %(actor_id)s, 'approval_decided', 'invoice',
                    %(invoice_id)s, %(before)s, %(after)s)
            """,
            {
                "tenant_id": row["tenant_id"],
                "actor_id": str(actor) if actor is not None else None,
                "invoice_id": row["invoice_id"],
                "before": Jsonb({"status": current_status.value}),
                "after": Jsonb({"status": new_status.value, "decision": decision}),
            },
        )

        if decision == "approved":
            idempotency_key = hashlib.sha256(
                f"{row['tenant_id']}:{row['invoice_id']}:post".encode()
            ).hexdigest()
            cur.execute(
                """
                INSERT INTO jobs (tenant_id, job_type, payload, idempotency_key)
                VALUES (%(tenant_id)s, 'post', %(payload)s, %(idempotency_key)s)
                ON CONFLICT (idempotency_key) DO UPDATE SET idempotency_key = jobs.idempotency_key
                """,
                {
                    "tenant_id": row["tenant_id"],
                    "payload": Jsonb({"invoice_id": str(row["invoice_id"])}),
                    "idempotency_key": idempotency_key,
                },
            )

    conn.commit()
    log.info(
        "approval_redeemed",
        invoice_id=str(row["invoice_id"]),
        decision=decision,
        new_status=new_status.value,
    )
    return RedeemedApproval(
        invoice_id=row["invoice_id"],
        exception_id=row["exception_id"],
        decision=decision,
        decided_by=actor,
        decided_at=now,
    )


# --- Minimal HTML rendering for the email-flow confirmation page -----------

_PAGE_STYLE = (
    "body{background:#08090B;color:#E6EDF3;font-family:sans-serif;"
    "padding:40px;margin:0;}"
    ".card{max-width:480px;margin:40px auto;background:#101215;"
    "border:1px solid #21262D;border-radius:8px;padding:24px;}"
    "button{font-size:16px;padding:12px 24px;border-radius:6px;border:none;"
    "font-weight:600;cursor:pointer;margin-right:12px;color:#fff;}"
    ".approve{background:#2EA88A;}"
    ".reject{background:#E5534B;}"
)


def render_approval_page(preview: ApprovalPreview) -> str:
    invoice_label = html.escape(preview.invoice_number or str(preview.invoice_id))
    total_display = (
        html.escape(f"{preview.currency} {preview.total}")
        if preview.total is not None
        else "amount unknown"
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Invoice Approval</title>
<style>{_PAGE_STYLE}</style></head>
<body><div class="card">
<h2>Invoice {invoice_label}</h2>
<p>Total: {total_display}</p>
<form method="post">
<button class="approve" name="decision" value="approved" type="submit">Approve</button>
<button class="reject" name="decision" value="rejected" type="submit">Reject</button>
</form>
</div></body></html>"""


def render_failure_page() -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Invoice Approval</title>
<style>{_PAGE_STYLE}</style></head>
<body><div class="card"><p>{html.escape(GENERIC_TOKEN_FAILURE_MESSAGE)}</p></div></body></html>"""


def render_confirmation_page(decision: Decision) -> str:
    verb = "approved" if decision == "approved" else "rejected"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Invoice Approval</title>
<style>{_PAGE_STYLE}</style></head>
<body><div class="card"><p>Thanks -- this invoice has been {verb}.</p></div></body></html>"""
