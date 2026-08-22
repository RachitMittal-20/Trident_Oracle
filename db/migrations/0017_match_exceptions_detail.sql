-- match_exceptions (0006_matching.sql) had nowhere to record *why* an
-- exception fired beyond its typed fields (expected/actual/delta) -- but
-- core.matching.duplicates.DuplicateFinding and core.matching.three_way.
-- MatchFinding were both designed around a mandatory `detail` string
-- specifically because a reviewer needs the reasoning ("resembles invoice
-- INV-1044 from Acme Corp: total within 0.3%, dated 2 days apart, 8/10 line
-- items match"), not just a bare exception_type + severity. Without this
-- column, the worker's 'match' handler would have to throw that reasoning
-- away when persisting a MatchFinding as a match_exceptions row.
alter table match_exceptions add column detail text not null default '';

comment on column match_exceptions.detail is
    'Human-readable reasoning for this exception, from core.matching''s '
    'MatchFinding/DuplicateFinding.detail -- shown to the reviewer in the '
    'exception queue UI, not just the bare exception_type.';
