-- Backs the /v1/events/stream SSE endpoint (apps/api/api/events.py). Every
-- invoice status change in this codebase -- creation at RECEIVED, and every
-- subsequent transition -- already goes through exactly one of two
-- statements: `INSERT INTO invoices (..., status) VALUES (..., 'RECEIVED')`
-- (apps/api/api/db.py::insert_invoice) or `UPDATE invoices SET status = ...`
-- (apps/worker/worker/extract_handler.py, match_handler.py, and
-- apps/api/api/approvals.py, all via the shared `_transition` helper that
-- calls core.state_machine.validate_transition first). That covers every
-- event apps/api/api/events.py needs to publish -- job queued (RECEIVED),
-- job started (EXTRACTING or MATCHING), extraction done (EXTRACTED /
-- EXTRACTION_FAILED), match done (MATCHED_CLEAN / NEEDS_VERIFICATION /
-- EXCEPTIONS_RAISED), decision made (AUTO_POSTED / PENDING_APPROVAL), and
-- approval received (APPROVED / REJECTED) -- without adding a second
-- trigger on `jobs`, and without the worker or apps/api ever importing or
-- knowing about the SSE bridge. That decoupling is the point of a
-- LISTEN/NOTIFY bridge: the publisher (worker or apps/api) only ever writes
-- to Postgres exactly as it always has.
create or replace function notify_invoice_pipeline_event() returns trigger as $$
begin
    perform pg_notify(
        'trident_pipeline_events',
        jsonb_build_object(
            'invoice_id', new.id,
            'tenant_id', new.tenant_id,
            'from_status', case when tg_op = 'INSERT' then null else old.status end,
            'to_status', new.status,
            'occurred_at', now()
        )::text
    );
    return new;
end;
$$ language plpgsql;

create trigger invoices_notify_insert
    after insert on invoices
    for each row execute function notify_invoice_pipeline_event();

create trigger invoices_notify_status_change
    after update of status on invoices
    for each row
    when (old.status is distinct from new.status)
    execute function notify_invoice_pipeline_event();
