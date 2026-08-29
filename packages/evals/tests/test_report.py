from datetime import UTC, datetime

from evals.metrics import CalibrationBucket, EvalMetrics, FieldMetrics, LineItemMetrics
from evals.report import append_to_benchmarks_md, format_run_report
from evals.runner import RunFailure, RunResult


def _sample_metrics() -> EvalMetrics:
    return EvalMetrics(
        dataset="fake",
        backend="mock",
        n_documents=1,
        fields={
            "header.total": FieldMetrics(
                field_path="header.total",
                n=1,
                precision=1.0,
                recall=1.0,
                f1=1.0,
                exact_match_rate=1.0,
                mean_absolute_error=0.0,
                within_tolerance_rate=1.0,
                mean_confidence=0.9,
            )
        },
        calibration=(
            CalibrationBucket(low=0.9, high=1.0, n=1, mean_confidence=0.9, actual_accuracy=1.0),
        ),
        line_items=LineItemMetrics(
            precision=1.0,
            recall=1.0,
            f1=1.0,
            n_ground_truth_lines=1,
            n_predicted_lines=1,
            n_matched=1,
        ),
        mean_latency_ms=10.0,
        total_estimated_cost_usd=0.001,
    )


def _sample_run_result() -> RunResult:
    now = datetime.now(UTC)
    return RunResult(
        dataset="fake",
        backend="mock",
        model_version="test-v1",
        sample_count=1,
        started_at=now,
        finished_at=now,
        pairs=[],
        failures=[RunFailure(doc_id="doc-9", error="boom")],
    )


def test_format_run_report_includes_headline_sections() -> None:
    report = format_run_report(_sample_run_result(), _sample_metrics())

    assert "## Run: fake / mock" in report
    assert "header.total" in report
    assert "Confidence calibration" in report
    assert "0.9" in report
    assert "doc-9" in report  # failures table
    assert "boom" in report


def test_append_to_benchmarks_md_creates_file_with_preamble(tmp_path) -> None:
    path = tmp_path / "BENCHMARKS.md"
    append_to_benchmarks_md(path, "## Run: test section")

    content = path.read_text()
    assert content.startswith("# Benchmarks")
    assert "## Run: test section" in content


def test_append_to_benchmarks_md_never_overwrites_prior_runs(tmp_path) -> None:
    path = tmp_path / "BENCHMARKS.md"
    append_to_benchmarks_md(path, "## Run: first")
    append_to_benchmarks_md(path, "## Run: second")

    content = path.read_text()
    assert "## Run: first" in content
    assert "## Run: second" in content
    assert content.index("## Run: first") < content.index("## Run: second")
