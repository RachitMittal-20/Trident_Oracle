"""Seeds the exact PO/GRN/tolerance-policy data the four demo/fixtures/*
invoices are designed against -- see docs/DEMO.md for the full runbook this
supports.

Usage:
    DATABASE_URL=postgresql://... uv run python demo/seed_demo.py

Requires a connection that can bypass RLS (the Supabase service-role/direct
Postgres connection) -- same requirement and same reasoning as
db/seed/seed.py, which this script deliberately does NOT replace: seed.py
builds a large, realistic 25-PO dataset for browsing the dashboard; this
script builds the minimal, exact 3-PO/3-GRN dataset the demo runbook's four
specific invoices need, and is safe to run against the same tenant as
seed.py (disjoint natural keys -- "demo:po:..." vs seed.py's "po:...").

Idempotent, same technique as db/seed/seed.py: every id is a UUIDv5 derived
from a stable natural key, every insert is ON CONFLICT DO NOTHING. Running
this any number of times against the same database is safe.

This script does NOT create the four invoice rows themselves or upload the
fixture files -- that happens for real, live, through the actual running
app (POST /v1/invoices/upload, or dragging a file onto the pipeline
screen), which is the point of a demo. What it seeds is the PO/GRN ground
truth those uploads need to match against, plus the tolerance policy that
decides their outcome. See demo/link_po.py for the one manual step this
system doesn't automate yet (see docs/ROADMAP.md: no automatic PO linkage
from an uploaded invoice's extracted content exists) -- something has to
set invoices.po_id, and it isn't this script.
"""

import os
import sys
import uuid
from dataclasses import astuple
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
from core.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    TolerancePolicy,
    Vendor,
)
from psycopg.types.json import Jsonb

DEMO_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://trident-oracle.example/demo")


def demo_id(key: str) -> uuid.UUID:
    return uuid.uuid5(DEMO_NAMESPACE, key)


# Must match db/seed/seed.py's own id derivation exactly -- this script
# adds to that same demo tenant (and reuses its admin user as GRNs'
# received_by), it does not create either from scratch.
SEED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://trident-oracle.example/seed")


def seed_id(key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, key)


TENANT_ID = seed_id("tenant:doritech-demo")
ADMIN_USER_ID = seed_id("user:admin")

CREATED_AT = datetime(2026, 8, 1, tzinfo=UTC)


def _insert_vendor(cur: psycopg.Cursor, vendor: Vendor) -> None:
    # Column order matches core.models.Vendor's own field order exactly --
    # astuple() is positional, not by name.
    cur.execute(
        """
        INSERT INTO vendors (id, tenant_id, name, normalized_name, created_at, tax_id, email)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        astuple(vendor),
    )


def _insert_po(cur: psycopg.Cursor, po: PurchaseOrder) -> None:
    cur.execute(
        """
        INSERT INTO purchase_orders
            (id, tenant_id, vendor_id, po_number, issued_at, currency,
             subtotal, tax, total, status, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        astuple(po),
    )


def _insert_po_line(cur: psycopg.Cursor, line: PurchaseOrderLine) -> None:
    cur.execute(
        """
        INSERT INTO purchase_order_lines
            (id, tenant_id, po_id, line_no, description, normalized_description,
             qty_ordered, unit_price, tax_rate, line_total, created_at, sku)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        astuple(line),
    )


def _insert_grn(cur: psycopg.Cursor, grn: GoodsReceipt) -> None:
    cur.execute(
        """
        INSERT INTO goods_receipts
            (id, tenant_id, po_id, grn_number, received_at, received_by, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        astuple(grn),
    )


def _insert_grn_line(cur: psycopg.Cursor, line: GoodsReceiptLine) -> None:
    # Column order matches core.models.GoodsReceiptLine's own field order:
    # ..., qty_received, condition, created_at, notes.
    cur.execute(
        """
        INSERT INTO goods_receipt_lines
            (id, tenant_id, grn_id, po_line_id, qty_received, condition, created_at, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        astuple(line),
    )


def _insert_policy(cur: psycopg.Cursor, policy: TolerancePolicy) -> None:
    cur.execute(
        """
        INSERT INTO tolerance_policies
            (id, tenant_id, name, is_active, rules, version, created_at)
        VALUES (%(id)s, %(tenant_id)s, %(name)s, %(is_active)s, %(rules)s, %(version)s,
                %(created_at)s)
        ON CONFLICT (id) DO NOTHING
        """,
        {
            "id": policy.id,
            "tenant_id": policy.tenant_id,
            "name": policy.name,
            "is_active": policy.is_active,
            "rules": Jsonb(
                {
                    "price_variance_pct": str(policy.price_variance_pct),
                    "qty_tolerance_pct": str(policy.qty_tolerance_pct),
                    "auto_approve_below": str(policy.auto_approve_below),
                    "dual_approval_above": str(policy.dual_approval_above),
                    "min_field_confidence": str(policy.min_field_confidence),
                    "duplicate_window_days": policy.duplicate_window_days,
                }
            ),
            "version": policy.version,
            "created_at": policy.created_at,
        },
    )


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("error: DATABASE_URL must be set (service-role/direct connection)", file=sys.stderr)
        return 1

    vendors = [
        Vendor(
            id=demo_id("demo:vendor:northwind"),
            tenant_id=TENANT_ID,
            name="Northwind Traders",
            normalized_name="northwind traders",
            created_at=CREATED_AT,
        ),
        Vendor(
            id=demo_id("demo:vendor:acme"),
            tenant_id=TENANT_ID,
            name="Acme Supply",
            normalized_name="acme supply",
            created_at=CREATED_AT,
        ),
    ]

    # PO-3001 / GRN-3001 -- for fixtures 01 (clean) and 04 (duplicate of 01).
    po_3001 = PurchaseOrder(
        id=demo_id("demo:po:PO-3001"),
        tenant_id=TENANT_ID,
        vendor_id=vendors[0].id,
        po_number="PO-3001",
        issued_at=CREATED_AT,
        currency="USD",
        subtotal=Decimal("320.00"),
        tax=Decimal("0.00"),
        total=Decimal("320.00"),
        status="open",
        created_at=CREATED_AT,
    )
    po_3001_line = PurchaseOrderLine(
        id=demo_id("demo:po-line:PO-3001:1"),
        tenant_id=TENANT_ID,
        po_id=po_3001.id,
        line_no=1,
        description="Bracket",
        normalized_description="bracket",
        qty_ordered=Decimal("8"),
        unit_price=Decimal("40.00"),
        tax_rate=Decimal("0"),
        line_total=Decimal("320.00"),
        created_at=CREATED_AT,
        sku=None,
    )
    grn_3001 = GoodsReceipt(
        id=demo_id("demo:grn:GRN-3001"),
        tenant_id=TENANT_ID,
        po_id=po_3001.id,
        grn_number="GRN-3001",
        received_at=CREATED_AT,
        received_by=ADMIN_USER_ID,
        created_at=CREATED_AT,
    )
    grn_3001_line = GoodsReceiptLine(
        id=demo_id("demo:grn-line:GRN-3001:1"),
        tenant_id=TENANT_ID,
        grn_id=grn_3001.id,
        po_line_id=po_3001_line.id,
        qty_received=Decimal("8"),  # matches qty_ordered exactly -- clean
        condition="good",
        created_at=CREATED_AT,
    )

    # PO-3002 / GRN-3002 -- for fixture 02 (over-billed: invoice bills 12,
    # only 9 actually received).
    po_3002 = PurchaseOrder(
        id=demo_id("demo:po:PO-3002"),
        tenant_id=TENANT_ID,
        vendor_id=vendors[1].id,
        po_number="PO-3002",
        issued_at=CREATED_AT,
        currency="USD",
        subtotal=Decimal("180.00"),
        tax=Decimal("0.00"),
        total=Decimal("180.00"),
        status="open",
        created_at=CREATED_AT,
    )
    po_3002_line = PurchaseOrderLine(
        id=demo_id("demo:po-line:PO-3002:1"),
        tenant_id=TENANT_ID,
        po_id=po_3002.id,
        line_no=1,
        description="Gasket",
        normalized_description="gasket",
        qty_ordered=Decimal("12"),
        unit_price=Decimal("15.00"),
        tax_rate=Decimal("0"),
        line_total=Decimal("180.00"),
        created_at=CREATED_AT,
        sku=None,
    )
    grn_3002 = GoodsReceipt(
        id=demo_id("demo:grn:GRN-3002"),
        tenant_id=TENANT_ID,
        po_id=po_3002.id,
        grn_number="GRN-3002",
        received_at=CREATED_AT,
        received_by=ADMIN_USER_ID,
        created_at=CREATED_AT,
    )
    grn_3002_line = GoodsReceiptLine(
        id=demo_id("demo:grn-line:GRN-3002:1"),
        tenant_id=TENANT_ID,
        grn_id=grn_3002.id,
        po_line_id=po_3002_line.id,
        qty_received=Decimal("9"),  # invoice bills 12 -- QTY_OVER
        condition="good",
        created_at=CREATED_AT,
    )

    # PO-3003 / GRN-3003 -- for fixture 03 (blurry photo). Would otherwise
    # match cleanly; the point of this scenario is that low extraction
    # confidence overrides that before the match result is even consulted.
    po_3003 = PurchaseOrder(
        id=demo_id("demo:po:PO-3003"),
        tenant_id=TENANT_ID,
        vendor_id=vendors[0].id,
        po_number="PO-3003",
        issued_at=CREATED_AT,
        currency="USD",
        subtotal=Decimal("100.00"),
        tax=Decimal("0.00"),
        total=Decimal("100.00"),
        status="open",
        created_at=CREATED_AT,
    )
    po_3003_line = PurchaseOrderLine(
        id=demo_id("demo:po-line:PO-3003:1"),
        tenant_id=TENANT_ID,
        po_id=po_3003.id,
        line_no=1,
        description="Cable",
        normalized_description="cable",
        qty_ordered=Decimal("5"),
        unit_price=Decimal("20.00"),
        tax_rate=Decimal("0"),
        line_total=Decimal("100.00"),
        created_at=CREATED_AT,
        sku=None,
    )
    grn_3003 = GoodsReceipt(
        id=demo_id("demo:grn:GRN-3003"),
        tenant_id=TENANT_ID,
        po_id=po_3003.id,
        grn_number="GRN-3003",
        received_at=CREATED_AT,
        received_by=ADMIN_USER_ID,
        created_at=CREATED_AT,
    )
    grn_3003_line = GoodsReceiptLine(
        id=demo_id("demo:grn-line:GRN-3003:1"),
        tenant_id=TENANT_ID,
        grn_id=grn_3003.id,
        po_line_id=po_3003_line.id,
        qty_received=Decimal("5"),
        condition="good",
        created_at=CREATED_AT,
    )

    # min_field_confidence=0.85: fixtures 01/02 (TesseractExtractor min
    # field confidence ~0.88-0.92, verified against the real extractor)
    # clear this; fixture 03's genuine blur does not (~0.0), by design.
    #
    # version=2, not 1: apps/worker/worker/match_handler.py selects the
    # active policy via `ORDER BY version DESC LIMIT 1`, and db/seed/seed.py
    # already seeds its own active, version=1 policy for this same demo
    # tenant. version=2 here deterministically wins that ordering rather
    # than leaving two version=1 rows in an arbitrary tie.
    policy = TolerancePolicy(
        id=demo_id("demo:policy:default"),
        tenant_id=TENANT_ID,
        name="demo-default",
        is_active=True,
        version=2,
        price_variance_pct=Decimal("2"),
        qty_tolerance_pct=Decimal("0"),
        auto_approve_below=Decimal("5000"),
        dual_approval_above=Decimal("100000"),
        min_field_confidence=Decimal("0.85"),
        duplicate_window_days=90,
        created_at=CREATED_AT,
    )

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for vendor in vendors:
                _insert_vendor(cur, vendor)
            for po, line, grn, grn_line in (
                (po_3001, po_3001_line, grn_3001, grn_3001_line),
                (po_3002, po_3002_line, grn_3002, grn_3002_line),
                (po_3003, po_3003_line, grn_3003, grn_3003_line),
            ):
                _insert_po(cur, po)
                _insert_po_line(cur, line)
                _insert_grn(cur, grn)
                _insert_grn_line(cur, grn_line)
            _insert_policy(cur, policy)
        conn.commit()

    print(f"Seeded demo PO/GRN data for tenant {TENANT_ID}")
    print("  PO-3001 (Northwind Traders, Bracket x8) -> matches 01-clean-invoice.png")
    print("  PO-3002 (Acme Supply, Gasket 12 ordered/9 received) -> matches 02-overbilled.png")
    print("  PO-3003 (Northwind Traders, Cable x5) -> matches 03-blurry-photo.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
