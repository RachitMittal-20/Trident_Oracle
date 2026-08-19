-- tenants is the root of the multi-tenant hierarchy. It is intentionally not
-- itself RLS-scoped by tenant_id — see 0010_rls.sql.
create table tenants (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    slug text not null unique,
    created_at timestamptz not null default now()
);

create table users (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references tenants (id),
    email text not null,
    role text not null check (role in ('admin', 'approver', 'clerk')),
    created_at timestamptz not null default now(),
    unique (tenant_id, email)
);

comment on column users.role is
    'admin: full tenant access. approver: can decide on approval_requests. clerk: intake only.';
