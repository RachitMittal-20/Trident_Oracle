-- extraction_backend's CHECK constraint (0004_invoices.sql) only allowed
-- 'gemini' and 'tesseract' -- but 'mock' is a real, named backend in
-- packages/extractors/factory.py's _BACKENDS, selectable via
-- EXTRACTOR_BACKEND=mock, and MockExtractor legitimately sets
-- ExtractionResult.backend="mock". Every extract-handler test that uses
-- MockExtractor (deliberately, so the suite never touches a real
-- vision/OCR backend) hits this constraint when persisting the result --
-- caught by actually running those tests against a real Postgres, not
-- just by reading the code.
alter table invoices drop constraint invoices_extraction_backend_check;
alter table invoices add constraint invoices_extraction_backend_check
    check (extraction_backend in ('gemini', 'tesseract', 'mock'));
