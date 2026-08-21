"""Duplicate invoice detection (docs/ARCHITECTURE.md, "Stage 1 — Duplicate
detection"). Pure function, no I/O -- CLAUDE.md's most important rule: this
module queries nothing, it only compares the candidate invoice the caller
hands it against the candidate set of prior invoices the caller also hands
it.

Three tiers, cheapest and most-certain first, checked independently against
every prior invoice (a later, cheaper-tier match doesn't suppress a
same-invoice match at an earlier tier -- each prior invoice is checked
top-down and stops at its first hit):

    1. Hard duplicate     -- identical content_hash. Normally caught at
                             upload before this ever runs; kept here so an
                             invoice arriving by another channel (email,
                             webhook) that skipped that check still gets
                             caught. -> DUPLICATE_INVOICE, severity 'block'.
    2. Exact duplicate    -- same vendor_id and same invoice_number,
                             compared case-insensitively and whitespace-
                             stripped. -> DUPLICATE_INVOICE, severity 'block'.
    3. Suspected duplicate -- same vendor (by normalized name), total within
                             ±0.5%, invoice_date within
                             policy.duplicate_window_days, and at least 70%
                             of line descriptions overlapping by fuzzy
                             similarity. -> SUSPECTED_DUPLICATE, severity
                             'warn', with a detail string naming the specific
                             prior invoice and the reasoning -- a reviewer
                             sees why, not just that.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final
from uuid import UUID

from rapidfuzz import fuzz

from core.errors import MatchingError
from core.matching.vendor_normalize import normalize_vendor_name
from core.models import ExceptionType, Severity, TolerancePolicy

# Fixed thresholds per docs/ARCHITECTURE.md -- unlike the date window, these
# are not policy-configurable.
TOTAL_VARIANCE_PCT: Final[Decimal] = Decimal("0.005")
LINE_OVERLAP_THRESHOLD: Final[Decimal] = Decimal("0.70")

# How similar two individual line descriptions must be (token-set ratio) to
# count as "the same line" when computing overlap for tier 3. Separate from
# LINE_OVERLAP_THRESHOLD, which is the fraction of lines that must clear
# this bar.
LINE_SIMILARITY_THRESHOLD: Final[Decimal] = Decimal("0.80")


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise MatchingError(message)


@dataclass(frozen=True, slots=True)
class InvoiceSummary:
    """The candidate set duplicate detection compares against -- a
    projection of an invoice, not the full aggregate. Both the invoice under
    test and every prior invoice it's compared to are represented this way.
    """

    id: UUID
    vendor_id: UUID
    vendor_name: str
    content_hash: str
    invoice_number: str | None = None
    invoice_date: date | None = None
    total: Decimal | None = None
    line_descriptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _check(bool(self.vendor_name.strip()), "InvoiceSummary.vendor_name must not be blank")
        _check(
            len(self.content_hash) == 64,
            "InvoiceSummary.content_hash must be a 64-character SHA-256 hex digest",
        )
        if self.total is not None:
            _check(self.total >= 0, "InvoiceSummary.total must not be negative")


@dataclass(frozen=True, slots=True)
class DuplicateFinding:
    """One duplicate-detection hit against one specific prior invoice.
    Not a persisted MatchException -- id/match_run_id/created_at are
    DB-assigned and don't exist yet at this point in the pipeline. The
    caller turns each finding into a MatchException row.
    """

    exception_type: ExceptionType
    severity: Severity
    prior_invoice_id: UUID
    detail: str

    def __post_init__(self) -> None:
        _check(
            self.exception_type
            in (ExceptionType.DUPLICATE_INVOICE, ExceptionType.SUSPECTED_DUPLICATE),
            f"DuplicateFinding.exception_type {self.exception_type!r} is not a duplicate-tier type",
        )
        _check(bool(self.detail.strip()), "DuplicateFinding.detail must not be blank")


def _normalize_for_comparison(description: str) -> str:
    return " ".join(sorted(description.lower().split()))


def _line_overlap_ratio(
    candidate_lines: Sequence[str], prior_lines: Sequence[str]
) -> Decimal:
    if not candidate_lines:
        return Decimal("0")
    normalized_prior = [_normalize_for_comparison(line) for line in prior_lines]
    matched = 0
    for line in candidate_lines:
        norm = _normalize_for_comparison(line)
        for prior_norm in normalized_prior:
            score = Decimal(str(fuzz.token_set_ratio(norm, prior_norm))) / Decimal("100")
            if score >= LINE_SIMILARITY_THRESHOLD:
                matched += 1
                break
    return Decimal(matched) / Decimal(len(candidate_lines))


def _totals_within_tolerance(candidate_total: Decimal, prior_total: Decimal) -> bool:
    if candidate_total == 0:
        return prior_total == 0
    delta_pct = abs(candidate_total - prior_total) / candidate_total
    return delta_pct <= TOTAL_VARIANCE_PCT


def _hard_duplicate(candidate: InvoiceSummary, prior: InvoiceSummary) -> DuplicateFinding | None:
    if candidate.content_hash != prior.content_hash:
        return None
    return DuplicateFinding(
        exception_type=ExceptionType.DUPLICATE_INVOICE,
        severity=Severity.BLOCK,
        prior_invoice_id=prior.id,
        detail=(
            f"Identical content as invoice {prior.id} "
            f"(invoice_number={prior.invoice_number!r}) -- same file, byte for byte."
        ),
    )


def _exact_duplicate(candidate: InvoiceSummary, prior: InvoiceSummary) -> DuplicateFinding | None:
    if candidate.vendor_id != prior.vendor_id:
        return None
    if candidate.invoice_number is None or prior.invoice_number is None:
        return None
    candidate_number = candidate.invoice_number.strip().casefold()
    prior_number = prior.invoice_number.strip().casefold()
    if not candidate_number or candidate_number != prior_number:
        return None
    return DuplicateFinding(
        exception_type=ExceptionType.DUPLICATE_INVOICE,
        severity=Severity.BLOCK,
        prior_invoice_id=prior.id,
        detail=(
            f"Same vendor and invoice number ({prior.invoice_number!r}) as invoice {prior.id}."
        ),
    )


def _suspected_duplicate(
    candidate: InvoiceSummary, prior: InvoiceSummary, policy: TolerancePolicy
) -> DuplicateFinding | None:
    if normalize_vendor_name(candidate.vendor_name) != normalize_vendor_name(prior.vendor_name):
        return None
    if candidate.total is None or prior.total is None:
        return None
    if not _totals_within_tolerance(candidate.total, prior.total):
        return None
    if candidate.invoice_date is None or prior.invoice_date is None:
        return None
    days_apart = abs((candidate.invoice_date - prior.invoice_date).days)
    if days_apart > policy.duplicate_window_days:
        return None
    overlap = _line_overlap_ratio(candidate.line_descriptions, prior.line_descriptions)
    if overlap < LINE_OVERLAP_THRESHOLD:
        return None

    delta_pct = (
        Decimal("0")
        if candidate.total == 0
        else (abs(candidate.total - prior.total) / candidate.total * Decimal("100"))
    )
    matched_lines = round(overlap * len(candidate.line_descriptions))
    return DuplicateFinding(
        exception_type=ExceptionType.SUSPECTED_DUPLICATE,
        severity=Severity.WARN,
        prior_invoice_id=prior.id,
        detail=(
            f"Resembles invoice {prior.id} (invoice_number={prior.invoice_number!r}) "
            f"from the same vendor: total {candidate.total} vs {prior.total} "
            f"(Δ{delta_pct.quantize(Decimal('0.01'))}%), dated {candidate.invoice_date} vs "
            f"{prior.invoice_date} ({days_apart} day(s) apart), and "
            f"{matched_lines}/{len(candidate.line_descriptions)} line items "
            f"({(overlap * 100).quantize(Decimal('0.1'))}%) fuzzy-match."
        ),
    )


def find_duplicates(
    candidate: InvoiceSummary,
    prior_invoices: Sequence[InvoiceSummary],
    policy: TolerancePolicy,
) -> tuple[DuplicateFinding, ...]:
    """Compare `candidate` against every invoice in `prior_invoices`,
    cascading tier 1 -> 2 -> 3 per prior invoice and stopping at its first
    hit. Pure: no DB, no network -- `prior_invoices` is the caller's
    candidate set, not something this function goes and fetches.
    """
    findings: list[DuplicateFinding] = []
    for prior in prior_invoices:
        if prior.id == candidate.id:
            continue
        finding = (
            _hard_duplicate(candidate, prior)
            or _exact_duplicate(candidate, prior)
            or _suspected_duplicate(candidate, prior, policy)
        )
        if finding is not None:
            findings.append(finding)
    return tuple(findings)
