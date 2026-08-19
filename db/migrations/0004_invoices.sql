create table invoices (
    id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references tenants (id),
    vendor_id uuid references vendors (id),
    po_id uuid references purchase_orders (id),
    invoice_number text not null,
    invoice_date date not null,
    due_date date,
    currency text not null,
    subtotal numeric(14, 2) not null,
    tax numeric(14, 2) not null,
    total numeric(14, 2) not null,
    source_channel text not null check (source_channel in ('upload', 'email', 'webhook')),
    source_file_path text not null,
    content_hash text not null check (char_length(content_hash) = 64),
    extraction_backend text check (extraction_backend in ('gemini', 'tesseract')),
    overall_confidence numeric(5, 4) check (overall_confidence between 0 and 1),
    status text not null default 'RECEIVED' check (status in (
        'RECEIVED', 'EXTRACTING', 'EXTRACTION_FAILED', 'EXTRACTED', 'MATCHING',
        'MATCHED_CLEAN', 'NEEDS_VERIFICATION', 'EXCEPTIONS_RAISED', 'AUTO_POSTED',
        'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'POSTED'
    )),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (tenant_id, content_hash)
);

comment on column invoices.vendor_id is
    'Nullable: unresolved until extraction + vendor-name normalization runs.';
comment on column invoices.po_id is
    'Nullable: unresolved until document linkage (matching engine stage 2) runs.';
comment on column invoices.source_file_path is
    'Supabase Storage path in the private bucket. Always accessed via a signed URL.';
comment on column invoices.content_hash is
    'SHA-256 hex digest of the raw file bytes. Hard-duplicate dedupe key (see unique '
    'constraint below) -- a repeat upload is rejected before extraction even runs.';
comment on column invoices.extraction_backend is
    'Nullable until an extraction job runs. gemini is primary, tesseract is the '
    'free-tier-quota fallback.';
comment on column invoices.overall_confidence is
    'Nullable until extraction completes. Low confidence always beats a clean match '
    'in the decision matrix (matching engine stage 6).';
comment on column invoices.status is
    'Server-side transition validation only -- see docs/ARCHITECTURE.md section 5. '
    'Illegal transitions raise, they do not silently no-op.';

create index idx_invoices_tenant_id_status on invoices (tenant_id, status);

create table invoice_lines (
    id uuid primary key default gen_random_uuid(),
    invoice_id uuid not null references invoices (id),
    line_no integer not null,
    description text not null,
    normalized_description text,
    qty numeric(14, 3) not null,
    unit_price numeric(14, 2) not null,
    line_total numeric(14, 2) not null,
    matched_po_line_id uuid references purchase_order_lines (id),
    match_method text check (match_method in ('sku', 'fuzzy', 'llm', 'unmatched')),
    created_at timestamptz not null default now()
);

comment on column invoice_lines.match_method is
    'How matched_po_line_id was resolved: sku (exact, confidence 1.0), fuzzy '
    '(token-set ratio >= 0.88), llm (batched fallback for what is left), or '
    'unmatched (matched_po_line_id is null, raises UNMATCHED_LINE).';

create table field_confidences (
    id uuid primary key default gen_random_uuid(),
    invoice_id uuid not null references invoices (id),
    field_path text not null,
    confidence numeric(5, 4) not null check (confidence between 0 and 1),
    bbox jsonb,
    raw_text text,
    created_at timestamptz not null default now()
);

comment on column field_confidences.field_path is
    'Dotted/indexed path into the extracted document, e.g. header.total, lines[2].qty.';
comment on column field_confidences.bbox is
    'Bounding box on the source page: {page, x, y, w, h}. Drives the frontend '
    'verification-screen overlay draw-in animation.';
