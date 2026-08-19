"""The Extractor interface and its return type.

Every extraction backend (Gemini, Tesseract, the mock used in tests) implements
Extractor. Callers never depend on a backend directly -- this is the interface
CLAUDE.md means by "every external dependency sits behind an interface."
"""

from abc import ABC, abstractmethod
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Confidence = Annotated[float, Field(ge=0, le=1)]
Normalized = Annotated[float, Field(ge=0, le=1)]


class BoundingBox(BaseModel):
    """Location of an extracted field on the source document, normalized to
    the page's own dimensions so it's resolution-independent."""

    model_config = ConfigDict(frozen=True)

    page: Annotated[int, Field(ge=1)]
    x: Normalized
    y: Normalized
    w: Normalized
    h: Normalized


class InvoiceHeader(BaseModel):
    """Header-level fields, exactly as read off the document.

    These are deliberately loose (strings, not date/Decimal) -- an extraction
    backend reports what it saw, formatting quirks included. extractors.normalize
    is the separate, explicitly-tested layer that turns "$1,234.56" or
    "March 3, 2026" into a Decimal or a date. Keeping that a distinct step is
    what lets a raw-OCR backend (Tesseract) and a structured-output backend
    (Gemini) share one normalization path.
    """

    model_config = ConfigDict(frozen=True)

    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    vendor_name: str | None = None
    currency: str | None = None
    subtotal: str | None = None
    tax: str | None = None
    total: str | None = None


class LineItem(BaseModel):
    """One invoice line, exactly as read off the document. See InvoiceHeader
    for why these are strings rather than Decimal/int."""

    model_config = ConfigDict(frozen=True)

    description: str
    qty: str
    unit_price: str
    line_total: str


class ExtractionResult(BaseModel):
    """What every Extractor.extract() call returns.

    confidence and bbox are keyed by field path -- "header.total",
    "lines[0].qty" -- mirroring field_confidences.field_path in the DB schema.
    bbox omits an entry for any field the backend couldn't localize; confidence
    always has an entry for every field the backend attempted to read.
    """

    model_config = ConfigDict(frozen=True)

    header: InvoiceHeader
    line_items: tuple[LineItem, ...] = ()
    confidence: dict[str, Confidence] = Field(default_factory=dict)
    bbox: dict[str, BoundingBox] = Field(default_factory=dict)

    backend: str
    model_version: str
    latency_ms: Annotated[int, Field(ge=0)]
    estimated_tokens: Annotated[int, Field(ge=0)]


class Extractor(ABC):
    """Interface every extraction backend implements. No I/O contract is
    specified beyond "give me bytes and a mime type, get structured data back" --
    what happens inside (an API call, local OCR, a fixture lookup) is the
    backend's business."""

    @abstractmethod
    def extract(self, file_bytes: bytes, mime_type: str) -> ExtractionResult:
        """Extract structured invoice data from a document.

        Raises ExtractionError (packages/core) or one of its subclasses on
        failure -- never returns a partial or sentinel result.
        """
        raise NotImplementedError
