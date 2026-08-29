"""SROIE (ICDAR 2019 Task 3, "Key Information Extraction") loader -- a
fallback dataset (see docile.py's docstring for why fallbacks exist).
Public, no registration needed, e.g. via the Kaggle mirror
"sroie-datasetv2".

Expected local layout:
    {root}/img/{id}.jpg
    {root}/entities/{id}.txt   -- JSON: {"company", "date", "address", "total"}

SROIE Task 3's ground truth has exactly four keys, full stop -- this is the
entire schema, not an excerpt. Field mapping:
    company -> header.vendor_name  (confident)
    date    -> header.invoice_date (confident)
    total   -> header.total        (confident)
    address -> kept in unmapped_fields; no field in our schema for a
               vendor's street address.

This is by a wide margin the weakest of the three loaders: no
invoice_number, no due_date, no currency, no subtotal, no tax, and -- most
significant for this project's own matching engine, which is built almost
entirely around line items -- NO LINE ITEMS AT ALL. SROIE Task 3 only ever
labels four whole-document fields; metrics.py's line-item precision/recall
will show 0 ground-truth lines for every SROIE example, which is a
statement about SROIE, not a bug. Use it as a bare vendor/date/total smoke
test only, never as evidence about line-item extraction quality.
"""

import json
from collections.abc import Iterator
from pathlib import Path

from evals.datasets.base import DatasetLoader
from evals.models import DatasetExample, GroundTruthDocument, GroundTruthHeader


class SroieLoader(DatasetLoader):
    name = "sroie"

    def __iter__(self) -> Iterator[DatasetExample]:
        img_dir = self.root / "img"
        entities_dir = self.root / "entities"
        for entity_path in sorted(entities_dir.glob("*.txt")):
            doc_id = entity_path.stem
            image_path = img_dir / f"{doc_id}.jpg"
            if not image_path.is_file():
                continue

            entities: dict[str, str] = json.loads(entity_path.read_text())
            unmapped = {"address": entities["address"]} if "address" in entities else {}

            yield DatasetExample(
                doc_id=doc_id,
                document_bytes=image_path.read_bytes(),
                mime_type="image/jpeg",
                ground_truth=GroundTruthDocument(
                    doc_id=doc_id,
                    header=GroundTruthHeader(
                        vendor_name=entities.get("company"),
                        invoice_date=entities.get("date"),
                        total=entities.get("total"),
                    ),
                    line_items=(),
                    unmapped_fields=unmapped,
                ),
            )
