"""Pure job-queue domain logic: the Job/DeadLetter value objects and the
backoff schedule. No I/O -- see apps/worker for the actual Postgres access
layer that reads and writes these. Splitting it this way keeps the retry
math and the row shape testable without a database, per CLAUDE.md's rule
that packages/core never does I/O.
"""
