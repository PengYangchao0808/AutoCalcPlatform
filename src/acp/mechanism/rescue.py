"""TS / INT rescue matrix (declarative, mirroring RPH v4.0.1).

A declarative mapping from ``(failure_type, structure_kind)`` to an ordered
list of rescue strategies. The workflow consults the matrix after a failed
optimization, applies the strategies in order, and re-runs the failing stage
until one succeeds or the list is exhausted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FailureType = Literal[
    "geometry_not_converged",
    "higher_order_saddle",
    "ts_no_imaginary",
    "minimum_with_imaginary",
    "scf_failure",
    "crash_timeout",
    "collapsed_to_product",
]

StructureKind = Literal["ts", "intermediate", "minimum", "precursor", "product"]

_RESCUE_MATRIX: dict[tuple[FailureType, StructureKind], list[str]] = {
    ("geometry_not_converged", "ts"): [
        "fresh_hessian_restart",
        "ts_mode_directed",
        "calcall_opt",
    ],
    ("higher_order_saddle", "ts"): [
        "saddle_break",
        "ts_mode_directed",
        "calcall_opt",
    ],
    ("ts_no_imaginary", "ts"): [
        "ts_mode_directed",
        "fresh_hessian_restart",
    ],
    ("minimum_with_imaginary", "intermediate"): [
        "mode_displacement",
        "tight_opt_calchess",
    ],
    ("minimum_with_imaginary", "minimum"): [
        "tight_opt_calchess",
        "mode_displacement",
    ],
    ("collapsed_to_product", "intermediate"): [
        "irc_midpoint_recovery",
        "mode_displacement",
    ],
    ("scf_failure", "ts"): [],
    ("scf_failure", "intermediate"): [],
    ("scf_failure", "minimum"): [],
    ("scf_failure", "precursor"): [],
    ("scf_failure", "product"): [],
    ("crash_timeout", "ts"): [],
    ("crash_timeout", "intermediate"): [],
    ("crash_timeout", "minimum"): [],
    ("crash_timeout", "precursor"): [],
    ("crash_timeout", "product"): [],
}

_RESCUE_DESCRIPTIONS: dict[str, str] = {
    "fresh_hessian_restart": "restart with CalcHess + RecalcHess=5",
    "ts_mode_directed": "re-run with TS_Mode targeting the lowest imaginary mode",
    "calcall_opt": "re-run with RecalcHess=1 (CalcAll semantics)",
    "saddle_break": "displace along the second imaginary mode to break the saddle",
    "mode_displacement": "displace ±0.30 Å along the imaginary mode",
    "tight_opt_calchess": "tight optimization with calculated Hessian",
    "irc_midpoint_recovery": "re-seed from the IRC midpoint (collapsed INT recovery)",
}

FAILURE_EXIT: set[FailureType] = {"scf_failure", "crash_timeout"}


@dataclass(frozen=True)
class RescueAction:
    """One rescue step emitted by the matrix.

    Attributes:
        strategy: Rescue strategy id.
        failure_type: The failure that triggered this rescue.
        structure_kind: The structure being rescued.
        description: Human-readable strategy description.
        index: Position in the ordered rescue chain (0-based).
    """

    strategy: str
    failure_type: FailureType
    structure_kind: StructureKind
    description: str
    index: int = 0


@dataclass(frozen=True)
class RescuePlan:
    """Ordered rescue chain for one failed optimization.

    Attributes:
        failure_type: Classified failure.
        structure_kind: Structure being rescued.
        actions: Ordered rescue actions (empty when the failure is terminal).
        terminal: Whether the failure has no rescue path (SCF crash / timeout).
    """

    failure_type: FailureType
    structure_kind: StructureKind
    actions: list[RescueAction] = field(default_factory=list)
    terminal: bool = False

    @property
    def exhausted(self) -> bool:
        return self.terminal or not self.actions


def build_rescue_plan(failure_type: FailureType, structure_kind: StructureKind) -> RescuePlan:
    """Build a :class:`RescuePlan` for a ``(failure_type, structure_kind)`` pair.

    SCF failures and crashes/timeouts are terminal (no rescue); everything
    else follows the ordered strategies in the matrix (an empty list for the
    pair behaves as terminal).
    """
    terminal = failure_type in FAILURE_EXIT
    strategies = _RESCUE_MATRIX.get((failure_type, structure_kind), [])
    if terminal or not strategies:
        return RescuePlan(
            failure_type=failure_type,
            structure_kind=structure_kind,
            actions=[],
            terminal=terminal or not strategies,
        )
    actions = [
        RescueAction(
            strategy=strategy,
            failure_type=failure_type,
            structure_kind=structure_kind,
            description=_RESCUE_DESCRIPTIONS.get(strategy, strategy),
            index=i,
        )
        for i, strategy in enumerate(strategies)
    ]
    return RescuePlan(
        failure_type=failure_type,
        structure_kind=structure_kind,
        actions=actions,
        terminal=False,
    )


def apply_rescue_kwargs(action: RescueAction, base_kwargs: dict[str, object]) -> dict[str, object]:
    """Map a rescue action onto keyword overrides for the next stage run.

    Each strategy translates to ORCA/xTB keyword adjustments (fresh Hessian,
    TS_Mode, RecalcHess, trust radius, displacement). Unknown strategies pass
    *base_kwargs* through unchanged.
    """
    kwargs = dict(base_kwargs)
    strategy = action.strategy
    if strategy == "fresh_hessian_restart":
        kwargs["initial_hessian"] = "calculate"
        kwargs["recalc_hess"] = 5
    elif strategy == "ts_mode_directed":
        kwargs["ts_mode"] = True
        kwargs["trust_radius"] = 0.15
    elif strategy == "calcall_opt":
        kwargs["recalc_hess"] = 1
    elif strategy == "saddle_break":
        kwargs["mode_displacement"] = 0.30
    elif strategy == "mode_displacement":
        kwargs["mode_displacement"] = 0.30
    elif strategy == "tight_opt_calchess":
        kwargs["opt_level"] = "tight"
        kwargs["initial_hessian"] = "calculate"
    elif strategy == "irc_midpoint_recovery":
        kwargs["irc_midpoint_reseed"] = True
    return kwargs


__all__ = [
    "FAILURE_EXIT",
    "RescueAction",
    "RescuePlan",
    "apply_rescue_kwargs",
    "build_rescue_plan",
]
