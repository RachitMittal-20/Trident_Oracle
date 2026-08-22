"""Email notifier over SMTP -- CLAUDE.md's secondary notification channel.

Sends a multipart/alternative message (HTML plus a plaintext alternative,
per RFC 2046 -- a mail client that can't or won't render HTML still gets a
readable message) built from the same channel-agnostic NotificationMessage
every other backend renders. Actions render as buttons linking to
{APP_BASE_URL}/approve/{action_id} -- action_id carries the signed approval
token (see notifiers/base.py's docstring on why the message model has no
opinion about what that string means); which decision a token resolves to
is encoded in the token itself, not in the URL path, so every action renders
through the same /approve/ route regardless of its label or style.

smtplib and email.* are imported only in this module -- same discipline as
every other backend in this package.
"""

import os
import re
import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import make_msgid
from html import escape as html_escape

from core.errors import NotificationError

from notifiers.base import DeliveryResult, NotificationMessage, Notifier
from notifiers.errors import RetryableNotificationError

# This codebase's own markdown convention (see notifiers/base.py): **bold**
# spans, otherwise plain text.
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)

_BUTTON_COLORS = {
    "primary": "#2EA88A",  # signal-clean
    "secondary": "#6E7BFF",  # accent
    "danger": "#E5534B",  # signal-block
}

DEFAULT_SMTP_PORT = 587


def _strip_bold(text: str) -> str:
    """Plaintext alternative: drop the ** markers, keep the words."""
    return _BOLD_RE.sub(r"\1", text)


def _render_html_body(text: str) -> str:
    """HTML alternative. Escapes everything first -- an outbound email
    rendering untrusted content as raw HTML is exactly as much of an
    injection risk as a web page doing the same -- then re-introduces
    **bold** as <strong> only around already-escaped content, so nothing
    inside a bold span can smuggle markup through.
    """
    rendered: list[str] = []
    last_end = 0
    for match in _BOLD_RE.finditer(text):
        rendered.append(html_escape(text[last_end : match.start()]).replace("\n", "<br>"))
        rendered.append(f"<strong>{html_escape(match.group(1))}</strong>")
        last_end = match.end()
    rendered.append(html_escape(text[last_end:]).replace("\n", "<br>"))
    return "".join(rendered)


def _render_html_button(label: str, url: str, style: str) -> str:
    color = _BUTTON_COLORS.get(style, _BUTTON_COLORS["secondary"])
    return (
        f'<a href="{html_escape(url)}" '
        f'style="display:inline-block;margin:4px 8px 4px 0;padding:10px 20px;'
        f"background:{color};color:#E6EDF3;text-decoration:none;"
        f'border-radius:6px;font-family:sans-serif;font-weight:600;">'
        f"{html_escape(label)}</a>"
    )


class EmailNotifier(Notifier):
    def __init__(
        self,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        from_address: str | None = None,
        app_base_url: str | None = None,
        smtp_client_factory: Callable[[], smtplib.SMTP] | None = None,
    ) -> None:
        resolved_host = smtp_host or os.environ.get("SMTP_HOST")
        if not resolved_host and smtp_client_factory is None:
            raise NotificationError("SMTP_HOST is not set")
        self._host = resolved_host
        self._port = smtp_port or int(os.environ.get("SMTP_PORT", str(DEFAULT_SMTP_PORT)))
        self._username = username or os.environ.get("SMTP_USER")
        self._password = password or os.environ.get("SMTP_PASS")
        self._from_address = from_address or self._username or "notifications@trident-oracle.local"

        resolved_app_base_url = app_base_url or os.environ.get("APP_BASE_URL")
        if not resolved_app_base_url:
            raise NotificationError("APP_BASE_URL is not set")
        self._app_base_url = resolved_app_base_url.rstrip("/")

        self._smtp_client_factory = smtp_client_factory or self._default_smtp_client

    def _default_smtp_client(self) -> smtplib.SMTP:
        assert self._host is not None
        return smtplib.SMTP(self._host, self._port, timeout=10.0)

    def send(
        self, recipient: str, message: NotificationMessage, idempotency_key: str
    ) -> DeliveryResult:
        html_actions = "".join(
            _render_html_button(
                action.label, f"{self._app_base_url}/approve/{action.action_id}", action.style
            )
            for action in message.actions
        )
        plaintext_actions = "\n".join(
            f"{action.label}: {self._app_base_url}/approve/{action.action_id}"
            for action in message.actions
        )

        html_title = html_escape(message.title)
        html_body_text = _render_html_body(message.body)
        html = (
            '<html><body style="font-family:sans-serif;background:#08090B;'
            'color:#E6EDF3;padding:24px;">'
            f'<h2 style="margin:0 0 12px;">{html_title}</h2>'
            f'<p style="white-space:normal;">{html_body_text}</p>'
            f'<div style="margin-top:20px;">{html_actions}</div>'
            "</body></html>"
        )

        plaintext = message.title + "\n\n" + _strip_bold(message.body)
        if plaintext_actions:
            plaintext += "\n\n" + plaintext_actions

        email_message = EmailMessage()
        email_message["Subject"] = message.title
        email_message["From"] = self._from_address
        email_message["To"] = recipient
        provider_message_id = make_msgid()
        email_message["Message-ID"] = provider_message_id
        email_message.set_content(plaintext)
        email_message.add_alternative(html, subtype="html")

        try:
            with self._smtp_client_factory() as client:
                client.starttls()
                if self._username and self._password:
                    client.login(self._username, self._password)
                client.send_message(email_message)
        except smtplib.SMTPRecipientsRefused as exc:
            raise NotificationError(f"recipient refused: {recipient}") from exc
        except smtplib.SMTPAuthenticationError as exc:
            raise NotificationError(f"SMTP authentication failed: {exc}") from exc
        except (
            smtplib.SMTPConnectError,
            smtplib.SMTPServerDisconnected,
            smtplib.SMTPHeloError,
            TimeoutError,
            ConnectionError,
        ) as exc:
            raise RetryableNotificationError(f"transient SMTP failure: {exc}") from exc
        except smtplib.SMTPException as exc:
            raise NotificationError(f"SMTP send failed: {exc}") from exc

        return DeliveryResult(provider_message_id=provider_message_id.strip("<>"), channel="email")
