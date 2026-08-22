"""Notification-specific exceptions. Both are NotificationError (packages/core),
so callers that only care about "send failed" can catch NotificationError;
callers that want to distinguish transient failures -- worth retrying --
catch RetryableNotificationError specifically.
"""

from core.errors import NotificationError


class RetryableNotificationError(NotificationError):
    """Sending failed for a transient reason (rate limit, connection drop,
    5xx) -- safe to retry. `retry_after` is the number of seconds the
    backend itself asked the caller to wait before retrying, when it said
    so explicitly (e.g. Telegram's 429 `retry_after`); None when no such
    hint was given.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
