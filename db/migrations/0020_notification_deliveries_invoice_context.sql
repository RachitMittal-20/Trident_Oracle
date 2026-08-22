-- notification_deliveries (0005_queue.sql) tracked channel/recipient/status
-- but nothing linking a delivery row back to the invoice (or specific
-- exception) it's about -- GET /v1/deliveries (the dashboard's delivery
-- list) needs that to be useful at all: "3 deliveries are pending" isn't
-- actionable without knowing which invoices they're for.
--
-- Both nullable: exception_id mirrors approval_requests.exception_id's own
-- nullability (a notification can be about an invoice generally -- e.g. a
-- dual-approval-required clean invoice -- with no specific exception
-- attached), and invoice_id is nullable for the same reason match_runs/
-- match_exceptions aren't the only conceivable notification_deliveries
-- consumer even though every notify job in this project today is invoice-
-- related.
alter table notification_deliveries add column invoice_id uuid references invoices (id);
alter table notification_deliveries add column exception_id uuid references match_exceptions (id);

comment on column notification_deliveries.invoice_id is
    'Which invoice this delivery is about -- nullable for forward compatibility, '
    'always set in practice by worker.notify_handler.';
comment on column notification_deliveries.exception_id is
    'Which match_exceptions row this delivery is about, if any -- nullable the '
    'same way approval_requests.exception_id is (a delivery can be for a plain '
    'invoice-level decision with no specific exception attached).';

create index idx_notification_deliveries_invoice_id on notification_deliveries (invoice_id);
