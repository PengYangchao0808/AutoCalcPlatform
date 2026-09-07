"""Immutable contracts shared by calculation workflows and executors.

Author: QCcalc Team
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal, TypeAlias

logger = logging.getLogger(__name__)

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


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
    CASSCF = "casscf"


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


# ── Electronic state and spin configuration (design doc §5) ─────────────


class SpinMode(str, Enum):
    """Backend-independent spin treatment of one target electronic state."""

    AUTO = "auto"
    RESTRICTED = "restricted"
    UNRESTRICTED = "unrestricted"
    BROKEN_SYMMETRY = "broken_symmetry"


class GuessStrategy(str, Enum):
    """Initial-guess strategy for constructing the target wavefunction."""

    DEFAULT = "default"
    GUESSMIX = "guessmix"
    FLIPSPIN = "flipspin"
    BROKEN_SYM = "broken_sym"
    MOREAD = "moread"
    STABILITY_RESTART = "stability_restart"


class SpatialSymmetryMode(str, Enum):
    """Molecular point-group symmetry handling (independent of spin symmetry)."""

    AUTO = "auto"
    DISABLE = "disable"
    PRESERVE = "preserve"


class WavefunctionSource(str, Enum):
    """Where a step obtains its starting wavefunction."""

    AUTO = "auto"
    REGENERATE = "regenerate"
    ARTIFACT = "artifact"
    NONE = "none"


class IncompatibleBasisPolicy(str, Enum):
    """Action when an inherited ``.gbw`` uses a different basis."""

    REGENERATE = "regenerate"
    PROJECT = "project"
    FAIL = "fail"


class PopulationAnalysis(str, Enum):
    """Population scheme used for local spin-density diagnostics."""

    MULLIKEN = "mulliken"
    LOEWDIN = "loewdin"


class StabilityMode(str, Enum):
    """When to run an SCF stability analysis (always an SP-like node, §3.4)."""

    NONE = "none"
    FINAL_GEOMETRY = "final_geometry"


class CollapsePolicy(str, Enum):
    """What happens when a broken-symmetry solution is judged collapsed."""

    ERROR = "error"
    WARNING = "warning"
    IGNORE = "ignore"


class ElectronicStateExecutionMode(str, Enum):
    """How a level's state list is executed."""

    SINGLE = "single"
    STATE_SWEEP = "state_sweep"


class DynamicCorrelation(str, Enum):
    """Post-CASSCF dynamic correlation treatment."""

    NONE = "none"
    SC_NEVPT2 = "sc_nevpt2"
    FIC_NEVPT2 = "fic_nevpt2"


@dataclass(frozen=True, slots=True)
class GuessSpec:
    """Initial-guess configuration (design doc §5.3).

    ``flip_atoms`` uses 1-based atom numbering by default
    (``atom_index_base``); conversion to ORCA 0-based indices happens at
    render time via :func:`orca_flip_atoms`.
    """

    strategy: GuessStrategy = GuessStrategy.DEFAULT
    reference_multiplicity: int | None = None
    final_ms: float | None = None
    flip_atoms: tuple[int, ...] = ()
    atom_index_base: Literal[0, 1] = 1
    guess_mix_angle: float = 45.0
    orbital_source: Path | None = None
    broken_sym_na: int | None = None
    broken_sym_nb: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy", GuessStrategy(self.strategy))
        object.__setattr__(self, "flip_atoms", tuple(int(a) for a in self.flip_atoms))
        if self.orbital_source is not None:
            object.__setattr__(self, "orbital_source", Path(self.orbital_source))

    def orca_flip_atoms(self) -> tuple[int, ...]:
        """Convert ``flip_atoms`` to ORCA 0-based indices."""
        base = int(self.atom_index_base)
        return tuple(int(a) - base for a in self.flip_atoms)


@dataclass(frozen=True, slots=True)
class WavefunctionPolicy:
    """Wavefunction continuity policy between calculation steps (§5.4)."""

    source: WavefunctionSource = WavefunctionSource.AUTO
    artifact_path: Path | None = None
    inherit_between_steps: bool = True
    on_incompatible_basis: IncompatibleBasisPolicy = IncompatibleBasisPolicy.REGENERATE
    require_same_state_signature: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", WavefunctionSource(self.source))
        object.__setattr__(
            self,
            "on_incompatible_basis",
            IncompatibleBasisPolicy(self.on_incompatible_basis),
        )
        if self.artifact_path is not None:
            object.__setattr__(self, "artifact_path", Path(self.artifact_path))


@dataclass(frozen=True, slots=True)
class SpinDiagnosticsSpec:
    """Spin and stability diagnostics attached to a state (§5.1)."""

    population: PopulationAnalysis = PopulationAnalysis.MULLIKEN
    stability: StabilityMode = StabilityMode.NONE
    write_spin_density: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "population", PopulationAnalysis(self.population))
        object.__setattr__(self, "stability", StabilityMode(self.stability))


@dataclass(frozen=True, slots=True)
class SpinQualityGate:
    """Acceptance gate for broken-symmetry and open-shell solutions (§10.3).

    The defaults are deliberately permissive: ``<S²>`` alone must never be a
    hard-coded diradical criterion (§10.3, §20).
    """

    collapse_policy: CollapsePolicy = CollapsePolicy.WARNING
    s2_min: float | None = None
    s2_max: float | None = None
    require_opposite_spin_centers: bool = False
    spin_center_threshold: float = 0.05

    def __post_init__(self) -> None:
        object.__setattr__(self, "collapse_policy", CollapsePolicy(self.collapse_policy))


@dataclass(frozen=True, slots=True)
class ElectronicStateSpec:
    """One target electronic state (design doc §5.2).

    ``target_multiplicity`` is the physical state; for the FlipSpin BS
    construction the ORCA coordinate-line multiplicity comes from
    ``guess.reference_multiplicity`` instead (see
    :func:`orca_xyz_multiplicity`).
    """

    state_id: str
    label: str = ""
    target_multiplicity: int = 1
    spin_mode: SpinMode = SpinMode.AUTO
    guess: GuessSpec = field(default_factory=GuessSpec)
    spatial_symmetry: SpatialSymmetryMode = SpatialSymmetryMode.AUTO
    wavefunction: WavefunctionPolicy = field(default_factory=WavefunctionPolicy)
    diagnostics: SpinDiagnosticsSpec = field(default_factory=SpinDiagnosticsSpec)
    quality_gate: SpinQualityGate = field(default_factory=SpinQualityGate)
    reference_state_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "spin_mode", SpinMode(self.spin_mode))
        object.__setattr__(
            self,
            "spatial_symmetry",
            SpatialSymmetryMode(self.spatial_symmetry),
        )
        if not self.state_id:
            message = "electronic state requires a non-empty state_id"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class ElectronicStateConfig:
    """Electronic-state module attached to a method level (§5.1)."""

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
            state.spin_mode is SpinMode.AUTO
            and state.guess.strategy is GuessStrategy.DEFAULT
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
class CASSCFSpec:
    """CASSCF / NEVPT2 calculation contract (design doc §11.2)."""

    active_electrons: int
    active_orbitals: int
    multiplicity: int = 1
    nroots: int = 1
    state_weights: tuple[float, ...] = ()
    orbital_source: Path | None = None
    active_orbital_indices: tuple[int, ...] = ()
    orbital_selection: str = "manual"
    dynamic_correlation: DynamicCorrelation = DynamicCorrelation.NONE
    frozen_core: bool = True
    max_iterations: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dynamic_correlation",
            DynamicCorrelation(self.dynamic_correlation),
        )
        object.__setattr__(self, "state_weights", tuple(float(w) for w in self.state_weights))
        object.__setattr__(
            self,
            "active_orbital_indices",
            tuple(int(i) for i in self.active_orbital_indices),
        )
        if self.orbital_source is not None:
            object.__setattr__(self, "orbital_source", Path(self.orbital_source))

    def active_space_signature(self) -> str:
        """Stable identity for cross-structure comparability checks (§13.5)."""
        return json.dumps(
            {
                "nel": self.active_electrons,
                "norb": self.active_orbitals,
                "mult": self.multiplicity,
                "indices": list(self.active_orbital_indices),
                "dc": self.dynamic_correlation.value,
                "frozen_core": self.frozen_core,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class ElectronicStateValidation:
    """Split validation outcome (§15.3): errors block, warnings advise."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


# ── electronic-state serialization (JSON-safe dicts) ────────────────────


def _enum_value(value: object) -> str:
    return value.value if isinstance(value, Enum) else str(value)


def _clean_json_dict(payload: Mapping[str, object]) -> JsonObject:
    result: JsonObject = {}
    for key, value in payload.items():
        if value is None or isinstance(value, (str, int, float, bool)):
            result[str(key)] = value
        elif isinstance(value, Enum):
            result[str(key)] = value.value
        elif isinstance(value, Path):
            result[str(key)] = str(value)
        elif isinstance(value, Mapping):
            result[str(key)] = _clean_json_dict(value)
        elif isinstance(value, (list, tuple)):
            result[str(key)] = [_json_scalar(item) for item in value]
    return result


def _json_scalar(item: object) -> JsonValue:
    if item is None or isinstance(item, (str, int, float, bool)):
        return item
    if isinstance(item, Enum):
        return item.value
    if isinstance(item, Path):
        return str(item)
    if isinstance(item, Mapping):
        return _clean_json_dict(item)
    if isinstance(item, (list, tuple)):
        return [_json_scalar(entry) for entry in item]
    return str(item)


def guess_spec_to_dict(spec: GuessSpec) -> JsonObject:
    """Serialise a :class:`GuessSpec` to a JSON-safe dict."""
    return _clean_json_dict(
        {
            "strategy": spec.strategy,
            "reference_multiplicity": spec.reference_multiplicity,
            "final_ms": spec.final_ms,
            "flip_atoms": list(spec.flip_atoms),
            "atom_index_base": spec.atom_index_base,
            "guess_mix_angle": spec.guess_mix_angle,
            "orbital_source": spec.orbital_source,
            "broken_sym_na": spec.broken_sym_na,
            "broken_sym_nb": spec.broken_sym_nb,
        }
    )


def electronic_state_spec_to_dict(spec: ElectronicStateSpec) -> JsonObject:
    """Serialise an :class:`ElectronicStateSpec` to a JSON-safe dict."""
    payload: JsonObject = {
        "state_id": spec.state_id,
        "label": spec.label,
        "target_multiplicity": spec.target_multiplicity,
        "spin_mode": spec.spin_mode.value,
        "spatial_symmetry": spec.spatial_symmetry.value,
        "reference_state_id": spec.reference_state_id,
    }
    if spec.guess != GuessSpec():
        payload["guess"] = guess_spec_to_dict(spec.guess)
    if spec.wavefunction != WavefunctionPolicy():
        payload["wavefunction"] = _clean_json_dict(
            {
                "source": spec.wavefunction.source,
                "artifact_path": spec.wavefunction.artifact_path,
                "inherit_between_steps": spec.wavefunction.inherit_between_steps,
                "on_incompatible_basis": spec.wavefunction.on_incompatible_basis,
                "require_same_state_signature": (
                    spec.wavefunction.require_same_state_signature
                ),
            }
        )
    if spec.diagnostics != SpinDiagnosticsSpec():
        payload["diagnostics"] = _clean_json_dict(
            {
                "population": spec.diagnostics.population,
                "stability": spec.diagnostics.stability,
                "write_spin_density": spec.diagnostics.write_spin_density,
            }
        )
    if spec.quality_gate != SpinQualityGate():
        payload["quality_gate"] = _clean_json_dict(
            {
                "collapse_policy": spec.quality_gate.collapse_policy,
                "s2_min": spec.quality_gate.s2_min,
                "s2_max": spec.quality_gate.s2_max,
                "require_opposite_spin_centers": (
                    spec.quality_gate.require_opposite_spin_centers
                ),
                "spin_center_threshold": spec.quality_gate.spin_center_threshold,
            }
        )
    return payload


def electronic_state_config_to_dict(config: ElectronicStateConfig) -> JsonObject:
    """Serialise an :class:`ElectronicStateConfig` (module envelope §4.1)."""
    return {
        "schema_version": 1,
        "execution_mode": config.execution_mode.value,
        "default_state_id": config.default_state_id,
        "states": [electronic_state_spec_to_dict(state) for state in config.states],
    }


def casscf_spec_to_dict(spec: CASSCFSpec) -> JsonObject:
    """Serialise a :class:`CASSCFSpec` to a JSON-safe dict."""
    return _clean_json_dict(
        {
            "active_electrons": spec.active_electrons,
            "active_orbitals": spec.active_orbitals,
            "multiplicity": spec.multiplicity,
            "nroots": spec.nroots,
            "state_weights": list(spec.state_weights),
            "orbital_source": spec.orbital_source,
            "active_orbital_indices": list(spec.active_orbital_indices),
            "orbital_selection": spec.orbital_selection,
            "dynamic_correlation": spec.dynamic_correlation,
            "frozen_core": spec.frozen_core,
            "max_iterations": spec.max_iterations,
        }
    )


def _require_int(payload: Mapping[str, object], key: str, default: int | None = None) -> int | None:
    value = payload.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and float(value).is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _require_float(payload: Mapping[str, object], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _require_bool(payload: Mapping[str, object], key: str, default: bool) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    return default


def _require_str(payload: Mapping[str, object], key: str, default: str = "") -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else default


def _int_tuple(payload: Mapping[str, object], key: str) -> tuple[int, ...]:
    value = payload.get(key)
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            continue
        result.append(int(item))
    return tuple(result)


def _float_tuple(payload: Mapping[str, object], key: str) -> tuple[float, ...]:
    value = payload.get(key)
    if not isinstance(value, (list, tuple)):
        return ()
    result: list[float] = []
    for item in value:
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            result.append(float(item))
    return tuple(result)


def guess_spec_from_dict(payload: Mapping[str, object] | None) -> GuessSpec:
    """Parse a :class:`GuessSpec` from a (possibly partial) mapping."""
    if not payload:
        return GuessSpec()
    raw_strategy = _require_str(payload, "strategy", "default")
    try:
        strategy = GuessStrategy(raw_strategy)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in GuessStrategy)
        message = f"unknown guess strategy {raw_strategy!r}; expected one of: {allowed}"
        raise ValueError(message) from exc
    atom_index_base = _require_int(payload, "atom_index_base", 1) or 1
    if atom_index_base not in (0, 1):
        atom_index_base = 1
    orbital_source = payload.get("orbital_source")
    return GuessSpec(
        strategy=strategy,
        reference_multiplicity=_require_int(payload, "reference_multiplicity"),
        final_ms=_require_float(payload, "final_ms"),
        flip_atoms=_int_tuple(payload, "flip_atoms"),
        atom_index_base=atom_index_base,  # type: ignore[arg-type]
        guess_mix_angle=_require_float(payload, "guess_mix_angle") or 45.0,
        orbital_source=(
        Path(str(orbital_source)) if isinstance(orbital_source, str) and orbital_source else None
    ),
        broken_sym_na=_require_int(payload, "broken_sym_na"),
        broken_sym_nb=_require_int(payload, "broken_sym_nb"),
    )


def electronic_state_spec_from_dict(payload: Mapping[str, object]) -> ElectronicStateSpec:
    """Parse an :class:`ElectronicStateSpec` from a mapping."""
    state_id = _require_str(payload, "state_id")
    if not state_id:
        message = "electronic state entry requires a non-empty state_id"
        raise ValueError(message)

    raw_spin_mode = _require_str(payload, "spin_mode", SpinMode.AUTO.value)
    try:
        spin_mode = SpinMode(raw_spin_mode)
    except ValueError as exc:
        allowed = ", ".join(m.value for m in SpinMode)
        message = f"unknown spin_mode {raw_spin_mode!r}; expected one of: {allowed}"
        raise ValueError(message) from exc

    raw_spatial = _require_str(payload, "spatial_symmetry", SpatialSymmetryMode.AUTO.value)
    try:
        spatial_symmetry = SpatialSymmetryMode(raw_spatial)
    except ValueError:
        spatial_symmetry = SpatialSymmetryMode.AUTO

    target_multiplicity = _require_int(payload, "target_multiplicity", 1) or 1

    wavefunction_raw = payload.get("wavefunction")
    if isinstance(wavefunction_raw, Mapping):
        raw_source = _require_str(wavefunction_raw, "source", WavefunctionSource.AUTO.value)
        try:
            source = WavefunctionSource(raw_source)
        except ValueError:
            source = WavefunctionSource.AUTO
        raw_basis_policy = _require_str(
            wavefunction_raw,
            "on_incompatible_basis",
            IncompatibleBasisPolicy.REGENERATE.value,
        )
        try:
            basis_policy = IncompatibleBasisPolicy(raw_basis_policy)
        except ValueError:
            basis_policy = IncompatibleBasisPolicy.REGENERATE
        artifact_raw = wavefunction_raw.get("artifact_path")
        wavefunction = WavefunctionPolicy(
            source=source,
            artifact_path=(
                Path(str(artifact_raw)) if isinstance(artifact_raw, str) and artifact_raw else None
            ),
            inherit_between_steps=_require_bool(wavefunction_raw, "inherit_between_steps", True),
            on_incompatible_basis=basis_policy,
            require_same_state_signature=_require_bool(
                wavefunction_raw, "require_same_state_signature", True
            ),
        )
    else:
        wavefunction = WavefunctionPolicy()

    diagnostics_raw = payload.get("diagnostics")
    if isinstance(diagnostics_raw, Mapping):
        raw_population = _require_str(
            diagnostics_raw, "population", PopulationAnalysis.MULLIKEN.value
        )
        try:
            population = PopulationAnalysis(raw_population)
        except ValueError:
            population = PopulationAnalysis.MULLIKEN
        raw_stability = _require_str(diagnostics_raw, "stability", StabilityMode.NONE.value)
        try:
            stability = StabilityMode(raw_stability)
        except ValueError:
            stability = StabilityMode.NONE
        diagnostics = SpinDiagnosticsSpec(
            population=population,
            stability=stability,
            write_spin_density=_require_bool(diagnostics_raw, "write_spin_density", False),
        )
    else:
        diagnostics = SpinDiagnosticsSpec()

    gate_raw = payload.get("quality_gate")
    if isinstance(gate_raw, Mapping):
        raw_collapse = _require_str(gate_raw, "collapse_policy", CollapsePolicy.WARNING.value)
        try:
            collapse_policy = CollapsePolicy(raw_collapse)
        except ValueError:
            collapse_policy = CollapsePolicy.WARNING
        threshold = _require_float(gate_raw, "spin_center_threshold")
        quality_gate = SpinQualityGate(
            collapse_policy=collapse_policy,
            s2_min=_require_float(gate_raw, "s2_min"),
            s2_max=_require_float(gate_raw, "s2_max"),
            require_opposite_spin_centers=_require_bool(
            gate_raw, "require_opposite_spin_centers", False
        ),
            spin_center_threshold=(
                threshold if threshold is not None and threshold > 0 else 0.05
            ),
        )
    else:
        quality_gate = SpinQualityGate()

    return ElectronicStateSpec(
        state_id=state_id,
        label=_require_str(payload, "label"),
        target_multiplicity=target_multiplicity,
        spin_mode=spin_mode,
        guess=guess_spec_from_dict(
            payload.get("guess") if isinstance(payload.get("guess"), Mapping) else None
        ),
        spatial_symmetry=spatial_symmetry,
        wavefunction=wavefunction,
        diagnostics=diagnostics,
        quality_gate=quality_gate,
        reference_state_id=_require_str(payload, "reference_state_id"),
    )


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


def casscf_spec_from_dict(payload: Mapping[str, object] | None) -> CASSCFSpec:
    """Parse a :class:`CASSCFSpec` from a mapping; raises on missing space."""
    if not payload:
        message = "CASSCF requires an active-space definition (active_electrons/active_orbitals)"
        raise ValueError(message)
    active_electrons = _require_int(payload, "active_electrons")
    active_orbitals = _require_int(payload, "active_orbitals")
    if active_electrons is None or active_orbitals is None:
        message = (
            "CASSCF requires positive active_electrons and active_orbitals "
            "(missing active-space information)"
        )
        raise ValueError(message)
    raw_dc = _require_str(payload, "dynamic_correlation", DynamicCorrelation.NONE.value)
    try:
        dynamic_correlation = DynamicCorrelation(raw_dc)
    except ValueError as exc:
        allowed = ", ".join(d.value for d in DynamicCorrelation)
        message = f"unknown dynamic_correlation {raw_dc!r}; expected one of: {allowed}"
        raise ValueError(message) from exc
    orbital_source = payload.get("orbital_source")
    nroots = _require_int(payload, "nroots", 1) or 1
    weights = _float_tuple(payload, "state_weights")
    return CASSCFSpec(
        active_electrons=active_electrons,
        active_orbitals=active_orbitals,
        multiplicity=_require_int(payload, "multiplicity", 1) or 1,
        nroots=nroots,
        state_weights=weights,
        orbital_source=(
        Path(str(orbital_source)) if isinstance(orbital_source, str) and orbital_source else None
    ),
        active_orbital_indices=_int_tuple(payload, "active_orbital_indices"),
        orbital_selection=_require_str(payload, "orbital_selection", "manual"),
        dynamic_correlation=dynamic_correlation,
        frozen_core=_require_bool(payload, "frozen_core", True),
        max_iterations=_require_int(payload, "max_iterations"),
    )


# ── electronic-state semantics helpers ──────────────────────────────────


def orca_xyz_multiplicity(state: ElectronicStateSpec) -> int:
    """Multiplicity written on the ORCA ``* xyz`` line (§3.1, §9.4).

    FlipSpin states converge a high-spin reference first, so the input
    reference multiplicity differs from the target physical multiplicity.
    """
    if state.guess.strategy is GuessStrategy.FLIPSPIN and state.guess.reference_multiplicity:
        return int(state.guess.reference_multiplicity)
    return int(state.target_multiplicity)


def expected_s2_for_multiplicity(multiplicity: int) -> float:
    """Ideal ``<S²>`` = S(S+1) with S = (M-1)/2."""
    s = (int(multiplicity) - 1) / 2.0
    return s * (s + 1.0)


def electron_parity_ok(n_electrons: int, multiplicity: int) -> bool:
    """Electron count and multiplicity must have compatible parity (§13.1)."""
    return (int(n_electrons) - (int(multiplicity) - 1)) % 2 == 0


def state_signature(state: ElectronicStateSpec, method: str = "", basis: str = "") -> JsonObject:
    """Compatibility signature for automatic ``.gbw`` inheritance (§5.4)."""
    return {
        "state_id": state.state_id,
        "target_multiplicity": state.target_multiplicity,
        "reference_multiplicity": state.guess.reference_multiplicity
        if state.guess.strategy is GuessStrategy.FLIPSPIN
        else None,
        "spin_mode": state.spin_mode.value,
        "guess_strategy": state.guess.strategy.value,
        "flip_atoms": list(state.guess.flip_atoms),
        "atom_index_base": int(state.guess.atom_index_base),
        "method": method,
        "basis": basis,
    }


def validate_electronic_state(
    config: ElectronicStateConfig,
    *,
    backend: str = "orca",
    n_electrons: int | None = None,
    n_atoms: int | None = None,
) -> ElectronicStateValidation:
    """Validate an electronic-state module (design doc §13).

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

        if state.target_multiplicity < 1:
            errors.append(f"{prefix}: target_multiplicity must be a positive integer")

        if n_electrons is not None and state.target_multiplicity >= 1:
            if not electron_parity_ok(n_electrons, state.target_multiplicity):
                errors.append(
                    f"{prefix}: {n_electrons} electrons are incompatible with "
                    f"multiplicity {state.target_multiplicity} (parity mismatch)"
                )

        if state.spin_mode is SpinMode.RESTRICTED and state.target_multiplicity > 1:
            warnings.append(
                f"{prefix}: restricted open-shell (ROHF/ROKS) is only valid when "
                "the backend explicitly supports it"
            )

        if state.spin_mode is SpinMode.BROKEN_SYMMETRY:
            if backend != "orca":
                errors.append(f"{prefix}: broken_symmetry is only supported on the ORCA backend")
            if state.guess.strategy is GuessStrategy.DEFAULT:
                warnings.append(
                    f"{prefix}: broken-symmetry state without GuessMix/FlipSpin/"
                    "BrokenSym/MORead guess — submit-time warning (§15.3)"
                )
            if state.spatial_symmetry is SpatialSymmetryMode.PRESERVE:
                warnings.append(
                    f"{prefix}: BS state usually requires spatial_symmetry=disable"
                )

        guess = state.guess
        if guess.strategy is GuessStrategy.FLIPSPIN:
            reference = guess.reference_multiplicity
            if reference is None or reference <= state.target_multiplicity:
                errors.append(
                    f"{prefix}: flipspin requires reference_multiplicity > "
                    f"target_multiplicity ({state.target_multiplicity})"
                )
            if guess.final_ms is None:
                errors.append(f"{prefix}: flipspin requires final_ms (or a derivation rule)")
            if not guess.flip_atoms:
                errors.append(f"{prefix}: flipspin requires a non-empty flip_atoms list")
            if n_atoms is not None:
                base = int(guess.atom_index_base)
                for atom in guess.flip_atoms:
                    zero_based = atom - base
                    if zero_based < 0 or zero_based >= n_atoms:
                        errors.append(
                            f"{prefix}: flip atom {atom} (base {base}) is outside "
                            f"the structure range 1..{n_atoms}"
                        )

        if guess.strategy is GuessStrategy.GUESSMIX:
            if state.spin_mode not in (SpinMode.UNRESTRICTED, SpinMode.BROKEN_SYMMETRY):
                errors.append(f"{prefix}: guessmix requires an unrestricted spin_mode")
            angle = guess.guess_mix_angle
            if not 0.0 < angle < 90.0:
                warnings.append(
                    f"{prefix}: guess_mix_angle {angle} outside the recommended (0, 90)"
                )

        if guess.strategy is GuessStrategy.BROKEN_SYM:
            if guess.broken_sym_na is None or guess.broken_sym_nb is None:
                errors.append(
                    f"{prefix}: broken_sym guess requires broken_sym_na and broken_sym_nb"
                )

    if (
        config.execution_mode is ElectronicStateExecutionMode.STATE_SWEEP
        and len(config.states) < 2
    ):
        errors.append("state_sweep execution requires at least two states")

    if config.default_state_id and config.default_state_id not in seen_ids:
        errors.append(
            f"default_state_id {config.default_state_id!r} does not match any state_id"
        )

    for state in config.states:
        if state.reference_state_id and state.reference_state_id not in seen_ids:
            errors.append(
                f"state {state.state_id!r} references unknown reference_state_id "
                f"{state.reference_state_id!r}"
            )

    return ElectronicStateValidation(errors, warnings)


def validate_casscf_spec(spec: CASSCFSpec, *, n_electrons: int | None = None) -> list[str]:
    """Validate a CASSCF contract (design doc §13.5)."""
    errors: list[str] = []
    if spec.active_electrons <= 0:
        errors.append("CASSCF active_electrons must be > 0")
    if spec.active_orbitals <= 0:
        errors.append("CASSCF active_orbitals must be > 0")
    if spec.active_electrons > 2 * spec.active_orbitals:
        errors.append(
            f"CASSCF active_electrons ({spec.active_electrons}) cannot exceed "
            f"2 x active_orbitals ({2 * spec.active_orbitals})"
        )
    if len(spec.active_orbital_indices) not in (0, spec.active_orbitals):
        errors.append(
            f"CASSCF active_orbital_indices count ({len(spec.active_orbital_indices)}) "
            f"must equal active_orbitals ({spec.active_orbitals})"
        )
    if spec.nroots < 1:
        errors.append("CASSCF nroots must be >= 1")
    if spec.state_weights:
        if len(spec.state_weights) != spec.nroots:
            errors.append("CASSCF state_weights length must equal nroots")
        total = sum(spec.state_weights)
        if abs(total - 1.0) > 1e-6:
            errors.append(f"CASSCF state_weights must sum to 1.0 (got {total})")
    if spec.multiplicity < 1:
        errors.append("CASSCF multiplicity must be a positive integer")
    if n_electrons is not None and not electron_parity_ok(n_electrons, spec.multiplicity):
        errors.append(
            f"CASSCF multiplicity {spec.multiplicity} is incompatible with "
            f"{n_electrons} electrons (parity mismatch)"
        )
    if n_electrons is not None and spec.active_electrons > n_electrons:
        errors.append(
            f"CASSCF active_electrons ({spec.active_electrons}) exceeds the total "
            f"electron count ({n_electrons})"
        )
    return errors


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
    "state_signature",
    "validate_casscf_spec",
    "validate_electronic_state",
    "validate_plan",
]
