create table jobs (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references tenants (id),
    job_type text not null check (job_type in ('extract', 'match', 'notify', 'post')),
    payload jsonb not null default '{}'::jsonb,
    status text not null default 'queued'
        check (status in ('queued', 'running', 'done', 'failed', 'dead')),
    attempts integer not null default 0,
    max_attempts integer not null default 3,
    idempotency_key text not null unique,
    run_after timestamptz not null default now(),
    locked_at timestamptz,
    locked_by text,
    last_error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

comment on column jobs.payload is
    'Job-type-specific input, e.g. {"invoice_id": "..."} for extract/match.';
comment on column jobs.idempotency_key is
    'sha256(tenant_id + content_hash) for extraction, '
    'sha256(tenant_id + exception_id + recipient + channel) for notification. '
    'Re-submitting returns the existing row instead of creating a duplicate.';
comment on column jobs.locked_at is
    'Set when a worker claims the row via FOR UPDATE SKIP LOCKED. A stale-lock '
    'reaper returns any row with locked_at older than 10 minutes to queued.';
comment on column jobs.locked_by is
    'Identifier of the worker process holding the lock.';

create index idx_jobs_status_run_after on jobs (status, run_after);

create table dead_letters (
    id uuid primary key default gen_random_uuid(),
    job_id uuid not null references jobs (id),
    payload jsonb not null,
    final_error text not null,
    created_at timestamptz not null default now()
);

create table notification_deliveries (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references tenants (id),
    channel text not null check (channel in ('telegram', 'email', 'whatsapp')),
    recipient text not null,
    idempotency_key text not null unique,
    status text not null default 'pending'
        check (status in ('pending', 'sent', 'failed', 'dead')),
    attempts integer not null default 0,
    next_retry_at timestamptz,
    provider_message_id text,
    error text,
    sent_at timestamptz,
    created_at timestamptz not null default now()
);

comment on column notification_deliveries.idempotency_key is
    'sha256(tenant_id + exception_id + recipient + channel). A retried delivery '
    'after a timeout must not send a second message.';
comment on column notification_deliveries.provider_message_id is
    'Message ID returned by the channel provider (e.g. Telegram message_id), for '
    'correlating inbound callbacks/webhooks back to this delivery.';
