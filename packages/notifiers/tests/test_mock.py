from notifiers.base import NotificationAction, NotificationMessage
from notifiers.mock import MockNotifier


def test_send_records_the_message() -> None:
    notifier = MockNotifier()
    message = NotificationMessage(
        title="t", body="b", actions=(NotificationAction(label="Approve", action_id="tok"),)
    )

    result = notifier.send("recipient-1", message, "idem-1")

    assert len(notifier.sent) == 1
    sent = notifier.sent[0]
    assert sent.recipient == "recipient-1"
    assert sent.message is message
    assert sent.idempotency_key == "idem-1"
    assert result.channel == "mock"
    assert result.provider_message_id == "mock-1"


def test_repeated_sends_get_distinct_provider_message_ids() -> None:
    notifier = MockNotifier()
    message = NotificationMessage(title="t", body="b")

    first = notifier.send("r", message, "idem-1")
    second = notifier.send("r", message, "idem-2")

    assert first.provider_message_id != second.provider_message_id
    assert len(notifier.sent) == 2
