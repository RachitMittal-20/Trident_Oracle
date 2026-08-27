"""Maps InvoiceStatus onto the four rail stages the /pipeline screen draws
(QUEUED -> EXTRACTING -> MATCHING -> DECIDED), plus FAILED for the one
status that leaves the rail entirely (apps/web's failures tray). Pure
mapping, no I/O -- consumed by apps/api/api/events.py when it enriches a
raw pg_notify payload before publishing it to an SSE client.
"""

from enum import StrEnum

from core.errors import UnmappedPipelineStage
from core.models import InvoiceStatus


class PipelineStage(StrEnum):
    QUEUED = "QUEUED"
    EXTRACTING = "EXTRACTING"
    MATCHING = "MATCHING"
    DECIDED = "DECIDED"
    FAILED = "FAILED"


_STAGE_BY_STATUS: dict[InvoiceStatus, PipelineStage] = {
    InvoiceStatus.RECEIVED: PipelineStage.QUEUED,
    InvoiceStatus.EXTRACTING: PipelineStage.EXTRACTING,
    InvoiceStatus.EXTRACTION_FAILED: PipelineStage.FAILED,
    InvoiceStatus.EXTRACTED: PipelineStage.MATCHING,
    InvoiceStatus.MATCHING: PipelineStage.MATCHING,
    InvoiceStatus.MATCHED_CLEAN: PipelineStage.DECIDED,
    InvoiceStatus.NEEDS_VERIFICATION: PipelineStage.DECIDED,
    InvoiceStatus.EXCEPTIONS_RAISED: PipelineStage.DECIDED,
    InvoiceStatus.AUTO_POSTED: PipelineStage.DECIDED,
    InvoiceStatus.PENDING_APPROVAL: PipelineStage.DECIDED,
    InvoiceStatus.APPROVED: PipelineStage.DECIDED,
    InvoiceStatus.REJECTED: PipelineStage.DECIDED,
    InvoiceStatus.POSTED: PipelineStage.DECIDED,
}


def stage_for_status(status: InvoiceStatus) -> PipelineStage:
    """Raises UnmappedPipelineStage, never a bare KeyError, if `status` has
    no entry above -- see test_pipeline_stage.py's exhaustiveness test,
    which is what should catch this long before it reaches here."""
    try:
        return _STAGE_BY_STATUS[status]
    except KeyError as exc:
        raise UnmappedPipelineStage(
            f"no PipelineStage mapped for InvoiceStatus.{status.name}"
        ) from exc
