"""CORD (Consolidated Receipt Dataset) loader -- a fallback dataset (see
docile.py's docstring for why fallbacks exist). CORD is public, no
registration needed: https://github.com/clovaai/cord.

Expected local layout, matching CORD's own released structure:
    {root}/{split}/image/{id}.png
    {root}/{split}/json/{id}.json

Each json file's ground truth shape:
    {"valid_line": [
        {"words": [{"text": "..."}, ...], "category": "menu.nm", "group_id": 0},
        ...
    ]}
Multiple `words` entries in one line are joined with a space to form that
line's text. `group_id` ties a menu item's name/count/price entries
together into one row -- CORD's schema, not something invented here.

Field mapping (CORD category -> our schema), grouped by group_id within
each "menu.*" category to reconstruct one line per row:
    menu.nm         -> lines[].description  (confident)
    menu.cnt        -> lines[].qty          (confident)
    menu.unitprice  -> lines[].unit_price   (confident)
    menu.price      -> lines[].line_total   (confident -- CORD's "price" is
                                              the line's total, not a
                                              per-unit figure)
    sub_total.subtotal_price -> header.subtotal (confident)
    sub_total.tax_price      -> header.tax      (confident)
    total.total_price        -> header.total    (confident)

CORD is a RECEIPT dataset, not an invoice dataset: it has no category for
an invoice number, a due date, or a named vendor/store at all -- these are
structurally absent from CORD's annotation schema, not just unlabeled on
individual receipts. header.invoice_number, header.due_date,
header.vendor_name, and header.currency are always None for every CORD
example; there is nothing to map them from. This is the single biggest gap
in what CORD can validate compared to DocILE (see docile.py) -- CORD can
only ever test header amounts and line items, never header identity/date
fields, however well an extractor performs on those.

Categories with no home in our schema (kept in unmapped_fields, first
occurrence per document only -- CORD documents can repeat a category, e.g.
several sub_total.etc lines, and this dict is document-scoped, not
line-scoped): menu.num, menu.discountprice, menu.itemsubtotal, menu.vatyn,
menu.etc, menu.ntm, menu.sub_*, void_menu.*, sub_total.discount_price,
sub_total.service_price, sub_total.othersvc_price, sub_total.etc,
total.total_etc, total.cashprice, total.changeprice,
total.creditcardprice, total.emoneyprice, total.menutype_cnt,
total.menuqty_cnt.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from evals.datasets.base import DatasetLoader
from evals.models import DatasetExample, GroundTruthDocument, GroundTruthHeader, GroundTruthLineItem

_LINE_CATEGORY_TO_FIELD = {
    "menu.nm": "description",
    "menu.cnt": "qty",
    "menu.unitprice": "unit_price",
    "menu.price": "line_total",
}

_HEADER_CATEGORY_TO_FIELD = {
    "sub_total.subtotal_price": "subtotal",
    "sub_total.tax_price": "tax",
    "total.total_price": "total",
}


def _line_text(entry: dict[str, Any]) -> str:
    return " ".join(word.get("text", "") for word in entry.get("words", [])).strip()


def _parse_ground_truth_json(doc_id: str, data: dict[str, Any]) -> GroundTruthDocument:
    header_values: dict[str, str] = {}
    unmapped: dict[str, str] = {}
    line_values: dict[object, dict[str, str]] = {}

    for entry in data.get("valid_line", []):
        category = entry.get("category")
        text = _line_text(entry)
        if not category or not text:
            continue

        if category in _LINE_CATEGORY_TO_FIELD:
            group_id = entry.get("group_id")
            line_values.setdefault(group_id, {})[_LINE_CATEGORY_TO_FIELD[category]] = text
        elif category in _HEADER_CATEGORY_TO_FIELD:
            header_values[_HEADER_CATEGORY_TO_FIELD[category]] = text
        elif category not in unmapped:
            unmapped[category] = text

    line_items = tuple(
        GroundTruthLineItem(
            description=fields.get("description"),
            qty=fields.get("qty"),
            unit_price=fields.get("unit_price"),
            line_total=fields.get("line_total"),
        )
        # group_id ordering is CORD's own row order on the receipt; None
        # (ungrouped stray line-category words) sorts last rather than
        # raising on a mixed int/None key.
        for _group_id, fields in sorted(line_values.items(), key=lambda kv: (kv[0] is None, kv[0]))
    )

    return GroundTruthDocument(
        doc_id=doc_id,
        header=GroundTruthHeader(**header_values),
        line_items=line_items,
        unmapped_fields=unmapped,
    )


class CordLoader(DatasetLoader):
    name = "cord"

    def __init__(self, root: Path, split: str = "test") -> None:
        super().__init__(root)
        self.split = split

    def __iter__(self) -> Iterator[DatasetExample]:
        image_dir = self.root / self.split / "image"
        json_dir = self.root / self.split / "json"
        for json_path in sorted(json_dir.glob("*.json")):
            doc_id = json_path.stem
            image_candidates = [image_dir / f"{doc_id}{ext}" for ext in (".png", ".jpg", ".jpeg")]
            image_path = next((p for p in image_candidates if p.is_file()), None)
            if image_path is None:
                continue

            ground_truth = _parse_ground_truth_json(doc_id, json.loads(json_path.read_text()))
            mime_type = "image/png" if image_path.suffix == ".png" else "image/jpeg"
            yield DatasetExample(
                doc_id=doc_id,
                document_bytes=image_path.read_bytes(),
                mime_type=mime_type,
                ground_truth=ground_truth,
            )
