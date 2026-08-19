-- Denormalize tenant_id onto every remaining tenant-scoped child table so RLS
-- policies can compare a plain column instead of subquerying through a parent.
-- This is the standard, faster, simpler RLS pattern -- see db/README.md.
--
-- Each column is added nullable, backfilled from its parent, then set NOT NULL.
-- The schema has no data yet, so the backfill is trivial here, but the
-- add -> backfill -> constrain sequence is the safe pattern regardless of
-- whether the table is actually empty when this runs.

alter table purchase_order_lines add column tenant_id uuid references tenants (id);
update purchase_order_lines pol
    set tenant_id = po.tenant_id
    from purchase_orders po
    where po.id = pol.po_id;
alter table purchase_order_lines alter column tenant_id set not null;

alter table goods_receipt_lines add column tenant_id uuid references tenants (id);
update goods_receipt_lines grl
    set tenant_id = gr.tenant_id
    from goods_receipts gr
    where gr.id = grl.grn_id;
alter table goods_receipt_lines alter column tenant_id set not null;

alter table invoice_lines add column tenant_id uuid references tenants (id);
update invoice_lines il
    set tenant_id = inv.tenant_id
    from invoices inv
    where inv.id = il.invoice_id;
alter table invoice_lines alter column tenant_id set not null;

alter table field_confidences add column tenant_id uuid references tenants (id);
update field_confidences fc
    set tenant_id = inv.tenant_id
    from invoices inv
    where inv.id = fc.invoice_id;
alter table field_confidences alter column tenant_id set not null;

alter table dead_letters add column tenant_id uuid references tenants (id);
update dead_letters dl
    set tenant_id = j.tenant_id
    from jobs j
    where j.id = dl.job_id;
alter table dead_letters alter column tenant_id set not null;

alter table match_runs add column tenant_id uuid references tenants (id);
update match_runs mr
    set tenant_id = inv.tenant_id
    from invoices inv
    where inv.id = mr.invoice_id;
alter table match_runs alter column tenant_id set not null;

alter table match_exceptions add column tenant_id uuid references tenants (id);
update match_exceptions me
    set tenant_id = inv.tenant_id
    from invoices inv
    where inv.id = me.invoice_id;
alter table match_exceptions alter column tenant_id set not null;

comment on column purchase_order_lines.tenant_id is
    'Denormalized from purchase_orders.tenant_id via po_id, for direct RLS.';
comment on column goods_receipt_lines.tenant_id is
    'Denormalized from goods_receipts.tenant_id via grn_id, for direct RLS.';
comment on column invoice_lines.tenant_id is
    'Denormalized from invoices.tenant_id via invoice_id, for direct RLS.';
comment on column field_confidences.tenant_id is
    'Denormalized from invoices.tenant_id via invoice_id, for direct RLS.';
comment on column dead_letters.tenant_id is
    'Denormalized from jobs.tenant_id via job_id, for direct RLS.';
comment on column match_runs.tenant_id is
    'Denormalized from invoices.tenant_id via invoice_id, for direct RLS.';
comment on column match_exceptions.tenant_id is
    'Denormalized from invoices.tenant_id via invoice_id, for direct RLS.';

-- Same tenant_isolation policy pattern as 0010_rls.sql.

create policy tenant_isolation on purchase_order_lines
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table purchase_order_lines enable row level security;
alter table purchase_order_lines force row level security;

create policy tenant_isolation on goods_receipt_lines
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table goods_receipt_lines enable row level security;
alter table goods_receipt_lines force row level security;

create policy tenant_isolation on invoice_lines
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table invoice_lines enable row level security;
alter table invoice_lines force row level security;

create policy tenant_isolation on field_confidences
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table field_confidences enable row level security;
alter table field_confidences force row level security;

create policy tenant_isolation on dead_letters
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table dead_letters enable row level security;
alter table dead_letters force row level security;

create policy tenant_isolation on match_runs
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table match_runs enable row level security;
alter table match_runs force row level security;

create policy tenant_isolation on match_exceptions
    using (tenant_id = current_setting('app.tenant_id', true)::uuid)
    with check (tenant_id = current_setting('app.tenant_id', true)::uuid);
alter table match_exceptions enable row level security;
alter table match_exceptions force row level security;
