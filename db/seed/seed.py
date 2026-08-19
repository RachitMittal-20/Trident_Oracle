"""Populate a realistic demo dataset for Trident Oracle.

Usage:
    DATABASE_URL=postgresql://... uv run python db/seed/seed.py

Requires a connection with enough privilege to bypass RLS (the Supabase
`postgres`/service-role connection, not an RLS-scoped app role) -- this script
seeds data for a specific tenant directly, it does not go through the
request-scoped `app.tenant_id` path that application code uses.

Idempotent: every row's id is a UUIDv5 deterministically derived from a stable
natural key (e.g. "vendor:2", "po-line:PO-2026-1007:3"), and every insert uses
`ON CONFLICT (id) DO NOTHING`. Running this script any number of times against
the same database produces the same rows -- it never duplicates or overwrites.

Every generated row is constructed as a packages/core dataclass first (Tenant,
Vendor, PurchaseOrder, ...) before being written. core does no I/O itself, but
its __post_init__ validation is exactly the safety net this script wants: if
the generation logic below ever produces a negative quantity or a total that
doesn't reconcile, construction raises and the whole seed aborts loudly
instead of writing bad data.
"""

import os
import random
import sys
import uuid
from collections.abc import Sequence
from dataclasses import astuple
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

import psycopg
from core.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    Tenant,
    TolerancePolicy,
    Vendor,
)
from psycopg.types.json import Jsonb

SEED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://trident-oracle.example/seed")


def seed_id(key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, key)


def naive_normalize(name: str) -> str:
    """A rough stand-in for the real vendor-normalization function (not yet
    implemented in packages/core). Lowercases and strips punctuation only --
    deliberately does NOT expand corporate suffixes, so some of the messy
    vendor variants below still collide and some deliberately don't. That gap
    is exactly what a real normalizer has work to do."""
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in name.lower())
    return " ".join(cleaned.split())


TENANT_NAME = "Doritech Demo"
TENANT_SLUG = "doritech-demo"

VENDOR_NAMES = [
    "ACME Corp.",
    "Acme Corporation",
    "ACME CORP",
    "Northwind Traders LLC",
    "Northwind Traders, LLC.",
    "Global Fasteners Inc.",
    "BlueSky Logistics",
    "Bluesky Logistics Pvt. Ltd.",
]

CATALOG: list[tuple[str, str, Decimal]] = [
    ("BRK-2210", "Steel bracket, 4in, zinc-plated", Decimal("3.25")),
    ("HSE-0091", "Hydraulic hose, 10ft", Decimal("18.75")),
    ("GSK-3305", "Rubber gasket, 6in flange", Decimal("1.10")),
    ("BLT-1102", "Hex bolt, M8x40, stainless", Decimal("0.45")),
    ("VLV-7788", "Ball valve, 1in NPT", Decimal("22.50")),
    ("PMP-4021", "Centrifugal pump seal kit", Decimal("64.00")),
    ("WIR-9012", "Copper wire, 12AWG, 100ft spool", Decimal("38.90")),
    ("FLT-5541", "Inline air filter, 2in", Decimal("9.15")),
    ("PIP-6630", "PVC pipe, 2in, 10ft length", Decimal("12.40")),
    ("SNS-3399", "Pressure sensor, 0-200 PSI", Decimal("47.75")),
    ("CBL-8820", "Control cable, 5m", Decimal("15.60")),
    ("BRG-2244", "Ball bearing, 6205-2RS", Decimal("6.80")),
    ("FUS-1190", "Fuse, 30A, automotive", Decimal("2.15")),
    ("CNT-4456", "Electrical connector, 4-pin", Decimal("3.90")),
    ("TNK-9987", "Storage tank fitting, 3in", Decimal("28.30")),
]

TAX_RATE = Decimal("7.00")
START_DATE = date(2026, 5, 1)
N_PURCHASE_ORDERS = 25
N_RECEIPTED = 20


def build_tenant() -> Tenant:
    return Tenant(
        id=seed_id("tenant:doritech-demo"),
        name=TENANT_NAME,
        slug=TENANT_SLUG,
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )


def build_users(tenant_id: uuid.UUID) -> list[tuple[uuid.UUID, uuid.UUID, str, str, datetime]]:
    # (id, tenant_id, email, role, created_at) -- users has no core dataclass
    # (it's an auth-adjacent table, not part of the matching domain), so this
    # stays a plain tuple list rather than going through core.
    created_at = datetime(2026, 5, 1, tzinfo=UTC)
    return [
        (seed_id("user:admin"), tenant_id, "admin@doritech-demo.example", "admin", created_at),
        (
            seed_id("user:approver"),
            tenant_id,
            "approver@doritech-demo.example",
            "approver",
            created_at,
        ),
        (seed_id("user:clerk"), tenant_id, "clerk@doritech-demo.example", "clerk", created_at),
    ]


def build_vendors(tenant_id: uuid.UUID) -> list[Vendor]:
    created_at = datetime(2026, 5, 1, tzinfo=UTC)
    return [
        Vendor(
            id=seed_id(f"vendor:{i}"),
            tenant_id=tenant_id,
            name=name,
            normalized_name=naive_normalize(name),
            created_at=created_at,
        )
        for i, name in enumerate(VENDOR_NAMES)
    ]


def build_purchase_orders(
    tenant_id: uuid.UUID, vendors: list[Vendor]
) -> tuple[list[PurchaseOrder], dict[str, list[PurchaseOrderLine]]]:
    orders: list[PurchaseOrder] = []
    lines_by_po: dict[str, list[PurchaseOrderLine]] = {}

    for i in range(N_PURCHASE_ORDERS):
        po_number = f"PO-2026-{1000 + i}"
        vendor = vendors[i % len(vendors)]
        issued_at = datetime.combine(
            START_DATE + timedelta(days=i * 3), datetime.min.time(), tzinfo=UTC
        )

        rng = random.Random(1000 + i)
        n_lines = rng.randint(3, 8)
        skus = rng.sample(CATALOG, k=n_lines)

        po_id = seed_id(f"po:{po_number}")
        lines: list[PurchaseOrderLine] = []
        for line_no, (sku, description, unit_price) in enumerate(skus, start=1):
            qty_ordered = Decimal(rng.randint(5, 60))
            line_total = qty_ordered * unit_price
            lines.append(
                PurchaseOrderLine(
                    id=seed_id(f"po-line:{po_number}:{line_no}"),
                    tenant_id=tenant_id,
                    po_id=po_id,
                    line_no=line_no,
                    description=description,
                    normalized_description=naive_normalize(description),
                    qty_ordered=qty_ordered,
                    unit_price=unit_price,
                    tax_rate=TAX_RATE,
                    line_total=line_total,
                    created_at=issued_at,
                    sku=sku,
                )
            )

        subtotal = sum((line.line_total for line in lines), start=Decimal("0"))
        tax = (subtotal * TAX_RATE / Decimal("100")).quantize(Decimal("0.01"))
        total = subtotal + tax

        has_receipt = i < N_RECEIPTED
        status: Literal["open", "partially_received", "closed", "cancelled"]
        if not has_receipt:
            status = "open"
        elif i < 5 or (5 <= i < 15):
            status = "closed"
        else:
            status = "partially_received"

        orders.append(
            PurchaseOrder(
                id=po_id,
                tenant_id=tenant_id,
                vendor_id=vendor.id,
                po_number=po_number,
                issued_at=issued_at,
                currency="USD",
                subtotal=subtotal,
                tax=tax,
                total=total,
                status=status,
                created_at=issued_at,
            )
        )
        lines_by_po[po_number] = lines

    return orders, lines_by_po


def build_goods_receipts(
    tenant_id: uuid.UUID,
    orders: list[PurchaseOrder],
    lines_by_po: dict[str, list[PurchaseOrderLine]],
    clerk_id: uuid.UUID,
) -> tuple[list[GoodsReceipt], list[GoodsReceiptLine]]:
    receipts: list[GoodsReceipt] = []
    receipt_lines: list[GoodsReceiptLine] = []

    # Cycle through four delivery scenarios across the 20 receipted POs so
    # the demo has a real spread: exact / short / over / damaged.
    scenarios = ["exact", "exact", "exact", "exact", "exact"]
    scenarios += ["short"] * 5
    scenarios += ["over"] * 5
    scenarios += ["damaged"] * 5

    for i, po in enumerate(orders[:N_RECEIPTED]):
        scenario = scenarios[i]
        po_lines = lines_by_po[po.po_number]
        received_at = po.issued_at + timedelta(days=5 + (i % 4))
        grn_id = seed_id(f"grn:{po.po_number}")

        receipts.append(
            GoodsReceipt(
                id=grn_id,
                tenant_id=tenant_id,
                po_id=po.id,
                grn_number=f"GRN-{po.po_number}",
                received_at=received_at,
                received_by=clerk_id,
                created_at=received_at,
            )
        )

        for idx, pol in enumerate(po_lines):
            qty_received = pol.qty_ordered
            condition: Literal["good", "damaged", "partial"] = "good"

            if idx == 0 and scenario == "short":
                shortfall = min(Decimal("2"), pol.qty_ordered - Decimal("1"))
                qty_received = pol.qty_ordered - shortfall
            elif idx == 0 and scenario == "over":
                qty_received = pol.qty_ordered + Decimal("3")
            elif idx == 0 and scenario == "damaged":
                damage = min(Decimal("1"), pol.qty_ordered - Decimal("1"))
                qty_received = pol.qty_ordered - damage
                condition = "damaged"

            receipt_lines.append(
                GoodsReceiptLine(
                    id=seed_id(f"grn-line:{po.po_number}:{pol.line_no}"),
                    tenant_id=tenant_id,
                    grn_id=grn_id,
                    po_line_id=pol.id,
                    qty_received=qty_received,
                    condition=condition,
                    created_at=received_at,
                )
            )

    return receipts, receipt_lines


def build_tolerance_policy(tenant_id: uuid.UUID) -> TolerancePolicy:
    return TolerancePolicy(
        id=seed_id("tolerance-policy:default"),
        tenant_id=tenant_id,
        name="Default Policy",
        is_active=True,
        version=1,
        price_variance_pct=Decimal("2.0"),
        qty_tolerance_pct=Decimal("0.0"),
        auto_approve_below=Decimal("5000"),
        dual_approval_above=Decimal("100000"),
        min_field_confidence=Decimal("0.85"),
        duplicate_window_days=90,
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )


def tolerance_policy_rules(policy: TolerancePolicy) -> dict[str, float | int]:
    """tolerance_policies stores its rule fields as a single `rules` jsonb
    column (0006_matching.sql), not individual columns -- the core dataclass
    exposes them as typed fields for validation, this converts back to the
    DB's storage shape."""
    return {
        "price_variance_pct": float(policy.price_variance_pct),
        "qty_tolerance_pct": float(policy.qty_tolerance_pct),
        "auto_approve_below": float(policy.auto_approve_below),
        "dual_approval_above": float(policy.dual_approval_above),
        "min_field_confidence": float(policy.min_field_confidence),
        "duplicate_window_days": policy.duplicate_window_days,
    }


def upsert(
    cur: psycopg.Cursor, table: str, columns: list[str], rows: Sequence[tuple[object, ...]]
) -> None:
    if not rows:
        return
    placeholders = ", ".join(["%s"] * len(columns))
    col_list = ", ".join(columns)
    sql = f"insert into {table} ({col_list}) values ({placeholders}) on conflict (id) do nothing"
    cur.executemany(sql, rows)


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        raise SystemExit(1)

    tenant = build_tenant()
    users = build_users(tenant.id)
    vendors = build_vendors(tenant.id)
    orders, lines_by_po = build_purchase_orders(tenant.id, vendors)
    all_po_lines = [line for lines in lines_by_po.values() for line in lines]
    clerk_id = next(u[0] for u in users if u[2] == "clerk@doritech-demo.example")
    receipts, receipt_lines = build_goods_receipts(tenant.id, orders, lines_by_po, clerk_id)
    policy = build_tolerance_policy(tenant.id)

    with psycopg.connect(database_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            upsert(cur, "tenants", ["id", "name", "slug", "created_at"], [astuple(tenant)])
            upsert(cur, "users", ["id", "tenant_id", "email", "role", "created_at"], users)
            upsert(
                cur,
                "vendors",
                ["id", "tenant_id", "name", "normalized_name", "created_at", "tax_id", "email"],
                [astuple(v) for v in vendors],
            )
            upsert(
                cur,
                "purchase_orders",
                [
                    "id",
                    "tenant_id",
                    "vendor_id",
                    "po_number",
                    "issued_at",
                    "currency",
                    "subtotal",
                    "tax",
                    "total",
                    "status",
                    "created_at",
                ],
                [astuple(po) for po in orders],
            )
            upsert(
                cur,
                "purchase_order_lines",
                [
                    "id",
                    "tenant_id",
                    "po_id",
                    "line_no",
                    "description",
                    "normalized_description",
                    "qty_ordered",
                    "unit_price",
                    "tax_rate",
                    "line_total",
                    "created_at",
                    "sku",
                ],
                [astuple(line) for line in all_po_lines],
            )
            upsert(
                cur,
                "goods_receipts",
                [
                    "id",
                    "tenant_id",
                    "po_id",
                    "grn_number",
                    "received_at",
                    "received_by",
                    "created_at",
                ],
                [astuple(gr) for gr in receipts],
            )
            upsert(
                cur,
                "goods_receipt_lines",
                [
                    "id",
                    "tenant_id",
                    "grn_id",
                    "po_line_id",
                    "qty_received",
                    "condition",
                    "created_at",
                    "notes",
                ],
                [astuple(grl) for grl in receipt_lines],
            )
            upsert(
                cur,
                "tolerance_policies",
                ["id", "tenant_id", "name", "is_active", "rules", "version", "created_at"],
                [
                    (
                        policy.id,
                        policy.tenant_id,
                        policy.name,
                        policy.is_active,
                        Jsonb(tolerance_policy_rules(policy)),
                        policy.version,
                        policy.created_at,
                    )
                ],
            )
        conn.commit()

    print(
        f"Seeded: 1 tenant, {len(users)} users, {len(vendors)} vendors, "
        f"{len(orders)} purchase orders, {len(all_po_lines)} PO lines, "
        f"{len(receipts)} goods receipts, {len(receipt_lines)} receipt lines, "
        f"1 tolerance policy."
    )


if __name__ == "__main__":
    main()
