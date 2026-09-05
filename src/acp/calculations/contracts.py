"""Immutable contracts shared by calculation workflows and executors.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TypeAlias

logger = logging.getLogger(__name__)

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class StructureRole(str, Enum):
    """Role of a structure in a calculation workflow."""

    MINIMUM = "minimum"
    TRANSITION_STATE = "transition_state"


class StepKind(str, Enum):
    """Whitelisted atomic operations; IRC is an independent request."""

    SINGLEPOINT = "singlepoint"
    OPTIMIZE = "optimize"
    FREQUENCY = "frequency"
    SCAN = "scan"
    THERMOCHEMISTRY = "thermochemistry"


class OptimizationMode(str, Enum):
    """Geometry-optimization mode for a calculation step."""

    UNCONSTRAINED = "unconstrained"
    TRANSITION_STATE = "transition_state"
    CONSTRAINED = "constrained"


@dataclass(frozen=True, slots=True)
class OptimizationSpec:
    """Optimization keywords, including transition-state parameters."""

    method: str = ""
    basis: str = ""
    initial_hessian: str | None = None
    recalc_hess: int | None = None
    trust_radius: float | None = None
    max_cycles: int | None = None
    geom_maxiter: int | None = None
    solvent: str | None = None
    solvent_model: str | None = None
    grid: str | None = None
    scf: str | None = None


StepSpec: TypeAlias = OptimizationSpec | dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class StructureArtifact:
    """Structure path and identity metadata passed between calculations."""

    path: Path
    elements: list[str] = field(default_factory=list)
    role: StructureRole = StructureRole.MINIMUM
    source: str = ""
    candidate_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        object.__setattr__(self, "elements", list(self.elements))
        try:
            role = StructureRole(self.role)
        except (TypeError, ValueError) as exc:
            allowed = ", ".join(role.value for role in StructureRole)
            message = f"role must be one of: {allowed}"
            raise ValueError(message) from exc
        object.__setattr__(self, "role", role)


@dataclass(frozen=True, slots=True)
class CalculationRequest:
    """Backend-independent input, method, resources, and workflow request."""

    input_artifact: StructureArtifact
    method: str
    resources: dict[str, JsonValue] = field(default_factory=dict)
    workflow: str = ""
    profile: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "resources", dict(self.resources))

    @property
    def input(self) -> StructureArtifact:
        return self.input_artifact


@dataclass(frozen=True, slots=True)
class CalculationStep:
    """One executable atomic calculation operation."""

    kind: StepKind
    mode: OptimizationMode = OptimizationMode.UNCONSTRAINED
    spec: StepSpec | None = None

    def __post_init__(self) -> None:
        try:
            kind = StepKind(self.kind)
        except (TypeError, ValueError) as exc:
            allowed = ", ".join(step_kind.value for step_kind in StepKind)
            message = f"kind must be one of: {allowed}"
            raise ValueError(message) from exc
        try:
            mode = OptimizationMode(self.mode)
        except (TypeError, ValueError) as exc:
            allowed = ", ".join(opt_mode.value for opt_mode in OptimizationMode)
            message = f"mode must be one of: {allowed}"
            raise ValueError(message) from exc
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "mode", mode)
        if isinstance(self.spec, Mapping):
            object.__setattr__(self, "spec", dict(self.spec))


@dataclass(frozen=True, slots=True)
class CalculationPlan:
    """Ordered calculation steps and their structure inputs."""

    workflow: str
    profile: str = "default"
    items: list[StructureArtifact | Mapping[str, JsonValue]] = field(default_factory=list)
    steps: list[CalculationStep | Mapping[str, JsonValue]] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", list(self.items))
        object.__setattr__(self, "steps", list(self.steps))


_STEP_KIND_VALUES = frozenset(step_kind.value for step_kind in StepKind)


def validate_plan(plan: CalculationPlan) -> list[str]:
    """Return validation errors for a calculation plan.

    Raw mapping steps are accepted only at this boundary so malformed JSON
    plans can produce actionable errors. In particular, ``irc`` is not a
    ``StepKind`` and therefore cannot be part of a ``BatchOptimize`` plan.

    Args:
        plan: Plan to inspect.

    Returns:
        A list of human-readable validation errors; an empty list means valid.
    """
    errors: list[str] = []
    for index, step in enumerate(plan.steps):
        if isinstance(step, CalculationStep):
            kind = step.kind.value
        else:
            raw_kind = step.get("kind")
            kind = raw_kind if isinstance(raw_kind, str) else ""
        if kind not in _STEP_KIND_VALUES:
            label = kind or "<missing>"
            prefix = f"steps[{index}]: unsupported kind {label!r};"
            errors.append(f"{prefix} IRC requests must be submitted independently")
    return errors


@dataclass(frozen=True, slots=True)
class CalculationResult:
    """Standardized energy, geometry, frequencies, and artifact result."""

    energy: float | None = None
    coords: Sequence[Sequence[float]] | None = None
    frequencies: list[float] = field(default_factory=list)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    status: str = "completed"
    errors: list[str] = field(default_factory=list)
    provenance: Provenance | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "frequencies", list(self.frequencies))
        object.__setattr__(self, "artifacts", list(self.artifacts))
        object.__setattr__(self, "errors", list(self.errors))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Reference to a persisted file in a result manifest."""

    path: Path
    type: str
    checksum: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))


@dataclass(frozen=True, slots=True)
class Provenance:
    """Backend and input identity attached to a calculation result."""

    backend: str
    method: str
    profile: str
    version: str
    input_signature: str


@dataclass(frozen=True, slots=True)
class TaskManifest:
    """Unique display index for all artifacts produced by a task."""

    task_id: str
    workflow: str
    status: str = "pending"
    artifacts: list[ArtifactRef] = field(default_factory=list)
    provenance: Provenance | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", list(self.artifacts))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Internal resumable state, separate from the display manifest."""

    task_id: str
    workflow: str
    plan_fingerprint: str
    step_states: list[JsonValue] = field(default_factory=list)
    items_state: dict[str, JsonValue] = field(default_factory=dict)
    attempts: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_states", list(self.step_states))
        object.__setattr__(self, "items_state", dict(self.items_state))


__all__ = [
    "ArtifactRef",
    "CalculationPlan",
    "CalculationRequest",
    "CalculationResult",
    "CalculationStep",
    "Checkpoint",
    "OptimizationMode",
    "OptimizationSpec",
    "Provenance",
    "StepKind",
    "StructureArtifact",
    "StructureRole",
    "TaskManifest",
    "validate_plan",
]
