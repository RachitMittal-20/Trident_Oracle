create table tolerance_policies (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references tenants (id),
    name text not null,
    is_active boolean not null default true,
    rules jsonb not null,
    version integer not null default 1,
    created_at timestamptz not null default now()
);

comment on column tolerance_policies.rules is
    'e.g. {"price_variance_pct": 2.0, "qty_tolerance_pct": 0.0, '
    '"auto_approve_below": 5000, "dual_approval_above": 100000, '
    '"min_field_confidence": 0.85, "duplicate_window_days": 90}';
comment on column tolerance_policies.version is
    'Referenced by match_runs.policy_version so a historical run can be traced '
    'back to the exact ruleset that produced it, even after the policy changes.';

create table match_runs (
    id uuid primary key default gen_random_uuid(),
    invoice_id uuid not null references invoices (id),
    policy_version integer not null,
    result text not null check (result in ('clean', 'exceptions', 'blocked')),
    duration_ms integer not null,
    executed_at timestamptz not null default now(),
    created_at timestamptz not null default now()
);

create table match_exceptions (
    id uuid primary key default gen_random_uuid(),
    match_run_id uuid not null references match_runs (id),
    invoice_id uuid not null references invoices (id),
    exception_type text not null check (exception_type in (
        'NO_PO', 'NO_GRN', 'DUPLICATE_INVOICE', 'SUSPECTED_DUPLICATE',
        'PRICE_VARIANCE', 'QTY_SHORT', 'QTY_OVER', 'UNMATCHED_LINE',
        'ARITHMETIC_ERROR', 'TAX_MISMATCH', 'DATE_ANOMALY'
    )),
    severity text not null check (severity in ('info', 'warn', 'block')),
    po_line_id uuid references purchase_order_lines (id),
    invoice_line_id uuid references invoice_lines (id),
    expected_value numeric(14, 2),
    actual_value numeric(14, 2),
    delta numeric(14, 2),
    delta_pct numeric(7, 4),
    -- Provisional lifecycle; architecture doc describes the exception queue UI but
    -- not a formal status enum. Revisit alongside the exception-queue resolution flow.
    status text not null default 'open'
        check (status in ('open', 'resolved', 'dismissed')),
    resolved_by uuid references users (id),
    resolved_at timestamptz,
    resolution_note text,
    created_at timestamptz not null default now()
);

comment on column match_exceptions.expected_value is
    'e.g. grn.qty_received or po.unit_price, depending on exception_type.';
comment on column match_exceptions.actual_value is
    'e.g. invoice.qty or invoice.unit_price, depending on exception_type.';

create index idx_match_exceptions_invoice_id_status on match_exceptions (invoice_id, status);
