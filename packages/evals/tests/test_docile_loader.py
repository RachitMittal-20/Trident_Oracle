import json

import pytest
from evals.datasets.docile import DocileLoader
from evals.errors import DatasetFormatError, DatasetNotFoundError


def _build_docile_root(tmp_path):
    root = tmp_path / "docile"
    (root / "annotations").mkdir(parents=True)
    (root / "pdfs").mkdir(parents=True)

    (root / "val.json").write_text(json.dumps(["doc-1"]))
    (root / "pdfs" / "doc-1.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "annotations" / "doc-1.json").write_text(
        json.dumps(
            {
                # Matches a real downloaded DocILE export: header (KILE)
                # fields and line-item (LIR) fields are two separate
                # top-level lists, never merged, and line_item_id never
                # appears inside field_extractions.
                "field_extractions": [
                    {"fieldtype": "date_issue", "text": "2026-08-12"},
                    {"fieldtype": "vendor_name", "text": "Acme Supply Co."},
                    {"fieldtype": "amount_total_gross", "text": "450.47"},
                    {"fieldtype": "customer_tax_id", "text": "TAX-999"},
                ],
                "line_item_extractions": [
                    {
                        "fieldtype": "line_item_description",
                        "text": "Steel bracket",
                        "page": 0,
                        "line_item_id": 0,
                    },
                    {"fieldtype": "line_item_quantity", "text": "40", "page": 0, "line_item_id": 0},
                    {
                        "fieldtype": "line_item_description",
                        "text": "Hydraulic hose",
                        "page": 0,
                        "line_item_id": 1,
                    },
                    {"fieldtype": "line_item_quantity", "text": "12", "page": 0, "line_item_id": 1},
                ],
                "line_item_headers": [],
            }
        )
    )
    return root


def test_docile_loader_missing_root_raises_dataset_not_found(tmp_path) -> None:
    with pytest.raises(DatasetNotFoundError):
        DocileLoader(tmp_path / "does-not-exist")


def test_docile_loader_maps_header_and_line_fields(tmp_path) -> None:
    root = _build_docile_root(tmp_path)
    loader = DocileLoader(root, split="val")

    examples = list(loader)

    assert len(examples) == 1
    example = examples[0]
    assert example.doc_id == "doc-1"
    assert example.mime_type == "application/pdf"
    assert example.ground_truth.header.invoice_date == "2026-08-12"
    assert example.ground_truth.header.vendor_name == "Acme Supply Co."
    assert example.ground_truth.header.total == "450.47"
    # No DocILE field maps to invoice_number -- always None, never
    # approximated from document_id (see module docstring).
    assert example.ground_truth.header.invoice_number is None


def test_docile_loader_preserves_unmapped_fields(tmp_path) -> None:
    root = _build_docile_root(tmp_path)
    loader = DocileLoader(root, split="val")

    example = next(iter(loader))

    assert example.ground_truth.unmapped_fields["customer_tax_id"] == "TAX-999"
    assert example.ground_truth.unmapped_fields["document_id"] == "doc-1"


def test_docile_loader_groups_line_items_by_line_item_id(tmp_path) -> None:
    root = _build_docile_root(tmp_path)
    loader = DocileLoader(root, split="val")

    example = next(iter(loader))

    assert len(example.ground_truth.line_items) == 2
    assert example.ground_truth.line_items[0].description == "Steel bracket"
    assert example.ground_truth.line_items[0].qty == "40"
    assert example.ground_truth.line_items[1].description == "Hydraulic hose"
    assert example.ground_truth.line_items[1].qty == "12"


def test_docile_loader_missing_split_file_raises_format_error(tmp_path) -> None:
    root = tmp_path / "docile"
    root.mkdir()
    loader = DocileLoader(root, split="val")

    with pytest.raises(DatasetFormatError):
        list(loader)


def test_docile_loader_skips_documents_missing_pdf_or_annotation(tmp_path) -> None:
    root = _build_docile_root(tmp_path)
    (root / "val.json").write_text(json.dumps(["doc-1", "doc-missing"]))

    examples = list(DocileLoader(root, split="val"))

    assert [e.doc_id for e in examples] == ["doc-1"]
