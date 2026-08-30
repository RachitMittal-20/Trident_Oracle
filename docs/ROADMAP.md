# Roadmap — Phase 2

Phase 1 (this build) proves the core loop end to end: extract, three-way match, gate on
confidence, route exceptions to a human, post the clean ones. Everything below is scope that
was deliberately left out of Phase 1, not scope that was attempted and fell short. Each item
says what exists today, what "done" would actually require, and why it wasn't Phase 1's job.

---

## Automatic PO/vendor linkage

**Today**: nothing. This is a genuine capability gap, not an unwired-but-otherwise-complete
mechanism — confirmed by reading the actual extraction schema, not assumed: `InvoiceHeader`
(`packages/extractors/extractors/base.py`) has no `po_number` field at all, so no extraction
backend, Gemini or Tesseract, ever attempts to read a PO reference off the document in the
first place. `POST /v1/invoices/upload` and the webhook payload both take only a file. Nothing
anywhere infers `invoices.po_id`/`vendor_id` after the fact. An invoice ingested through the
real running system — upload or webhook, no exceptions — gets `po_id = NULL` and
`vendor_id = NULL` forever, which means `core.matching.three_way.run_three_way_match` raises on
missing `vendor_id` before it ever gets far enough to consult a PO, and the invoice never
reaches a real match outcome at all. The only invoices in this codebase that have ever had a
`po_id` are rows inserted directly by `db/seed/seed.py` and `demo/seed_demo.py`, which write
directly to Postgres and bypass ingestion entirely.

`demo/link_po.py` is not a scaled-down version of a real mechanism — it is a manual mapping,
full stop. It takes a PO number **typed on the command line by a human who already knows the
answer**, looks up that exact PO by `po_number` for the invoice's tenant, and writes `po_id`
(plus `vendor_id`, inferred from that PO's own vendor) directly with a SQL `UPDATE`. It performs
no extraction, no lookup by any field on the invoice itself, no fuzzy vendor-name matching, and
no disambiguation — it exists solely to make `docs/DEMO.md`'s four staged scenarios reach a real
match outcome, and it only works because the demo already knows, out of band, exactly which PO
each fixture is "for."

**Why deferred**: a real version of this is a materially bigger problem than it looks, not a
missing plumbing step. It needs, at minimum: (1) extraction to actually attempt reading a PO
reference off the document (a new field, a new label/prompt, its own accuracy story — this
project's own benchmark work found even the fields that already exist are far from reliable);
(2) a lookup strategy for when no PO number is present or it doesn't match anything (the
common case for a scanned or photographed invoice), most plausibly vendor-name fuzzy matching
against that vendor's open POs; and (3) a disambiguation path — a UI, or a routed exception —
for when more than one open PO matches, since silently picking one would be worse than raising
`NO_PO` honestly. None of that exists in any partial form today, and guessing at its shape
without a real multi-PO-per-vendor tenant to validate against risks the same wrong-abstraction
trap the rule engine section below is deliberately avoiding.

**What would trigger building it**: this is close to Phase 1 scope, not a nice-to-have — without
it, no invoice that enters the system through its own real ingestion path can ever be matched,
which is the core thing this system exists to do. It should be one of the first things built in
whatever comes after this snapshot, not deferred indefinitely alongside the rule engine or
WhatsApp. The concrete trigger is simply: before any real (non-demo, non-seeded) invoice is
ever expected to flow through this system end to end.

---

## The full rule engine

**Today**: one `TolerancePolicy` per tenant (`price_variance_pct`, `qty_tolerance_pct`,
`auto_approve_below`, `dual_approval_above`, `min_field_confidence`, `duplicate_window_days`),
version-stamped, referenced by every `match_runs` row so a historical decision stays explicable
even after the policy changes. That's a fixed, hand-coded set of rules — real, but not a rule
*engine*: there's no way for a tenant to define a new rule, a per-vendor exception, a
category-specific tolerance, or a rule that only applies above a certain PO value, without a
code change.

**Why deferred**: a real rule engine (a rule DSL or a builder UI, per-vendor/per-category
overrides, rule precedence when two rules disagree, an audit trail for rule *changes* on top of
the audit trail this system already has for invoice state changes) is a project roughly the
size of everything already built, and building it against one tenant's guessed-at requirements
before a second real tenant exists to validate against would very likely produce the wrong
abstraction. The fixed policy schema is deliberately the smallest thing that lets the matching
engine and decision layer be tenant-configurable at all, without guessing at a generality
nobody has asked for yet.

**What would trigger building it**: a second real tenant whose tolerance requirements can't be
expressed in the current six fields — that's the concrete signal for what the rule DSL actually
needs to express, rather than a speculative one.

---

## Escalation ladders

**Today**: a `PENDING_APPROVAL` invoice notifies its approver(s) once, and stays pending until
someone acts — `notification_deliveries` tracks retries of the *delivery* (a failed send retries
up to 5 times with backoff) but nothing re-notifies, reminds, or reassigns based on how long the
invoice has sat unactioned. There is no "if unactioned for 24h, escalate to a manager" path.

**Why deferred**: escalation needs a policy of its own (who's next in the ladder, after how
long, does it repeat, does it ever auto-decide) layered on top of a `users` table that currently
has three flat roles (`admin`/`approver`/`clerk`) with no reporting hierarchy to escalate along.
Building an escalation ladder before there's a real org chart to escalate through would be
inventing both ends of the problem.

**What would trigger building it**: real usage data showing invoices sitting in
`PENDING_APPROVAL` long enough to matter (this system doesn't do anything special about it
today), plus an actual approver hierarchy to route the escalation to.

---

## Multi-tenant UI

**Today**: RLS is genuinely multi-tenant — every tenant-scoped table, every query, the worker's
per-job `app.tenant_id`, all of it. The frontend is not: `apps/web` has no tenant switcher, no
tenant-scoped login, no concept of "which tenant is this session" beyond whatever `tenant_id`
value a page happens to be constructed with. It's a single-tenant UI sitting on top of a
fully multi-tenant backend.

**Why deferred**: a tenant switcher is a real UI surface (an org-switcher component, session/
auth wiring to know which tenants a logged-in user belongs to, probably a subdomain or path
scheme) that has zero bearing on whether the matching engine, the RLS policies, or the queue
are correct — none of that logic changes based on whether the *frontend* can address more than
one tenant at a time. Building the schema and backend multi-tenant from day one (cheap, and
wrong to retrofit later) and deferring the UI surface for it (expensive, and worthless to build
speculatively) was the right split of effort for a solo, time-boxed build.

**What would trigger building it**: a second real tenant. The backend already supports it; the
UI work is bounded and mechanical once there's a real second tenant to test the switcher
against.

---

## WhatsApp adapter

**Today**: `WhatsAppNotifier.send()` unconditionally raises `NotImplementedError`. See
`docs/DECISIONS.md` (ADR-006) for the full reasoning — this is the channel most likely to be
the right one for this system's actual target users, and it's the one that doesn't work.

**Why deferred**: not primarily an engineering gap. A real implementation needs a verified Meta
Business Manager account, a dedicated verified phone number, a permanent Graph API access
token, and Meta-pre-approved message *templates* — nearly all of this system's outbound traffic
is business-initiated and falls outside the 24-hour open-session window that permits freeform
text, so template design and approval is a real, non-code dependency with its own review
timeline. None of those credentials or approvals exist for this project.

**What would trigger building it**: WhatsApp Business API credentials becoming available. At
that point, the `Notifier` interface doesn't need to change — only a new class implementing it,
plus wiring `NOTIFIER_CHAIN` to include `whatsapp`. The stub's own docstring is close to a spec
for that implementation already (template mapping, session-window handling, quick-reply
buttons in place of Telegram's `callback_data`).

---

## ERP posting integration

**Today**: `POSTED` exists as a legal state in the invoice state machine (`APPROVED → POSTED`
is a valid transition per `core/state_machine.py`), and the schema, the analytics views, and the
exception queue all already treat it as a real terminal outcome. Nothing ever calls it. There is
no `post` job handler — `JobType.POST` is a defined enum value with no corresponding logic in
`apps/worker`. An approved invoice today simply stays `APPROVED` forever; "posting to the ERP"
is entirely unbuilt, not partially built or stubbed.

**Why deferred**: this is the one item on this list that's fully external-system-dependent —
which ERP (SAP, NetSuite, Tally, QuickBooks, a dozen others common in the markets this project
targets) determines the entire shape of the integration (its own auth model, its own posting
API or file-based batch format, its own idempotency guarantees or lack thereof), and there is no
real ERP account or sandbox available to build and test against honestly. Building a generic
"ERP adapter interface" with nothing real behind it would produce an abstraction shaped by
guesswork, the same risk the rule-engine deferral above is avoiding.

**What would trigger building it**: a real ERP account (even a sandbox/trial) to integrate
against for real, at which point the existing `Notifier`/`Extractor`/`Storage` interface
pattern this codebase already uses gives a template for how the adapter should be shaped —
one interface in `packages/core` or a new `packages/erp`, one real implementation behind it, a
`post` job handler in `apps/worker` that calls it, and a `MockErpClient` for tests, matching how
every other external dependency in this codebase is already structured.
