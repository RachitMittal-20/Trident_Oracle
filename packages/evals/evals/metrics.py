"""Per-field, calibration, and line-item metrics -- pure functions over
already-collected (ExtractionResult, GroundTruthDocument) pairs, no I/O.

"Exact match" always means "matches after normalization", using the exact
same normalization functions production code uses (extractors.normalize) --
a benchmark that normalizes differently than the pipeline it's benchmarking
would be measuring the wrong thing.

Calibration is deliberately the least skippable metric here (CLAUDE.md
prompt: "Make sure that point is visible in the output"): the entire
decision matrix (packages/core's auto-post/needs-verification/exceptions
split) trusts a reported confidence number directly against
tolerance_policies.min_field_confidence. If a backend reports 0.9 and is
actually right 60% of the time at that confidence, every threshold tuned
against its confidence score is wrong, silently.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import partial

from core.errors import ExtractionError
from extractors.base import ExtractionResult
from extractors.normalize import (
    normalize_description,
    normalize_vendor_name,
    parse_currency,
    parse_date,
)

from evals.models import GroundTruthDocument, GroundTruthLineItem

HEADER_FIELDS = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "vendor_name",
    "currency",
    "subtotal",
    "tax",
    "total",
)
LINE_FIELDS = ("description", "qty", "unit_price", "line_total")

# Fields compared as Decimal (numeric MAE/tolerance metrics apply).
_NUMERIC_HEADER_FIELDS = frozenset({"subtotal", "tax", "total"})
_NUMERIC_LINE_FIELDS = frozenset({"qty", "unit_price", "line_total"})

_CALIBRATION_BUCKETS = 10
_DEFAULT_NUMERIC_TOLERANCE = Decimal("0.01")


class _NormalizeFailed(Exception):
    """Internal sentinel: a value existed but didn't parse. Distinct from
    "value was absent" -- both count as a mismatch, but a parse failure is
    worth being able to tell apart when debugging a bad eval run."""


def _normalize_header_value(name: str, raw: str) -> object:
    try:
        if name in ("invoice_date", "due_date"):
            return parse_date(raw)
        if name in _NUMERIC_HEADER_FIELDS:
            return parse_currency(raw)
        if name == "vendor_name":
            return normalize_vendor_name(raw)
        return raw.strip().lower()
    except ExtractionError as exc:
        raise _NormalizeFailed(str(exc)) from exc


def _normalize_line_value(name: str, raw: str) -> object:
    try:
        if name == "description":
            return normalize_description(raw)
        if name in _NUMERIC_LINE_FIELDS:
            return parse_currency(raw)
        return raw.strip().lower()
    except ExtractionError as exc:
        raise _NormalizeFailed(str(exc)) from exc


@dataclass
class _FieldAccumulator:
    """Running totals for one field path across every document in a run.
    Kept mutable/internal -- FieldMetrics (below) is the frozen, public
    result computed from this once accumulation is done."""

    n_ground_truth_present: int = 0
    n_predicted_present: int = 0
    n_true_positive: int = 0  # both present
    n_exact_match: int = 0  # both present, normalized-equal (subset of TP)
    absolute_errors: list[Decimal] = field(default_factory=list)
    within_tolerance: int = 0
    confidences: list[float] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FieldMetrics:
    field_path: str
    n: int  # documents where ground truth has this field
    precision: float | None
    recall: float | None
    f1: float | None
    exact_match_rate: float | None
    mean_absolute_error: float | None
    within_tolerance_rate: float | None
    mean_confidence: float | None


@dataclass(frozen=True, slots=True)
class CalibrationBucket:
    low: float
    high: float
    n: int
    mean_confidence: float | None
    actual_accuracy: float | None

    @property
    def gap(self) -> float | None:
        """Positive means overconfident (reported confidence exceeds actual
        accuracy) -- the failure mode that matters most here, since it's
        the one that makes packages/core auto-post something it shouldn't."""
        if self.mean_confidence is None or self.actual_accuracy is None:
            return None
        return self.mean_confidence - self.actual_accuracy


@dataclass(frozen=True, slots=True)
class LineItemMetrics:
    precision: float | None
    recall: float | None
    f1: float | None
    n_ground_truth_lines: int
    n_predicted_lines: int
    n_matched: int


@dataclass(frozen=True, slots=True)
class EvalMetrics:
    dataset: str
    backend: str
    n_documents: int
    fields: dict[str, FieldMetrics]
    calibration: tuple[CalibrationBucket, ...]
    line_items: LineItemMetrics
    mean_latency_ms: float | None
    total_estimated_cost_usd: float


def _safe_div(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def match_line_items(
    ground_truth: tuple[GroundTruthLineItem, ...], predicted: tuple[object, ...]
) -> list[tuple[int, int]]:
    """Greedy one-to-one matching by normalized description equality.
    Returns (ground_truth_index, predicted_index) pairs. A simplification
    documented up front: two ground-truth lines with identical descriptions
    (e.g. the same SKU ordered twice) can only ever match one predicted
    line each in whichever order they're iterated -- acceptable for a
    benchmark harness, not for the production matching engine, which uses
    the real fuzzy/SKU/LLM cascade (packages/core's actual job)."""
    predicted_desc: dict[str, list[int]] = defaultdict(list)
    for i, line in enumerate(predicted):
        desc = getattr(line, "description", None)
        if not desc:
            continue
        try:
            key = normalize_description(desc)
        except ExtractionError:
            continue
        predicted_desc[key].append(i)

    matches: list[tuple[int, int]] = []
    used_predicted: set[int] = set()
    for gt_index, gt_line in enumerate(ground_truth):
        if not gt_line.description:
            continue
        try:
            key = normalize_description(gt_line.description)
        except ExtractionError:
            continue
        for candidate in predicted_desc.get(key, []):
            if candidate not in used_predicted:
                matches.append((gt_index, candidate))
                used_predicted.add(candidate)
                break
    return matches


def compute_metrics(
    dataset: str,
    backend: str,
    pairs: list[tuple[GroundTruthDocument, ExtractionResult]],
    numeric_tolerance: Decimal = _DEFAULT_NUMERIC_TOLERANCE,
) -> EvalMetrics:
    """`pairs` is (ground truth, prediction) for every document in the run --
    a run's full result set, not a single document, since every metric here
    (precision/recall, calibration buckets) is only meaningful in aggregate.
    """
    accumulators: dict[str, _FieldAccumulator] = {
        f"header.{name}": _FieldAccumulator() for name in HEADER_FIELDS
    }
    for name in LINE_FIELDS:
        accumulators[f"lines[].{name}"] = _FieldAccumulator()

    calibration_pairs: list[tuple[float, bool]] = []  # (confidence, was_correct)
    latencies: list[int] = []
    total_cost = 0.0

    line_gt_total = 0
    line_pred_total = 0
    line_matched_total = 0

    for gt, prediction in pairs:
        latencies.append(prediction.latency_ms)
        total_cost += estimated_cost_usd(prediction)

        for name in HEADER_FIELDS:
            path = f"header.{name}"
            gt_raw = getattr(gt.header, name)
            pred_raw = getattr(prediction.header, name)
            confidence = prediction.confidence.get(path)
            _accumulate_field(
                accumulators[path],
                gt_raw,
                pred_raw,
                confidence,
                name in _NUMERIC_HEADER_FIELDS,
                numeric_tolerance,
                partial(_normalize_header_value, name),
                calibration_pairs,
            )

        matches = match_line_items(gt.line_items, prediction.line_items)
        line_gt_total += len(gt.line_items)
        line_pred_total += len(prediction.line_items)
        line_matched_total += len(matches)

        for gt_index, pred_index in matches:
            gt_line = gt.line_items[gt_index]
            pred_line = prediction.line_items[pred_index]
            for name in LINE_FIELDS:
                path = f"lines[].{name}"
                confidence = prediction.confidence.get(f"lines[{pred_index}].{name}")
                _accumulate_field(
                    accumulators[path],
                    getattr(gt_line, name),
                    getattr(pred_line, name),
                    confidence,
                    name in _NUMERIC_LINE_FIELDS,
                    numeric_tolerance,
                    partial(_normalize_line_value, name),
                    calibration_pairs,
                )

    fields = {path: _finalize_field(path, acc) for path, acc in accumulators.items()}

    line_precision = _safe_div(line_matched_total, line_pred_total)
    line_recall = _safe_div(line_matched_total, line_gt_total)
    line_items_metrics = LineItemMetrics(
        precision=line_precision,
        recall=line_recall,
        f1=_f1(line_precision, line_recall),
        n_ground_truth_lines=line_gt_total,
        n_predicted_lines=line_pred_total,
        n_matched=line_matched_total,
    )

    return EvalMetrics(
        dataset=dataset,
        backend=backend,
        n_documents=len(pairs),
        fields=fields,
        calibration=_bucket_calibration(calibration_pairs),
        line_items=line_items_metrics,
        mean_latency_ms=(sum(latencies) / len(latencies)) if latencies else None,
        total_estimated_cost_usd=total_cost,
    )


def _accumulate_field(
    acc: _FieldAccumulator,
    gt_raw: str | None,
    pred_raw: str | None,
    confidence: float | None,
    numeric: bool,
    tolerance: Decimal,
    normalize: Callable[[str], object],
    calibration_pairs: list[tuple[float, bool]],
) -> None:
    gt_present = bool(gt_raw and gt_raw.strip())
    pred_present = bool(pred_raw and pred_raw.strip())
    if gt_present:
        acc.n_ground_truth_present += 1
    if pred_present:
        acc.n_predicted_present += 1
    if not (gt_present and pred_present):
        return

    acc.n_true_positive += 1
    if confidence is not None:
        acc.confidences.append(confidence)

    assert gt_raw is not None and pred_raw is not None  # gt_present/pred_present guarantee this
    try:
        gt_norm = normalize(gt_raw)
        pred_norm = normalize(pred_raw)
    except _NormalizeFailed:
        if confidence is not None:
            calibration_pairs.append((confidence, False))
        return

    is_match = gt_norm == pred_norm
    if is_match:
        acc.n_exact_match += 1
    if confidence is not None:
        calibration_pairs.append((confidence, is_match))

    if numeric and isinstance(gt_norm, Decimal) and isinstance(pred_norm, Decimal):
        try:
            diff = abs(gt_norm - pred_norm)
        except InvalidOperation:
            return
        acc.absolute_errors.append(diff)
        if diff <= tolerance:
            acc.within_tolerance += 1


def _finalize_field(field_path: str, acc: _FieldAccumulator) -> FieldMetrics:
    precision = _safe_div(acc.n_true_positive, acc.n_predicted_present)
    recall = _safe_div(acc.n_true_positive, acc.n_ground_truth_present)
    return FieldMetrics(
        field_path=field_path,
        n=acc.n_ground_truth_present,
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
        exact_match_rate=_safe_div(acc.n_exact_match, acc.n_ground_truth_present),
        mean_absolute_error=(
            float(sum(acc.absolute_errors) / len(acc.absolute_errors))
            if acc.absolute_errors
            else None
        ),
        within_tolerance_rate=_safe_div(acc.within_tolerance, len(acc.absolute_errors)),
        mean_confidence=(sum(acc.confidences) / len(acc.confidences)) if acc.confidences else None,
    )


def _bucket_calibration(pairs: list[tuple[float, bool]]) -> tuple[CalibrationBucket, ...]:
    buckets: list[CalibrationBucket] = []
    for i in range(_CALIBRATION_BUCKETS):
        low, high = i / _CALIBRATION_BUCKETS, (i + 1) / _CALIBRATION_BUCKETS
        # The top bucket includes confidence == 1.0 on its upper edge.
        in_bucket = [
            (conf, correct)
            for conf, correct in pairs
            if (low <= conf < high) or (i == _CALIBRATION_BUCKETS - 1 and conf == 1.0)
        ]
        if in_bucket:
            mean_confidence = sum(c for c, _ in in_bucket) / len(in_bucket)
            actual_accuracy = sum(1 for _, correct in in_bucket if correct) / len(in_bucket)
        else:
            mean_confidence = None
            actual_accuracy = None
        buckets.append(
            CalibrationBucket(
                low=low, high=high, n=len(in_bucket),
                mean_confidence=mean_confidence, actual_accuracy=actual_accuracy,
            )
        )
    return tuple(buckets)


# Gemini Flash pricing as of the model pinned in extractors.gemini
# (GEMINI_MODEL default) -- per CLAUDE.md's own note that the pinned model
# rotates on a free-tier schedule, this constant will go stale and is
# clearly labeled as an estimate, never billed fact. $0.075 / 1M input
# tokens, $0.30 / 1M output tokens (published Gemini Flash rate at time of
# writing); since ExtractionResult only reports one combined
# estimated_tokens figure, this uses the input rate for all of it -- an
# undercount for the (typically much smaller) output share, biasing the
# estimate down, not up.
_GEMINI_USD_PER_TOKEN = 0.075 / 1_000_000
_COST_PER_TOKEN: dict[str, float] = {
    "gemini": _GEMINI_USD_PER_TOKEN,
    "tesseract": 0.0,
    "mock": 0.0,
}


def estimated_cost_usd(result: ExtractionResult) -> float:
    """A rough estimate, not an invoice -- see the pricing constant's own
    comment for the specific ways this undercounts. Good enough to compare
    backends' relative cost, not to reconcile against a real bill."""
    rate = _COST_PER_TOKEN.get(result.backend, 0.0)
    return result.estimated_tokens * rate
