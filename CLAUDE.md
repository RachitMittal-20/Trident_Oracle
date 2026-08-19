# CLAUDE.md

> Put this at the repo root **before** running any other prompt. Claude Code reads it
> automatically on every session, which keeps all 24 prompts consistent. Without it you
> will get drift — different naming, different error handling, different styling by week three.

---

## Project

**Trident Oracle** — an invoice intake and three-way match engine.

Vendors send invoices as PDFs or phone photos. The system extracts structured data from them,
matches that data against the corresponding Purchase Order and Goods Receipt, flags
discrepancies, and routes exceptions to a human approver via Telegram. Clean, high-confidence,
low-value invoices post automatically.

This is a solo 45-day internship project. It must be complete and demoable, not sprawling.

---

## Non-negotiable principles

1. **`packages/core/` performs no I/O.** The matching engine is pure functions: given a PO,
   a GRN, an invoice, and a tolerance policy, return exceptions. No DB calls, no HTTP,
   no file reads. This is the most important rule in the repo.
2. **Every external dependency sits behind an interface.** Extractors and notifiers are
   swappable. Never import `google.generativeai` or a Telegram SDK outside its adapter.
3. **Idempotency is mandatory** on anything that can be retried. Every job and every
   notification carries a UNIQUE `idempotency_key`.
4. **The audit log is append-only.** Every state mutation writes to it. A DB trigger raises
   on UPDATE or DELETE against `audit_log`.
5. **Server-side state transition validation.** Status is never set directly by a client.
   Illegal transitions raise an exception; they do not silently no-op.
6. **RLS is the authorization boundary**, including in the worker. The worker sets
   `app.tenant_id` per job. Never bypass RLS with a service-role key in request paths.
7. **Fail loudly in development, degrade gracefully in production.** No bare `except:`.
   No swallowed errors.

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 15 (App Router), TypeScript, Tailwind, shadcn/ui, Framer Motion, **anime.js v4**, Recharts |
| API | FastAPI (Python 3.11+), Pydantic v2 |
| Worker | Python, polls Postgres queue |
| Database | Supabase Postgres, RLS enabled on every table |
| Storage | Supabase Storage, private bucket, signed URLs only |
| Extraction | Gemini Flash (free tier) primary, Tesseract local fallback |
| Notifications | Telegram Bot API primary, SMTP secondary, WhatsApp adapter stubbed |
| Queue | Postgres `FOR UPDATE SKIP LOCKED` — **no Redis, no Celery** |
| Tests | pytest (Python), Vitest (TS) |

**Gemini model note:** `gemini-2.5-flash` returned a 404 for new API keys as of the day-one
sanity check (Aug 2026) — it's no longer available to new users. `gemini-3.6-flash` is confirmed
working (exact-match extraction test against a synthetic invoice with known values). `GeminiExtractor`
reads the model name from `GEMINI_MODEL`, defaulting to `gemini-3.6-flash`, specifically so the
next free-tier model rotation is a config change, not a code change.

---

## Layout

```
apps/web/        Next.js dashboard
apps/api/        FastAPI — REST + webhooks
apps/worker/     Worker loop
packages/core/       pure domain logic — matching, policy, decisions
packages/extractors/ extraction adapters
packages/notifiers/  notification adapters
packages/evals/      benchmark harness
db/migrations/       numbered SQL migrations
docs/                ARCHITECTURE.md · DECISIONS.md · DEMO.md
```

---

## Conventions

**Python**

- Type hints on every function signature. `mypy --strict` should pass in `packages/core/`.
- Pydantic v2 models for all boundary data. Domain objects are dataclasses.
- Custom exception hierarchy rooted at `TridentOracleError`. Never raise bare `Exception`.
- Structured logging via `structlog` — JSON output, always include `tenant_id`, `job_id`,
  `invoice_id` when in scope. No `print()`.
- Money is `Decimal`, never `float`. Store as `NUMERIC(14,2)`.

**TypeScript**

- No `any`. Use `unknown` and narrow.
- Server Components by default; `"use client"` only where interactivity demands it.
- Types generated from the Supabase schema, never hand-written.

**SQL**

- Numbered migrations: `db/migrations/0001_description.sql`. Never edit a committed migration.
- Every table: `id uuid primary key default gen_random_uuid()`, `created_at timestamptz default now()`.
- Every tenant-scoped table has `tenant_id` plus an RLS policy. No exceptions.

**Git**

- Conventional commits: `feat(scope):`, `fix(scope):`, `test(scope):`, `docs(scope):`, `chore(scope):`
- Commit after each completed prompt. Never commit `.env`, datasets, or `node_modules`.

---

## Frontend rules

**Design tone:** engineered, not playful. Dark, high contrast, generous negative space.
Reference points are Linear and the Vercel dashboard. No gradient blobs, no bouncy easing,
no decorative motion.

**Palette** (define as CSS variables, never hardcode hex in components):

```
bg-base      #08090B
bg-raised    #101215
bg-overlay   #16191E
border       #21262D
text-primary #E6EDF3
text-muted   #8B949E
accent       #6E7BFF
signal-clean #2EA88A
signal-warn  #D9A343
signal-block #E5534B
```

Monospace (JetBrains Mono or Geist Mono) for all numbers, IDs, currency and deltas —
with `font-variant-numeric: tabular-nums` on anything that animates.

**Library responsibilities — do not blur these:**

- **shadcn/ui** → structural primitives (Dialog, Sheet, Table, Tabs, Badge, Tooltip, Command)
- **Framer Motion** → layout transitions, `AnimatePresence`, shared-layout expansion
- **anime.js v4** → all expressive motion: SVG path drawing, timelines, staggers, number counters

**anime.js v4 notes.** v4 is ESM with a function-based API:
`import { animate, createTimeline, stagger, svg } from 'animejs'`. This differs substantially
from v3's `anime({ targets: ... })`. Pin the version in `package.json` and consult the
installed version's docs before writing animation code — do not write v3 syntax from memory.

**Motion rules:**

- Max 600ms, except the one-time pipeline rail draw on mount
- `easeOutExpo` for entrances, `easeInOutQuad` for state changes. No bounce, no elastic.
- Animate `transform` and `opacity` only — never `width`, `height`, `top`, `left`
- Every anime.js call gated behind a `prefers-reduced-motion` check (use a shared
  `useReducedMotion` hook — write it once, use it everywhere)
- Clean up animations on unmount
- **Every animation must encode information.** If it doesn't tell the user something about
  state, progress, or relationship, delete it.

---

## Testing expectations

- `packages/core/` requires unit tests for every matching rule, including boundary cases at
  exactly the tolerance threshold. This is the most-tested code in the repo.
- Golden-file tests for extraction: fixture image in, expected JSON out.
- Queue tests must cover: concurrent claim (no double-processing), retry backoff,
  dead-lettering after max attempts, stale lock recovery.
- Do not write tests for framework glue or trivial getters.

---

## Things Claude should NOT do

- Do not add Redis, Celery, RabbitMQ, Kafka, or any broker. Postgres is the queue.
- Do not add an ORM migration tool. Plain numbered SQL files.
- Do not install a component library other than shadcn/ui.
- Do not use `float` for money.
- Do not put business logic in API route handlers — handlers validate, call `core`, and serialize.
- Do not commit secrets, dataset files, or generated artifacts.
- Do not scaffold features that aren't in the current prompt. Build exactly what's asked.
- Do not add animations that don't convey state.

---

## Current status

<!-- Update this line after each phase so a fresh session has context. -->

Phase: 0 (Foundation) — complete. Monorepo scaffold, DB schema + RLS migrations,
packages/core domain models, and seed data are done. Phase 1 (Extraction) starts next.
