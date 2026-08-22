-- approval_requests.token_hash (0007_approvals.sql) had a length CHECK but
-- no uniqueness constraint. redeem_approval_token looks a row up by
-- token_hash alone -- a duplicate hash across two different rows would mean
-- "which invoice does this token even belong to" has more than one answer,
-- which must be impossible by construction, not just improbable because
-- SHA-256 collisions are astronomically unlikely.
alter table approval_requests
    add constraint approval_requests_token_hash_key unique (token_hash);
