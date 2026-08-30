# Demo runbook

A 15-minute walkthrough, four real invoices, four real outcomes -- every scenario below was
run end to end against the real worker (`TesseractExtractor`, real Postgres, real state
machine) while building this file, not assumed. See "How this was verified" at the bottom for
exactly what that means.

## ⚠ Verify this yourself before demo day

**The real `POST /v1/invoices/upload` HTTP endpoint has not been exercised end to end anywhere
in this build.** Everything downstream of it (extraction, matching, the decision, the
ingestion-time duplicate check) has been verified for real against the actual worker code --
see "How this was verified" below -- but that verification called the ingestion and job-handler
functions directly, using an in-memory `Storage` stand-in, because this environment has no live
Supabase Storage bucket or service-role credentials to test the real HTTP path against. The
upload handler and real file storage are comparatively simple and already covered by
`apps/api/tests/`, but "covered by unit tests" is not the same claim as "verified end to end,"
and this is the one link in the chain that isn't. **Before your first live rehearsal**, actually
drag a fixture file onto the running pipeline screen (or `curl` it) against your real Supabase
project and confirm it creates an invoice and an extract job the way every other step in this
runbook has already been shown to. Do not find out live in front of an audience.

## Before you start

**One real gap this demo works around, stated plainly: this system cannot link an invoice to
its PO on its own.** This is not a missing wiring step on top of an otherwise-working
mechanism -- it is a genuine, unbuilt capability. No extraction backend (Gemini or Tesseract)
even attempts to read a PO reference off the document; `InvoiceHeader`
(`packages/extractors/extractors/base.py`) has no `po_number` field to populate in the first
place. `POST /v1/invoices/upload` and the webhook payload both take only a file. Nothing
anywhere infers `invoices.po_id`/`vendor_id` afterward. The practical consequence: **any
invoice that enters this system through its real upload or webhook path, today, gets `po_id =
NULL` forever and never reaches a real match outcome.** The only invoices that have ever had a
PO in this codebase are rows the seed scripts write directly to Postgres, bypassing ingestion
entirely. See `docs/ROADMAP.md`'s "Automatic PO/vendor linkage" entry for the full scope of
what a real fix needs (it's larger than it sounds) and why it's close to Phase 1 scope rather
than a deferred nice-to-have.

`demo/link_po.py` is this runbook's workaround, and it is exactly as manual as that gap implies:
you tell it, by PO number, on the command line, which PO a given fixture is for; it looks up
that PO for the invoice's tenant and writes `po_id` and `vendor_id` (inferred from the PO's own
vendor) with a direct SQL `UPDATE`. It reads nothing from the invoice itself -- no extracted
field, no fuzzy match, no inference of any kind -- because there is nothing on the invoice yet
to read. It only "works" because this runbook already knows, out of band, exactly which PO each
staged fixture corresponds to; it would not generalize to a real, unstaged invoice even a
little. Run it immediately after each upload below, before narrating -- it takes under a second
and the timing race against the worker's own extract job is generous (default poll interval 5s)
but not infinite.

**Setup, once, before rehearsing:**

```bash
# 1. Base demo dataset (tenant, 9 vendors, 25 realistic POs/GRNs, analytics history)
DATABASE_URL=<service-role connection> uv run python db/seed/seed.py

# 2. This runbook's exact PO/GRN data (3 POs, matched to the 4 fixtures below)
DATABASE_URL=<service-role connection> uv run python demo/seed_demo.py

# 3. Generate the 4 fixture files (regenerate any time -- deterministic, not committed as binaries)
uv run python demo/fixtures/generate.py

# 4. Point the worker at Tesseract, not Gemini -- this environment has no Gemini API key,
#    and Tesseract is real, free, and (per the fixture design below) reliable for this demo.
export EXTRACTOR_BACKEND=tesseract

# Run the stack: apps/web, apps/api, apps/worker, per the README's "Running" section.
```

**Rehearse this exact sequence at least twice before presenting.** The three moments that
matter (2:00, 5:00, 8:00 below) are the ones to get fluent on.

---

## Minute-by-minute

**0:00 -- One sentence.** *"Procurement teams check every invoice against what was ordered and
what actually arrived, by hand. This does it in under a second, and only asks a human about the
ones that look wrong."*

**0:30 -- Clean invoice, auto-posts.**
Drag `demo/fixtures/01-clean-invoice.png` onto the pipeline screen (or `curl -F file=@demo/fixtures/01-clean-invoice.png -F tenant_id=<id> $API/v1/invoices/upload`).
Immediately run:
```bash
uv run python demo/link_po.py --fixture demo/fixtures/01-clean-invoice.png --po PO-3001
```
Watch the card travel the rail: `QUEUED -> EXTRACTING -> MATCHING -> DECIDED`. It bills 8 units
of Bracket at $40.00 against a PO for 8 units at $40.00, exactly received -- zero exceptions,
confidence well above threshold, total ($320) well under the auto-approve cap ($5,000).
**Outcome: `AUTO_POSTED`.** No human ever touches this one -- that's the point.

**2:00 -- Over-billed, exception raised, real Telegram message.**
Upload `demo/fixtures/02-overbilled-invoice.png`, then:
```bash
uv run python demo/link_po.py --fixture demo/fixtures/02-overbilled-invoice.png --po PO-3002
```
This invoice bills 12 units of Gasket -- but the seeded goods receipt (`GRN-3002`) only
received 9. Watch the exception queue: `QTY_OVER`, block severity, with the exact detail string
("bills 12 units but only 9 were received"). If `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are
configured for a real bot, a message lands on your actual phone with inline Approve/Reject
buttons -- tap Reject, watch the dashboard update live over SSE, no page refresh.
**Outcome: `PENDING_APPROVAL`, one `QTY_OVER` exception.**

**5:00 -- Blurry photo, low confidence, verification screen.**
Upload `demo/fixtures/03-blurry-photo.jpg`, then:
```bash
uv run python demo/link_po.py --fixture demo/fixtures/03-blurry-photo.jpg --po PO-3003
```
This is a genuinely degraded image (real Gaussian blur + heavy JPEG recompression, not a canned
"low confidence" flag) -- verified to bring `TesseractExtractor`'s own minimum field confidence
to roughly 0.71, below this policy's 0.85 threshold. Say the sentence out loud here: *"The match
came back clean, but the model wasn't confident it read the document correctly -- so a clean
result doesn't get trusted."* Open the invoice's verify screen: bounding boxes draw in over the
source image, the low-confidence field breathes amber. Correct nothing (or correct one field)
and resubmit -- watch it re-enter `MATCHING` and resolve.
**Outcome: `NEEDS_VERIFICATION`**, confidence gating overriding an otherwise-clean match
(`docs/DECISIONS.md`, ADR-004).

**8:00 -- Duplicate, caught before extraction runs.**
Upload `demo/fixtures/04-duplicate-of-01.png` -- a literal byte-for-byte copy of fixture 1.
**No `link_po.py` step here.** Say what's actually happening: *"This is caught by the cheapest
possible check -- identical file, identical hash, rejected before we spend a single extraction
call on it."* The response references the original invoice's id directly; no new invoice, no
new job, nothing appears on the pipeline rail at all. This demonstrates
`invoices.content_hash`'s ingestion-time dedupe (`apps/api/api/ingest.py`), which is a
different, cheaper mechanism than the matching engine's own fuzzy/exact duplicate-detection
stage (`core.matching.duplicates`) -- say so if asked; don't imply this exercised the fuzzy
matcher, since a genuine re-scan (different bytes, same vendor/invoice number) would be a
better demonstration of *that* stage and isn't what this fixture does.

**9:30 -- Benchmark page.** *"Here's what raw local OCR gets on real invoices versus what a
vision model would -- and here's exactly why the free-tier fallback isn't the primary path."*
Show `docs/BENCHMARKS.md`'s real DocILE/Tesseract numbers plainly, including the honest gaps
(near-zero line-item recall, the concurrency bug, the two extraction bugs found and fixed while
producing this file). A benchmark page with only good numbers on it is a marketing page.

**11:00 -- Architecture.** Pure matching core (`packages/core`, no I/O, 91 tests), Postgres
queue with `SKIP LOCKED` (no broker), RLS enforced across background workers not just requests,
idempotency on every job and notification. Pull up `docs/ARCHITECTURE.md`.

**13:00 -- The honest gaps.** *"The notifier is provider-agnostic -- WhatsApp is a config
change and one adapter class away, not a rewrite, but it's genuinely not built: here's the
stub and why."* Pull up `docs/DECISIONS.md` ADR-006. This lands better than pretending
everything is finished.

**14:00 -- Roadmap.** `docs/ROADMAP.md` -- framed as deliberately deferred, not unfinished.
Questions.

---

## Resetting between rehearsals

```bash
DATABASE_URL=<service-role connection> uv run python demo/reset.py
```

Deletes the four demo invoices (`INV-4001`-`INV-4003`, matched by `invoice_number LIKE
'INV-4%'`) and everything that cascades from them -- lines, field confidences, match runs/
exceptions, jobs, notification deliveries, approval requests. Leaves `db/seed/seed.py`'s
dataset and `demo/seed_demo.py`'s PO/GRN/policy rows untouched, so you don't need to reseed
between rehearsals, only reset and re-upload.

---

## How this was verified

Every one of the four outcomes above was produced by actually running the real
`TesseractExtractor` and the real `handle_match`/`make_extract_handler` worker code (not a
mock, not an assumption) against a disposable local Postgres with the full migration set
applied, while building this runbook. Two real bugs in the extraction/matching path were found
and fixed in the process (see `docs/BENCHMARKS.md`'s "known operational issues" and this
project's git history: `TesseractExtractor`'s missing `subtotal` label, and `match_handler.py`
crashing instead of routing to `NEEDS_VERIFICATION` when a degraded scan produces
domain-invalid data) -- neither was known before this runbook was written, and both would have
broken this exact demo live.

What was verified is every step downstream of upload -- the extract job, the match job, the
decision, the ingestion-time duplicate check (`ingest_invoice` called directly) -- using an
in-memory `Storage` implementation in place of Supabase's. **The real HTTP upload endpoint
itself was not part of that verification** -- see the callout at the top of this file before
your first rehearsal.
