-- Enables apps/api's /v1/benchmarks/* read endpoints (packages/evals'
-- consumer) and adds the per-document storage the failure gallery needs.
--
-- app_role has never had any grant on eval_runs/eval_results
-- (0013_app_role.sql's own comment: "not tenant-scoped", deliberately
-- excluded) -- apps/api's get_connection() connects as app_role
-- (apps/api/api/config.py), so without this grant the benchmarks route
-- can't read this data at all. SELECT-only: writing stays exclusively
-- packages/evals' job, via its own direct DATABASE_URL connection
-- (evals/storage.py), same as it already was.
grant select on eval_runs to app_role;
grant select on eval_results to app_role;
grant select on eval_run_calibration to app_role;

-- Per-field document count, needed to compute a genuinely weighted
-- "overall exact-match rate" across fields (the headline strip) instead of
-- an unweighted average across fields with wildly different sample sizes.
alter table eval_results add column n integer;

-- Latency PERCENTILES, not just the single mean_latency_ms already on
-- eval_runs -- "Latency distribution" (the /benchmarks prompt) needs more
-- than one number. Computed the same way apps/api/api/analytics_view.py's
-- pipeline latency percentiles are (percentile_cont equivalent, done in
-- Python here since the raw per-document latencies only ever exist inside
-- one packages/evals/evals/runner.py run, never as their own SQL rows).
alter table eval_runs add column latency_p50_ms numeric(10, 2);
alter table eval_runs add column latency_p95_ms numeric(10, 2);
alter table eval_runs add column latency_p99_ms numeric(10, 2);

-- One row per document in a run -- neither eval_runs (1 per run) nor
-- eval_results (1 per field per run) can answer "show me the worst
-- documents": that needs per-document ground truth/prediction and a
-- rankable badness score. thumbnail_path is nullable: a run persisted
-- without Storage configured (see evals/cli.py) still records everything
-- except the image itself.
create table eval_run_documents (
    id uuid primary key default gen_random_uuid(),
    eval_run_id uuid not null references eval_runs (id),
    doc_id text not null,
    ground_truth jsonb not null,
    extraction_result jsonb not null,
    mismatch_count integer not null,
    thumbnail_path text,
    mime_type text,
    created_at timestamptz not null default now()
);

comment on column eval_run_documents.mismatch_count is
    'Count of header fields where the normalized ground truth and '
    'normalized prediction disagree (packages/evals/evals/metrics.py) -- '
    'the failure gallery''s sort key: highest mismatch_count first.';
comment on column eval_run_documents.thumbnail_path is
    'Path in the private Storage bucket (same bucket real invoice uploads '
    'use, under an evals/ prefix), signed on read -- never a public URL, '
    'same rule as invoices.source_file_path.';

create index idx_eval_run_documents_eval_run_id_mismatch_count
    on eval_run_documents (eval_run_id, mismatch_count desc);

grant select on eval_run_documents to app_role;
