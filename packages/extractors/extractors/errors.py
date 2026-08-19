"""Extraction-specific exceptions. Both are ExtractionError (packages/core),
so callers that only care about "extraction failed" can catch ExtractionError;
callers that want to distinguish retryable failures catch RetryableExtractionError
specifically.
"""

from core.errors import ExtractionError


class RetryableExtractionError(ExtractionError):
    """Extraction failed for a transient reason (HTTP 429, 5xx) -- safe to retry."""
