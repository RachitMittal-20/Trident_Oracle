import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from core.errors import PolicyViolation
from core.policy import load_tolerance_policy

TENANT_ID = uuid.uuid4()
NOW = datetime(2026, 1, 1, tzinfo=UTC)

VALID_RULES = {
    "price_variance_pct": 2.0,
    "qty_tolerance_pct": 0.0,
    "auto_approve_below": 5000,
    "dual_approval_above": 100000,
    "min_field_confidence": 0.85,
    "duplicate_window_days": 90,
}


def load(**rule_overrides: object) -> object:
    rules = dict(VALID_RULES)
    rules.update(rule_overrides)
    return load_tolerance_policy(
        id=uuid.uuid4(),
        tenant_id=TENANT_ID,
        name="default",
        is_active=True,
        version=1,
        rules=rules,
        created_at=NOW,
    )


# --- Happy path --------------------------------------------------------------


def test_valid_rules_load_successfully() -> None:
    policy = load()
    assert policy.price_variance_pct == Decimal("2.0")
    assert policy.qty_tolerance_pct == Decimal("0.0")
    assert policy.auto_approve_below == Decimal("5000")
    assert policy.dual_approval_above == Decimal("100000")
    assert policy.min_field_confidence == Decimal("0.85")
    assert policy.duplicate_window_days == 90


def test_string_encoded_numbers_are_accepted() -> None:
    policy = load(price_variance_pct="2.5", auto_approve_below="6000")
    assert policy.price_variance_pct == Decimal("2.5")
    assert policy.auto_approve_below == Decimal("6000")


# --- version -------------------------------------------------------------


def test_version_must_be_positive() -> None:
    with pytest.raises(PolicyViolation):
        load_tolerance_policy(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            name="default",
            is_active=True,
            version=0,
            rules=VALID_RULES,
            created_at=NOW,
        )


# --- percentages in 0-100 -----------------------------------------------


@pytest.mark.parametrize("field", ["price_variance_pct", "qty_tolerance_pct"])
def test_percentage_below_zero_is_rejected(field: str) -> None:
    with pytest.raises(PolicyViolation):
        load(**{field: -0.01})


@pytest.mark.parametrize("field", ["price_variance_pct", "qty_tolerance_pct"])
def test_percentage_above_hundred_is_rejected(field: str) -> None:
    with pytest.raises(PolicyViolation):
        load(**{field: 100.01})


@pytest.mark.parametrize("field", ["price_variance_pct", "qty_tolerance_pct"])
def test_percentage_boundary_values_are_accepted(field: str) -> None:
    load(**{field: 0})
    load(**{field: 100})


# --- amounts positive ------------------------------------------------------


@pytest.mark.parametrize("field", ["auto_approve_below", "dual_approval_above"])
def test_amount_zero_is_rejected(field: str) -> None:
    with pytest.raises(PolicyViolation):
        load(**{field: 0})


@pytest.mark.parametrize("field", ["auto_approve_below", "dual_approval_above"])
def test_amount_negative_is_rejected(field: str) -> None:
    with pytest.raises(PolicyViolation):
        load(**{field: -1})


# --- dual_approval_above > auto_approve_below ------------------------------


def test_dual_approval_equal_to_auto_approve_is_rejected() -> None:
    with pytest.raises(PolicyViolation):
        load(auto_approve_below=5000, dual_approval_above=5000)


def test_dual_approval_below_auto_approve_is_rejected() -> None:
    with pytest.raises(PolicyViolation):
        load(auto_approve_below=5000, dual_approval_above=4999)


def test_dual_approval_strictly_above_auto_approve_is_accepted() -> None:
    load(auto_approve_below=5000, dual_approval_above=5001)


# --- min_field_confidence in 0-1 --------------------------------------------


def test_min_field_confidence_below_zero_is_rejected() -> None:
    with pytest.raises(PolicyViolation):
        load(min_field_confidence=-0.01)


def test_min_field_confidence_above_one_is_rejected() -> None:
    with pytest.raises(PolicyViolation):
        load(min_field_confidence=1.01)


def test_min_field_confidence_boundary_values_are_accepted() -> None:
    load(min_field_confidence=0)
    load(min_field_confidence=1)


# --- duplicate_window_days --------------------------------------------------


def test_negative_duplicate_window_days_is_rejected() -> None:
    with pytest.raises(PolicyViolation):
        load(duplicate_window_days=-1)


def test_non_integer_duplicate_window_days_is_rejected() -> None:
    with pytest.raises(PolicyViolation):
        load(duplicate_window_days=90.5)


# --- malformed/missing fields ------------------------------------------------


def test_missing_field_is_rejected() -> None:
    rules = dict(VALID_RULES)
    del rules["price_variance_pct"]
    with pytest.raises(PolicyViolation):
        load_tolerance_policy(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            name="default",
            is_active=True,
            version=1,
            rules=rules,
            created_at=NOW,
        )


def test_non_numeric_field_is_rejected() -> None:
    with pytest.raises(PolicyViolation):
        load(price_variance_pct="not a number")


def test_boolean_is_rejected_even_though_it_is_an_int_subclass() -> None:
    with pytest.raises(PolicyViolation):
        load(qty_tolerance_pct=True)


# --- raised at load time, not evaluation time -------------------------------


def test_invalid_policy_raises_immediately_at_load_not_lazily() -> None:
    # No downstream use of the return value -- if this doesn't raise here,
    # load_tolerance_policy failed to validate eagerly.
    with pytest.raises(PolicyViolation):
        load(dual_approval_above=1)  # below auto_approve_below (5000)
