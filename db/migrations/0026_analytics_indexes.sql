-- Two indexes for apps/api/api/analytics_view.py, added ahead of real data
-- volume rather than after the dashboard is observed to be slow.
--
-- invoices(tenant_id, vendor_id): get_vendors()'s vendor_invoices CTE joins
-- vendors to invoices and groups by vendor_id, over every invoice a tenant
-- has -- at the current ~24-row seed volume Postgres correctly picks a
-- sequential scan over invoices regardless (confirmed via EXPLAIN (ANALYZE,
-- BUFFERS) against the live project), but that scan is a scan of the
-- table's *entire* physical storage across every tenant, not just this
-- one's rows -- idx_invoices_tenant_id_status (0004_invoices.sql) has
-- tenant_id leading, so it already lets the planner skip other tenants
-- once a tenant has enough of its own rows to make an index scan worth it,
-- but it doesn't cover vendor_id, so it can't also deliver invoices
-- pre-sorted by vendor_id the way this composite index can -- exactly the
-- order get_vendors()'s GROUP BY vendor_id wants at real volume.
create index idx_invoices_tenant_id_vendor_id on invoices (tenant_id, vendor_id);

-- audit_log(tenant_id, action, entity_type, created_at): get_summary()'s
-- mean-time-to-decision query filters on exactly this column combination
-- (action = 'approval_decided', entity_type = 'invoice', created_at within
-- the period), and confirmed via the same EXPLAIN pass to still be a
-- sequential scan today. Unlike jobs or invoices, audit_log is append-only
-- by design (db/migrations/0008_audit.sql's trigger blocks UPDATE/DELETE
-- outright) -- every row ever written stays forever, so this table's scan
-- cost only ever grows, never resets. tenant_id leads the index for the
-- same reason every other tenant-scoped composite index in this schema
-- puts it first (idx_invoices_tenant_id_status, idx_vendors_tenant_id_
-- normalized_name): it's what RLS's tenant_isolation policy filters on for
-- every single query against this table, with no exception.
create index idx_audit_log_tenant_id_action_entity_type on audit_log (tenant_id, action, entity_type, created_at);
