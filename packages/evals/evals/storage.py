"""Persists a run's metrics to eval_runs/eval_results/eval_run_calibration
(db/migrations/0009_evals.sql, extended by 0027_eval_metrics_extensions.sql).

Connects with a plain DATABASE_URL, not app_role: eval_runs/eval_results are
deliberately excluded from app_role's grants (0013_app_role.sql's own
comment -- "not tenant-scoped"), the same reason db/seed/seed.py uses a
direct service-role connection rather than going through RLS.
"""

import uuid
from typing import Any

import psycopg

from evals.metrics import EvalMetrics
from evals.runner import RunResult


def persist_run(
    conn: psycopg.Connection[Any], run_result: RunResult, metrics: EvalMetrics
) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into eval_runs
                (dataset, backend, model_version, sample_count, started_at, finished_at,
                 mean_latency_ms, total_estimated_cost_usd,
                 line_item_precision, line_item_recall, line_item_f1,
                 line_item_n_ground_truth, line_item_n_predicted, line_item_n_matched)
            values (%(dataset)s, %(backend)s, %(model_version)s, %(sample_count)s,
                    %(started_at)s, %(finished_at)s, %(mean_latency_ms)s, %(total_cost)s,
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
                    (eval_run_id, field_path, precision, recall, f1, exact_match_rate,
                     mean_confidence, mean_absolute_error, within_tolerance_rate)
                values (%(eval_run_id)s, %(field_path)s, %(precision)s, %(recall)s, %(f1)s,
                        %(exact_match_rate)s, %(mean_confidence)s, %(mae)s, %(within_tolerance)s)
                """,
                {
                    "eval_run_id": eval_run_id,
                    "field_path": field_metrics.field_path,
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

    conn.commit()
    return eval_run_id
