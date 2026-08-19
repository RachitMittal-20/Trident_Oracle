"""Extractor selection and backend fallback.

get_extractor() picks a single named backend. FallbackExtractor wraps a
primary and a fallback backend so a transient primary failure (rate limit,
5xx) degrades to the local OCR backend instead of failing the whole job --
CLAUDE.md principle 7: fail loudly in development, degrade gracefully in
production. Which backend actually produced a given result is always
recoverable from ExtractionResult.backend; this module also logs the
fallback event itself for observability.
"""

import os

import structlog
from core.errors import ExtractionError

from extractors.base import ExtractionResult, Extractor
from extractors.errors import RetryableExtractionError
from extractors.gemini import GeminiExtractor
from extractors.mock import MockExtractor
from extractors.tesseract import TesseractExtractor

log = structlog.get_logger()

DEFAULT_BACKEND = "gemini"
DEFAULT_FALLBACK_BACKEND = "tesseract"
DEFAULT_MAX_RETRIES = 2

_BACKENDS: dict[str, type[Extractor]] = {
    "gemini": GeminiExtractor,
    "tesseract": TesseractExtractor,
    "mock": MockExtractor,
}


def get_extractor(name: str | None = None) -> Extractor:
    """Construct a single named backend ("gemini", "tesseract", or "mock").

    Reads EXTRACTOR_BACKEND for the default when `name` is omitted, falling
    back to "gemini" if that isn't set either. Does not wire up a fallback
    chain -- see get_extractor_with_fallback for that.
    """
    resolved = name or os.environ.get("EXTRACTOR_BACKEND", DEFAULT_BACKEND)
    try:
        backend_cls = _BACKENDS[resolved]
    except KeyError:
        raise ExtractionError(f"unknown extractor backend: {resolved!r}") from None
    return backend_cls()


class FallbackExtractor(Extractor):
    """Tries `primary` up to `max_retries + 1` times; if every attempt raises
    RetryableExtractionError, falls through to `fallback`. Any other
    exception from `primary` propagates immediately without falling back --
    fallback exists for transient failures, not to silently paper over a
    primary that is fundamentally broken or misconfigured.
    """

    def __init__(self, primary: Extractor, fallback: Extractor, max_retries: int = 2) -> None:
        self._primary = primary
        self._fallback = fallback
        self._max_retries = max_retries

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        last_error: RetryableExtractionError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return self._primary.extract(file_bytes, mime_type)
            except RetryableExtractionError as exc:
                last_error = exc
                log.warning(
                    "extractor_retryable_failure",
                    primary=type(self._primary).__name__,
                    attempt=attempt + 1,
                    max_attempts=self._max_retries + 1,
                    error=str(exc),
                )

        log.warning(
            "extractor_falling_back",
            primary=type(self._primary).__name__,
            fallback=type(self._fallback).__name__,
            reason=str(last_error) if last_error else "unknown",
        )
        return self._fallback.extract(file_bytes, mime_type)


def get_extractor_with_fallback(
    primary_name: str | None = None,
    fallback_name: str = DEFAULT_FALLBACK_BACKEND,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Extractor:
    """The primary extractor for real invoice intake: `primary_name` (or
    EXTRACTOR_BACKEND, or "gemini") with automatic fallback to `fallback_name`
    ("tesseract" by default) on repeated RetryableExtractionError."""
    primary = get_extractor(primary_name)
    fallback = get_extractor(fallback_name)
    return FallbackExtractor(primary, fallback, max_retries=max_retries)
