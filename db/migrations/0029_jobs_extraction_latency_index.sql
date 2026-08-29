-- apps/api/api/analytics_view.py's extraction-latency query (flagged in
-- Prompt 20's EXPLAIN pass, not fixed at the time since it wasn't
-- audit_log's own scan and jobs isn't append-only-forever the way
-- audit_log is) filters jobs on tenant_id (via RLS), job_type, status, and
-- a locked_at is not null / updated_at range -- none of which
-- idx_jobs_status_run_after (0005_queue.sql, keyed on status + run_after,
-- built for the queue claimer's own "next runnable job" query) can serve.
-- Confirmed via EXPLAIN (ANALYZE, BUFFERS) with enable_seqscan off against
-- a disposable Postgres: without this index, that query is a Seq Scan.
create index idx_jobs_tenant_id_job_type_status_updated_at
    on jobs (tenant_id, job_type, status, updated_at);
