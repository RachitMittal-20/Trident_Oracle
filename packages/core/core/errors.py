"""Exception hierarchy for Trident Oracle.

Every error raised anywhere in this codebase is one of these -- never a bare
Exception, never a raw builtin like ValueError, per CLAUDE.md.
"""


class TridentOracleError(Exception):
    """Root of the application exception hierarchy."""


class InvalidStateTransition(TridentOracleError):
    """A status transition is not legal per the invoice state machine."""


class ExtractionError(TridentOracleError):
    """A vision extractor (Gemini, Tesseract) failed to produce a usable result."""


class MatchingError(TridentOracleError):
    """The three-way matching engine could not complete a run."""


class PolicyViolation(TridentOracleError):
    """An operation would violate an active tolerance_policy."""


class TokenError(TridentOracleError):
    """An approval token is invalid, expired, or already consumed.

    Three distinct subtypes exist below so server-side code (logs, metrics)
    can tell exactly what went wrong -- but the API surface that redeems a
    token must map all three to one identical generic client-facing
    message. Which of these three fired is never something a client should
    be able to distinguish: telling an attacker "expired" vs. "already
    used" vs. "not found" narrows down what they might try next for very
    little legitimate benefit to a real user, who doesn't need the
    distinction either -- "this link no longer works" is all they can act
    on regardless.
    """


class TokenNotFound(TokenError):
    """No approval_requests row's token_hash matches the presented token."""


class TokenExpired(TokenError):
    """The token was found but its expires_at has already passed."""


class TokenAlreadyUsed(TokenError):
    """The token was found but consumed_at is already set -- a replay of a
    single-use token."""


class StorageError(TridentOracleError):
    """A file storage backend (Supabase Storage) failed to upload, download,
    or sign a URL for an object."""


class NotificationError(TridentOracleError):
    """A notification backend (Telegram, email, WhatsApp) failed to send a
    message."""
