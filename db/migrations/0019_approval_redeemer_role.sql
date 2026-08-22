-- approval_redeemer exists for the same underlying problem
-- 0012_queue_claimer_role.sql solves for the job queue: redeem_approval_token
-- (and its read-only sibling, preview_approval_token) look approval_requests
-- up by token_hash BEFORE knowing which tenant that row belongs to -- the
-- caller presents only an opaque token (an anonymous Telegram callback, or a
-- link clicked from an email), neither of which carries app.tenant_id.
-- tenant_isolation's default-deny (0010_rls.sql) makes that first lookup
-- structurally impossible under an ordinary RLS-bound connection with no
-- app.tenant_id set.
--
-- Unlike queue_claimer, this role is NOT a BYPASSRLS role. It is
-- NOSUPERUSER NOBYPASSRLS, with IDENTICAL grants to app_role
-- (0013_app_role.sql) on the five tables one issue/redeem transaction
-- touches -- approval_requests, invoices, match_exceptions, jobs,
-- audit_log -- and is therefore subject to every tenant_isolation policy
-- on all five exactly the way app_role is, with exactly one narrow,
-- additive exception: a second, permissive SELECT-only policy on
-- approval_requests, scoped to this role alone, whose USING clause is
-- unconditionally true.
--
-- Why narrower than queue_claimer, not broader: queue_claimer's BYPASSRLS
-- removes RLS as a safeguard entirely for the two tables it's granted on --
-- that's fine there because it has no grant on any table holding tenant
-- business data, so there's nothing behind RLS for it to leak in the first
-- place. approval_redeemer is different: it IS granted on tables that hold
-- real tenant business data (invoices, match_exceptions), so RLS staying
-- live and enforced on those tables is a real second line of defense here
-- -- if apps/api/api/approvals.py ever had a bug that read or wrote the
-- wrong tenant's invoice, tenant_isolation would still catch it, the same
-- protection app_role gets. The ONE place this role can see across
-- tenants is a single SELECT on a single table (approval_requests) for the
-- single purpose the whole token mechanism structurally requires: finding
-- which tenant a presented token belongs to before app.tenant_id can be
-- set. Every other read, and every write anywhere, stays exactly as
-- tenant-scoped as it is for app_role.
--
-- See apps/api/api/approvals.py's redeem_approval_token/preview_approval_token
-- for how this is actually used: an initial plain SELECT (no FOR UPDATE) on
-- approval_requests, relying on the permissive policy below to find the row
-- and read its tenant_id; set_config('app.tenant_id', ...) immediately after,
-- on the same connection/transaction; then a second SELECT ... FOR UPDATE by
-- id (now under ordinary tenant_isolation, which now matches) to actually
-- take the row lock the single-use guarantee depends on, and every
-- subsequent statement in the transaction. The initial lookup deliberately
-- does NOT use FOR UPDATE: Postgres requires a locking SELECT's rows to
-- satisfy both the SELECT policy's and the UPDATE policy's USING clauses
-- (see "Row Security Policies" in the Postgres manual), and tenant_isolation's
-- UPDATE policy cannot pass yet at that point -- app.tenant_id isn't set
-- until after this first, lock-free lookup completes.
--
-- Credentials provisioned outside migrations, same as queue_claimer/app_role.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'approval_redeemer') then
        create role approval_redeemer login nosuperuser nobypassrls nocreatedb nocreaterole;
    end if;
end
$$;

grant usage on schema public to approval_redeemer;

-- Defense in depth: explicit revoke-all before explicit grants, so a
-- future `grant ... to public` elsewhere in the schema can't silently
-- extend this role's reach beyond the five tables it actually needs.
revoke all on all tables in schema public from approval_redeemer;

grant select, insert, update on approval_requests to approval_redeemer;
grant select, insert, update on invoices to approval_redeemer;
grant select, insert, update on match_exceptions to approval_redeemer;
grant select, insert, update on jobs to approval_redeemer;
grant select, insert, update on audit_log to approval_redeemer;

-- The one structural exception this whole mechanism requires: without this,
-- there would be no way to ever discover which tenant a bare token belongs
-- to, since tenant_isolation (already enabled+forced on approval_requests by
-- 0010_rls.sql) denies by default when app.tenant_id is unset. This policy
-- is additive (permissive) and scoped to approval_redeemer alone (`to
-- approval_redeemer`) -- it does not weaken tenant_isolation for app_role or
-- any other role, and it grants no additional INSERT/UPDATE reach on this
-- table or any other: approval_redeemer's writes to approval_requests
-- remain governed solely by tenant_isolation's own USING/WITH CHECK, same
-- as every other grant this role has.
create policy approval_redeemer_token_lookup on approval_requests
    for select
    to approval_redeemer
    using (true);
