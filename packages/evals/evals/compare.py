"""Runs two backends over the identical sample and reports where they
differ: per-field F1 delta, latency delta, cost delta, and agreement rate
(how often the two backends produce the same normalized value, independent
of whether either one matches ground truth -- a distinct signal from
metrics.py's accuracy: two backends can agree with each other while both
being wrong, or disagree while one of them is right).

"Identical sample" relies on DatasetLoader.__iter__'s own documented
promise of a stable, deterministic order (evals/datasets/base.py) -- both
runs re-iterate the same loader instance from the top, rather than sharing
one iterator between them.
"""

from dataclasses import dataclass
from pathlib import Path

from extractors.base import ExtractionResult

from evals.datasets.base import DatasetLoader
from evals.metrics import (
    HEADER_FIELDS,
    LINE_FIELDS,
    EvalMetrics,
    _normalize_header_value,
    _NormalizeFailed,
    compute_metrics,
)
from evals.runner import RunResult, run


@dataclass(frozen=True, slots=True)
class FieldComparison:
    field_path: str
    f1_a: float | None
    f1_b: float | None
    f1_delta: float | None  # backend_b - backend_a
    agreement_rate: float | None
    n_compared: int


@dataclass(frozen=True, slots=True)
class CompareResult:
    dataset: str
    backend_a: str
    backend_b: str
    n_documents_a: int
    n_documents_b: int
    n_common: int
    fields: dict[str, FieldComparison]
    latency_delta_ms: float | None  # backend_b - backend_a
    cost_delta_usd: float
    metrics_a: EvalMetrics
    metrics_b: EvalMetrics
    run_a: RunResult
    run_b: RunResult


def _header_agreement(name: str, value_a: str | None, value_b: str | None) -> bool | None:
    a_present = bool(value_a and value_a.strip())
    b_present = bool(value_b and value_b.strip())
    if not a_present and not b_present:
        return None  # neither backend extracted this field -- not comparable
    if a_present != b_present:
        return False
    assert value_a is not None and value_b is not None
    try:
        return _normalize_header_value(name, value_a) == _normalize_header_value(name, value_b)
    except _NormalizeFailed:
        return value_a.strip().lower() == value_b.strip().lower()


def compare(
    dataset_name: str,
    loader: DatasetLoader,
    backend_a_name: str,
    backend_b_name: str,
    n: int,
    concurrency: int = 4,
    checkpoint_dir: Path | None = None,
) -> CompareResult:
    run_a = run(
        dataset_name,
        loader,
        backend_a_name,
        n,
        concurrency=concurrency,
        checkpoint_path=(checkpoint_dir / f"{backend_a_name}.jsonl") if checkpoint_dir else None,
    )
    run_b = run(
        dataset_name,
        loader,
        backend_b_name,
        n,
        concurrency=concurrency,
        checkpoint_path=(checkpoint_dir / f"{backend_b_name}.jsonl") if checkpoint_dir else None,
    )

    metrics_a = compute_metrics(dataset_name, backend_a_name, run_a.pairs)
    metrics_b = compute_metrics(dataset_name, backend_b_name, run_b.pairs)

    results_a: dict[str, ExtractionResult] = {gt.doc_id: pred for gt, pred in run_a.pairs}
    results_b: dict[str, ExtractionResult] = {gt.doc_id: pred for gt, pred in run_b.pairs}
    common_doc_ids = set(results_a) & set(results_b)

    fields: dict[str, FieldComparison] = {}
    for name in HEADER_FIELDS:
        path = f"header.{name}"
        agreements: list[bool] = []
        for doc_id in common_doc_ids:
            value_a = getattr(results_a[doc_id].header, name)
            value_b = getattr(results_b[doc_id].header, name)
            result = _header_agreement(name, value_a, value_b)
            if result is not None:
                agreements.append(result)
        fields[path] = _build_comparison(path, metrics_a, metrics_b, agreements)

    for name in LINE_FIELDS:
        path = f"lines[].{name}"
        # Line-level agreement would need the same cross-backend matching
        # match_line_items() does against ground truth, just between two
        # predictions instead -- out of scope for this comparison; header
        # fields carry the agreement-rate signal, line fields still get
        # their F1 delta from each backend's own metrics against ground
        # truth.
        fields[path] = _build_comparison(path, metrics_a, metrics_b, [])

    latency_delta = (
        metrics_b.mean_latency_ms - metrics_a.mean_latency_ms
        if metrics_a.mean_latency_ms is not None and metrics_b.mean_latency_ms is not None
        else None
    )
    cost_delta = metrics_b.total_estimated_cost_usd - metrics_a.total_estimated_cost_usd

    return CompareResult(
        dataset=dataset_name,
        backend_a=backend_a_name,
        backend_b=backend_b_name,
        n_documents_a=len(run_a.pairs),
        n_documents_b=len(run_b.pairs),
        n_common=len(common_doc_ids),
        fields=fields,
        latency_delta_ms=latency_delta,
        cost_delta_usd=cost_delta,
        metrics_a=metrics_a,
        metrics_b=metrics_b,
        run_a=run_a,
        run_b=run_b,
    )


def _build_comparison(
    path: str, metrics_a: EvalMetrics, metrics_b: EvalMetrics, agreements: list[bool]
) -> FieldComparison:
    f1_a = metrics_a.fields[path].f1
    f1_b = metrics_b.fields[path].f1
    return FieldComparison(
        field_path=path,
        f1_a=f1_a,
        f1_b=f1_b,
        f1_delta=(f1_b - f1_a) if f1_a is not None and f1_b is not None else None,
        agreement_rate=(sum(agreements) / len(agreements)) if agreements else None,
        n_compared=len(agreements),
    )
