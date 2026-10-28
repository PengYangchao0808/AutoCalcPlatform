"""TS Mode workflow contracts: requests, reports, checkpoint credentials.

Platform-side contract module of
``docs/archive/2026-10-16/history/ACP_TSMode_Optimization_Implementation_Plan.md``
§7 (request / snapshot / error codes) and §12 (report contract
``tsmode_report_v1``), plus the checkpoint v2 credentials.

**T23 split**: the platform-agnostic *science* contracts (error codes,
``TsmodeError``, source bundle/level/mode records, target resolution, and the
identity helpers) now live in :mod:`cccp.analysis.tsmode.contracts`.  This
module re-exports those **same objects** so the historical
``acp.calculations.tsmode.contracts`` import path keeps working, and keeps the
ACP-only request/report/credential contracts local.

Three mode identifiers are deliberately distinct (plan §6.1):

* ``source_mode_index`` — the native printed mode index of the frequency
  output (UI positioning only; never passed to the optimizer).
* ``target_mode_id`` — a stable identity bound to the source files AND the
  mode vector; survives retries and resubmission.
* ``optimizer_mode_index`` — the value passed to ORCA ``TS_Mode {M n}``
  (n counts eigenvalues ascending, M 0 = lowest eigenvalue).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from acp.calculations.contracts import JsonValue
from cccp.analysis.tsmode.contracts import (
    FREQUENCY_SOURCE_INCOMPLETE,
    HESSIAN_MISSING,
    MODE_MAPPING_AMBIGUOUS,
    MODE_MAPPING_UNSUPPORTED,
    SOURCE_FETCH_PENDING,
    SOURCE_GEOMETRY_MISMATCH,
    SOURCE_REVISION_CONFLICT,
    TARGET_MODE_INVALID,
    TARGET_MODE_MISMATCH,
    FrequencySourceBundle,
    MappingStatus,
    SourceLevelOfTheory,
    SourceModeRecord,
    TargetResolution,
    TsmodeError,
    compute_target_mode_id,
    geometry_hash,
    optimized_structure_digest,
    sha256_file,
    validate_coordinate_array,
    validate_mode_completeness,
)

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

_TSMODE_SCHEMA_VERSION = "tsmode_request_v1"
_TSMODE_REPORT_SCHEMA_VERSION = "tsmode_report_v1"


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
