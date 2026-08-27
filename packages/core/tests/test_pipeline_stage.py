from enum import StrEnum

import pytest
from core.errors import UnmappedPipelineStage
from core.models import InvoiceStatus
from core.pipeline_stage import PipelineStage, stage_for_status


@pytest.mark.parametrize("status", list(InvoiceStatus))
def test_every_invoice_status_maps_to_a_pipeline_stage(status: InvoiceStatus) -> None:
    """Exhaustiveness guarantee for apps/api/api/events.py's SSE bridge: an
    invoice can be written to any InvoiceStatus value that exists today or
    is ever added, and stage_for_status() must have an answer for all of
    them -- an unmapped status must never surface as a KeyError three
    layers away inside a live tenant's event stream. Parametrizing over
    `list(InvoiceStatus)` (not a hardcoded list of members) is what makes
    this fail the moment someone adds a new status without updating
    pipeline_stage.py's table, rather than only catching it if they
    happen to also remember to update this test.
    """
    stage = stage_for_status(status)
    assert isinstance(stage, PipelineStage)


class _NotARealInvoiceStatus(StrEnum):
    """A standalone enum, never a key in _STAGE_BY_STATUS -- exists only so
    the test below can exercise stage_for_status()'s error path without a
    real InvoiceStatus member (there isn't one; they're all mapped)."""

    ROGUE = "ROGUE"


def test_unmapped_status_raises_unmapped_pipeline_stage() -> None:
    """Every real InvoiceStatus is mapped (see above), so this fabricates
    a status-shaped value that deliberately isn't one of them, purely to
    exercise stage_for_status()'s error path -- confirming it raises the
    named UnmappedPipelineStage, not a bare KeyError."""
    with pytest.raises(UnmappedPipelineStage):
        stage_for_status(_NotARealInvoiceStatus.ROGUE)  # type: ignore[arg-type]
