from decimal import Decimal

from evals.metrics import compute_metrics, match_line_items
from evals.models import GroundTruthDocument, GroundTruthHeader, GroundTruthLineItem
from extractors.base import ExtractionResult, InvoiceHeader, LineItem


def _prediction(
    *,
    total: str | None = "450.47",
    vendor_name: str | None = "Acme Supply Co.",
    invoice_date: str | None = "2026-08-12",
    confidence: dict[str, float] | None = None,
    line_items: tuple[LineItem, ...] = (),
    line_confidence: dict[str, float] | None = None,
) -> ExtractionResult:
    conf = {
        "header.total": 0.95,
        "header.vendor_name": 0.9,
        "header.invoice_date": 0.9,
        **(confidence or {}),
        **(line_confidence or {}),
    }
    return ExtractionResult(
        header=InvoiceHeader(total=total, vendor_name=vendor_name, invoice_date=invoice_date),
        line_items=line_items,
        confidence=conf,
        backend="mock",
        model_version="test",
        latency_ms=10,
        estimated_tokens=100,
    )


def _ground_truth(
    *,
    total: str | None = "450.47",
    vendor_name: str | None = "Acme Supply Co.",
    invoice_date: str | None = "2026-08-12",
    line_items: tuple[GroundTruthLineItem, ...] = (),
) -> GroundTruthDocument:
    return GroundTruthDocument(
        doc_id="doc-1",
        header=GroundTruthHeader(total=total, vendor_name=vendor_name, invoice_date=invoice_date),
        line_items=line_items,
    )


def test_exact_match_after_normalization() -> None:
    # "$450.47" vs "450.47" and "ACME Supply Co." vs "Acme Supply Co." both
    # normalize equal -- exact match rate should be 1.0, not penalized for
    # superficial formatting differences.
    gt = _ground_truth(total="$450.47", vendor_name="ACME Supply Co.")
    pred = _prediction(total="450.47", vendor_name="Acme Supply Co.")

    metrics = compute_metrics("test", "mock", [(gt, pred)])

    assert metrics.fields["header.total"].exact_match_rate == 1.0
    assert metrics.fields["header.vendor_name"].exact_match_rate == 1.0


def test_mismatched_value_is_not_an_exact_match_but_still_a_true_positive() -> None:
    gt = _ground_truth(total="450.47")
    pred = _prediction(total="999.99")

    metrics = compute_metrics("test", "mock", [(gt, pred)])
    total_metrics = metrics.fields["header.total"]

    assert total_metrics.exact_match_rate == 0.0
    # Both present -> counts toward precision/recall as a true positive
    # (the field WAS extracted), even though the value is wrong.
    assert total_metrics.precision == 1.0
    assert total_metrics.recall == 1.0


def test_missing_ground_truth_field_does_not_count_as_false_positive_target() -> None:
    # Ground truth has no due_date; prediction hallucinates one. This is a
    # real false positive: recall's denominator (ground truth present) is
    # 0 so recall is undefined (None), but precision must reflect the
    # spurious value.
    gt = GroundTruthDocument(doc_id="doc-1", header=GroundTruthHeader(due_date=None))
    pred = ExtractionResult(
        header=InvoiceHeader(due_date="2026-09-01"),
        confidence={"header.due_date": 0.8},
        backend="mock",
        model_version="test",
        latency_ms=5,
        estimated_tokens=10,
    )

    metrics = compute_metrics("test", "mock", [(gt, pred)])
    due_date_metrics = metrics.fields["header.due_date"]

    assert due_date_metrics.n == 0  # no ground truth instances at all
    assert due_date_metrics.recall is None
    assert due_date_metrics.precision == 0.0  # 0 true positives / 1 predicted present


def test_numeric_mean_absolute_error_and_within_tolerance() -> None:
    gt = _ground_truth(total="100.00")
    pred_close = _prediction(total="100.005")  # within default $0.01 tolerance... actually rounds
    pred_far = _prediction(total="105.00")

    metrics = compute_metrics(
        "test", "mock", [(gt, pred_close), (gt, pred_far)], numeric_tolerance=Decimal("0.01")
    )
    total_metrics = metrics.fields["header.total"]

    assert total_metrics.mean_absolute_error == float((Decimal("0.005") + Decimal("5.00")) / 2)
    assert total_metrics.within_tolerance_rate == 0.5  # only the close one


def test_calibration_bucket_flags_overconfidence() -> None:
    # Ten documents, all predictions reported at 0.9 confidence, but only
    # half are actually correct -- a real 50% accuracy at reported 0.9
    # confidence, exactly the overconfidence scenario this metric exists
    # to surface.
    pairs = []
    for i in range(10):
        # vendor_name/invoice_date left absent on both sides so only
        # header.total contributes to the calibration pool below -- the
        # defaults on _ground_truth/_prediction would otherwise add their
        # own always-correct 0.9-confidence entries and dilute the signal.
        gt = _ground_truth(total="100.00", vendor_name=None, invoice_date=None)
        correct = i < 5
        pred = _prediction(
            total="100.00" if correct else "999.00",
            vendor_name=None,
            invoice_date=None,
            confidence={"header.total": 0.9},
        )
        pairs.append((gt, pred))

    metrics = compute_metrics("test", "mock", pairs)
    bucket_90 = next(b for b in metrics.calibration if b.low == 0.9)

    assert bucket_90.n == 10
    assert bucket_90.mean_confidence == 0.9
    assert bucket_90.actual_accuracy == 0.5
    assert bucket_90.gap == 0.4  # overconfident by 0.4


def test_line_item_matching_by_normalized_description() -> None:
    gt_lines = (
        GroundTruthLineItem(
            description="Steel bracket, 4in", qty="40", unit_price="3.25", line_total="130.00"
        ),
        GroundTruthLineItem(
            description="Hydraulic hose, 10ft", qty="12", unit_price="18.75", line_total="225.00"
        ),
    )
    pred_lines = (
        # Reordered and reformatted, but same normalized description.
        LineItem(
            description="hydraulic hose 10ft", qty="12", unit_price="18.75", line_total="225.00"
        ),
        LineItem(description="steel bracket 4in", qty="40", unit_price="3.25", line_total="130.00"),
        LineItem(description="Unrelated extra line", qty="1", unit_price="1.00", line_total="1.00"),
    )

    matches = match_line_items(gt_lines, pred_lines)

    assert len(matches) == 2
    matched_gt_indices = {m[0] for m in matches}
    assert matched_gt_indices == {0, 1}


def test_line_item_precision_recall_reflects_unmatched_lines() -> None:
    gt = _ground_truth(
        line_items=(
            GroundTruthLineItem(
                description="Widget A", qty="1", unit_price="1.00", line_total="1.00"
            ),
            GroundTruthLineItem(
                description="Widget B", qty="1", unit_price="1.00", line_total="1.00"
            ),
        )
    )
    pred = _prediction(
        line_items=(
            LineItem(description="widget a", qty="1", unit_price="1.00", line_total="1.00"),
            LineItem(
                description="Extra hallucinated line", qty="1", unit_price="1.00", line_total="1.00"
            ),
        )
    )

    metrics = compute_metrics("test", "mock", [(gt, pred)])

    assert metrics.line_items.n_ground_truth_lines == 2
    assert metrics.line_items.n_predicted_lines == 2
    assert metrics.line_items.n_matched == 1
    assert metrics.line_items.precision == 0.5  # 1 of 2 predicted lines matched
    assert metrics.line_items.recall == 0.5  # 1 of 2 ground-truth lines matched


def test_sroie_style_document_with_no_line_items_reports_zero_not_crash() -> None:
    gt = GroundTruthDocument(
        doc_id="sroie-1",
        header=GroundTruthHeader(vendor_name="Acme", invoice_date="2026-01-01", total="10.00"),
        line_items=(),
    )
    pred = _prediction(line_items=())

    metrics = compute_metrics("sroie", "mock", [(gt, pred)])

    assert metrics.line_items.n_ground_truth_lines == 0
    assert metrics.line_items.precision is None  # no predicted lines either -> undefined, not 0
    assert metrics.line_items.recall is None


def test_estimated_cost_is_zero_for_free_backends_and_positive_for_gemini() -> None:
    from evals.metrics import estimated_cost_usd

    tesseract_result = ExtractionResult(
        header=InvoiceHeader(),
        backend="tesseract",
        model_version="test",
        latency_ms=1,
        estimated_tokens=1000,
    )
    gemini_result = ExtractionResult(
        header=InvoiceHeader(),
        backend="gemini",
        model_version="test",
        latency_ms=1,
        estimated_tokens=1000,
    )

    assert estimated_cost_usd(tesseract_result) == 0.0
    assert estimated_cost_usd(gemini_result) > 0.0
