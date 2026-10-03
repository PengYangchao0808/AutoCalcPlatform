"""Serializable task request envelope for the cccp calculation layer.

``TaskRequest`` is the serializable computation intent (input reference,
typed per-task options, resources).  Runtime concerns — resolved config,
work directory, progress sink — live in :mod:`cccp.calculation.context` and
are passed separately (``run_*(request, *, context=None)``).  Platform
identity (workflow / profile / candidate id / trajectory item id /
state-sweep) MUST NOT appear here; see
``docs/ACP_CCCP_Task_API_DevDoc.md`` for the authoritative spec.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import ClassVar, Literal, TypeAlias

from cccp.calculation.contracts import (
    CASSCFSpec,
    ElectronicStateSpec,
    JsonObject,
    OptimizationMode,
    StructureRole,
    casscf_spec_from_dict,
    casscf_spec_to_dict,
    electronic_state_spec_from_dict,
    electronic_state_spec_to_dict,
    ensure_finite_json_number,
    ensure_finite_payload,
    parse_bool_strict,
    parse_enum_strict,
    parse_float_strict,
    parse_float_tuple_strict,
    parse_int_strict,
    parse_int_tuple_strict,
    parse_path_strict,
    parse_str_strict,
    parse_str_tuple_strict,
)
from cccp.calculation.errors import TaskInputError

TASK_REQUEST_SCHEMA_VERSION = 1


class TaskKind(str, Enum):
    """The seven core calculation tasks (P2 tasks extend this later)."""

    SINGLEPOINT = "singlepoint"
    OPTIMIZE = "optimize"
    FREQUENCY = "frequency"
    SCAN = "scan"
    IRC = "irc"
    CASSCF = "casscf"
    THERMOCHEMISTRY = "thermochemistry"


# ── request building blocks ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MethodSpec:
    """Backend-independent scientific level of theory (translation-layer input).

    Empty values are filled by the translation layer from the cccp method
    metadata (single source of defaults) — never by adapters or callers
    guessing defaults.
    """

    method: str = ""
    basis: str = ""
    dispersion: str | None = None
    solvent: str | None = None
    solvent_model: str | None = None
    integration_grid: str | None = None
    scf: str | None = None
    ri_approximation: str | None = None
    auxiliary_basis_j: str | None = None
    auxiliary_basis_c: str | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict (None fields omitted)."""
        payload: JsonObject = {"method": self.method, "basis": self.basis}
        for key, value in (
            ("dispersion", self.dispersion),
            ("solvent", self.solvent),
            ("solvent_model", self.solvent_model),
            ("integration_grid", self.integration_grid),
            ("scf", self.scf),
            ("ri_approximation", self.ri_approximation),
            ("auxiliary_basis_j", self.auxiliary_basis_j),
            ("auxiliary_basis_c", self.auxiliary_basis_c),
        ):
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> MethodSpec:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        values: dict[str, str | None] = {
            "method": parse_str_strict(payload, "method") or "",
            "basis": parse_str_strict(payload, "basis") or "",
        }
        for key in (
            "dispersion",
            "solvent",
            "solvent_model",
            "integration_grid",
            "scf",
            "ri_approximation",
            "auxiliary_basis_j",
            "auxiliary_basis_c",
        ):
            values[key] = parse_str_strict(payload, key)
        return cls(**values)


@dataclass(frozen=True, slots=True)
class StructureInput:
    """Single-structure input reference: structure file and/or inline geometry.

    Applicability rules (doc §"Input shapes"): the seven core tasks consume
    a single structure.  At execution, an inline geometry overrides the file
    when both are present (legacy ``load_inputs`` precedence).
    """

    path: Path | None = None
    coordinates: tuple[tuple[float, float, float], ...] | None = None
    symbols: tuple[str, ...] | None = None
    elements: tuple[str, ...] = ()
    role: StructureRole = StructureRole.MINIMUM
    source: str = ""

    def __post_init__(self) -> None:
        if self.path is not None:
            object.__setattr__(self, "path", Path(self.path))
        if self.coordinates is not None:
            object.__setattr__(
                self,
                "coordinates",
                tuple(tuple(float(c) for c in row) for row in self.coordinates),
            )
        if self.symbols is not None:
            object.__setattr__(self, "symbols", tuple(str(s) for s in self.symbols))
        object.__setattr__(self, "elements", tuple(str(e) for e in self.elements))
        try:
            role = StructureRole(self.role)
        except (TypeError, ValueError) as exc:
            allowed = ", ".join(r.value for r in StructureRole)
            message = f"role must be one of: {allowed}"
            raise ValueError(message) from exc
        object.__setattr__(self, "role", role)

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"role": self.role.value, "source": self.source}
        if self.path is not None:
            payload["path"] = str(self.path)
        if self.coordinates is not None:
            payload["coordinates"] = [[float(c) for c in row] for row in self.coordinates]
        if self.symbols is not None:
            payload["symbols"] = list(self.symbols)
        if self.elements:
            payload["elements"] = list(self.elements)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> StructureInput | None:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if payload is None:
            return None
        coordinates: tuple[tuple[float, float, float], ...] | None = None
        raw_coordinates = payload.get("coordinates")
        if raw_coordinates is not None:
            if not isinstance(raw_coordinates, list):
                message = "coordinates must be a list of 3-vectors"
                raise TaskInputError(message)
            rows: list[tuple[float, float, float]] = []
            for index, row in enumerate(raw_coordinates):
                if not isinstance(row, list) or len(row) != 3:
                    message = f"coordinates[{index}] must be a 3-vector"
                    raise TaskInputError(message)
                rows.append(
                    (
                        ensure_finite_json_number(row[0], f"coordinates[{index}][0]"),
                        ensure_finite_json_number(row[1], f"coordinates[{index}][1]"),
                        ensure_finite_json_number(row[2], f"coordinates[{index}][2]"),
                    )
                )
            coordinates = tuple(rows)
        symbols_raw = payload.get("symbols")
        symbols: tuple[str, ...] | None = None
        if symbols_raw is not None:
            symbols = parse_str_tuple_strict(payload, "symbols")
        role_raw = payload.get("role", StructureRole.MINIMUM.value)
        role = parse_enum_strict(StructureRole, role_raw, "structure.role")
        return cls(
            path=parse_path_strict(payload, "path"),
            coordinates=coordinates,
            symbols=symbols,
            elements=parse_str_tuple_strict(payload, "elements"),
            role=role,  # type: ignore[arg-type]
            source=parse_str_strict(payload, "source") or "",
        )


@dataclass(frozen=True, slots=True)
class TaskResources:
    """Per-task resource quota (batch budgets are separate — doc R5).

    ``maxcore`` is per-core memory in MB (ORCA ``%maxcore`` semantics);
    ``mem`` is the total task memory budget (int = MB, or strings such as
    ``"32GB"`` / ``"32000MB"``).  When all of ``nproc``/``maxcore``/``mem``
    are resolvable, ``nproc * maxcore <= mem_total_mb`` must hold.
    """

    nproc: int | None = None
    mem: str | int | None = None
    maxcore: int | None = None
    timeout_s: float | None = None

    def mem_total_mb(self) -> float | None:
        """Return the total memory budget in MB, or None when absent."""
        return parse_memory_mb(self.mem)

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict (None fields omitted)."""
        payload: JsonObject = {}
        if self.nproc is not None:
            payload["nproc"] = self.nproc
        if self.mem is not None:
            payload["mem"] = self.mem
        if self.maxcore is not None:
            payload["maxcore"] = self.maxcore
        if self.timeout_s is not None:
            payload["timeout_s"] = self.timeout_s
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> TaskResources:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        mem_raw = payload.get("mem")
        mem: str | int | None
        if mem_raw is None:
            mem = None
        elif isinstance(mem_raw, bool):
            message = "mem must be an int (MB) or a memory string"
            raise TaskInputError(message)
        elif isinstance(mem_raw, int):
            mem = mem_raw
        elif isinstance(mem_raw, float) and float(mem_raw).is_integer():
            mem = int(mem_raw)
        elif isinstance(mem_raw, str):
            mem = mem_raw
        else:
            message = "mem must be an int (MB) or a memory string"
            raise TaskInputError(message)
        timeout = parse_float_strict(payload, "timeout_s")
        return cls(
            nproc=parse_int_strict(payload, "nproc"),
            mem=mem,
            maxcore=parse_int_strict(payload, "maxcore"),
            timeout_s=timeout,
        )


def parse_memory_mb(mem: str | int | None) -> float | None:
    """Parse ``TaskResources.mem`` into total MB.

    Raises:
        TaskInputError: On an unparsable memory value.
    """
    if mem is None:
        return None
    if isinstance(mem, bool):
        message = "mem must be an int (MB) or a memory string"
        raise TaskInputError(message)
    if isinstance(mem, int):
        return float(mem)
    text = mem.strip().lower().replace(" ", "")
    if not text:
        message = "mem must not be empty"
        raise TaskInputError(message)
    multiplier = 1.0
    suffixes = (
        ("tb", 1024.0 * 1024.0),
        ("gb", 1024.0),
        ("mb", 1.0),
        ("t", 1024.0 * 1024.0),
        ("g", 1024.0),
        ("m", 1.0),
    )
    for suffix, factor in suffixes:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            multiplier = factor
            break
    try:
        number = float(text)
    except ValueError as exc:
        message = f"mem must be an int (MB) or a memory string, got {mem!r}"
        raise TaskInputError(message) from exc
    if number != number or number <= 0:
        message = f"mem must be positive and finite, got {mem!r}"
        raise TaskInputError(message)
    return number * multiplier


# ── per-task typed options (closed union) ───────────────────────────────


@dataclass(frozen=True, slots=True)
class SinglePointOptions:
    """Task-specific options for ``singlepoint``."""

    task: ClassVar[TaskKind] = TaskKind.SINGLEPOINT
    stability_check: bool | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {}
        if self.stability_check is not None:
            payload["stability_check"] = self.stability_check
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> SinglePointOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        return cls(stability_check=parse_bool_strict(payload, "stability_check"))


@dataclass(frozen=True, slots=True)
class TsSpec:
    """Transition-state mode-following control (replaces legacy ``ts_mode``)."""

    enabled: bool = False
    mode_index: int | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"enabled": self.enabled}
        if self.mode_index is not None:
            payload["mode_index"] = self.mode_index
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> TsSpec | None:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if payload is None:
            return None
        return cls(
            enabled=parse_bool_strict(payload, "enabled") or False,
            mode_index=parse_int_strict(payload, "mode_index"),
        )


@dataclass(frozen=True, slots=True)
class RescueSpec:
    """Rescue-retry policy for geometry optimization.

    ``failure_type`` is the caller-supplied restore input (v3.1 §4);
    derived failure diagnostics land in the result payload instead.
    """

    policy: str = "adaptive"
    max_rescue: int | None = None
    failure_type: str | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"policy": self.policy}
        if self.max_rescue is not None:
            payload["max_rescue"] = self.max_rescue
        if self.failure_type is not None:
            payload["failure_type"] = self.failure_type
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> RescueSpec | None:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if payload is None:
            return None
        return cls(
            policy=parse_str_strict(payload, "policy") or "adaptive",
            max_rescue=parse_int_strict(payload, "max_rescue"),
            failure_type=parse_str_strict(payload, "failure_type"),
        )


@dataclass(frozen=True, slots=True)
class OptimizeOptions:
    """Task-specific options for ``optimize``."""

    task: ClassVar[TaskKind] = TaskKind.OPTIMIZE
    mode: OptimizationMode = OptimizationMode.UNCONSTRAINED
    initial_hessian: str | None = None
    recalc_hess: int | None = None
    trust_radius: float | None = None
    max_cycles: int | None = None
    geom_maxiter: int | None = None
    ts: TsSpec | None = None
    rescue: RescueSpec | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"mode": self.mode.value}
        for key, value in (
            ("initial_hessian", self.initial_hessian),
            ("recalc_hess", self.recalc_hess),
            ("trust_radius", self.trust_radius),
            ("max_cycles", self.max_cycles),
            ("geom_maxiter", self.geom_maxiter),
        ):
            if value is not None:
                payload[key] = value
        if self.ts is not None:
            payload["ts"] = self.ts.to_dict()
        if self.rescue is not None:
            payload["rescue"] = self.rescue.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> OptimizeOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        default_mode = OptimizationMode.UNCONSTRAINED
        mode = parse_enum_strict(
            OptimizationMode, payload.get("mode", default_mode.value), "options.mode"
        )
        raw_ts = payload.get("ts")
        raw_rescue = payload.get("rescue")
        if raw_ts is not None and not isinstance(raw_ts, Mapping):
            message = "options.ts must be a mapping"
            raise TaskInputError(message)
        if raw_rescue is not None and not isinstance(raw_rescue, Mapping):
            message = "options.rescue must be a mapping"
            raise TaskInputError(message)
        return cls(
            mode=mode,  # type: ignore[arg-type]
            initial_hessian=parse_str_strict(payload, "initial_hessian"),
            recalc_hess=parse_int_strict(payload, "recalc_hess"),
            trust_radius=parse_float_strict(payload, "trust_radius"),
            max_cycles=parse_int_strict(payload, "max_cycles"),
            geom_maxiter=parse_int_strict(payload, "geom_maxiter"),
            ts=TsSpec.from_dict(raw_ts),
            rescue=RescueSpec.from_dict(raw_rescue),
        )


@dataclass(frozen=True, slots=True)
class FrequencyOptions:
    """Task-specific options for ``frequency``.

    Empty in schema v1: numerical differentiation is not promised (v2 §8)
    and normal-mode frame products are built by the ACP layer.
    """

    task: ClassVar[TaskKind] = TaskKind.FREQUENCY

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> FrequencyOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        return cls()


class ScanMode(str, Enum):
    """Scan relaxation mode. ``rigid`` is reserved and rejected in v1."""

    RELAXED = "relaxed"


@dataclass(frozen=True, slots=True)
class ScanCoordinateSpec:
    """One scan coordinate (distance/angle/dihedral) with explicit index base."""

    atoms: tuple[int, ...] = ()
    start: float | None = None
    end: float | None = None
    kind: str = "distance"
    atom_index_base: Literal[0, 1] = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "atoms", tuple(int(a) for a in self.atoms))
        if self.atom_index_base not in (0, 1):
            message = "atom_index_base must be 0 or 1"
            raise ValueError(message)

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {
            "atoms": list(self.atoms),
            "kind": self.kind,
            "atom_index_base": self.atom_index_base,
        }
        if self.start is not None:
            payload["start"] = self.start
        if self.end is not None:
            payload["end"] = self.end
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ScanCoordinateSpec:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        base_raw = payload.get("atom_index_base", 1)
        if isinstance(base_raw, bool) or not isinstance(base_raw, int) or base_raw not in (0, 1):
            message = "atom_index_base must be 0 or 1"
            raise TaskInputError(message)
        return cls(
            atoms=parse_int_tuple_strict(payload, "atoms"),
            start=parse_float_strict(payload, "start"),
            end=parse_float_strict(payload, "end"),
            kind=parse_str_strict(payload, "kind") or "distance",
            atom_index_base=base_raw,  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class ScanOptions:
    """Task-specific options for ``scan``.

    ``values`` carries an explicit grid for a single coordinate; coupled
    multi-coordinate grids stay in the ACP PES layer and are passed as
    explicit ``values`` per request.
    """

    task: ClassVar[TaskKind] = TaskKind.SCAN
    coordinates: tuple[ScanCoordinateSpec, ...] = ()
    points: int | None = None
    values: tuple[float, ...] = ()
    mode: ScanMode = ScanMode.RELAXED

    def __post_init__(self) -> None:
        object.__setattr__(self, "coordinates", tuple(self.coordinates))
        object.__setattr__(self, "values", tuple(float(v) for v in self.values))
        object.__setattr__(self, "mode", ScanMode(self.mode))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {
            "coordinates": [coordinate.to_dict() for coordinate in self.coordinates],
            "mode": self.mode.value,
        }
        if self.points is not None:
            payload["points"] = self.points
        if self.values:
            payload["values"] = list(self.values)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> ScanOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        default_mode = ScanMode.RELAXED
        mode = parse_enum_strict(ScanMode, payload.get("mode", default_mode.value), "options.mode")
        raw_coordinates = payload.get("coordinates", [])
        if not isinstance(raw_coordinates, list):
            message = "options.coordinates must be a list"
            raise TaskInputError(message)
        coordinates: list[ScanCoordinateSpec] = []
        for index, entry in enumerate(raw_coordinates):
            if not isinstance(entry, Mapping):
                message = f"options.coordinates[{index}] must be a mapping"
                raise TaskInputError(message)
            coordinates.append(ScanCoordinateSpec.from_dict(entry))
        return cls(
            coordinates=tuple(coordinates),
            points=parse_int_strict(payload, "points"),
            values=parse_float_tuple_strict(payload, "values"),
            mode=mode,  # type: ignore[arg-type]
        )


class IrcDirection(str, Enum):
    """One IRC integration direction."""

    FORWARD = "forward"
    REVERSE = "reverse"


@dataclass(frozen=True, slots=True)
class IrcOptions:
    """Task-specific options for ``irc`` (no ``ts_mode`` — v3.2 §6)."""

    task: ClassVar[TaskKind] = TaskKind.IRC
    directions: tuple[IrcDirection, ...] = (IrcDirection.FORWARD, IrcDirection.REVERSE)
    maxpoints: int | None = None
    step: float | None = None
    initial_hessian: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "directions", tuple(IrcDirection(d) for d in self.directions))
        if not self.directions:
            message = "irc requires at least one direction"
            raise ValueError(message)
        if len(set(self.directions)) != len(self.directions):
            message = "irc directions must be unique"
            raise ValueError(message)

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"directions": [d.value for d in self.directions]}
        for key, value in (
            ("maxpoints", self.maxpoints),
            ("step", self.step),
            ("initial_hessian", self.initial_hessian),
        ):
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> IrcOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_directions = payload.get("directions", [d.value for d in IrcDirection])
        if not isinstance(raw_directions, list):
            message = "options.directions must be a list"
            raise TaskInputError(message)
        directions = tuple(
            parse_enum_strict(IrcDirection, entry, "options.directions") for entry in raw_directions
        )
        return cls(
            directions=directions,  # type: ignore[arg-type]
            maxpoints=parse_int_strict(payload, "maxpoints"),
            step=parse_float_strict(payload, "step"),
            initial_hessian=parse_str_strict(payload, "initial_hessian"),
        )


@dataclass(frozen=True, slots=True)
class CasscfOptions:
    """Task-specific options for ``casscf`` (wraps the active-space spec)."""

    task: ClassVar[TaskKind] = TaskKind.CASSCF
    spec: CASSCFSpec

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {"spec": casscf_spec_to_dict(self.spec)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> CasscfOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            message = "casscf options require an active-space definition"
            raise TaskInputError(message)
        raw_spec = payload.get("spec")
        if not isinstance(raw_spec, Mapping):
            message = "options.spec must be a mapping"
            raise TaskInputError(message)
        try:
            return cls(spec=casscf_spec_from_dict(raw_spec))
        except ValueError as exc:
            raise TaskInputError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class ThermochemistryOptions:
    """Task-specific options for ``thermochemistry`` (input = frequency log).

    ``standard_state`` is ``"1atm"`` or ``"1M"``; ``None`` means the
    contract default ``"1atm"`` is applied at execution (empty-value
    semantics — the adapter never fills it).
    """

    task: ClassVar[TaskKind] = TaskKind.THERMOCHEMISTRY
    freq_log_path: Path | None = None
    sp_energy_hartree: float | None = None
    temperature_k: float | None = None
    pressure_atm: float | None = None
    standard_state: str | None = None
    scl_zpe: float | None = None
    ilowfreq: int | None = None
    imagreal: int | None = None
    conc: float | None = None

    def __post_init__(self) -> None:
        if self.freq_log_path is not None:
            object.__setattr__(self, "freq_log_path", Path(self.freq_log_path))
        if self.standard_state is not None and self.standard_state not in ("1atm", "1M"):
            message = "standard_state must be '1atm' or '1M'"
            raise ValueError(message)

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {}
        for key, value in (
            ("freq_log_path", str(self.freq_log_path) if self.freq_log_path is not None else None),
            ("sp_energy_hartree", self.sp_energy_hartree),
            ("temperature_k", self.temperature_k),
            ("pressure_atm", self.pressure_atm),
            ("standard_state", self.standard_state),
            ("scl_zpe", self.scl_zpe),
            ("ilowfreq", self.ilowfreq),
            ("imagreal", self.imagreal),
            ("conc", self.conc),
        ):
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> ThermochemistryOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        standard_state = parse_str_strict(payload, "standard_state")
        try:
            return cls(
                freq_log_path=parse_path_strict(payload, "freq_log_path"),
                sp_energy_hartree=parse_float_strict(payload, "sp_energy_hartree"),
                temperature_k=parse_float_strict(payload, "temperature_k"),
                pressure_atm=parse_float_strict(payload, "pressure_atm"),
                standard_state=standard_state,
                scl_zpe=parse_float_strict(payload, "scl_zpe"),
                ilowfreq=parse_int_strict(payload, "ilowfreq"),
                imagreal=parse_int_strict(payload, "imagreal"),
                conc=parse_float_strict(payload, "conc"),
            )
        except ValueError as exc:
            raise TaskInputError(str(exc)) from exc


TaskOptions: TypeAlias = (
    SinglePointOptions
    | OptimizeOptions
    | FrequencyOptions
    | ScanOptions
    | IrcOptions
    | CasscfOptions
    | ThermochemistryOptions
)

# Table ① (todo 11): task → options/payload types.  The task → execution
# function table is completed by todos 17–22 / 42–43 and is intentionally
# absent here (this todo claims no executability).
TASK_OPTIONS_TYPES: dict[TaskKind, type] = {
    TaskKind.SINGLEPOINT: SinglePointOptions,
    TaskKind.OPTIMIZE: OptimizeOptions,
    TaskKind.FREQUENCY: FrequencyOptions,
    TaskKind.SCAN: ScanOptions,
    TaskKind.IRC: IrcOptions,
    TaskKind.CASSCF: CasscfOptions,
    TaskKind.THERMOCHEMISTRY: ThermochemistryOptions,
}


def options_to_dict(options: TaskOptions | None) -> JsonObject | None:
    """Serialise typed options to their JSON-safe dict form."""
    if options is None:
        return None
    return options.to_dict()


def options_from_dict(task: TaskKind, payload: Mapping[str, object] | None) -> TaskOptions | None:
    """Parse typed options for ``task``; type mismatch raises TaskInputError."""
    if payload is None:
        return None
    options_cls = TASK_OPTIONS_TYPES[task]
    try:
        return options_cls.from_dict(payload)
    except TaskInputError:
        raise
    except ValueError as exc:
        raise TaskInputError(str(exc)) from exc


# ── TaskRequest envelope ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TaskRequest:
    """Serializable computation intent for exactly one task execution.

    Runtime state (config / workdir / progress) is NOT part of a request —
    see :class:`cccp.calculation.context.TaskContext`.
    """

    task: TaskKind
    structure: StructureInput | None = None
    charge: int = 0
    multiplicity: int = 1
    level: MethodSpec = field(default_factory=MethodSpec)
    backend: str | None = None
    electronic_state: ElectronicStateSpec | None = None
    options: TaskOptions | None = None
    resources: TaskResources = field(default_factory=TaskResources)
    output_dir: Path | None = None
    schema_version: int = TASK_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        try:
            task = TaskKind(self.task)
        except ValueError as exc:
            allowed = ", ".join(kind.value for kind in TaskKind)
            message = f"task must be one of: {allowed}"
            raise ValueError(message) from exc
        object.__setattr__(self, "task", task)
        object.__setattr__(self, "charge", int(self.charge))
        object.__setattr__(self, "multiplicity", int(self.multiplicity))
        if self.output_dir is not None:
            object.__setattr__(self, "output_dir", Path(self.output_dir))
        object.__setattr__(self, "schema_version", int(self.schema_version))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict (rule S4: NaN/Inf rejected)."""
        payload: JsonObject = {
            "schema_version": self.schema_version,
            "task": self.task.value,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "level": self.level.to_dict(),
            "resources": self.resources.to_dict(),
        }
        if self.structure is not None:
            payload["structure"] = self.structure.to_dict()
        if self.backend is not None:
            payload["backend"] = self.backend
        if self.electronic_state is not None:
            payload["electronic_state"] = electronic_state_spec_to_dict(self.electronic_state)
        options_payload = options_to_dict(self.options)
        if options_payload is not None:
            payload["options"] = options_payload
        if self.output_dir is not None:
            payload["output_dir"] = str(self.output_dir)
        ensure_finite_payload(payload, "TaskRequest")
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> TaskRequest:
        """Parse strictly (rules S1–S4); unknown fields are ignored (S2).

        Raises:
            TaskInputError: On unknown schema versions, invalid enums,
                type mismatches, or non-finite floats.
        """
        if not isinstance(payload, Mapping):
            message = "TaskRequest payload must be a mapping"
            raise TaskInputError(message)
        ensure_finite_payload(payload, "TaskRequest")
        version = payload.get("schema_version", TASK_REQUEST_SCHEMA_VERSION)
        if isinstance(version, bool) or not isinstance(version, int):
            message = "schema_version must be an integer"
            raise TaskInputError(message)
        if version != TASK_REQUEST_SCHEMA_VERSION:
            message = (
                f"unsupported schema_version {version}; expected {TASK_REQUEST_SCHEMA_VERSION}"
            )
            raise TaskInputError(message)
        task = parse_enum_strict(TaskKind, payload.get("task"), "task")
        raw_structure = payload.get("structure")
        if raw_structure is not None and not isinstance(raw_structure, Mapping):
            message = "structure must be a mapping"
            raise TaskInputError(message)
        raw_level = payload.get("level")
        if raw_level is not None and not isinstance(raw_level, Mapping):
            message = "level must be a mapping"
            raise TaskInputError(message)
        raw_resources = payload.get("resources")
        if raw_resources is not None and not isinstance(raw_resources, Mapping):
            message = "resources must be a mapping"
            raise TaskInputError(message)
        raw_state = payload.get("electronic_state")
        electronic_state: ElectronicStateSpec | None = None
        if raw_state is not None:
            if not isinstance(raw_state, Mapping):
                message = "electronic_state must be a mapping"
                raise TaskInputError(message)
            try:
                electronic_state = electronic_state_spec_from_dict(raw_state)
            except ValueError as exc:
                raise TaskInputError(str(exc)) from exc
        raw_options = payload.get("options")
        if raw_options is not None and not isinstance(raw_options, Mapping):
            message = "options must be a mapping"
            raise TaskInputError(message)
        request = cls(
            task=task,  # type: ignore[arg-type]
            structure=StructureInput.from_dict(raw_structure),
            charge=parse_int_strict(payload, "charge") or 0,
            multiplicity=parse_int_strict(payload, "multiplicity") or 1,
            level=MethodSpec.from_dict(raw_level),
            backend=parse_str_strict(payload, "backend"),
            electronic_state=electronic_state,
            options=options_from_dict(task, raw_options),  # type: ignore[arg-type]
            resources=TaskResources.from_dict(raw_resources),
            output_dir=parse_path_strict(payload, "output_dir"),
            schema_version=version,
        )
        validate_request(request)
        return request


def validate_request(request: TaskRequest) -> None:
    """Validate envelope-level invariants of a task request.

    Checks the typed-options registry match, input-shape applicability,
    resource quota sanity (``mem`` vs ``maxcore``), and the ban on
    ``backend="auto"``.  Task-specific scientific constraints are validated
    by the task implementations (todos 17–22).

    Raises:
        TaskInputError: On any envelope-level violation.
    """
    if request.schema_version != TASK_REQUEST_SCHEMA_VERSION:
        message = (
            f"unsupported schema_version {request.schema_version}; "
            f"expected {TASK_REQUEST_SCHEMA_VERSION}"
        )
        raise TaskInputError(message)

    if request.backend is not None and request.backend.strip().lower() == "auto":
        message = (
            "backend='auto' is not a value; pass an explicit backend name "
            "or None for deterministic selection"
        )
        raise TaskInputError(message)

    options_cls = TASK_OPTIONS_TYPES[request.task]
    if request.options is not None and not isinstance(request.options, options_cls):
        message = (
            f"options type mismatch for task {request.task.value!r}: "
            f"expected {options_cls.__name__}, got {type(request.options).__name__}"
        )
        raise TaskInputError(message)

    # input-shape applicability (doc §"Input shapes")
    if request.task is TaskKind.THERMOCHEMISTRY:
        if request.structure is not None:
            message = "thermochemistry takes no structure input (frequency log only)"
            raise TaskInputError(message)
        if not isinstance(request.options, ThermochemistryOptions):
            message = "thermochemistry requires ThermochemistryOptions"
            raise TaskInputError(message)
        if request.options.freq_log_path is None:
            message = "thermochemistry requires options.freq_log_path (frequency log input)"
            raise TaskInputError(message)
    else:
        if request.structure is None:
            message = f"task {request.task.value!r} requires a structure input"
            raise TaskInputError(message)
        has_inline = (
            request.structure.coordinates is not None and request.structure.symbols is not None
        )
        if request.structure.path is None and not has_inline:
            message = "structure input requires a path or inline coordinates+symbols"
            raise TaskInputError(message)

    # resource quota sanity (doc §"Resource semantics")
    for name, value in (
        ("nproc", request.resources.nproc),
        ("maxcore", request.resources.maxcore),
    ):
        if value is not None and value <= 0:
            message = f"resources.{name} must be positive"
            raise TaskInputError(message)
    if request.resources.timeout_s is not None and request.resources.timeout_s <= 0:
        message = "resources.timeout_s must be positive"
        raise TaskInputError(message)
    mem_mb = request.resources.mem_total_mb()
    if (
        mem_mb is not None
        and request.resources.nproc is not None
        and request.resources.maxcore is not None
    ):
        needed = request.resources.nproc * request.resources.maxcore
        if needed > mem_mb:
            message = f"resources.mem ({mem_mb:g} MB) cannot cover nproc * maxcore ({needed} MB)"
            raise TaskInputError(message)


__all__ = [
    "TASK_OPTIONS_TYPES",
    "TASK_REQUEST_SCHEMA_VERSION",
    "CasscfOptions",
    "FrequencyOptions",
    "IrcDirection",
    "IrcOptions",
    "MethodSpec",
    "OptimizeOptions",
    "RescueSpec",
    "ScanCoordinateSpec",
    "ScanMode",
    "ScanOptions",
    "SinglePointOptions",
    "StructureInput",
    "TaskKind",
    "TaskOptions",
    "TaskRequest",
    "TaskResources",
    "ThermochemistryOptions",
    "TsSpec",
    "options_from_dict",
    "options_to_dict",
    "parse_memory_mb",
    "validate_request",
]
