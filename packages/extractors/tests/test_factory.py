"""factory.py tests: backend selection (name + env-driven default), and the
FallbackExtractor retry/fallback chain, exercised with scripted fake
Extractor doubles so the retry-count and fallback-trigger logic is tested in
isolation from any real backend.
"""

import pytest
from core.errors import ExtractionError
from extractors.base import ExtractionResult, Extractor
from extractors.errors import RetryableExtractionError
from extractors.factory import FallbackExtractor, get_extractor, get_extractor_with_fallback
from extractors.mock import MockExtractor
from extractors.tesseract import TesseractExtractor


class _ScriptedExtractor(Extractor):
    """Raises/returns exactly what it's told to, in call order."""

    def __init__(self, *outcomes: Exception | ExtractionResult) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _result(backend: str) -> ExtractionResult:
    return MockExtractor("clean_invoice").extract(b"x", "image/png").model_copy(
        update={"backend": backend}
    )


# --- get_extractor ---------------------------------------------------------


def test_get_extractor_mock_by_name() -> None:
    assert isinstance(get_extractor("mock"), MockExtractor)


def test_get_extractor_tesseract_by_name() -> None:
    assert isinstance(get_extractor("tesseract"), TesseractExtractor)


def test_get_extractor_unknown_name_raises() -> None:
    with pytest.raises(ExtractionError):
        get_extractor("not-a-real-backend")


def test_get_extractor_reads_env_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTRACTOR_BACKEND", "mock")
    assert isinstance(get_extractor(), MockExtractor)


def test_get_extractor_explicit_name_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXTRACTOR_BACKEND", "tesseract")
    assert isinstance(get_extractor("mock"), MockExtractor)


def test_get_extractor_defaults_to_gemini_when_nothing_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EXTRACTOR_BACKEND", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-used")
    from extractors.gemini import GeminiExtractor

    assert isinstance(get_extractor(), GeminiExtractor)


# --- FallbackExtractor -----------------------------------------------------


def test_primary_success_never_touches_fallback() -> None:
    primary = _ScriptedExtractor(_result("gemini"))
    fallback = _ScriptedExtractor(_result("tesseract"))
    extractor = FallbackExtractor(primary, fallback, max_retries=2)

    result = extractor.extract(b"x", "image/png")

    assert result.backend == "gemini"
    assert primary.calls == 1
    assert fallback.calls == 0


def test_non_retryable_error_propagates_without_fallback() -> None:
    primary = _ScriptedExtractor(ExtractionError("permanent failure"))
    fallback = _ScriptedExtractor(_result("tesseract"))
    extractor = FallbackExtractor(primary, fallback, max_retries=2)

    with pytest.raises(ExtractionError):
        extractor.extract(b"x", "image/png")

    assert fallback.calls == 0


def test_retryable_error_exhausts_retries_then_falls_back() -> None:
    primary = _ScriptedExtractor(
        RetryableExtractionError("rate limited"),
        RetryableExtractionError("rate limited"),
        RetryableExtractionError("rate limited"),
    )
    fallback = _ScriptedExtractor(_result("tesseract"))
    extractor = FallbackExtractor(primary, fallback, max_retries=2)

    result = extractor.extract(b"x", "image/png")

    assert result.backend == "tesseract"
    assert primary.calls == 3  # 1 initial attempt + 2 retries
    assert fallback.calls == 1


def test_retryable_error_succeeds_on_retry_without_fallback() -> None:
    primary = _ScriptedExtractor(
        RetryableExtractionError("rate limited"),
        _result("gemini"),
    )
    fallback = _ScriptedExtractor(_result("tesseract"))
    extractor = FallbackExtractor(primary, fallback, max_retries=2)

    result = extractor.extract(b"x", "image/png")

    assert result.backend == "gemini"
    assert primary.calls == 2
    assert fallback.calls == 0


def test_fallback_itself_can_raise() -> None:
    primary = _ScriptedExtractor(RetryableExtractionError("down"))
    fallback = _ScriptedExtractor(ExtractionError("tesseract also failed"))
    extractor = FallbackExtractor(primary, fallback, max_retries=0)

    with pytest.raises(ExtractionError):
        extractor.extract(b"x", "image/png")


def test_get_extractor_with_fallback_wires_mock_primary_and_tesseract_fallback() -> None:
    extractor = get_extractor_with_fallback(primary_name="mock", fallback_name="tesseract")
    assert isinstance(extractor, FallbackExtractor)
    result = extractor.extract(b"x", "image/png")
    assert result.backend == "mock"  # primary succeeded, no fallback needed
