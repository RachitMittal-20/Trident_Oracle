create table approval_requests (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references tenants (id),
    invoice_id uuid not null references invoices (id),
    exception_id uuid references match_exceptions (id),
    token_hash text not null check (char_length(token_hash) = 64),
    channel text not null check (channel in ('telegram', 'email', 'whatsapp')),
    recipient text not null,
    expires_at timestamptz not null,
    consumed_at timestamptz,
    decision text check (decision in ('approved', 'rejected')),
    decided_by uuid references users (id),
    decided_at timestamptz,
    decision_note text,
    created_at timestamptz not null default now()
);

comment on column approval_requests.exception_id is
    'Nullable: a request can be for a plain PENDING_APPROVAL invoice with no '
    'specific exception attached (e.g. dual approval above a value threshold).';
comment on column approval_requests.token_hash is
    'SHA-256 hex digest of a 32-random-byte base64url token. Only the hash is '
    'ever stored -- the raw token exists only in the outbound notification link.';
comment on column approval_requests.consumed_at is
    'Set the first time the token is used. Enforces single-use in application code.';
comment on column approval_requests.decision is
    'NULL until decided_by acts. Not the sole authorization check -- application '
    'code must also verify expires_at and consumed_at.';
