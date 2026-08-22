"""NotificationMessage/NotificationAction must stay channel-agnostic -- no
backend-specific type or vocabulary leaks into the message model itself.
"""

import ast
import dataclasses
import inspect

from notifiers import base
from notifiers.base import DeliveryResult, NotificationAction, NotificationMessage

# Prose in base.py's own docstrings is allowed to *mention* other modules by
# filename (see its module docstring, which points to telegram.py/email.py
# as examples) -- what must never happen is base.py actually importing one,
# which is what would make the message model depend on a specific channel.
_FORBIDDEN_IMPORTS = (
    "httpx",
    "smtplib",
    "notifiers.telegram",
    "notifiers.email",
    "notifiers.whatsapp",
)


def test_base_module_imports_no_specific_channel() -> None:
    tree = ast.parse(inspect.getsource(base))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    for forbidden in _FORBIDDEN_IMPORTS:
        assert forbidden not in imported, f"notifiers.base imports channel-specific {forbidden!r}"


def test_notification_message_fields_are_plain_data_types() -> None:
    field_types = {f.name: f.type for f in dataclasses.fields(NotificationMessage)}
    assert field_types == {
        "title": str,
        "body": str,
        "actions": tuple[NotificationAction, ...],
        "metadata": dict[str, object],
    }


def test_notification_action_fields_are_plain_data_types() -> None:
    field_types = {f.name: f.type for f in dataclasses.fields(NotificationAction)}
    assert field_types == {
        "label": str,
        "action_id": str,
        "style": base.ActionStyle,
    }


def test_message_and_action_construct_without_any_backend_import() -> None:
    # If constructing these required knowing about a specific backend, this
    # module-level import list alone (no notifiers.telegram/email/whatsapp)
    # would already fail to satisfy the type checker / runtime behavior.
    action = NotificationAction(label="Approve", action_id="tok123", style="primary")
    message = NotificationMessage(title="t", body="b", actions=(action,), metadata={"k": "v"})
    assert message.actions[0].action_id == "tok123"


def test_delivery_result_is_channel_agnostic_shape() -> None:
    field_types = {f.name: f.type for f in dataclasses.fields(DeliveryResult)}
    assert field_types == {"provider_message_id": str | None, "channel": str}
