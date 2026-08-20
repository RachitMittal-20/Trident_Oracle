import pytest
from core.errors import InvalidStateTransition
from core.models import InvoiceStatus
from core.state_machine import ALLOWED_TRANSITIONS, validate_transition


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (InvoiceStatus.RECEIVED, InvoiceStatus.EXTRACTING),
        (InvoiceStatus.EXTRACTING, InvoiceStatus.EXTRACTED),
        (InvoiceStatus.EXTRACTING, InvoiceStatus.EXTRACTION_FAILED),
        (InvoiceStatus.EXTRACTED, InvoiceStatus.MATCHING),
        (InvoiceStatus.MATCHING, InvoiceStatus.MATCHED_CLEAN),
        (InvoiceStatus.MATCHING, InvoiceStatus.NEEDS_VERIFICATION),
        (InvoiceStatus.MATCHING, InvoiceStatus.EXCEPTIONS_RAISED),
        (InvoiceStatus.MATCHED_CLEAN, InvoiceStatus.AUTO_POSTED),
        (InvoiceStatus.MATCHED_CLEAN, InvoiceStatus.PENDING_APPROVAL),
        (InvoiceStatus.NEEDS_VERIFICATION, InvoiceStatus.MATCHING),
        (InvoiceStatus.EXCEPTIONS_RAISED, InvoiceStatus.PENDING_APPROVAL),
        (InvoiceStatus.PENDING_APPROVAL, InvoiceStatus.APPROVED),
        (InvoiceStatus.PENDING_APPROVAL, InvoiceStatus.REJECTED),
        (InvoiceStatus.APPROVED, InvoiceStatus.POSTED),
    ],
)
def test_legal_transitions_do_not_raise(
    from_status: InvoiceStatus, to_status: InvoiceStatus
) -> None:
    validate_transition(from_status, to_status)  # must not raise


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (InvoiceStatus.RECEIVED, InvoiceStatus.EXTRACTED),  # skips EXTRACTING
        (InvoiceStatus.RECEIVED, InvoiceStatus.MATCHING),
        (InvoiceStatus.RECEIVED, InvoiceStatus.POSTED),
        (InvoiceStatus.EXTRACTING, InvoiceStatus.MATCHING),  # skips EXTRACTED
        (InvoiceStatus.EXTRACTED, InvoiceStatus.EXTRACTING),  # backwards
        (InvoiceStatus.EXTRACTED, InvoiceStatus.MATCHED_CLEAN),  # skips MATCHING
        (InvoiceStatus.MATCHING, InvoiceStatus.RECEIVED),
        (InvoiceStatus.MATCHING, InvoiceStatus.APPROVED),
        (InvoiceStatus.PENDING_APPROVAL, InvoiceStatus.POSTED),  # skips APPROVED
        (InvoiceStatus.APPROVED, InvoiceStatus.REJECTED),
        (InvoiceStatus.REJECTED, InvoiceStatus.PENDING_APPROVAL),  # terminal
        (InvoiceStatus.POSTED, InvoiceStatus.APPROVED),  # terminal
        (InvoiceStatus.AUTO_POSTED, InvoiceStatus.POSTED),  # terminal
        (InvoiceStatus.EXTRACTION_FAILED, InvoiceStatus.EXTRACTING),  # terminal
        (InvoiceStatus.RECEIVED, InvoiceStatus.RECEIVED),  # self-transition
    ],
)
def test_illegal_transitions_raise(from_status: InvoiceStatus, to_status: InvoiceStatus) -> None:
    with pytest.raises(InvalidStateTransition):
        validate_transition(from_status, to_status)


def test_every_status_has_an_entry_in_the_map() -> None:
    # Even terminal states must be explicit keys (mapped to an empty set),
    # not silently absent -- absence and "no legal transitions" should read
    # the same to callers, but the map should still be exhaustive to review.
    for status in InvoiceStatus:
        assert status in ALLOWED_TRANSITIONS


def test_terminal_states_allow_nothing() -> None:
    for terminal in (
        InvoiceStatus.AUTO_POSTED,
        InvoiceStatus.POSTED,
        InvoiceStatus.REJECTED,
        InvoiceStatus.EXTRACTION_FAILED,
    ):
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()
