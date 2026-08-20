-- Ingestion (apps/api's POST /v1/invoices/upload) inserts an invoices row
-- at status RECEIVED, before any extraction has run. At that point
-- invoice_number, invoice_date, subtotal, tax, and total are genuinely
-- unknown -- not just temporarily blank -- since they only exist once a
-- vision/OCR backend has read the document. 0004_invoices.sql declared them
-- NOT NULL, which only fits an already-extracted invoice; this loosens them
-- to nullable so the RECEIVED-state row is representable at all. The
-- worker's extract handler (apps/worker) fills them in when it transitions
-- the invoice to EXTRACTED.
alter table invoices alter column invoice_number drop not null;
alter table invoices alter column invoice_date drop not null;
alter table invoices alter column subtotal drop not null;
alter table invoices alter column tax drop not null;
alter table invoices alter column total drop not null;
