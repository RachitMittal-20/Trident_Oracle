"""issue_approval_token -- the DB-backed half of core.tokens
(packages/core/core/tokens.py), which is pure and cannot do this itself
(CLAUDE.md principle 1: "packages/core performs no I/O... This is the most
important rule in the repo"). core.tokens provides every pure building block
used here: generate/hash/expiry. This module wires that into one INSERT
against approval_requests.

Shared between apps/api (apps/api/api/approvals.py, which owns
preview_approval_token/redeem_approval_token -- only the API's HTTP
endpoints ever redeem or preview a token) and apps/worker
(worker/match_handler.py, which mints one token per resolved approver
before enqueueing their notify job). Both deployables depend on this
package the same way they already both depend on packages/notifiers -- a
real, I/O-performing shared library, not a pure one -- rather than one
importing from the other.

Takes its connection as an argument rather than opening one itself -- the
caller decides which role connects. tenant_id is already known by the time
either caller reaches this function (the API resolves it from request
context before issuing; the worker's handler connection already has
app.tenant_id set for the claimed job), so there is no chicken-and-egg
problem here the way there is for redemption (see apps/api/api/approvals.py's
module docstring for that one).

Security-critical: the raw token must never be logged. The one log call
here logs only invoice/exception ids, channel, and expiry -- never
`raw_token`.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import psycopg
import structlog
from core.tokens import mint_approval_token

log = structlog.get_logger()

ApprovalChannel = Literal["telegram", "email", "whatsapp"]


def issue_approval_token(
    conn: psycopg.Connection,
    *,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    exception_id: uuid.UUID | None,
    recipient: str,
    channel: ApprovalChannel,
    ttl: timedelta,
) -> str:
    """Mints a new approval token, persists ONLY its hash, and returns the
    raw token exactly once. The caller -- the 'notify' job handler -- is
    responsible for putting it straight into the outbound message (a
    Telegram callback_data value or an /approve/{token} email link) and for
    never logging it; this function doesn't log it either.
    """
    issued = mint_approval_token(ttl, now=datetime.now(UTC))
    with conn.cursor() as cur:
        # tenant_isolation's WITH CHECK (approval_redeemer is not a
        # BYPASSRLS role -- db/migrations/0019) requires this before the
        # INSERT below, same as any app_role write.
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant_id),))
        cur.execute(
            """
            INSERT INTO approval_requests
                (tenant_id, invoice_id, exception_id, token_hash, channel, recipient, expires_at)
            VALUES (%(tenant_id)s, %(invoice_id)s, %(exception_id)s, %(token_hash)s,
                    %(channel)s, %(recipient)s, %(expires_at)s)
            """,
            {
                "tenant_id": tenant_id,
                "invoice_id": invoice_id,
                "exception_id": exception_id,
                "token_hash": issued.token_hash,
                "channel": channel,
                "recipient": recipient,
                "expires_at": issued.expires_at,
            },
        )
    conn.commit()
    log.info(
        "approval_token_issued",
        invoice_id=str(invoice_id),
        exception_id=str(exception_id) if exception_id else None,
        channel=channel,
        expires_at=issued.expires_at.isoformat(),
    )
    return issued.raw_token
