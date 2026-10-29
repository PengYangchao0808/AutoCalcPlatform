"""Persistent research/task budget contracts (B17).

The durable ledger lives on the existing
:class:`acp_services.runtime.admission.ReservationLedger` (same SQLite database,
same short ``BEGIN IMMEDIATE`` transactions).  This module owns only the value
objects and the frozen policy vocabulary so the ledger, the Controller submit
path and the acceptance harness share one interpretation.

Budget identity is ``project + proposal_ref`` (a research round/batch).  It is
deliberately independent of ``run_id``: a retry, restart or replay inherits the
remaining batch budget instead of creating a fresh one.  The server derives it
from the B16 approval record; a client cannot submit an arbitrary scope.

No prices
---------

These are execution/usage facts.  There is no price, rate, credit, balance or
account vocabulary here, and none may enter ``TaskRequest`` or a scientific
result.  Model-token enforcement is ``NOT_VERIFIED``: the model-call proxy is
C-owned (``acp2_experience_budget_proxy.py``, C T-13) and B only records trusted
usage.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from acp_services.runtime.usage_units import (
    USAGE_UNIT_FIELDS,
    UsageUnitCounts,
    UsageUnitValidationError,
    normalize_usage_unit,
)

__all__ = [
    "BudgetUnit",
    "BudgetReason",
    "BudgetState",
    "BudgetCapability",
    "BudgetPolicy",
    "BudgetReservation",
    "BudgetDecision",
    "BudgetSettlement",
    "BudgetProjection",
    "BudgetRelease",
    "BudgetBoundary",
    "BudgetConfigurationError",
    "TIMEOUT_KEYS",
    "DEFAULT_TIMEOUTS",
    "UNIT_CAPABILITIES",
    "UNIT_CONSUMPTION_RULES",
    "RECEIPT_CONSUMED_UNITS",
    "RELEASE_RULES",
    "RESEARCH_BUDGET_NOTES",
    "budget_id_for",
    "parse_budget_policy",
    "evaluate_budget_boundary",
    "release_statement",
]


class BudgetCapability(str):
    """What the platform truthfully enforces for one unit."""

    ENFORCED = "enforced"
    RECORDED_ONLY = "recorded-only"
    NOT_VERIFIED = "NOT_VERIFIED"


class BudgetUnit(str):
    """Separate units the platform counts (never summed across unit names)."""

    TASK_REF = "task_ref"
    RECIPE_STEP = "recipe_step"
    QC_INVOCATION = "qc_invocation"
    ATTEMPT = "attempt"
    BSUB = "bsub"
    MODEL_REQUEST = "model_request"
    ALL = frozenset(USAGE_UNIT_FIELDS)

    @classmethod
    def normalize(cls, value: Any) -> str:
        return normalize_usage_unit(value)


class BudgetReason(str):
    """Structured budget verdict vocabulary."""

    ADMITTED = "budget_admitted"
    EXHAUSTED = "budget_exhausted"
    NOT_CONFIGURED = "budget_not_configured"
    UNKNOWN_UNIT = "budget_unit_unknown"
    INVALID = "budget_invalid"
    EXCEEDS_APPROVED = "budget_exceeds_approved_range"
    RESERVATION_NOT_FOUND = "budget_reservation_not_found"
    RESERVATION_RELEASED = "budget_reservation_released"
    SETTLED = "budget_settled"
    DUPLICATE = "budget_receipt_duplicate"
    RELEASED = "budget_released"
    HELD = "budget_held"

    ALL = frozenset(
        {
            ADMITTED,
            EXHAUSTED,
            NOT_CONFIGURED,
            UNKNOWN_UNIT,
            INVALID,
            EXCEEDS_APPROVED,
            RESERVATION_NOT_FOUND,
            RESERVATION_RELEASED,
            SETTLED,
            DUPLICATE,
            RELEASED,
            HELD,
        }
    )


class BudgetState(str):
    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    SETTLED = "settled"
    RELEASED = "released"
    ACTIVE = frozenset({RESERVED, DISPATCHED, SETTLED})


class BudgetConfigurationError(ValueError):
    """A budget definition is invalid or exceeds the approved range."""


UNIT_CAPABILITIES: dict[str, str] = {
    BudgetUnit.TASK_REF: BudgetCapability.ENFORCED,
    BudgetUnit.ATTEMPT: BudgetCapability.ENFORCED,
    BudgetUnit.RECIPE_STEP: BudgetCapability.RECORDED_ONLY,
    BudgetUnit.QC_INVOCATION: BudgetCapability.RECORDED_ONLY,
    BudgetUnit.BSUB: BudgetCapability.RECORDED_ONLY,
    BudgetUnit.MODEL_REQUEST: BudgetCapability.RECORDED_ONLY,
}

#: Units added to the consumed projection from trusted receipts.  TaskRef and
#: attempt are reserved before dispatch, so a receipt copy is evidence only and
#: must not be charged twice.
RECEIPT_CONSUMED_UNITS: frozenset[str] = frozenset(
    {
        BudgetUnit.RECIPE_STEP,
        BudgetUnit.QC_INVOCATION,
        BudgetUnit.BSUB,
        BudgetUnit.MODEL_REQUEST,
    }
)

UNIT_CONSUMPTION_RULES: dict[str, dict[str, str]] = {
    BudgetUnit.TASK_REF: {
        "consumed_at": "accepted submit, before any work directory",
        "failure": "consumes",
        "replay": "does not consume a second TaskRef",
        "recovery": "does not consume a new TaskRef",
    },
    BudgetUnit.RECIPE_STEP: {
        "consumed_at": "trusted receipt for a dispatched step",
        "failure": "consumes when the step was invoked",
        "replay": "does not re-consume the same receipt",
        "recovery": "does not re-consume a completed step",
    },
    BudgetUnit.QC_INVOCATION: {
        "consumed_at": "trusted receipt per QC process invocation",
        "failure": "consumes when the process was invoked",
        "replay": "does not re-consume the same receipt",
        "recovery": "does not re-invoke a completed step",
    },
    BudgetUnit.ATTEMPT: {
        "consumed_at": "accepted submit/dispatch of an attempt",
        "failure": "consumes the failed attempt",
        "replay": "does not consume a second attempt",
        "recovery": "same attempt recovery does not consume; continue/rerun is a new attempt",
    },
    BudgetUnit.BSUB: {
        "consumed_at": "trusted receipt per real bsub",
        "failure": "consumes when bsub was invoked",
        "replay": "does not re-consume the same receipt",
        "recovery": "does not re-issue bsub",
    },
    BudgetUnit.MODEL_REQUEST: {
        "consumed_at": "trusted receipt from the C-owned model proxy",
        "failure": "recorded when the trusted source reports it",
        "replay": "does not re-consume the same receipt",
        "recovery": "does not re-request tokens",
    },
}

PUBLICATION_FAILURE_DOES_NOT_RECHARGE_NOTE = (
    "A publication failure or retry never re-charges computation: usage is "
    "ingested once from the trusted receipt and publication is an independent step."
)

RELEASE_RULES: dict[str, dict[str, object]] = {
    "submit_failed": {
        "release_reserved_units": True,
        "recharges_computation": False,
        "detail": "the claim never dispatched; its queued reservation is released",
    },
    "cancel_before_dispatch": {
        "release_reserved_units": True,
        "recharges_computation": False,
        "detail": "a queued reservation released before any QC invocation is refunded",
    },
    "cancel_after_dispatch": {
        "release_reserved_units": False,
        "recharges_computation": False,
        "detail": "science already started; the consumed units stay charged",
    },
    "science_failure": {
        "release_reserved_units": False,
        "recharges_computation": False,
        "detail": "a failed attempt that invoked QC consumes its units; a retry is a new attempt",
    },
    "publication_pending": {
        "release_reserved_units": False,
        "recharges_computation": False,
        "detail": "publication is a separate fact; pending publication keeps computation charged",
    },
    "publication_failure": {
        "release_reserved_units": False,
        "recharges_computation": False,
        "detail": "publication retries only republish; they never re-charge computation",
    },
    "replay": {
        "release_reserved_units": False,
        "recharges_computation": False,
        "detail": "a duplicate receipt/claim replay is idempotent and charges nothing new",
    },
    "recovery": {
        "release_reserved_units": False,
        "recharges_computation": False,
        "detail": "restart recovery replays reservations and receipts; it never resets a budget",
    },
}

TIMEOUT_KEYS: tuple[str, ...] = (
    "queue_wait_s",
    "science_runtime_s",
    "client_request_s",
    "overall_acceptance_s",
)

DEFAULT_TIMEOUTS: dict[str, float] = {
    "queue_wait_s": 0.0,
    "science_runtime_s": 1800.0,
    "client_request_s": 60.0,
    "overall_acceptance_s": 3600.0,
}

RESEARCH_BUDGET_NOTES: dict[str, str] = {
    "identity": (
        "budget identity is project + proposal_ref (research round/batch); it is "
        "independent of run_id, so retries and restarts inherit the remaining batch"
    ),
    "settlement": (
        "settlement comes from receipts/trusted usage; a duplicate receipt/event "
        "cannot charge twice because its deterministic receipt id is unique"
    ),
    "publication": PUBLICATION_FAILURE_DOES_NOT_RECHARGE_NOTE,
    "model_tokens": (
        "model-token enforcement is NOT_VERIFIED; the model-call proxy is C-owned "
        "and B neither proxies tokens nor invents unified billing"
    ),
    "science": (
        "budget/balance state never changes scientific behavior, TaskRequest or an algorithm result"
    ),
}


def budget_id_for(project_id: str, proposal_ref: str) -> str:
    """Stable research-round/batch budget id (never derived from ``run_id``)."""
    key = f"{str(project_id).strip()}\x00{str(proposal_ref).strip()}".encode()
    return "rb_" + hashlib.sha256(key).hexdigest()[:24]


def _coerce_limits(payload: Mapping[str, Any] | None) -> dict[str, int]:
    limits: dict[str, int] = {}
    for raw_unit, raw_value in (payload or {}).items():
        try:
            unit = BudgetUnit.normalize(raw_unit)
        except UsageUnitValidationError as exc:
            # M2-03c: a bare unit-counter error must not escape as a 500.  The
            # budget layer owns one typed configuration error.
            raise BudgetConfigurationError(f"unknown budget unit {raw_unit!r}") from exc
        try:
            amount = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise BudgetConfigurationError(f"{unit} limit must be an integer") from exc
        if amount < 0:
            raise BudgetConfigurationError(f"{unit} limit must be non-negative")
        limits[unit] = amount
    return limits


@dataclass(frozen=True)
class BudgetPolicy:
    """The frozen limits and separate timeouts for one research budget."""

    budget_id: str
    limits: Mapping[str, int] = field(default_factory=dict)
    approved_limits: Mapping[str, int] = field(default_factory=dict)
    timeouts: Mapping[str, float] = field(default_factory=dict)
    approval_ref: str = ""
    project_id: str = ""
    #: Parse-time projection of the D09 budget shape (``wall_seconds``,
    #: ``queue_seconds``, tokens, core hours).  It is *not* durable policy: the
    #: durable limits stay ``limits``/``timeouts``.  It exists so a caller can
    #: see that a recognized non-unit field was interpreted rather than dropped.
    shape: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.budget_id).strip():
            raise BudgetConfigurationError("budget_id is required")
        object.__setattr__(self, "limits", _coerce_limits(self.limits))
        object.__setattr__(self, "approved_limits", _coerce_limits(self.approved_limits))
        for unit, approved in self.approved_limits.items():
            requested = self.limits.get(unit)
            if requested is not None and requested > approved:
                raise BudgetConfigurationError(
                    f"{unit} limit {requested} exceeds the approved range {approved}"
                )
        merged = dict(DEFAULT_TIMEOUTS)
        for key in TIMEOUT_KEYS:
            if key in (self.timeouts or {}):
                merged[key] = float(self.timeouts[key])
        for key, value in merged.items():
            if value < 0:
                raise BudgetConfigurationError(f"{key} must be non-negative")
        object.__setattr__(self, "timeouts", merged)

    def capability(self, unit: str) -> str:
        return UNIT_CAPABILITIES.get(BudgetUnit.normalize(unit), BudgetCapability.RECORDED_ONLY)

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_id": self.budget_id,
            "limits": dict(self.limits),
            "approved_limits": dict(self.approved_limits),
            "timeouts": dict(self.timeouts),
            "approval_ref": self.approval_ref,
            "project_id": self.project_id,
            "unit_capabilities": {unit: self.capability(unit) for unit in sorted(self.limits)},
            "execution_limits": dict(self.shape),
        }


def parse_budget_policy(
    budget: Mapping[str, Any] | None,
    *,
    budget_id: str,
    approved_budget: Mapping[str, Any] | None = None,
    approval_ref: str = "",
    project_id: str = "",
) -> BudgetPolicy:
    """Parse an approval budget into a :class:`BudgetPolicy`.

    Accepts the B16 shape ``{"unit": "task_count", "count": 1}`` and the additive
    ``{"units": {...}}`` / ``{"timeouts": {...}}`` shape.  ``timeout_s`` maps to
    ``science_runtime_s`` for compatibility; the four timeout classes stay
    separate and are never collapsed into one value.

    D09 (M1-04b): the shape is bound by
    :func:`acp_services.runtime.execution_limits.bind_budget_shape` — the single
    implementation — so ``wall_seconds`` / ``queue_seconds`` /
    ``input_tokens`` / ``output_tokens`` / ``core_hours`` are *interpreted*
    (they never silently vanish) and a non-empty unknown key is a typed
    ``BudgetConfigurationError`` instead of "no limits".

    Raises:
        BudgetConfigurationError: A non-empty unknown budget shape, or a value
            that is not a non-negative integer.
    """
    source = dict(budget or {})
    # Validation + D09 interpretation (single implementation; also lazy-imported
    # this way because execution_limits imports the capability vocabulary here).
    from acp_services.runtime.execution_limits import (
        LAUNCH_SHAPE_UNITS,
        bind_launch_budget_shape,
    )

    # M2-03c: a C-shaped Proposal budget is an RPH ``PlanBudget`` (its own field
    # names).  It is mapped by the one budget mapping instead of being rejected
    # as an unknown shape, so an approval can bind the proposal budget as-is.
    source = _plan_budget_to_shape_payload(source)

    launch_limits, shape = bind_launch_budget_shape(source)
    limits: dict[str, int] = {}
    for key in ("units", "unit_limits"):
        raw = source.get(key)
        if isinstance(raw, Mapping):
            limits.update(_coerce_limits(raw))
    if "unit" in source and "count" in source:
        # The v1 launch field also accepts the two *shape* units
        # (``walltime_s``/``core_hours``); they are interpreted by the D09 shape
        # above and are never usage-unit counters (M2-03c).
        if str(source["unit"]) not in LAUNCH_SHAPE_UNITS:
            try:
                limits[BudgetUnit.normalize(str(source["unit"]))] = int(source["count"])
            except UsageUnitValidationError as exc:
                raise BudgetConfigurationError(f"unknown budget unit {source['unit']!r}") from exc
    for unit, value in launch_limits.items():
        limits.setdefault(unit, int(value))

    timeouts: dict[str, float] = {}
    for key in TIMEOUT_KEYS:
        if key in source:
            timeouts[key] = float(source[key])
    if "timeout_s" in source and "science_runtime_s" not in timeouts:
        timeouts["science_runtime_s"] = float(source["timeout_s"])
    raw_timeouts = source.get("timeouts")
    if isinstance(raw_timeouts, Mapping):
        for key in TIMEOUT_KEYS:
            if key in raw_timeouts:
                timeouts[key] = float(raw_timeouts[key])

    approved_limits: dict[str, int] = {}
    if approved_budget is not None:
        approved_limits = parse_budget_policy(
            approved_budget,
            budget_id=budget_id,
        ).limits

    return BudgetPolicy(
        budget_id=budget_id,
        limits=limits,
        approved_limits=approved_limits,
        timeouts=timeouts,
        approval_ref=str(approval_ref),
        project_id=str(project_id),
        shape=shape.to_dict() if shape.bounded or shape.recorded_only else {},
    )


@dataclass(frozen=True)
class BudgetReservation:
    """One durable reservation of separate units for a task/batch."""

    reservation_id: str
    budget_id: str
    units: Mapping[str, int]
    state: str = BudgetState.RESERVED
    task_ref: str = ""
    run_id: str = ""
    claim_id: str = ""
    admission_reservation_id: str = ""
    detail: str = ""
    created_at: str = ""
    updated_at: str = ""
    #: Execution identity persisted at settlement (M0-07) — attempt, the
    #: receipt that settled it and its chain summary.  Empty before settlement.
    attempt: int = 0
    receipt_id: str = ""
    chain_digest: str = ""
    release_id: str = ""
    plan_identity: str = ""

    @property
    def consumed(self) -> dict[str, int]:
        return dict(self.units) if self.state in BudgetState.ACTIVE else {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "budget_id": self.budget_id,
            "units": dict(self.units),
            "state": self.state,
            "task_ref": self.task_ref,
            "run_id": self.run_id,
            "claim_id": self.claim_id,
            "admission_reservation_id": self.admission_reservation_id,
            "detail": self.detail,
            "attempt": self.attempt,
            "receipt_id": self.receipt_id,
            "chain_digest": self.chain_digest,
            "release_id": self.release_id,
            "plan_identity": self.plan_identity,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class BudgetDecision:
    """A pre-dispatch budget verdict (structured, never a silent pass)."""

    admitted: bool
    reason_code: str
    budget_id: str = ""
    detail: str = ""
    reservation_id: str | None = None
    exhausted_unit: str | None = None
    requested: Mapping[str, int] = field(default_factory=dict)
    remaining: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "reason_code": self.reason_code,
            "budget_id": self.budget_id,
            "detail": self.detail,
            "reservation_id": self.reservation_id,
            "exhausted_unit": self.exhausted_unit,
            "requested": dict(self.requested),
            "remaining": dict(self.remaining),
        }


@dataclass(frozen=True)
class BudgetSettlement:
    """Settlement from one trusted receipt (idempotent by receipt id)."""

    applied: bool
    duplicate: bool
    reason_code: str
    budget_id: str
    reservation_id: str
    receipt_id: str
    counters: UsageUnitCounts = field(default_factory=UsageUnitCounts)
    consumed: Mapping[str, int] = field(default_factory=dict)
    remaining: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "duplicate": self.duplicate,
            "reason_code": self.reason_code,
            "budget_id": self.budget_id,
            "reservation_id": self.reservation_id,
            "receipt_id": self.receipt_id,
            "counters": self.counters.to_dict(),
            "consumed": dict(self.consumed),
            "remaining": dict(self.remaining),
        }


@dataclass(frozen=True)
class BudgetRelease:
    """Outcome of a failure/cancel/publication release decision."""

    released: bool
    reason_code: str
    rule: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "released": self.released,
            "reason_code": self.reason_code,
            "rule": self.rule,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class BudgetProjection:
    """Server-side quote/remaining/consumed projection for one budget."""

    policy: BudgetPolicy
    consumed: Mapping[str, int] = field(default_factory=dict)
    remaining: Mapping[str, int] = field(default_factory=dict)
    reservations: tuple[BudgetReservation, ...] = ()
    receipts: int = 0
    receipt_counters: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.policy.to_dict(),
            "consumed": dict(self.consumed),
            "remaining": dict(self.remaining),
            "reservations": [item.to_dict() for item in self.reservations],
            "receipts": self.receipts,
            "receipt_counters": dict(self.receipt_counters),
            "release_statement": release_statement(),
        }


@dataclass(frozen=True)
class BudgetBoundary:
    """Boundary verdict for an acceptance/budget line."""

    status: str
    at_cap: bool
    over_cap: Mapping[str, int] = field(default_factory=dict)
    missing_operations: tuple[str, ...] = ()
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "at_cap": self.at_cap,
            "over_cap": dict(self.over_cap),
            "missing_operations": list(self.missing_operations),
            "detail": self.detail,
        }


def evaluate_budget_boundary(
    *,
    limits: Mapping[str, int],
    consumed: Mapping[str, int],
    required_operations: Mapping[str, bool] | None = None,
) -> BudgetBoundary:
    """Evaluate the frozen boundary rule.

    At the cap with every required operation complete is PASS.  Over the cap or
    a missing required operation is FAIL.  An expected rejection is a separate
    negative PASS and is never represented by this function.
    """
    normalized_limits = _coerce_limits(limits)
    over = {
        unit: int(consumed.get(unit, 0)) - limit
        for unit, limit in normalized_limits.items()
        if int(consumed.get(unit, 0)) > limit
    }
    missing = tuple(sorted(unit for unit, done in (required_operations or {}).items() if not done))
    at_cap = bool(normalized_limits) and all(
        int(consumed.get(unit, 0)) >= limit for unit, limit in normalized_limits.items()
    )
    if over:
        return BudgetBoundary(
            status="FAIL",
            at_cap=False,
            over_cap=over,
            missing_operations=missing,
            detail="consumed units exceed the frozen cap",
        )
    if missing:
        return BudgetBoundary(
            status="FAIL",
            at_cap=at_cap,
            missing_operations=missing,
            detail="required operations are missing",
        )
    return BudgetBoundary(status="PASS", at_cap=at_cap, detail="within cap")


def _plan_budget_to_shape_payload(source: dict[str, Any]) -> dict[str, Any]:
    """Translate an RPH ``PlanBudget`` into the D09 shape (identity when not one)."""
    from acp_services.runtime.budget_projection import (
        PLAN_BUDGET_UNIT_FIELDS,
        plan_budget_to_execution_shape,
        plan_budget_to_unit_limits,
    )

    if not any(name in source for name in PLAN_BUDGET_UNIT_FIELDS):
        return source
    try:
        limits = plan_budget_to_unit_limits(source)
        shape = plan_budget_to_execution_shape(source)
    except ValueError as exc:  # BudgetRequestError
        raise BudgetConfigurationError(str(exc)) from exc
    payload: dict[str, Any] = dict(shape)
    if limits:
        payload["units"] = dict(limits)
    for key in ("timeouts", "unit_basis", "notes"):
        if key in source:
            payload[key] = source[key]
    return payload


def release_statement() -> dict[str, Any]:
    """The enforced / recorded-only / NOT_VERIFIED release statement."""
    from acp_services.runtime.execution_limits import execution_limits_statement

    enforced = [unit for unit, cap in UNIT_CAPABILITIES.items() if cap == BudgetCapability.ENFORCED]
    recorded = [
        unit for unit, cap in UNIT_CAPABILITIES.items() if cap == BudgetCapability.RECORDED_ONLY
    ]
    return {
        "enforced": enforced,
        "recorded_only": recorded,
        "not_verified": [
            {
                "capability": "model_token_enforcement",
                "reason": "model-call proxy is C-owned (acp2_experience_budget_proxy.py, C T-13)",
            },
            {
                "capability": "bsub_enforcement",
                "reason": "LSF is absent in this environment; bsub count is 0 and recorded only",
            },
        ],
        "unit_consumption": UNIT_CONSUMPTION_RULES,
        "release_rules": RELEASE_RULES,
        "notes": RESEARCH_BUDGET_NOTES,
        # M1-04b: the non-BudgetUnit limits (admission-bound / process
        # termination / queue deadline) and the three clocks, from the one
        # execution-limits matrix.  Never re-derived per surface.
        "execution_limits": execution_limits_statement(),
        # M2-03c: the composed projection (unit capabilities + approval policy +
        # deadline wiring + the upgrade gate of every non-final row).
        "enforcement": _enforcement_projection(),
    }


def _enforcement_projection() -> dict[str, Any]:
    """Lazy import: the projection imports this module's unit matrix."""
    from acp_services.runtime.enforcement_projection import enforcement_projection

    return enforcement_projection()
