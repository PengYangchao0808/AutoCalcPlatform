"""Planning reservations: the pre-proposal model budget (M1-04c / D09 + R08).

The first research round spends model budget **before** a Proposal, an approval
or a TaskRef exists.  Charging that spend to ``project + proposal_ref`` is
impossible (there is no proposal yet), so M1-04c introduces one additive budget
identity:

``planning budget_id = project + "planning:<research>:<session>"``

The identity itself is not re-implemented here: :func:`acp_services.runtime.
research_session.planning_claim` owns the claim id
(``plan|<project>|<research>|<session>|<round>|<call_seq>``), the request digest
and the ``modelrcpt|…`` receipt id.  This module only *uses* that claim to:

* define and reserve the planning budget on the existing
  :class:`~acp_services.runtime.admission.ReservationLedger` (no new ledger),
* derive a non-empty, server-side reservation id and keep it addressable by the
  claim (a retry with the same claim replays the same reservation),
* never fabricate a job or an attempt (``task_ref``/``run_id`` stay empty),
* keep an unknown/interrupted call **reserved** — a missing receipt is never a
  refund and never resets an allowance,
* reconcile the planning ledger with the execution ledger once the proposal
  exists (``research``/``session`` association, still no new ledger).

Settlement authority: the Controller-side model gateway (B, M3-03) is the
trusted producer of a planning receipt, so settlement uses the internal
``controller-terminal-receipt`` path.  A C-owned proxy receipt is *external* and
must go through the external settlement path when that gateway lands; until then
a planning call can only be reserved or retained, never silently settled or
released.

Isolation: stdlib + first-party ``acp_services`` only (no ``acp`` import).

Author: ACP Services (B lane, M1-04c).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from acp_services.runtime.admission import ReservationLedger
from acp_services.runtime.canonical_digest import canonical_digest
from acp_services.runtime.research_budget import (
    BudgetDecision,
    BudgetPolicy,
    BudgetReservation,
    budget_id_for,
)
from acp_services.runtime.research_session import PlanningClaim
from acp_services.runtime.usage_units import UsageUnitCounts, normalize_usage_unit

__all__ = [
    "PLANNING_BUDGET_PREFIX",
    "PLANNING_RESERVATION_SCHEMA",
    "PLANNING_RESERVATION_STATE_RESERVED",
    "PLANNING_RESERVATION_STATE_SETTLED",
    "PLANNING_RESERVATION_STATE_RETAINED",
    "PlanningReservation",
    "PlanningReservationError",
    "PlanningReservationOutcome",
    "PlanningRetention",
    "PlanningReconciliation",
    "TwoPhaseLedgerLink",
    "link_execution_budget",
    "planning_budget_policy",
    "planning_reservation_id",
    "reconcile_planning",
    "reserve_planning_call",
    "retain_unknown_planning_call",
    "settle_planning_call",
]

#: Budget-id segment for the pre-proposal phase (project + this prefix).
PLANNING_BUDGET_PREFIX = "planning:"

PLANNING_RESERVATION_SCHEMA = "acp2.runtime.planning-reservation.v1"

PLANNING_RESERVATION_STATE_RESERVED = "reserved"
PLANNING_RESERVATION_STATE_SETTLED = "settled"
PLANNING_RESERVATION_STATE_RETAINED = "retained-unknown"


class PlanningReservationError(ValueError):
    """A planning reservation violates the frozen identity rule (typed)."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule
        self.message = message

    def detail(self) -> dict[str, Any]:
        return {"code": "input_invalid", "message": self.message, "params": {"rule": self.rule}}


@dataclass(frozen=True, slots=True)
class PlanningReservation:
    """One planning reservation (never a job, never an attempt)."""

    claim_id: str
    budget_id: str
    reservation_id: str
    request_digest: str
    model_receipt_id: str
    project_id: str
    research_id: str
    session_id: str
    round: int
    call_seq: int
    unit: str = "model_request"
    units: int = 1
    state: str = PLANNING_RESERVATION_STATE_RESERVED
    job_id: str = ""
    attempt: int = 0
    detail: str = ""

    def __post_init__(self) -> None:
        for label, value in (
            ("claim_id", self.claim_id),
            ("budget_id", self.budget_id),
            ("reservation_id", self.reservation_id),
        ):
            if not str(value or "").strip():
                raise PlanningReservationError("missing_identity", f"{label} is required")
        if self.job_id or self.attempt:
            raise PlanningReservationError(
                "fabricated_execution_fact",
                "a planning reservation is not a job/attempt; job_id and attempt must stay empty",
            )
        if self.units < 1:
            raise PlanningReservationError("invalid_units", "units must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PLANNING_RESERVATION_SCHEMA,
            "claim_id": self.claim_id,
            "budget_id": self.budget_id,
            "reservation_id": self.reservation_id,
            "request_digest": self.request_digest,
            "model_receipt_id": self.model_receipt_id,
            "project_id": self.project_id,
            "research_id": self.research_id,
            "session_id": self.session_id,
            "round": self.round,
            "call_seq": self.call_seq,
            "unit": self.unit,
            "units": self.units,
            "state": self.state,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "detail": self.detail,
            "is_execution_fact": False,
        }


@dataclass(frozen=True, slots=True)
class PlanningReservationOutcome:
    """Result of reserving one planning call."""

    admitted: bool
    reservation: PlanningReservation
    decision: BudgetDecision

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "reservation": self.reservation.to_dict(),
            "decision": self.decision.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PlanningRetention:
    """An unknown/interrupted planning call kept reserved (never refunded)."""

    reservation_id: str
    claim_id: str
    reason: str
    released: bool = False
    state: str = PLANNING_RESERVATION_STATE_RETAINED

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "claim_id": self.claim_id,
            "reason": self.reason,
            "released": self.released,
            "state": self.state,
            "rule": "unknown keeps the charge; a receipt is required to release",
        }


@dataclass(frozen=True, slots=True)
class PlanningReconciliation:
    """Planning-vs-execution ledger reconciliation for one research session."""

    planning_budget_id: str
    reservations: tuple[PlanningReservation, ...] = ()
    settled: int = 0
    reserved: int = 0
    released: int = 0
    execution_budget_ids: tuple[str, ...] = ()

    @property
    def outstanding(self) -> int:
        return self.reserved

    def to_dict(self) -> dict[str, Any]:
        return {
            "planning_budget_id": self.planning_budget_id,
            "settled": self.settled,
            "reserved": self.reserved,
            "released": self.released,
            "outstanding": self.outstanding,
            "execution_budget_ids": list(self.execution_budget_ids),
            "reservations": [item.to_dict() for item in self.reservations],
            "new_ledger_created": False,
        }


@dataclass(frozen=True, slots=True)
class TwoPhaseLedgerLink:
    """The research/session association between the planning and execution books."""

    project_id: str
    research_id: str
    session_id: str
    planning_claim_id: str
    planning_budget_id: str
    execution_budget_id: str
    approval_ref: str = ""

    def __post_init__(self) -> None:
        for label, value in (
            ("planning_claim_id", self.planning_claim_id),
            ("planning_budget_id", self.planning_budget_id),
            ("execution_budget_id", self.execution_budget_id),
        ):
            if not str(value or "").strip():
                raise PlanningReservationError("missing_identity", f"{label} is required")
        if self.planning_budget_id == self.execution_budget_id:
            raise PlanningReservationError(
                "collapsed_budget_identity",
                "the planning and execution budgets are distinct identities and never merge",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "research_id": self.research_id,
            "session_id": self.session_id,
            "planning_claim_id": self.planning_claim_id,
            "planning_budget_id": self.planning_budget_id,
            "execution_budget_id": self.execution_budget_id,
            "approval_ref": self.approval_ref,
            "association": "research+session (both books stay separate)",
            "new_ledger_created": False,
        }


def _require_claim(claim: PlanningClaim) -> PlanningClaim:
    if not isinstance(claim, PlanningClaim):
        raise PlanningReservationError("invalid_claim", "a PlanningClaim is required")
    return claim


def planning_reservation_id(claim: PlanningClaim) -> str:
    """Derive the stable reservation id of one planning claim (never empty)."""
    _require_claim(claim)
    digest = canonical_digest(
        {
            "claim_id": claim.claim_id,
            "request_digest": claim.request_digest,
            "budget_id": claim.budget_id,
            "unit": claim.unit,
        }
    )
    return "bres_plan_" + digest.split(":", 1)[1][:32]


def planning_budget_policy(
    claim: PlanningClaim,
    *,
    model_requests: int = 1,
    approved_model_requests: int | None = None,
) -> BudgetPolicy:
    """Build the planning budget policy for one research session.

    The identity is ``project + "planning:<research>:<session>"`` — deliberately
    not ``proposal_ref`` (there is no proposal yet) and not ``run_id``.
    """
    _require_claim(claim)
    if model_requests < 0:
        raise PlanningReservationError("invalid_units", "model_requests must be non-negative")
    approved = model_requests if approved_model_requests is None else approved_model_requests
    return BudgetPolicy(
        budget_id=claim.budget_id,
        limits={"model_request": int(model_requests)},
        approved_limits={"model_request": int(approved)},
        approval_ref="",
        project_id=claim.project_id,
    )


def reserve_planning_call(
    ledger: ReservationLedger,
    claim: PlanningClaim,
    *,
    policy: BudgetPolicy | None = None,
    units: int = 1,
) -> PlanningReservationOutcome:
    """Define (idempotently) and reserve one planning model call.

    Args:
        ledger: The single durable reservation ledger (no second ledger).
        claim: The server-derived planning claim of this model call.
        policy: An explicit planning policy; defaults to one model request.
        units: Reservation size (normally 1).

    Returns:
        The outcome.  A replayed claim returns the *same* reservation id
        (``already_reserved``), so a retry never double-charges.

    Raises:
        PlanningReservationError: The claim or units are invalid.
    """
    _require_claim(claim)
    if not isinstance(ledger, ReservationLedger):
        raise PlanningReservationError(
            "invalid_ledger", "planning reservations require the durable ReservationLedger"
        )
    count = int(units)
    if count < 1:
        raise PlanningReservationError("invalid_units", "units must be >= 1")
    planning_policy = policy or planning_budget_policy(claim, model_requests=max(1, count))
    if planning_policy.budget_id != claim.budget_id:
        raise PlanningReservationError(
            "budget_identity_mismatch",
            f"policy budget {planning_policy.budget_id!r} != claim budget {claim.budget_id!r}",
        )
    ledger.define_research_budget(planning_policy)
    unit = normalize_usage_unit(claim.unit)
    reservation_id = planning_reservation_id(claim)
    decision = ledger.reserve_research_budget(
        claim.budget_id,
        {unit: count},
        reservation_id=reservation_id,
        # Deliberately empty: a planning call is not a job/attempt.
        task_ref="",
        run_id="",
        claim_id=claim.claim_id,
        detail="planning model request before any proposal exists",
    )
    return PlanningReservationOutcome(
        admitted=bool(decision.admitted),
        reservation=PlanningReservation(
            claim_id=claim.claim_id,
            budget_id=claim.budget_id,
            reservation_id=str(decision.reservation_id or reservation_id),
            request_digest=claim.request_digest,
            model_receipt_id=claim.model_receipt_id,
            project_id=claim.project_id,
            research_id=claim.research_id,
            session_id=claim.session_id,
            round=claim.round,
            call_seq=claim.call_seq,
            unit=unit,
            units=count,
            detail=str(decision.detail or ""),
        ),
        decision=decision,
    )


def settle_planning_call(
    ledger: ReservationLedger,
    reservation: PlanningReservation,
    *,
    receipt_id: str,
    counters: UsageUnitCounts | None = None,
) -> Any:
    """Settle one planning reservation from the Controller-side model gateway.

    The gateway (B, M3-03) runs inside the Controller, so its receipt is the
    internal ``controller-terminal-receipt`` source.  A C-owned proxy receipt is
    *external* and must use the external settlement path once it exists; passing
    it here would misrepresent the authority and is refused by the ledger.

    Returns:
        The :class:`~acp_services.runtime.research_budget.BudgetSettlement`.
    """
    if not isinstance(reservation, PlanningReservation):
        raise PlanningReservationError("invalid_reservation", "a PlanningReservation is required")
    if not str(receipt_id or "").strip():
        raise PlanningReservationError("missing_receipt", "a settlement needs a receipt id")
    return ledger.settle_research_budget(
        reservation.budget_id,
        receipt_id=str(receipt_id),
        reservation_id=reservation.reservation_id,
        counters=counters or UsageUnitCounts(model_request=reservation.units),
        # Not a job/attempt: the receipt names the planning claim, not a task.
        job_id="",
        attempt=0,
        run_id="",
    )


def retain_unknown_planning_call(
    reservation: PlanningReservation,
    *,
    reason: str,
) -> PlanningRetention:
    """Keep an unknown/interrupted planning call charged.

    An unknown outcome (stream interrupted, receipt lost, gateway timeout) is
    **not** evidence that the provider was not called.  Releasing the reservation
    would reset the allowance and allow the same spend twice, so this function
    performs no release at all; the caller keeps the reservation and reconciles it
    against the gateway ledger.
    """
    if not isinstance(reservation, PlanningReservation):
        raise PlanningReservationError("invalid_reservation", "a PlanningReservation is required")
    return PlanningRetention(
        reservation_id=reservation.reservation_id,
        claim_id=reservation.claim_id,
        reason=str(reason or "unknown"),
    )


def reconcile_planning(
    ledger: ReservationLedger,
    budget_id: str,
) -> PlanningReconciliation:
    """Read back the planning book (no writes, no release, no new ledger)."""
    projection = ledger.research_budget_projection(str(budget_id))
    if projection is None:
        raise PlanningReservationError("unknown_budget", f"no planning budget {budget_id!r} exists")
    reservations: list[PlanningReservation] = []
    settled = reserved = released = 0
    for item in projection.reservations:
        reservations.append(_reservation_from_row(item))
        if str(item.state) == "settled":
            settled += 1
        elif str(item.state) == "released":
            released += 1
        else:
            reserved += 1
    return PlanningReconciliation(
        planning_budget_id=str(budget_id),
        reservations=tuple(reservations),
        settled=settled,
        reserved=reserved,
        released=released,
    )


def _reservation_from_row(item: BudgetReservation) -> PlanningReservation:
    """Project a ledger row onto the planning value object.

    A row written by :func:`reserve_planning_call` always carries a claim id of
    the frozen form ``plan|<project>|<research>|<session>|<round>|<call_seq>``.
    """
    claim_id = str(item.claim_id or "")
    segments = claim_id.split("|") if claim_id else []
    parsed = len(segments) == 6
    return PlanningReservation(
        claim_id=claim_id,
        budget_id=str(item.budget_id),
        reservation_id=str(item.reservation_id),
        request_digest="",
        model_receipt_id="",
        project_id=segments[1] if parsed else "",
        research_id=segments[2] if parsed else "",
        session_id=segments[3] if parsed else "",
        round=int(segments[4]) if parsed and segments[4].isdigit() else 0,
        call_seq=int(segments[5]) if parsed and segments[5].isdigit() else 1,
        units=int(sum(item.units.values()) or 1),
        state=str(item.state),
        detail=str(item.detail or ""),
    )


def link_execution_budget(
    claim: PlanningClaim,
    *,
    execution_budget_id: str,
    approval_ref: str = "",
) -> TwoPhaseLedgerLink:
    """Associate the planning book with the execution book (research/session).

    Once the science proposal exists, execution is approved and charged on the
    existing ``project + proposal_ref`` budget.  The association is by
    ``research``/``session``; neither book is merged and no job/attempt is
    invented for the planning phase.
    """
    _require_claim(claim)
    if not str(execution_budget_id or "").strip():
        raise PlanningReservationError(
            "missing_identity", "execution_budget_id is required for the second phase"
        )
    return TwoPhaseLedgerLink(
        project_id=claim.project_id,
        research_id=claim.research_id,
        session_id=claim.session_id,
        planning_claim_id=claim.claim_id,
        planning_budget_id=claim.budget_id,
        execution_budget_id=str(execution_budget_id),
        approval_ref=str(approval_ref),
    )


def planning_budget_id_for(project_id: str, research_id: str, session_id: str) -> str:
    """Public helper: the planning budget id of one research session."""
    return budget_id_for(project_id, f"{PLANNING_BUDGET_PREFIX}{research_id}:{session_id}")
