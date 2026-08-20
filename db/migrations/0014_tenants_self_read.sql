-- tenants gets its own policy shape, distinct from every other RLS'd table
-- in this schema (0010, 0011). Everywhere else, tenant_isolation compares a
-- row's own tenant_id column against current_setting('app.tenant_id', true).
-- tenants has no tenant_id column -- a tenant *is* the row, not something
-- a row belongs to -- so the equivalent comparison is against the table's
-- own primary key: a session may read the one tenants row whose id matches
-- its current app.tenant_id, and no other. Same default-deny behavior when
-- app.tenant_id is unset (current_setting(..., true) returns NULL, id = NULL
-- is NULL, not true), same reasoning as every other policy in this schema.

create policy tenant_self_read on tenants
    for select
    using (id = current_setting('app.tenant_id', true)::uuid);
alter table tenants enable row level security;
alter table tenants force row level security;

-- SELECT only, deliberately -- tenant provisioning (creating, renaming, or
-- deleting a tenant) is an administrative operation done out of band, not
-- something application code does on a tenant's own behalf. app_role
-- previously had no grant on tenants at all (0013_app_role.sql excluded it
-- as not tenant-scoped); this adds exactly enough for a tenant to read its
-- own registry row (name, slug, created_at) -- e.g. to display it in the
-- UI -- without being able to write to it.
revoke all on tenants from app_role;
grant select on tenants to app_role;
