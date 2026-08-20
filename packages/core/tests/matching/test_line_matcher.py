import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from core.errors import MatchingError
from core.matching.line_matcher import (
    LlmMatchSuggestion,
    match_lines,
)
from core.models import InvoiceLine, MatchMethod, PurchaseOrderLine

TENANT_ID = uuid.uuid4()
INVOICE_ID = uuid.uuid4()
PO_ID = uuid.uuid4()
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_invoice_line(**overrides: object) -> InvoiceLine:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        invoice_id=INVOICE_ID,
        line_no=1,
        description="Widget",
        qty=Decimal("10"),
        unit_price=Decimal("5.00"),
        line_total=Decimal("50.00"),
        created_at=NOW,
        normalized_description=None,
    )
    fields.update(overrides)
    return InvoiceLine(**fields)  # type: ignore[arg-type]


def make_po_line(**overrides: object) -> PurchaseOrderLine:
    fields = dict(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        po_id=PO_ID,
        line_no=1,
        description="Widget",
        normalized_description="widget",
        qty_ordered=Decimal("10"),
        unit_price=Decimal("5.00"),
        tax_rate=Decimal("0"),
        line_total=Decimal("50.00"),
        created_at=NOW,
        sku=None,
    )
    fields.update(overrides)
    return PurchaseOrderLine(**fields)  # type: ignore[arg-type]


# --- Tier 1: exact SKU -------------------------------------------------------


def test_exact_sku_match_wins_over_a_higher_fuzzy_score() -> None:
    # sku_po's description is a poor fuzzy match for the invoice line, but
    # carries the sku the invoice line literally quotes -- fuzzy_po is a
    # near-perfect textual match. SKU must win regardless.
    sku_po = make_po_line(
        description="Bracket assembly", normalized_description="bracket assembly", sku="WID-4521"
    )
    fuzzy_po = make_po_line(description="Blue widget", normalized_description="blue widget")
    inv_line = make_invoice_line(description="WID-4521 replacement part")

    result = match_lines([inv_line], [sku_po, fuzzy_po])

    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.po_line_id == sku_po.id
    assert match.match_method == MatchMethod.SKU
    assert match.confidence == Decimal("1")
    assert fuzzy_po.id in result.unmatched_po_line_ids


def test_sku_match_requires_a_whole_token_not_a_substring() -> None:
    po_line = make_po_line(
        sku="WID-45", description="Bracket assembly", normalized_description="bracket assembly"
    )
    # "WID-4521" contains "WID-45" as a substring but is a different token.
    # description is deliberately unrelated to the PO line's so this test
    # isolates the sku tier -- it must not accidentally pass via fuzzy.
    inv_line = make_invoice_line(description="WID-4521 replacement part")

    result = match_lines([inv_line], [po_line])

    assert result.matches == ()
    assert inv_line.id in result.unmatched_invoice_line_ids


def test_sku_match_is_case_insensitive() -> None:
    po_line = make_po_line(sku="wid-4521")
    inv_line = make_invoice_line(description="WID-4521 widget")

    result = match_lines([inv_line], [po_line])

    assert len(result.matches) == 1
    assert result.matches[0].match_method == MatchMethod.SKU


def test_sku_tier_never_double_assigns_a_po_line() -> None:
    po_line = make_po_line(sku="WID-4521")
    inv_a = make_invoice_line(description="WID-4521 widget", line_no=1)
    inv_b = make_invoice_line(description="WID-4521 widget", line_no=2)

    result = match_lines([inv_a, inv_b], [po_line])

    sku_matches = [m for m in result.matches if m.match_method == MatchMethod.SKU]
    assert len(sku_matches) == 1
    matched_invoice_ids = {m.invoice_line_id for m in sku_matches}
    unmatched_invoice_ids = set(result.unmatched_invoice_line_ids)
    assert matched_invoice_ids | unmatched_invoice_ids == {inv_a.id, inv_b.id}
    assert len(matched_invoice_ids) == 1


# --- Tier 2: fuzzy ------------------------------------------------------------


def test_greedy_resolution_when_two_invoice_lines_fuzzy_match_one_po_line() -> None:
    po_line = make_po_line(description="Blue widget", normalized_description="blue widget")
    good_match = make_invoice_line(description="Blue widget", line_no=1)
    weaker_match = make_invoice_line(description="Blu widgett", line_no=2)

    result = match_lines([good_match, weaker_match], [po_line])

    fuzzy_matches = {
        m.invoice_line_id: m for m in result.matches if m.match_method == MatchMethod.FUZZY
    }
    assert good_match.id in fuzzy_matches
    assert fuzzy_matches[good_match.id].po_line_id == po_line.id
    # The PO line is claimed -- the second invoice line has nothing left to
    # match against and falls through to unmatched, never double-assigned.
    assert weaker_match.id in result.unmatched_invoice_line_ids
    assert weaker_match.id not in fuzzy_matches


def test_threshold_boundary_exact_match_included(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.matching.line_matcher._token_set_ratio", lambda a, b: 88.0)
    inv_line = make_invoice_line()
    po_line = make_po_line()

    result = match_lines([inv_line], [po_line], fuzzy_threshold=Decimal("0.88"))

    assert len(result.matches) == 1
    assert result.matches[0].match_method == MatchMethod.FUZZY
    assert result.matches[0].confidence == Decimal("0.88")


def test_threshold_boundary_just_under_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.matching.line_matcher._token_set_ratio", lambda a, b: 87.99)
    inv_line = make_invoice_line()
    po_line = make_po_line()

    result = match_lines([inv_line], [po_line], fuzzy_threshold=Decimal("0.88"))

    assert result.matches == ()
    assert inv_line.id in result.unmatched_invoice_line_ids
    assert po_line.id in result.unmatched_po_line_ids


def test_abbreviation_and_token_order_variance_still_match() -> None:
    po_line = make_po_line(description="LG Widget, Blue", normalized_description="LG Widget, Blue")
    inv_line = make_invoice_line(description="blue widget lg")

    result = match_lines([inv_line], [po_line])

    assert len(result.matches) == 1
    assert result.matches[0].match_method == MatchMethod.FUZZY
    assert result.matches[0].po_line_id == po_line.id


def test_fuzzy_uses_raw_description_when_normalized_description_is_none() -> None:
    po_line = make_po_line(description="Blue widget", normalized_description="blue widget")
    inv_line = make_invoice_line(description="Blue widget", normalized_description=None)

    result = match_lines([inv_line], [po_line])

    assert len(result.matches) == 1
    assert result.matches[0].match_method == MatchMethod.FUZZY


# --- Tier 3: LLM fallback -----------------------------------------------------


def test_llm_tier_skipped_when_no_callable_is_injected() -> None:
    inv_line = make_invoice_line(description="totally unrelated text")
    po_line = make_po_line(description="Something else entirely", normalized_description="x")

    result = match_lines([inv_line], [po_line], llm_matcher=None)

    assert result.matches == ()
    assert inv_line.id in result.unmatched_invoice_line_ids
    assert po_line.id in result.unmatched_po_line_ids


def test_llm_tier_receives_one_batched_call_for_n_unmatched_lines() -> None:
    inv_lines = [
        make_invoice_line(description=f"zzz mystery item {i}", line_no=i + 1) for i in range(5)
    ]
    po_lines = [
        make_po_line(
            description=f"qqq unrelated {i}",
            normalized_description=f"qqq unrelated {i}",
            line_no=i + 1,
        )
        for i in range(5)
    ]

    call_count = 0
    received_invoice_lines: tuple[InvoiceLine, ...] = ()

    def fake_llm_matcher(
        unmatched_invoice_lines: tuple[InvoiceLine, ...],
        candidate_po_lines: tuple[PurchaseOrderLine, ...],
    ) -> list[LlmMatchSuggestion]:
        nonlocal call_count, received_invoice_lines
        call_count += 1
        received_invoice_lines = tuple(unmatched_invoice_lines)
        return [
            LlmMatchSuggestion(
                invoice_line_id=line.id,
                po_line_id=None,
                confidence=Decimal("0.5"),
                rationale="no clear correspondence",
            )
            for line in unmatched_invoice_lines
        ]

    match_lines(inv_lines, po_lines, llm_matcher=fake_llm_matcher)

    assert call_count == 1
    assert len(received_invoice_lines) == 5


def test_llm_tier_only_receives_lines_unmatched_after_sku_and_fuzzy() -> None:
    sku_po = make_po_line(sku="WID-1", line_no=1)
    fuzzy_po = make_po_line(
        description="Blue widget", normalized_description="blue widget", line_no=2
    )
    leftover_po = make_po_line(
        description="Random", normalized_description="random gizmo", line_no=3
    )

    sku_inv = make_invoice_line(description="WID-1 item", line_no=1)
    fuzzy_inv = make_invoice_line(description="Blue widget", line_no=2)
    leftover_inv = make_invoice_line(description="nothing like it", line_no=3)

    received: tuple[InvoiceLine, ...] = ()

    def fake_llm_matcher(
        unmatched_invoice_lines: tuple[InvoiceLine, ...],
        candidate_po_lines: tuple[PurchaseOrderLine, ...],
    ) -> list[LlmMatchSuggestion]:
        nonlocal received
        received = tuple(unmatched_invoice_lines)
        return []

    match_lines(
        [sku_inv, fuzzy_inv, leftover_inv],
        [sku_po, fuzzy_po, leftover_po],
        llm_matcher=fake_llm_matcher,
    )

    assert received == (leftover_inv,)


def test_llm_tier_applies_suggestions_and_respects_confidence() -> None:
    po_a = make_po_line(description="Gizmo A", normalized_description="gizmo a", line_no=1)
    po_b = make_po_line(description="Gizmo B", normalized_description="gizmo b", line_no=2)
    inv_line = make_invoice_line(description="some gizmo")

    def fake_llm_matcher(
        unmatched_invoice_lines: tuple[InvoiceLine, ...],
        candidate_po_lines: tuple[PurchaseOrderLine, ...],
    ) -> list[LlmMatchSuggestion]:
        return [
            LlmMatchSuggestion(
                invoice_line_id=inv_line.id,
                po_line_id=po_b.id,
                confidence=Decimal("0.7"),
                rationale="matches gizmo B's description most closely",
            )
        ]

    result = match_lines([inv_line], [po_a, po_b], llm_matcher=fake_llm_matcher)

    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.match_method == MatchMethod.LLM
    assert match.po_line_id == po_b.id
    assert match.confidence == Decimal("0.7")
    assert match.rationale == "matches gizmo B's description most closely"
    assert po_a.id in result.unmatched_po_line_ids


def test_llm_tier_never_double_assigns_a_po_line_across_suggestions() -> None:
    po_line = make_po_line(description="Zqx unit", normalized_description="zqx unit")
    inv_low = make_invoice_line(description="Bolt fastener", line_no=1)
    inv_high = make_invoice_line(description="Steel pin kit", line_no=2)

    def fake_llm_matcher(
        unmatched_invoice_lines: tuple[InvoiceLine, ...],
        candidate_po_lines: tuple[PurchaseOrderLine, ...],
    ) -> list[LlmMatchSuggestion]:
        return [
            LlmMatchSuggestion(
                invoice_line_id=inv_low.id,
                po_line_id=po_line.id,
                confidence=Decimal("0.4"),
                rationale="weak guess",
            ),
            LlmMatchSuggestion(
                invoice_line_id=inv_high.id,
                po_line_id=po_line.id,
                confidence=Decimal("0.9"),
                rationale="strong guess",
            ),
        ]

    result = match_lines([inv_low, inv_high], [po_line], llm_matcher=fake_llm_matcher)

    assert len(result.matches) == 1
    assert result.matches[0].invoice_line_id == inv_high.id
    assert inv_low.id in result.unmatched_invoice_line_ids


def test_llm_suggestion_of_none_leaves_line_unmatched() -> None:
    po_line = make_po_line(description="Zqx unit", normalized_description="zqx unit")
    inv_line = make_invoice_line(description="Bolt fastener kit")

    def fake_llm_matcher(
        unmatched_invoice_lines: tuple[InvoiceLine, ...],
        candidate_po_lines: tuple[PurchaseOrderLine, ...],
    ) -> list[LlmMatchSuggestion]:
        return [
            LlmMatchSuggestion(
                invoice_line_id=inv_line.id,
                po_line_id=None,
                confidence=Decimal("0.1"),
                rationale="no correspondence found",
            )
        ]

    result = match_lines([inv_line], [po_line], llm_matcher=fake_llm_matcher)

    assert result.matches == ()
    assert inv_line.id in result.unmatched_invoice_line_ids


# --- Fully unmatched -----------------------------------------------------


def test_lines_with_no_match_anywhere_are_reported_unmatched() -> None:
    inv_line = make_invoice_line(description="completely unrelated text")
    po_line = make_po_line(description="also unrelated", normalized_description="also unrelated")

    result = match_lines([inv_line], [po_line])

    assert result.matches == ()
    assert result.unmatched_invoice_line_ids == (inv_line.id,)
    assert result.unmatched_po_line_ids == (po_line.id,)


# --- Validation ------------------------------------------------------------


@pytest.mark.parametrize("threshold", [Decimal("-0.01"), Decimal("1.01")])
def test_invalid_fuzzy_threshold_raises(threshold: Decimal) -> None:
    with pytest.raises(MatchingError):
        match_lines([], [], fuzzy_threshold=threshold)


def test_line_match_rejects_unmatched_method() -> None:
    from core.matching.line_matcher import LineMatch

    with pytest.raises(MatchingError):
        LineMatch(
            invoice_line_id=uuid.uuid4(),
            po_line_id=uuid.uuid4(),
            match_method=MatchMethod.UNMATCHED,
            confidence=Decimal("1"),
        )
