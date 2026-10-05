"""Immutable contracts shared by calculation workflows and executors.

Field-duty split (todo 11): scientific single-structure / single-state /
single-execution types now live in ``cccp.calculation.contracts`` and are
re-exported here unchanged (``A is B`` identity).  This module keeps the
ACP-side orchestration and compatibility contracts: plan/step kinds,
``CalculationPlan``/``validate_plan``, task manifests, checkpoints, the
state-sweep ``ElectronicStateConfig``, and the legacy
``CalculationRequest``/``CalculationResult`` shapes (ACP compatibility
contracts converted by ``acp.calculations.legacy_adapters``).

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TypeAlias

# ── relocated scientific types (unchanged semantics; A is B) ────────────
from cccp.calculation.contracts import (
    ArtifactRef,
    CASSCFSpec,
    CollapsePolicy,
    DynamicCorrelation,
    ElectronicStateSpec,
    ElectronicStateValidation,
    GuessSpec,
    GuessStrategy,
    IncompatibleBasisPolicy,
    JsonObject,
    JsonValue,
    OptimizationMode,
    OptimizationSpec,
    PopulationAnalysis,
    SpatialSymmetryMode,
    SpinDiagnosticsSpec,
    SpinMode,
    SpinQualityGate,
    StabilityMode,
    StructureRole,
    WavefunctionPolicy,
    WavefunctionSource,
    casscf_spec_from_dict,
    casscf_spec_to_dict,
    electron_parity_ok,
    electronic_state_spec_from_dict,
    electronic_state_spec_to_dict,
    expected_s2_for_multiplicity,
    guess_spec_from_dict,
    guess_spec_to_dict,
    orca_xyz_multiplicity,
    state_signature,
    validate_casscf_spec,
    validate_electronic_state_spec,
)
from cccp.calculation.contracts import _require_str as _require_str

logger = logging.getLogger(__name__)


# ── ACP orchestration contracts (stay in ACP) ───────────────────────────


class StepKind(str, Enum):
    """Whitelisted atomic operations; IRC is an independent request."""

    SINGLEPOINT = "singlepoint"
    OPTIMIZE = "optimize"
    FREQUENCY = "frequency"
    SCAN = "scan"
    THERMOCHEMISTRY = "thermochemistry"
    CASSCF = "casscf"


StepSpec: TypeAlias = OptimizationSpec | dict[str, JsonValue]


class ElectronicStateExecutionMode(str, Enum):
    """How a level's state list is executed (state-sweep orchestration)."""

    SINGLE = "single"
    STATE_SWEEP = "state_sweep"


@dataclass(frozen=True, slots=True)
class ElectronicStateConfig:
    """Electronic-state module attached to a method level (§5.1).

    State-set / state-sweep orchestration is ACP-side: task requests carry
    exactly one :class:`ElectronicStateSpec`.
    """

    execution_mode: ElectronicStateExecutionMode = ElectronicStateExecutionMode.SINGLE
    default_state_id: str = ""
    states: tuple[ElectronicStateSpec, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "execution_mode",
            ElectronicStateExecutionMode(self.execution_mode),
        )
        object.__setattr__(self, "states", tuple(self.states))

    @property
    def is_active(self) -> bool:
        """``True`` when the module changes default behaviour."""
        if not self.states:
            return False
        if len(self.states) > 1:
            return True
        state = self.states[0]
        return not (
            state.spin_mode is SpinMode.AUTO and state.guess.strategy is GuessStrategy.DEFAULT
        )

    def selected_state(self) -> ElectronicStateSpec | None:
        """Return the state executed by ``single`` mode, or ``None``."""
        if not self.states:
            return None
        if self.default_state_id:
            for state in self.states:
                if state.state_id == self.default_state_id:
                    return state
            message = (
                f"default_state_id {self.default_state_id!r} does not match "
                f"any state in {[s.state_id for s in self.states]}"
            )
            raise ValueError(message)
        return self.states[0]


@dataclass(frozen=True, slots=True)
class StructureArtifact:
    """Structure path and identity metadata passed between calculations.

    ACP compatibility shape (keeps ``candidate_id``); the cccp scientific
    counterpart is ``cccp.calculation.contracts.StructureArtifact`` and the
    two are converted explicitly by ``acp.calculations.legacy_adapters``.
    """

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
    """Backend-independent input, method, resources, and workflow request.

    ACP compatibility shape (legacy request envelope); converted to
    ``cccp.calculation.TaskRequest`` via ``acp.calculations.legacy_adapters``.
    """

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
class ExecutionPolicy:
    """Explicit execution policy for prerequisite failure (D07).

    ``upstream_failure="block"`` (default): a step whose prerequisite is
    unmet becomes ``blocked`` and its primitive is never invoked.
    ``"diagnostics"``: the step runs anyway, but the result and every
    manifest product are marked ``metadata["diagnostic_only"]=True`` and
    can never satisfy a normal downstream prerequisite.
    """

    upstream_failure: str = "block"

    def __post_init__(self) -> None:
        if self.upstream_failure not in _UPSTREAM_FAILURE_POLICIES:
            allowed = ", ".join(repr(value) for value in _UPSTREAM_FAILURE_POLICIES)
            message = f"upstream_failure must be one of: {allowed}"
            raise ValueError(message)


_UPSTREAM_FAILURE_POLICIES: tuple[str, ...] = ("block", "diagnostics")


@dataclass(frozen=True, slots=True)
class CalculationPlan:
    """Ordered calculation steps and their structure inputs."""

    workflow: str
    profile: str = "default"
    items: list[StructureArtifact | Mapping[str, JsonValue]] = field(default_factory=list)
    steps: list[CalculationStep | Mapping[str, JsonValue]] = field(default_factory=list)
    #: Optional D07 execution policy; ``None`` means the default (``block``).
    #: Raw mappings are accepted at the boundary and validated by
    #: :func:`validate_plan` before :func:`resolve_execution_policy` coerces.
    execution_policy: ExecutionPolicy | Mapping[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", list(self.items))
        object.__setattr__(self, "steps", list(self.steps))


_STEP_KIND_VALUES = frozenset(step_kind.value for step_kind in StepKind)

#: Exact rejection messages (contract text pinned by tests).
_CARDINALITY_ERROR = (
    "calculation plans support exactly one input item; "
    "submit multiple structures via BatchOptimize"
)
_THERMOCHEMISTRY_ERROR = (
    "thermochemistry requires preceding frequency and singlepoint steps"
)


def validate_plan(plan: CalculationPlan) -> list[str]:
    """Return validation errors for a calculation plan.

    This is the single rejection layer for plan input (executor entry and
    explicit callers).  The contract is intentionally narrow — exactly one
    input item, at most one step per kind (output directories are
    per-kind), no unsupported kinds (``irc`` is not a ``StepKind`` and must
    be submitted independently), and ``THERMOCHEMISTRY`` only after
    preceding ``FREQUENCY`` and ``SINGLEPOINT`` steps.  It is not a
    general DAG validator.

    Raw mapping steps are accepted only at this boundary so malformed JSON
    plans can produce actionable errors.  The optional ``execution_policy``
    enum is validated here as well (a raw mapping may carry an unsupported
    ``upstream_failure`` value).

    Args:
        plan: Plan to inspect.

    Returns:
        A list of human-readable validation errors; an empty list means valid.
    """
    errors: list[str] = []

    if len(plan.items) != 1:
        errors.append(_CARDINALITY_ERROR)

    policy = plan.execution_policy
    if policy is not None and not isinstance(policy, ExecutionPolicy):
        if isinstance(policy, Mapping):
            upstream = policy.get("upstream_failure", "block")
            if upstream not in _UPSTREAM_FAILURE_POLICIES:
                allowed = ", ".join(repr(value) for value in _UPSTREAM_FAILURE_POLICIES)
                errors.append(
                    f"execution_policy.upstream_failure must be one of: {allowed}; "
                    f"got {upstream!r}"
                )
        else:
            errors.append("execution_policy must be an ExecutionPolicy or a mapping")

    kind_values: list[str] = []
    seen_kinds: set[str] = set()
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
            kind_values.append("")
            continue
        if kind in seen_kinds:
            errors.append(
                f"duplicate step kind {kind!r} is unsupported: output directories "
                "are per-kind; split into separate plans"
            )
        seen_kinds.add(kind)
        kind_values.append(kind)

    thermo_error_reported = False
    for index, kind in enumerate(kind_values):
        if kind != StepKind.THERMOCHEMISTRY.value or thermo_error_reported:
            continue
        preceding = set(kind_values[:index])
        required = {StepKind.FREQUENCY.value, StepKind.SINGLEPOINT.value}
        if not required <= preceding:
            errors.append(_THERMOCHEMISTRY_ERROR)
            thermo_error_reported = True

    return errors


def resolve_execution_policy(plan: CalculationPlan) -> ExecutionPolicy:
    """Return the effective :class:`ExecutionPolicy` of *plan*.

    Call after :func:`validate_plan` — raw mapping policies are coerced
    here; an absent policy resolves to the default (``block``).
    """
    policy = plan.execution_policy
    if isinstance(policy, ExecutionPolicy):
        return policy
    if isinstance(policy, Mapping):
        raw = policy.get("upstream_failure", "block")
        return ExecutionPolicy(upstream_failure=str(raw))
    return ExecutionPolicy()


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

    def to_step_result_dict(
        self, *, root: Path | str | None = None
    ) -> dict[str, JsonValue]:
        """Serialise the science payload of ``step_result.json`` (V02).

        Artifact paths are stored relative to *root* when the file lives
        inside it (portable across attempt archives); each persisted
        artifact carries its ``sha256``.  Artifacts that are not readable
        right now are omitted — an unverifiable reference must never be
        recorded as durable science.

        Symmetric counterpart: :meth:`from_step_result_dict`.  Unknown-field
        policy: the reader ignores unknown keys and falls back to field
        defaults for type-invalid values, so a newer writer stays readable.
        """
        from acp.calculations.step_result import file_sha256, json_safe, portable_path

        artifacts: list[JsonValue] = []
        for artifact in self.artifacts:
            path = Path(artifact.path)
            sha256 = file_sha256(path)
            if sha256 is None:
                continue
            artifacts.append(
                {
                    "path": portable_path(path, root),
                    "type": artifact.type,
                    "sha256": sha256,
                    "source": artifact.source,
                }
            )
        payload: dict[str, JsonValue] = {
            "energy": self.energy,
            "coords": (
                [[float(value) for value in row] for row in self.coords]
                if self.coords is not None
                else None
            ),
            "frequencies": [float(value) for value in self.frequencies],
            "artifacts": artifacts,
            "status": self.status,
            "errors": [str(entry) for entry in self.errors],
            "metadata": dict(json_safe(self.metadata) or {}),
        }
        if self.provenance is not None:
            payload["provenance"] = {
                "backend": self.provenance.backend,
                "method": self.provenance.method,
                "profile": self.provenance.profile,
                "version": self.provenance.version,
                "input_signature": self.provenance.input_signature,
            }
        return payload

    @classmethod
    def from_step_result_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        root: Path | str | None = None,
        roots: Sequence[Path | str] | None = None,
    ) -> CalculationResult:
        """Rebuild a result from a ``step_result.json`` payload (V02).

        *roots* is the ordered resolution base for relative artifact paths
        (archived attempt first, active task root second); *root* is a
        single-base shorthand.  Unresolvable paths fall back to the first
        base so the artifact shape is preserved.
        """
        from acp.calculations.step_result import locate_recorded_file

        bases: Sequence[Path] = (
            tuple(Path(entry) for entry in roots)
            if roots is not None
            else ((Path(root),) if root is not None else ())
        )

        def _float(value: Any) -> float | None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            return float(value)

        energy = _float(payload.get("energy"))

        coords_raw = payload.get("coords")
        coords: list[list[float]] | None = None
        if isinstance(coords_raw, list):
            parsed: list[list[float]] = []
            valid = True
            for row in coords_raw:
                if not isinstance(row, list):
                    valid = False
                    break
                numeric_row: list[float] = []
                for entry in row:
                    number = _float(entry)
                    if number is None:
                        valid = False
                        break
                    numeric_row.append(number)
                if not valid:
                    break
                parsed.append(numeric_row)
            coords = parsed if valid else None

        frequencies_raw = payload.get("frequencies")
        frequencies = (
            [number for entry in frequencies_raw if (number := _float(entry)) is not None]
            if isinstance(frequencies_raw, list)
            else []
        )

        artifacts: list[ArtifactRef] = []
        raw_artifacts = payload.get("artifacts")
        for entry in raw_artifacts if isinstance(raw_artifacts, list) else []:
            if not isinstance(entry, Mapping):
                continue
            recorded = entry.get("path")
            if not isinstance(recorded, str) or not recorded:
                continue
            resolved = locate_recorded_file(bases, recorded) if bases else None
            if resolved is None:
                resolved = (bases[0] / recorded) if bases else Path(recorded)
            sha256 = entry.get("sha256")
            artifacts.append(
                ArtifactRef(
                    path=resolved,
                    type=str(entry.get("type") or "file"),
                    checksum=sha256 if isinstance(sha256, str) else "",
                    source=str(entry.get("source") or ""),
                )
            )

        raw_errors = payload.get("errors")
        errors = [str(entry) for entry in raw_errors] if isinstance(raw_errors, list) else []

        raw_metadata = payload.get("metadata")
        metadata: dict[str, JsonValue] = (
            {str(key): value for key, value in raw_metadata.items()}
            if isinstance(raw_metadata, Mapping)
            else {}
        )

        provenance: Provenance | None = None
        raw_provenance = payload.get("provenance")
        if isinstance(raw_provenance, Mapping) and all(
            isinstance(raw_provenance.get(field), str) and raw_provenance.get(field)
            for field in ("backend", "method", "version", "input_signature")
        ):
            provenance = Provenance(
                backend=str(raw_provenance.get("backend")),
                method=str(raw_provenance.get("method")),
                profile=str(raw_provenance.get("profile") or ""),
                version=str(raw_provenance.get("version")),
                input_signature=str(raw_provenance.get("input_signature")),
            )

        return cls(
            energy=energy,
            coords=coords,
            frequencies=frequencies,
            artifacts=artifacts,
            status=str(payload.get("status") or "completed"),
            errors=errors,
            provenance=provenance,
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class Provenance:
    """Backend and input identity attached to a calculation result.

    ACP compatibility shape (keeps ``profile``); the cccp task-level
    counterpart is ``cccp.calculation.contracts.Provenance`` and profile is
    carried as ACP-side binding information instead.
    """

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
    """Internal resumable state, separate from the display manifest.

    ``identity_schema`` binds the fingerprint scheme: ``1`` = legacy
    (content of ``plan_fingerprint`` unverifiable against the v2 science
    identity), ``2`` = v2 identity (``acp.calculations.identity``).  Missing
    on disk → reads as ``1`` for legacy files.

    ``resume_count`` is the checkpoint-internal resume counter — explicitly
    separate from ``jobs.attempt`` (no second attempt counter).  The v1
    serialisation key stays ``attempts`` (frozen fixtures); v2 writes
    ``resume_count``.
    """

    task_id: str
    workflow: str
    plan_fingerprint: str
    step_states: list[JsonValue] = field(default_factory=list)
    items_state: dict[str, JsonValue] = field(default_factory=dict)
    resume_count: int = 0
    identity_schema: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_states", list(self.step_states))
        object.__setattr__(self, "items_state", dict(self.items_state))


# ── electronic-state module serialization (ACP orchestration envelope) ──


def electronic_state_config_to_dict(config: ElectronicStateConfig) -> JsonObject:
    """Serialise an :class:`ElectronicStateConfig` (module envelope §4.1)."""
    return {
        "schema_version": 1,
        "execution_mode": config.execution_mode.value,
        "default_state_id": config.default_state_id,
        "states": [electronic_state_spec_to_dict(state) for state in config.states],
    }


def electronic_state_config_from_dict(
    payload: Mapping[str, object] | None,
) -> ElectronicStateConfig:
    """Parse an :class:`ElectronicStateConfig` from a method-level value.

    Accepts both the full module envelope (``execution_mode`` + ``states``)
    and the shorthand ``{"mode": "automatic"}`` / ``{"mode": ...}`` preset
    form used by the catalog default (§4.1).
    """
    if not payload:
        return ElectronicStateConfig()

    shorthand = _require_str(payload, "mode")
    if shorthand:
        return electronic_state_config_from_preset_mode(shorthand)

    raw_states = payload.get("states")
    states: list[ElectronicStateSpec] = []
    if isinstance(raw_states, list):
        for entry in raw_states:
            if isinstance(entry, Mapping):
                states.append(electronic_state_spec_from_dict(entry))

    raw_mode = _require_str(payload, "execution_mode", ElectronicStateExecutionMode.SINGLE.value)
    try:
        execution_mode = ElectronicStateExecutionMode(raw_mode)
    except ValueError:
        execution_mode = ElectronicStateExecutionMode.SINGLE

    return ElectronicStateConfig(
        execution_mode=execution_mode,
        default_state_id=_require_str(payload, "default_state_id"),
        states=tuple(states),
    )


def electronic_state_config_from_preset_mode(mode: str) -> ElectronicStateConfig:
    """Build a config from a top-level UI mode (§4.3)."""
    normalized = mode.strip().lower()
    mapping: dict[str, tuple[SpinMode, int]] = {
        "automatic": (SpinMode.AUTO, 1),
        "auto": (SpinMode.AUTO, 1),
        "closed_shell": (SpinMode.RESTRICTED, 1),
        "closed-shell": (SpinMode.RESTRICTED, 1),
        "open_shell": (SpinMode.UNRESTRICTED, 2),
        "open-shell": (SpinMode.UNRESTRICTED, 2),
        "unrestricted": (SpinMode.UNRESTRICTED, 2),
    }
    if normalized in mapping:
        spin_mode, multiplicity = mapping[normalized]
        return ElectronicStateConfig(
            states=(
                ElectronicStateSpec(
                    state_id="default",
                    target_multiplicity=multiplicity,
                    spin_mode=spin_mode,
                ),
            ),
        )
    if normalized in {"bs_singlet", "bs", "broken_symmetry"}:
        return ElectronicStateConfig(
            states=(
                ElectronicStateSpec(
                    state_id="s1_bs",
                    label="BS singlet",
                    target_multiplicity=1,
                    spin_mode=SpinMode.BROKEN_SYMMETRY,
                    guess=GuessSpec(
                        strategy=GuessStrategy.FLIPSPIN,
                        reference_multiplicity=3,
                        final_ms=0.0,
                    ),
                    spatial_symmetry=SpatialSymmetryMode.DISABLE,
                ),
            ),
        )
    message = f"unknown electronic-state mode {mode!r}"
    raise ValueError(message)


def validate_electronic_state(
    config: ElectronicStateConfig,
    *,
    backend: str = "orca",
    n_electrons: int | None = None,
    n_atoms: int | None = None,
) -> ElectronicStateValidation:
    """Validate an electronic-state module (design doc §13).

    Set-level orchestration checks (duplicate ids, state-sweep arity,
    default/reference state selection) plus per-state scientific validation
    (delegated to ``cccp.calculation.contracts.validate_electronic_state_spec``).

    Args:
        config: Parsed module configuration.
        backend: Target QC backend (BS is ORCA-only in first phase).
        n_electrons: Total electron count when known (parity check).
        n_atoms: Atom count when known (FlipSpin range check).

    Returns:
        Split errors/warnings; only errors block submission (§15.3).
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not config.states:
        return ElectronicStateValidation(errors, warnings)

    seen_ids: set[str] = set()
    for state in config.states:
        prefix = f"state {state.state_id!r}"

        if state.state_id in seen_ids:
            errors.append(f"{prefix}: duplicate state_id in the state set")
        seen_ids.add(state.state_id)

        outcome = validate_electronic_state_spec(
            state,
            backend=backend,
            n_electrons=n_electrons,
            n_atoms=n_atoms,
        )
        errors.extend(outcome.errors)
        warnings.extend(outcome.warnings)

    if config.execution_mode is ElectronicStateExecutionMode.STATE_SWEEP and len(config.states) < 2:
        errors.append("state_sweep execution requires at least two states")

    if config.default_state_id and config.default_state_id not in seen_ids:
        errors.append(f"default_state_id {config.default_state_id!r} does not match any state_id")

    for state in config.states:
        if state.reference_state_id and state.reference_state_id not in seen_ids:
            errors.append(
                f"state {state.state_id!r} references unknown reference_state_id "
                f"{state.reference_state_id!r}"
            )

    return ElectronicStateValidation(errors, warnings)


__all__ = [
    "ArtifactRef",
    "CASSCFSpec",
    "CalculationPlan",
    "CalculationRequest",
    "CalculationResult",
    "CalculationStep",
    "Checkpoint",
    "CollapsePolicy",
    "DynamicCorrelation",
    "ElectronicStateConfig",
    "ElectronicStateExecutionMode",
    "ElectronicStateSpec",
    "ElectronicStateValidation",
    "ExecutionPolicy",
    "GuessSpec",
    "GuessStrategy",
    "IncompatibleBasisPolicy",
    "JsonObject",
    "OptimizationMode",
    "OptimizationSpec",
    "PopulationAnalysis",
    "Provenance",
    "SpatialSymmetryMode",
    "SpinDiagnosticsSpec",
    "SpinMode",
    "SpinQualityGate",
    "StabilityMode",
    "StepKind",
    "StructureArtifact",
    "StructureRole",
    "TaskManifest",
    "WavefunctionPolicy",
    "WavefunctionSource",
    "casscf_spec_from_dict",
    "casscf_spec_to_dict",
    "electron_parity_ok",
    "electronic_state_config_from_dict",
    "electronic_state_config_from_preset_mode",
    "electronic_state_config_to_dict",
    "electronic_state_spec_from_dict",
    "electronic_state_spec_to_dict",
    "expected_s2_for_multiplicity",
    "guess_spec_from_dict",
    "guess_spec_to_dict",
    "orca_xyz_multiplicity",
    "resolve_execution_policy",
    "state_signature",
    "validate_casscf_spec",
    "validate_electronic_state",
    "validate_plan",
]
