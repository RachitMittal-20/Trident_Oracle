"""WhatsApp notifier -- explicit, documented stub.

Not implemented. This module exists to show that the Notifier interface
(notifiers/base.py) accommodates a WhatsApp channel without needing
Doritech's actual WhatsApp Business credentials to build against -- the
shape of the integration is fully specified below, including the parts that
would trip up an implementation done without reading Meta's docs first.

## What this would actually take to stand up

**A Meta Business account and a WhatsApp Business Platform (Cloud API)
integration**, specifically:

- A Meta Business Manager account, with the business verified (Meta's
  "Business Verification" process -- legal business name, address, and
  supporting documents; this alone can take days and is not something a
  demo can shortcut).
- A WhatsApp Business Account (WABA) under that Business Manager, either
  self-hosted via the Cloud API (Meta-hosted, the current recommended path)
  or through a Business Solution Provider (Twilio, 360dialog, etc. -- an
  extra layer, extra cost, but no infrastructure of your own to run).
- A registered, verified phone number dedicated to the WABA -- it cannot
  simultaneously be an ordinary consumer WhatsApp account, and it cannot be
  the same number already registered elsewhere.
- A permanent access token (system user token, not a short-lived one) and
  the WABA's phone_number_id, both needed on every Graph API call:
  `POST https://graph.facebook.com/v20.0/{phone_number_id}/messages`.

**Message templates, pre-approved by Meta.** Unlike Telegram or email, you
cannot just send arbitrary freeform text as the *first* message in a
conversation. Any business-initiated message sent outside an open customer
service window (see below) must use a template: a fixed structure
(header/body/footer/buttons) with `{{1}}`, `{{2}}`, ... placeholders,
submitted to Meta for review, and approved before it can ever be sent. Review
can take anywhere from minutes to a couple of days, and Meta can reject a
template for tone, formatting, or perceived promotional content -- an
approval-request message ("Invoice INV-2026-00417 needs your approval,
total $3,200, tap to review") is exactly the kind of template that would need
to be drafted, submitted, and approved *before* this integration could send
its first real notification, not something wired up at deploy time.

**The 24-hour session window.** WhatsApp draws a hard line between two kinds
of messages:

- **Session (freeform) messages** -- ordinary text, sendable only within 24
  hours of the *user's* last inbound message to the business. Once that
  window closes, the business cannot send another freeform message until the
  user writes in again.
- **Template messages** -- the only thing a business can send to *re-open*
  contact once the 24-hour window has lapsed (or to message a user who has
  never written in at all). This is exactly Trident Oracle's actual shape of
  outbound traffic: the system is telling an approver about an exception they
  didn't ask about, not replying to something they said. In practice that
  means essentially every approval notification this system would send goes
  out as a template message, not a session message -- the 24-hour window is
  close to irrelevant here except for whatever back-and-forth happens *after*
  an approver first responds (a session opens the moment they reply, and a
  clarifying follow-up within that window could then be sent as freeform
  text).

A correct implementation would therefore need: a pre-approved template
specifically for the approval-request use case (with `{{n}}` placeholders for
invoice number, vendor, amount, and reason -- NotificationMessage.title/body
don't map onto that at all, they'd need to be decomposed into template
parameters instead of sent as free text), template *buttons* declared as part
of that same approved template (WhatsApp's "quick reply" buttons carry a
developer-defined button payload, filling the same role Telegram's
callback_data or the email approval-link token plays here), and a fallback
path (email, this package's other real channel) for the interval before a
template exists or is approved.

None of that is available for this project, so this stub is the honest
answer: implement the interface shape, refuse to pretend it works.
"""

from notifiers.base import DeliveryResult, NotificationMessage, Notifier


class WhatsAppNotifier(Notifier):
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def send(
        self, recipient: str, message: NotificationMessage, idempotency_key: str
    ) -> DeliveryResult:
        raise NotImplementedError(
            "WhatsAppNotifier is a documented stub -- see this module's docstring "
            "for what a real implementation needs (Meta Business verification, "
            "an approved message template, and the 24-hour session window). "
            "Not implemented because no WhatsApp Business credentials exist for "
            "this project."
        )
