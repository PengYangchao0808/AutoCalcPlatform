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

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, TypeAlias

if TYPE_CHECKING:  # pragma: no cover - typing only (keeps this module qc-free)
    from cccp.qc.interfaces.constraints import ReactionCoordinatePlan

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
    """The seven core calculation tasks plus the seven P2 capability tasks."""

    SINGLEPOINT = "singlepoint"
    OPTIMIZE = "optimize"
    FREQUENCY = "frequency"
    SCAN = "scan"
    IRC = "irc"
    CASSCF = "casscf"
    THERMOCHEMISTRY = "thermochemistry"
    # P2 capability tasks (contracts only in todo 24; execution wires in 42/43)
    CONFORMER_SEARCH = "conformer_search"
    MD_SAMPLING = "md_sampling"
    CLUSTERING = "clustering"
    CENSO_REFINE = "censo_refine"
    NMR_SHIELDING = "nmr_shielding"
    XTB_PATH_SEARCH = "xtb_path_search"
    ORCA_GRADIENT = "orca_gradient"


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
    """Transition-state mode-following control (replaces legacy ``ts_mode``).

    ``mode_index=None`` follows the lowest imaginary mode; an explicit index
    is the mapped target.  ``mode_index`` is only legal when ``enabled`` —
    the legacy ``ts_mode: bool|int`` dual semantics (one field carrying both
    the switch and the index) are gone.
    """

    enabled: bool = False
    mode_index: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "enabled", bool(self.enabled))
        if self.mode_index is not None:
            if isinstance(self.mode_index, bool) or not isinstance(self.mode_index, int):
                message = "ts.mode_index must be an int"
                raise TaskInputError(message)
            if self.mode_index < 0:
                message = "ts.mode_index must be >= 0"
                raise TaskInputError(message)
            if not self.enabled:
                message = "ts.mode_index requires ts.enabled=True"
                raise TaskInputError(message)

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


#: Failure-type token for rescue restore input / derived diagnostics (the
#: closed vocabulary used by the rescue matrix rows).  Typed as ``str`` so
#: unknown caller tokens round-trip verbatim and simply do not override the
#: task-derived classification (legacy ``_failure_type`` override/derive).
FailureType: TypeAlias = str


@dataclass(frozen=True, slots=True)
class RescueSpec:
    """Rescue-retry policy for geometry optimization.

    ``failure_type`` is the optional caller-supplied restore input (v3.1 §4);
    ``None`` (or an unknown token) means the task derives the failure class
    from its own error classification.  Derived failure diagnostics land in
    the result payload (``OptimizePayload.rescue_failure_type``) — input and
    output never share one writable field.
    """

    policy: str = "adaptive"
    max_rescue: int | None = None
    failure_type: FailureType | None = None

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
    """Task-specific options for ``optimize``.

    Field contract (plan todo 18): theory carriers (solvent/grid/SCF/basis/
    dispersion/RI/aux) live ONLY on ``level`` (``MethodSpec``) — the options
    carry no second copy; the structure role derives from ``mode``/``ts``
    and is cross-checked against ``StructureInput.role``; ``ts`` replaces the
    dual-semantics ``ts_mode``; ``rescue`` carries the caller restore input.
    """

    task: ClassVar[TaskKind] = TaskKind.OPTIMIZE
    mode: OptimizationMode = OptimizationMode.UNCONSTRAINED
    level: MethodSpec | None = None
    initial_hessian: str | None = None
    recalc_hess: int | None = None
    trust_radius: float | None = None
    max_cycles: int | None = None
    constraints: ReactionCoordinatePlan | None = None
    ts: TsSpec | None = None
    rescue: RescueSpec = field(default_factory=RescueSpec)
    geom_maxiter: int | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"mode": self.mode.value, "rescue": self.rescue.to_dict()}
        if self.level is not None:
            payload["level"] = self.level.to_dict()
        for key, value in (
            ("initial_hessian", self.initial_hessian),
            ("recalc_hess", self.recalc_hess),
            ("trust_radius", self.trust_radius),
            ("max_cycles", self.max_cycles),
            ("geom_maxiter", self.geom_maxiter),
        ):
            if value is not None:
                payload[key] = value
        if self.constraints is not None:
            payload["constraints"] = self.constraints.to_dict()  # type: ignore[attr-defined]
        if self.ts is not None:
            payload["ts"] = self.ts.to_dict()
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
        raw_level = payload.get("level")
        raw_constraints = payload.get("constraints")
        if raw_ts is not None and not isinstance(raw_ts, Mapping):
            message = "options.ts must be a mapping"
            raise TaskInputError(message)
        if raw_rescue is not None and not isinstance(raw_rescue, Mapping):
            message = "options.rescue must be a mapping"
            raise TaskInputError(message)
        if raw_level is not None and not isinstance(raw_level, Mapping):
            message = "options.level must be a mapping"
            raise TaskInputError(message)
        if raw_constraints is not None and not isinstance(raw_constraints, Mapping):
            message = "options.constraints must be a mapping"
            raise TaskInputError(message)
        constraints: ReactionCoordinatePlan | None = None
        if raw_constraints is not None:
            from cccp.qc.interfaces.constraints import ReactionCoordinatePlan as _Plan

            try:
                constraints = _Plan.from_dict(dict(raw_constraints))
            except ValueError as exc:
                raise TaskInputError(str(exc)) from exc
        rescue = RescueSpec.from_dict(raw_rescue)
        return cls(
            mode=mode,  # type: ignore[arg-type]
            level=MethodSpec.from_dict(raw_level) if raw_level is not None else None,
            initial_hessian=parse_str_strict(payload, "initial_hessian"),
            recalc_hess=parse_int_strict(payload, "recalc_hess"),
            trust_radius=parse_float_strict(payload, "trust_radius"),
            max_cycles=parse_int_strict(payload, "max_cycles"),
            geom_maxiter=parse_int_strict(payload, "geom_maxiter"),
            constraints=constraints,
            ts=TsSpec.from_dict(raw_ts),
            rescue=rescue if rescue is not None else RescueSpec(),
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
    #: ORCA ``ScanTS`` route toggle — default OFF so a plain relaxed scan never
    #: emits a transition-state-oriented scan.  The task layer always forwards
    #: the effective boolean to the backend (the ORCA interface default stays
    #: ``True``); ``ScanMode.RELAXED`` semantics are unchanged.
    use_scants: bool = False
    #: Per-point geometry-optimisation iteration cap → ORCA ``%geom MaxIter``.
    #: ``None`` (the contract default) leaves ORCA's own default in force; a
    #: value ``<= 0`` is accepted but never rendered (the translation gate is
    #: ``> 0``), so an explicit ``0`` behaves like ``None``.  The 200 default
    #: for the manual ``acp run scan`` path is owned by the CLI layer.
    geom_maxiter: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "coordinates", tuple(self.coordinates))
        object.__setattr__(self, "values", tuple(float(v) for v in self.values))
        object.__setattr__(self, "mode", ScanMode(self.mode))
        if not isinstance(self.use_scants, bool):
            message = "use_scants must be a boolean"
            raise TaskInputError(message)

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {
            "coordinates": [coordinate.to_dict() for coordinate in self.coordinates],
            "mode": self.mode.value,
            "use_scants": self.use_scants,
        }
        if self.points is not None:
            payload["points"] = self.points
        if self.values:
            payload["values"] = list(self.values)
        if self.geom_maxiter is not None:
            payload["geom_maxiter"] = self.geom_maxiter
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> ScanOptions:
        """Parse strictly; unknown fields are ignored (rule S2).

        ``use_scants`` is a strict boolean: a non-bool value raises.  A
        missing field defaults to ``False`` (the typed contract), while the
        raw payload distinction (field absent vs explicit ``False``) is
        preserved for identity — see ``acp.calculations.identity``.
        """
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
        use_scants = parse_bool_strict(payload, "use_scants")
        return cls(
            coordinates=tuple(coordinates),
            points=parse_int_strict(payload, "points"),
            values=parse_float_tuple_strict(payload, "values"),
            mode=mode,  # type: ignore[arg-type]
            use_scants=use_scants if use_scants is not None else False,
            geom_maxiter=parse_int_strict(payload, "geom_maxiter"),
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

    @property
    def orbital_selection(self) -> str:
        """Orbital selection mode — read-only view of ``CASSCFSpec``.

        No duplicated storage (T11 D5): ``CASSCFSpec`` owns the field and the
        serialised form (``to_dict``/``from_dict`` round-trip through
        ``spec``); this property is a pure convenience view.
        """
        return self.spec.orbital_selection

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


# ── scoped backend-input fragments (P2 raw knobs, explicit + digest) ────


class BackendInputKind(str, Enum):
    """Closed vocabulary of raw backend-input fragment channels (P2 only).

    Scope-limited by design: only these five legacy knob families may be
    carried verbatim into a request; everything else must map to a typed
    field.  Fragments are never a renamed unconstrained passthrough.
    """

    PATH_INP_TEXT = "path_inp_text"
    EXTRA_ARGS = "extra_args"
    ROUTE_EXTRAS = "route_extras"
    EXTRA_BLOCKS = "extra_blocks"
    OUTPUT_NAME = "output_name"


class FragmentConflictRule(str, Enum):
    """Deterministic behavior when a fragment re-specifies a structured knob."""

    STRUCTURED_FIELDS_WIN = "structured_fields_win"
    REJECT_ON_CONFLICT = "reject_on_conflict"


#: Per-kind deterministic conflict rule (translation layer enforces the same
#: table; a fragment may not choose its own rule).
FRAGMENT_CONFLICT_RULES: dict[BackendInputKind, FragmentConflictRule] = {
    BackendInputKind.PATH_INP_TEXT: FragmentConflictRule.STRUCTURED_FIELDS_WIN,
    BackendInputKind.EXTRA_ARGS: FragmentConflictRule.STRUCTURED_FIELDS_WIN,
    BackendInputKind.ROUTE_EXTRAS: FragmentConflictRule.STRUCTURED_FIELDS_WIN,
    BackendInputKind.EXTRA_BLOCKS: FragmentConflictRule.REJECT_ON_CONFLICT,
    BackendInputKind.OUTPUT_NAME: FragmentConflictRule.STRUCTURED_FIELDS_WIN,
}

#: Structured-owned knobs per fragment kind (token → structured field name).
#: Only these tokens are conflict-checked; the fragment never owns them.
FRAGMENT_KNOB_TOKENS: dict[BackendInputKind, dict[str, str]] = {
    BackendInputKind.PATH_INP_TEXT: {
        "gfn": "gfn_level",
        "uhf": "uhf",
        "chrg": "charge",
        "spin": "multiplicity",
    },
    BackendInputKind.EXTRA_ARGS: {
        "--gfn": "gfn_level",
        "--uhf": "uhf",
        "--chrg": "charge",
        "--seed": "seed",
    },
    BackendInputKind.ROUTE_EXTRAS: {},
    BackendInputKind.EXTRA_BLOCKS: {
        "nprocs": "nproc",
        "maxcore": "maxcore",
    },
    BackendInputKind.OUTPUT_NAME: {},
}


@dataclass(frozen=True, slots=True)
class BackendInputFragment:
    """Scope-limited, explicitly-marked raw backend input (P2 tasks only).

    Records the verbatim content plus its content digest, the conflict rule
    that governs overlap with structured fields, and its source field name
    in the legacy request.  ``content_digest`` participates in every cache
    signature derived from the serialized request.
    """

    kind: BackendInputKind
    source: str
    content: str | tuple[str, ...]
    conflict_rule: FragmentConflictRule
    content_digest: str = ""

    def __post_init__(self) -> None:
        try:
            kind = BackendInputKind(self.kind)
        except ValueError as exc:
            allowed = ", ".join(k.value for k in BackendInputKind)
            message = f"fragment kind must be one of: {allowed}"
            raise TaskInputError(message) from exc
        object.__setattr__(self, "kind", kind)
        try:
            rule = FragmentConflictRule(self.conflict_rule)
        except ValueError as exc:
            allowed = ", ".join(r.value for r in FragmentConflictRule)
            message = f"fragment conflict_rule must be one of: {allowed}"
            raise TaskInputError(message) from exc
        expected = FRAGMENT_CONFLICT_RULES[kind]
        if rule is not expected:
            message = (
                f"fragment kind {kind.value!r} must use conflict_rule "
                f"{expected.value!r}, got {rule.value!r}"
            )
            raise TaskInputError(message)
        object.__setattr__(self, "conflict_rule", rule)
        if not self.source or not str(self.source).strip():
            message = "fragment source must be a non-empty legacy field name"
            raise TaskInputError(message)
        object.__setattr__(self, "source", str(self.source))
        if isinstance(self.content, str):
            normalized: str | tuple[str, ...] = self.content
        elif isinstance(self.content, (list, tuple)):
            items = tuple(str(item) for item in self.content)
            for item in items:
                if not item:
                    message = "fragment arg-list content entries must be non-empty"
                    raise TaskInputError(message)
            normalized = items
        else:
            message = "fragment content must be a string or a list of strings"
            raise TaskInputError(message)
        object.__setattr__(self, "content", normalized)
        object.__setattr__(self, "content_digest", self.compute_digest())

    def compute_digest(self) -> str:
        """sha256 over the canonical JSON of the fragment content."""
        return hashlib.sha256(
            json.dumps(self.content, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()

    def cache_signature(self) -> JsonObject:
        """Cache-identity contribution: kind + content digest (content itself
        is covered by the digest)."""
        return {"kind": self.kind.value, "content_digest": self.content_digest}

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        content: str | list[str] = (
            self.content if isinstance(self.content, str) else list(self.content)
        )
        return {
            "kind": self.kind.value,
            "source": self.source,
            "content": content,
            "conflict_rule": self.conflict_rule.value,
            "content_digest": self.content_digest,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> BackendInputFragment:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not isinstance(payload, Mapping):
            message = "backend input fragment must be a mapping"
            raise TaskInputError(message)
        kind = parse_enum_strict(BackendInputKind, payload.get("kind"), "fragment.kind")
        rule = parse_enum_strict(
            FragmentConflictRule, payload.get("conflict_rule"), "fragment.conflict_rule"
        )
        raw_content = payload.get("content")
        content: str | tuple[str, ...]
        if isinstance(raw_content, str):
            content = raw_content
        elif isinstance(raw_content, list):
            content = tuple(str(item) for item in raw_content)
        else:
            message = "fragment.content must be a string or a list of strings"
            raise TaskInputError(message)
        return cls(
            kind=kind,  # type: ignore[arg-type]
            source=parse_str_strict(payload, "source") or "",
            content=content,
            conflict_rule=rule,  # type: ignore[arg-type]
        )


def _fragment_knob_value(fragment: BackendInputFragment, token: str) -> str | None:
    """Value a fragment assigns to a knob token, or None when absent.

    Accepts ``token=value`` and ``token value`` forms; returns ``""`` for a
    bare flag (re-specification without a value is unresolvable overlap).
    """
    haystack = fragment.content if isinstance(fragment.content, str) else " ".join(fragment.content)
    parts = haystack.replace("\n", " ").split()
    for index, part in enumerate(parts):
        if part == token:
            if index + 1 < len(parts):
                return parts[index + 1]
            return ""
        if part.startswith(f"{token}="):
            return part[len(token) + 1 :]
    return None


def fragment_structured_conflicts(
    fragment: BackendInputFragment, structured: Mapping[str, object]
) -> tuple[str, ...]:
    """Structured knobs contradicted by ``fragment`` (deterministic scan).

    A conflict exists when a declared knob token in the fragment assigns a
    value that **disagrees** with the structured field (string comparison of
    the token value vs ``str(structured value)``).  Matching values are
    consistent duplicates and keep the effective input identical before and
    after conversion.  Bare flags without a value count as disagreement.
    """
    tokens = FRAGMENT_KNOB_TOKENS.get(fragment.kind, {})
    if not tokens:
        return ()
    found: list[str] = []
    for token, field_name in tokens.items():
        structured_value = structured.get(field_name)
        if structured_value is None:
            continue
        assigned = _fragment_knob_value(fragment, token)
        if assigned is None:
            continue
        if assigned != str(structured_value):
            found.append(field_name)
    return tuple(sorted(set(found)))


def resolve_fragment_conflicts(
    fragment: BackendInputFragment, structured: Mapping[str, object]
) -> tuple[str, ...]:
    """Apply ``fragment.conflict_rule`` to detected conflicts.

    Returns the conflicting structured field names when the rule keeps the
    structured fields authoritative (the fragment's conflicting directive is
    ignored downstream, the fragment stays recorded verbatim).  Raises
    :class:`TaskInputError` under ``reject_on_conflict``.

    Raises:
        TaskInputError: When the fragment's rule is ``reject_on_conflict``
            and any structured-owned knob is re-specified.
    """
    conflicts = fragment_structured_conflicts(fragment, structured)
    if not conflicts:
        return ()
    if fragment.conflict_rule is FragmentConflictRule.REJECT_ON_CONFLICT:
        message = (
            f"fragment {fragment.kind.value!r} contradicts structured knob(s) "
            f"{', '.join(conflicts)} under rule 'reject_on_conflict'"
        )
        raise TaskInputError(message)
    return conflicts


# ── P2 per-task typed options (contracts only; execution in todos 42/43) ─


@dataclass(frozen=True, slots=True)
class ConformerSearchOptions:
    """Task-specific options for ``conformer_search`` (CREST)."""

    task: ClassVar[TaskKind] = TaskKind.CONFORMER_SEARCH
    energy_window: float | None = None
    gfn_level: int | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {}
        if self.energy_window is not None:
            payload["energy_window"] = self.energy_window
        if self.gfn_level is not None:
            payload["gfn_level"] = self.gfn_level
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> ConformerSearchOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        return cls(
            energy_window=parse_float_strict(payload, "energy_window"),
            gfn_level=parse_int_strict(payload, "gfn_level"),
        )


@dataclass(frozen=True, slots=True)
class MdSamplingOptions:
    """Task-specific options for ``md_sampling`` (Molclus/xTB-MD)."""

    task: ClassVar[TaskKind] = TaskKind.MD_SAMPLING
    md_method: str | None = None
    gfn_level: int | None = None
    temperature_k: float | None = None
    time_ps: float | None = None
    dump_fs: float | None = None
    step_fs: float | None = None
    hmass: float | None = None
    shake: bool | None = None
    nvt: bool | None = None
    seed: int | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {}
        for key, value in (
            ("md_method", self.md_method),
            ("gfn_level", self.gfn_level),
            ("temperature_k", self.temperature_k),
            ("time_ps", self.time_ps),
            ("dump_fs", self.dump_fs),
            ("step_fs", self.step_fs),
            ("hmass", self.hmass),
            ("shake", self.shake),
            ("nvt", self.nvt),
            ("seed", self.seed),
        ):
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> MdSamplingOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        return cls(
            md_method=parse_str_strict(payload, "md_method"),
            gfn_level=parse_int_strict(payload, "gfn_level"),
            temperature_k=parse_float_strict(payload, "temperature_k"),
            time_ps=parse_float_strict(payload, "time_ps"),
            dump_fs=parse_float_strict(payload, "dump_fs"),
            step_fs=parse_float_strict(payload, "step_fs"),
            hmass=parse_float_strict(payload, "hmass"),
            shake=parse_bool_strict(payload, "shake"),
            nvt=parse_bool_strict(payload, "nvt"),
            seed=parse_int_strict(payload, "seed"),
        )


@dataclass(frozen=True, slots=True)
class ClusteringOptions:
    """Task-specific options for ``clustering`` (ISOSTAT/Molclus)."""

    task: ClassVar[TaskKind] = TaskKind.CLUSTERING
    edis: float | None = None
    gdis: float | None = None
    temperature_k: float | None = None
    nout: int | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {}
        for key, value in (
            ("edis", self.edis),
            ("gdis", self.gdis),
            ("temperature_k", self.temperature_k),
            ("nout", self.nout),
        ):
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> ClusteringOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        return cls(
            edis=parse_float_strict(payload, "edis"),
            gdis=parse_float_strict(payload, "gdis"),
            temperature_k=parse_float_strict(payload, "temperature_k"),
            nout=parse_int_strict(payload, "nout"),
        )


@dataclass(frozen=True, slots=True)
class CensoLevelOverride:
    """One CENSO part-level theory override (preset refinement seam)."""

    part: str
    func: str | None = None
    basis: str | None = None
    threshold: float | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"part": self.part}
        if self.func is not None:
            payload["func"] = self.func
        if self.basis is not None:
            payload["basis"] = self.basis
        if self.threshold is not None:
            payload["threshold"] = self.threshold
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> CensoLevelOverride:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        return cls(
            part=parse_str_strict(payload, "part") or "",
            func=parse_str_strict(payload, "func"),
            basis=parse_str_strict(payload, "basis"),
            threshold=parse_float_strict(payload, "threshold"),
        )


@dataclass(frozen=True, slots=True)
class CensoRefineOptions:
    """Task-specific options for ``censo_refine``.

    Structured preset/level overrides only — the CENSO template/rcfile text
    is generated by the translation layer and is deliberately NOT a request
    field (workflows must never assemble template text).
    """

    task: ClassVar[TaskKind] = TaskKind.CENSO_REFINE
    preset: str | None = None
    level_overrides: tuple[CensoLevelOverride, ...] = ()
    temperature_k: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "level_overrides", tuple(self.level_overrides))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {}
        if self.preset is not None:
            payload["preset"] = self.preset
        if self.level_overrides:
            payload["level_overrides"] = [entry.to_dict() for entry in self.level_overrides]
        if self.temperature_k is not None:
            payload["temperature_k"] = self.temperature_k
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> CensoRefineOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_overrides = payload.get("level_overrides", [])
        if not isinstance(raw_overrides, list):
            message = "options.level_overrides must be a list"
            raise TaskInputError(message)
        overrides: list[CensoLevelOverride] = []
        for index, entry in enumerate(raw_overrides):
            if not isinstance(entry, Mapping):
                message = f"options.level_overrides[{index}] must be a mapping"
                raise TaskInputError(message)
            overrides.append(CensoLevelOverride.from_dict(entry))
        return cls(
            preset=parse_str_strict(payload, "preset"),
            level_overrides=tuple(overrides),
            temperature_k=parse_float_strict(payload, "temperature_k"),
        )


@dataclass(frozen=True, slots=True)
class NmrShieldingOptions:
    """Task-specific options for ``nmr_shielding`` (GIAO).

    ``atom_indices`` carries an explicit base (record identity); the result
    keeps the atom → ``{symbol, isotropic}`` key shape verbatim.
    """

    task: ClassVar[TaskKind] = TaskKind.NMR_SHIELDING
    atom_indices: tuple[int, ...] = ()
    atom_index_base: Literal[0, 1] = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "atom_indices", tuple(int(i) for i in self.atom_indices))
        if self.atom_index_base not in (0, 1):
            message = "atom_index_base must be 0 or 1"
            raise TaskInputError(message)

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"atom_index_base": self.atom_index_base}
        if self.atom_indices:
            payload["atom_indices"] = list(self.atom_indices)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> NmrShieldingOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        base_raw = payload.get("atom_index_base", 0)
        if isinstance(base_raw, bool) or not isinstance(base_raw, int) or base_raw not in (0, 1):
            message = "atom_index_base must be 0 or 1"
            raise TaskInputError(message)
        return cls(
            atom_indices=parse_int_tuple_strict(payload, "atom_indices"),
            atom_index_base=base_raw,  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class XtbPathSearchOptions:
    """Task-specific options for ``xtb_path_search`` (GFN2-xTB PATH).

    The start geometry is the request-level ``structure``; ``end_structure``
    completes the structure pair.  Raw recipe knobs travel only as scoped
    :class:`BackendInputFragment` entries.
    """

    task: ClassVar[TaskKind] = TaskKind.XTB_PATH_SEARCH
    end_structure: StructureInput | None = None
    gfn_level: int | None = None
    uhf: int | None = None
    seed: int | None = None
    backend_inputs: tuple[BackendInputFragment, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "backend_inputs", tuple(self.backend_inputs))
        for fragment in self.backend_inputs:
            if fragment.kind not in (
                BackendInputKind.PATH_INP_TEXT,
                BackendInputKind.EXTRA_ARGS,
            ):
                message = (
                    f"xtb_path_search accepts fragments of kind "
                    f"path_inp_text/extra_args, got {fragment.kind.value!r}"
                )
                raise TaskInputError(message)

    def cache_signature(self) -> JsonObject:
        """Cache-identity payload: structured knobs + fragment digests."""
        return {
            "gfn_level": self.gfn_level,
            "uhf": self.uhf,
            "seed": self.seed,
            "backend_inputs": [f.cache_signature() for f in self.backend_inputs],
        }

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {
            "backend_inputs": [f.to_dict() for f in self.backend_inputs],
        }
        if self.end_structure is not None:
            payload["end_structure"] = self.end_structure.to_dict()
        for key, value in (
            ("gfn_level", self.gfn_level),
            ("uhf", self.uhf),
            ("seed", self.seed),
        ):
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> XtbPathSearchOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_end = payload.get("end_structure")
        if raw_end is not None and not isinstance(raw_end, Mapping):
            message = "options.end_structure must be a mapping"
            raise TaskInputError(message)
        raw_inputs = payload.get("backend_inputs", [])
        if not isinstance(raw_inputs, list):
            message = "options.backend_inputs must be a list"
            raise TaskInputError(message)
        fragments: list[BackendInputFragment] = []
        for index, entry in enumerate(raw_inputs):
            if not isinstance(entry, Mapping):
                message = f"options.backend_inputs[{index}] must be a mapping"
                raise TaskInputError(message)
            fragments.append(BackendInputFragment.from_dict(entry))
        return cls(
            end_structure=StructureInput.from_dict(raw_end),
            gfn_level=parse_int_strict(payload, "gfn_level"),
            uhf=parse_int_strict(payload, "uhf"),
            seed=parse_int_strict(payload, "seed"),
            backend_inputs=tuple(fragments),
        )


@dataclass(frozen=True, slots=True)
class OrcaGradientOptions:
    """Task-specific options for ``orca_gradient`` (ORCA EnGrad).

    ``scf_convergence`` lives on ``level`` (``MethodSpec.scf``) and the
    geometry on the request-level ``structure``; only the raw ORCA input
    fragments travel here.
    """

    task: ClassVar[TaskKind] = TaskKind.ORCA_GRADIENT
    backend_inputs: tuple[BackendInputFragment, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "backend_inputs", tuple(self.backend_inputs))
        for fragment in self.backend_inputs:
            if fragment.kind not in (
                BackendInputKind.ROUTE_EXTRAS,
                BackendInputKind.EXTRA_BLOCKS,
                BackendInputKind.OUTPUT_NAME,
            ):
                message = (
                    f"orca_gradient accepts fragments of kind "
                    f"route_extras/extra_blocks/output_name, got {fragment.kind.value!r}"
                )
                raise TaskInputError(message)

    def cache_signature(self) -> JsonObject:
        """Cache-identity payload: fragment digests (structured knobs live
        on ``level``/``resources`` and are covered there)."""
        return {"backend_inputs": [f.cache_signature() for f in self.backend_inputs]}

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {"backend_inputs": [f.to_dict() for f in self.backend_inputs]}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> OrcaGradientOptions:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_inputs = payload.get("backend_inputs", [])
        if not isinstance(raw_inputs, list):
            message = "options.backend_inputs must be a list"
            raise TaskInputError(message)
        fragments: list[BackendInputFragment] = []
        for index, entry in enumerate(raw_inputs):
            if not isinstance(entry, Mapping):
                message = f"options.backend_inputs[{index}] must be a mapping"
                raise TaskInputError(message)
            fragments.append(BackendInputFragment.from_dict(entry))
        return cls(backend_inputs=tuple(fragments))


# ── P2 contract mapping table (input/backend/semantics/identity) ───────


@dataclass(frozen=True, slots=True)
class TaskContractMapping:
    """Per-task contract mapping: input shape, backend/capability names,
    success/partial/empty semantics, artifact + record identity."""

    input_shape: str
    capability: str
    backends: tuple[str, ...]
    success: str
    partial: str
    empty: str
    artifact_identity: str
    record_identity: str


P2_TASK_CONTRACTS: dict[TaskKind, TaskContractMapping] = {
    TaskKind.CONFORMER_SEARCH: TaskContractMapping(
        input_shape="single_structure",
        capability="conformer_search",
        backends=("crest", "censo", "molclus"),
        success="completed with ensemble_ref and n_conformers >= 1",
        partial="complete=False keeps valid conformers at original indices",
        empty="n_conformers == 0 is failed(error_kind=backend_failure), not an empty success",
        artifact_identity="ensemble_ref = ensemble artifact, root-relative",
        record_identity="conformer table rows keep original conformer indices",
    ),
    TaskKind.MD_SAMPLING: TaskContractMapping(
        input_shape="single_structure",
        capability="md_sampling",
        backends=("molclus",),
        success="completed with trajectory_ref and n_frames >= 1",
        partial="complete=False keeps the valid frame prefix at original indices",
        empty="n_frames == 0 is failed(error_kind=backend_failure)",
        artifact_identity="trajectory_ref = trajectory artifact, root-relative",
        record_identity="frame indices follow trajectory order, never renumbered",
    ),
    TaskKind.CLUSTERING: TaskContractMapping(
        input_shape="ensemble",
        capability="clustering",
        backends=("isostat", "external"),
        success="completed with every input frame assigned to a cluster",
        partial="complete=False keeps assigned clusters and their representatives",
        empty="zero clusters for a non-empty ensemble is failed(error_kind=backend_failure)",
        artifact_identity="clustered_ref = clustered-ensemble artifact, root-relative",
        record_identity="assignments reference original ensemble frame indices",
    ),
    TaskKind.CENSO_REFINE: TaskContractMapping(
        input_shape="ensemble",
        capability="censo_refine",
        backends=("censo",),
        success="completed with per-conformer energy/free-energy/weight rows + refined ensemble",
        partial="complete=False keeps valid rows keyed by original frame_index",
        empty="zero surviving records is failed(error_kind=backend_failure)",
        artifact_identity="refined_ensemble_ref = refined ensemble artifact, root-relative",
        record_identity="records carry conf_id + original frame_index (CENSO ordering maps back)",
    ),
    TaskKind.NMR_SHIELDING: TaskContractMapping(
        input_shape="single_structure",
        capability="nmr_shielding",
        backends=("orca",),
        success="completed with shieldings keyed atom → {symbol, isotropic}",
        partial="complete=False keeps the shielded-atom subset at original keys",
        empty="zero shieldings parsed is failed(error_kind=parse_failure)",
        artifact_identity="shielding log referenced as artifact; payload keeps the table",
        record_identity="atom keys keep their integer identity and declared atom_index_base",
    ),
    TaskKind.XTB_PATH_SEARCH: TaskContractMapping(
        input_shape="structure_pair",
        capability="xtb_path_search",
        backends=("xtb",),
        success="completed with trajectory_ref, frames and endpoint frame indices",
        partial="complete=False keeps valid frames at original indices",
        empty="zero frames is failed(error_kind=backend_failure)",
        artifact_identity="trajectory_ref = path trajectory artifact, root-relative",
        record_identity="frame indices follow path order; endpoints reference those indices",
    ),
    TaskKind.ORCA_GRADIENT: TaskContractMapping(
        input_shape="single_structure",
        capability="orca_gradient",
        backends=("orca",),
        success="completed with one gradient row per atom (unit + convention recorded)",
        partial="n/a — a gradient is all-or-nothing per atom order",
        empty="missing gradient rows is failed(error_kind=parse_failure)",
        artifact_identity="engrad/log referenced as artifact; payload keeps gradients",
        record_identity="gradient rows carry unit + shape + input atom order",
    ),
}


TaskOptions: TypeAlias = (
    SinglePointOptions
    | OptimizeOptions
    | FrequencyOptions
    | ScanOptions
    | IrcOptions
    | CasscfOptions
    | ThermochemistryOptions
    | ConformerSearchOptions
    | MdSamplingOptions
    | ClusteringOptions
    | CensoRefineOptions
    | NmrShieldingOptions
    | XtbPathSearchOptions
    | OrcaGradientOptions
)

# Table ① (todo 11/24): task → options/payload types.  The task → execution
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
    TaskKind.CONFORMER_SEARCH: ConformerSearchOptions,
    TaskKind.MD_SAMPLING: MdSamplingOptions,
    TaskKind.CLUSTERING: ClusteringOptions,
    TaskKind.CENSO_REFINE: CensoRefineOptions,
    TaskKind.NMR_SHIELDING: NmrShieldingOptions,
    TaskKind.XTB_PATH_SEARCH: XtbPathSearchOptions,
    TaskKind.ORCA_GRADIENT: OrcaGradientOptions,
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


def _validate_fragment_conflicts(request: TaskRequest) -> None:
    """Deterministic fragment/structured-field conflict resolution (doc §10)."""
    options = request.options
    fragments: tuple[BackendInputFragment, ...] = ()
    if isinstance(options, (XtbPathSearchOptions, OrcaGradientOptions)):
        fragments = options.backend_inputs
    if not fragments:
        return
    structured: dict[str, object] = {
        "charge": request.charge,
        "multiplicity": request.multiplicity,
        "nproc": request.resources.nproc,
        "maxcore": request.resources.maxcore,
    }
    if isinstance(options, XtbPathSearchOptions):
        structured["gfn_level"] = options.gfn_level
        structured["uhf"] = options.uhf
        structured["seed"] = options.seed
    for fragment in fragments:
        resolve_fragment_conflicts(fragment, structured)


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

    if request.task is TaskKind.XTB_PATH_SEARCH:
        if not isinstance(request.options, XtbPathSearchOptions):
            message = "xtb_path_search requires XtbPathSearchOptions"
            raise TaskInputError(message)
        end = request.options.end_structure
        if end is None:
            message = "xtb_path_search requires options.end_structure (structure pair)"
            raise TaskInputError(message)
        end_inline = end.coordinates is not None and end.symbols is not None
        if end.path is None and not end_inline:
            message = "options.end_structure requires a path or inline coordinates+symbols"
            raise TaskInputError(message)

    _validate_fragment_conflicts(request)

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
    "FRAGMENT_CONFLICT_RULES",
    "FRAGMENT_KNOB_TOKENS",
    "P2_TASK_CONTRACTS",
    "TASK_OPTIONS_TYPES",
    "TASK_REQUEST_SCHEMA_VERSION",
    "BackendInputFragment",
    "BackendInputKind",
    "CasscfOptions",
    "CensoLevelOverride",
    "CensoRefineOptions",
    "ClusteringOptions",
    "ConformerSearchOptions",
    "FailureType",
    "FragmentConflictRule",
    "FrequencyOptions",
    "IrcDirection",
    "IrcOptions",
    "MdSamplingOptions",
    "MethodSpec",
    "NmrShieldingOptions",
    "OptimizeOptions",
    "OrcaGradientOptions",
    "RescueSpec",
    "ScanCoordinateSpec",
    "ScanMode",
    "ScanOptions",
    "SinglePointOptions",
    "StructureInput",
    "TaskContractMapping",
    "TaskKind",
    "TaskOptions",
    "TaskRequest",
    "TaskResources",
    "ThermochemistryOptions",
    "TsSpec",
    "XtbPathSearchOptions",
    "fragment_structured_conflicts",
    "options_from_dict",
    "options_to_dict",
    "parse_memory_mb",
    "resolve_fragment_conflicts",
    "validate_request",
]
