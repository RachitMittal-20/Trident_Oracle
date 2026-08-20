-- Resolves the tension flagged in apps/worker/worker/db.py and main.py's
-- module docstrings: claim_next() (0005_queue.sql, used by
-- apps/worker/worker/db.py) has no tenant_id filter by design -- it claims
-- the globally-next queued job across every tenant, ordered by created_at.
-- That is fundamentally incompatible with jobs' tenant_isolation RLS policy
-- (0010_rls.sql), which only ever shows the rows for whatever single tenant
-- app.tenant_id currently names.
--
-- queue_claimer is a narrowly-scoped role for exactly that one job: the
-- worker's queue-management connection (enqueue/claim_next/complete/fail/
-- reap_stale_locks in apps/worker/worker/db.py::JobQueue). It is not the
-- role a job's *handler* uses -- handlers keep using the ordinary
-- app-role connection with app.tenant_id set per job (apps/worker/worker/
-- main.py), which remains fully RLS-restricted. See db/README.md's
-- "Security model" section for the two-role picture end to end.
--
-- Why BYPASSRLS rather than a permissive `USING (true)` policy for this
-- role: Postgres RLS policies are additive per command type (SELECT,
-- INSERT, UPDATE each need their own permissive policy to cover this
-- role), so a "see everything" policy would mean writing and maintaining
-- three near-duplicate policies that sit alongside tenant_isolation and
-- must be kept in sync if that policy's shape ever changes. A role-level
-- BYPASSRLS is the standard Postgres idiom for "this role is
-- infrastructure, not a tenant" -- one flag, visible directly in \du,
-- rather than logic spread across policy definitions. It is safe here
-- specifically because it is paired with table-level grants scoped to
-- exactly jobs and dead_letters: bypassing RLS cannot leak tenant business
-- data through this role because there is no business data (invoices,
-- purchase_orders, etc.) this role has any grant to read in the first
-- place. RLS therefore remains the authorization boundary for every table
-- that holds tenant business data (CLAUDE.md principle 6) -- queue_claimer
-- only ever touches queue plumbing, which is operational metadata, not
-- tenant data.
--
-- Credentials (the role's password) are provisioned outside migrations,
-- same as every other secret in this project -- see .env.example.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'queue_claimer') then
        create role queue_claimer login bypassrls nosuperuser nocreatedb nocreaterole;
    end if;
end
$$;

grant usage on schema public to queue_claimer;

-- Defense in depth: explicit revoke-all before explicit grants, so a
-- future `grant ... to public` elsewhere in the schema can't silently
-- extend this role's reach beyond the two tables it actually needs.
revoke all on all tables in schema public from queue_claimer;

grant select, insert, update on jobs to queue_claimer;
grant select, insert on dead_letters to queue_claimer;
