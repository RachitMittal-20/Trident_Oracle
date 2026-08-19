create table vendors (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references tenants (id),
    name text not null,
    normalized_name text not null,
    tax_id text,
    email text,
    created_at timestamptz not null default now()
);

comment on column vendors.normalized_name is
    'Lowercased, punctuation-stripped form of name, used to collapse "ACME Corp.", '
    '"Acme Corporation", "ACME CORP" etc. into one entity before matching.';

create index idx_vendors_tenant_id_normalized_name on vendors (tenant_id, normalized_name);

create table purchase_orders (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references tenants (id),
    vendor_id uuid not null references vendors (id),
    po_number text not null,
    issued_at timestamptz not null,
    currency text not null,
    subtotal numeric(14, 2) not null,
    tax numeric(14, 2) not null,
    total numeric(14, 2) not null,
    -- Provisional lifecycle; architecture doc does not enumerate PO status values.
    -- Revisit once the procurement intake flow (outside this project's scope) is defined.
    status text not null default 'open'
        check (status in ('open', 'partially_received', 'closed', 'cancelled')),
    created_at timestamptz not null default now()
);

create table purchase_order_lines (
    id uuid primary key default gen_random_uuid(),
    po_id uuid not null references purchase_orders (id),
    line_no integer not null,
    sku text,
    description text not null,
    normalized_description text not null,
    qty_ordered numeric(14, 3) not null,
    unit_price numeric(14, 2) not null,
    tax_rate numeric(5, 2) not null default 0,
    line_total numeric(14, 2) not null,
    created_at timestamptz not null default now()
);

comment on column purchase_order_lines.normalized_description is
    'Lowercased, punctuation-stripped, abbreviation-expanded, token-sorted form of '
    'description, used for token-set-ratio fuzzy matching (matching engine stage 3).';

create index idx_purchase_order_lines_normalized_description_trgm
    on purchase_order_lines using gin (normalized_description gin_trgm_ops);

create table goods_receipts (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references tenants (id),
    po_id uuid not null references purchase_orders (id),
    grn_number text not null,
    received_at timestamptz not null,
    received_by uuid not null references users (id),
    created_at timestamptz not null default now()
);

create table goods_receipt_lines (
    id uuid primary key default gen_random_uuid(),
    grn_id uuid not null references goods_receipts (id),
    po_line_id uuid not null references purchase_order_lines (id),
    qty_received numeric(14, 3) not null,
    condition text not null check (condition in ('good', 'damaged', 'partial')),
    notes text,
    created_at timestamptz not null default now()
);

comment on column goods_receipt_lines.qty_received is
    'What actually arrived. The matching engine checks invoice.qty against this, '
    'never against purchase_order_lines.qty_ordered -- that is the point of three-way match.';
