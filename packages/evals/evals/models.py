"""Ground truth types -- the "our field schema" every dataset loader maps
into. Deliberately shaped like extractors.base.InvoiceHeader/LineItem (same
field names, same field-path convention: "header.total", "lines[0].qty")
so metrics.py can compare a GroundTruthDocument against an
extractors.base.ExtractionResult field-by-field with no translation layer
in between.

Ground truth values are strings, exactly as extraction output is (see
InvoiceHeader's own docstring for why) -- normalization for comparison
happens once, in metrics.py, via the same extractors.normalize functions
production code uses, not duplicated here.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GroundTruthLineItem:
    description: str | None = None
    qty: str | None = None
    unit_price: str | None = None
    line_total: str | None = None


@dataclass(frozen=True, slots=True)
class GroundTruthHeader:
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    vendor_name: str | None = None
    currency: str | None = None
    subtotal: str | None = None
    tax: str | None = None
    total: str | None = None


@dataclass(frozen=True, slots=True)
class GroundTruthDocument:
    """One labeled example. `unmapped_fields` keeps whatever the source
    dataset annotated that has no home in our schema -- e.g. DocILE's
    customer_billing_address, CORD's void_menu.* categories, SROIE's
    address -- so "what did we drop and why" stays inspectable per document
    rather than silently discarded, per CLAUDE.md's ask to document mapping
    decisions."""

    doc_id: str
    header: GroundTruthHeader
    line_items: tuple[GroundTruthLineItem, ...] = ()
    unmapped_fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DatasetExample:
    doc_id: str
    document_bytes: bytes
    mime_type: str
    ground_truth: GroundTruthDocument
