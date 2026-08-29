import json

import pytest
from evals.datasets.sroie import SroieLoader
from evals.errors import DatasetNotFoundError


def _build_sroie_root(tmp_path):
    root = tmp_path / "sroie"
    (root / "img").mkdir(parents=True)
    (root / "entities").mkdir(parents=True)

    (root / "img" / "receipt-1.jpg").write_bytes(b"\xff\xd8 fake jpeg")
    (root / "entities" / "receipt-1.txt").write_text(
        json.dumps(
            {
                "company": "ACME SUPPLY SDN BHD",
                "date": "12/08/2026",
                "address": "1 Jalan Test, Kuala Lumpur",
                "total": "450.47",
            }
        )
    )
    return root


def test_sroie_loader_missing_root_raises_dataset_not_found(tmp_path) -> None:
    with pytest.raises(DatasetNotFoundError):
        SroieLoader(tmp_path / "does-not-exist")


def test_sroie_loader_maps_the_four_fields(tmp_path) -> None:
    root = _build_sroie_root(tmp_path)
    example = next(iter(SroieLoader(root)))

    assert example.ground_truth.header.vendor_name == "ACME SUPPLY SDN BHD"
    assert example.ground_truth.header.invoice_date == "12/08/2026"
    assert example.ground_truth.header.total == "450.47"


def test_sroie_loader_has_no_line_items_ever(tmp_path) -> None:
    root = _build_sroie_root(tmp_path)
    example = next(iter(SroieLoader(root)))

    assert example.ground_truth.line_items == ()


def test_sroie_loader_has_no_subtotal_tax_due_date_or_invoice_number(tmp_path) -> None:
    root = _build_sroie_root(tmp_path)
    example = next(iter(SroieLoader(root)))

    assert example.ground_truth.header.subtotal is None
    assert example.ground_truth.header.tax is None
    assert example.ground_truth.header.due_date is None
    assert example.ground_truth.header.invoice_number is None


def test_sroie_loader_preserves_address_as_unmapped(tmp_path) -> None:
    root = _build_sroie_root(tmp_path)
    example = next(iter(SroieLoader(root)))

    assert example.ground_truth.unmapped_fields["address"] == "1 Jalan Test, Kuala Lumpur"


def test_sroie_loader_skips_entities_without_matching_image(tmp_path) -> None:
    root = _build_sroie_root(tmp_path)
    orphan_entities = {"company": "x", "date": "x", "total": "x"}
    (root / "entities" / "orphan.txt").write_text(json.dumps(orphan_entities))

    examples = list(SroieLoader(root))

    assert [e.doc_id for e in examples] == ["receipt-1"]
