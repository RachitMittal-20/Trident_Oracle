import pytest
from notifiers.base import NotificationMessage
from notifiers.whatsapp import WhatsAppNotifier


def test_construction_never_raises() -> None:
    WhatsAppNotifier()
    WhatsAppNotifier("anything", key="value")  # accepts arbitrary args, still doesn't raise


def test_send_raises_not_implemented_error() -> None:
    notifier = WhatsAppNotifier()
    with pytest.raises(NotImplementedError):
        notifier.send("+15551234567", NotificationMessage(title="t", body="b"), "idem-1")


def test_send_error_message_explains_why() -> None:
    notifier = WhatsAppNotifier()
    with pytest.raises(NotImplementedError, match="documented stub"):
        notifier.send("+15551234567", NotificationMessage(title="t", body="b"), "idem-1")
