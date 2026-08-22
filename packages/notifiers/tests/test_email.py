"""Unit tests for EmailNotifier using a fake SMTP client -- no real SMTP
connection is ever made.
"""

import smtplib
from email.message import EmailMessage

import pytest
from core.errors import NotificationError
from notifiers.base import NotificationAction, NotificationMessage
from notifiers.email import EmailNotifier
from notifiers.errors import RetryableNotificationError

APP_BASE_URL = "https://app.trident-oracle.example"


class FakeSMTP:
    def __init__(self, sent: list[EmailMessage], raise_on: dict[str, Exception] | None = None):
        self.sent = sent
        self._raise_on = raise_on or {}
        self.starttls_called = False
        self.login_args: tuple[str, str] | None = None

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def starttls(self) -> None:
        if "starttls" in self._raise_on:
            raise self._raise_on["starttls"]
        self.starttls_called = True

    def login(self, user: str, password: str) -> None:
        if "login" in self._raise_on:
            raise self._raise_on["login"]
        self.login_args = (user, password)

    def send_message(self, msg: EmailMessage) -> None:
        if "send_message" in self._raise_on:
            raise self._raise_on["send_message"]
        self.sent.append(msg)


def _notifier(
    sent: list[EmailMessage], raise_on: dict[str, Exception] | None = None
) -> EmailNotifier:
    return EmailNotifier(
        username="bot@example.com",
        password="secret",
        app_base_url=APP_BASE_URL,
        smtp_client_factory=lambda: FakeSMTP(sent, raise_on),
    )


def test_send_renders_html_and_plaintext_alternatives() -> None:
    sent: list[EmailMessage] = []
    notifier = _notifier(sent)
    message = NotificationMessage(
        title="Invoice INV-2026-00417 needs approval",
        body="Total **$3,200.00** exceeds the auto-approve threshold.",
        actions=(
            NotificationAction(label="Approve", action_id="tok-approve-1", style="primary"),
            NotificationAction(label="Reject", action_id="tok-reject-1", style="danger"),
        ),
    )

    result = notifier.send("approver@example.com", message, "idem-1")

    assert len(sent) == 1
    email_message = sent[0]
    assert email_message["Subject"] == "Invoice INV-2026-00417 needs approval"
    assert email_message["To"] == "approver@example.com"

    plaintext = email_message.get_body(preferencelist=("plain",)).get_content()
    assert "**" not in plaintext
    assert "$3,200.00" in plaintext
    assert f"{APP_BASE_URL}/approve/tok-approve-1" in plaintext
    assert f"{APP_BASE_URL}/approve/tok-reject-1" in plaintext

    html = email_message.get_body(preferencelist=("html",)).get_content()
    assert "<strong>$3,200.00</strong>" in html
    assert f'href="{APP_BASE_URL}/approve/tok-approve-1"' in html
    assert f'href="{APP_BASE_URL}/approve/tok-reject-1"' in html

    assert result.channel == "email"
    assert result.provider_message_id is not None
    assert "<" not in result.provider_message_id and ">" not in result.provider_message_id


def test_html_body_escapes_content_outside_bold_spans() -> None:
    sent: list[EmailMessage] = []
    notifier = _notifier(sent)
    message = NotificationMessage(title="t", body="<script>alert(1)</script> **safe**")

    notifier.send("approver@example.com", message, "idem-1")

    html = sent[0].get_body(preferencelist=("html",)).get_content()
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<strong>safe</strong>" in html


def test_starttls_and_login_are_called() -> None:
    sent: list[EmailMessage] = []
    fake = FakeSMTP(sent)
    notifier = EmailNotifier(
        username="bot@example.com",
        password="secret",
        app_base_url=APP_BASE_URL,
        smtp_client_factory=lambda: fake,
    )
    notifier.send("approver@example.com", NotificationMessage(title="t", body="b"), "idem-1")

    assert fake.starttls_called
    assert fake.login_args == ("bot@example.com", "secret")


def test_missing_app_base_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    with pytest.raises(NotificationError):
        EmailNotifier(smtp_client_factory=lambda: FakeSMTP([]))


def test_missing_smtp_host_raises_without_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with pytest.raises(NotificationError):
        EmailNotifier(app_base_url=APP_BASE_URL)


# --- retry classification ----------------------------------------------------


def test_recipients_refused_is_not_retryable() -> None:
    notifier = _notifier(
        [],
        raise_on={"send_message": smtplib.SMTPRecipientsRefused({"a@example.com": (550, b"no")})},
    )
    with pytest.raises(NotificationError) as exc_info:
        notifier.send("a@example.com", NotificationMessage(title="t", body="b"), "idem-1")
    assert not isinstance(exc_info.value, RetryableNotificationError)


def test_authentication_error_is_not_retryable() -> None:
    notifier = _notifier([], raise_on={"login": smtplib.SMTPAuthenticationError(535, b"bad creds")})
    with pytest.raises(NotificationError) as exc_info:
        notifier.send("a@example.com", NotificationMessage(title="t", body="b"), "idem-1")
    assert not isinstance(exc_info.value, RetryableNotificationError)


def test_connect_error_is_retryable() -> None:
    notifier = _notifier([], raise_on={"starttls": smtplib.SMTPConnectError(421, b"can't connect")})
    with pytest.raises(RetryableNotificationError):
        notifier.send("a@example.com", NotificationMessage(title="t", body="b"), "idem-1")


def test_server_disconnected_is_retryable() -> None:
    notifier = _notifier([], raise_on={"send_message": smtplib.SMTPServerDisconnected("gone")})
    with pytest.raises(RetryableNotificationError):
        notifier.send("a@example.com", NotificationMessage(title="t", body="b"), "idem-1")


def test_timeout_is_retryable() -> None:
    notifier = _notifier([], raise_on={"starttls": TimeoutError("timed out")})
    with pytest.raises(RetryableNotificationError):
        notifier.send("a@example.com", NotificationMessage(title="t", body="b"), "idem-1")
