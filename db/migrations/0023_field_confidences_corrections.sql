-- Backs the /invoices/[id]/verify screen's inline-edit flow
-- (apps/api/api/verification.py). A human correction to an extracted field
-- is training signal, not just a display update -- these columns record
-- that a human, not the extractor, is now the source of truth for this
-- field, distinct from field_confidences.confidence (the extractor's own
-- score, left untouched historically by not overwriting it in place --
-- see verification.py's comment on why confidence is instead set to 1.0
-- rather than preserved: once corrected, the field is no longer usefully
-- described by an extraction-time confidence score at all).
alter table field_confidences add column human_corrected boolean not null default false;
alter table field_confidences add column corrected_at timestamptz;

comment on column field_confidences.human_corrected is
    'Set true by a correction on the verification screen. Training signal: '
    'distinguishes a field a human confirmed/fixed from one still trusting '
    'the original extractor output.';
comment on column field_confidences.corrected_at is
    'Nullable -- set the same moment human_corrected flips to true.';
