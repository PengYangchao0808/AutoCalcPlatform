"""Scientific calculation contracts: single-structure / single-state / single-run.

Field-duty split (todo 11): this module holds ONLY scientific contract
types — single-structure geometry, one electronic state, single-execution
parameters, and scientific artifact references — plus their scientific
validation/serialization functions.  Platform identity (workflow / profile /
candidate id / trajectory item id / state-sweep orchestration), calculation
plans, step kinds, manifests, and checkpoints stay in the ACP compatibility
contracts (``acp.calculations.contracts``).

This module is a pure-type seam: importing it must never pull in task
execution modules or ``cccp.qc`` interfaces.  The current API specification
lives in ``docs/ACP_CCCP_Task_API_DevDoc.md``.

Author: QCcalc Team
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal, TypeAlias

from cccp.calculation.errors import TaskInputError

logger = logging.getLogger(__name__)

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class StructureRole(str, Enum):
    """Role of a structure in a calculation workflow."""

    MINIMUM = "minimum"
    TRANSITION_STATE = "transition_state"


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


@dataclass(frozen=True, slots=True)
class StructureArtifact:
    """Structure path and scientific identity metadata for one structure.

    Deliberately free of platform identity (no candidate id): candidate
    association is ACP-side binding information (see
    ``acp.calculations.legacy_adapters.LegacyBinding``).
    """

    path: Path
    elements: list[str] = field(default_factory=list)
    role: StructureRole = StructureRole.MINIMUM
    source: str = ""

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
class ArtifactRef:
    """Reference to a persisted file produced by a calculation."""

    path: Path
    type: str
    checksum: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))


@dataclass(frozen=True, slots=True)
class Provenance:
    """Backend and input identity attached to a calculation result.

    Deliberately free of platform identity (no profile): profile is
    ACP-side binding information and is never part of a task result.
    """

    backend: str
    method: str
    version: str
    input_signature: str


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
                "require_same_state_signature": (spec.wavefunction.require_same_state_signature),
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
                "require_opposite_spin_centers": (spec.quality_gate.require_opposite_spin_centers),
                "spin_center_threshold": spec.quality_gate.spin_center_threshold,
            }
        )
    return payload


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
            Path(str(orbital_source))
            if isinstance(orbital_source, str) and orbital_source
            else None
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
            spin_center_threshold=(threshold if threshold is not None and threshold > 0 else 0.05),
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
            Path(str(orbital_source))
            if isinstance(orbital_source, str) and orbital_source
            else None
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


def validate_electronic_state_spec(
    state: ElectronicStateSpec,
    *,
    backend: str = "orca",
    n_electrons: int | None = None,
    n_atoms: int | None = None,
) -> ElectronicStateValidation:
    """Scientifically validate one electronic state (design doc §13).

    Per-state checks only (parity, guess construction, spin-mode
    consistency).  Set-level orchestration checks (duplicate state ids,
    state-sweep arity, default-state selection) live with the ACP-side
    state-set config.

    Args:
        state: The target state to validate.
        backend: Target QC backend (BS is ORCA-only in first phase).
        n_electrons: Total electron count when known (parity check).
        n_atoms: Atom count when known (FlipSpin range check).

    Returns:
        Split errors/warnings; only errors block submission (§15.3).
    """
    errors: list[str] = []
    warnings: list[str] = []
    prefix = f"state {state.state_id!r}"

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
            warnings.append(f"{prefix}: BS state usually requires spatial_symmetry=disable")

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
            warnings.append(f"{prefix}: guess_mix_angle {angle} outside the recommended (0, 90)")

    if guess.strategy is GuessStrategy.BROKEN_SYM:
        if guess.broken_sym_na is None or guess.broken_sym_nb is None:
            errors.append(f"{prefix}: broken_sym guess requires broken_sym_na and broken_sym_nb")

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


# ── strict envelope serialization helpers (TaskRequest/TaskResult) ──────
#
# These implement the documented serialization rules of
# ``docs/ACP_CCCP_Task_API_DevDoc.md`` (strict enums, finite floats,
# schema-version checks).  They are deliberately separate from the
# permissive legacy ``*_from_dict`` parsers above, whose coercion semantics
# are preserved unchanged for ACP compatibility consumers.


def ensure_finite_json_number(value: object, label: str) -> float:
    """Return ``value`` as a finite float; NaN/Inf are rejected.

    Raises:
        TaskInputError: If the value is not a finite number.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        message = f"{label} must be a number, got {type(value).__name__}"
        raise TaskInputError(message)
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        message = f"{label} must be finite (JSON has no NaN/Inf)"
        raise TaskInputError(message)
    return number


def ensure_finite_payload(payload: Mapping[str, object], label: str) -> None:
    """Recursively reject NaN/Inf floats anywhere inside a mapping/list."""
    for key, value in payload.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            ensure_finite_json_number(value, f"{label}.{key}")
        elif isinstance(value, Mapping):
            ensure_finite_payload(value, f"{label}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    ensure_finite_json_number(item, f"{label}.{key}[{index}]")
                elif isinstance(item, Mapping):
                    ensure_finite_payload(item, f"{label}.{key}[{index}]")


def parse_enum_strict(enum_cls: type[Enum], value: object, label: str) -> Enum:
    """Parse an enum member strictly; unknown values are rejected.

    Raises:
        TaskInputError: On an invalid enum value or a non-string value.
    """
    if not isinstance(value, str):
        message = f"{label} must be a string, got {type(value).__name__}"
        raise TaskInputError(message)
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_cls)
        message = f"{label} must be one of: {allowed}; got {value!r}"
        raise TaskInputError(message) from exc


def parse_int_strict(payload: Mapping[str, object], key: str) -> int | None:
    """Parse a strict optional int field; ``None``/missing means absent."""
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float) and float(value).is_integer():
            return int(value)
        message = f"{key} must be an integer, got {type(value).__name__}"
        raise TaskInputError(message)
    return int(value)


def parse_float_strict(payload: Mapping[str, object], key: str) -> float | None:
    """Parse a strict optional finite float field."""
    value = payload.get(key)
    if value is None:
        return None
    return ensure_finite_json_number(value, key)


def parse_bool_strict(payload: Mapping[str, object], key: str) -> bool | None:
    """Parse a strict optional bool field."""
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        message = f"{key} must be a boolean, got {type(value).__name__}"
        raise TaskInputError(message)
    return value


def parse_str_strict(payload: Mapping[str, object], key: str) -> str | None:
    """Parse a strict optional string field."""
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        message = f"{key} must be a string, got {type(value).__name__}"
        raise TaskInputError(message)
    return value


def parse_path_strict(payload: Mapping[str, object], key: str) -> Path | None:
    """Parse a strict optional path field (serialised as a string)."""
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, Path):
        return value
    if not isinstance(value, str):
        message = f"{key} must be a path string, got {type(value).__name__}"
        raise TaskInputError(message)
    return Path(value)


def parse_float_tuple_strict(payload: Mapping[str, object], key: str) -> tuple[float, ...]:
    """Parse a strict float-tuple field (JSON list of finite numbers)."""
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        message = f"{key} must be a list, got {type(value).__name__}"
        raise TaskInputError(message)
    return tuple(
        ensure_finite_json_number(item, f"{key}[{index}]") for index, item in enumerate(value)
    )


def parse_int_tuple_strict(payload: Mapping[str, object], key: str) -> tuple[int, ...]:
    """Parse a strict int-tuple field (JSON list of integers)."""
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        message = f"{key} must be a list, got {type(value).__name__}"
        raise TaskInputError(message)
    result: list[int] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int):
            message = f"{key}[{index}] must be an integer, got {type(item).__name__}"
            raise TaskInputError(message)
        result.append(int(item))
    return tuple(result)


def parse_str_tuple_strict(payload: Mapping[str, object], key: str) -> tuple[str, ...]:
    """Parse a strict string-tuple field (JSON list of strings)."""
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        message = f"{key} must be a list, got {type(value).__name__}"
        raise TaskInputError(message)
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            message = f"{key}[{index}] must be a string, got {type(item).__name__}"
            raise TaskInputError(message)
        result.append(item)
    return tuple(result)


__all__ = [
    "ArtifactRef",
    "CASSCFSpec",
    "CollapsePolicy",
    "DynamicCorrelation",
    "ElectronicStateSpec",
    "ElectronicStateValidation",
    "GuessSpec",
    "GuessStrategy",
    "IncompatibleBasisPolicy",
    "JsonObject",
    "JsonValue",
    "OptimizationMode",
    "OptimizationSpec",
    "PopulationAnalysis",
    "Provenance",
    "SpatialSymmetryMode",
    "SpinDiagnosticsSpec",
    "SpinMode",
    "SpinQualityGate",
    "StabilityMode",
    "StructureArtifact",
    "StructureRole",
    "WavefunctionPolicy",
    "WavefunctionSource",
    "casscf_spec_from_dict",
    "casscf_spec_to_dict",
    "electron_parity_ok",
    "electronic_state_spec_from_dict",
    "electronic_state_spec_to_dict",
    "ensure_finite_json_number",
    "ensure_finite_payload",
    "expected_s2_for_multiplicity",
    "guess_spec_from_dict",
    "guess_spec_to_dict",
    "orca_xyz_multiplicity",
    "parse_bool_strict",
    "parse_enum_strict",
    "parse_float_strict",
    "parse_float_tuple_strict",
    "parse_int_strict",
    "parse_int_tuple_strict",
    "parse_path_strict",
    "parse_str_strict",
    "parse_str_tuple_strict",
    "state_signature",
    "validate_casscf_spec",
    "validate_electronic_state_spec",
]
