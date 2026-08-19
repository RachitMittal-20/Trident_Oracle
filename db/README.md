# Database migrations

Plain numbered SQL files, applied in order. No ORM migration tool — see CLAUDE.md.
Never edit a committed migration; add a new numbered file instead.

```
0001_extensions.sql   pgcrypto, pg_trgm
0002_tenancy.sql      tenants, users
0003_procurement.sql  vendors, purchase_orders, purchase_order_lines,
                      goods_receipts, goods_receipt_lines
0004_invoices.sql     invoices, invoice_lines, field_confidences
0005_queue.sql        jobs, dead_letters, notification_deliveries
0006_matching.sql     tolerance_policies, match_runs, match_exceptions
0007_approvals.sql    approval_requests
0008_audit.sql        audit_log + append-only trigger
0009_evals.sql        eval_runs, eval_results
0010_rls.sql          RLS policies on every tenant-scoped table
```

## Applying against Supabase

### Option A — Supabase CLI (recommended)

```bash
brew install supabase/tap/supabase   # if not already installed
supabase login
supabase link --project-ref <your-project-ref>
```

Copy each file into `supabase/migrations/` with a Supabase-compatible timestamp
prefix (or symlink `db/migrations` there), then:

```bash
supabase db push
```

This runs every migration Supabase hasn't already recorded, in filename order, inside
its own migration-history tracking table.

### Option B — psql directly against the project's connection string

Useful for a quick apply without adopting the CLI's migration bookkeeping. Get
`DATABASE_URL` from Supabase → Project Settings → Database → Connection string
(use the pooler connection for normal use; direct connection for one-off DDL runs
that need a session-level `SET`).

```bash
export DATABASE_URL="postgresql://postgres:<password>@<host>:5432/postgres"

for f in db/migrations/*.sql; do
  echo "applying $f"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f" || { echo "failed at $f"; break; }
done
```

`ON_ERROR_STOP=1` is important — without it, psql keeps going after a failed
statement and you can end up with a partially-applied migration.

### Verifying locally before pushing

Since these are plain SQL files with no framework tying them to Supabase, they
can be smoke-tested against any throwaway Postgres 16+ instance first, e.g.:

```bash
createdb trident_scratch
for f in db/migrations/*.sql; do
  psql -d trident_scratch -v ON_ERROR_STOP=1 -f "$f" || break
done
dropdb trident_scratch
```

## Setting `app.tenant_id`

RLS policies (0010, 0011) key off `current_setting('app.tenant_id', true)::uuid`. Both
the API (per-request) and the worker (per-job) must call, on the same connection
that runs the subsequent query:

```sql
select set_config('app.tenant_id', '<tenant-uuid>', false);
```

If it's never set, `current_setting(..., true)` returns `NULL`, every policy's
`tenant_id = NULL` comparison is `NULL` (not true), and access is denied by
default — confirmed by testing against a non-superuser role locally. Note that
Postgres superusers (and the Supabase `postgres` role) bypass RLS entirely
regardless of policies or `FORCE ROW LEVEL SECURITY`, so always test tenant
isolation as a restricted role, not as the admin connection.

## Known scope limits

- `purchase_orders.status` and `match_exceptions.status` use provisional enum
  values (see comments in 0003 and 0006) — the architecture doc doesn't specify
  these lifecycles, so they'll likely need a follow-up migration once the
  corresponding flows are built.
