"""Telegram Bot API notifier -- CLAUDE.md's primary notification channel.

httpx is imported only in this module, same discipline as the google-genai
SDK in extractors/gemini.py and httpx itself in storage/supabase_storage.py:
callers depend on notifiers.base.Notifier, never on the Bot API directly.

Two Bot API details this module has to get right, both easy to get wrong:

1. MarkdownV2 escaping. Telegram's MarkdownV2 parse mode is fussy: a
   reserved character appearing *anywhere* outside a recognized entity --
   even an ordinary period or hyphen in plain prose -- makes sendMessage
   fail outright with HTTP 400 ("can't parse entities"). The reserved set
   per Telegram's own docs is:

       _ * [ ] ( ) ~ ` > # + - = | { } . !

   This module recognizes exactly one markdown construct from
   NotificationMessage.body -- **bold** spans, this codebase's own
   convention (see notifiers/base.py) -- converts each to Telegram's
   single-asterisk *bold*, and escapes every reserved character everywhere
   else, including any stray `*` or `_` that isn't part of a recognized
   **bold** span. Correctness over expressiveness: an unescaped reserved
   character silently breaks message delivery, so nothing is passed through
   unescaped on the chance it might have been intentional formatting.

2. callback_data has a hard 64-byte limit (UTF-8 encoded) enforced by
   Telegram itself -- a button whose callback_data exceeds that is rejected
   by the API, not just recommended against. Since callback_data here
   carries the signed approval token (NotificationAction.action_id), this
   module checks the encoded length before sending and raises
   NotificationError with a clear message rather than letting Telegram's own
   400 respond with a much more opaque one.
"""

import os
import re

import httpx
from core.errors import NotificationError

from notifiers.base import DeliveryResult, NotificationMessage, Notifier
from notifiers.errors import RetryableNotificationError

DEFAULT_BASE_URL = "https://api.telegram.org"

# Telegram's own reserved-character list for MarkdownV2 (see module docstring).
_RESERVED = set("_*[]()~`>#+-=|{}.!")

# This codebase's own markdown convention: **bold** spans, non-greedy so
# "**a** and **b**" produces two spans, not one spanning "a** and **b".
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)

# callback_data's hard limit, per Telegram Bot API docs.
_MAX_CALLBACK_DATA_BYTES = 64


def escape_markdown_v2(text: str) -> str:
    """Escapes every MarkdownV2-reserved character in `text` so it renders
    as literal text, not (mis)parsed as an entity boundary."""
    return "".join(f"\\{ch}" if ch in _RESERVED else ch for ch in text)


def render_markdown_v2(text: str) -> str:
    """Converts this codebase's **bold** convention into Telegram MarkdownV2,
    escaping everything else. See module docstring point 1."""
    rendered: list[str] = []
    last_end = 0
    for match in _BOLD_RE.finditer(text):
        rendered.append(escape_markdown_v2(text[last_end : match.start()]))
        rendered.append("*" + escape_markdown_v2(match.group(1)) + "*")
        last_end = match.end()
    rendered.append(escape_markdown_v2(text[last_end:]))
    return "".join(rendered)


class TelegramNotifier(Notifier):
    def __init__(
        self,
        bot_token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        resolved_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not resolved_token:
            raise NotificationError("TELEGRAM_BOT_TOKEN is not set")
        self._token = resolved_token
        self._api_base = base_url.rstrip("/")
        self._client = client if client is not None else httpx.Client(timeout=10.0)

    def _inline_keyboard(self, message: NotificationMessage) -> list[list[dict[str, str]]] | None:
        if not message.actions:
            return None
        for action in message.actions:
            encoded_len = len(action.action_id.encode("utf-8"))
            if encoded_len > _MAX_CALLBACK_DATA_BYTES:
                raise NotificationError(
                    f"action {action.label!r} callback_data is {encoded_len} bytes, "
                    f"exceeding Telegram's {_MAX_CALLBACK_DATA_BYTES}-byte limit"
                )
        # One button per row -- reliably tappable on a phone screen, which is
        # how every approval notification in this system will actually be read.
        return [
            [{"text": action.label, "callback_data": action.action_id}]
            for action in message.actions
        ]

    def send(
        self, recipient: str, message: NotificationMessage, idempotency_key: str
    ) -> DeliveryResult:
        title = render_markdown_v2(message.title)
        body = render_markdown_v2(message.body)
        text = f"*{title}*\n\n{body}"

        payload: dict[str, object] = {
            "chat_id": recipient,
            "text": text,
            "parse_mode": "MarkdownV2",
        }
        keyboard = self._inline_keyboard(message)
        if keyboard is not None:
            payload["reply_markup"] = {"inline_keyboard": keyboard}

        try:
            response = self._client.post(
                f"{self._api_base}/bot{self._token}/sendMessage", json=payload
            )
        except httpx.HTTPError as exc:
            raise RetryableNotificationError(f"Telegram request failed: {exc}") from exc

        body_json = response.json() if response.content else {}

        if response.status_code == 429:
            retry_after = None
            params = body_json.get("parameters") if isinstance(body_json, dict) else None
            if isinstance(params, dict):
                retry_after = params.get("retry_after")
            header_retry_after = response.headers.get("retry-after")
            if retry_after is None and header_retry_after is not None:
                retry_after = header_retry_after
            raise RetryableNotificationError(
                f"Telegram rate limit hit for chat {recipient}",
                retry_after=float(retry_after) if retry_after is not None else None,
            )

        if response.status_code >= 500:
            raise RetryableNotificationError(
                f"Telegram server error {response.status_code}: {response.text}"
            )

        if response.status_code >= 400 or not body_json.get("ok"):
            description = body_json.get("description", response.text)
            raise NotificationError(f"Telegram sendMessage failed: {description}")

        result = body_json.get("result", {})
        message_id = result.get("message_id")
        return DeliveryResult(
            provider_message_id=str(message_id) if message_id is not None else None,
            channel="telegram",
        )
