# Trident Oracle — System Architecture

**Invoice Intake & Three-Way Match Engine**
Doritech Internship Project · 45 days · Solo build · Zero budget

---

## 1. What the system does

Vendors send invoices as PDFs or phone photos. Trident Oracle reads them, checks them against
what was actually ordered (Purchase Order) and what actually arrived (Goods Receipt), and
flags anything that doesn't line up. Clean invoices post automatically. Suspicious ones go
to a human, who approves or rejects from their phone.

This is called **three-way matching**. Every procurement department on earth does it by hand.

```
                                          ┌──────────────────┐
  Invoice (PDF/photo) ──▶ EXTRACT ──▶ ... │  PO  ↔ GRN ↔ INV │ ... ──▶ DECIDE
                          (vision)        └──────────────────┘         │
                                             three-way match           │
                                                                       ▼
                                              ┌────────────────────────────────┐
                                              │ clean + confident + under cap  │──▶ AUTO-POST
                                              │ low confidence                 │──▶ VERIFY QUEUE
                                              │ match exception                │──▶ APPROVAL (Telegram)
                                              └────────────────────────────────┘
```

---

## 2. Why this architecture (the interview answer)

Three deliberate decisions you should be able to defend:

| Decision | Alternative rejected | Why |
|---|---|---|
| Postgres as the job queue (`FOR UPDATE SKIP LOCKED`) | Redis / Celery / RabbitMQ | Zero extra infrastructure, transactional with the domain data (no dual-write problem), free tier friendly. Job state and business state commit atomically. |
| Pluggable extractor interface | Hardcode one vision API | Free-tier quotas force a fallback path. Also turns the constraint into a measurable benchmark: cloud VLM vs. local OCR on the same test set. |
| Pluggable notifier interface | Hardcode Telegram | WhatsApp Cloud API is Doritech's actual channel but needs credentials you don't have. Adapter pattern means it's a config change, not a rewrite. |
| Confidence × severity decision matrix | Auto-approve everything / approve nothing | The genuine product problem. Knowing *when not to trust the model* is the engineering. |

---

## 3. Service topology

Monorepo, three deployables, four shared packages.

```
trident-oracle/
├── apps/
│   ├── web/                  Next.js 15 (App Router) — dashboard, Vercel
│   ├── api/                  FastAPI — REST + webhook receiver, Render
│   └── worker/               Python worker loop — polls Postgres queue
├── packages/
│   ├── core/                 Domain logic. Pure functions, no I/O.
│   │                         matching engine · tolerance policy · decision matrix
│   ├── extractors/           GeminiExtractor · TesseractExtractor · MockExtractor
│   ├── notifiers/            TelegramNotifier · EmailNotifier · WhatsAppNotifier(stub) · MockNotifier
│   └── evals/                Benchmark harness — DocILE / CORD / SROIE
├── db/
│   └── migrations/           Numbered SQL migrations
└── docs/
    ├── ARCHITECTURE.md
    ├── DECISIONS.md          ADR log — one entry per real trade-off
    └── DEMO.md               Runbook for the final presentation
```

**Why `core/` has no I/O:** the matching engine is pure — given a PO, a GRN, an invoice, and
a tolerance policy, it returns a list of exceptions. No database, no network. That makes it
trivially testable and is the single biggest quality signal in the codebase.

---

## 4. Data model

Multi-tenant from day one. Row Level Security is the authorization boundary — same principle
you used at Nile Tech, but this time it has to hold across **background workers**, not just
request-scoped queries.

### Core tables

```sql
tenants(id, name, slug, created_at)

users(id, tenant_id, email, role, created_at)
  -- role: 'admin' | 'approver' | 'clerk'

vendors(id, tenant_id, name, normalized_name, tax_id, email, created_at)
  -- normalized_name: lowercased, punctuation-stripped, for fuzzy matching

purchase_orders(id, tenant_id, vendor_id, po_number, issued_at, currency,
                subtotal, tax, total, status)

purchase_order_lines(id, po_id, line_no, sku, description, normalized_description,
                     qty_ordered, unit_price, tax_rate, line_total)

goods_receipts(id, tenant_id, po_id, grn_number, received_at, received_by)

goods_receipt_lines(id, grn_id, po_line_id, qty_received, condition, notes)
  -- condition: 'good' | 'damaged' | 'partial'
```

### Ingestion & extraction

```sql
invoices(id, tenant_id, vendor_id NULL, po_id NULL,
         invoice_number, invoice_date, due_date, currency,
         subtotal, tax, total,
         source_channel,           -- 'upload' | 'email' | 'webhook'
         source_file_path,         -- Supabase Storage, signed-URL gated
         content_hash,             -- SHA-256 of the file bytes; dedupe key
         extraction_backend,       -- 'gemini' | 'tesseract'
         overall_confidence,
         status,                   -- see state machine below
         created_at, updated_at)

invoice_lines(id, invoice_id, line_no, description, normalized_description,
              qty, unit_price, line_total, matched_po_line_id NULL,
              match_method NULL)   -- 'sku' | 'fuzzy' | 'llm' | 'unmatched'

field_confidences(id, invoice_id, field_path, confidence, bbox JSONB, raw_text)
  -- field_path: 'header.total' | 'lines[2].qty' etc.
  -- bbox: {page, x, y, w, h} — drives the frontend overlay animation
```

### Queue & delivery

```sql
jobs(id, tenant_id, job_type, payload JSONB,
     status,                       -- 'queued'|'running'|'done'|'failed'|'dead'
     attempts, max_attempts,
     idempotency_key UNIQUE,
     run_after, locked_at, locked_by,
     last_error, created_at, updated_at)
  -- job_type: 'extract' | 'match' | 'notify' | 'post'

dead_letters(id, job_id, payload JSONB, final_error, created_at)

notification_deliveries(id, tenant_id, channel, recipient,
                        idempotency_key UNIQUE,
                        status, attempts, next_retry_at,
                        provider_message_id, error, sent_at)
```

### Matching & approvals

```sql
tolerance_policies(id, tenant_id, name, is_active, rules JSONB, version)
  -- rules example:
  -- { "price_variance_pct": 2.0,
  --   "qty_tolerance_pct": 0.0,
  --   "auto_approve_below": 5000,
  --   "dual_approval_above": 100000,
  --   "min_field_confidence": 0.85,
  --   "duplicate_window_days": 90 }

match_runs(id, invoice_id, policy_version, result, duration_ms, executed_at)
  -- result: 'clean' | 'exceptions' | 'blocked'

match_exceptions(id, match_run_id, invoice_id, exception_type, severity,
                 po_line_id NULL, invoice_line_id NULL,
                 expected_value, actual_value, delta, delta_pct,
                 status, resolved_by, resolved_at, resolution_note)
  -- exception_type: NO_PO | NO_GRN | DUPLICATE_INVOICE | SUSPECTED_DUPLICATE
  --                 PRICE_VARIANCE | QTY_SHORT | QTY_OVER | UNMATCHED_LINE
  --                 ARITHMETIC_ERROR | TAX_MISMATCH | DATE_ANOMALY
  -- severity: 'info' | 'warn' | 'block'

approval_requests(id, tenant_id, invoice_id, exception_id NULL,
                  token_hash,               -- SHA-256 only. NEVER store the raw token.
                  channel, recipient,
                  expires_at, consumed_at,
                  decision, decided_by, decided_at, decision_note)
  -- decision: NULL | 'approved' | 'rejected'

audit_log(id, tenant_id, actor_type, actor_id, action,
          entity_type, entity_id, before JSONB, after JSONB, created_at)
  -- append-only. No UPDATE, no DELETE. Enforced by trigger.
```

### Evaluation

```sql
eval_runs(id, dataset, backend, model_version, sample_count, started_at, finished_at)

eval_results(id, eval_run_id, field_path,
             precision, recall, f1, exact_match_rate,
             mean_confidence, mean_latency_ms)
```

---

## 5. Invoice state machine

Server-side transition validation — no client may set status directly.

```
     RECEIVED
        │
        ▼
    EXTRACTING ──(fail ×3)──▶ EXTRACTION_FAILED
        │
        ▼
    EXTRACTED
        │
        ▼
     MATCHING
        │
        ├──▶ MATCHED_CLEAN ──┬──▶ AUTO_POSTED
        │                    └──▶ PENDING_APPROVAL ──┬──▶ APPROVED ──▶ POSTED
        │                                            └──▶ REJECTED
        │
        ├──▶ NEEDS_VERIFICATION  (low extraction confidence — human checks fields)
        │         │
        │         └──▶ (corrected) ──▶ MATCHING
        │
        └──▶ EXCEPTIONS_RAISED ──▶ PENDING_APPROVAL ──┬──▶ APPROVED ──▶ POSTED
                                                      └──▶ REJECTED
```

Every transition writes to `audit_log`. Illegal transitions raise, they don't silently no-op.

---

## 6. The matching engine (the intellectual core)

Pure function. Lives in `packages/core/matching/`.

```python
def run_three_way_match(
    invoice: Invoice,
    po: PurchaseOrder | None,
    grn: GoodsReceipt | None,
    policy: TolerancePolicy,
    recent_invoices: list[InvoiceSummary],
) -> MatchResult:
    ...
```

### Stage 1 — Duplicate detection (runs first, cheapest, highest value)

1. **Hard duplicate:** identical `content_hash` → reject immediately, don't even extract.
2. **Exact duplicate:** same `vendor_id` + `invoice_number` → `DUPLICATE_INVOICE` (block).
3. **Suspected duplicate:** same vendor + total within ±0.5% + invoice_date within
   `duplicate_window_days` + ≥70% line description overlap → `SUSPECTED_DUPLICATE` (warn).

Vendor name normalization matters here — "ACME Corp.", "Acme Corporation", and "ACME CORP"
must collapse to one entity before comparison.

### Stage 2 — Document linkage

- Find the PO: explicit `po_number` on the invoice → else vendor + date-window + amount heuristic.
- No PO found → `NO_PO` (block). No GRN → `NO_GRN` (block, unless policy allows two-way match).

### Stage 3 — Line item matching (the hard part)

Invoice line descriptions rarely equal PO line descriptions. Cascade, cheapest first:

1. **Exact SKU match** → `match_method = 'sku'`, confidence 1.0
2. **Normalized string similarity** — token-set ratio ≥ 0.88 → `'fuzzy'`
   (normalize: lowercase, strip punctuation, expand common abbreviations, sort tokens)
3. **LLM fallback** — only for lines unmatched after 1 & 2, batched into a single call.
   Prompt: "Which PO line, if any, does this invoice line correspond to?" Structured output,
   returns index + confidence + reasoning. → `'llm'`
4. Still unmatched → `UNMATCHED_LINE` exception

> Batch step 3. One call for all unmatched lines, not one call per line — you have a
> 10 requests/minute quota.

### Stage 4 — Per-line checks

For every matched line:

- **Quantity:** `invoice.qty` vs **`grn.qty_received`** — *not* `po.qty_ordered`.
  This is the whole point of three-way matching: you pay for what arrived, not what you asked for.
  - `invoice.qty > grn.qty_received` → `QTY_OVER` (block — you're being billed for phantom goods)
  - `invoice.qty < grn.qty_received` → `QTY_SHORT` (info — underbilled, still worth flagging)
- **Price:** `invoice.unit_price` vs `po.unit_price`, tolerance `price_variance_pct`
  → `PRICE_VARIANCE`, severity scales with delta

### Stage 5 — Document-level arithmetic

Catches OCR errors that per-field confidence misses:

- `Σ(line_total) == subtotal` (±0.01 rounding)
- `subtotal + tax == total`
- `line_total == qty × unit_price` per line
- Any failure → `ARITHMETIC_ERROR` (block). A total that doesn't add up means the extraction
  is wrong regardless of what the model's confidence score claimed.

### Stage 6 — Decision matrix

```
                    │ all conf ≥ threshold │ any conf < threshold
────────────────────┼──────────────────────┼──────────────────────
 no exceptions      │ total < auto_cap:    │ NEEDS_VERIFICATION
                    │   AUTO_POST          │
                    │ else: PENDING_APPROVAL│
────────────────────┼──────────────────────┼──────────────────────
 warn only          │ PENDING_APPROVAL     │ NEEDS_VERIFICATION
────────────────────┼──────────────────────┼──────────────────────
 any block          │ PENDING_APPROVAL     │ NEEDS_VERIFICATION
                    │ (+ dual approval if  │
                    │  total > dual_cap)   │
```

**Low extraction confidence always beats a clean match.** If the model isn't sure what it
read, a "clean" result is meaningless. Be ready to say that sentence out loud in the demo.

---

## 7. Queue semantics

### Claiming work

```sql
UPDATE jobs SET status='running', locked_at=now(), locked_by=$worker_id
WHERE id = (
  SELECT id FROM jobs
  WHERE status='queued' AND run_after <= now()
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
RETURNING *;
```

`SKIP LOCKED` means N workers never collide. No Redis, no broker, no dual-write.

### Retry policy

- Exponential backoff with jitter: `run_after = now() + (2^attempts × base) ± rand(0..base)`
- `max_attempts` default 3 for extraction, 5 for notification
- On final failure → row copied to `dead_letters`, status `dead`
- **Stale lock reaper:** any job `running` with `locked_at < now() - 10 min` returns to `queued`
  (a worker that crashed mid-job must not strand the work)

### Idempotency

Every job carries an `idempotency_key`, UNIQUE-constrained:

- Extraction: `sha256(tenant_id + content_hash)`
- Notification: `sha256(tenant_id + exception_id + recipient + channel)`

Re-submitting returns the existing job rather than creating a duplicate. This is what makes
retries safe — a notification retried after a timeout must not send a second Telegram message.

---

## 8. Security model

| Concern | Mechanism |
|---|---|
| Tenant isolation | Postgres RLS on every table. Worker connects as a role that sets `app.tenant_id` per job — RLS holds in background context, not just request context. |
| File access | Supabase Storage, private bucket, short-lived signed URLs only. No public paths. |
| Webhook authenticity | HMAC-SHA256 over `timestamp + body`, sent as `X-Signature`. Constant-time compare. Reject if timestamp older than 5 minutes (replay protection). |
| Approval tokens | 32 random bytes, base64url. **Only the SHA-256 hash is stored.** Single-use (`consumed_at`), 24h expiry, scoped to one exception and one recipient. |
| Audit integrity | `audit_log` is append-only, enforced by a trigger that raises on UPDATE/DELETE. |
| Secrets | `.env` never committed. `.env.example` documents every key. |
| Transport | HTTPS everywhere; CSP headers on the Next.js app. |

---

## 9. Free-tier stack

| Component | Choice | Limit to design around |
|---|---|---|
| Database + Auth + Storage | Supabase free | 500 MB DB, 1 GB storage |
| Vision extraction | Gemini Flash / Flash-Lite free tier | ~10–15 RPM, ~250–1000 RPD — **forces the queue** |
| Local extraction fallback | Tesseract or PaddleOCR | CPU only, no quota, no network |
| Approvals | Telegram Bot API | Free, no verification, inline buttons |
| Email | SMTP (Gmail app password) | ~500/day |
| Frontend host | Vercel free | — |
| API host | Render free | Spins down when idle — note in docs |
| Worker | In-process with API for demo | Document the production split |
| Test data | DocILE (gated, free) + CORD/SROIE (ungated) | Request DocILE access **day one** |

---

## 10. Frontend design direction

**Tone:** engineered, not playful. Dark, high contrast, generous negative space. Motion that
communicates state rather than decorating it. Think Linear or Vercel dashboard, not a SaaS
landing page with floating blobs.

### Palette

```
bg-base      #08090B     near-black, slight blue cast
bg-raised    #101215     cards
bg-overlay   #16191E     modals, popovers
border       #21262D
text-primary #E6EDF3
text-muted   #8B949E
accent       #6E7BFF     primary action, links, focus rings
signal-clean #2EA88A     matched / approved
signal-warn  #D9A343     low confidence / variance within tolerance
signal-block #E5534B     exception / rejected
```

Numbers, deltas, IDs, and currency in a monospace face (JetBrains Mono / Geist Mono).
Body in Inter or Geist Sans. Tabular figures on every number that might animate.

### Libraries

- **anime.js v4** — *mandatory.* SVG path drawing, timelines, staggers, number counters.
  v4 uses `import { animate, createTimeline, stagger, svg } from 'animejs'` — the API changed
  substantially from v3's `anime({targets})`. Pin the version and check the docs before writing.
- **shadcn/ui** — primitives only: Dialog, Sheet, Table, Tabs, Badge, Tooltip, Command, Toast.
- **Framer Motion** — layout transitions, `AnimatePresence`, shared-layout card→detail expansion.
- **Recharts** — analytics charts (already on your resume).

Division of labour: shadcn owns structure, Framer owns layout/presence, **anime.js owns
everything expressive** — the pipeline, the bounding boxes, the counters.

### Screens and their signature motion

**1 · Pipeline (the money shot)**
Live SVG rail: `Queued → Extracting → Matching → Decided`. Each invoice is a card that
physically travels the rail. anime.js timeline driven by **real SSE events** from the worker,
not a fake loop. The rail path draws itself on mount via `strokeDashoffset`. Stage nodes pulse
when a job enters. A counter at each stage ticks with an anime.js number animation.

> Make sure it's driven by real events. If a reviewer asks "is this animation real?" the
> answer being *yes* is worth more than the animation itself.

**2 · Document verification**
Split view. Original document left, extracted fields right. On mount, bounding boxes
**draw themselves** onto the document with staggered `strokeDashoffset` animation (~40ms
stagger). Hovering a field on the right highlights its box on the left and vice versa.
Confidence below threshold → box strokes amber and breathes on a slow loop. Corrections
animate the value into place.

**3 · Three-way comparison**
Three columns: PO · Received · Invoiced. Animated SVG connectors link matched lines,
drawing left to right on mount. Matched rows settle green. Mismatches: connector strokes red,
the row does a single 6px horizontal shake, and the delta counts up from 0 to the variance
amount. Unmatched invoice lines drop in from the right with no connector — visually orphaned.

**4 · Exception queue**
Filterable grid. Cards enter with `anime.stagger(35)` on a slight y-offset and blur-to-focus.
Resolving a card animates it out and the grid reflows via Framer's layout animation. Severity
shown as a left border-weight, not a loud badge.

**5 · Analytics**
Counters animate from zero on scroll-into-view. Charts draw their lines/bars in.
Extraction accuracy by field, exception breakdown, p50/p95 latency, auto-post rate,
cost-per-invoice estimate.

**6 · Benchmark**
The DocILE results page. Gemini vs Tesseract per-field F1 as an animated bar comparison.
This page is what separates you from someone who wired up an API — it's evidence you
*measured* rather than assumed.

### Motion rules

- Nothing animates longer than 600ms except the one-time pipeline rail draw
- Easing: `easeOutExpo` for entrances, `easeInOutQuad` for state changes. No bounce, no elastic.
- Everything respects `prefers-reduced-motion` — gate every anime.js call
- Animate `transform` and `opacity` only. Never `width`/`top`/`left`.
- Every animation must encode information. If it doesn't tell the user something, delete it.

---

## 11. Milestones

| Days | Phase | Ships |
|---|---|---|
| 1–3 | Foundation | Repo, schema, migrations, RLS, seed data |
| 4–12 | Extraction | Extractor interface, Gemini + Tesseract, confidence, queue + worker |
| 13–22 | Matching | Line matcher, three-way rules, duplicates, decision matrix |
| 23–30 | Approvals | Telegram, signed tokens, delivery tracking, webhooks |
| 31–40 | Frontend | Design system, pipeline, verification, comparison, queue, analytics |
| 41–45 | Eval + polish | DocILE benchmark, docs, ADRs, demo runbook |

**Buffer is deliberately zero after day 45 — see the cut list.**

---

## 12. Cut list (drop in this order when behind)

1. **LLM line-matching fallback** — SKU + fuzzy handles most cases. Document as future work.
2. **Email notifier** — Telegram alone proves the pattern.
3. **Analytics page** — the pipeline view already demonstrates observability.
4. **Dual approval above threshold** — single approval proves the flow.
5. **PaddleOCR** — Tesseract alone is a sufficient second backend.
6. **Suspected-duplicate fuzzy logic** — keep exact duplicate detection, drop the fuzzy tier.
7. **Multi-tenant UI** (tenant switcher) — keep RLS in the schema, hardcode one tenant in the UI.

**Never cut:** the pure matching engine, its unit tests, the audit log, idempotency,
the benchmark page. Those are the project.

---

## 13. Demo runbook (15 minutes)

1. **0:00** — One sentence: "Procurement teams check every invoice against what was ordered
   and what arrived, by hand. This does it in four seconds and only asks a human about the
   ones that look wrong."
2. **0:30** — Drop a clean invoice on the pipeline view. Watch it travel. Auto-posted.
3. **2:00** — Drop an invoice billing 12 units when the goods receipt says 9.
   Exception raised, Telegram message arrives **on your actual phone**, you tap Reject,
   the dashboard updates live.
4. **5:00** — Drop a blurry phone photo. Low confidence. Verification screen, bounding boxes
   draw in, amber field, you correct it, it re-runs and passes.
5. **8:00** — Resubmit the first invoice. Caught as a duplicate before extraction even runs.
6. **9:30** — Benchmark page. "Here's per-field accuracy on 500 DocILE invoices,
   Gemini vs. local OCR."
7. **11:00** — Architecture: pure matching core, Postgres queue with SKIP LOCKED, idempotency,
   RLS across workers. Show `DECISIONS.md`.
8. **13:00** — "The notifier is provider-agnostic. Plugging in WhatsApp Cloud API is a config
   change and one adapter class — here's the stub."
9. **14:00** — Phase 2 roadmap. Questions.

Steps 3, 4 and 5 are what people remember. Rehearse those three until they cannot fail.
