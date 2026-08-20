"""Invoice-line-to-PO-line matching (docs/ARCHITECTURE.md, "Stage 3 — Line
item matching"). Pure function, no I/O -- CLAUDE.md's most important rule.

Invoice line descriptions rarely equal PO line descriptions verbatim, so this
runs a cascade, cheapest and most-certain strategy first, falling through to
progressively more expensive ones only for what's left unmatched:

    1. Exact SKU match             -- match_method='sku',   confidence 1.0
    2. Normalized string similarity -- match_method='fuzzy', confidence = score
    3. LLM fallback (batched)       -- match_method='llm',   confidence from the model
    4. Whatever's left              -- unmatched

Every tier enforces a strict one-to-one pairing: once a PO line is claimed by
an invoice line, no later tier may also assign it to a different invoice
line. Tiers 2 and 3 resolve competition for the same PO line greedily by
descending confidence.

Tier 3 (the LLM fallback) is expressed here only as a `Callable` type --
`LlmLineMatcher` -- never as a concrete API call. The concrete adapter (a
Gemini call) belongs in packages/extractors, which is allowed to do I/O;
this module is not. If the caller doesn't inject one, tier 3 is skipped
entirely and whatever tier 1/2 left unmatched simply stays unmatched.
CRITICAL: `LlmLineMatcher` is called *once* with every remaining unmatched
invoice line, never once per line -- the free-tier Gemini quota this project
runs on is 10 requests/minute (see GEMINI_RATE_LIMIT_RPM in CLAUDE.md),
which one-call-per-line would blow through on any invoice with more than a
handful of unmatched lines.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from rapidfuzz import fuzz

from core.errors import MatchingError
from core.models import InvoiceLine, MatchMethod, PurchaseOrderLine

DEFAULT_FUZZY_THRESHOLD = Decimal("0.88")


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise MatchingError(message)


# --- Tier 3 interface --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LlmMatchSuggestion:
    """One row of the LLM fallback's batched response: for a single
    unmatched invoice line, which PO line (if any) it corresponds to."""

    invoice_line_id: UUID
    po_line_id: UUID | None
    confidence: Decimal
    rationale: str

    def __post_init__(self) -> None:
        _check(
            Decimal("0") <= self.confidence <= Decimal("1"),
            "LlmMatchSuggestion.confidence must be between 0 and 1",
        )
        _check(bool(self.rationale.strip()), "LlmMatchSuggestion.rationale must not be blank")


class LlmLineMatcher(Protocol):
    """The shape tier 3 requires. Implemented by an adapter in
    packages/extractors (an actual Gemini call) -- never implemented in
    this module, which stays I/O-free. Tests inject a plain function or
    lambda matching this signature.

    Takes every still-unmatched invoice line and every still-unmatched PO
    line in ONE call and returns one LlmMatchSuggestion per invoice line
    passed in (a suggestion with po_line_id=None is a legitimate "no match"
    answer, not an omission).
    """

    def __call__(
        self,
        unmatched_invoice_lines: Sequence[InvoiceLine],
        candidate_po_lines: Sequence[PurchaseOrderLine],
    ) -> Sequence[LlmMatchSuggestion]: ...


# --- Result types --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LineMatch:
    """One resolved pairing. Unmatched lines are not represented as a
    LineMatch -- see LineMatchResult.unmatched_invoice_line_ids -- because
    an unmatched line has no po_line_id or confidence to attach."""

    invoice_line_id: UUID
    po_line_id: UUID
    match_method: MatchMethod
    confidence: Decimal
    rationale: str | None = None

    def __post_init__(self) -> None:
        _check(
            self.match_method != MatchMethod.UNMATCHED,
            "LineMatch.match_method must not be UNMATCHED -- unmatched lines belong in "
            "LineMatchResult.unmatched_invoice_line_ids, not as a LineMatch",
        )
        _check(
            Decimal("0") <= self.confidence <= Decimal("1"),
            "LineMatch.confidence must be between 0 and 1",
        )


@dataclass(frozen=True, slots=True)
class LineMatchResult:
    matches: tuple[LineMatch, ...]
    unmatched_invoice_line_ids: tuple[UUID, ...]
    unmatched_po_line_ids: tuple[UUID, ...]


# --- Tier 1: exact SKU -----------------------------------------------------

# A run of letters/digits, allowing internal hyphens (e.g. "WID-4521") but
# not leading/trailing ones, so surrounding punctuation ("SKU: WID-4521,")
# never becomes part of the token.
_SKU_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")


def _sku_tokens(description: str) -> set[str]:
    return {tok.upper() for tok in _SKU_TOKEN_RE.findall(description)}


# --- Tier 2: normalized string similarity ----------------------------------

_PUNCTUATION_RE = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_for_comparison(description: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, sort tokens --
    docs/ARCHITECTURE.md's normalization recipe for stage 3, minus
    abbreviation expansion (that's expected to already be baked into
    normalized_description by the time it gets here; this is a defensive
    re-normalization so a fuzzy comparison never depends on that having
    happened correctly upstream). Token-sorting also means token_set_ratio
    is being handed two strings whose word order can never itself be the
    reason two equivalent descriptions fail to match.
    """
    lowered = description.lower()
    stripped = _PUNCTUATION_RE.sub(" ", lowered)
    tokens = sorted(_WHITESPACE_RE.split(stripped.strip()))
    return " ".join(tok for tok in tokens if tok)


def _token_set_ratio(a: str, b: str) -> float:
    """Thin wrapper around rapidfuzz so tests can patch this one seam to
    pin an exact boundary score (crafting real strings that produce exactly
    87.99 vs. 88.00 out of rapidfuzz's C implementation isn't practical)."""
    return fuzz.token_set_ratio(a, b)


# --- The cascade -----------------------------------------------------------


def match_lines(
    invoice_lines: Sequence[InvoiceLine],
    po_lines: Sequence[PurchaseOrderLine],
    *,
    fuzzy_threshold: Decimal = DEFAULT_FUZZY_THRESHOLD,
    llm_matcher: LlmLineMatcher | None = None,
) -> LineMatchResult:
    """Match every invoice line to at most one PO line, cascading through
    tiers 1-3 and leaving anything left over unmatched. Pure: no DB, no
    network, no file I/O -- `llm_matcher`, if supplied, is called but never
    constructed here.
    """
    _check(
        Decimal("0") <= fuzzy_threshold <= Decimal("1"),
        "fuzzy_threshold must be between 0 and 1",
    )

    claimed_po_ids: set[UUID] = set()
    outcomes: dict[UUID, LineMatch] = {}

    # Tier 1 -- exact SKU match. Invoice lines have no dedicated sku field
    # (extraction reports free-text description only), so a match means the
    # PO line's sku appears as a whole token in the invoice line's
    # description -- e.g. "WID-4521 - Blue Widget" contains PO line sku
    # "WID-4521". First PO line (in input order) whose sku token-matches
    # and isn't already claimed wins; confidence is always 1.0 since this
    # is an exact match, not an estimate.
    for inv_line in invoice_lines:
        tokens = _sku_tokens(inv_line.description)
        for po_line in po_lines:
            if po_line.id in claimed_po_ids or po_line.sku is None:
                continue
            sku = po_line.sku.strip().upper()
            if sku and sku in tokens:
                outcomes[inv_line.id] = LineMatch(
                    invoice_line_id=inv_line.id,
                    po_line_id=po_line.id,
                    match_method=MatchMethod.SKU,
                    confidence=Decimal("1"),
                )
                claimed_po_ids.add(po_line.id)
                break

    # Tier 2 -- normalized string similarity (token-set ratio). Score every
    # still-open (invoice line, PO line) pair, then resolve conflicts
    # greedily by descending score so the best available match always wins
    # a contested PO line, per the prompt's requirement.
    remaining_invoice = [line for line in invoice_lines if line.id not in outcomes]
    remaining_po = [line for line in po_lines if line.id not in claimed_po_ids]

    candidates: list[tuple[Decimal, InvoiceLine, PurchaseOrderLine]] = []
    for inv_line in remaining_invoice:
        inv_description = inv_line.normalized_description or inv_line.description
        inv_text = _normalize_for_comparison(inv_description)
        for po_line in remaining_po:
            po_text = _normalize_for_comparison(po_line.normalized_description)
            score = Decimal(str(_token_set_ratio(inv_text, po_text))) / Decimal("100")
            if score >= fuzzy_threshold:
                candidates.append((score, inv_line, po_line))

    candidates.sort(key=lambda c: c[0], reverse=True)
    for score, inv_line, po_line in candidates:
        if inv_line.id in outcomes or po_line.id in claimed_po_ids:
            continue
        outcomes[inv_line.id] = LineMatch(
            invoice_line_id=inv_line.id,
            po_line_id=po_line.id,
            match_method=MatchMethod.FUZZY,
            confidence=score,
        )
        claimed_po_ids.add(po_line.id)

    # Tier 3 -- LLM fallback, one batched call for everything tiers 1-2 left
    # unmatched. Skipped entirely if no llm_matcher was injected.
    remaining_invoice = [line for line in invoice_lines if line.id not in outcomes]
    remaining_po = [line for line in po_lines if line.id not in claimed_po_ids]

    if remaining_invoice and llm_matcher is not None:
        suggestions = llm_matcher(tuple(remaining_invoice), tuple(remaining_po))
        for suggestion in sorted(suggestions, key=lambda s: s.confidence, reverse=True):
            if suggestion.invoice_line_id in outcomes:
                continue
            if suggestion.po_line_id is None or suggestion.po_line_id in claimed_po_ids:
                continue
            outcomes[suggestion.invoice_line_id] = LineMatch(
                invoice_line_id=suggestion.invoice_line_id,
                po_line_id=suggestion.po_line_id,
                match_method=MatchMethod.LLM,
                confidence=suggestion.confidence,
                rationale=suggestion.rationale,
            )
            claimed_po_ids.add(suggestion.po_line_id)

    matches = tuple(outcomes[line.id] for line in invoice_lines if line.id in outcomes)
    unmatched_invoice_line_ids = tuple(line.id for line in invoice_lines if line.id not in outcomes)
    unmatched_po_line_ids = tuple(line.id for line in po_lines if line.id not in claimed_po_ids)

    return LineMatchResult(
        matches=matches,
        unmatched_invoice_line_ids=unmatched_invoice_line_ids,
        unmatched_po_line_ids=unmatched_po_line_ids,
    )
