"""Approval-token cryptography -- pure, no I/O.

CLAUDE.md principle 1 ("packages/core performs no I/O... This is the most
important rule in the repo") means the actual DB-backed
issue_approval_token/redeem_approval_token functions -- which read and write
approval_requests, invoices, match_exceptions, jobs, and audit_log inside one
transaction -- do not live here, the same way JobQueue (apps/worker/worker/db.py)
doesn't live beside core.queue.models/core.queue.backoff even though both are
"the queue." This module is every pure building block that DB-backed code
needs: random token generation, hashing, constant-time verification, and
expiry checking. See apps/api/api/approvals.py for the actual issue/preview/
redeem implementations, and db/migrations/0019_approval_redeemer_role.sql for
why they run under a dedicated, narrowly-scoped role rather than the
ordinary RLS-bound app_role connection.

Security notes -- read before touching anything in this file:

  - The raw token is 32 bytes from secrets.token_bytes -- a CSPRNG, never
    random.random() or anything seeded/predictable -- base64url-encoded
    without padding ('=' has no defined meaning in a URL path segment or a
    Telegram callback_data value, so it's stripped rather than escaped).

  - Only the SHA-256 hash of the raw token is ever meant to be persisted;
    this module never persists anything itself, but IssuedApprovalToken's
    __repr__ is overridden so that logging, printing, or an uncaught
    exception's traceback involving this object can never accidentally
    print the raw token -- the one value in this entire feature that must
    never appear in a log line, per CLAUDE.md's instructions for this
    module.

  - tokens_match() compares a presented token's hash against a stored hash
    with hmac.compare_digest, not `==`. A plain `==` on two strings
    short-circuits at the first differing character, which is a real (if
    narrow) timing side-channel for a secret-dependent comparison.
    Comparing hashes rather than raw secrets already reduces the practical
    exploitability of that a great deal, but there's no reason to rely on
    that instead of just using the constant-time primitive built for
    exactly this.
"""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

TOKEN_BYTES: Final[int] = 32


def generate_raw_token() -> str:
    """32 cryptographically random bytes, base64url-encoded, no padding."""
    return base64.urlsafe_b64encode(secrets.token_bytes(TOKEN_BYTES)).rstrip(b"=").decode("ascii")


def hash_token(raw_token: str) -> str:
    """SHA-256 hex digest of a raw token -- the only form ever persisted."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def tokens_match(raw_token: str, stored_hash: str) -> bool:
    """Constant-time check that `raw_token` hashes to `stored_hash`."""
    return hmac.compare_digest(hash_token(raw_token), stored_hash)


def is_expired(expires_at: datetime, *, now: datetime) -> bool:
    return now >= expires_at


@dataclass(frozen=True, slots=True, repr=False)
class IssuedApprovalToken:
    """The one-time result of minting a token: the raw value (returned to
    the caller exactly once -- it is not reconstructable from token_hash,
    since SHA-256 is one-way) and what actually gets persisted for it
    (token_hash, expires_at). __repr__ is overridden so the raw token can
    never leak through a log line, a debugger's default object display, or
    an uncaught exception's traceback -- see this module's docstring.
    """

    raw_token: str
    token_hash: str
    expires_at: datetime

    def __repr__(self) -> str:
        return (
            f"IssuedApprovalToken(raw_token='<redacted>', "
            f"token_hash={self.token_hash!r}, expires_at={self.expires_at!r})"
        )


def mint_approval_token(ttl: timedelta, *, now: datetime) -> IssuedApprovalToken:
    """Generates a new raw token and computes what should be persisted for
    it. `now` is a required argument, not `datetime.now()` read internally
    -- same reasoning as core.matching.three_way's `today` parameter: a
    pure function's output must be reproducible from its inputs alone, and
    tests must never depend on the real wall clock.
    """
    raw_token = generate_raw_token()
    return IssuedApprovalToken(
        raw_token=raw_token,
        token_hash=hash_token(raw_token),
        expires_at=now + ttl,
    )
