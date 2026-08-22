import pytest
from core.errors import NotificationError
from notifiers.base import NotificationMessage
from notifiers.errors import RetryableNotificationError
from notifiers.factory import (
    DEFAULT_CHAIN,
    FallbackNotifier,
    _chain_from_env,
    get_notifier,
    get_notifier_with_fallback,
)
from notifiers.mock import MockNotifier
from notifiers.whatsapp import WhatsAppNotifier


class _AlwaysRetryable:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, recipient: str, message: NotificationMessage, idempotency_key: str) -> object:
        self.calls += 1
        raise RetryableNotificationError("transient failure")


class _AlwaysNonRetryable:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, recipient: str, message: NotificationMessage, idempotency_key: str) -> object:
        self.calls += 1
        raise NotificationError("permanent failure")


# --- get_notifier -------------------------------------------------------------


def test_get_notifier_mock() -> None:
    assert isinstance(get_notifier("mock"), MockNotifier)


def test_get_notifier_whatsapp_constructs_but_send_is_unimplemented() -> None:
    notifier = get_notifier("whatsapp")
    assert isinstance(notifier, WhatsAppNotifier)


def test_get_notifier_unknown_backend_raises() -> None:
    with pytest.raises(NotificationError):
        get_notifier("carrier-pigeon")


def test_get_notifier_reads_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTIFIER_BACKEND", "mock")
    assert isinstance(get_notifier(), MockNotifier)


# --- FallbackNotifier ---------------------------------------------------------


def test_fallback_falls_through_on_retryable_error() -> None:
    failing = _AlwaysRetryable()
    mock = MockNotifier()
    chain = FallbackNotifier([failing, mock])  # type: ignore[list-item]

    message = NotificationMessage(title="t", body="b")
    result = chain.send("r", message, "idem-1")

    assert failing.calls == 1
    assert len(mock.sent) == 1
    assert result.channel == "mock"


def test_fallback_does_not_try_first_backend_again_once_it_succeeds() -> None:
    mock = MockNotifier()
    chain = FallbackNotifier([mock])

    chain.send("r", NotificationMessage(title="t", body="b"), "idem-1")

    assert len(mock.sent) == 1


def test_fallback_does_not_fall_through_on_non_retryable_error() -> None:
    failing = _AlwaysNonRetryable()
    mock = MockNotifier()
    chain = FallbackNotifier([failing, mock])  # type: ignore[list-item]

    with pytest.raises(NotificationError) as exc_info:
        chain.send("r", NotificationMessage(title="t", body="b"), "idem-1")

    assert not isinstance(exc_info.value, RetryableNotificationError)
    assert failing.calls == 1
    assert mock.sent == []  # never reached


def test_fallback_raises_last_error_when_entire_chain_is_exhausted() -> None:
    a = _AlwaysRetryable()
    b = _AlwaysRetryable()
    chain = FallbackNotifier([a, b])  # type: ignore[list-item]

    with pytest.raises(RetryableNotificationError):
        chain.send("r", NotificationMessage(title="t", body="b"), "idem-1")
    assert a.calls == 1
    assert b.calls == 1


def test_fallback_empty_chain_raises_at_construction() -> None:
    with pytest.raises(NotificationError):
        FallbackNotifier([])


# --- get_notifier_with_fallback / env chain parsing --------------------------


def test_get_notifier_with_fallback_explicit_chain() -> None:
    notifier = get_notifier_with_fallback(["mock"])
    result = notifier.send("r", NotificationMessage(title="t", body="b"), "idem-1")
    assert result.channel == "mock"


def test_chain_from_env_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTIFIER_CHAIN", raising=False)
    assert _chain_from_env() == DEFAULT_CHAIN


def test_chain_from_env_parses_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTIFIER_CHAIN", "mock, telegram ,email")
    assert _chain_from_env() == ["mock", " telegram ", "email"]
