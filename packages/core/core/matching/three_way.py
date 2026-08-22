"""The three-way match -- docs/ARCHITECTURE.md section 6, "the intellectual
core" of Trident Oracle. Pure function, no I/O -- CLAUDE.md's most important
rule. Every fact this module needs (the invoice and its lines, the PO and its
lines, the GRN and its lines, the active policy, the candidate set of recent
invoices for duplicate detection, and even "what day is it") is handed in by
the caller. This module never reaches out to get anything itself, which is
also why `today` is a required argument rather than `date.today()` read
internally -- a pure function's output must be reproducible from its inputs
alone.

docs/ARCHITECTURE.md sketches the signature as
`run_three_way_match(invoice, po, grn, policy, recent_invoices)`, eliding the
line collections for brevity. A pure function can't go fetch invoice_lines,
po_lines, or grn_lines on its own, so this module's real signature adds them
explicitly -- the caller (worker) loads everything once and hands it over.

The pipeline, cheapest and highest-value checks first:

    1. Duplicates (duplicates.py). A hard or exact duplicate is a `block`-
       severity DUPLICATE_INVOICE finding, and per the prompt, short-circuits
       the whole match -- there is no point spending a Gemini call or a
       fuzzy-matching pass reconciling an invoice we're about to reject
       outright. A merely SUSPECTED_DUPLICATE ('warn') does not short-circuit;
       it rides along as one more finding in the final result.
    2. Linkage. No PO at all means there are no PO lines to match invoice
       lines against, no unit prices to compare, nothing -- so a missing PO
       halts the pipeline right there (after recording NO_PO, and NO_GRN too
       if that's also missing). A missing GRN is different: we still have a
       PO, so line matching and price checks are still useful information for
       a reviewer even though quantity can't be verified against nothing --
       so a missing GRN only skips the per-line QUANTITY check, not the rest
       of the pipeline.
    3. Line matching (line_matcher.py).
    4. Per matched pair: QUANTITY and PRICE checks.
    5. Unmatched invoice lines.
    6. Document arithmetic -- runs unconditionally, even over an otherwise
       spotless invoice.
    7. TAX_MISMATCH and DATE_ANOMALY.
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Final, Literal
from uuid import UUID

from core.errors import MatchingError
from core.matching.duplicates import InvoiceSummary, find_duplicates
from core.matching.line_matcher import (
    DEFAULT_FUZZY_THRESHOLD,
    LineMatchResult,
    LlmLineMatcher,
    match_lines,
)
from core.models import (
    ExceptionType,
    GoodsReceipt,
    GoodsReceiptLine,
    Invoice,
    InvoiceLine,
    PurchaseOrder,
    PurchaseOrderLine,
    Severity,
    TolerancePolicy,
    Vendor,
)

ARITHMETIC_TOLERANCE: Final[Decimal] = Decimal("0.01")

# GST-style slabs, close enough for a demo policy default -- callers running
# in a different tax regime pass their own via `expected_tax_rates_pct`.
DEFAULT_EXPECTED_TAX_RATES_PCT: Final[tuple[Decimal, ...]] = (
    Decimal("0"),
    Decimal("5"),
    Decimal("12"),
    Decimal("18"),
    Decimal("28"),
)
DEFAULT_TAX_RATE_EPSILON_PCT: Final[Decimal] = Decimal("0.5")


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise MatchingError(message)


@dataclass(frozen=True, slots=True)
class MatchFinding:
    """One exception surfaced by the match, before persistence. Not a
    core.models.MatchException -- id/match_run_id/created_at are DB-assigned
    and don't exist at this point in the pipeline (same reasoning as
    duplicates.DuplicateFinding). `detail` always carries the specific
    reasoning a reviewer needs, never just the bare exception_type."""

    exception_type: ExceptionType
    severity: Severity
    detail: str
    po_line_id: UUID | None = None
    invoice_line_id: UUID | None = None
    expected_value: Decimal | None = None
    actual_value: Decimal | None = None
    delta: Decimal | None = None
    delta_pct: Decimal | None = None

    def __post_init__(self) -> None:
        _check(bool(self.detail.strip()), "MatchFinding.detail must not be blank")


@dataclass(frozen=True, slots=True)
class ThreeWayMatchResult:
    invoice_id: UUID
    result: Literal["clean", "exceptions", "blocked"]
    findings: tuple[MatchFinding, ...]
    line_matches: LineMatchResult | None
    stage_timings_ms: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        _check(
            self.result in ("clean", "exceptions", "blocked"),
            f"ThreeWayMatchResult.result {self.result!r} is not recognized",
        )
        if self.result == "clean":
            _check(
                len(self.findings) == 0,
                "ThreeWayMatchResult.result 'clean' must have no findings",
            )
        else:
            _check(
                len(self.findings) > 0,
                f"ThreeWayMatchResult.result {self.result!r} must have at least one finding",
            )


def _overall_result(findings: Sequence[MatchFinding]) -> Literal["clean", "exceptions", "blocked"]:
    if any(f.severity == Severity.BLOCK for f in findings):
        return "blocked"
    if findings:
        return "exceptions"
    return "clean"


def _timed(timings: dict[str, Decimal], stage: str, start: float) -> None:
    timings[stage] = Decimal(str((time.perf_counter() - start) * 1000))


# --- Stage 4a: QUANTITY -----------------------------------------------------


def _qty_finding(
    po_line: PurchaseOrderLine,
    invoice_line: InvoiceLine,
    received_qty: Decimal,
    policy: TolerancePolicy,
) -> MatchFinding | None:
    """Compares invoice.qty against grn.qty_received -- deliberately NOT
    po.qty_ordered. Three-way matching exists precisely so you pay for what
    ARRIVED, not what you ordered: an over-ordered PO doesn't entitle a
    vendor to bill for units that were never delivered, and an invoice that
    merely matches the PO tells you nothing about what actually showed up on
    the dock. `received_qty` is already computed with damaged-condition
    receipt lines excluded by the caller."""
    actual = invoice_line.qty
    delta = actual - received_qty
    if delta == 0:
        return None

    delta_pct = Decimal("100") if received_qty == 0 else abs(delta) / received_qty * Decimal("100")
    if delta_pct <= policy.qty_tolerance_pct:
        return None

    if delta > 0:
        return MatchFinding(
            exception_type=ExceptionType.QTY_OVER,
            severity=Severity.BLOCK,
            po_line_id=po_line.id,
            invoice_line_id=invoice_line.id,
            expected_value=received_qty,
            actual_value=actual,
            delta=delta,
            delta_pct=delta_pct,
            detail=(
                f"Invoice line {invoice_line.line_no} bills {actual} units but only "
                f"{received_qty} were received against PO line {po_line.line_no} "
                f"(damaged units excluded) -- {delta_pct.quantize(Decimal('0.01'))}% over."
            ),
        )
    return MatchFinding(
        exception_type=ExceptionType.QTY_SHORT,
        severity=Severity.INFO,
        po_line_id=po_line.id,
        invoice_line_id=invoice_line.id,
        expected_value=received_qty,
        actual_value=actual,
        delta=delta,
        delta_pct=delta_pct,
        detail=(
            f"Invoice line {invoice_line.line_no} bills {actual} units while "
            f"{received_qty} were received against PO line {po_line.line_no} "
            f"(damaged units excluded) -- {delta_pct.quantize(Decimal('0.01'))}% short."
        ),
    )


# --- Stage 4b: PRICE ---------------------------------------------------------


def _price_finding(
    po_line: PurchaseOrderLine, invoice_line: InvoiceLine, policy: TolerancePolicy
) -> MatchFinding | None:
    expected = po_line.unit_price
    actual = invoice_line.unit_price
    delta = actual - expected
    if delta == 0:
        return None

    delta_pct = Decimal("100") if expected == 0 else abs(delta) / expected * Decimal("100")
    tolerance = policy.price_variance_pct
    if delta_pct <= tolerance:
        severity = Severity.INFO
    elif delta_pct <= tolerance * 2:
        severity = Severity.WARN
    else:
        severity = Severity.BLOCK

    sign = "+" if delta > 0 else ""
    return MatchFinding(
        exception_type=ExceptionType.PRICE_VARIANCE,
        severity=severity,
        po_line_id=po_line.id,
        invoice_line_id=invoice_line.id,
        expected_value=expected,
        actual_value=actual,
        delta=delta,
        delta_pct=delta_pct,
        detail=(
            f"Invoice line {invoice_line.line_no} priced at {actual} vs PO line "
            f"{po_line.line_no} at {expected} "
            f"({sign}{delta_pct.quantize(Decimal('0.01'))}% variance)."
        ),
    )


# --- Stage 6: document arithmetic -------------------------------------------


def _arithmetic_findings(
    invoice: Invoice, invoice_lines: Sequence[InvoiceLine]
) -> list[MatchFinding]:
    """Runs unconditionally, even over an invoice that looked clean at every
    other stage. A total that doesn't reconcile means the extraction is
    wrong regardless of what confidence the model reported for it -- a
    high-confidence read of the wrong number is still the wrong number, and
    that's a data-integrity problem no downstream check would otherwise
    catch. Two of the three checks below (per-line line_total, and
    subtotal+tax==total) are also enforced exactly by InvoiceLine/Invoice's
    own constructors, so they're structurally unreachable for valid domain
    objects -- kept here anyway as the last checkpoint before this invoice
    can post, in case that ever changes.
    """
    findings: list[MatchFinding] = []

    line_sum = sum((line.line_total for line in invoice_lines), Decimal("0"))
    if invoice.subtotal is not None and abs(line_sum - invoice.subtotal) > ARITHMETIC_TOLERANCE:
        findings.append(
            MatchFinding(
                exception_type=ExceptionType.ARITHMETIC_ERROR,
                severity=Severity.BLOCK,
                expected_value=invoice.subtotal,
                actual_value=line_sum,
                delta=line_sum - invoice.subtotal,
                detail=(
                    f"Invoice line items sum to {line_sum} but the extracted subtotal is "
                    f"{invoice.subtotal} -- extraction is wrong regardless of reported confidence."
                ),
            )
        )

    if invoice.subtotal is not None and invoice.tax is not None and invoice.total is not None:
        computed_total = invoice.subtotal + invoice.tax
        if abs(computed_total - invoice.total) > ARITHMETIC_TOLERANCE:
            findings.append(
                MatchFinding(
                    exception_type=ExceptionType.ARITHMETIC_ERROR,
                    severity=Severity.BLOCK,
                    expected_value=computed_total,
                    actual_value=invoice.total,
                    delta=invoice.total - computed_total,
                    detail=(
                        f"subtotal ({invoice.subtotal}) + tax ({invoice.tax}) = {computed_total}, "
                        f"which doesn't reconcile with the extracted total of {invoice.total}."
                    ),
                )
            )

    for line in invoice_lines:
        computed = line.qty * line.unit_price
        if abs(computed - line.line_total) > ARITHMETIC_TOLERANCE:
            findings.append(
                MatchFinding(
                    exception_type=ExceptionType.ARITHMETIC_ERROR,
                    severity=Severity.BLOCK,
                    invoice_line_id=line.id,
                    expected_value=computed,
                    actual_value=line.line_total,
                    delta=line.line_total - computed,
                    detail=(
                        f"Invoice line {line.line_no}: qty ({line.qty}) * unit_price "
                        f"({line.unit_price}) = {computed}, which doesn't reconcile with the "
                        f"extracted line_total of {line.line_total}."
                    ),
                )
            )

    return findings


# --- Stage 7: TAX_MISMATCH and DATE_ANOMALY ---------------------------------


def _tax_mismatch_finding(
    invoice: Invoice,
    expected_tax_rates_pct: Sequence[Decimal],
    tax_rate_epsilon_pct: Decimal,
) -> MatchFinding | None:
    if invoice.subtotal is None or invoice.tax is None or invoice.subtotal == 0:
        return None
    effective_rate = invoice.tax / invoice.subtotal * Decimal("100")
    if any(abs(effective_rate - rate) <= tax_rate_epsilon_pct for rate in expected_tax_rates_pct):
        return None
    nearest = min(expected_tax_rates_pct, key=lambda rate: abs(effective_rate - rate))
    rates_listed = ", ".join(f"{rate}%" for rate in expected_tax_rates_pct)
    return MatchFinding(
        exception_type=ExceptionType.TAX_MISMATCH,
        severity=Severity.WARN,
        expected_value=nearest,
        actual_value=effective_rate,
        delta=effective_rate - nearest,
        detail=(
            f"Effective tax rate is {effective_rate.quantize(Decimal('0.01'))}%, which doesn't "
            f"match any expected rate ({rates_listed}) within {tax_rate_epsilon_pct}pp."
        ),
    )


def _date_anomaly_finding(
    invoice: Invoice, po: PurchaseOrder | None, today: date
) -> MatchFinding | None:
    if invoice.invoice_date is None:
        return None
    if invoice.invoice_date > today:
        return MatchFinding(
            exception_type=ExceptionType.DATE_ANOMALY,
            severity=Severity.WARN,
            actual_value=None,
            detail=f"Invoice is dated {invoice.invoice_date}, which is after today ({today}).",
        )
    if po is not None and invoice.invoice_date < po.issued_at.date():
        return MatchFinding(
            exception_type=ExceptionType.DATE_ANOMALY,
            severity=Severity.WARN,
            detail=(
                f"Invoice is dated {invoice.invoice_date}, before PO {po.po_number} was even "
                f"issued on {po.issued_at.date()}."
            ),
        )
    return None


# --- The pipeline -------------------------------------------------------------


def run_three_way_match(
    invoice: Invoice,
    invoice_lines: Sequence[InvoiceLine],
    vendor: Vendor,
    po: PurchaseOrder | None,
    po_lines: Sequence[PurchaseOrderLine],
    grn: GoodsReceipt | None,
    grn_lines: Sequence[GoodsReceiptLine],
    policy: TolerancePolicy,
    recent_invoices: Sequence[InvoiceSummary],
    *,
    today: date,
    expected_tax_rates_pct: Sequence[Decimal] = DEFAULT_EXPECTED_TAX_RATES_PCT,
    tax_rate_epsilon_pct: Decimal = DEFAULT_TAX_RATE_EPSILON_PCT,
    fuzzy_threshold: Decimal = DEFAULT_FUZZY_THRESHOLD,
    llm_matcher: LlmLineMatcher | None = None,
) -> ThreeWayMatchResult:
    """Run the full three-way match for one invoice. Pure: no DB, no
    network, no file I/O, no reading the system clock for `today` -- every
    fact used comes in as an argument. `vendor` is required because
    duplicate detection compares normalized vendor *names*, and Invoice only
    carries a vendor_id.
    """
    _check(invoice.vendor_id is not None, "run_three_way_match requires invoice.vendor_id")
    _check(invoice.vendor_id == vendor.id, "vendor.id must match invoice.vendor_id")
    _check(
        invoice.invoice_number is not None,
        "run_three_way_match requires an extracted invoice_number",
    )
    _check(
        invoice.subtotal is not None and invoice.tax is not None and invoice.total is not None,
        "run_three_way_match requires extracted subtotal/tax/total",
    )

    timings: dict[str, Decimal] = {}

    # --- Stage 1: duplicates -------------------------------------------------
    start = time.perf_counter()
    candidate_summary = InvoiceSummary(
        id=invoice.id,
        vendor_id=vendor.id,
        vendor_name=vendor.name,
        content_hash=invoice.content_hash,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        total=invoice.total,
        line_descriptions=tuple(line.description for line in invoice_lines),
    )
    duplicate_findings = find_duplicates(candidate_summary, recent_invoices, policy)
    _timed(timings, "duplicates", start)

    findings: list[MatchFinding] = [
        MatchFinding(
            exception_type=d.exception_type,
            severity=d.severity,
            detail=d.detail,
        )
        for d in duplicate_findings
    ]

    # A hard/exact duplicate is conclusive -- don't spend a fuzzy match or an
    # LLM call reconciling an invoice we're about to reject outright.
    if any(d.exception_type == ExceptionType.DUPLICATE_INVOICE for d in duplicate_findings):
        return ThreeWayMatchResult(
            invoice_id=invoice.id,
            result="blocked",
            findings=tuple(findings),
            line_matches=None,
            stage_timings_ms=MappingProxyType(timings),
        )

    # --- Stage 2: linkage -----------------------------------------------------
    start = time.perf_counter()
    if po is None:
        detail = "No matching purchase order found."
        if grn is None:
            detail += " No goods receipt either -- this invoice cannot be matched at all."
        findings.append(
            MatchFinding(exception_type=ExceptionType.NO_PO, severity=Severity.BLOCK, detail=detail)
        )
    if grn is None:
        detail = "No matching goods receipt found."
        if po is None:
            detail += " No purchase order either -- this invoice cannot be matched at all."
        findings.append(
            MatchFinding(
                exception_type=ExceptionType.NO_GRN, severity=Severity.BLOCK, detail=detail
            )
        )
    _timed(timings, "linkage", start)

    if po is None:
        # No PO means no PO lines -- nothing left to match, price, or check.
        return ThreeWayMatchResult(
            invoice_id=invoice.id,
            result=_overall_result(findings),
            findings=tuple(findings),
            line_matches=None,
            stage_timings_ms=MappingProxyType(timings),
        )

    # --- Stage 3: line matching ------------------------------------------------
    start = time.perf_counter()
    line_match_result = match_lines(
        invoice_lines, po_lines, fuzzy_threshold=fuzzy_threshold, llm_matcher=llm_matcher
    )
    _timed(timings, "line_matching", start)

    # --- Stage 4: per-line QUANTITY and PRICE ----------------------------------
    start = time.perf_counter()
    invoice_lines_by_id = {line.id: line for line in invoice_lines}
    po_lines_by_id = {line.id: line for line in po_lines}

    received_qty: dict[UUID, Decimal] = {}
    if grn is not None:
        for grn_line in grn_lines:
            if grn_line.condition == "damaged":
                continue
            received_qty[grn_line.po_line_id] = (
                received_qty.get(grn_line.po_line_id, Decimal("0")) + grn_line.qty_received
            )

    for line_match in line_match_result.matches:
        invoice_line = invoice_lines_by_id[line_match.invoice_line_id]
        po_line = po_lines_by_id[line_match.po_line_id]

        if grn is not None:
            qty_finding = _qty_finding(
                po_line, invoice_line, received_qty.get(po_line.id, Decimal("0")), policy
            )
            if qty_finding is not None:
                findings.append(qty_finding)

        price_finding = _price_finding(po_line, invoice_line, policy)
        if price_finding is not None:
            findings.append(price_finding)
    _timed(timings, "per_line_checks", start)

    # --- Stage 5: unmatched invoice lines ---------------------------------------
    start = time.perf_counter()
    for invoice_line_id in line_match_result.unmatched_invoice_line_ids:
        invoice_line = invoice_lines_by_id[invoice_line_id]
        findings.append(
            MatchFinding(
                exception_type=ExceptionType.UNMATCHED_LINE,
                severity=Severity.BLOCK,
                invoice_line_id=invoice_line.id,
                detail=(
                    f"Invoice line {invoice_line.line_no} ({invoice_line.description!r}, "
                    f"qty {invoice_line.qty} @ {invoice_line.unit_price}) does not correspond to "
                    f"any PO line -- billed for something that was never ordered."
                ),
            )
        )
    _timed(timings, "unmatched_lines", start)

    # --- Stage 6: document arithmetic ------------------------------------------
    start = time.perf_counter()
    findings.extend(_arithmetic_findings(invoice, invoice_lines))
    _timed(timings, "arithmetic", start)

    # --- Stage 7: TAX_MISMATCH and DATE_ANOMALY ---------------------------------
    start = time.perf_counter()
    tax_finding = _tax_mismatch_finding(invoice, expected_tax_rates_pct, tax_rate_epsilon_pct)
    if tax_finding is not None:
        findings.append(tax_finding)
    date_finding = _date_anomaly_finding(invoice, po, today)
    if date_finding is not None:
        findings.append(date_finding)
    _timed(timings, "tax_and_date", start)

    return ThreeWayMatchResult(
        invoice_id=invoice.id,
        result=_overall_result(findings),
        findings=tuple(findings),
        line_matches=line_match_result,
        stage_timings_ms=MappingProxyType(timings),
    )
