"""Gemini Flash extraction backend.

The google-genai SDK is imported only in this module -- CLAUDE.md forbids it
leaking anywhere else. Callers depend on extractors.base.Extractor and
extractors.base.ExtractionResult, neither of which reference the SDK.
"""

import os
import time
from typing import TYPE_CHECKING

import structlog
from core.errors import ExtractionError
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from extractors.base import BoundingBox, ExtractionResult, Extractor, InvoiceHeader, LineItem
from extractors.errors import RetryableExtractionError
from extractors.pdf import render_pdf_pages
from extractors.ratelimit import TokenBucket

if TYPE_CHECKING:
    from google.genai.types import GenerateContentResponse

log = structlog.get_logger()

DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_RATE_LIMIT_RPM = 10

PROMPT = """You are extracting structured data from a vendor invoice, which may
be a scanned PDF or a phone photo.

For every field you extract, report:
- value: the field's value exactly as it appears on the document (do not
  reformat dates or currency -- report the raw text)
- confidence: your own calibrated confidence, 0.0 to 1.0, that the value is
  correct, based on how clearly it appears in the document
- bbox: the field's bounding box on the page it appears on, as
  {"page": <1-indexed page number>, "x": <left, 0-1>, "y": <top, 0-1>,
  "w": <width, 0-1>, "h": <height, 0-1>}, normalized to that page's own
  dimensions. Omit bbox (leave it null) if you cannot determine a location for
  the field -- never guess.

Extract every line item on the invoice, in the order they appear.

Return only the structured data. Do not invent a value for a field that does
not appear on the document -- report an empty value and low confidence
instead.
"""


class _GeminiField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = ""
    confidence: float = Field(ge=0, le=1)
    bbox: BoundingBox | None = None


class _GeminiHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_number: _GeminiField
    invoice_date: _GeminiField
    due_date: _GeminiField
    vendor_name: _GeminiField
    currency: _GeminiField
    subtotal: _GeminiField
    tax: _GeminiField
    total: _GeminiField


class _GeminiLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: _GeminiField
    qty: _GeminiField
    unit_price: _GeminiField
    line_total: _GeminiField


class _GeminiResponseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    header: _GeminiHeader
    line_items: list[_GeminiLineItem] = Field(default_factory=list)


class GeminiExtractor(Extractor):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        rate_limiter: TokenBucket | None = None,
        client: "genai.Client | None" = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
        if client is None and not resolved_key:
            raise ExtractionError("GEMINI_API_KEY is not set")
        self._client = client if client is not None else genai.Client(api_key=resolved_key)
        self._model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        rpm = float(os.environ.get("GEMINI_RATE_LIMIT_RPM", str(DEFAULT_RATE_LIMIT_RPM)))
        self._rate_limiter = rate_limiter if rate_limiter is not None else TokenBucket(rpm)

    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        parts = self._build_parts(file_bytes, mime_type)

        start = time.monotonic()
        response = self._generate(parts)
        parsed = self._parse_response(response)
        if parsed is None:
            log.warning("gemini_malformed_output_reasking", model=self._model)
            response = self._generate(parts)
            parsed = self._parse_response(response)
            if parsed is None:
                raise ExtractionError(
                    "Gemini returned malformed structured output after one re-ask"
                )
        latency_ms = int((time.monotonic() - start) * 1000)

        return self._to_extraction_result(parsed, response, latency_ms)

    def _build_parts(self, file_bytes: bytes, mime_type: str) -> list[types.Part]:
        if mime_type == "application/pdf":
            pages = render_pdf_pages(file_bytes)
            return [types.Part.from_bytes(data=page, mime_type="image/png") for page in pages]
        return [types.Part.from_bytes(data=file_bytes, mime_type=mime_type)]

    def _generate(self, parts: list[types.Part]) -> "GenerateContentResponse":
        self._rate_limiter.acquire()
        contents: list[types.PartUnionDict] = [*parts, PROMPT]
        try:
            return self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_GeminiResponseSchema,
                ),
            )
        except genai_errors.APIError as exc:
            if isinstance(exc, genai_errors.ServerError) or exc.code == 429:
                raise RetryableExtractionError(str(exc)) from exc
            raise ExtractionError(str(exc)) from exc

    def _parse_response(
        self, response: "GenerateContentResponse"
    ) -> _GeminiResponseSchema | None:
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, _GeminiResponseSchema):
            return parsed
        return None

    def _to_extraction_result(
        self,
        parsed: _GeminiResponseSchema,
        response: "GenerateContentResponse",
        latency_ms: int,
    ) -> ExtractionResult:
        confidence: dict[str, float] = {}
        bbox: dict[str, BoundingBox] = {}

        header_fields = {
            "invoice_number": parsed.header.invoice_number,
            "invoice_date": parsed.header.invoice_date,
            "due_date": parsed.header.due_date,
            "vendor_name": parsed.header.vendor_name,
            "currency": parsed.header.currency,
            "subtotal": parsed.header.subtotal,
            "tax": parsed.header.tax,
            "total": parsed.header.total,
        }
        for name, field in header_fields.items():
            path = f"header.{name}"
            confidence[path] = field.confidence
            if field.bbox is not None:
                bbox[path] = field.bbox

        header_values = {name: field.value or None for name, field in header_fields.items()}
        header = InvoiceHeader(**header_values)

        line_items: list[LineItem] = []
        for i, line in enumerate(parsed.line_items):
            line_fields = {
                "description": line.description,
                "qty": line.qty,
                "unit_price": line.unit_price,
                "line_total": line.line_total,
            }
            for name, field in line_fields.items():
                path = f"lines[{i}].{name}"
                confidence[path] = field.confidence
                if field.bbox is not None:
                    bbox[path] = field.bbox
            line_items.append(
                LineItem(
                    description=line.description.value,
                    qty=line.qty.value,
                    unit_price=line.unit_price.value,
                    line_total=line.line_total.value,
                )
            )

        usage = getattr(response, "usage_metadata", None)
        estimated_tokens = int(getattr(usage, "total_token_count", 0) or 0)

        return ExtractionResult(
            header=header,
            line_items=tuple(line_items),
            confidence=confidence,
            bbox=bbox,
            backend="gemini",
            model_version=self._model,
            latency_ms=latency_ms,
            estimated_tokens=estimated_tokens,
        )
