-- Closes the gap flagged in worker/match_handler.py's module docstring
-- (added alongside 0019/0020): the notify pipeline had no way to resolve an
-- actual approver's contact info, so a real notify job's payload was left
-- incomplete on purpose rather than improvised. users.email already exists
-- (0002_tenancy.sql) and is always populated; telegram_chat_id is the only
-- missing piece -- nullable, since not every approver has linked a Telegram
-- chat. worker.match_handler resolves channel per approver from this: a set
-- telegram_chat_id means channel='telegram' (recipient=telegram_chat_id),
-- otherwise channel='email' (recipient=users.email).
alter table users add column telegram_chat_id text;

comment on column users.telegram_chat_id is
    'Telegram chat id to notify this user at, e.g. from /start with the '
    'approvals bot. Nullable -- worker.match_handler falls back to email '
    '(always populated) when this is unset.';
