"""Links a just-uploaded demo invoice to its PO and vendor, standing in for
linkage this system doesn't do automatically (see docs/ROADMAP.md: no code
anywhere resolves invoices.po_id from an uploaded invoice's extracted
content -- apps/api/api/main.py's upload endpoint and the webhook payload
both take only a file, never a po_id, and nothing infers one afterward;
vendor_id has the same gap, compounded by TesseractExtractor never
populating vendor_name in the first place). Run this immediately after
uploading a fixture through the real API/UI -- before the 'match' job would
otherwise run and immediately fail: no po_id means NO_PO (block, pipeline
stops there); no vendor_id means run_three_way_match raises outright
(vendor_id is a required argument, needed for duplicate detection's
vendor-name comparison).

Usage:
    DATABASE_URL=postgresql://... uv run python demo/link_po.py \\
        --fixture demo/fixtures/01-clean-invoice.png --po PO-3001

Looks up the invoice by content_hash (sha256 of the fixture file's bytes --
identical computation to apps/api/api/ingest.py's own dedupe key) rather
than by id, since the id is only known from the upload response, which this
script deliberately doesn't need to be handed -- the presenter can run this
straight after a UI drag-and-drop, not just after a scripted curl upload.
"""

import argparse
import hashlib
import os
import sys

import psycopg


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True, help="path to the uploaded fixture file")
    parser.add_argument("--po", required=True, help="po_number to link, e.g. PO-3001")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("error: DATABASE_URL must be set (service-role/direct connection)", file=sys.stderr)
        return 1

    content_hash = hashlib.sha256(open(args.fixture, "rb").read()).hexdigest()

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, tenant_id, po_id, status FROM invoices WHERE content_hash = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (content_hash,),
            )
            row = cur.fetchone()
            if row is None:
                print(
                    f"error: no invoice found with content_hash of {args.fixture!r} -- "
                    "upload it first",
                    file=sys.stderr,
                )
                return 1
            invoice_id, tenant_id, existing_po_id, status = row

            # vendor_id, not just po_id: core.matching.three_way.run_three_way_match
            # requires invoice.vendor_id (needed for duplicate detection's
            # vendor-name comparison) and TesseractExtractor never populates
            # vendor_name (no reliable label to search for -- see that
            # module's docstring), so nothing else will ever set it either.
            # Once the PO is known, its vendor is the only sane inference.
            cur.execute(
                "SELECT id, vendor_id FROM purchase_orders WHERE tenant_id = %s AND po_number = %s",
                (tenant_id, args.po),
            )
            po_row = cur.fetchone()
            if po_row is None:
                print(
                    f"error: no PO {args.po!r} found for tenant {tenant_id} -- "
                    "run demo/seed_demo.py first",
                    file=sys.stderr,
                )
                return 1
            po_id, vendor_id = po_row

            cur.execute(
                "UPDATE invoices SET po_id = %s, vendor_id = %s WHERE id = %s",
                (po_id, vendor_id, invoice_id),
            )
        conn.commit()

    print(f"Linked invoice {invoice_id} (status={status}) to {args.po}")
    if existing_po_id is not None and existing_po_id != po_id:
        print(f"  (was previously linked to a different po_id: {existing_po_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
