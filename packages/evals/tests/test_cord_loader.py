import json

import pytest
from evals.datasets.cord import CordLoader
from evals.errors import DatasetNotFoundError


def _build_cord_root(tmp_path):
    root = tmp_path / "cord"
    (root / "test" / "image").mkdir(parents=True)
    (root / "test" / "json").mkdir(parents=True)

    (root / "test" / "image" / "receipt-1.png").write_bytes(b"\x89PNG fake")
    (root / "test" / "json" / "receipt-1.json").write_text(
        json.dumps(
            {
                "valid_line": [
                    {
                        "words": [{"text": "Steel"}, {"text": "bracket"}],
                        "category": "menu.nm",
                        "group_id": 0,
                    },
                    {"words": [{"text": "2"}], "category": "menu.cnt", "group_id": 0},
                    {"words": [{"text": "3.25"}], "category": "menu.unitprice", "group_id": 0},
                    {"words": [{"text": "6.50"}], "category": "menu.price", "group_id": 0},
                    {"words": [{"text": "Hose"}], "category": "menu.nm", "group_id": 1},
                    {
                        "words": [{"text": "6.50"}],
                        "category": "sub_total.subtotal_price",
                        "group_id": None,
                    },
                    {
                        "words": [{"text": "0.46"}],
                        "category": "sub_total.tax_price",
                        "group_id": None,
                    },
                    {
                        "words": [{"text": "6.96"}],
                        "category": "total.total_price",
                        "group_id": None,
                    },
                    {"words": [{"text": "10001"}], "category": "menu.num", "group_id": 0},
                ]
            }
        )
    )
    return root


def test_cord_loader_missing_root_raises_dataset_not_found(tmp_path) -> None:
    with pytest.raises(DatasetNotFoundError):
        CordLoader(tmp_path / "does-not-exist")


def test_cord_loader_reconstructs_multi_word_line_text(tmp_path) -> None:
    root = _build_cord_root(tmp_path)
    example = next(iter(CordLoader(root, split="test")))

    assert example.ground_truth.line_items[0].description == "Steel bracket"


def test_cord_loader_maps_header_totals(tmp_path) -> None:
    root = _build_cord_root(tmp_path)
    example = next(iter(CordLoader(root, split="test")))

    assert example.ground_truth.header.subtotal == "6.50"
    assert example.ground_truth.header.tax == "0.46"
    assert example.ground_truth.header.total == "6.96"


def test_cord_loader_has_no_vendor_or_invoice_number_fields(tmp_path) -> None:
    # CORD's schema has no such categories at all -- always None, not
    # merely unpopulated on this particular receipt.
    root = _build_cord_root(tmp_path)
    example = next(iter(CordLoader(root, split="test")))

    assert example.ground_truth.header.vendor_name is None
    assert example.ground_truth.header.invoice_number is None
    assert example.ground_truth.header.due_date is None


def test_cord_loader_groups_line_items_by_group_id(tmp_path) -> None:
    root = _build_cord_root(tmp_path)
    example = next(iter(CordLoader(root, split="test")))

    assert len(example.ground_truth.line_items) == 2
    first = example.ground_truth.line_items[0]
    assert first.description == "Steel bracket"
    assert first.qty == "2"
    assert first.unit_price == "3.25"
    assert first.line_total == "6.50"


def test_cord_loader_preserves_unmapped_categories(tmp_path) -> None:
    root = _build_cord_root(tmp_path)
    example = next(iter(CordLoader(root, split="test")))

    assert example.ground_truth.unmapped_fields["menu.num"] == "10001"


def test_cord_loader_skips_json_without_matching_image(tmp_path) -> None:
    root = _build_cord_root(tmp_path)
    (root / "test" / "json" / "orphan.json").write_text(json.dumps({"valid_line": []}))

    examples = list(CordLoader(root, split="test"))

    assert [e.doc_id for e in examples] == ["receipt-1"]
