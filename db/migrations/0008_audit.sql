create table audit_log (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references tenants (id),
    actor_type text not null check (actor_type in ('user', 'system', 'worker')),
    actor_id text,
    action text not null,
    entity_type text not null,
    entity_id uuid not null,
    before jsonb,
    after jsonb,
    created_at timestamptz not null default now()
);

comment on column audit_log.actor_id is
    'users.id as text when actor_type = ''user''; a worker/process identifier '
    'otherwise. Not a foreign key -- system/worker actors are not rows in users.';
comment on column audit_log.action is
    'e.g. status_transition, approval_decided, invoice_corrected.';
comment on column audit_log.before is
    'Entity state before the mutation, or null for a creation event.';
comment on column audit_log.after is
    'Entity state after the mutation, or null for a deletion event.';

-- Append-only: every state mutation in the system writes a new row here, never
-- edits or removes one. This is enforced at the database level, not just by
-- application discipline, per CLAUDE.md's non-negotiable principles.
create function audit_log_block_mutation() returns trigger as $$
begin
    raise exception 'audit_log is append-only: % is not permitted', tg_op;
end;
$$ language plpgsql;

create trigger audit_log_no_update
    before update on audit_log
    for each row execute function audit_log_block_mutation();

create trigger audit_log_no_delete
    before delete on audit_log
    for each row execute function audit_log_block_mutation();
