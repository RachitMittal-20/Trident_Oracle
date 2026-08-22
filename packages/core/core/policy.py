"""TolerancePolicy loading and validation.

tolerance_policies.rules (db/migrations/0006_matching.sql) is a permissive
JSONB blob -- whatever a human editing a policy through the UI or database
directly can type ends up in there, so it has to be treated as untrusted
input, not a pre-validated ruleset. CLAUDE.md principle 7 -- "fail loudly in
development, degrade gracefully in production" -- means a malformed policy
must be rejected here, at load time, with a clear PolicyViolation, never
discovered three stages deep into a match run when some comparison produces
a nonsensical result nobody can explain.

TolerancePolicy's own constructor (core/models.py) already enforces a few
invariants (non-negative percentages, dual_approval_above >=
auto_approve_below, confidence in [0, 1]), but those are meant as a last
line of defense against a bug in *this* module, not the primary validation
surface -- they raise plain TridentOracleError, and some of them are looser
than what a real policy requires (e.g. the model allows auto_approve_below
== 0, or dual_approval_above == auto_approve_below; a real policy shouldn't).
Every check below is a superset of the model's, and everything here raises
PolicyViolation specifically, so a caller always sees one exception type for
"this policy is bad" regardless of which rule caught it.

Policies are versioned (tolerance_policies.version): callers pass the row's
version straight through into the returned TolerancePolicy, and every
match_run persists policy_version alongside its result, so a historical
decision stays explicable even after the active policy has since changed.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from core.errors import PolicyViolation, TridentOracleError
from core.models import TolerancePolicy

_PERCENTAGE_FIELDS = ("price_variance_pct", "qty_tolerance_pct")
_POSITIVE_AMOUNT_FIELDS = ("auto_approve_below", "dual_approval_above")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PolicyViolation(message)


def _as_decimal(rules: Mapping[str, Any], field: str) -> Decimal:
    _require(field in rules, f"tolerance policy rules missing required field {field!r}")
    value = rules[field]
    _require(
        isinstance(value, int | float | str | Decimal) and not isinstance(value, bool),
        f"tolerance policy field {field!r} must be numeric, got {type(value).__name__}",
    )
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise PolicyViolation(
            f"tolerance policy field {field!r} is not a valid number: {value!r}"
        ) from exc


def _as_int(rules: Mapping[str, Any], field: str) -> int:
    _require(field in rules, f"tolerance policy rules missing required field {field!r}")
    value = rules[field]
    _require(
        isinstance(value, int) and not isinstance(value, bool),
        f"tolerance policy field {field!r} must be an integer, got {type(value).__name__}",
    )
    return int(value)


def load_tolerance_policy(
    *,
    id: uuid.UUID,
    tenant_id: uuid.UUID,
    name: str,
    is_active: bool,
    version: int,
    rules: Mapping[str, Any],
    created_at: datetime,
) -> TolerancePolicy:
    """Parse and validate one tolerance_policies row into a TolerancePolicy.

    Raises PolicyViolation for anything invalid -- a bad policy is caught
    here, before it is ever handed to the matching engine, never mid-match.
    """
    _require(version > 0, f"tolerance policy version must be positive, got {version}")

    percentages = {field: _as_decimal(rules, field) for field in _PERCENTAGE_FIELDS}
    for field, value in percentages.items():
        _require(
            Decimal("0") <= value <= Decimal("100"),
            f"tolerance policy field {field!r} must be a percentage between 0 and 100, "
            f"got {value}",
        )

    amounts = {field: _as_decimal(rules, field) for field in _POSITIVE_AMOUNT_FIELDS}
    for field, value in amounts.items():
        _require(
            value > 0,
            f"tolerance policy field {field!r} must be a positive amount, got {value}",
        )
    auto_approve_below = amounts["auto_approve_below"]
    dual_approval_above = amounts["dual_approval_above"]
    _require(
        dual_approval_above > auto_approve_below,
        f"tolerance policy dual_approval_above ({dual_approval_above}) must be greater "
        f"than auto_approve_below ({auto_approve_below})",
    )

    min_field_confidence = _as_decimal(rules, "min_field_confidence")
    _require(
        Decimal("0") <= min_field_confidence <= Decimal("1"),
        f"tolerance policy min_field_confidence must be between 0 and 1, got "
        f"{min_field_confidence}",
    )

    duplicate_window_days = _as_int(rules, "duplicate_window_days")
    _require(
        duplicate_window_days >= 0,
        f"tolerance policy duplicate_window_days must not be negative, "
        f"got {duplicate_window_days}",
    )

    try:
        return TolerancePolicy(
            id=id,
            tenant_id=tenant_id,
            name=name,
            is_active=is_active,
            version=version,
            price_variance_pct=percentages["price_variance_pct"],
            qty_tolerance_pct=percentages["qty_tolerance_pct"],
            auto_approve_below=auto_approve_below,
            dual_approval_above=dual_approval_above,
            min_field_confidence=min_field_confidence,
            duplicate_window_days=duplicate_window_days,
            created_at=created_at,
        )
    except TridentOracleError as exc:
        raise PolicyViolation(str(exc)) from exc
