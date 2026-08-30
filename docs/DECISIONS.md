# Decisions

ADR log — one entry per real architectural trade-off actually made in this codebase, not a
list of features. A decision record that only lists upsides is a sales document, not
engineering, so every entry below states what it costs, not just what it buys.

---

## ADR-001: Postgres queue (`FOR UPDATE SKIP LOCKED`) instead of Redis/Celery/RabbitMQ

**Context.** The system needs a background job queue: extraction, matching, notification, and
posting all run asynchronously after upload, with retries, backoff, and dead-lettering.

**Options considered.**
- Redis + a task library (Celery, RQ, Dramatiq).
- A managed queue (SQS, Cloud Tasks).
- Postgres itself, using `SELECT ... FOR UPDATE SKIP LOCKED` as the claim primitive.

**Decision.** Postgres. `jobs` is a table in the same database as every other table this
system writes to. `apps/worker/worker/db.py::claim_next()` claims the oldest due, queued row
with `FOR UPDATE SKIP LOCKED`, which lets any number of worker processes poll concurrently
without ever double-claiming a row — no separate broker, no second system to keep alive.

**Consequences.**
- *Upside*: a job's existence and the business row it operates on can commit in the same
  transaction — no dual-write problem between "the invoice was created" and "the extract job
  was enqueued." Zero additional infrastructure to run, monitor, or pay for, which matters for
  a free-tier, solo-maintained project.
- *Downside, stated plainly*: this is a polling queue, not a push queue. Workers sleep and
  re-poll (`WORKER_POLL_INTERVAL_SECONDS`, default 5s) rather than being woken instantly by a
  new job — there's an inherent latency floor here that Redis pub/sub or SQS long-polling
  doesn't have. It also puts queue traffic on the same database that holds business data;
  at high job volume, `jobs` churn (frequent `UPDATE`s, index maintenance on
  `idx_jobs_status_run_after`) competes for the same I/O and connection pool as `invoices`
  reads. This system has no horizontal scaling story for the API tier either (see
  `db/README.md`'s rate-limiter note) — Postgres-as-queue was sized for one API process and a
  handful of worker processes, not a fleet.
- *What would make me revisit it*: sustained job volume high enough that `jobs` table churn
  measurably degrades unrelated query latency, or a genuine need for sub-second job pickup
  latency that polling can't deliver regardless of poll interval.

---

## ADR-002: Pluggable extractor interface (Gemini primary, Tesseract fallback) instead of one hardcoded provider

**Context.** Vision-based invoice extraction needs *some* backend. Free-tier vision APIs
(Gemini Flash) have real rate limits (10 RPM in this project's tier) and can be discontinued or
repriced without notice — `gemini-2.5-flash` itself returned a 404 for new API keys partway
through this very project (see CLAUDE.md's model note), which is exactly the failure mode this
decision defends against.

**Options considered.**
- Hardcode a single provider's SDK call directly in the worker's extract handler.
- An `Extractor` interface (`packages/extractors/extractors/base.py`) with swappable backends
  behind a factory, plus a fallback chain.

**Decision.** The interface. `GeminiExtractor` is primary, `TesseractExtractor` (local OCR, no
network, no quota) is the fallback, and `FallbackExtractor` retries the primary a bounded
number of times before falling through. `GEMINI_MODEL` is an env var, not a hardcoded string,
specifically so the next free-tier model rotation is a config change.

**Consequences.**
- *Upside*: the system degrades instead of stopping entirely when the primary quota is
  exhausted or the model name changes underneath it. It also turned a constraint into a
  measurement: the same harness (`packages/evals`) can run either backend across the same
  documents and report the real accuracy gap, rather than assuming one.
- *Downside, stated plainly*: the interface (`InvoiceHeader`/`LineItem` as raw strings,
  `ExtractionResult.confidence` keyed by field path) is shaped by what Gemini's structured
  output naturally provides. Adding a genuinely different kind of backend — a classical
  template-matching engine, a different vendor's document AI with a different confidence
  model entirely — would likely need the interface itself to grow, not just a new
  implementation of the existing one. And the fallback exists for *transient* primary
  failures only; a real measured comparison (`docs/BENCHMARKS.md`) shows Tesseract is
  meaningfully worse at line-item recognition than a vision model, so falling back doesn't mean
  falling back to something equally good — it means degrading gracefully to something
  measurably worse, and that gap is exactly what the confidence-gating decision (ADR-004)
  exists to catch before a bad Tesseract read posts automatically.
- *What would make me revisit it*: a production deployment where Tesseract's real
  accuracy gap (see BENCHMARKS.md) makes "gracefully degrade to OCR" worse than "queue the
  document and retry the vision backend later" — i.e., treating a Gemini outage as a
  *delay*, not a silent quality downgrade.

---

## ADR-003: `min()`, not `mean()`, for an invoice's overall extraction confidence

**Context.** Every extracted field carries its own confidence score. Something has to reduce
that per-field map to one number (`overall_confidence`) the decision layer can gate on.

**Options considered.**
- Mean of every field's confidence.
- Weighted mean (weight high-stakes fields like `total` more than low-stakes ones like a line
  description).
- Minimum of every field's confidence.

**Decision.** Minimum (`apps/worker/worker/extract_handler.py::compute_overall_confidence`).
Quoting the function's own docstring: *"A mean lets one badly-read field hide behind many easy,
high-confidence ones... The weakest field is what should determine whether a human needs to
look."*

**Consequences.**
- *Upside*: an invoice with nine perfectly-read fields and one garbled total can't average its
  way to a passing score — the one field most likely to actually be wrong is the one that
  decides whether a human looks. This is the conservative choice, and for a system whose whole
  premise is "know when not to trust the model," conservative is the right default.
- *Downside, stated plainly*: `min()` doesn't distinguish a low-stakes field from a high-stakes
  one. A single smudged, low-confidence read on a field nobody actually uses downstream (say, a
  customer reference number this schema doesn't even map to anything) forces the exact same
  `NEEDS_VERIFICATION` review as a smudged `total` would — the system can't tell "the field that
  matters is uncertain" from "an irrelevant field is uncertain," so it treats both as equally
  disqualifying. At scale this means a non-trivial fraction of invoices route to human review
  for reasons that don't actually bear on whether the invoice is safe to post. A
  weighted-minimum (weight by which fields the matching engine and decision layer actually
  consume) would fix this but doesn't exist yet.
- *What would make me revisit it*: verification-queue volume dominated by fields the matching
  engine never reads — that's the concrete signal that the naive minimum is generating false
  positives an accuracy-weighted score wouldn't.

---

## ADR-004: Low confidence overrides an otherwise-clean match

**Context.** The three-way match can return `clean` (zero exceptions) even when the extraction
that fed it was uncertain — "no exceptions found" and "the fields are actually correct" are not
the same claim, and the decision layer has to pick which one it trusts.

**Options considered.**
- Trust a clean match result regardless of extraction confidence — if nothing looked wrong,
  post it.
- Gate on confidence *only* when the match result isn't clean (i.e., let a clean match through
  even at low confidence, since there's "nothing to fix" anyway).
- Gate on confidence first, unconditionally, before the match result is even consulted.

**Decision.** The third option (`core/decision.py::decide()`). Low confidence forces
`NEEDS_VERIFICATION` in every branch of the matrix, including the "no exceptions" row.

**Consequences.**
- *Upside*: this closes the most dangerous failure mode in the whole system — a badly-read
  invoice that happens to compare "clean" only because the misreads didn't happen to collide
  with a check the matching engine runs. A garbled total that's coincidentally still under
  `auto_approve_below`, or a misread quantity that happens to equal what was received by
  chance, would otherwise auto-post with nobody ever looking at the source document.
- *Downside, stated plainly*: this is a real cost, not a free safety margin. It means a
  perfectly fine invoice that the extractor was simply *unlucky* on — one field genuinely hard
  to read even though every value on it turns out correct — gets routed to human review anyway,
  indistinguishable from one that's actually wrong. Combined with ADR-003's minimum-confidence
  reduction, this compounds: the one field most likely to be wrong is also the one field that,
  by itself, can force a review regardless of how clean everything else is. The system has no
  way to "trust but verify quickly" — every low-confidence clean match costs a full human
  review, the same cost as a genuine exception.
- *What would make me revisit it*: evidence (from the verification queue's own resolution data
  — did the human actually change anything?) that a large fraction of low-confidence-but-clean
  invoices get confirmed unchanged, meaning the gate is spending human attention on invoices
  that were fine all along.

---

## ADR-005: Quantity checked against goods received, not goods ordered

**Context.** The quantity check needs a reference value to compare `invoice.qty` against.
Two candidates exist on a matched line: `purchase_order_lines.qty_ordered` and
`goods_receipt_lines.qty_received`.

**Options considered.**
- Compare against `qty_ordered` (what the PO says should show up).
- Compare against `qty_received` (what the goods receipt says actually showed up), excluding
  damaged-condition receipt lines.

**Decision.** `qty_received`, damaged lines excluded (`core/matching/three_way.py::_qty_finding`
docstring: *"Three-way matching exists precisely so you pay for what ARRIVED, not what you
ordered."*).

**Consequences.**
- *Upside*: this is the entire reason three-way matching exists as a discipline rather than
  simple two-way (invoice vs. PO) matching. An over-ordered PO doesn't entitle a vendor to bill
  for units that never actually arrived, and a vendor who ships short but bills for the full PO
  quantity gets caught by this check specifically, not by any comparison against the PO alone.
- *Downside, stated plainly*: this makes the whole quantity check **structurally dependent on
  goods-receipt data existing and being accurate**. If receiving staff under-record what
  arrived (common in a rushed dock — count now, reconcile later), every affected invoice gets
  flagged `QTY_OVER` even though the vendor billed correctly and the discrepancy is a receiving
  data-entry problem, not a vendor problem. The system has no way to distinguish "vendor
  overbilled" from "warehouse under-recorded receipt" — both produce the identical exception,
  and a reviewer has to know to check the physical dock, not just the numbers on screen. It also
  means a PO with genuinely no GRN yet (goods in transit, invoice arrived early) skips the
  quantity check entirely rather than falling back to a weaker check against the PO — see the
  `NO_GRN` handling in §5 of `docs/ARCHITECTURE.md`.
- *What would make me revisit it*: a customer whose receiving process is reliably slower or
  less accurate than their AP process, where this check would generate more false positives
  from receiving lag than true positives from vendor overbilling.

---

## ADR-006: Telegram as the real approval channel, WhatsApp deliberately stubbed

**Context.** A `PENDING_APPROVAL` invoice needs to reach a human somewhere they'll actually see
it promptly. Telegram, email, and WhatsApp were all candidates.

**Options considered.**
- Email only — universally available, no bot setup, but slow (people don't watch inboxes the
  way they watch a phone).
- WhatsApp Business Cloud API — plausibly the channel a real Indian SME procurement team
  actually uses day to day.
- Telegram Bot API — free, trivial to set up, supports inline action buttons and callback
  webhooks, but not the channel most procurement teams have open by default.

**Decision.** Telegram is the real, fully implemented primary channel (inline buttons, callback
webhook, message editing after decision). Email is a real secondary. WhatsApp is a documented
stub: `WhatsAppNotifier.send()` unconditionally raises `NotImplementedError`, with a docstring
explaining exactly what's missing (Meta Business Manager verification, a dedicated verified
number, a permanent Graph API token, and Meta-pre-approved message templates, since nearly all
outbound traffic here is business-initiated and falls outside any 24-hour open-session window
that would permit freeform text).

**Consequences.**
- *Upside*: the `Notifier` interface and the decision/routing logic that calls it don't know or
  care which channel is behind `send()` — plugging in a real WhatsApp implementation later is
  an adapter class and a credentials-only config change, not a rewrite of anything upstream. The
  stub is honest about being a stub rather than silently no-op'ing or pretending to send.
- *Downside, stated plainly*: this is a genuine capability gap, not just an implementation
  detail. **The channel most likely to be the actual right one for this system's real target
  users (WhatsApp) is the one that doesn't work.** Telegram proves the pattern end-to-end, but a
  team that lives in WhatsApp and doesn't already use Telegram gets nothing from this system
  today except email, which this same set of trade-offs already conceded is slower than the
  channel people actually watch. Getting WhatsApp working for real is also not purely
  engineering effort — it requires a Meta Business verification process (identity documents,
  waiting time) and pre-approved message templates that constrain what the notification can
  even say, which is a business/ops dependency this project doesn't control, not just code to
  write.
- *What would make me revisit it*: WhatsApp Business API credentials becoming available for a
  real deployment — at that point the stub's own docstring is close to a spec for the real
  implementation (template design, session-window handling, quick-reply buttons in place of
  Telegram's `callback_data`).
