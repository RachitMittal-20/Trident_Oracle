-- Benchmark harness data (packages/evals). Not tenant-scoped: these are
-- internal measurements of extraction quality, not tenant business data.
create table eval_runs (
    id uuid primary key default gen_random_uuid(),
    dataset text not null,
    backend text not null check (backend in ('gemini', 'tesseract')),
    model_version text,
    sample_count integer not null,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    created_at timestamptz not null default now()
);

comment on column eval_runs.dataset is
    'e.g. docile, cord, sroie.';

create table eval_results (
    id uuid primary key default gen_random_uuid(),
    eval_run_id uuid not null references eval_runs (id),
    field_path text not null,
    precision numeric(5, 4),
    recall numeric(5, 4),
    f1 numeric(5, 4),
    exact_match_rate numeric(5, 4),
    mean_confidence numeric(5, 4),
    mean_latency_ms numeric(10, 2),
    created_at timestamptz not null default now()
);

comment on column eval_results.field_path is
    'Per-field breakdown, e.g. header.total, lines[].qty -- matches '
    'field_confidences.field_path conventions.';
