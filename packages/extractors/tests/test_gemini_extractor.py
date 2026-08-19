"""Unit tests for GeminiExtractor with an injected fake client -- no network
call is ever made. The google-genai SDK's own request/response plumbing is
not under test here; the extractor's error classification, re-ask logic, and
response-to-ExtractionResult mapping are.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from core.errors import ExtractionError
from extractors.errors import RetryableExtractionError
from extractors.gemini import (
    GeminiExtractor,
    _GeminiField,
    _GeminiHeader,
    _GeminiLineItem,
    _GeminiResponseSchema,
)
from extractors.ratelimit import TokenBucket
from google.genai import errors as genai_errors


class _NoWaitLimiter:
    """Stands in for TokenBucket so tests never actually sleep."""

    def acquire(self, tokens: float = 1.0) -> None:
        return None


class _FakeUsage:
    def __init__(self, total_token_count: int) -> None:
        self.total_token_count = total_token_count


class _FakeResponse:
    def __init__(self, parsed: Any, total_token_count: int = 321) -> None:
        self.parsed = parsed
        self.usage_metadata = _FakeUsage(total_token_count)


class _FakeModels:
    def __init__(self, responses: Iterator[Any]) -> None:
        self._responses = responses
        self.calls = 0

    def generate_content(self, **kwargs: Any) -> _FakeResponse:
        self.calls += 1
        item = next(self._responses)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeClient:
    def __init__(self, *responses: Any) -> None:
        self.models = _FakeModels(iter(responses))


def _field(value: str, confidence: float = 0.9) -> _GeminiField:
    return _GeminiField(value=value, confidence=confidence)


def _valid_parsed() -> _GeminiResponseSchema:
    return _GeminiResponseSchema(
        header=_GeminiHeader(
            invoice_number=_field("INV-1"),
            invoice_date=_field("2026-01-01"),
            due_date=_field("2026-01-31"),
            vendor_name=_field("Acme"),
            currency=_field("USD"),
            subtotal=_field("100.00"),
            tax=_field("7.00"),
            total=_field("107.00"),
        ),
        line_items=[
            _GeminiLineItem(
                description=_field("Widget"),
                qty=_field("10"),
                unit_price=_field("10.00"),
                line_total=_field("100.00"),
            )
        ],
    )


def _extractor(*responses: Any) -> GeminiExtractor:
    return GeminiExtractor(client=_FakeClient(*responses), rate_limiter=_NoWaitLimiter())  # type: ignore[arg-type]


def test_requires_api_key_when_no_client_given() -> None:
    with pytest.raises(ExtractionError):
        GeminiExtractor(api_key=None, client=None)


def test_model_defaults_to_default_model_constant() -> None:
    from extractors.gemini import DEFAULT_MODEL

    extractor = GeminiExtractor(client=_FakeClient())
    assert extractor._model == DEFAULT_MODEL  # type: ignore[attr-defined]


def test_model_reads_gemini_model_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-9.9-flash")
    extractor = GeminiExtractor(client=_FakeClient())
    assert extractor._model == "gemini-9.9-flash"  # type: ignore[attr-defined]


def test_explicit_model_argument_overrides_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "gemini-9.9-flash")
    extractor = GeminiExtractor(client=_FakeClient(), model="gemini-explicit")
    assert extractor._model == "gemini-explicit"  # type: ignore[attr-defined]


def test_successful_extraction_maps_header_and_lines() -> None:
    extractor = _extractor(_FakeResponse(_valid_parsed()))

    result = extractor.extract(b"file bytes", "image/png")

    assert result.backend == "gemini"
    assert result.header.invoice_number == "INV-1"
    assert result.header.total == "107.00"
    assert len(result.line_items) == 1
    assert result.line_items[0].description == "Widget"
    assert result.confidence["header.total"] == 0.9
    assert result.confidence["lines[0].qty"] == 0.9
    assert result.estimated_tokens == 321


def test_bbox_included_only_when_present() -> None:
    parsed = _valid_parsed()
    parsed.header.total.bbox = None
    extractor = _extractor(_FakeResponse(parsed))

    result = extractor.extract(b"file bytes", "image/png")

    assert "header.total" not in result.bbox


def test_429_raises_retryable() -> None:
    error = genai_errors.ClientError(429, {"message": "rate limited"})
    extractor = _extractor(error)

    with pytest.raises(RetryableExtractionError):
        extractor.extract(b"file bytes", "image/png")


def test_5xx_raises_retryable() -> None:
    error = genai_errors.ServerError(503, {"message": "unavailable"})
    extractor = _extractor(error)

    with pytest.raises(RetryableExtractionError):
        extractor.extract(b"file bytes", "image/png")


def test_non_429_client_error_raises_permanent() -> None:
    error = genai_errors.ClientError(400, {"message": "bad request"})
    extractor = _extractor(error)

    with pytest.raises(ExtractionError) as exc_info:
        extractor.extract(b"file bytes", "image/png")
    assert not isinstance(exc_info.value, RetryableExtractionError)


def test_malformed_output_triggers_one_reask_then_succeeds() -> None:
    extractor = _extractor(_FakeResponse(None), _FakeResponse(_valid_parsed()))

    result = extractor.extract(b"file bytes", "image/png")

    assert result.header.invoice_number == "INV-1"
    assert extractor._client.models.calls == 2  # type: ignore[attr-defined]


def test_malformed_output_after_reask_raises_permanent() -> None:
    extractor = _extractor(_FakeResponse(None), _FakeResponse(None))

    with pytest.raises(ExtractionError) as exc_info:
        extractor.extract(b"file bytes", "image/png")
    assert not isinstance(exc_info.value, RetryableExtractionError)
    assert extractor._client.models.calls == 2  # type: ignore[attr-defined]


def test_default_rate_limiter_is_a_token_bucket() -> None:
    extractor = GeminiExtractor(client=_FakeClient())
    assert isinstance(extractor._rate_limiter, TokenBucket)  # type: ignore[attr-defined]


def test_pdf_mime_type_renders_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    rendered_pages = [b"page-1-png-bytes", b"page-2-png-bytes"]
    monkeypatch.setattr("extractors.gemini.render_pdf_pages", lambda data: rendered_pages)

    extractor = _extractor(_FakeResponse(_valid_parsed()))
    result = extractor.extract(b"fake pdf bytes", "application/pdf")

    assert result.header.invoice_number == "INV-1"  # extraction still completes end to end
