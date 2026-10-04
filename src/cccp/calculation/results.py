"""Typed task result envelope for the cccp calculation layer.

``TaskResult`` carries the structured scientific outcome of one task
execution plus a typed per-task payload.  Partial scientific output is
represented as ``complete=False`` with valid sub-items — there is no global
``PARTIAL`` status (ACP maps explicitly).  See
``docs/ACP_CCCP_Task_API_DevDoc.md`` for the authoritative spec.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias

from cccp.calculation.contracts import (
    ArtifactRef,
    JsonObject,
    Provenance,
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
from cccp.calculation.requests import IrcDirection, TaskKind

TASK_RESULT_SCHEMA_VERSION = 1


class ErrorKind(str, Enum):
    """Closed failure taxonomy for structured failed results.

    Pre-launch rejections (invalid input / unsupported capability /
    unavailable binary) are raised as typed exceptions instead; the
    corresponding values exist so serialised results from rejecting runners
    stay representable.
    """

    INVALID_INPUT = "invalid_input"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    BACKEND_FAILURE = "backend_failure"
    NOT_CONVERGED = "not_converged"
    TIMEOUT = "timeout"
    PARSE_FAILURE = "parse_failure"
    CANCELLED = "cancelled"


# ── per-task typed payloads (closed union) ──────────────────────────────


@dataclass(frozen=True, slots=True)
class SinglePointPayload:
    """Typed payload for ``singlepoint`` results."""

    electronic_state: JsonObject | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {}
        if self.electronic_state is not None:
            payload["electronic_state"] = self.electronic_state
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> SinglePointPayload:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_state = payload.get("electronic_state")
        if raw_state is not None and not isinstance(raw_state, Mapping):
            message = "payload.electronic_state must be a mapping"
            raise TaskInputError(message)
        return cls(electronic_state=dict(raw_state) if raw_state is not None else None)


@dataclass(frozen=True, slots=True)
class OptimizePayload:
    """Typed payload for ``optimize`` results.

    ``rescue_failure_type`` / ``rescue_structure_kind`` are the derived
    diagnostics (the caller-supplied restore input lives in
    ``OptimizeOptions.rescue.failure_type``).  ``rescue_*`` fields are set
    whenever the rescue plan was built (first attempt failed); they stay
    ``None``/empty on a first-attempt success so converters never invent
    legacy keys.
    """

    optimization_status: str | None = None
    rescue_failure_type: str | None = None
    rescue_structure_kind: str | None = None
    rescue_actions: tuple[str, ...] = ()
    rescue_attempts: int | None = None
    rescue_terminal: bool | None = None
    tsmode_explicit_target: int | None = None
    tsmode_target_preserved: bool | None = None
    electronic_state: JsonObject | None = None
    trajectory_ref: ArtifactRef | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rescue_actions", tuple(str(a) for a in self.rescue_actions))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {}
        if self.optimization_status is not None:
            payload["optimization_status"] = self.optimization_status
        if self.rescue_failure_type is not None:
            payload["rescue_failure_type"] = self.rescue_failure_type
        if self.rescue_structure_kind is not None:
            payload["rescue_structure_kind"] = self.rescue_structure_kind
        if self.rescue_failure_type is not None or self.rescue_actions:
            payload["rescue_actions"] = list(self.rescue_actions)
        if self.rescue_attempts is not None:
            payload["rescue_attempts"] = self.rescue_attempts
        if self.rescue_terminal is not None:
            payload["rescue_terminal"] = self.rescue_terminal
        if self.tsmode_explicit_target is not None:
            payload["tsmode_explicit_target"] = self.tsmode_explicit_target
        if self.tsmode_target_preserved is not None:
            payload["tsmode_target_preserved"] = self.tsmode_target_preserved
        if self.electronic_state is not None:
            payload["electronic_state"] = self.electronic_state
        if self.trajectory_ref is not None:
            payload["trajectory_ref"] = _artifact_to_dict(self.trajectory_ref)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> OptimizePayload:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_state = payload.get("electronic_state")
        if raw_state is not None and not isinstance(raw_state, Mapping):
            message = "payload.electronic_state must be a mapping"
            raise TaskInputError(message)
        raw_ref = payload.get("trajectory_ref")
        if raw_ref is not None and not isinstance(raw_ref, Mapping):
            message = "payload.trajectory_ref must be a mapping"
            raise TaskInputError(message)
        rescue_actions: tuple[str, ...] = ()
        if "rescue_actions" in payload:
            rescue_actions = parse_str_tuple_strict(payload, "rescue_actions")
        return cls(
            optimization_status=parse_str_strict(payload, "optimization_status"),
            rescue_failure_type=parse_str_strict(payload, "rescue_failure_type"),
            rescue_structure_kind=parse_str_strict(payload, "rescue_structure_kind"),
            rescue_actions=rescue_actions,
            rescue_attempts=parse_int_strict(payload, "rescue_attempts"),
            rescue_terminal=parse_bool_strict(payload, "rescue_terminal"),
            tsmode_explicit_target=parse_int_strict(payload, "tsmode_explicit_target"),
            tsmode_target_preserved=parse_bool_strict(payload, "tsmode_target_preserved"),
            electronic_state=dict(raw_state) if raw_state is not None else None,
            trajectory_ref=_artifact_from_dict(raw_ref),
        )


@dataclass(frozen=True, slots=True)
class FrequencyAnalysis:
    """Parsed vibrational-frequency scientific data (plan todo 19).

    The single scientific parse of ORCA vibrational output (the reusable
    capability lives in :mod:`cccp.calculation.frequency_parse`): the
    frequency list, IR intensities and the indexed normal-mode maps (ORCA
    native mode indices, zero modes kept in the maps).  Attribute names
    match the ``normal_modes`` product builder's duck-type contract so the
    ACP publication half consumes this data without re-parsing the log.
    """

    frequencies: tuple[float, ...] = ()
    imaginary_frequencies: tuple[float, ...] = ()
    ir_intensities: tuple[float, ...] | None = None
    mode_frequencies: dict[int, float] = field(default_factory=dict)
    mode_vectors: dict[int, tuple[tuple[float, float, float], ...]] = field(default_factory=dict)
    mode_ir_intensities: dict[int, float] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "frequencies", tuple(float(f) for f in self.frequencies))
        object.__setattr__(
            self, "imaginary_frequencies", tuple(float(f) for f in self.imaginary_frequencies)
        )
        if self.ir_intensities is not None:
            object.__setattr__(self, "ir_intensities", tuple(float(v) for v in self.ir_intensities))
        object.__setattr__(
            self,
            "mode_frequencies",
            {int(index): float(freq) for index, freq in self.mode_frequencies.items()},
        )
        object.__setattr__(
            self,
            "mode_vectors",
            {
                int(index): tuple((float(row[0]), float(row[1]), float(row[2])) for row in rows)
                for index, rows in self.mode_vectors.items()
            },
        )
        if self.mode_ir_intensities is not None:
            object.__setattr__(
                self,
                "mode_ir_intensities",
                {int(index): float(value) for index, value in self.mode_ir_intensities.items()},
            )

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict (mode-map keys become strings)."""
        payload: JsonObject = {}
        if self.frequencies:
            payload["frequencies"] = [float(f) for f in self.frequencies]
        if self.imaginary_frequencies:
            payload["imaginary_frequencies"] = [float(f) for f in self.imaginary_frequencies]
        if self.ir_intensities is not None:
            payload["ir_intensities"] = [float(v) for v in self.ir_intensities]
        if self.mode_frequencies:
            payload["mode_frequencies"] = {
                str(index): float(freq) for index, freq in sorted(self.mode_frequencies.items())
            }
        if self.mode_vectors:
            payload["mode_vectors"] = {
                str(index): [[float(c) for c in row] for row in rows]
                for index, rows in sorted(self.mode_vectors.items())
            }
        if self.mode_ir_intensities is not None:
            payload["mode_ir_intensities"] = {
                str(index): float(value)
                for index, value in sorted(self.mode_ir_intensities.items())
            }
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> FrequencyAnalysis:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_ir = payload.get("ir_intensities")
        ir_intensities: tuple[float, ...] | None = None
        if raw_ir is not None:
            ir_intensities = _strict_float_tuple(raw_ir, "ir_intensities")
        raw_mode_ir = payload.get("mode_ir_intensities")
        mode_ir: dict[int, float] | None = None
        if raw_mode_ir is not None:
            if not isinstance(raw_mode_ir, Mapping):
                message = "analysis.mode_ir_intensities must be a mapping"
                raise TaskInputError(message)
            mode_ir = {
                _strict_int_key(key, "mode_ir_intensities"): _strict_float(
                    value, "mode_ir_intensities"
                )
                for key, value in raw_mode_ir.items()
            }
        return cls(
            frequencies=_strict_float_tuple(payload.get("frequencies"), "frequencies")
            if payload.get("frequencies") is not None
            else (),
            imaginary_frequencies=(
                _strict_float_tuple(payload.get("imaginary_frequencies"), "imaginary_frequencies")
                if payload.get("imaginary_frequencies") is not None
                else ()
            ),
            ir_intensities=ir_intensities,
            mode_frequencies=_strict_mode_frequencies(payload.get("mode_frequencies")),
            mode_vectors=_strict_mode_vectors(payload.get("mode_vectors")),
            mode_ir_intensities=mode_ir,
        )


def _strict_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        message = f"analysis.{label} values must be numbers"
        raise TaskInputError(message)
    return float(value)


def _strict_float_tuple(value: object, label: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)):
        message = f"analysis.{label} must be a list"
        raise TaskInputError(message)
    return tuple(_strict_float(entry, label) for entry in value)


def _strict_int_key(key: object, label: str) -> int:
    try:
        return int(key)
    except (TypeError, ValueError) as exc:
        message = f"analysis.{label} keys must be mode indices"
        raise TaskInputError(message) from exc


def _strict_mode_frequencies(value: object) -> dict[int, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        message = "analysis.mode_frequencies must be a mapping"
        raise TaskInputError(message)
    return {
        _strict_int_key(key, "mode_frequencies"): _strict_float(freq, "mode_frequencies")
        for key, freq in value.items()
    }


def _strict_mode_vectors(value: object) -> dict[int, tuple[tuple[float, float, float], ...]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        message = "analysis.mode_vectors must be a mapping"
        raise TaskInputError(message)
    vectors: dict[int, tuple[tuple[float, float, float], ...]] = {}
    for key, rows in value.items():
        index = _strict_int_key(key, "mode_vectors")
        if not isinstance(rows, (list, tuple)):
            message = "analysis.mode_vectors values must be lists of rows"
            raise TaskInputError(message)
        parsed_rows: list[tuple[float, float, float]] = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 3:
                message = "analysis.mode_vectors rows must have exactly 3 components"
                raise TaskInputError(message)
            parsed_rows.append(
                (
                    _strict_float(row[0], "mode_vectors"),
                    _strict_float(row[1], "mode_vectors"),
                    _strict_float(row[2], "mode_vectors"),
                )
            )
        vectors[index] = tuple(parsed_rows)
    return vectors


@dataclass(frozen=True, slots=True)
class FrequencyPayload:
    """Typed payload for ``frequency`` results.

    The authoritative frequency list is ``TaskResult.frequencies``
    (cm⁻¹); ``analysis`` carries the parsed vibration vectors / IR
    intensities so an independent cccp call gets the full scientific data
    without ACP interpretation.  The ``normal_modes.json`` product format,
    geometry binding and manifest registration stay ACP-side — the task
    writes no platform product.
    """

    n_imaginary: int | None = None
    freq_log_ref: ArtifactRef | None = None
    analysis: FrequencyAnalysis | None = None
    electronic_state: JsonObject | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {}
        if self.n_imaginary is not None:
            payload["n_imaginary"] = self.n_imaginary
        if self.freq_log_ref is not None:
            payload["freq_log_ref"] = _artifact_to_dict(self.freq_log_ref)
        if self.analysis is not None:
            payload["analysis"] = self.analysis.to_dict()
        if self.electronic_state is not None:
            payload["electronic_state"] = self.electronic_state
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> FrequencyPayload:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_state = payload.get("electronic_state")
        if raw_state is not None and not isinstance(raw_state, Mapping):
            message = "payload.electronic_state must be a mapping"
            raise TaskInputError(message)
        raw_freq = payload.get("freq_log_ref")
        if raw_freq is not None and not isinstance(raw_freq, Mapping):
            message = "payload.freq_log_ref must be a mapping"
            raise TaskInputError(message)
        raw_analysis = payload.get("analysis")
        if raw_analysis is not None and not isinstance(raw_analysis, Mapping):
            message = "payload.analysis must be a mapping"
            raise TaskInputError(message)
        return cls(
            n_imaginary=parse_int_strict(payload, "n_imaginary"),
            freq_log_ref=_artifact_from_dict(raw_freq),
            analysis=(
                FrequencyAnalysis.from_dict(dict(raw_analysis))
                if raw_analysis is not None
                else None
            ),
            electronic_state=dict(raw_state) if raw_state is not None else None,
        )


@dataclass(frozen=True, slots=True)
class ScanFrame:
    """One scan point; ``index`` is the ORIGINAL frame index.

    Failed points keep their original index and are never renumbered
    (record-identity rule, doc §"Record identity").
    """

    index: int
    values: tuple[float, ...] = ()
    energy_hartree: float | None = None
    geometry_ref: ArtifactRef | None = None
    converged: bool | None = None
    success: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "index", int(self.index))
        object.__setattr__(self, "values", tuple(float(v) for v in self.values))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {
            "index": self.index,
            "values": list(self.values),
            "success": self.success,
        }
        if self.energy_hartree is not None:
            payload["energy_hartree"] = self.energy_hartree
        if self.geometry_ref is not None:
            payload["geometry_ref"] = _artifact_to_dict(self.geometry_ref)
        if self.converged is not None:
            payload["converged"] = self.converged
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ScanFrame:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        raw_ref = payload.get("geometry_ref")
        if raw_ref is not None and not isinstance(raw_ref, Mapping):
            message = "payload.frames[].geometry_ref must be a mapping"
            raise TaskInputError(message)
        index = parse_int_strict(payload, "index")
        if index is None:
            message = "payload.frames[].index is required (original frame index)"
            raise TaskInputError(message)
        success = parse_bool_strict(payload, "success")
        return cls(
            index=index,
            values=parse_float_tuple_strict(payload, "values"),
            energy_hartree=parse_float_strict(payload, "energy_hartree"),
            geometry_ref=_artifact_from_dict(raw_ref),
            converged=parse_bool_strict(payload, "converged"),
            success=True if success is None else success,
        )


@dataclass(frozen=True, slots=True)
class ScanPayload:
    """Typed payload for ``scan`` results (valid sub-results kept partial)."""

    frames: tuple[ScanFrame, ...] = ()
    profile_ref: ArtifactRef | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "frames", tuple(self.frames))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"frames": [frame.to_dict() for frame in self.frames]}
        if self.profile_ref is not None:
            payload["profile_ref"] = _artifact_to_dict(self.profile_ref)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> ScanPayload:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_frames = payload.get("frames", [])
        if not isinstance(raw_frames, list):
            message = "payload.frames must be a list"
            raise TaskInputError(message)
        frames: list[ScanFrame] = []
        for index, entry in enumerate(raw_frames):
            if not isinstance(entry, Mapping):
                message = f"payload.frames[{index}] must be a mapping"
                raise TaskInputError(message)
            frames.append(ScanFrame.from_dict(entry))
        raw_ref = payload.get("profile_ref")
        if raw_ref is not None and not isinstance(raw_ref, Mapping):
            message = "payload.profile_ref must be a mapping"
            raise TaskInputError(message)
        return cls(frames=tuple(frames), profile_ref=_artifact_from_dict(raw_ref))


@dataclass(frozen=True, slots=True)
class IrcDirectionResult:
    """Per-direction IRC sub-result (kept valid even when other directions fail)."""

    direction: IrcDirection
    energy_hartree: float | None = None
    coordinates: tuple[tuple[float, float, float], ...] | None = None
    symbols: tuple[str, ...] | None = None
    converged: bool | None = None
    steps: int | None = None
    success: bool = True
    trajectory_ref: ArtifactRef | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "direction", IrcDirection(self.direction))
        if self.coordinates is not None:
            object.__setattr__(
                self,
                "coordinates",
                tuple(tuple(float(c) for c in row) for row in self.coordinates),
            )

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"direction": self.direction.value, "success": self.success}
        if self.energy_hartree is not None:
            payload["energy_hartree"] = self.energy_hartree
        if self.coordinates is not None:
            payload["coordinates"] = [[float(c) for c in row] for row in self.coordinates]
        if self.symbols is not None:
            payload["symbols"] = list(self.symbols)
        if self.converged is not None:
            payload["converged"] = self.converged
        if self.steps is not None:
            payload["steps"] = self.steps
        if self.trajectory_ref is not None:
            payload["trajectory_ref"] = _artifact_to_dict(self.trajectory_ref)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> IrcDirectionResult:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        direction = parse_enum_strict(IrcDirection, payload.get("direction"), "direction")
        coordinates: tuple[tuple[float, float, float], ...] | None = None
        raw_coordinates = payload.get("coordinates")
        if raw_coordinates is not None:
            if not isinstance(raw_coordinates, list):
                message = "direction coordinates must be a list of 3-vectors"
                raise TaskInputError(message)
            rows: list[tuple[float, float, float]] = []
            for index, row in enumerate(raw_coordinates):
                if not isinstance(row, list) or len(row) != 3:
                    message = f"direction coordinates[{index}] must be a 3-vector"
                    raise TaskInputError(message)
                rows.append(
                    (
                        float(row[0]),
                        float(row[1]),
                        float(row[2]),
                    )
                )
            coordinates = tuple(rows)
        raw_ref = payload.get("trajectory_ref")
        if raw_ref is not None and not isinstance(raw_ref, Mapping):
            message = "payload.directions[].trajectory_ref must be a mapping"
            raise TaskInputError(message)
        success = parse_bool_strict(payload, "success")
        return cls(
            direction=direction,  # type: ignore[arg-type]
            energy_hartree=parse_float_strict(payload, "energy_hartree"),
            coordinates=coordinates,
            symbols=(
                parse_str_tuple_strict(payload, "symbols")
                if payload.get("symbols") is not None
                else None
            ),
            converged=parse_bool_strict(payload, "converged"),
            steps=parse_int_strict(payload, "steps"),
            success=True if success is None else success,
            trajectory_ref=_artifact_from_dict(raw_ref),
        )


@dataclass(frozen=True, slots=True)
class IrcPayload:
    """Typed payload for ``irc`` results; one-way runs keep the valid direction."""

    directions: tuple[IrcDirectionResult, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "directions", tuple(self.directions))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {"directions": [entry.to_dict() for entry in self.directions]}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> IrcPayload:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_entries = payload.get("directions", [])
        if not isinstance(raw_entries, list):
            message = "payload.directions must be a list"
            raise TaskInputError(message)
        entries: list[IrcDirectionResult] = []
        for index, entry in enumerate(raw_entries):
            if not isinstance(entry, Mapping):
                message = f"payload.directions[{index}] must be a mapping"
                raise TaskInputError(message)
            entries.append(IrcDirectionResult.from_dict(entry))
        return cls(directions=tuple(entries))


@dataclass(frozen=True, slots=True)
class CasscfPayload:
    """Typed payload for ``casscf`` results (projection of legacy multireference)."""

    root_energies: tuple[float, ...] = ()
    natural_occupations: tuple[float, ...] = ()
    nevpt2_energies: tuple[float, ...] = ()
    active_space: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "root_energies", tuple(float(v) for v in self.root_energies))
        object.__setattr__(
            self, "natural_occupations", tuple(float(v) for v in self.natural_occupations)
        )
        object.__setattr__(self, "nevpt2_energies", tuple(float(v) for v in self.nevpt2_energies))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {
            "root_energies": list(self.root_energies),
            "natural_occupations": list(self.natural_occupations),
            "nevpt2_energies": list(self.nevpt2_energies),
            "active_space": self.active_space,
        }
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> CasscfPayload:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        return cls(
            root_energies=parse_float_tuple_strict(payload, "root_energies"),
            natural_occupations=parse_float_tuple_strict(payload, "natural_occupations"),
            nevpt2_energies=parse_float_tuple_strict(payload, "nevpt2_energies"),
            active_space=parse_str_strict(payload, "active_space") or "",
        )


def _payload_float_tuple(value: object) -> tuple[float, ...]:
    """Coerce a sequence of numbers to floats; anything else yields ``()``."""
    if not isinstance(value, (list, tuple)):
        return ()
    numbers: list[float] = []
    for entry in value:
        if isinstance(entry, (int, float)) and not isinstance(entry, bool):
            numbers.append(float(entry))
    return tuple(numbers)


def _payload_per_root(multiref: Mapping[str, object], key: str) -> tuple[float, ...]:
    """Per-root ``key`` values from ``nevpt2_roots`` entries, in order."""
    roots = multiref.get("nevpt2_roots")
    if not isinstance(roots, (list, tuple)):
        return ()
    values: list[float] = []
    for entry in roots:
        if isinstance(entry, Mapping):
            raw = entry.get(key)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                values.append(float(raw))
    return tuple(values)


def _payload_scalar_fallback(multiref: Mapping[str, object], key: str) -> tuple[float, ...]:
    raw = multiref.get(key)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return (float(raw),)
    return ()


def casscf_payload_from_multireference(multiref: Mapping[str, object]) -> CasscfPayload:
    """Project legacy ``metadata["multireference"]`` onto :class:`CasscfPayload`.

    The single explicit mapping (plan todo 22) shared by the CASSCF task core
    and the legacy adapter.  Both recorded shapes of the legacy multireference
    dict are accepted:

    * ``natural_occupations`` is always read directly from
      ``multiref["natural_occupations"]`` (any shape).
    * The **adapter-rebuild shape wins when present**: a mapping carrying
      ``root_energies`` / ``nevpt2_energies`` / ``active_space`` keys (the
      shape ``legacy_adapters.to_legacy_result`` re-emits) uses those values
      directly, keeping the legacy round-trip symmetric.
    * Otherwise the **current production shape** (the dict produced by the
      CASSCF task / ``_multireference_metadata``) is projected:

      - ``root_energies`` ← per-root ``casscf_energy_hartree`` from
        ``nevpt2_roots`` entries in order when non-empty; else the scalar
        ``casscf_energy_hartree`` when set; else ``()``.
      - ``nevpt2_energies`` ← per-root ``correlated_energy_hartree`` from
        ``nevpt2_roots`` entries in order when non-empty; else the scalar
        ``correlated_energy_hartree`` when set; else ``()``.
      - ``active_space`` ← ``active_space_signature`` (``""`` when absent).

    Pure data mapping: no I/O and no heavy imports.
    """
    natural_occupations = _payload_float_tuple(multiref.get("natural_occupations"))
    rebuild_shape = any(
        key in multiref for key in ("root_energies", "nevpt2_energies", "active_space")
    )
    if rebuild_shape:
        active = multiref.get("active_space")
        return CasscfPayload(
            root_energies=_payload_float_tuple(multiref.get("root_energies")),
            natural_occupations=natural_occupations,
            nevpt2_energies=_payload_float_tuple(multiref.get("nevpt2_energies")),
            active_space=active if isinstance(active, str) else "",
        )
    root_energies = _payload_per_root(multiref, "casscf_energy_hartree")
    if not root_energies:
        root_energies = _payload_scalar_fallback(multiref, "casscf_energy_hartree")
    nevpt2_energies = _payload_per_root(multiref, "correlated_energy_hartree")
    if not nevpt2_energies:
        nevpt2_energies = _payload_scalar_fallback(multiref, "correlated_energy_hartree")
    active_space = multiref.get("active_space_signature")
    return CasscfPayload(
        root_energies=root_energies,
        natural_occupations=natural_occupations,
        nevpt2_energies=nevpt2_energies,
        active_space=active_space if isinstance(active_space, str) else "",
    )


@dataclass(frozen=True, slots=True)
class ThermochemistryPayload:
    """Typed payload for ``thermochemistry`` results (units: Hartree / au)."""

    enthalpy_hartree: float | None = None
    gibbs_hartree: float | None = None
    entropy_au: float | None = None
    gibbs_source: str | None = None
    standard_state: str | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {}
        for key, value in (
            ("enthalpy_hartree", self.enthalpy_hartree),
            ("gibbs_hartree", self.gibbs_hartree),
            ("entropy_au", self.entropy_au),
            ("gibbs_source", self.gibbs_source),
            ("standard_state", self.standard_state),
        ):
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> ThermochemistryPayload:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        return cls(
            enthalpy_hartree=parse_float_strict(payload, "enthalpy_hartree"),
            gibbs_hartree=parse_float_strict(payload, "gibbs_hartree"),
            entropy_au=parse_float_strict(payload, "entropy_au"),
            gibbs_source=parse_str_strict(payload, "gibbs_source"),
            standard_state=parse_str_strict(payload, "standard_state"),
        )


# ── P2 typed payloads (contracts only; execution in todos 42/43) ────────


@dataclass(frozen=True, slots=True)
class ConformerEnergy:
    """One conformer energy-table row (original conformer index preserved)."""

    conf_id: str
    frame_index: int
    energy_hartree: float | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"conf_id": self.conf_id, "frame_index": self.frame_index}
        if self.energy_hartree is not None:
            payload["energy_hartree"] = self.energy_hartree
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ConformerEnergy:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        frame_index = parse_int_strict(payload, "frame_index")
        if frame_index is None:
            message = "conformer energy row requires 'frame_index'"
            raise TaskInputError(message)
        return cls(
            conf_id=parse_str_strict(payload, "conf_id") or "",
            frame_index=frame_index,
            energy_hartree=parse_float_strict(payload, "energy_hartree"),
        )


@dataclass(frozen=True, slots=True)
class ConformerSearchPayload:
    """Typed payload for ``conformer_search`` results (CREST ensemble)."""

    ensemble_ref: ArtifactRef | None = None
    conformer_count: int = 0
    energy_table: tuple[ConformerEnergy, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "energy_table", tuple(self.energy_table))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {
            "conformer_count": self.conformer_count,
            "energy_table": [row.to_dict() for row in self.energy_table],
        }
        if self.ensemble_ref is not None:
            payload["ensemble_ref"] = _artifact_to_dict(self.ensemble_ref)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> ConformerSearchPayload:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_ref = payload.get("ensemble_ref")
        if raw_ref is not None and not isinstance(raw_ref, Mapping):
            message = "payload.ensemble_ref must be a mapping"
            raise TaskInputError(message)
        raw_table = payload.get("energy_table", [])
        if not isinstance(raw_table, list):
            message = "payload.energy_table must be a list"
            raise TaskInputError(message)
        rows: list[ConformerEnergy] = []
        for index, entry in enumerate(raw_table):
            if not isinstance(entry, Mapping):
                message = f"payload.energy_table[{index}] must be a mapping"
                raise TaskInputError(message)
            rows.append(ConformerEnergy.from_dict(entry))
        return cls(
            ensemble_ref=_artifact_from_dict(raw_ref),
            conformer_count=parse_int_strict(payload, "conformer_count") or 0,
            energy_table=tuple(rows),
        )


@dataclass(frozen=True, slots=True)
class MdSamplingPayload:
    """Typed payload for ``md_sampling`` results (trajectory + frame count)."""

    trajectory_ref: ArtifactRef | None = None
    n_frames: int = 0

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"n_frames": self.n_frames}
        if self.trajectory_ref is not None:
            payload["trajectory_ref"] = _artifact_to_dict(self.trajectory_ref)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> MdSamplingPayload:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_ref = payload.get("trajectory_ref")
        if raw_ref is not None and not isinstance(raw_ref, Mapping):
            message = "payload.trajectory_ref must be a mapping"
            raise TaskInputError(message)
        return cls(
            trajectory_ref=_artifact_from_dict(raw_ref),
            n_frames=parse_int_strict(payload, "n_frames") or 0,
        )


@dataclass(frozen=True, slots=True)
class ClusterAssignment:
    """One cluster assignment (indices reference the input ensemble order)."""

    cluster_id: int
    representative_index: int
    member_indices: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "member_indices", tuple(int(i) for i in self.member_indices))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {
            "cluster_id": self.cluster_id,
            "representative_index": self.representative_index,
            "member_indices": list(self.member_indices),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ClusterAssignment:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        cluster_id = parse_int_strict(payload, "cluster_id")
        representative_index = parse_int_strict(payload, "representative_index")
        if cluster_id is None or representative_index is None:
            message = "cluster assignment requires 'cluster_id' and 'representative_index'"
            raise TaskInputError(message)
        return cls(
            cluster_id=cluster_id,
            representative_index=representative_index,
            member_indices=parse_int_tuple_strict(payload, "member_indices"),
        )


@dataclass(frozen=True, slots=True)
class ClusteringPayload:
    """Typed payload for ``clustering`` results (assignments + representatives)."""

    assignments: tuple[ClusterAssignment, ...] = ()
    clustered_ref: ArtifactRef | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "assignments", tuple(self.assignments))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"assignments": [a.to_dict() for a in self.assignments]}
        if self.clustered_ref is not None:
            payload["clustered_ref"] = _artifact_to_dict(self.clustered_ref)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> ClusteringPayload:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_ref = payload.get("clustered_ref")
        if raw_ref is not None and not isinstance(raw_ref, Mapping):
            message = "payload.clustered_ref must be a mapping"
            raise TaskInputError(message)
        raw_assignments = payload.get("assignments", [])
        if not isinstance(raw_assignments, list):
            message = "payload.assignments must be a list"
            raise TaskInputError(message)
        assignments: list[ClusterAssignment] = []
        for index, entry in enumerate(raw_assignments):
            if not isinstance(entry, Mapping):
                message = f"payload.assignments[{index}] must be a mapping"
                raise TaskInputError(message)
            assignments.append(ClusterAssignment.from_dict(entry))
        return cls(assignments=tuple(assignments), clustered_ref=_artifact_from_dict(raw_ref))


@dataclass(frozen=True, slots=True)
class CensoRefineRecord:
    """One CENSO refinement row keyed back to the original conformer."""

    conf_id: str
    frame_index: int
    energy_hartree: float | None = None
    free_energy_hartree: float | None = None
    weight: float | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"conf_id": self.conf_id, "frame_index": self.frame_index}
        for key, value in (
            ("energy_hartree", self.energy_hartree),
            ("free_energy_hartree", self.free_energy_hartree),
            ("weight", self.weight),
        ):
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> CensoRefineRecord:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        frame_index = parse_int_strict(payload, "frame_index")
        if frame_index is None:
            message = "CENSO refine record requires 'frame_index'"
            raise TaskInputError(message)
        return cls(
            conf_id=parse_str_strict(payload, "conf_id") or "",
            frame_index=frame_index,
            energy_hartree=parse_float_strict(payload, "energy_hartree"),
            free_energy_hartree=parse_float_strict(payload, "free_energy_hartree"),
            weight=parse_float_strict(payload, "weight"),
        )


@dataclass(frozen=True, slots=True)
class CensoRefinePayload:
    """Typed payload for ``censo_refine`` results.

    Energy / free-energy / weight table + refined ensemble.  The CENSO
    template text is a translation-layer product and is never a result
    field.
    """

    records: tuple[CensoRefineRecord, ...] = ()
    refined_ensemble_ref: ArtifactRef | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"records": [r.to_dict() for r in self.records]}
        if self.refined_ensemble_ref is not None:
            payload["refined_ensemble_ref"] = _artifact_to_dict(self.refined_ensemble_ref)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> CensoRefinePayload:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_ref = payload.get("refined_ensemble_ref")
        if raw_ref is not None and not isinstance(raw_ref, Mapping):
            message = "payload.refined_ensemble_ref must be a mapping"
            raise TaskInputError(message)
        raw_records = payload.get("records", [])
        if not isinstance(raw_records, list):
            message = "payload.records must be a list"
            raise TaskInputError(message)
        records: list[CensoRefineRecord] = []
        for index, entry in enumerate(raw_records):
            if not isinstance(entry, Mapping):
                message = f"payload.records[{index}] must be a mapping"
                raise TaskInputError(message)
            records.append(CensoRefineRecord.from_dict(entry))
        return cls(
            records=tuple(records),
            refined_ensemble_ref=_artifact_from_dict(raw_ref),
        )


@dataclass(frozen=True, slots=True)
class NmrShielding:
    """One shielding entry: the atom → ``{symbol, isotropic}`` key shape."""

    symbol: str
    isotropic: float

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {"symbol": self.symbol, "isotropic": self.isotropic}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> NmrShielding:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        isotropic = parse_float_strict(payload, "isotropic")
        if isotropic is None:
            message = "shielding entry requires 'isotropic'"
            raise TaskInputError(message)
        return cls(
            symbol=parse_str_strict(payload, "symbol") or "",
            isotropic=isotropic,
        )


@dataclass(frozen=True, slots=True)
class NmrShieldingPayload:
    """Typed payload for ``nmr_shielding`` results (GIAO).

    ``shieldings`` keeps the atom-index key shape; JSON object keys become
    strings on the wire and are restored to integers here (record identity).
    """

    shieldings: dict[int, NmrShielding] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "shieldings", dict(self.shieldings))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict (int keys become JSON strings)."""
        return {
            "shieldings": {
                str(index): entry.to_dict() for index, entry in sorted(self.shieldings.items())
            }
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> NmrShieldingPayload:
        """Parse strictly; restores integer atom keys (rule S2 otherwise)."""
        if not payload:
            return cls()
        raw = payload.get("shieldings", {})
        if not isinstance(raw, Mapping):
            message = "payload.shieldings must be a mapping"
            raise TaskInputError(message)
        shieldings: dict[int, NmrShielding] = {}
        for key, value in raw.items():
            try:
                index = int(key)
            except (TypeError, ValueError) as exc:
                message = f"payload.shieldings keys must be atom indices, got {key!r}"
                raise TaskInputError(message) from exc
            if not isinstance(value, Mapping):
                message = f"payload.shieldings[{key!r}] must be a mapping"
                raise TaskInputError(message)
            shieldings[index] = NmrShielding.from_dict(value)
        return cls(shieldings=shieldings)


@dataclass(frozen=True, slots=True)
class XtbPathFrame:
    """One xTB PATH frame (original trajectory index preserved)."""

    index: int
    energy_hartree: float | None = None

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"index": self.index}
        if self.energy_hartree is not None:
            payload["energy_hartree"] = self.energy_hartree
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> XtbPathFrame:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        index = parse_int_strict(payload, "index")
        if index is None:
            message = "path frame requires 'index'"
            raise TaskInputError(message)
        return cls(index=index, energy_hartree=parse_float_strict(payload, "energy_hartree"))


@dataclass(frozen=True, slots=True)
class XtbPathSearchPayload:
    """Typed payload for ``xtb_path_search`` results (trajectory/frames/endpoints)."""

    trajectory_ref: ArtifactRef | None = None
    frames: tuple[XtbPathFrame, ...] = ()
    start_frame_index: int | None = None
    end_frame_index: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "frames", tuple(self.frames))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {"frames": [f.to_dict() for f in self.frames]}
        if self.trajectory_ref is not None:
            payload["trajectory_ref"] = _artifact_to_dict(self.trajectory_ref)
        if self.start_frame_index is not None:
            payload["start_frame_index"] = self.start_frame_index
        if self.end_frame_index is not None:
            payload["end_frame_index"] = self.end_frame_index
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> XtbPathSearchPayload:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_ref = payload.get("trajectory_ref")
        if raw_ref is not None and not isinstance(raw_ref, Mapping):
            message = "payload.trajectory_ref must be a mapping"
            raise TaskInputError(message)
        raw_frames = payload.get("frames", [])
        if not isinstance(raw_frames, list):
            message = "payload.frames must be a list"
            raise TaskInputError(message)
        frames: list[XtbPathFrame] = []
        for index, entry in enumerate(raw_frames):
            if not isinstance(entry, Mapping):
                message = f"payload.frames[{index}] must be a mapping"
                raise TaskInputError(message)
            frames.append(XtbPathFrame.from_dict(entry))
        return cls(
            trajectory_ref=_artifact_from_dict(raw_ref),
            frames=tuple(frames),
            start_frame_index=parse_int_strict(payload, "start_frame_index"),
            end_frame_index=parse_int_strict(payload, "end_frame_index"),
        )


@dataclass(frozen=True, slots=True)
class OrcaGradientPayload:
    """Typed payload for ``orca_gradient`` results (atomic gradients + energy).

    ``gradients`` carries unit + convention + input atom order (record
    identity): row ``i`` is the gradient of input atom ``i``, in
    ``gradient_unit`` (native Eh/bohr, energy gradient dE/dX — not force).
    """

    gradients: tuple[tuple[float, float, float], ...] = ()
    energy_hartree: float | None = None
    gradient_unit: str = "Eh/bohr"
    gradient_convention: str = "energy_gradient_dE_dX"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "gradients",
            tuple(tuple(float(c) for c in row) for row in self.gradients),
        )

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {
            "gradients": [[float(c) for c in row] for row in self.gradients],
            "gradient_unit": self.gradient_unit,
            "gradient_convention": self.gradient_convention,
        }
        if self.energy_hartree is not None:
            payload["energy_hartree"] = self.energy_hartree
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object] | None) -> OrcaGradientPayload:
        """Parse strictly; unknown fields are ignored (rule S2)."""
        if not payload:
            return cls()
        raw_gradients = payload.get("gradients", [])
        if not isinstance(raw_gradients, list):
            message = "payload.gradients must be a list"
            raise TaskInputError(message)
        rows: list[tuple[float, float, float]] = []
        for index, row in enumerate(raw_gradients):
            if not isinstance(row, list) or len(row) != 3:
                message = f"payload.gradients[{index}] must be a 3-vector"
                raise TaskInputError(message)
            rows.append(
                (
                    ensure_finite_json_number(row[0], f"gradients[{index}][0]"),
                    ensure_finite_json_number(row[1], f"gradients[{index}][1]"),
                    ensure_finite_json_number(row[2], f"gradients[{index}][2]"),
                )
            )
        return cls(
            gradients=tuple(rows),
            energy_hartree=parse_float_strict(payload, "energy_hartree"),
            gradient_unit=parse_str_strict(payload, "gradient_unit") or "Eh/bohr",
            gradient_convention=parse_str_strict(payload, "gradient_convention")
            or "energy_gradient_dE_dX",
        )


TaskPayload: TypeAlias = (
    SinglePointPayload
    | OptimizePayload
    | FrequencyPayload
    | ScanPayload
    | IrcPayload
    | CasscfPayload
    | ThermochemistryPayload
    | ConformerSearchPayload
    | MdSamplingPayload
    | ClusteringPayload
    | CensoRefinePayload
    | NmrShieldingPayload
    | XtbPathSearchPayload
    | OrcaGradientPayload
)

# Table ① (todo 11/24): task → payload types (paired with TASK_OPTIONS_TYPES).
TASK_PAYLOAD_TYPES: dict[TaskKind, type] = {
    TaskKind.SINGLEPOINT: SinglePointPayload,
    TaskKind.OPTIMIZE: OptimizePayload,
    TaskKind.FREQUENCY: FrequencyPayload,
    TaskKind.SCAN: ScanPayload,
    TaskKind.IRC: IrcPayload,
    TaskKind.CASSCF: CasscfPayload,
    TaskKind.THERMOCHEMISTRY: ThermochemistryPayload,
    TaskKind.CONFORMER_SEARCH: ConformerSearchPayload,
    TaskKind.MD_SAMPLING: MdSamplingPayload,
    TaskKind.CLUSTERING: ClusteringPayload,
    TaskKind.CENSO_REFINE: CensoRefinePayload,
    TaskKind.NMR_SHIELDING: NmrShieldingPayload,
    TaskKind.XTB_PATH_SEARCH: XtbPathSearchPayload,
    TaskKind.ORCA_GRADIENT: OrcaGradientPayload,
}


def payload_from_dict(task: TaskKind, payload: Mapping[str, object] | None) -> TaskPayload | None:
    """Parse the typed payload for ``task``; mismatch raises TaskInputError."""
    if payload is None:
        return None
    payload_cls = TASK_PAYLOAD_TYPES[task]
    try:
        return payload_cls.from_dict(payload)
    except TaskInputError:
        raise
    except ValueError as exc:
        raise TaskInputError(str(exc)) from exc


# ── artifact helpers ────────────────────────────────────────────────────


def _artifact_to_dict(artifact: ArtifactRef) -> JsonObject:
    payload: JsonObject = {"path": str(artifact.path), "type": artifact.type}
    if artifact.checksum:
        payload["checksum"] = artifact.checksum
    if artifact.source:
        payload["source"] = artifact.source
    return payload


def _artifact_from_dict(payload: Mapping[str, object] | None) -> ArtifactRef | None:
    if payload is None:
        return None
    path = parse_path_strict(payload, "path")
    artifact_type = parse_str_strict(payload, "type")
    if path is None or artifact_type is None:
        message = "artifact reference requires 'path' and 'type'"
        raise TaskInputError(message)
    return ArtifactRef(
        path=path,
        type=artifact_type,
        checksum=parse_str_strict(payload, "checksum") or "",
        source=parse_str_strict(payload, "source") or "",
    )


def artifact_ref_to_dict(artifact: ArtifactRef) -> JsonObject:
    """Serialise an :class:`ArtifactRef` to a JSON-safe dict."""
    return _artifact_to_dict(artifact)


def artifact_ref_from_dict(payload: Mapping[str, object]) -> ArtifactRef:
    """Parse an :class:`ArtifactRef` strictly."""
    artifact = _artifact_from_dict(payload)
    if artifact is None:
        message = "artifact reference must be a mapping"
        raise TaskInputError(message)
    return artifact


# ── TaskResult envelope ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TaskResult:
    """Structured scientific outcome of one task execution.

    Units: coordinates in Å, energies in Hartree, frequencies in cm⁻¹,
    entropy in au.  ``complete=False`` marks partial scientific output
    (valid sub-items are kept; there is no global PARTIAL status).
    """

    task: TaskKind
    status: str = "completed"
    complete: bool = True
    error_kind: ErrorKind | None = None
    errors: tuple[str, ...] = ()
    energy_hartree: float | None = None
    coordinates: tuple[tuple[float, float, float], ...] | None = None
    symbols: tuple[str, ...] | None = None
    frequencies: tuple[float, ...] = ()
    converged: bool | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    provenance: Provenance | None = None
    payload: TaskPayload | None = None
    metadata: JsonObject = field(default_factory=dict)
    schema_version: int = TASK_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        try:
            task = TaskKind(self.task)
        except ValueError as exc:
            allowed = ", ".join(kind.value for kind in TaskKind)
            message = f"task must be one of: {allowed}"
            raise ValueError(message) from exc
        object.__setattr__(self, "task", task)
        object.__setattr__(self, "errors", tuple(str(e) for e in self.errors))
        object.__setattr__(self, "frequencies", tuple(float(f) for f in self.frequencies))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        if self.coordinates is not None:
            object.__setattr__(
                self,
                "coordinates",
                tuple(tuple(float(c) for c in row) for row in self.coordinates),
            )
        if self.symbols is not None:
            object.__setattr__(self, "symbols", tuple(str(s) for s in self.symbols))
        if self.payload is not None:
            expected_cls = TASK_PAYLOAD_TYPES.get(task)
            if expected_cls is not None and not isinstance(self.payload, expected_cls):
                message = (
                    f"payload type mismatch for task {task.value!r}: "
                    f"expected {expected_cls.__name__}, got {type(self.payload).__name__}"
                )
                raise ValueError(message)
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "schema_version", int(self.schema_version))

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict (rule S4: NaN/Inf rejected)."""
        payload: JsonObject = {
            "schema_version": self.schema_version,
            "task": self.task.value,
            "status": self.status,
            "complete": self.complete,
            "errors": list(self.errors),
            "frequencies": list(self.frequencies),
            "artifacts": [_artifact_to_dict(artifact) for artifact in self.artifacts],
            "metadata": self.metadata,
        }
        if self.error_kind is not None:
            payload["error_kind"] = self.error_kind.value
        if self.energy_hartree is not None:
            payload["energy_hartree"] = self.energy_hartree
        if self.coordinates is not None:
            payload["coordinates"] = [[float(c) for c in row] for row in self.coordinates]
        if self.symbols is not None:
            payload["symbols"] = list(self.symbols)
        if self.converged is not None:
            payload["converged"] = self.converged
        if self.provenance is not None:
            payload["provenance"] = {
                "backend": self.provenance.backend,
                "method": self.provenance.method,
                "version": self.provenance.version,
                "input_signature": self.provenance.input_signature,
            }
        if self.payload is not None:
            payload["payload"] = self.payload.to_dict()
        ensure_finite_payload(payload, "TaskResult")
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> TaskResult:
        """Parse strictly (rules S1–S4); unknown fields are ignored (S2).

        Raises:
            TaskInputError: On unknown schema versions, invalid enums,
                type mismatches, or non-finite floats.
        """
        if not isinstance(payload, Mapping):
            message = "TaskResult payload must be a mapping"
            raise TaskInputError(message)
        ensure_finite_payload(payload, "TaskResult")
        version = payload.get("schema_version", TASK_RESULT_SCHEMA_VERSION)
        if isinstance(version, bool) or not isinstance(version, int):
            message = "schema_version must be an integer"
            raise TaskInputError(message)
        if version != TASK_RESULT_SCHEMA_VERSION:
            message = f"unsupported schema_version {version}; expected {TASK_RESULT_SCHEMA_VERSION}"
            raise TaskInputError(message)
        task = parse_enum_strict(TaskKind, payload.get("task"), "task")
        error_kind: ErrorKind | None = None
        raw_error_kind = payload.get("error_kind")
        if raw_error_kind is not None:
            error_kind = parse_enum_strict(ErrorKind, raw_error_kind, "error_kind")
        raw_coordinates = payload.get("coordinates")
        coordinates: tuple[tuple[float, float, float], ...] | None = None
        if raw_coordinates is not None:
            if not isinstance(raw_coordinates, list):
                message = "coordinates must be a list of 3-vectors"
                raise TaskInputError(message)
            rows: list[tuple[float, float, float]] = []
            for index, row in enumerate(raw_coordinates):
                if not isinstance(row, list) or len(row) != 3:
                    message = f"coordinates[{index}] must be a 3-vector"
                    raise TaskInputError(message)
                rows.append((float(row[0]), float(row[1]), float(row[2])))
            coordinates = tuple(rows)
        raw_artifacts = payload.get("artifacts", [])
        if not isinstance(raw_artifacts, list):
            message = "artifacts must be a list"
            raise TaskInputError(message)
        artifacts: list[ArtifactRef] = []
        for index, entry in enumerate(raw_artifacts):
            if not isinstance(entry, Mapping):
                message = f"artifacts[{index}] must be a mapping"
                raise TaskInputError(message)
            artifacts.append(artifact_ref_from_dict(entry))
        raw_provenance = payload.get("provenance")
        provenance: Provenance | None = None
        if raw_provenance is not None:
            if not isinstance(raw_provenance, Mapping):
                message = "provenance must be a mapping"
                raise TaskInputError(message)
            provenance = Provenance(
                backend=parse_str_strict(raw_provenance, "backend") or "",
                method=parse_str_strict(raw_provenance, "method") or "",
                version=parse_str_strict(raw_provenance, "version") or "",
                input_signature=parse_str_strict(raw_provenance, "input_signature") or "",
            )
        raw_payload = payload.get("payload")
        if raw_payload is not None and not isinstance(raw_payload, Mapping):
            message = "payload must be a mapping"
            raise TaskInputError(message)
        raw_metadata = payload.get("metadata")
        if raw_metadata is not None and not isinstance(raw_metadata, Mapping):
            message = "metadata must be a mapping"
            raise TaskInputError(message)
        status = parse_str_strict(payload, "status") or "completed"
        complete = parse_bool_strict(payload, "complete")
        return cls(
            task=task,  # type: ignore[arg-type]
            status=status,
            complete=True if complete is None else complete,
            error_kind=error_kind,
            errors=parse_str_tuple_strict(payload, "errors"),
            energy_hartree=parse_float_strict(payload, "energy_hartree"),
            coordinates=coordinates,
            symbols=parse_str_tuple_strict(payload, "symbols"),
            frequencies=parse_float_tuple_strict(payload, "frequencies"),
            converged=parse_bool_strict(payload, "converged"),
            artifacts=tuple(artifacts),
            provenance=provenance,
            payload=payload_from_dict(task, raw_payload),  # type: ignore[arg-type]
            metadata=dict(raw_metadata) if raw_metadata is not None else {},
            schema_version=version,
        )


__all__ = [
    "TASK_PAYLOAD_TYPES",
    "TASK_RESULT_SCHEMA_VERSION",
    "CasscfPayload",
    "CensoRefinePayload",
    "CensoRefineRecord",
    "ClusterAssignment",
    "ClusteringPayload",
    "ConformerEnergy",
    "ConformerSearchPayload",
    "ErrorKind",
    "FrequencyAnalysis",
    "FrequencyPayload",
    "IrcDirectionResult",
    "IrcPayload",
    "MdSamplingPayload",
    "NmrShielding",
    "NmrShieldingPayload",
    "OptimizePayload",
    "OrcaGradientPayload",
    "ScanFrame",
    "ScanPayload",
    "SinglePointPayload",
    "TaskPayload",
    "TaskResult",
    "ThermochemistryPayload",
    "XtbPathFrame",
    "XtbPathSearchPayload",
    "artifact_ref_from_dict",
    "artifact_ref_to_dict",
    "casscf_payload_from_multireference",
    "payload_from_dict",
]
