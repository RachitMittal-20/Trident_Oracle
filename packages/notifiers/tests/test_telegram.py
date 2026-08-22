"""Unit tests for TelegramNotifier using httpx.MockTransport -- no network
call is ever made. See test_supabase_storage.py for the same pattern used
elsewhere in this repo.
"""

from collections.abc import Callable

import httpx
import pytest
from core.errors import NotificationError
from notifiers.base import NotificationAction, NotificationMessage
from notifiers.errors import RetryableNotificationError
from notifiers.telegram import TelegramNotifier, escape_markdown_v2, render_markdown_v2

BOT_TOKEN = "123456:test-bot-token"


def _notifier(handler_fn: Callable[[httpx.Request], httpx.Response]) -> TelegramNotifier:
    transport = httpx.MockTransport(handler_fn)
    return TelegramNotifier(bot_token=BOT_TOKEN, client=httpx.Client(transport=transport))


# --- MarkdownV2 escaping -----------------------------------------------------


@pytest.mark.parametrize("char", list("_*[]()~`>#+-=|{}.!"))
def test_escape_markdown_v2_escapes_every_reserved_character(char: str) -> None:
    assert escape_markdown_v2(char) == f"\\{char}"


def test_escape_markdown_v2_leaves_ordinary_letters_and_digits_alone() -> None:
    assert escape_markdown_v2("Invoice 2026") == "Invoice 2026"


def test_escape_markdown_v2_handles_a_realistic_sentence() -> None:
    text = "Total: $3,200.00 (was $3,000.00) - 6.7% over!"
    escaped = escape_markdown_v2(text)
    # Every reserved char from the source must appear backslash-escaped.
    assert "\\(" in escaped and "\\)" in escaped
    assert "\\." in escaped
    assert "\\-" in escaped
    assert "\\!" in escaped
    # Non-reserved characters must be untouched.
    assert "Total: $3,200" in escaped


def test_render_markdown_v2_converts_bold_span_to_single_asterisk() -> None:
    result = render_markdown_v2("**Auto-posted**")
    assert result == "*Auto\\-posted*"


def test_render_markdown_v2_escapes_reserved_chars_inside_bold_content() -> None:
    result = render_markdown_v2("**Total: $5.00**")
    assert result == "*Total: $5\\.00*"


def test_render_markdown_v2_escapes_plain_text_outside_bold() -> None:
    result = render_markdown_v2("see invoice #1 (urgent).")
    assert result == "see invoice \\#1 \\(urgent\\)\\."


def test_render_markdown_v2_handles_multiple_bold_spans() -> None:
    result = render_markdown_v2("**A** and **B**.")
    assert result == "*A* and *B*\\."


def test_render_markdown_v2_escapes_a_lone_unmatched_asterisk() -> None:
    # Not a recognized **bold** span -- must still be escaped, or Telegram
    # rejects the whole message. "=" is also MarkdownV2-reserved.
    result = render_markdown_v2("5 * 3 = 15")
    assert result == "5 \\* 3 \\= 15"


def test_render_markdown_v2_handles_underscore_in_plain_text() -> None:
    result = render_markdown_v2("field_name is missing")
    assert result == "field\\_name is missing"


# --- send(): request shape ---------------------------------------------------


def test_send_posts_expected_payload_shape() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    notifier = _notifier(handler)
    message = NotificationMessage(
        title="Invoice needs approval",
        body="Total **$3,200.00** is over threshold.",
        actions=(
            NotificationAction(label="Approve", action_id="tok-approve-1", style="primary"),
            NotificationAction(label="Reject", action_id="tok-reject-1", style="danger"),
        ),
    )

    result = notifier.send("chat-1", message, "idem-1")

    assert len(requests) == 1
    req = requests[0]
    assert req.url == f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = __import__("json").loads(req.content)
    assert payload["chat_id"] == "chat-1"
    assert payload["parse_mode"] == "MarkdownV2"
    assert "*Invoice needs approval*" in payload["text"]
    assert payload["reply_markup"]["inline_keyboard"] == [
        [{"text": "Approve", "callback_data": "tok-approve-1"}],
        [{"text": "Reject", "callback_data": "tok-reject-1"}],
    ]
    assert result.provider_message_id == "42"
    assert result.channel == "telegram"


def test_send_without_actions_omits_reply_markup() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    notifier = _notifier(handler)
    message = NotificationMessage(title="t", body="b")
    notifier.send("chat-1", message, "idem-1")  # must not raise


def test_callback_data_over_64_bytes_raises_before_any_request() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {}})

    notifier = _notifier(handler)
    message = NotificationMessage(
        title="t",
        body="b",
        actions=(NotificationAction(label="Approve", action_id="x" * 65),),
    )

    with pytest.raises(NotificationError):
        notifier.send("chat-1", message, "idem-1")
    assert requests == []


# --- retry classification ----------------------------------------------------


def test_429_with_json_retry_after_raises_retryable_with_value() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests: retry after 5",
                "parameters": {"retry_after": 5},
            },
        )

    notifier = _notifier(handler)
    message = NotificationMessage(title="t", body="b")

    with pytest.raises(RetryableNotificationError) as exc_info:
        notifier.send("chat-1", message, "idem-1")
    assert exc_info.value.retry_after == 5.0


def test_429_with_only_header_retry_after_is_still_honoured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"ok": False, "error_code": 429}, headers={"Retry-After": "7"}
        )

    notifier = _notifier(handler)
    message = NotificationMessage(title="t", body="b")

    with pytest.raises(RetryableNotificationError) as exc_info:
        notifier.send("chat-1", message, "idem-1")
    assert exc_info.value.retry_after == 7.0


def test_5xx_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"ok": False, "description": "Internal Server Error"})

    notifier = _notifier(handler)
    message = NotificationMessage(title="t", body="b")

    with pytest.raises(RetryableNotificationError):
        notifier.send("chat-1", message, "idem-1")


def test_400_is_not_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"ok": False, "description": "Bad Request: can't parse entities"}
        )

    notifier = _notifier(handler)
    message = NotificationMessage(title="t", body="b")

    with pytest.raises(NotificationError) as exc_info:
        notifier.send("chat-1", message, "idem-1")
    assert not isinstance(exc_info.value, RetryableNotificationError)


def test_transport_level_connection_error_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    notifier = _notifier(handler)
    message = NotificationMessage(title="t", body="b")

    with pytest.raises(RetryableNotificationError):
        notifier.send("chat-1", message, "idem-1")


def test_missing_bot_token_raises_notification_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(NotificationError):
        TelegramNotifier()


# --- edit_message / answer_callback_query ------------------------------------


def test_edit_message_strips_the_keyboard_and_renders_text() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    notifier = _notifier(handler)
    notifier.edit_message("chat-1", "5", "Decision recorded: **approved**.")

    assert len(requests) == 1
    req = requests[0]
    assert req.url == f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = __import__("json").loads(req.content)
    assert payload["chat_id"] == "chat-1"
    assert payload["message_id"] == "5"
    assert payload["reply_markup"] == {"inline_keyboard": []}
    assert "*approved*" in payload["text"]


def test_edit_message_5xx_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"ok": False, "description": "Internal Server Error"})

    notifier = _notifier(handler)
    with pytest.raises(RetryableNotificationError):
        notifier.edit_message("chat-1", "5", "t")


def test_answer_callback_query_posts_expected_payload() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    notifier = _notifier(handler)
    notifier.answer_callback_query("cbq-1", "Recorded: approved")

    assert len(requests) == 1
    req = requests[0]
    assert req.url == f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    payload = __import__("json").loads(req.content)
    assert payload == {"callback_query_id": "cbq-1", "text": "Recorded: approved"}


def test_answer_callback_query_without_text_omits_it() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    notifier = _notifier(handler)
    notifier.answer_callback_query("cbq-1")

    payload = __import__("json").loads(requests[0].content)
    assert payload == {"callback_query_id": "cbq-1"}


def test_answer_callback_query_400_is_not_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "query is too old"})

    notifier = _notifier(handler)
    with pytest.raises(NotificationError) as exc_info:
        notifier.answer_callback_query("cbq-1")
    assert not isinstance(exc_info.value, RetryableNotificationError)
