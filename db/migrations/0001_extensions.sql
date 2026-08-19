-- gen_random_uuid() for every table's primary key default.
create extension if not exists pgcrypto;

-- Trigram similarity index support, used for fuzzy line-item matching
-- (packages/core matching engine stage 3) against normalized_description.
create extension if not exists pg_trgm;
