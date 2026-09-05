"""Populate a realistic demo dataset for Trident Oracle.

Usage:
    RESET_SCRIPT_DATABASE_URL=postgresql://... uv run python db/seed/seed.py

Requires a connection with enough privilege to bypass RLS (the Supabase
`postgres`/service-role connection, not an RLS-scoped app role) -- this script
seeds data for a specific tenant directly, it does not go through the
request-scoped `app.tenant_id` path that application code uses. Deliberately
reads RESET_SCRIPT_DATABASE_URL, not DATABASE_URL: the app's own DATABASE_URL
is app_role, which has SELECT-only on `tenants` and would fail here exactly
as it does for demo/reset.py's DELETEs.

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

import hashlib
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
    ExceptionType,
    GoodsReceipt,
    GoodsReceiptLine,
    Invoice,
    InvoiceStatus,
    MatchException,
    PurchaseOrder,
    PurchaseOrderLine,
    Severity,
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
    # Not a messy-duplicate pair like the eight above -- this one exists
    # purely so /analytics has a vendor with a genuinely high exception
    # rate to flag ("which of my suppliers keeps overbilling me"), per
    # build_analytics_dataset() below.
    "Globex Manufacturing",
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


def build_users(
    tenant_id: uuid.UUID,
) -> list[tuple[uuid.UUID, uuid.UUID, str, str, datetime, str | None]]:
    # (id, tenant_id, email, role, created_at, telegram_chat_id) -- users has
    # no core dataclass (it's an auth-adjacent table, not part of the
    # matching domain), so this stays a plain tuple list rather than going
    # through core.
    #
    # telegram_chat_id is a placeholder value, deliberately not a real chat
    # id: seed.py is committed to version control and meant to be a
    # repeatable fixture, not tied to any one developer's personal Telegram
    # account. Replace it with your own bot's chat id (see CLAUDE.md's
    # Telegram env vars) to exercise the live notify pipeline against this
    # seeded approver.
    created_at = datetime(2026, 5, 1, tzinfo=UTC)
    return [
        (
            seed_id("user:admin"),
            tenant_id,
            "admin@doritech-demo.example",
            "admin",
            created_at,
            None,
        ),
        (
            seed_id("user:approver"),
            tenant_id,
            "approver@doritech-demo.example",
            "approver",
            created_at,
            "000000001",
        ),
        (
            seed_id("user:clerk"),
            tenant_id,
            "clerk@doritech-demo.example",
            "clerk",
            created_at,
            None,
        ),
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


# --- Analytics (invoices, matching/notification activity) --------------------
#
# Everything above this point is procurement reference data (vendors, POs,
# GRNs) with fixed calendar dates -- reproducible forever, since nothing
# reads them relative to "now". /analytics reads the opposite way: every
# query filters on a rolling `now() - N days` window (apps/api/api/
# analytics_view.py), so seed data anchored to a fixed past date would
# eventually age out of every window and the dashboard would go back to
# looking empty on a fresh clone months from now. These rows are anchored
# to the seed run's own `now()` instead -- the one deliberate departure
# from this file's otherwise-fixed-date convention, and the reason this
# section keeps its own `_now` rather than reusing START_DATE.
#
# None of match_runs/match_exceptions/jobs/notification_deliveries/
# dead_letters/audit_log have a packages/core dataclass (core models the
# pure matching domain; these are operational/infra rows -- a queue entry,
# an audit trail line -- same reason build_users() below is a plain tuple
# list too), so they're built as plain tuples. invoices and match_exceptions
# do have core dataclasses and go through them first for the same reason
# the module docstring gives for POs/GRNs: __post_init__ validation is a
# safety net against generating something that doesn't reconcile.

ANALYTICS_LOW_EXCEPTION_VENDOR = 0  # "ACME Corp." -- steady, few exceptions
ANALYTICS_HIGH_EXCEPTION_VENDOR = 8  # "Globex Manufacturing" -- flagged by design

# (n, vendor index into VENDOR_NAMES, day offset before the seed run, status,
# overall_confidence, total)
ANALYTICS_INVOICE_SPECS: list[tuple[int, int, int, str, float | None, str]] = [
    (1, ANALYTICS_LOW_EXCEPTION_VENDOR, 19, "AUTO_POSTED", 0.97, "420.00"),
    (2, ANALYTICS_LOW_EXCEPTION_VENDOR, 18, "AUTO_POSTED", 0.95, "315.50"),
    (3, ANALYTICS_LOW_EXCEPTION_VENDOR, 17, "POSTED", 0.91, "980.00"),
    (4, ANALYTICS_LOW_EXCEPTION_VENDOR, 16, "AUTO_POSTED", 0.98, "210.00"),
    (5, ANALYTICS_LOW_EXCEPTION_VENDOR, 15, "MATCHED_CLEAN", 0.93, "540.00"),
    (6, ANALYTICS_LOW_EXCEPTION_VENDOR, 14, "EXCEPTIONS_RAISED", 0.88, "1250.00"),
    (7, ANALYTICS_LOW_EXCEPTION_VENDOR, 13, "AUTO_POSTED", 0.96, "175.25"),
    (8, ANALYTICS_LOW_EXCEPTION_VENDOR, 12, "PENDING_APPROVAL", 0.82, "3200.00"),
    (9, ANALYTICS_LOW_EXCEPTION_VENDOR, 11, "APPROVED", 0.90, "640.00"),
    (10, ANALYTICS_LOW_EXCEPTION_VENDOR, 10, "AUTO_POSTED", 0.99, "88.00"),
    (11, ANALYTICS_LOW_EXCEPTION_VENDOR, 8, "AUTO_POSTED", 0.94, "460.00"),
    (12, ANALYTICS_LOW_EXCEPTION_VENDOR, 6, "NEEDS_VERIFICATION", 0.58, "720.00"),
    (13, ANALYTICS_LOW_EXCEPTION_VENDOR, 4, "AUTO_POSTED", 0.97, "300.00"),
    (14, ANALYTICS_LOW_EXCEPTION_VENDOR, 2, "RECEIVED", None, "150.00"),
    (15, ANALYTICS_HIGH_EXCEPTION_VENDOR, 19, "EXCEPTIONS_RAISED", 0.85, "2100.00"),
    (16, ANALYTICS_HIGH_EXCEPTION_VENDOR, 17, "REJECTED", 0.80, "1875.00"),
    (17, ANALYTICS_HIGH_EXCEPTION_VENDOR, 16, "AUTO_POSTED", 0.93, "260.00"),
    (18, ANALYTICS_HIGH_EXCEPTION_VENDOR, 14, "PENDING_APPROVAL", 0.78, "4500.00"),
    (19, ANALYTICS_HIGH_EXCEPTION_VENDOR, 12, "EXCEPTIONS_RAISED", 0.86, "990.00"),
    (20, ANALYTICS_HIGH_EXCEPTION_VENDOR, 10, "APPROVED", 0.89, "610.00"),
    (21, ANALYTICS_HIGH_EXCEPTION_VENDOR, 9, "REJECTED", 0.75, "2250.00"),
    (22, ANALYTICS_HIGH_EXCEPTION_VENDOR, 7, "AUTO_POSTED", 0.92, "340.00"),
    (23, ANALYTICS_HIGH_EXCEPTION_VENDOR, 5, "EXTRACTION_FAILED", None, "0.00"),
    (24, ANALYTICS_HIGH_EXCEPTION_VENDOR, 3, "AUTO_POSTED", 0.95, "410.00"),
]

# invoice n -> (exception_type, severity, expected, actual, delta_pct, status, detail)
_ExceptionSpec = tuple[str, str, str | None, str | None, str | None, str, str]
ANALYTICS_EXCEPTION_SPECS: dict[int, _ExceptionSpec] = {
    6: (
        "PRICE_VARIANCE", "warn", "120.00", "150.00", "25.0", "open",
        "Unit price on line 1 is 25% above the purchase order price.",
    ),
    8: (
        "QTY_OVER", "warn", None, None, None, "open",
        "Invoiced quantity exceeds the goods receipt.",
    ),
    15: (
        "PRICE_VARIANCE", "block", "200.00", "310.00", "55.0", "open",
        "Unit price on line 1 is 55% above the purchase order price -- well outside tolerance.",
    ),
    16: (
        "DUPLICATE_INVOICE", "block", None, None, None, "dismissed",
        "Duplicate of a previously posted invoice.",
    ),
    18: (
        "PRICE_VARIANCE", "block", "500.00", "890.00", "78.0", "open",
        "Unit price on line 1 is 78% above the purchase order price -- well outside tolerance.",
    ),
    19: (
        "PRICE_VARIANCE", "warn", "90.00", "108.00", "20.0", "open",
        "Unit price on line 1 is 20% above the purchase order price.",
    ),
    21: (
        "NO_GRN", "block", None, None, None, "dismissed",
        "No goods receipt on file for this purchase order.",
    ),
}

# invoice n -> hours from creation to the audit_log 'approval_decided' entry.
ANALYTICS_DECISION_HOURS: dict[int, float] = {9: 4, 16: 26, 20: 6, 21: 3}


def build_analytics_dataset(
    tenant_id: uuid.UUID, vendors: list[Vendor], approver_id: uuid.UUID
) -> dict[str, list[tuple[object, ...]]]:
    now = datetime.now(UTC)

    invoices: list[Invoice] = []
    invoice_rows: list[tuple[object, ...]] = []
    invoice_created_at: dict[int, datetime] = {}
    for n, vendor_idx, day_offset, status, confidence, total in ANALYTICS_INVOICE_SPECS:
        created_at = now - timedelta(days=day_offset)
        invoice_created_at[n] = created_at
        inv = Invoice(
            id=seed_id(f"analytics-invoice:{n}"),
            tenant_id=tenant_id,
            currency="USD",
            source_channel="email",
            source_file_path=f"invoices/seed/analytics-{n}.pdf",
            content_hash=hashlib.sha256(f"analytics-seed-{n}".encode()).hexdigest(),
            status=InvoiceStatus(status),
            created_at=created_at,
            updated_at=created_at,
            invoice_number=f"INV-AN-{n}",
            invoice_date=created_at.date(),
            total=Decimal(total),
            subtotal=Decimal(total),
            tax=Decimal("0.00"),
            vendor_id=vendors[vendor_idx].id,
            overall_confidence=Decimal(str(confidence)) if confidence is not None else None,
            extraction_backend="gemini" if confidence is not None else None,
        )
        invoices.append(inv)
        # Built explicitly rather than via astuple(inv) so status --
        # Invoice.status is InvoiceStatus, an enum -- goes over the wire as
        # its plain string .value, not a bare enum member.
        invoice_rows.append(
            (
                inv.id, inv.tenant_id, inv.currency, inv.source_channel, inv.source_file_path,
                inv.content_hash, inv.status.value, inv.created_at, inv.updated_at,
                inv.invoice_number, inv.invoice_date, inv.subtotal, inv.tax, inv.total,
                inv.vendor_id, inv.po_id, inv.due_date, inv.extraction_backend,
                inv.overall_confidence,
            )
        )

    match_run_statuses = {"RECEIVED", "EXTRACTION_FAILED", "NEEDS_VERIFICATION"}
    match_runs: list[tuple[object, ...]] = []
    match_run_id_by_invoice: dict[int, uuid.UUID] = {}
    for n, _vendor_idx, _day_offset, status, _confidence, _total in ANALYTICS_INVOICE_SPECS:
        if status in match_run_statuses:
            continue
        match_run_id = seed_id(f"analytics-match-run:{n}")
        match_run_id_by_invoice[n] = match_run_id
        result = "exceptions" if n in ANALYTICS_EXCEPTION_SPECS else "clean"
        duration_ms = 200 + (n * 137) % 2800
        created_at = invoice_created_at[n]
        match_runs.append(
            (
                match_run_id, tenant_id, seed_id(f"analytics-invoice:{n}"), 1, result,
                duration_ms, created_at, created_at,
            )
        )

    match_exceptions: list[tuple[object, ...]] = []
    for n, spec in ANALYTICS_EXCEPTION_SPECS.items():
        exc_type, severity, expected, actual, delta_pct, status, detail = spec
        exc = MatchException(
            id=seed_id(f"analytics-exception:{n}"),
            tenant_id=tenant_id,
            match_run_id=match_run_id_by_invoice[n],
            invoice_id=seed_id(f"analytics-invoice:{n}"),
            exception_type=ExceptionType(exc_type),
            severity=Severity(severity),
            status=status,  # type: ignore[arg-type]
            created_at=invoice_created_at[n],
            expected_value=Decimal(expected) if expected else None,
            actual_value=Decimal(actual) if actual else None,
            delta=(Decimal(actual) - Decimal(expected)) if expected and actual else None,
            delta_pct=Decimal(delta_pct) if delta_pct else None,
            resolved_by=approver_id if status != "open" else None,
            resolved_at=invoice_created_at[n] if status != "open" else None,
        )
        match_exceptions.append(
            (
                exc.id, exc.tenant_id, exc.match_run_id, exc.invoice_id, exc.exception_type.value,
                exc.severity.value, exc.expected_value, exc.actual_value, exc.delta, exc.delta_pct,
                exc.status, exc.resolved_by, exc.resolved_at,
                "resolved via seeded decision" if exc.status != "open" else None,
                exc.created_at, detail,
            )
        )

    status_by_n = {spec[0]: spec[3] for spec in ANALYTICS_INVOICE_SPECS}
    audit_log_rows: list[tuple[object, ...]] = [
        (
            seed_id(f"analytics-decision:{n}"), tenant_id, "user", str(approver_id),
            "approval_decided", "invoice", seed_id(f"analytics-invoice:{n}"),
            Jsonb({"status": "PENDING_APPROVAL"}),
            Jsonb(
                {
                    "status": status_by_n[n],
                    "decision": "rejected" if status_by_n[n] == "REJECTED" else "approved",
                }
            ),
            invoice_created_at[n] + timedelta(hours=hours),
        )
        for n, hours in ANALYTICS_DECISION_HOURS.items()
    ]

    # 18 extraction jobs spread over the same window, for the latency
    # percentile chart -- jobs has no invoice_id column (payload is opaque
    # jsonb), so these don't need to line up 1:1 with the invoices above.
    # Every row gets an explicit seed_id() (not the default gen_random_uuid())
    # for the same reason every other table in this file does: ON CONFLICT
    # (id) DO NOTHING only makes a row idempotent if its id is deterministic
    # -- a DB-generated default id would insert a fresh duplicate every run.
    jobs_rows: list[tuple[object, ...]] = []
    for i in range(1, 19):
        created_at = now - timedelta(days=i)
        duration_ms = 300 + (i * 211) % 4000
        jobs_rows.append(
            (
                seed_id(f"analytics-extract-job:{i}"), tenant_id, "extract", Jsonb({"seed": i}),
                "done", 1, 3, f"seed:analytics-extract-{i}", created_at, created_at,
                "seed-worker", created_at, created_at + timedelta(milliseconds=duration_ms),
            )
        )

    # 15 notification deliveries; every 6th one failed, for the delivery
    # health panel's success-rate/mean-attempts figures.
    notification_rows: list[tuple[object, ...]] = []
    for i in range(1, 16):
        created_at = now - timedelta(days=i)
        failed = i % 6 == 0
        sent_at = None if failed else created_at + timedelta(milliseconds=150 + (i * 97) % 1200)
        notification_rows.append(
            (
                seed_id(f"analytics-notification:{i}"), tenant_id, "telegram", f"seed-chat-{i}",
                f"seed:analytics-notify-{i}", "failed" if failed else "sent",
                2 if failed else 1, sent_at, created_at,
            )
        )

    # One permanently dead notification job + its dead_letters row, so the
    # delivery health panel's dead-letter indicator has something to show.
    dead_job_id = seed_id("analytics-dead-job:1")
    dead_job_created_at = now - timedelta(days=2)
    jobs_rows.append(
        (
            dead_job_id, tenant_id, "notify", Jsonb({"seed": "dead"}), "dead", 3, 3,
            "seed:analytics-notify-dead-1", dead_job_created_at, dead_job_created_at,
            "seed-worker", dead_job_created_at, dead_job_created_at,
        )
    )
    dead_letters_row = (
        seed_id("analytics-dead-letter:1"), tenant_id, dead_job_id,
        Jsonb({"seed": "dead"}), "telegram API timeout after 3 attempts", dead_job_created_at,
    )

    return {
        "invoices": invoice_rows,
        "match_runs": match_runs,
        "match_exceptions": match_exceptions,
        "audit_log": audit_log_rows,
        "jobs": jobs_rows,
        "notification_deliveries": notification_rows,
        "dead_letters": [dead_letters_row],
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
    database_url = os.environ.get("RESET_SCRIPT_DATABASE_URL")
    if not database_url:
        print("ERROR: RESET_SCRIPT_DATABASE_URL is not set", file=sys.stderr)
        raise SystemExit(1)

    tenant = build_tenant()
    users = build_users(tenant.id)
    vendors = build_vendors(tenant.id)
    orders, lines_by_po = build_purchase_orders(tenant.id, vendors)
    all_po_lines = [line for lines in lines_by_po.values() for line in lines]
    clerk_id = next(u[0] for u in users if u[2] == "clerk@doritech-demo.example")
    receipts, receipt_lines = build_goods_receipts(tenant.id, orders, lines_by_po, clerk_id)
    policy = build_tolerance_policy(tenant.id)
    approver_id = next(u[0] for u in users if u[2] == "approver@doritech-demo.example")
    analytics = build_analytics_dataset(tenant.id, vendors, approver_id)

    with psycopg.connect(database_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            upsert(cur, "tenants", ["id", "name", "slug", "created_at"], [astuple(tenant)])
            upsert(
                cur,
                "users",
                ["id", "tenant_id", "email", "role", "created_at", "telegram_chat_id"],
                users,
            )
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
            upsert(
                cur,
                "invoices",
                [
                    "id", "tenant_id", "currency", "source_channel", "source_file_path",
                    "content_hash", "status", "created_at", "updated_at", "invoice_number",
                    "invoice_date", "subtotal", "tax", "total", "vendor_id", "po_id",
                    "due_date", "extraction_backend", "overall_confidence",
                ],
                analytics["invoices"],
            )
            upsert(
                cur,
                "match_runs",
                [
                    "id", "tenant_id", "invoice_id", "policy_version", "result", "duration_ms",
                    "executed_at", "created_at",
                ],
                analytics["match_runs"],
            )
            upsert(
                cur,
                "match_exceptions",
                [
                    "id", "tenant_id", "match_run_id", "invoice_id", "exception_type", "severity",
                    "expected_value", "actual_value", "delta", "delta_pct", "status",
                    "resolved_by", "resolved_at", "resolution_note", "created_at", "detail",
                ],
                analytics["match_exceptions"],
            )
            upsert(
                cur,
                "audit_log",
                [
                    "id", "tenant_id", "actor_type", "actor_id", "action", "entity_type",
                    "entity_id", "before", "after", "created_at",
                ],
                analytics["audit_log"],
            )
            upsert(
                cur,
                "jobs",
                [
                    "id", "tenant_id", "job_type", "payload", "status", "attempts", "max_attempts",
                    "idempotency_key", "run_after", "locked_at", "locked_by", "created_at",
                    "updated_at",
                ],
                analytics["jobs"],
            )
            upsert(
                cur,
                "notification_deliveries",
                [
                    "id", "tenant_id", "channel", "recipient", "idempotency_key", "status",
                    "attempts", "sent_at", "created_at",
                ],
                analytics["notification_deliveries"],
            )
            upsert(
                cur,
                "dead_letters",
                ["id", "tenant_id", "job_id", "payload", "final_error", "created_at"],
                analytics["dead_letters"],
            )
        conn.commit()

    print(
        f"Seeded: 1 tenant, {len(users)} users, {len(vendors)} vendors, "
        f"{len(orders)} purchase orders, {len(all_po_lines)} PO lines, "
        f"{len(receipts)} goods receipts, {len(receipt_lines)} receipt lines, "
        f"1 tolerance policy, {len(analytics['invoices'])} analytics invoices, "
        f"{len(analytics['match_exceptions'])} analytics exceptions, "
        f"{len(analytics['jobs'])} analytics jobs, "
        f"{len(analytics['notification_deliveries'])} analytics deliveries, "
        f"{len(analytics['dead_letters'])} dead letter(s)."
    )


if __name__ == "__main__":
    main()
