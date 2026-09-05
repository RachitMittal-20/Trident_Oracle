"""Resets demo state between rehearsals: deletes every invoice (and
everything that cascades from one -- lines, field confidences, match runs/
exceptions, jobs, notification deliveries, approval requests) belonging to
the demo tenant, WITHOUT touching the PO/GRN/vendor/policy data
demo/seed_demo.py seeded -- that's meant to persist across runs, only the
invoice-shaped state that a demo run creates needs clearing.

Usage:
    RESET_SCRIPT_DATABASE_URL=postgresql://... uv run python demo/reset.py

Requires a service-role/direct connection, not app_role: app_role has no
DELETE grant on any of these tables (by design -- production never deletes
invoice-shaped rows, only a demo reset does), so this can never run against
the app's own DATABASE_URL. RESET_SCRIPT_DATABASE_URL is a separate env var
for exactly that reason -- keeping it distinct from DATABASE_URL means
sourcing the app's own .env can never accidentally hand this script a
connection it silently can't use.

Does not touch audit_log (append-only by design, per CLAUDE.md -- a demo
reset is not an exemption from that rule; old demo runs' audit trail is
left in place, same as any other invoice history would be).
"""

import os
import sys
import uuid

import psycopg

TENANT_ID = uuid.uuid5(uuid.NAMESPACE_URL, "https://trident-oracle.example/seed")
TENANT_ID = uuid.uuid5(TENANT_ID, "tenant:doritech-demo")


def main() -> int:
    database_url = os.environ.get("RESET_SCRIPT_DATABASE_URL")
    if not database_url:
        print(
            "error: RESET_SCRIPT_DATABASE_URL must be set (service-role/direct connection)",
            file=sys.stderr,
        )
        return 1

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (str(TENANT_ID),))
            cur.execute(
                "SELECT id FROM invoices WHERE tenant_id = %s "
                "AND invoice_number LIKE 'INV-4%%'",
                (TENANT_ID,),
            )
            invoice_ids = [row[0] for row in cur.fetchall()]
            if not invoice_ids:
                print("Nothing to reset -- no demo invoices (INV-4xxx) found.")
                return 0

            invoice_id_strs = [str(i) for i in invoice_ids]
            cur.execute(
                "DELETE FROM dead_letters WHERE job_id IN ("
                "SELECT id FROM jobs WHERE tenant_id = %s AND payload->>'invoice_id' = ANY(%s))",
                (TENANT_ID, invoice_id_strs),
            )
            cur.execute(
                "DELETE FROM approval_requests WHERE invoice_id = ANY(%s)", (invoice_ids,)
            )
            cur.execute(
                "DELETE FROM notification_deliveries WHERE invoice_id = ANY(%s)", (invoice_ids,)
            )
            cur.execute(
                "DELETE FROM match_exceptions WHERE invoice_id = ANY(%s)", (invoice_ids,)
            )
            cur.execute("DELETE FROM match_runs WHERE invoice_id = ANY(%s)", (invoice_ids,))
            cur.execute(
                "DELETE FROM field_confidences WHERE invoice_id = ANY(%s)", (invoice_ids,)
            )
            cur.execute("DELETE FROM invoice_lines WHERE invoice_id = ANY(%s)", (invoice_ids,))
            cur.execute(
                "DELETE FROM jobs WHERE tenant_id = %s AND payload->>'invoice_id' = ANY(%s)",
                (TENANT_ID, invoice_id_strs),
            )
            cur.execute("DELETE FROM invoices WHERE id = ANY(%s)", (invoice_ids,))
        conn.commit()

    print(f"Reset {len(invoice_ids)} demo invoice(s) and everything that cascaded from them.")
    print("PO/GRN/vendor/policy seed data (demo/seed_demo.py) left untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
