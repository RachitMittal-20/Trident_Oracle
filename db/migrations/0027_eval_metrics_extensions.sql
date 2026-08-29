-- Extends the benchmark harness schema (0009_evals.sql) for packages/evals:
-- confidence calibration and line-item recognition are both explicit,
-- non-optional deliverables of that harness ("Make sure that point is
-- visible in the output" -- calibration in particular), so they need
-- somewhere durable to land, not just a stdout report that's gone the
-- next time someone runs `python -m evals run`.

-- "mock" (extractors.mock.MockExtractor) is a real, registered backend
-- (extractors/factory.py) used throughout this codebase's own test suite;
-- the original check constraint only allowed the two production backends.
-- Recording a mock run is exactly how packages/evals/tests/test_cli.py
-- exercises the full run -> persist -> report path without a live Gemini
-- key or a Tesseract install, and that self-check has to persist somewhere
-- real, not bypass the same constraint production runs go through.
alter table eval_runs drop constraint eval_runs_backend_check;
alter table eval_runs add constraint eval_runs_backend_check
    check (backend in ('gemini', 'tesseract', 'mock'));

-- Run-level aggregates: one value per run, not per field, so these live on
-- eval_runs directly rather than repeated onto every eval_results row.
alter table eval_runs add column mean_latency_ms numeric(10, 2);
alter table eval_runs add column total_estimated_cost_usd numeric(12, 6);
alter table eval_runs add column line_item_precision numeric(5, 4);
alter table eval_runs add column line_item_recall numeric(5, 4);
alter table eval_runs add column line_item_f1 numeric(5, 4);
alter table eval_runs add column line_item_n_ground_truth integer;
alter table eval_runs add column line_item_n_predicted integer;
alter table eval_runs add column line_item_n_matched integer;

comment on column eval_runs.total_estimated_cost_usd is
    'A rough estimate from token counts, not a reconciled bill -- see '
    'packages/evals/evals/metrics.py::estimated_cost_usd.';

-- Per-field numeric-comparison metrics that didn't exist in the original
-- schema (mean absolute error, within-tolerance rate) -- both nullable,
-- since non-numeric fields (vendor_name, description) never populate them.
alter table eval_results add column mean_absolute_error numeric(14, 4);
alter table eval_results add column within_tolerance_rate numeric(5, 4);

-- Calibration is genuinely 1:many per run (one row per confidence bucket),
-- unlike the line-item metrics above -- a separate table, not more columns
-- on eval_runs.
create table eval_run_calibration (
    id uuid primary key default gen_random_uuid(),
    eval_run_id uuid not null references eval_runs (id),
    bucket_low numeric(3, 2) not null,
    bucket_high numeric(3, 2) not null,
    n integer not null,
    mean_confidence numeric(5, 4),
    actual_accuracy numeric(5, 4),
    created_at timestamptz not null default now()
);

comment on column eval_run_calibration.actual_accuracy is
    'Fraction of predictions in this confidence bucket that were actually '
    'correct (normalized-exact-match against ground truth). Compared '
    'against mean_confidence, this is the calibration gap the CLI reports -- '
    'the whole reason this table exists (see packages/evals/evals/metrics.py '
    'module docstring: "our entire decision matrix depends on trusting '
    'confidence scores").';
