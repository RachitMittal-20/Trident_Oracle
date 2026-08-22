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
    """An approval token is invalid, expired, or already consumed."""


class StorageError(TridentOracleError):
    """A file storage backend (Supabase Storage) failed to upload, download,
    or sign a URL for an object."""


class NotificationError(TridentOracleError):
    """A notification backend (Telegram, email, WhatsApp) failed to send a
    message."""
