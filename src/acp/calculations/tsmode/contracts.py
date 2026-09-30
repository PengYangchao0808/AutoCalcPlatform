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
from typing import Literal

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
    "FrequencySourceBundle",
    "MappingStatus",
    "SourceLevelOfTheory",
    "SourceModeRecord",
    "TargetResolution",
    "TsmodeError",
    "TsmodeOptimizationSettings",
    "TsmodeReport",
    "TsmodeRequest",
    "compute_target_mode_id",
    "geometry_hash",
    "sha256_file",
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
            "attempts": [dict(attempt) for attempt in self.attempts],
            "execution_status": self.execution_status,
            "optimization_status": self.optimization_status,
            "frequency_status": self.frequency_status,
            "imaginary_modes": [dict(mode) for mode in self.imaginary_modes],
            "validation": dict(self.validation),
            "artifacts": [dict(artifact) for artifact in self.artifacts],
            "warnings": list(self.warnings),
        }


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
