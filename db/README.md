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
0015_invoices_nullable_pre_extraction.sql  invoice_number/date/subtotal/tax/total
                       become nullable -- unknown at RECEIVED, before extraction runs
0016_invoices_allow_mock_backend.sql  extraction_backend CHECK also allows 'mock'
                       (MockExtractor's real, named backend value -- see
                       packages/extractors/factory.py)
0017_match_exceptions_detail.sql  match_exceptions.detail -- human-readable
                       reasoning from core.matching's MatchFinding/DuplicateFinding
0018_approval_requests_token_hash_unique.sql  UNIQUE(token_hash)
0019_approval_redeemer_role.sql  approval_redeemer role (see "Security model" below)
0020_notification_deliveries_invoice_context.sql  invoice_id/exception_id on
                       notification_deliveries, for GET /v1/deliveries
0021_users_telegram_chat_id.sql  users.telegram_chat_id, for the notify
                       pipeline to resolve a real approver's contact info
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

Three Postgres roles, three jobs:

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

**`approval_redeemer`** (0019_approval_redeemer_role.sql) exists for the same
shape of problem as `queue_claimer`, one level up the stack: redeeming an
approval token (`apps/api/api/approvals.py`) means looking `approval_requests`
up by `token_hash` *before* knowing which tenant that row belongs to — the
caller presents only an opaque token (a Telegram callback, a clicked email
link), never `app.tenant_id`. `tenant_isolation` denies by default when
`app.tenant_id` is unset, so that first lookup is structurally impossible
under `app_role`.

Unlike `queue_claimer`, `approval_redeemer` is **narrower** than `app_role`'s
own RLS exposure, not broader — it is `NOSUPERUSER NOBYPASSRLS`, with grants
on `approval_requests`/`invoices`/`match_exceptions`/`jobs`/`audit_log`
*identical* to `app_role`'s own grants on those five tables, and is subject
to `tenant_isolation` on all five exactly as `app_role` is. The only
addition is one extra, permissive policy —
`approval_redeemer_token_lookup`, `FOR SELECT ... TO approval_redeemer
USING (true)` — on `approval_requests` alone. That policy is additive
(Postgres OR's permissive policies of the same command type together), so
it doesn't touch `tenant_isolation`'s own `SELECT` policy on that table for
`app_role` or anyone else, and it grants no `INSERT`/`UPDATE` reach:
`approval_redeemer`'s writes to `approval_requests` remain governed solely
by `tenant_isolation`'s own `USING`/`WITH CHECK`, same as every other grant
this role has.

Why this matters, and why it's the opposite trade-off from `queue_claimer`:
`queue_claimer`'s `BYPASSRLS` removes RLS as a safeguard *entirely* on the
two tables it touches — safe there only because neither table holds tenant
business data, so there's nothing behind RLS for a bug in that role's code
to leak. `approval_redeemer` **is** granted on tables that hold real tenant
business data (`invoices`, `match_exceptions`), so leaving `tenant_isolation`
fully live and enforced on those tables is a real second line of defense: if
`apps/api/api/approvals.py` ever had a bug that mishandled `tenant_id` —
read or wrote the wrong tenant's invoice because of a coding mistake, not a
malicious token — `tenant_isolation` would still catch it and deny the
query, exactly the protection `app_role` gets everywhere. The one place this
role can see across tenants is a single `SELECT` on a single table, for the
one purpose the whole token mechanism structurally requires: discovering
which tenant a bare, presented token belongs to before `app.tenant_id` can
be set at all. Every other read, and every write anywhere, stays exactly as
tenant-scoped as it is for `app_role`.

The resulting shape in `apps/api/api/approvals.py` is two steps, not one:
(1) a plain `SELECT` (never `SELECT ... FOR UPDATE`) on `approval_requests`,
relying on the permissive policy to find the row and read its `tenant_id`
across every tenant — deliberately not a locking read, since Postgres
requires a `FOR UPDATE`/`FOR SHARE` SELECT's rows to satisfy *both* the
SELECT policy's and the UPDATE policy's `USING` clause, and
`tenant_isolation`'s UPDATE policy cannot pass yet at that point; then (2)
`set_config('app.tenant_id', ...)` on that same connection/transaction,
immediately, after which every remaining statement — including a second,
locking `SELECT ... FOR UPDATE` by id to actually take the row lock the
single-use guarantee depends on, the token-consuming `UPDATE`, the invoice
transition, resolving `match_exceptions`, the `audit_log` write, and the
`post` job `INSERT` — runs under ordinary `tenant_isolation`, identical to
`app_role`.

All three roles' passwords/credentials are provisioned outside migrations,
like every other secret — see `.env.example`'s `QUEUE_CLAIMER_DATABASE_URL`,
`DATABASE_URL`, and `APPROVAL_REDEEMER_DATABASE_URL`. Applying
`db/migrations/` alone, in order, against a fresh Postgres instance is enough
to reproduce the entire RLS story — every role, every grant, every policy —
without any manual `CREATE ROLE` step.

## Known scope limits

- `purchase_orders.status` and `match_exceptions.status` use provisional enum
  values (see comments in 0003 and 0006) — the architecture doc doesn't specify
  these lifecycles, so they'll likely need a follow-up migration once the
  corresponding flows are built.
