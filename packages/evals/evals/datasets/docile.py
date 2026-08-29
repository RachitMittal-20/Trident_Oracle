"""DocILE loader -- the primary dataset (CLAUDE.md prompt: "DocILE
(primary), CORD and SROIE (fallbacks if DocILE access hasn't come
through)").

DocILE (https://docile.rossum.ai/) requires registering for access before
you can download it; there was no local copy available while writing this
loader, so the mapping below is implemented against DocILE's *documented*
KILE/LIR field taxonomy and annotation layout (rossum-ai/docile on GitHub,
the DocILE paper's field list), not verified against real downloaded files.

IMPORTANT: the first time you actually run this against a real DocILE
export, open one `annotations/<doc_id>.json` file and confirm it matches
what `_parse_annotation_json` below expects (an object with a
`field_extractions` list, each entry having `fieldtype`, `text`, `page`,
`bbox`, and `line_item_id`) before trusting any metric this loader feeds
into. If DocILE's shipped schema differs, the fix is localized to that one
function.

Expected local layout, per the toolkit's own convention:
    {root}/{split}.json          -- list of document ids in this split
    {root}/annotations/{id}.json -- this document's field_extractions
    {root}/pdfs/{id}.pdf          -- the source PDF

Field mapping (DocILE KILE/LIR fieldtype -> our schema):
    date_issue                 -> header.invoice_date        (confident)
    date_due                   -> header.due_date            (confident)
    vendor_name                -> header.vendor_name         (confident)
    currency_code_amount_due   -> header.currency            (confident)
    amount_total_gross         -> header.total               (confident)
    amount_total_net           -> header.subtotal            (confident)
    amount_total_tax           -> header.tax                 (confident)
    line_item_description      -> lines[].description        (confident)
    line_item_quantity         -> lines[].qty                (confident)
    line_item_unit_price_gross -> lines[].unit_price         (confident)
    line_item_amount_gross     -> lines[].line_total         (confident)

Fields with NO clean home in our schema (kept in
GroundTruthDocument.unmapped_fields, never silently dropped):
    account_num, bank_num, customer_* (billing/delivery/tax/registration
    id, address, name), order_id, customer_order_id, vendor_order_id,
    vendor_address, vendor_email, vendor_registration_id, vendor_tax_id,
    payment_terms, tax_detail_* (per-rate breakdown -- we only carry one
    aggregate tax figure), line_item_code, line_item_currency,
    line_item_date, line_item_discount_amount, line_item_discount_rate,
    line_item_hts_number, line_item_order_id, line_item_person_name,
    line_item_tax, line_item_tax_rate, line_item_unit_base,
    line_item_unit_base_quantity, line_item_weight.

No DocILE field maps cleanly to header.invoice_number: DocILE's taxonomy
has no field for "the invoice number as printed on the document" the way
our schema does. document_id (DocILE's own dataset identifier for the
document) is NOT the same thing and is deliberately not used as a stand-in
-- it would silently manufacture a value no extractor could ever actually
read off the page. header.invoice_number is always None for this loader;
document_id is preserved under unmapped_fields for reference.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from evals.datasets.base import DatasetLoader
from evals.errors import DatasetFormatError
from evals.models import DatasetExample, GroundTruthDocument, GroundTruthHeader, GroundTruthLineItem

_KILE_TO_HEADER = {
    "date_issue": "invoice_date",
    "date_due": "due_date",
    "vendor_name": "vendor_name",
    "currency_code_amount_due": "currency",
    "amount_total_gross": "total",
    "amount_total_net": "subtotal",
    "amount_total_tax": "tax",
}

_LIR_TO_LINE = {
    "line_item_description": "description",
    "line_item_quantity": "qty",
    "line_item_unit_price_gross": "unit_price",
    "line_item_amount_gross": "line_total",
}


def _parse_annotation_json(doc_id: str, data: dict[str, Any]) -> GroundTruthDocument:
    try:
        extractions = data["field_extractions"]
    except KeyError as exc:
        raise DatasetFormatError(
            f"{doc_id}: annotation JSON has no 'field_extractions' key -- "
            "see docile.py's module docstring, this loader may need updating "
            "to match the real downloaded schema"
        ) from exc

    header_values: dict[str, str] = {}
    unmapped: dict[str, str] = {"document_id": doc_id}
    line_values: dict[int, dict[str, str]] = {}

    for entry in extractions:
        fieldtype = entry.get("fieldtype")
        text = entry.get("text")
        if not fieldtype or text is None:
            continue
        line_item_id = entry.get("line_item_id")

        if line_item_id is not None and fieldtype in _LIR_TO_LINE:
            line_values.setdefault(line_item_id, {})[_LIR_TO_LINE[fieldtype]] = text
        elif line_item_id is None and fieldtype in _KILE_TO_HEADER:
            header_values[_KILE_TO_HEADER[fieldtype]] = text
        elif line_item_id is None:
            unmapped[fieldtype] = text
        # LIR fields with no home in our schema (line_item_tax, etc.) are
        # dropped per-line rather than added to the document-level
        # unmapped_fields dict -- there's no clean per-line equivalent of
        # that dict without a much bigger schema change, and per CLAUDE.md
        # "build exactly what's asked" this loader documents the drop (see
        # module docstring) rather than inventing one.

    line_items = tuple(
        GroundTruthLineItem(
            description=fields.get("description"),
            qty=fields.get("qty"),
            unit_price=fields.get("unit_price"),
            line_total=fields.get("line_total"),
        )
        for _line_id, fields in sorted(line_values.items())
    )

    return GroundTruthDocument(
        doc_id=doc_id,
        header=GroundTruthHeader(**header_values),
        line_items=line_items,
        unmapped_fields=unmapped,
    )


class DocileLoader(DatasetLoader):
    name = "docile"

    def __init__(self, root: Path, split: str = "val") -> None:
        super().__init__(root)
        self.split = split

    def __iter__(self) -> Iterator[DatasetExample]:
        split_path = self.root / f"{self.split}.json"
        if not split_path.is_file():
            raise DatasetFormatError(
                f"docile split file not found: {split_path} -- expected a JSON "
                "list of document ids for this split"
            )
        doc_ids: list[str] = json.loads(split_path.read_text())

        for doc_id in doc_ids:
            annotation_path = self.root / "annotations" / f"{doc_id}.json"
            pdf_path = self.root / "pdfs" / f"{doc_id}.pdf"
            if not annotation_path.is_file() or not pdf_path.is_file():
                continue
            ground_truth = _parse_annotation_json(
                doc_id, json.loads(annotation_path.read_text())
            )
            yield DatasetExample(
                doc_id=doc_id,
                document_bytes=pdf_path.read_bytes(),
                mime_type="application/pdf",
                ground_truth=ground_truth,
            )
