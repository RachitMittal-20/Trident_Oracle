"""Read model for /v1/benchmarks/* -- packages/evals' eval_runs/eval_results/
eval_run_calibration/eval_run_documents (db/migrations/0009_evals.sql,
0027_eval_metrics_extensions.sql, 0028_benchmarks_read_access.sql).

Not tenant-scoped (0009's own comment) -- no tenant_id parameter, no
set_tenant() call, unlike every other *_view module in this package. The
connection is still plain app_role (apps/api/api/config.py::get_connection);
0028 granted it SELECT-only on these four tables specifically so this
module never needs its own connection path.
"""

import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _decimal_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def list_runs(conn: psycopg.Connection[Any], *, limit: int = 100) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select id, dataset, backend, model_version, sample_count,
                   started_at, finished_at
            from eval_runs
            order by started_at desc
            limit %(limit)s
            """,
            {"limit": limit},
        )
        rows = cur.fetchall()
    return [
        {
            "id": str(row["id"]),
            "dataset": row["dataset"],
            "backend": row["backend"],
            "model_version": row["model_version"],
            "sample_count": row["sample_count"],
            "started_at": row["started_at"].isoformat(),
            "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        }
        for row in rows
    ]


def get_run(conn: psycopg.Connection[Any], run_id: uuid.UUID) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("select * from eval_runs where id = %s", (run_id,))
        run_row = cur.fetchone()
        if run_row is None:
            return None

        cur.execute(
            """
            select field_path, n, precision, recall, f1, exact_match_rate,
                   mean_confidence, mean_absolute_error, within_tolerance_rate
            from eval_results
            where eval_run_id = %s
            order by field_path
            """,
            (run_id,),
        )
        field_rows = cur.fetchall()

        cur.execute(
            """
            select bucket_low, bucket_high, n, mean_confidence, actual_accuracy
            from eval_run_calibration
            where eval_run_id = %s
            order by bucket_low
            """,
            (run_id,),
        )
        calibration_rows = cur.fetchall()

    weighted_sum = 0.0
    weight_total = 0
    for row in field_rows:
        if row["exact_match_rate"] is not None and row["n"]:
            weighted_sum += float(row["exact_match_rate"]) * row["n"]
            weight_total += row["n"]
    overall_exact_match_rate = (weighted_sum / weight_total) if weight_total else None

    sample_count = run_row["sample_count"]
    total_cost = run_row["total_estimated_cost_usd"]
    cost_per_1000 = (
        float(total_cost) / sample_count * 1000
        if total_cost is not None and sample_count
        else None
    )

    return {
        "id": str(run_row["id"]),
        "dataset": run_row["dataset"],
        "backend": run_row["backend"],
        "model_version": run_row["model_version"],
        "sample_count": sample_count,
        "started_at": run_row["started_at"].isoformat(),
        "finished_at": run_row["finished_at"].isoformat() if run_row["finished_at"] else None,
        "overall_exact_match_rate": overall_exact_match_rate,
        "mean_latency_ms": _decimal_str(run_row["mean_latency_ms"]),
        "latency_p50_ms": _decimal_str(run_row["latency_p50_ms"]),
        "latency_p95_ms": _decimal_str(run_row["latency_p95_ms"]),
        "latency_p99_ms": _decimal_str(run_row["latency_p99_ms"]),
        "total_estimated_cost_usd": _decimal_str(run_row["total_estimated_cost_usd"]),
        "cost_per_1000_usd": cost_per_1000,
        "line_item_precision": _decimal_str(run_row["line_item_precision"]),
        "line_item_recall": _decimal_str(run_row["line_item_recall"]),
        "line_item_f1": _decimal_str(run_row["line_item_f1"]),
        "fields": [
            {
                "field_path": row["field_path"],
                "n": row["n"],
                "precision": _decimal_str(row["precision"]),
                "recall": _decimal_str(row["recall"]),
                "f1": _decimal_str(row["f1"]),
                "exact_match_rate": _decimal_str(row["exact_match_rate"]),
                "mean_confidence": _decimal_str(row["mean_confidence"]),
                "mean_absolute_error": _decimal_str(row["mean_absolute_error"]),
                "within_tolerance_rate": _decimal_str(row["within_tolerance_rate"]),
            }
            for row in field_rows
        ],
        "calibration": [
            {
                "bucket_low": float(row["bucket_low"]),
                "bucket_high": float(row["bucket_high"]),
                "n": row["n"],
                "mean_confidence": _decimal_str(row["mean_confidence"]),
                "actual_accuracy": _decimal_str(row["actual_accuracy"]),
            }
            for row in calibration_rows
        ],
    }


def get_failures(
    conn: psycopg.Connection[Any], run_id: uuid.UUID, limit: int = 12
) -> list[dict[str, Any]]:
    """Ordered worst-first (highest mismatch_count) -- the failure
    gallery's entire point (CLAUDE.md prompt: "make this section prominent,
    not hidden") is showing the documents the backend got most wrong, not a
    random or dataset-order sample."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            select doc_id, ground_truth, extraction_result, mismatch_count,
                   thumbnail_path, mime_type
            from eval_run_documents
            where eval_run_id = %s
            order by mismatch_count desc, doc_id
            limit %s
            """,
            (run_id, limit),
        )
        rows = cur.fetchall()
    return [
        {
            "doc_id": row["doc_id"],
            "ground_truth": row["ground_truth"],
            "extraction_result": row["extraction_result"],
            "mismatch_count": row["mismatch_count"],
            "thumbnail_path": row["thumbnail_path"],
            "mime_type": row["mime_type"],
        }
        for row in rows
    ]
