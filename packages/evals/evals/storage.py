"""Persists a run's metrics to eval_runs/eval_results/eval_run_calibration
(db/migrations/0009_evals.sql, extended by 0027_eval_metrics_extensions.sql
and 0028_benchmarks_read_access.sql) and, when a Storage backend is given,
one eval_run_documents row per document -- the failure gallery's data
source (apps/api/api/benchmarks_view.py).

Connects with a plain DATABASE_URL, not app_role: eval_runs/eval_results are
deliberately excluded from app_role's grants (0013_app_role.sql's own
comment -- "not tenant-scoped"), the same reason db/seed/seed.py uses a
direct service-role connection rather than going through RLS. app_role gets
a narrow read-only grant instead (0028), for apps/api's own connection.
"""

import mimetypes
import uuid
from dataclasses import asdict
from typing import Any

import psycopg
import structlog
from core.errors import StorageError
from psycopg.types.json import Jsonb
from storage.base import Storage

from evals.metrics import EvalMetrics, count_header_mismatches
from evals.models import DatasetExample, GroundTruthDocument
from evals.runner import RunResult

log = structlog.get_logger()


def persist_run(
    conn: psycopg.Connection[Any],
    run_result: RunResult,
    metrics: EvalMetrics,
    storage: Storage | None = None,
) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into eval_runs
                (dataset, backend, model_version, sample_count, started_at, finished_at,
                 mean_latency_ms, latency_p50_ms, latency_p95_ms, latency_p99_ms,
                 total_estimated_cost_usd,
                 line_item_precision, line_item_recall, line_item_f1,
                 line_item_n_ground_truth, line_item_n_predicted, line_item_n_matched)
            values (%(dataset)s, %(backend)s, %(model_version)s, %(sample_count)s,
                    %(started_at)s, %(finished_at)s, %(mean_latency_ms)s,
                    %(p50)s, %(p95)s, %(p99)s, %(total_cost)s,
                    %(li_precision)s, %(li_recall)s, %(li_f1)s,
                    %(li_n_gt)s, %(li_n_pred)s, %(li_n_matched)s)
            returning id
            """,
            {
                "dataset": run_result.dataset,
                "backend": run_result.backend,
                "model_version": run_result.model_version,
                "sample_count": run_result.sample_count,
                "started_at": run_result.started_at,
                "finished_at": run_result.finished_at,
                "mean_latency_ms": metrics.mean_latency_ms,
                "p50": metrics.latency_p50_ms,
                "p95": metrics.latency_p95_ms,
                "p99": metrics.latency_p99_ms,
                "total_cost": metrics.total_estimated_cost_usd,
                "li_precision": metrics.line_items.precision,
                "li_recall": metrics.line_items.recall,
                "li_f1": metrics.line_items.f1,
                "li_n_gt": metrics.line_items.n_ground_truth_lines,
                "li_n_pred": metrics.line_items.n_predicted_lines,
                "li_n_matched": metrics.line_items.n_matched,
            },
        )
        row = cur.fetchone()
        assert row is not None
        eval_run_id: uuid.UUID = row[0]

        for field_metrics in metrics.fields.values():
            cur.execute(
                """
                insert into eval_results
                    (eval_run_id, field_path, n, precision, recall, f1, exact_match_rate,
                     mean_confidence, mean_absolute_error, within_tolerance_rate)
                values (%(eval_run_id)s, %(field_path)s, %(n)s, %(precision)s, %(recall)s,
                        %(f1)s, %(exact_match_rate)s, %(mean_confidence)s, %(mae)s,
                        %(within_tolerance)s)
                """,
                {
                    "eval_run_id": eval_run_id,
                    "field_path": field_metrics.field_path,
                    "n": field_metrics.n,
                    "precision": field_metrics.precision,
                    "recall": field_metrics.recall,
                    "f1": field_metrics.f1,
                    "exact_match_rate": field_metrics.exact_match_rate,
                    "mean_confidence": field_metrics.mean_confidence,
                    "mae": field_metrics.mean_absolute_error,
                    "within_tolerance": field_metrics.within_tolerance_rate,
                },
            )

        for bucket in metrics.calibration:
            if bucket.n == 0:
                continue
            cur.execute(
                """
                insert into eval_run_calibration
                    (eval_run_id, bucket_low, bucket_high, n, mean_confidence, actual_accuracy)
                values
                    (%(eval_run_id)s, %(low)s, %(high)s, %(n)s, %(mean_confidence)s, %(accuracy)s)
                """,
                {
                    "eval_run_id": eval_run_id,
                    "low": bucket.low,
                    "high": bucket.high,
                    "n": bucket.n,
                    "mean_confidence": bucket.mean_confidence,
                    "accuracy": bucket.actual_accuracy,
                },
            )

        for gt, prediction in run_result.pairs:
            example = run_result.documents.get(gt.doc_id)
            thumbnail_path = None
            mime_type = example.mime_type if example else None
            if storage is not None and example is not None:
                thumbnail_path = _upload_thumbnail(storage, eval_run_id, example)

            cur.execute(
                """
                insert into eval_run_documents
                    (eval_run_id, doc_id, ground_truth, extraction_result, mismatch_count,
                     thumbnail_path, mime_type)
                values (%(eval_run_id)s, %(doc_id)s, %(ground_truth)s, %(extraction_result)s,
                        %(mismatch_count)s, %(thumbnail_path)s, %(mime_type)s)
                """,
                {
                    "eval_run_id": eval_run_id,
                    "doc_id": gt.doc_id,
                    "ground_truth": Jsonb(_ground_truth_json(gt)),
                    "extraction_result": Jsonb(prediction.model_dump(mode="json")),
                    "mismatch_count": count_header_mismatches(gt, prediction),
                    "thumbnail_path": thumbnail_path,
                    "mime_type": mime_type,
                },
            )

    conn.commit()
    return eval_run_id


def _ground_truth_json(gt: GroundTruthDocument) -> dict[str, Any]:
    return asdict(gt)


def _upload_thumbnail(
    storage: Storage, eval_run_id: uuid.UUID, example: DatasetExample
) -> str | None:
    doc_id = example.doc_id
    mime_type = example.mime_type
    document_bytes = example.document_bytes
    extension = mimetypes.guess_extension(mime_type) or ""
    path = f"evals/{eval_run_id}/{doc_id}{extension}"
    try:
        storage.upload(path, document_bytes, mime_type)
    except StorageError as exc:
        # A thumbnail failing to upload shouldn't sink the whole run's
        # metrics -- the failure gallery just shows no image for this one
        # document (apps/api/api/benchmarks_view.py handles a null
        # thumbnail_path already).
        log.warning("eval_thumbnail_upload_failed", doc_id=doc_id, error=str(exc))
        return None
    return path
