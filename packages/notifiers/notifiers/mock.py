"""A memory-backed Notifier. Every test that needs to verify "did we notify
someone, and with what" uses this instead of a real backend, so the suite
never touches a network, an SMTP server, or a live Bot API token.
"""

from dataclasses import dataclass

from notifiers.base import DeliveryResult, NotificationMessage, Notifier


@dataclass(frozen=True, slots=True)
class SentMessage:
    recipient: str
    message: NotificationMessage
    idempotency_key: str


class MockNotifier(Notifier):
    def __init__(self) -> None:
        self.sent: list[SentMessage] = []

    def send(
        self, recipient: str, message: NotificationMessage, idempotency_key: str
    ) -> DeliveryResult:
        self.sent.append(SentMessage(recipient, message, idempotency_key))
        return DeliveryResult(provider_message_id=f"mock-{len(self.sent)}", channel="mock")
