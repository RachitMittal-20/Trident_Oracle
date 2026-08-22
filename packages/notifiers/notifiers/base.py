"""The Notifier interface and its channel-agnostic message model.

Every notification backend (Telegram, email, WhatsApp, the mock used in
tests) implements Notifier. Callers -- the worker's 'notify' handler, the
approval-routing code -- never depend on a backend directly, and never
construct anything backend-specific to describe what to send: they build one
NotificationMessage and hand it to whichever Notifier the factory resolved.
CLAUDE.md principle 2, "every external dependency sits behind an interface,"
applied to notifications the same way packages/extractors applies it to
Gemini/Tesseract and packages/storage applies it to Supabase Storage.

NotificationMessage is deliberately ignorant of every channel it might end up
rendered through: no Telegram inline-keyboard shape, no HTML, no MIME parts,
no WhatsApp template name. It carries a title, a body written in a small,
plain markdown subset (this codebase's own convention: **bold** spans,
otherwise plain text -- not full CommonMark), a list of actions, and an open
metadata dict for anything a specific channel wants to look up without the
message model needing a field for it. Each backend is responsible for
translating that into whatever its wire format actually requires -- see
telegram.py's MarkdownV2 escaping and email.py's HTML rendering for two very
different answers to "what does **bold** mean here."
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

ActionStyle = Literal["primary", "secondary", "danger"]


@dataclass(frozen=True, slots=True)
class NotificationAction:
    """One actionable button on a notification -- e.g. "Approve" / "Reject".

    `action_id` is an opaque string as far as this model is concerned: it is
    whatever the caller needs a channel to carry back (or link to) so a
    decision can be resolved later -- in practice, a signed approval token.
    NotificationMessage does not know or care that it's a token; that
    meaning belongs to the caller and to whichever backend renders it (a
    Telegram callback_data payload, an /approve/{token} URL, ...).
    """

    label: str
    action_id: str
    style: ActionStyle = "secondary"


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    """A channel-agnostic notification. `body` is markdown in this
    codebase's own restricted sense -- **bold** spans are recognized,
    everything else is plain text -- not a full CommonMark document; a
    backend that can't render markdown at all is free to strip it."""

    title: str
    body: str
    actions: tuple[NotificationAction, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """What a successful Notifier.send() returns. `provider_message_id` is
    the backend's own identifier for the sent message -- a Telegram
    message_id, an email Message-ID -- so a later decision (approve/reject)
    can edit or reference the original notification. None when a backend has
    no such concept."""

    provider_message_id: str | None
    channel: str


class Notifier(ABC):
    """Interface every notification backend implements."""

    @abstractmethod
    def send(
        self, recipient: str, message: NotificationMessage, idempotency_key: str
    ) -> DeliveryResult:
        """Send `message` to `recipient` over this backend's channel.

        `recipient` is channel-shaped (a Telegram chat id, an email
        address, a WhatsApp phone number) -- resolving the right recipient
        for a channel is the caller's job, not this interface's.

        `idempotency_key` identifies this specific send at the application
        level (matching notification_deliveries.idempotency_key). No
        backend here has a native provider-side dedupe mechanism for an
        arbitrary key like this -- true idempotency comes from the caller
        checking notification_deliveries before ever calling send() again
        for the same key, the same way every other retryable job in this
        system works. Backends accept the parameter so that contract is
        visible in every implementation's signature, not because any of
        them enforce it themselves.

        Raises NotificationError (packages/core) on failure, or its
        subclass RetryableNotificationError for a transient failure safe to
        retry -- never returns a partial or sentinel result.
        """
        raise NotImplementedError
