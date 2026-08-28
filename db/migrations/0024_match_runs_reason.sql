-- Backs the /invoices/[id]/match screen's footer, which shows "the
-- Decision, with its full reason string" (core.decision.Decision.reason).
-- That string was previously only ever written into audit_log.after and
-- notification bodies (apps/worker/worker/match_handler.py) -- readable
-- for one specific historical event, but not a stable, queryable field a
-- screen can just select. Persisting it alongside policy_version (already
-- on this table for the same "keep a historical run explicable" reason)
-- means a match_runs row is self-contained: what happened, under which
-- policy, and why.
alter table match_runs add column reason text;

comment on column match_runs.reason is
    'core.decision.Decision.reason at the time this run''s outcome was decided -- '
    'persisted so a historical run stays explicable without recomputing decide() '
    'against data (e.g. the active policy) that may have since changed.';
