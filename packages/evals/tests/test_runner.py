from collections.abc import Iterator

from core.errors import ExtractionError
from evals.datasets.base import DatasetLoader
from evals.models import DatasetExample, GroundTruthDocument, GroundTruthHeader
from evals.runner import run
from extractors.base import ExtractionResult, Extractor, InvoiceHeader


class _FakeLoader(DatasetLoader):
    """In-memory loader for runner tests -- no filesystem, no real dataset."""

    name = "fake"

    def __init__(self, n: int) -> None:  # deliberately skips DatasetLoader.__init__'s root check
        self._n = n

    def __iter__(self) -> Iterator[DatasetExample]:
        for i in range(self._n):
            doc_id = f"doc-{i}"
            yield DatasetExample(
                doc_id=doc_id,
                document_bytes=b"fake",
                mime_type="application/pdf",
                ground_truth=GroundTruthDocument(
                    doc_id=doc_id, header=GroundTruthHeader(total="100.00")
                ),
            )


class _AlwaysSucceedsExtractor(Extractor):
    def __init__(self) -> None:
        self.call_count = 0

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        self.call_count += 1
        return ExtractionResult(
            header=InvoiceHeader(total="100.00"),
            confidence={"header.total": 0.9},
            backend="mock",
            model_version="test",
            latency_ms=1,
            estimated_tokens=10,
        )


class _FailsOnFirstCallThenSucceeds(Extractor):
    def __init__(self, fail_doc_ids: set[str]) -> None:
        self._fail_doc_ids = set(fail_doc_ids)
        self.calls: list[bytes] = []

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        self.calls.append(file_bytes)
        # This stub can't see doc_id (Extractor.extract only gets bytes),
        # so tests using it key failure off call order instead.
        if len(self.calls) <= len(self._fail_doc_ids):
            raise ExtractionError("simulated transient failure")
        return ExtractionResult(
            header=InvoiceHeader(total="100.00"),
            backend="mock",
            model_version="test",
            latency_ms=1,
            estimated_tokens=10,
        )


def test_run_succeeds_across_all_documents() -> None:
    extractor = _AlwaysSucceedsExtractor()
    result = run("fake", _FakeLoader(5), "mock", n=5, extractor=extractor)

    assert len(result.pairs) == 5
    assert result.failures == []
    assert extractor.call_count == 5


def test_run_respects_n_even_with_more_documents_available() -> None:
    extractor = _AlwaysSucceedsExtractor()
    result = run("fake", _FakeLoader(20), "mock", n=3, extractor=extractor)

    assert len(result.pairs) == 3
    assert extractor.call_count == 3


def test_run_records_failures_without_aborting_the_whole_run() -> None:
    # concurrency=1: _FailsOnFirstCallThenSucceeds counts calls without a
    # lock, so this keeps the test itself deterministic -- runner.py's own
    # thread-safety (the shared, now-thread-safe TokenBucket) is covered
    # separately, not by this stub.
    extractor = _FailsOnFirstCallThenSucceeds(fail_doc_ids={"one"})
    result = run("fake", _FakeLoader(4), "mock", n=4, concurrency=1, extractor=extractor)

    assert len(result.failures) == 1
    assert len(result.pairs) == 3


def test_checkpoint_resume_skips_completed_documents(tmp_path) -> None:
    checkpoint_path = tmp_path / "checkpoint.jsonl"
    extractor = _AlwaysSucceedsExtractor()

    first = run(
        "fake", _FakeLoader(5), "mock", n=5, extractor=extractor, checkpoint_path=checkpoint_path
    )
    assert extractor.call_count == 5
    assert len(first.pairs) == 5

    # A "second run" against a fresh extractor instance simulates the
    # process being killed and restarted -- if resume genuinely skips
    # completed documents, this fresh extractor is never called at all.
    resumed_extractor = _AlwaysSucceedsExtractor()
    second = run(
        "fake",
        _FakeLoader(5),
        "mock",
        n=5,
        extractor=resumed_extractor,
        checkpoint_path=checkpoint_path,
    )

    assert resumed_extractor.call_count == 0
    assert len(second.pairs) == 5


def test_checkpoint_resume_retries_previously_failed_documents(tmp_path) -> None:
    checkpoint_path = tmp_path / "checkpoint.jsonl"

    failing_extractor = _FailsOnFirstCallThenSucceeds(fail_doc_ids={"one", "two", "three"})
    first = run(
        "fake",
        _FakeLoader(3),
        "mock",
        n=3,
        concurrency=1,
        extractor=failing_extractor,
        checkpoint_path=checkpoint_path,
    )
    assert len(first.failures) == 3
    assert len(first.pairs) == 0

    succeeding_extractor = _AlwaysSucceedsExtractor()
    second = run(
        "fake",
        _FakeLoader(3),
        "mock",
        n=3,
        extractor=succeeding_extractor,
        checkpoint_path=checkpoint_path,
    )

    # All 3 were retried (none were a durable "success" in the checkpoint)
    # and all 3 succeed against the new extractor.
    assert succeeding_extractor.call_count == 3
    assert len(second.pairs) == 3
    assert second.failures == []


def test_checkpoint_from_different_dataset_or_backend_raises(tmp_path) -> None:
    from evals.errors import CheckpointError

    checkpoint_path = tmp_path / "checkpoint.jsonl"
    extractor = _AlwaysSucceedsExtractor()
    run("fake", _FakeLoader(2), "mock", n=2, extractor=extractor, checkpoint_path=checkpoint_path)

    try:
        run(
            "a-different-dataset",
            _FakeLoader(2),
            "mock",
            n=2,
            extractor=_AlwaysSucceedsExtractor(),
            checkpoint_path=checkpoint_path,
        )
        raised = False
    except CheckpointError:
        raised = True
    assert raised
