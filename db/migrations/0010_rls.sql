-- Row Level Security is the authorization boundary (CLAUDE.md principle 6). The
-- worker sets app.tenant_id per job via `set_config('app.tenant_id', ..., true)`
-- before touching tenant data, so this holds in background-job context, not just
-- request context. `true` is passed to current_setting() so an unset value reads
-- as null instead of raising, and every policy therefore denies rather than errors
-- when app.tenant_id has not been set.
--
-- Scope: this migration covers tables that carry a tenant_id column directly.
-- Child tables scoped only via a foreign key to one of these (purchase_order_lines,
-- goods_receipt_lines, invoice_lines, field_confidences, dead_letters, match_runs,
-- match_exceptions) do not yet have their own policies -- that requires a join back
-- to the tenant-scoped parent, which is left for a follow-up migration once actual
-- query patterns from apps/api and apps/worker are known. eval_runs/eval_results
-- are intentionally not tenant-scoped (see 0009_evals.sql).

create policy tenant_isolation on users
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table users enable row level security;
alter table users force row level security;

create policy tenant_isolation on vendors
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table vendors enable row level security;
alter table vendors force row level security;

create policy tenant_isolation on purchase_orders
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table purchase_orders enable row level security;
alter table purchase_orders force row level security;

create policy tenant_isolation on goods_receipts
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table goods_receipts enable row level security;
alter table goods_receipts force row level security;

create policy tenant_isolation on invoices
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table invoices enable row level security;
alter table invoices force row level security;

create policy tenant_isolation on jobs
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table jobs enable row level security;
alter table jobs force row level security;

create policy tenant_isolation on notification_deliveries
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table notification_deliveries enable row level security;
alter table notification_deliveries force row level security;

create policy tenant_isolation on tolerance_policies
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table tolerance_policies enable row level security;
alter table tolerance_policies force row level security;

create policy tenant_isolation on approval_requests
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table approval_requests enable row level security;
alter table approval_requests force row level security;

create policy tenant_isolation on audit_log
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table audit_log enable row level security;
alter table audit_log force row level security;
