-- core.matching.line_matcher.LineMatch already computes a per-line
-- confidence score (exact SKU, fuzzy token-set-ratio, or the LLM
-- fallback's own reported score) -- this gives it somewhere to live.
-- Paired with the matched_po_line_id/match_method columns
-- 0004_invoices.sql already declared but apps/worker/worker/
-- match_handler.py never actually wrote until now (see that file's
-- _persist_line_matches), invoice_lines becomes a complete record of how
-- each line was resolved, not just that it was.
alter table invoice_lines add column match_confidence numeric(5, 4)
    check (match_confidence between 0 and 1);

comment on column invoice_lines.match_confidence is
    'core.matching.line_matcher.LineMatch.confidence -- null for an unmatched line '
    '(match_method = ''unmatched''), 1.0 for an exact SKU match, the token-set-ratio '
    'score for fuzzy, the model''s own reported score for llm.';
