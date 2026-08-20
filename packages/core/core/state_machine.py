"""The invoice status state machine. Every status change anywhere in this
codebase goes through validate_transition() -- no code ever sets
invoices.status directly. See docs/ARCHITECTURE.md section 5 for the state
diagram this is a direct encoding of.

CLAUDE.md principle 5: "Server-side state transition validation. Status is
never set directly by a client. Illegal transitions raise an exception; they
do not silently no-op." That's this module's entire job.
"""

from core.errors import InvalidStateTransition
from core.models import InvoiceStatus

ALLOWED_TRANSITIONS: dict[InvoiceStatus, frozenset[InvoiceStatus]] = {
    InvoiceStatus.RECEIVED: frozenset({InvoiceStatus.EXTRACTING}),
    InvoiceStatus.EXTRACTING: frozenset(
        {InvoiceStatus.EXTRACTED, InvoiceStatus.EXTRACTION_FAILED}
    ),
    InvoiceStatus.EXTRACTED: frozenset({InvoiceStatus.MATCHING}),
    InvoiceStatus.MATCHING: frozenset(
        {
            InvoiceStatus.MATCHED_CLEAN,
            InvoiceStatus.NEEDS_VERIFICATION,
            InvoiceStatus.EXCEPTIONS_RAISED,
        }
    ),
    InvoiceStatus.MATCHED_CLEAN: frozenset(
        {InvoiceStatus.AUTO_POSTED, InvoiceStatus.PENDING_APPROVAL}
    ),
    # Corrected in the verification UI, then re-runs the match.
    InvoiceStatus.NEEDS_VERIFICATION: frozenset({InvoiceStatus.MATCHING}),
    InvoiceStatus.EXCEPTIONS_RAISED: frozenset({InvoiceStatus.PENDING_APPROVAL}),
    InvoiceStatus.PENDING_APPROVAL: frozenset(
        {InvoiceStatus.APPROVED, InvoiceStatus.REJECTED}
    ),
    InvoiceStatus.APPROVED: frozenset({InvoiceStatus.POSTED}),
    # Terminal states -- the diagram has no outgoing edge from any of these.
    InvoiceStatus.AUTO_POSTED: frozenset(),
    InvoiceStatus.POSTED: frozenset(),
    InvoiceStatus.REJECTED: frozenset(),
    InvoiceStatus.EXTRACTION_FAILED: frozenset(),
}


def validate_transition(from_status: InvoiceStatus, to_status: InvoiceStatus) -> None:
    """Raises InvalidStateTransition if `to_status` is not a legal next state
    from `from_status`. Never no-ops on an illegal transition."""
    allowed = ALLOWED_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise InvalidStateTransition(
            f"illegal invoice status transition: {from_status} -> {to_status}"
        )
