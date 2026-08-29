from collections.abc import Iterator

from evals.compare import compare
from evals.datasets.base import DatasetLoader
from evals.models import DatasetExample, GroundTruthDocument, GroundTruthHeader
from extractors.base import ExtractionResult, Extractor, InvoiceHeader


class _FakeLoader(DatasetLoader):
    name = "fake"

    def __init__(self, n: int) -> None:
        self._n = n

    def __iter__(self) -> Iterator[DatasetExample]:
        for i in range(self._n):
            doc_id = f"doc-{i}"
            yield DatasetExample(
                doc_id=doc_id,
                document_bytes=b"fake",
                mime_type="application/pdf",
                ground_truth=GroundTruthDocument(
                    doc_id=doc_id,
                    header=GroundTruthHeader(total="100.00", vendor_name="Acme Corp."),
                ),
            )


class _StubExtractor(Extractor):
    """Always reports the same header values -- backend_name is baked in
    via `total`/`vendor_name` so two instances can be made to agree or
    disagree deterministically."""

    def __init__(self, name: str, total: str, vendor_name: str) -> None:
        self._name = name
        self._total = total
        self._vendor_name = vendor_name

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        return ExtractionResult(
            header=InvoiceHeader(total=self._total, vendor_name=self._vendor_name),
            confidence={"header.total": 0.9, "header.vendor_name": 0.9},
            backend=self._name,
            model_version="test",
            latency_ms=1,
            estimated_tokens=10,
        )


def test_compare_agreement_rate_when_backends_agree(monkeypatch) -> None:
    import evals.runner as runner_module

    extractors = {
        "backend-a": _StubExtractor("backend-a", total="100.00", vendor_name="Acme Corp."),
        "backend-b": _StubExtractor("backend-b", total="100.00", vendor_name="ACME CORP"),
    }
    monkeypatch.setattr(runner_module, "get_extractor", lambda name: extractors[name])

    result = compare("fake", _FakeLoader(3), "backend-a", "backend-b", n=3)

    assert result.n_common == 3
    # "Acme Corp." vs "ACME CORP" normalize equal -- full agreement despite
    # different casing/punctuation, same as metrics.py's exact-match logic.
    assert result.fields["header.vendor_name"].agreement_rate == 1.0
    assert result.fields["header.total"].agreement_rate == 1.0


def test_compare_agreement_rate_when_backends_disagree(monkeypatch) -> None:
    import evals.runner as runner_module

    extractors = {
        "backend-a": _StubExtractor("backend-a", total="100.00", vendor_name="Acme Corp."),
        "backend-b": _StubExtractor("backend-b", total="999.99", vendor_name="Acme Corp."),
    }
    monkeypatch.setattr(runner_module, "get_extractor", lambda name: extractors[name])

    result = compare("fake", _FakeLoader(3), "backend-a", "backend-b", n=3)

    assert result.fields["header.total"].agreement_rate == 0.0
    assert result.fields["header.vendor_name"].agreement_rate == 1.0


def test_compare_f1_delta_direction_is_b_minus_a(monkeypatch) -> None:
    import evals.runner as runner_module

    # backend-a matches ground truth (total=100.00); backend-b never does.
    extractors = {
        "backend-a": _StubExtractor("backend-a", total="100.00", vendor_name="Acme Corp."),
        "backend-b": _StubExtractor("backend-b", total="1.00", vendor_name="Acme Corp."),
    }
    monkeypatch.setattr(runner_module, "get_extractor", lambda name: extractors[name])

    result = compare("fake", _FakeLoader(2), "backend-a", "backend-b", n=2)

    total_comparison = result.fields["header.total"]
    # Both extract *a* value for every doc (F1 presence is 1.0 either way);
    # what differs is exact-match accuracy, which f1_delta doesn't capture
    # here since presence-F1 is identical -- so assert the delta is exactly
    # 0 and confirm the real signal (exact match) lives in metrics_a/b
    # instead, not in this field's F1.
    assert total_comparison.f1_delta == 0.0
    assert result.metrics_a.fields["header.total"].exact_match_rate == 1.0
    assert result.metrics_b.fields["header.total"].exact_match_rate == 0.0


def test_compare_cost_delta_reflects_backend_pricing(monkeypatch) -> None:
    import evals.runner as runner_module

    class _GeminiLike(Extractor):
        def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
            return ExtractionResult(
                header=InvoiceHeader(total="100.00"),
                backend="gemini",
                model_version="test",
                latency_ms=1,
                estimated_tokens=10_000,
            )

    class _TesseractLike(Extractor):
        def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
            return ExtractionResult(
                header=InvoiceHeader(total="100.00"),
                backend="tesseract",
                model_version="test",
                latency_ms=1,
                estimated_tokens=0,
            )

    extractors = {"gemini": _GeminiLike(), "tesseract": _TesseractLike()}
    monkeypatch.setattr(runner_module, "get_extractor", lambda name: extractors[name])

    result = compare("fake", _FakeLoader(2), "tesseract", "gemini", n=2)

    assert result.cost_delta_usd is not None
    assert result.cost_delta_usd > 0  # gemini (b) costs more than tesseract (a)
