"""Runs one named extractor across N documents from a dataset.

- Bounded concurrency via a thread pool (Extractor.extract is a blocking
  call -- network I/O for Gemini, subprocess/CPU-bound OCR for Tesseract --
  so threads, not asyncio, match the interface).
- Rate limiting is the extractor's own job: GeminiExtractor already carries
  a TokenBucket reading GEMINI_RATE_LIMIT_RPM (extractors/gemini.py). This
  runner shares a single Extractor instance across every worker thread
  specifically so that limiter is shared too -- one bucket per thread would
  let N threads each burst the full RPM independently, silently exceeding
  the real quota. (extractors.ratelimit.TokenBucket was made thread-safe
  for exactly this reason.) Tesseract/mock have no such limiter and don't
  need one; bounded concurrency alone still caps how many run at once.
- Checkpointing (evals.checkpoint) means a run killed partway through
  resumes rather than restarting: already-completed documents are read
  back from disk and skipped; failed ones are retried.
"""

import itertools
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import structlog
from extractors.base import ExtractionResult, Extractor
from extractors.factory import get_extractor

from evals.checkpoint import CheckpointEntry, append_entry, load_entries
from evals.datasets.base import DatasetLoader
from evals.metrics import estimated_cost_usd
from evals.models import DatasetExample, GroundTruthDocument

log = structlog.get_logger()

DEFAULT_CONCURRENCY = 4


@dataclass(frozen=True, slots=True)
class RunFailure:
    doc_id: str
    error: str


@dataclass(frozen=True, slots=True)
class RunResult:
    dataset: str
    backend: str
    model_version: str | None
    sample_count: int
    started_at: datetime
    finished_at: datetime
    pairs: list[tuple[GroundTruthDocument, ExtractionResult]] = field(default_factory=list)
    failures: list[RunFailure] = field(default_factory=list)
    # Keyed by doc_id, for the one caller (evals/storage.py's failure-gallery
    # thumbnail upload) that needs the original bytes a pair's ground truth
    # doesn't carry. Not persisted itself -- checkpoint.py already
    # deliberately doesn't durably store raw document bytes (see its own
    # module docstring), so this only exists for the lifetime of one process.
    documents: dict[str, DatasetExample] = field(default_factory=dict)


def _extract_one(extractor: Extractor, example: DatasetExample) -> ExtractionResult:
    return extractor.extract(example.document_bytes, example.mime_type)


def run(
    dataset_name: str,
    loader: DatasetLoader,
    backend_name: str,
    n: int,
    concurrency: int = DEFAULT_CONCURRENCY,
    checkpoint_path: Path | None = None,
    extractor: Extractor | None = None,
) -> RunResult:
    """`extractor`, if given, is used instead of resolving `backend_name`
    via extractors.factory.get_extractor -- lets tests inject a controlled
    stub (raise for one doc_id, succeed for another) without needing a real
    Gemini/Tesseract backend. Production callers (the CLI) always omit it."""
    started_at = datetime.now(UTC)
    examples = list(itertools.islice(loader, n))
    if not examples:
        log.warning("eval_run_no_examples", dataset=dataset_name, backend=backend_name, n=n)

    checkpointed = (
        load_entries(checkpoint_path, dataset_name, backend_name) if checkpoint_path else {}
    )
    already_done = {doc_id: e for doc_id, e in checkpointed.items() if e.error is None}
    log.info(
        "eval_run_starting",
        dataset=dataset_name,
        backend=backend_name,
        requested=n,
        found=len(examples),
        resumed_from_checkpoint=len(already_done),
    )

    extractor = extractor if extractor is not None else get_extractor(backend_name)
    results_by_doc_id: dict[str, ExtractionResult] = {
        doc_id: ExtractionResult.model_validate(entry.extraction_result)
        for doc_id, entry in already_done.items()
        if entry.extraction_result is not None
    }
    failures: list[RunFailure] = [
        RunFailure(doc_id=doc_id, error=entry.error)
        for doc_id, entry in checkpointed.items()
        if entry.error is not None and doc_id not in already_done
    ]

    pending = [ex for ex in examples if ex.doc_id not in results_by_doc_id]

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        future_to_example = {
            executor.submit(_extract_one, extractor, example): example for example in pending
        }
        for future in as_completed(future_to_example):
            example = future_to_example[future]
            attempt_start = time.monotonic()
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 -- any extractor failure is recorded, not fatal to the run
                error_message = str(exc)
                failures = [f for f in failures if f.doc_id != example.doc_id]
                failures.append(RunFailure(doc_id=example.doc_id, error=error_message))
                log.warning(
                    "eval_document_failed",
                    dataset=dataset_name,
                    backend=backend_name,
                    doc_id=example.doc_id,
                    error=error_message,
                )
                if checkpoint_path:
                    append_entry(
                        checkpoint_path,
                        CheckpointEntry(
                            doc_id=example.doc_id,
                            dataset=dataset_name,
                            backend=backend_name,
                            extraction_result=None,
                            error=error_message,
                            latency_ms=int((time.monotonic() - attempt_start) * 1000),
                            estimated_cost_usd=0.0,
                        ),
                    )
                continue

            results_by_doc_id[example.doc_id] = result
            # A retry that succeeds must clear any stale failure this
            # doc_id carried from an earlier interrupted run's checkpoint
            # -- otherwise a document that failed once and later succeeds
            # is reported as both a pair AND a failure.
            failures = [f for f in failures if f.doc_id != example.doc_id]
            if checkpoint_path:
                append_entry(
                    checkpoint_path,
                    CheckpointEntry(
                        doc_id=example.doc_id,
                        dataset=dataset_name,
                        backend=backend_name,
                        extraction_result=result.model_dump(mode="json"),
                        error=None,
                        latency_ms=result.latency_ms,
                        estimated_cost_usd=estimated_cost_usd(result),
                    ),
                )

    pairs: list[tuple[GroundTruthDocument, ExtractionResult]] = []
    for example in examples:
        matched_result = results_by_doc_id.get(example.doc_id)
        if matched_result is not None:
            pairs.append((example.ground_truth, matched_result))

    finished_at = datetime.now(UTC)
    log.info(
        "eval_run_finished",
        dataset=dataset_name,
        backend=backend_name,
        succeeded=len(pairs),
        failed=len(failures),
        duration_s=(finished_at - started_at).total_seconds(),
    )

    model_version = pairs[0][1].model_version if pairs else None
    return RunResult(
        dataset=dataset_name,
        backend=backend_name,
        model_version=model_version,
        sample_count=len(examples),
        started_at=started_at,
        finished_at=finished_at,
        pairs=pairs,
        failures=failures,
        documents={example.doc_id: example for example in examples},
    )
