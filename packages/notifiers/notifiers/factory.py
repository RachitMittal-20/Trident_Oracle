"""Notifier selection and channel fallback.

get_notifier() picks a single named backend. FallbackNotifier wraps an
ordered chain of backends so a transient failure on one (Telegram rate
limited, SMTP connection dropped) degrades to the next channel in the chain
instead of failing the whole notify job -- CLAUDE.md principle 7: fail loudly
in development, degrade gracefully in production. Same shape as
extractors/factory.py's FallbackExtractor, generalized from "one fallback" to
"an ordered chain" since CLAUDE.md's stack table lists three channels with an
explicit primary/secondary/stubbed order (Telegram primary, SMTP secondary,
WhatsApp stubbed) rather than just one pair.

whatsapp is registered in _BACKENDS (get_notifier("whatsapp") constructs a
real WhatsAppNotifier) but is never in DEFAULT_CHAIN -- constructing it
always succeeds, only calling .send() on it raises NotImplementedError, and
NotImplementedError is not a RetryableNotificationError, so a fallback chain
that reached it would stop there instead of moving on. Wiring it into the
default chain today would silently break every fallback path the moment
Telegram and email were both down; it stays reachable by explicit name only,
until real credentials exist.
"""

import os
from collections.abc import Sequence

import structlog
from core.errors import NotificationError

from notifiers.base import DeliveryResult, NotificationMessage, Notifier
from notifiers.email import EmailNotifier
from notifiers.errors import RetryableNotificationError
from notifiers.mock import MockNotifier
from notifiers.telegram import TelegramNotifier
from notifiers.whatsapp import WhatsAppNotifier

log = structlog.get_logger()

DEFAULT_CHANNEL = "telegram"
DEFAULT_CHAIN = ("telegram", "email")

_BACKENDS: dict[str, type[Notifier]] = {
    "telegram": TelegramNotifier,
    "email": EmailNotifier,
    "whatsapp": WhatsAppNotifier,
    "mock": MockNotifier,
}


def get_notifier(name: str | None = None) -> Notifier:
    """Construct a single named backend ("telegram", "email", "whatsapp",
    or "mock").

    Reads NOTIFIER_BACKEND for the default when `name` is omitted, falling
    back to "telegram" if that isn't set either. Does not wire up a
    fallback chain -- see get_notifier_with_fallback for that.
    """
    resolved = name or os.environ.get("NOTIFIER_BACKEND", DEFAULT_CHANNEL)
    try:
        backend_cls = _BACKENDS[resolved]
    except KeyError:
        raise NotificationError(f"unknown notifier backend: {resolved!r}") from None
    return backend_cls()


class FallbackNotifier(Notifier):
    """Tries each backend in `chain` order; a RetryableNotificationError
    from one falls through to the next. Any other exception -- including
    WhatsAppNotifier's NotImplementedError, deliberately -- propagates
    immediately without falling back, the same "transient failures only"
    rule FallbackExtractor applies.
    """

    def __init__(self, chain: Sequence[Notifier]) -> None:
        if not chain:
            raise NotificationError("FallbackNotifier requires at least one backend")
        self._chain = tuple(chain)

    def send(
        self, recipient: str, message: NotificationMessage, idempotency_key: str
    ) -> DeliveryResult:
        last_error: RetryableNotificationError | None = None
        for i, notifier in enumerate(self._chain):
            try:
                return notifier.send(recipient, message, idempotency_key)
            except RetryableNotificationError as exc:
                last_error = exc
                log.warning(
                    "notifier_falling_back",
                    channel=type(notifier).__name__,
                    attempt=i + 1,
                    chain_length=len(self._chain),
                    error=str(exc),
                )
        assert last_error is not None
        raise last_error


def get_notifier_with_fallback(chain: Sequence[str] | None = None) -> Notifier:
    """The primary notifier for real approval routing: an ordered chain
    (NOTIFIER_CHAIN, comma-separated, or DEFAULT_CHAIN) of backends, each
    tried in order until one succeeds or the chain runs out.
    """
    resolved_names = chain or _chain_from_env()
    backends = [get_notifier(name.strip()) for name in resolved_names]
    return FallbackNotifier(backends)


def _chain_from_env() -> Sequence[str]:
    raw = os.environ.get("NOTIFIER_CHAIN")
    if not raw:
        return DEFAULT_CHAIN
    return [name for name in raw.split(",") if name.strip()]
