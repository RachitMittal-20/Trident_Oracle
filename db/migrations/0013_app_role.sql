-- app_role is the ordinary application connection: the API uses it
-- per-request, and the worker uses it per-job (as the handler_conn in
-- apps/worker/worker/main.py) with app.tenant_id set to the claimed job's
-- own tenant before the handler runs. Unlike queue_claimer
-- (0012_queue_claimer_role.sql), this role must stay fully RLS-subject --
-- that is the entire point of having it be a separate role at all. It gets
-- NOSUPERUSER NOBYPASSRLS explicitly, not just by omission, so the intent
-- reads directly off this file rather than relying on Postgres defaults.
--
-- Same idempotent-create pattern as 0012, for the same reason: roles are
-- cluster-global, so this migration may run against a cluster that already
-- has app_role from a previous deploy.
--
-- Credentials (the role's password) are provisioned outside migrations,
-- same as queue_claimer -- see .env.example's DATABASE_URL.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'app_role') then
        create role app_role login nosuperuser nobypassrls nocreatedb nocreaterole;
    end if;
end
$$;

grant usage on schema public to app_role;

-- Defense in depth, same reasoning as 0012: explicit revoke-all before
-- explicit grants, so a future `grant ... to public` elsewhere in the
-- schema can't silently extend this role's reach.
revoke all on all tables in schema public from app_role;

-- Every tenant-scoped table (tenant_isolation policy from 0010/0011) --
-- app_role's actual isolation comes entirely from RLS on these grants, not
-- from the grants themselves being narrow the way queue_claimer's are.
-- tenants, eval_runs, and eval_results are deliberately excluded: they are
-- not tenant-scoped (see 0010's and 0009's own comments on why).
grant select, insert, update on users to app_role;
grant select, insert, update on vendors to app_role;
grant select, insert, update on purchase_orders to app_role;
grant select, insert, update on purchase_order_lines to app_role;
grant select, insert, update on goods_receipts to app_role;
grant select, insert, update on goods_receipt_lines to app_role;
grant select, insert, update on invoices to app_role;
grant select, insert, update on invoice_lines to app_role;
grant select, insert, update on field_confidences to app_role;
grant select, insert, update on jobs to app_role;
grant select, insert, update on dead_letters to app_role;
grant select, insert, update on notification_deliveries to app_role;
grant select, insert, update on tolerance_policies to app_role;
grant select, insert, update on match_runs to app_role;
grant select, insert, update on match_exceptions to app_role;
grant select, insert, update on approval_requests to app_role;
grant select, insert, update on audit_log to app_role;
