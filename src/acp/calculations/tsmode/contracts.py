"""TS Mode workflow contracts: requests, source bundles, target resolution.

Implements the data model of ``docs/ACP_TSMode_Optimization_Implementation_Plan.md``
§4 (FrequencySourceBundle), §6 (three mode-identifier model), §7 (request /
snapshot / error codes) and §12 (report contract ``tsmode_report_v1``).

Three mode identifiers are deliberately distinct (plan §6.1):

* ``source_mode_index`` — the native printed mode index of the frequency
  output (UI positioning only; never passed to the optimizer).
* ``target_mode_id`` — a stable identity bound to the source files AND the
  mode vector; survives retries and resubmission.
* ``optimizer_mode_index`` — the value passed to ORCA ``TS_Mode {M n}``
  (n counts eigenvalues ascending, M 0 = lowest eigenvalue).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from acp.calculations.contracts import JsonValue

__all__ = [
    "FREQUENCY_SOURCE_INCOMPLETE",
    "HESSIAN_MISSING",
    "MODE_MAPPING_AMBIGUOUS",
    "MODE_MAPPING_UNSUPPORTED",
    "SOURCE_FETCH_PENDING",
    "SOURCE_GEOMETRY_MISMATCH",
    "SOURCE_REVISION_CONFLICT",
    "TARGET_MODE_INVALID",
    "TARGET_MODE_MISMATCH",
    "FrequencyCredential",
    "FrequencySourceBundle",
    "MappingStatus",
    "OptimizeCredential",
    "PublicationState",
    "SourceLevelOfTheory",
    "SourceModeRecord",
    "TargetResolution",
    "TsmodeError",
    "TsmodeOptimizationSettings",
    "TsmodeReport",
    "TsmodeRequest",
    "compute_target_mode_id",
    "geometry_hash",
    "optimized_structure_digest",
    "sha256_file",
    "validate_coordinate_array",
    "validate_mode_completeness",
]

# ── Error codes (plan §7.3) ─────────────────────────────────────────────

FREQUENCY_SOURCE_INCOMPLETE = "frequency_source_incomplete"
HESSIAN_MISSING = "hessian_missing"
SOURCE_GEOMETRY_MISMATCH = "source_geometry_mismatch"
TARGET_MODE_INVALID = "target_mode_invalid"
SOURCE_REVISION_CONFLICT = "source_revision_conflict"
MODE_MAPPING_AMBIGUOUS = "mode_mapping_ambiguous"
MODE_MAPPING_UNSUPPORTED = "mode_mapping_unsupported"
TARGET_MODE_MISMATCH = "target_mode_mismatch"
SOURCE_FETCH_PENDING = "source_fetch_pending"

_ERROR_STATUS: dict[str, int] = {
    FREQUENCY_SOURCE_INCOMPLETE: 422,
    HESSIAN_MISSING: 422,
    SOURCE_GEOMETRY_MISMATCH: 422,
    TARGET_MODE_INVALID: 422,
    SOURCE_REVISION_CONFLICT: 409,
    MODE_MAPPING_AMBIGUOUS: 422,
    MODE_MAPPING_UNSUPPORTED: 422,
    TARGET_MODE_MISMATCH: 422,
    SOURCE_FETCH_PENDING: 409,
}

MappingStatus = Literal["pending", "resolved", "ambiguous", "unsupported", "mismatch"]

_TSMODE_SCHEMA_VERSION = "tsmode_request_v1"
_TSMODE_REPORT_SCHEMA_VERSION = "tsmode_report_v1"


class TsmodeError(Exception):
    """Domain error carrying one of the plan §7.3 error codes."""

    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(f"{error_code}: {detail}")
        self.error_code = error_code
        self.detail = detail
        self.http_status = _ERROR_STATUS.get(error_code, 422)


# ── Source bundle (plan §4) ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SourceLevelOfTheory:
    """Level of theory bound to a frequency source.

    ``method``/``basis`` etc. describe the source calculation; the TS Mode
    run inherits them unchanged in the first phase (plan §5.4).
    """

    method: str = ""
    basis: str = ""
    solvent: str | None = None
    solvent_model: str | None = None
    dispersion: str | None = None
    grid: str | None = None
    scf: str | None = None
    orca_version: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "method": self.method,
            "basis": self.basis,
        }
        for key in (
            "solvent",
            "solvent_model",
            "dispersion",
            "grid",
            "scf",
            "orca_version",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> SourceLevelOfTheory:
        def _opt_str(key: str) -> str | None:
            value = data.get(key)
            return value if isinstance(value, str) and value else None

        method = data.get("method")
        basis = data.get("basis")
        return cls(
            method=method if isinstance(method, str) else "",
            basis=basis if isinstance(basis, str) else "",
            solvent=_opt_str("solvent"),
            solvent_model=_opt_str("solvent_model"),
            dispersion=_opt_str("dispersion"),
            grid=_opt_str("grid"),
            scf=_opt_str("scf"),
            orca_version=_opt_str("orca_version"),
        )


@dataclass(frozen=True, slots=True)
class SourceModeRecord:
    """One printed vibrational mode of the frequency source output."""

    source_mode_index: int
    frequency_cm1: float
    vectors: list[list[float]] = field(default_factory=list)

    @property
    def is_imaginary(self) -> bool:
        return self.frequency_cm1 < 0.0

    def has_complete_vectors(self, n_atoms: int) -> bool:
        return (
            len(self.vectors) == n_atoms
            and all(len(row) == 3 for row in self.vectors)
            and any(abs(component) > 0.0 for row in self.vectors for component in row)
        )


def geometry_hash(elements: list[str], coordinates_angstrom: list[list[float]]) -> str:
    """Stable hash over element order and the geometry snapshot.

    Uses fixed-precision (1e-6 Å) canonicalization so the hash is stable
    across platforms; rigid-body invariance is deliberately NOT applied —
    the hash identifies the exact snapshot used by the task.
    """
    payload = {
        "elements": list(elements),
        "coordinates": [[round(float(value), 6) for value in row] for row in coordinates_angstrom],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "geo_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def compute_target_mode_id(
    hessian_sha256: str, source_mode_index: int, vectors: list[list[float]]
) -> str:
    """Stable target identity bound to the Hessian bytes and mode vector.

    A global sign flip of the vector does not change the identity (the same
    directional subspace is intended, plan §5.3): the sign is canonicalized
    so the largest-magnitude displacement component is positive.
    """
    flattened = [round(float(value), 8) for row in vectors for value in row]
    if flattened:
        pivot = max(range(len(flattened)), key=lambda i: abs(flattened[i]))
        if flattened[pivot] < 0:
            flattened = [-value for value in flattened]
    canonical = json.dumps(
        {"hess": hessian_sha256, "mode": int(source_mode_index), "vectors": flattened},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "tm_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class FrequencySourceBundle:
    """Validated immutable snapshot of one frequency result (plan §4).

    The Hessian matrix itself is referenced through ``hessian_file`` (kept
    as a file asset); it is not embedded in the serialized bundle.
    """

    bundle_id: str
    revision: str
    origin: dict[str, JsonValue]
    elements: list[str]
    masses_amu: list[float]
    coordinates_angstrom: list[list[float]]
    charge: int
    multiplicity: int
    level: SourceLevelOfTheory
    modes: list[SourceModeRecord]
    hessian_file: str = ""
    hessian_sha256: str = ""
    output_file: str = ""
    output_sha256: str = ""
    geometry_file: str = ""
    geometry_sha256: str = ""
    parser_version: str = "tsmode_source_v1"
    warnings: list[str] = field(default_factory=list)

    @property
    def n_atoms(self) -> int:
        return len(self.elements)

    def imaginary_modes(self) -> list[SourceModeRecord]:
        return [mode for mode in self.modes if mode.is_imaginary]

    def mode_by_index(self, source_mode_index: int) -> SourceModeRecord | None:
        for mode in self.modes:
            if mode.source_mode_index == source_mode_index:
                return mode
        return None

    @property
    def geo_hash(self) -> str:
        return geometry_hash(self.elements, self.coordinates_angstrom)

    def source_revision(self) -> str:
        """Revision token over all source bytes (plan §10 edit conflicts)."""
        digest = hashlib.sha256()
        for sha in (self.hessian_sha256, self.output_sha256, self.geometry_sha256):
            digest.update((sha or "").encode("utf-8"))
        digest.update(self.geo_hash.encode("utf-8"))
        return "rev_" + digest.hexdigest()[:20]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": "frequency_source_bundle_v1",
            "bundle_id": self.bundle_id,
            "revision": self.revision,
            "origin": dict(self.origin),
            "elements": list(self.elements),
            "masses_amu": [float(value) for value in self.masses_amu],
            "coordinates_angstrom": [
                [float(value) for value in row] for row in self.coordinates_angstrom
            ],
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "level": self.level.to_dict(),
            "modes": [
                {
                    "source_mode_index": mode.source_mode_index,
                    "frequency_cm1": float(mode.frequency_cm1),
                    "vectors": [[float(v) for v in row] for row in mode.vectors],
                }
                for mode in self.modes
            ],
            "files": {
                "hessian": {"path": self.hessian_file, "sha256": self.hessian_sha256},
                "output": {"path": self.output_file, "sha256": self.output_sha256},
                "geometry": {"path": self.geometry_file, "sha256": self.geometry_sha256},
            },
            "geometry_hash": self.geo_hash,
            "parser_version": self.parser_version,
            "warnings": list(self.warnings),
        }


# ── Request (plan §7.1) ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TsmodeOptimizationSettings:
    """Directed OptTS settings (plan §5.4/§9)."""

    initial_hessian: str = "read_source"
    max_iterations: int | None = None
    convergence: str | None = None
    recalc_hess: int | None = None
    trust_radius: float | None = None
    retry_limit: int = 2
    require_verified_mapping: bool = True

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "initial_hessian": self.initial_hessian,
            "max_iterations": self.max_iterations,
            "convergence": self.convergence,
            "recalc_hess": self.recalc_hess,
            "trust_radius": self.trust_radius,
            "retry_limit": self.retry_limit,
            "require_verified_mapping": self.require_verified_mapping,
        }


@dataclass(frozen=True, slots=True)
class TsmodeRequest:
    """Normalized TS Mode request (``tsmode_request_v1``)."""

    source: dict[str, JsonValue]
    source_mode_index: int
    optimization: TsmodeOptimizationSettings = field(default_factory=TsmodeOptimizationSettings)
    final_frequency: bool = True
    resources: dict[str, JsonValue] = field(default_factory=dict)
    request_id: str = ""

    @property
    def schema_version(self) -> str:
        return _TSMODE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "workflow": "tsmode",
            "schema_version": _TSMODE_SCHEMA_VERSION,
            "source": dict(self.source),
            "target": {"source_mode_index": self.source_mode_index},
            "optimization": self.optimization.to_dict(),
            "validation": {"final_frequency": self.final_frequency},
            "resources": dict(self.resources),
            "request_id": self.request_id,
        }


# ── Target resolution (plan §6) ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TargetResolution:
    """Binding between the user's chosen mode and the optimizer mode index."""

    source_mode_index: int
    source_frequency_cm1: float
    target_mode_id: str
    optimizer_mode_index: int | None
    status: MappingStatus
    mapping_method: str = ""
    mapping_version: str = ""
    evidence: dict[str, JsonValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": "target_resolution_v1",
            "source_mode_index": self.source_mode_index,
            "source_frequency_cm1": float(self.source_frequency_cm1),
            "target_mode_id": self.target_mode_id,
            "optimizer_mode_index": self.optimizer_mode_index,
            "status": self.status,
            "mapping_method": self.mapping_method,
            "mapping_version": self.mapping_version,
            "evidence": dict(self.evidence),
        }


# ── Report (plan §12) ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TsmodeReport:
    """``tsmode_report_v1`` — execution and validation kept separate."""

    source: dict[str, JsonValue]
    target: dict[str, JsonValue]
    mapping: dict[str, JsonValue]
    resolved_level: dict[str, JsonValue]
    source_level: dict[str, JsonValue] = field(default_factory=dict)
    attempts: list[dict[str, JsonValue]] = field(default_factory=list)
    execution_status: str = "pending"
    optimization_status: str = "pending"
    frequency_status: str = "pending"
    imaginary_modes: list[dict[str, JsonValue]] = field(default_factory=list)
    validation: dict[str, JsonValue] = field(default_factory=dict)
    artifacts: list[dict[str, JsonValue]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def schema_version(self) -> str:
        return _TSMODE_REPORT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": _TSMODE_REPORT_SCHEMA_VERSION,
            "source": dict(self.source),
            "target": dict(self.target),
            "mapping": dict(self.mapping),
            "resolved_level": dict(self.resolved_level),
            "source_level": dict(self.source_level),
            "attempts": [dict(attempt) for attempt in self.attempts],
            "execution_status": self.execution_status,
            "optimization_status": self.optimization_status,
            "frequency_status": self.frequency_status,
            "imaginary_modes": [dict(mode) for mode in self.imaginary_modes],
            "validation": dict(self.validation),
            "artifacts": [dict(artifact) for artifact in self.artifacts],
            "warnings": list(self.warnings),
        }


_OPTIMIZE_CREDENTIAL_SCHEMA = "tsmode_optimize_credential_v1"
_FREQUENCY_CREDENTIAL_SCHEMA = "tsmode_frequency_credential_v1"


@dataclass(frozen=True, slots=True)
class OptimizeCredential:
    """Checkpoint v2 optimize-stage credential.

    Binds source content, target mode, effective level, optimization
    parameters, the effective config digest and the optimized structure
    content digest to the exact completion artifacts that were present when
    the stage finished.  ``coordinates`` is the validated finite N×3 array;
    it is the ONLY copy of the optimized geometry on the resume path.
    """

    source_content_sha256: str
    target_mode_id: str
    optimizer_mode_index: int | None
    effective_level: dict[str, JsonValue]
    optimization_parameters: dict[str, JsonValue]
    effective_config_digest: str | None
    optimized_structure_sha256: str
    coordinates: list[list[float]]
    elements: list[str]
    required_completion_artifacts: list[dict[str, JsonValue]] = field(default_factory=list)
    energy_hartree: float | None = None

    @property
    def schema_version(self) -> str:
        return _OPTIMIZE_CREDENTIAL_SCHEMA

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "schema_version": self.schema_version,
            "source_content_sha256": self.source_content_sha256,
            "target_mode_id": self.target_mode_id,
            "optimizer_mode_index": self.optimizer_mode_index,
            "effective_level": dict(self.effective_level),
            "optimization_parameters": dict(self.optimization_parameters),
            "effective_config_digest": self.effective_config_digest,
            "optimized_structure_sha256": self.optimized_structure_sha256,
            "coordinates": [[float(value) for value in row] for row in self.coordinates],
            "elements": list(self.elements),
            "required_completion_artifacts": [
                dict(artifact) for artifact in self.required_completion_artifacts
            ],
        }
        if self.energy_hartree is not None:
            payload["energy_hartree"] = float(self.energy_hartree)
        return payload

    @classmethod
    def from_dict(cls, data: Any) -> OptimizeCredential | None:
        if not isinstance(data, dict):
            return None
        source = data.get("source_content_sha256")
        target = data.get("target_mode_id")
        digest = data.get("optimized_structure_sha256")
        if not all(isinstance(value, str) and value for value in (source, target, digest)):
            return None
        level = data.get("effective_level")
        parameters = data.get("optimization_parameters")
        if not isinstance(level, dict) or not isinstance(parameters, dict):
            return None
        raw_coordinates = data.get("coordinates")
        raw_elements = data.get("elements")
        if not isinstance(raw_coordinates, list) or not isinstance(raw_elements, list):
            return None
        try:
            coordinates = [[float(component) for component in row] for row in raw_coordinates]
        except (TypeError, ValueError):
            return None
        optimizer_mode_index = data.get("optimizer_mode_index")
        if optimizer_mode_index is not None and (
            not isinstance(optimizer_mode_index, int) or isinstance(optimizer_mode_index, bool)
        ):
            return None
        config_digest = data.get("effective_config_digest")
        if config_digest is not None and not isinstance(config_digest, str):
            return None
        raw_artifacts = data.get("required_completion_artifacts")
        artifacts = (
            [dict(entry) for entry in raw_artifacts if isinstance(entry, dict)]
            if isinstance(raw_artifacts, list)
            else []
        )
        energy = data.get("energy_hartree")
        return cls(
            source_content_sha256=str(source),
            target_mode_id=str(target),
            optimizer_mode_index=optimizer_mode_index,
            effective_level=dict(level),
            optimization_parameters=dict(parameters),
            effective_config_digest=config_digest,
            optimized_structure_sha256=str(digest),
            coordinates=coordinates,
            elements=[str(element) for element in raw_elements],
            required_completion_artifacts=artifacts,
            energy_hartree=float(energy) if isinstance(energy, (int, float)) else None,
        )


@dataclass(frozen=True, slots=True)
class FrequencyCredential:
    """Checkpoint v2 frequency-stage credential.

    Binds the ADOPTED optimized-structure digest (never a second copy of the
    coordinates), the effective level, the exact frequency/mode artifact
    relative paths + digests, and the validated canonical mode data that the
    resume path replays.  ``expected_mode_indices`` freezes the required
    native mode set; completeness is checked against it rather than assuming
    ``3N-6``.
    """

    adopted_optimized_structure_sha256: str
    effective_level: dict[str, JsonValue]
    expected_mode_indices: list[int] = field(default_factory=list)
    modes: list[dict[str, JsonValue]] = field(default_factory=list)
    frequencies: list[float] = field(default_factory=list)
    artifacts: list[dict[str, JsonValue]] = field(default_factory=list)

    @property
    def schema_version(self) -> str:
        return _FREQUENCY_CREDENTIAL_SCHEMA

    @property
    def mode_frequencies(self) -> dict[int, float]:
        result: dict[int, float] = {}
        for mode in self.modes:
            index = mode.get("mode_index")
            frequency = mode.get("frequency_cm1")
            if (
                isinstance(index, int)
                and not isinstance(index, bool)
                and isinstance(frequency, (int, float))
            ):
                result[int(index)] = float(frequency)
        return result

    @property
    def mode_vectors(self) -> dict[int, list[list[float]]]:
        result: dict[int, list[list[float]]] = {}
        for mode in self.modes:
            index = mode.get("mode_index")
            vectors = mode.get("vectors")
            if isinstance(index, int) and not isinstance(index, bool) and isinstance(vectors, list):
                try:
                    result[int(index)] = [
                        [float(component) for component in row] for row in vectors
                    ]
                except (TypeError, ValueError):
                    continue
        return result

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "adopted_optimized_structure_sha256": self.adopted_optimized_structure_sha256,
            "effective_level": dict(self.effective_level),
            "expected_mode_indices": [int(index) for index in self.expected_mode_indices],
            "modes": [dict(mode) for mode in self.modes],
            "frequencies": [float(value) for value in self.frequencies],
            "artifacts": [dict(artifact) for artifact in self.artifacts],
        }

    @classmethod
    def from_dict(cls, data: Any) -> FrequencyCredential | None:
        if not isinstance(data, dict):
            return None
        adopted = data.get("adopted_optimized_structure_sha256")
        level = data.get("effective_level")
        if not isinstance(adopted, str) or not adopted or not isinstance(level, dict):
            return None
        raw_indices = data.get("expected_mode_indices")
        if not isinstance(raw_indices, list):
            return None
        try:
            expected = [
                int(index)
                for index in raw_indices
                if isinstance(index, int) and not isinstance(index, bool)
            ]
        except (TypeError, ValueError):
            return None
        raw_modes = data.get("modes")
        modes = (
            [dict(mode) for mode in raw_modes if isinstance(mode, dict)]
            if isinstance(raw_modes, list)
            else []
        )
        raw_frequencies = data.get("frequencies")
        try:
            frequencies = (
                [float(value) for value in raw_frequencies]
                if isinstance(raw_frequencies, list)
                else []
            )
        except (TypeError, ValueError):
            frequencies = []
        raw_artifacts = data.get("artifacts")
        artifacts = (
            [dict(entry) for entry in raw_artifacts if isinstance(entry, dict)]
            if isinstance(raw_artifacts, list)
            else []
        )
        return cls(
            adopted_optimized_structure_sha256=adopted,
            effective_level=dict(level),
            expected_mode_indices=expected,
            modes=modes,
            frequencies=frequencies,
            artifacts=artifacts,
        )


@dataclass(frozen=True, slots=True)
class PublicationState:
    """Retryable publication record kept beside the science credentials."""

    status: str = "pending"
    normal_modes_sha256: str | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "status": self.status,
            "normal_modes_sha256": self.normal_modes_sha256,
        }

    @classmethod
    def from_dict(cls, data: Any) -> PublicationState | None:
        if not isinstance(data, dict):
            return None
        status = data.get("status")
        if not isinstance(status, str) or not status:
            return None
        digest = data.get("normal_modes_sha256")
        return cls(
            status=status,
            normal_modes_sha256=digest if isinstance(digest, str) else None,
        )


def optimized_structure_digest(elements: list[str], coordinates: list[list[float]]) -> str:
    """Content digest of the optimized structure (element order + geometry)."""
    return geometry_hash([str(element) for element in elements], coordinates)


def validate_coordinate_array(coordinates: Any, n_atoms: int) -> str:
    """Return ``""`` when *coordinates* is a finite N×3 array, else a reason."""
    try:
        array = np.asarray(coordinates, dtype=np.float64)
    except (TypeError, ValueError):
        return "coordinates_not_numeric"
    if array.ndim != 2 or array.shape[1] != 3:
        return "coordinates_shape_not_n_by_3"
    if array.shape[0] != int(n_atoms):
        return "coordinates_atom_count_mismatch"
    if not bool(np.all(np.isfinite(array))):
        return "coordinates_not_finite"
    return ""


def validate_mode_completeness(
    expected_indices: list[int],
    frequencies: dict[int, float],
    vectors: dict[int, list[list[float]]],
    n_atoms: int,
) -> list[str]:
    """Report every required native mode that is missing or not finite N×3.

    The expected set is the frozen native mode index set — zero-frequency
    modes never printed by ORCA are simply absent and therefore not required;
    ``3N-6`` is never assumed.
    """
    problems: list[str] = []
    for index in sorted({int(value) for value in expected_indices}):
        if index not in frequencies:
            problems.append(f"mode {index}: frequency missing")
            continue
        raw = vectors.get(index)
        if raw is None:
            problems.append(f"mode {index}: vectors missing")
            continue
        try:
            array = np.asarray(raw, dtype=np.float64)
        except (TypeError, ValueError):
            problems.append(f"mode {index}: vectors not numeric")
            continue
        if array.shape != (int(n_atoms), 3):
            problems.append(f"mode {index}: vectors shape {array.shape} != ({n_atoms}, 3)")
            continue
        if not bool(np.all(np.isfinite(array))):
            problems.append(f"mode {index}: vectors not finite")
    return problems


def sha256_file(path: str | Path) -> str:
    """Hex SHA-256 of a file (empty string when unreadable)."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()
