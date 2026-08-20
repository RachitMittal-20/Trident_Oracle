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
0010_rls.sql          RLS policies on tables with a direct tenant_id column
0011_rls_child_tables.sql  tenant_id + RLS on the remaining child tables
0012_queue_claimer_role.sql  queue_claimer role (see "Security model" below)
0013_app_role.sql     app_role (see "Security model" below)
0014_tenants_self_read.sql  tenants RLS + read-only self-scoped app_role grant
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
the API (per-request) and the worker's *handler* connection (per-job, as `app_role` —
see "Security model" below) must call, on the same connection that runs the
subsequent query:

```sql
select set_config('app.tenant_id', '<tenant-uuid>', false);
```

If it's never set, `current_setting(..., true)` returns `NULL`, every policy's
`tenant_id = NULL` comparison is `NULL` (not true), and access is denied by
default — confirmed by testing against a non-superuser role locally. Note that
Postgres superusers (and the Supabase `postgres` role) bypass RLS entirely
regardless of policies or `FORCE ROW LEVEL SECURITY`, so always test tenant
isolation as a restricted role, not as the admin connection.

Never set `app.tenant_id` on a `queue_claimer` connection — that role has no
tenant-scoped policies applied to it in the first place (see below), so doing
so would be misleading rather than protective.

## Security model

Two Postgres roles, two jobs:

**`app_role`** (0013_app_role.sql) is the ordinary, fully RLS-restricted
connection. The API uses it per-request; the worker uses it per-job for
everything a job *handler* does once claimed, with `app.tenant_id` set to
that job's own tenant before the handler runs. It has `NOSUPERUSER
NOBYPASSRLS` explicitly and `SELECT`/`INSERT`/`UPDATE` grants on every
tenant-scoped table (every table with a `tenant_isolation` policy from 0010
and 0011) — but grants alone don't isolate anything here, since app_role
doesn't bypass RLS. Every tenant-scoped table's `tenant_isolation` policy
applies in full on this role — this is the connection where "RLS is the
authorization boundary" (CLAUDE.md principle 6) is actually being enforced.

**`tenants` is a deliberate exception to the 0013 grant list** — 0014
(`tenants_self_read`) closes it separately, because `tenants` needed a
different *policy shape*, not just a different table. Every other RLS'd
table has its own `tenant_id` column, compared against
`current_setting('app.tenant_id', true)` — a row belongs to a tenant. A row
in `tenants` *is* a tenant; there's no `tenant_id` column to compare, so the
policy compares the table's own `id` instead: a session may read the one
row whose `id` matches its current `app.tenant_id`, and no other. Same
default-deny shape as everywhere else (unset `app.tenant_id` → `NULL` →
denied). `app_role`'s grant on `tenants` is `SELECT` only — no `INSERT`,
`UPDATE`, or `DELETE` — because provisioning a tenant (creating, renaming,
deleting one) is an administrative operation done out of band, not
something application code does on a tenant's own behalf. In practice this
means a tenant can read its own registry row (name, slug, created_at) — e.g.
to display it in the UI — and nothing about any other tenant, and can't
write to it at all.

**`queue_claimer`** (0012_queue_claimer_role.sql) exists for exactly one
problem: `claim_next()`'s query (`apps/worker/worker/db.py`) has no `tenant_id`
filter by design — it claims the globally-next queued job across every tenant,
ordered by `created_at`. That's fundamentally incompatible with `jobs`' RLS
policy, which only ever shows the rows for one tenant at a time. `queue_claimer`
has `BYPASSRLS` plus table grants scoped to exactly `jobs` and `dead_letters` —
`SELECT`/`INSERT`/`UPDATE` on `jobs`, `SELECT`/`INSERT` on `dead_letters`, and an
explicit `REVOKE ALL ... FROM queue_claimer` on every other table first. `apps/
worker/worker/db.py`'s `JobQueue` (`enqueue`/`claim_next`/`complete`/`fail`/
`reap_stale_locks`) always connects as this role; nothing else does.

Why this doesn't weaken "RLS is the authorization boundary": `queue_claimer`
bypassing RLS can't leak tenant business data, because it has no grant on any
table that holds tenant business data — `invoices`, `purchase_orders`, and
everything else remain completely unreachable to it regardless of RLS. RLS
stays the enforced boundary on every table where a boundary is actually needed;
`queue_claimer`'s reach is bounded by grants instead, on two tables that hold
queue plumbing, not tenant data.

Why `BYPASSRLS` rather than a permissive `USING (true)` policy for this role:
RLS policies are additive per command type, so a "see everything" policy would
mean writing and maintaining three near-duplicate policies (`SELECT`, `INSERT`,
`UPDATE`) that sit alongside `tenant_isolation` and must be kept in sync if that
policy's shape ever changes. A role-level `BYPASSRLS` is the standard Postgres
idiom for "this role is infrastructure, not a tenant" — one flag, visible
directly in `\du`, rather than logic spread across policy definitions.

Both roles' passwords/credentials are provisioned outside migrations, like
every other secret — see `.env.example`'s `QUEUE_CLAIMER_DATABASE_URL` and
`DATABASE_URL`. Applying `db/migrations/` alone, in order, against a fresh
Postgres instance is enough to reproduce the entire RLS story — both roles,
every grant, every policy — without any manual `CREATE ROLE` step.

## Known scope limits

- `purchase_orders.status` and `match_exceptions.status` use provisional enum
  values (see comments in 0003 and 0006) — the architecture doc doesn't specify
  these lifecycles, so they'll likely need a follow-up migration once the
  corresponding flows are built.
