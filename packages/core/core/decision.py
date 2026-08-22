"""decide() -- the approval-routing decision matrix, docs/ARCHITECTURE.md's
decision layer sitting on top of the three-way match (core/matching/three_way.py).
Pure function, no I/O: given a match result, an extraction confidence, the
invoice, and the active policy, returns a Decision -- never touches a DB,
never sends a notification itself (the worker's 'match' handler does that,
based on what comes back here).

The matrix (rows: match outcome: severity of the worst finding present;
columns: whether every extracted field cleared policy.min_field_confidence):

                       | all fields >= min_conf   | any field < min_conf
    -------------------|--------------------------|----------------------
    no exceptions      | total < auto_approve:     | NEEDS_VERIFICATION
                        |   AUTO_POST               |
                        | else PENDING_APPROVAL     |
    -------------------|--------------------------|----------------------
    warn only          | PENDING_APPROVAL          | NEEDS_VERIFICATION
    -------------------|--------------------------|----------------------
    any block          | PENDING_APPROVAL          | NEEDS_VERIFICATION
                        | (2 approvers if total >   |
                        |  dual_approval_above)     |

Low extraction confidence ALWAYS overrides a clean match -- checked first,
before the match result, in every branch below. If the model isn't sure what
it read, a clean match is meaningless: "zero exceptions" just means nothing
looked wrong to a comparison run against fields that might themselves be
misread. A clean-looking match built on an uncertain extraction isn't
evidence of anything; it needs a human to confirm the fields are even right
before the match result can be trusted at all.

The dual-approval-count check (total > policy.dual_approval_above -> 2
approvers) is applied uniformly to every PENDING_APPROVAL outcome, not only
the "any block" row the table above calls it out on -- a large clean invoice
sitting above the auto-approve threshold, or a large invoice with only
warn-level findings, is exactly as financially risky as a large invoice with
a blocking exception, and the sign-off requirement should track the money at
stake, not which row of the matrix produced PENDING_APPROVAL.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from core.errors import MatchingError
from core.matching.three_way import ThreeWayMatchResult
from core.models import Invoice, Severity, TolerancePolicy

DecisionOutcome = Literal["AUTO_POST", "PENDING_APPROVAL", "NEEDS_VERIFICATION"]


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise MatchingError(message)


@dataclass(frozen=True, slots=True)
class Decision:
    outcome: DecisionOutcome
    reason: str
    required_approvers: int

    def __post_init__(self) -> None:
        _check(
            self.outcome in ("AUTO_POST", "PENDING_APPROVAL", "NEEDS_VERIFICATION"),
            f"Decision.outcome {self.outcome!r} is not recognized",
        )
        _check(bool(self.reason.strip()), "Decision.reason must not be blank")
        _check(self.required_approvers >= 0, "Decision.required_approvers must not be negative")
        if self.outcome != "PENDING_APPROVAL":
            _check(
                self.required_approvers == 0,
                f"Decision.outcome {self.outcome!r} must have zero required_approvers -- only "
                "PENDING_APPROVAL routes to a human approver",
            )


def _required_approvers(total: Decimal, policy: TolerancePolicy) -> int:
    return 2 if total > policy.dual_approval_above else 1


def decide(
    match_result: ThreeWayMatchResult,
    extraction_confidence: Decimal | None,
    invoice: Invoice,
    policy: TolerancePolicy,
) -> Decision:
    if invoice.total is None:
        raise MatchingError("decide() requires invoice.total")
    total = invoice.total

    if extraction_confidence is None or extraction_confidence < policy.min_field_confidence:
        conf_desc = (
            "no field confidence was recorded"
            if extraction_confidence is None
            else f"min field confidence {extraction_confidence:.2f}"
        )
        return Decision(
            outcome="NEEDS_VERIFICATION",
            reason=(
                f"Needs verification: {conf_desc}, below the "
                f"{policy.min_field_confidence:.2f} threshold required to trust extracted "
                "fields -- a clean match on an uncertain read isn't trustworthy."
            ),
            required_approvers=0,
        )

    # From here, extraction_confidence is not None (mypy narrows via the
    # `is None or ...` check above returning in every case it could be None).
    approvers = _required_approvers(total, policy)

    if match_result.result == "clean":
        if total < policy.auto_approve_below:
            return Decision(
                outcome="AUTO_POST",
                reason=(
                    f"Auto-posted: 0 exceptions, min field confidence "
                    f"{extraction_confidence:.2f}, total {invoice.currency} {total} below "
                    f"{invoice.currency} {policy.auto_approve_below} threshold."
                ),
                required_approvers=0,
            )
        return Decision(
            outcome="PENDING_APPROVAL",
            reason=(
                f"Pending approval: 0 exceptions, min field confidence "
                f"{extraction_confidence:.2f}, total {invoice.currency} {total} at or above "
                f"the {invoice.currency} {policy.auto_approve_below} auto-approve threshold "
                f"({approvers} approver(s) required)."
            ),
            required_approvers=approvers,
        )

    has_block = any(f.severity == Severity.BLOCK for f in match_result.findings)
    kind = "blocking" if has_block else "warning-level"
    dual_note = (
        f", total {invoice.currency} {total} exceeds the {invoice.currency} "
        f"{policy.dual_approval_above} dual-approval threshold"
        if approvers == 2
        else ""
    )
    return Decision(
        outcome="PENDING_APPROVAL",
        reason=(
            f"Pending approval: {len(match_result.findings)} {kind} exception(s), min field "
            f"confidence {extraction_confidence:.2f} ({approvers} approver(s) required{dual_note})."
        ),
        required_approvers=approvers,
    )
