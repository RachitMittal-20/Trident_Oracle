"""The 'extract' job handler: downloads the invoice file, runs it through
the extractor factory (Gemini, falling back to Tesseract), persists lines
and field confidences, and enqueues the follow-up 'match' job.

Every invoice status change goes through core.state_machine.validate_transition
and writes to audit_log -- CLAUDE.md principles 4 and 5, no exceptions.

Retry vs. permanent failure: by the time extractor.extract() raises here,
the extractor factory's own FallbackExtractor has already retried Gemini and
fallen through to Tesseract -- see packages/extractors/factory.py. That
already absorbs the architecture doc's "(fail x3)" at the extraction-backend
level. What this handler adds on top is job-level retry (the queue's own
backoff, orthogonal to invoice status): a failure only moves the invoice to
the terminal EXTRACTION_FAILED state once this is the job's *last* attempt
(job.attempts + 1 >= job.max_attempts) -- otherwise the invoice is left at
EXTRACTING and the exception is re-raised so worker/main.py's run_one_job
reschedules the job normally. Marking EXTRACTION_FAILED on every failed
attempt would make a subsequent retry impossible: EXTRACTION_FAILED has no
outgoing transition (it's terminal), so re-entering EXTRACTING from it would
itself raise InvalidStateTransition.
"""

import hashlib
import uuid
from typing import Any

import psycopg
import structlog
from core.errors import ExtractionError, TridentOracleError
from core.magic_bytes import sniff_mime_type
from core.models import InvoiceStatus, JobType
from core.queue.models import Job
from core.state_machine import validate_transition
from extractors.base import ExtractionResult
from extractors.factory import get_extractor_with_fallback
from extractors.normalize import parse_currency, parse_date
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from storage.base import Storage

from worker.db import JobQueue

log = structlog.get_logger()


def _transition(
    conn: psycopg.Connection[Any],
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID,
    from_status: InvoiceStatus,
    to_status: InvoiceStatus,
    extra_audit: dict[str, Any] | None = None,
) -> None:
    validate_transition(from_status, to_status)
    after: dict[str, Any] = {"status": to_status.value}
    if extra_audit:
        after.update(extra_audit)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE invoices SET status = %s, updated_at = now() WHERE id = %s",
            (to_status.value, invoice_id),
        )
        cur.execute(
            """
            INSERT INTO audit_log
                (tenant_id, actor_type, action, entity_type, entity_id, before, after)
            VALUES (%s, 'system', 'status_transition', 'invoice', %s, %s, %s)
            """,
            (
                tenant_id,
                invoice_id,
                Jsonb({"status": from_status.value}),
                Jsonb(after),
            ),
        )
    conn.commit()


def _safe_parse_date(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return parse_date(raw)
    except ExtractionError:
        log.warning("extract_field_unparseable", field="date", raw=raw)
        return None


def _safe_parse_currency(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return parse_currency(raw)
    except ExtractionError:
        log.warning("extract_field_unparseable", field="currency_amount", raw=raw)
        return None


def compute_overall_confidence(confidence: dict[str, float]) -> float | None:
    """The MINIMUM field confidence, not the mean.

    A mean lets one badly-read field hide behind many easy, high-confidence
    ones (e.g. nine correctly-read line descriptions averaging out one
    garbled total). The weakest field is what should determine whether a
    human needs to look -- one wrong number is enough to post a wrong
    invoice, so the decision matrix (docs/ARCHITECTURE.md section 6) has to
    react to the worst case, not the average case. None (not 0) when there
    are no confidence scores at all -- an extraction that read nothing isn't
    "zero confidence everywhere," it's "we don't know."
    """
    return min(confidence.values()) if confidence else None


def _persist_extraction(
    conn: psycopg.Connection[Any],
    invoice_id: uuid.UUID,
    tenant_id: uuid.UUID,
    result: ExtractionResult,
) -> None:
    header = result.header
    invoice_date = _safe_parse_date(header.invoice_date)
    due_date = _safe_parse_date(header.due_date)
    subtotal = _safe_parse_currency(header.subtotal)
    tax = _safe_parse_currency(header.tax)
    total = _safe_parse_currency(header.total)
    currency = (
        header.currency.strip().upper()
        if header.currency and len(header.currency.strip()) == 3
        else None
    )

    overall_confidence = compute_overall_confidence(result.confidence)

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE invoices
            SET invoice_number = coalesce(%(invoice_number)s, invoice_number),
                invoice_date = coalesce(%(invoice_date)s, invoice_date),
                due_date = coalesce(%(due_date)s, due_date),
                currency = coalesce(%(currency)s, currency),
                subtotal = coalesce(%(subtotal)s, subtotal),
                tax = coalesce(%(tax)s, tax),
                total = coalesce(%(total)s, total),
                extraction_backend = %(extraction_backend)s,
                overall_confidence = %(overall_confidence)s,
                updated_at = now()
            WHERE id = %(invoice_id)s
            """,
            {
                "invoice_number": header.invoice_number,
                "invoice_date": invoice_date,
                "due_date": due_date,
                "currency": currency,
                "subtotal": subtotal,
                "tax": tax,
                "total": total,
                "extraction_backend": result.backend,
                "overall_confidence": overall_confidence,
                "invoice_id": invoice_id,
            },
        )

        for line_no, line in enumerate(result.line_items, start=1):
            qty = _safe_parse_currency(line.qty)
            unit_price = _safe_parse_currency(line.unit_price)
            line_total = _safe_parse_currency(line.line_total)
            cur.execute(
                """
                INSERT INTO invoice_lines
                    (invoice_id, tenant_id, line_no, description, qty, unit_price, line_total)
                VALUES (%(invoice_id)s, %(tenant_id)s, %(line_no)s, %(description)s, %(qty)s,
                        %(unit_price)s, %(line_total)s)
                """,
                {
                    "invoice_id": invoice_id,
                    "tenant_id": tenant_id,
                    "line_no": line_no,
                    "description": line.description,
                    # Unparseable numeric fields land as 0, paired with
                    # whatever confidence score the extractor reported for
                    # that field -- visible to a human reviewer, not
                    # silently dropped. The matching engine's arithmetic
                    # check (docs/ARCHITECTURE.md stage 5) is what's
                    # supposed to catch qty * unit_price != line_total, not
                    # this persistence step -- rejecting the row here would
                    # throw away a real, if imperfect, extraction result.
                    "qty": qty if qty is not None else 0,
                    "unit_price": unit_price if unit_price is not None else 0,
                    "line_total": line_total if line_total is not None else 0,
                },
            )

        for field_path, confidence in result.confidence.items():
            bbox = result.bbox.get(field_path)
            cur.execute(
                """
                INSERT INTO field_confidences
                    (invoice_id, tenant_id, field_path, confidence, bbox)
                VALUES (%(invoice_id)s, %(tenant_id)s, %(field_path)s, %(confidence)s, %(bbox)s)
                """,
                {
                    "invoice_id": invoice_id,
                    "tenant_id": tenant_id,
                    "field_path": field_path,
                    "confidence": confidence,
                    "bbox": Jsonb(bbox.model_dump()) if bbox is not None else None,
                },
            )
    conn.commit()


def make_extract_handler(storage: Storage) -> Any:
    """Returns a Handler closed over `storage`, so the handler itself stays
    a plain function matching worker.registry.Handler's signature -- the
    registry has no notion of dependency injection beyond that."""

    def handle_extract(conn: psycopg.Connection[Any], queue: JobQueue, job: Job) -> None:
        invoice_id = uuid.UUID(job.payload["invoice_id"])
        tenant_id = job.tenant_id

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,))
            invoice = cur.fetchone()
        if invoice is None:
            raise ExtractionError(f"invoice {invoice_id} not found")

        current_status = InvoiceStatus(invoice["status"])
        _transition(conn, invoice_id, tenant_id, current_status, InvoiceStatus.EXTRACTING)

        is_last_attempt = job.attempts + 1 >= job.max_attempts

        try:
            file_bytes = storage.download(invoice["source_file_path"])
            mime_type = sniff_mime_type(file_bytes)
            if mime_type is None:
                raise ExtractionError(
                    f"stored file for invoice {invoice_id} is not a recognized format"
                )
            extractor = get_extractor_with_fallback()
            result = extractor.extract(file_bytes, mime_type)
        except (TridentOracleError, OSError) as exc:
            if is_last_attempt:
                _transition(
                    conn,
                    invoice_id,
                    tenant_id,
                    InvoiceStatus.EXTRACTING,
                    InvoiceStatus.EXTRACTION_FAILED,
                    extra_audit={"error": str(exc)},
                )
            log.warning(
                "extract_failed",
                invoice_id=str(invoice_id),
                is_last_attempt=is_last_attempt,
                error=str(exc),
            )
            raise

        _persist_extraction(conn, invoice_id, tenant_id, result)
        _transition(conn, invoice_id, tenant_id, InvoiceStatus.EXTRACTING, InvoiceStatus.EXTRACTED)

        match_idempotency_key = hashlib.sha256(
            f"{tenant_id}:{invoice_id}:match".encode()
        ).hexdigest()
        queue.enqueue(
            JobType.MATCH,
            {"invoice_id": str(invoice_id)},
            tenant_id,
            match_idempotency_key,
        )

    return handle_extract
