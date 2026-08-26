# pyright: reportUnnecessaryComparison=false

"""Build calculation plans and independent IRC requests.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, TypeAlias, assert_never

from .contracts import (
    CalculationPlan,
    CalculationRequest,
    CalculationStep,
    JsonValue,
    OptimizationMode,
    StepKind,
    StructureArtifact,
    StructureRole,
)

_BatchItem: TypeAlias = StructureArtifact | Mapping[str, JsonValue]

_PROFILE_STEPS: Final[dict[str, tuple[StepKind, ...]]] = {
    "opt_only": (StepKind.OPTIMIZE,),
    "opt_freq": (StepKind.OPTIMIZE, StepKind.FREQUENCY),
    "opt_freq_sp": (
        StepKind.OPTIMIZE,
        StepKind.FREQUENCY,
        StepKind.SINGLEPOINT,
    ),
    "opt_freq_sp_thermo": (
        StepKind.OPTIMIZE,
        StepKind.FREQUENCY,
        StepKind.SINGLEPOINT,
        StepKind.THERMOCHEMISTRY,
    ),
}


@dataclass(frozen=True, slots=True)
class IrcRequest:
    """Independent IRC request; IRC is intentionally not a plan step."""

    workflow: str
    input_artifact: StructureArtifact
    input_role: StructureRole
    directions: tuple[str, ...]


def _item_role(item: _BatchItem) -> StructureRole:
    match item:
        case StructureArtifact(role=role):
            return role
        case Mapping() as mapping:
            raw_role = mapping.get("role", StructureRole.MINIMUM.value)
            if not isinstance(raw_role, str):
                message = "batch item role must be a string"
                raise ValueError(message)
            try:
                return StructureRole(raw_role)
            except ValueError as exc:
                allowed = ", ".join(role.value for role in StructureRole)
                message = f"batch item role must be one of: {allowed}"
                raise ValueError(message) from exc
        case unreachable:
            assert_never(unreachable)


def _optimization_mode(kind: StepKind, transition_state: bool) -> OptimizationMode:
    if not transition_state:
        return OptimizationMode.UNCONSTRAINED

    match kind:
        case StepKind.OPTIMIZE:
            return OptimizationMode.TRANSITION_STATE
        case StepKind.SINGLEPOINT | StepKind.FREQUENCY | StepKind.SCAN | StepKind.THERMOCHEMISTRY:
            return OptimizationMode.UNCONSTRAINED
        case unreachable:
            assert_never(unreachable)


def build_simple_plan(kind: StepKind | str, request: CalculationRequest) -> CalculationPlan:
    """Create a one-step plan from a simple calculation request."""
    step_kind = StepKind(kind)
    workflow = request.workflow or step_kind.value
    profile = request.profile or "default"
    step = CalculationStep(
        kind=step_kind,
        mode=_optimization_mode(
            step_kind,
            request.input_artifact.role is StructureRole.TRANSITION_STATE,
        ),
    )
    return CalculationPlan(
        workflow=workflow,
        profile=profile,
        items=[request.input_artifact],
        steps=[step],
    )


def build_batch_plan(items: Sequence[_BatchItem], profile: str) -> CalculationPlan:
    """Create a BatchOptimize plan for one of the four supported profiles."""
    batch_items = list(items)
    if not batch_items:
        message = "batch plan requires at least one item"
        raise ValueError(message)

    try:
        step_kinds = _PROFILE_STEPS[profile]
    except KeyError:
        message = f"unknown batch profile: {profile!r}"
        raise ValueError(message) from None

    transition_state = any(
        _item_role(item) is StructureRole.TRANSITION_STATE for item in batch_items
    )
    steps: list[CalculationStep | Mapping[str, JsonValue]] = [
        CalculationStep(
            kind=kind,
            mode=_optimization_mode(kind, transition_state),
        )
        for kind in step_kinds
    ]
    return CalculationPlan(
        workflow="BatchOptimize",
        profile=profile,
        items=batch_items,
        steps=steps,
    )


def build_irc_request(ts_artifact: StructureArtifact, directions: Sequence[str]) -> IrcRequest:
    """Create an independent IRC request for a transition-state artifact."""
    return IrcRequest(
        workflow="irc",
        input_artifact=ts_artifact,
        input_role=StructureRole.TRANSITION_STATE,
        directions=tuple(directions),
    )


__all__ = [
    "IrcRequest",
    "build_batch_plan",
    "build_irc_request",
    "build_simple_plan",
]
