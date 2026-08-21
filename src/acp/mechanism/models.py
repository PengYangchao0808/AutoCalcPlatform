"""Mechanism study-layer data models.

This module preserves the legacy path/TS data structures while extending them
for the contract-first study pipeline (M0): study/network/frontier/
provenance/gates.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
from numpy.typing import NDArray

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan

from ._helpers import opt_float as _opt_float
from ._helpers import opt_int as _opt_int
from ._helpers import opt_str as _opt_str

PathStrategy = Literal["guided-scan", "rph-reverse", "direct-ts", "endpoint-path"]
Fidelity = Literal["s3", "s4"]


def _json_compatible(value: Any) -> Any:
    """Return a JSON-compatible representation of a nested value."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, deque)):
        return [_json_compatible(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return cast(Any, to_dict)()
    return value


def _serialize_coordinate_spec(spec: CoordinateSpec) -> dict[str, Any]:
    return {
        "id": spec.id,
        "kind": spec.kind,
        "atoms": list(spec.atoms),
        "role": spec.role,
        "start": spec.start,
        "end": spec.end,
        "force_constant": spec.force_constant,
    }


def _serialize_coordinate_plan(plan: ReactionCoordinatePlan) -> dict[str, Any]:
    return {
        "coordinates": [_serialize_coordinate_spec(spec) for spec in plan.coordinates],
        "points": plan.points,
        "coupling": plan.coupling,
        "start_from": plan.start_from,
    }


def _deserialize_coordinate_plan(
    data: dict[str, Any] | ReactionCoordinatePlan,
) -> ReactionCoordinatePlan:
    if isinstance(data, ReactionCoordinatePlan):
        return data
    return ReactionCoordinatePlan.from_dict(data)


def _serialize_structure(structure: Structure) -> dict[str, Any]:
    return {
        "id": structure.id,
        "charge": structure.charge,
        "multiplicity": structure.multiplicity,
        "symbols": list(structure.symbols),
        "coordinates": (
            np.asarray(structure.coordinates, dtype=float).tolist()
            if structure.coordinates is not None
            else None
        ),
        "metadata": _json_compatible(structure.metadata),
    }


def _deserialize_structure(data: dict[str, Any]) -> Structure:
    return Structure(
        id=str(data.get("id") or "structure"),
        charge=int(data.get("charge") or 0),
        multiplicity=int(data.get("multiplicity") or 1),
        symbols=[str(symbol) for symbol in data.get("symbols") or []],
        coordinates=data.get("coordinates"),
        metadata=dict(data.get("metadata") or {}),
    )


def _serialize_structure_record(record: StructureRecord) -> dict[str, Any]:
    return {
        "structure": _serialize_structure(record.structure),
        "energy_hartree": record.energy_hartree,
        "free_energy_hartree": record.free_energy_hartree,
        "weight": record.weight,
        "properties": _json_compatible(record.properties),
        "files": {name: str(path) for name, path in record.files.items()},
    }


def _deserialize_structure_record(data: dict[str, Any]) -> StructureRecord:
    return StructureRecord(
        structure=_deserialize_structure(dict(data.get("structure") or {})),
        energy_hartree=_opt_float(data.get("energy_hartree")),
        free_energy_hartree=_opt_float(data.get("free_energy_hartree")),
        weight=_opt_float(data.get("weight")),
        properties=dict(data.get("properties") or {}),
        files={str(name): Path(str(path)) for name, path in dict(data.get("files") or {}).items()},
    )


def _serialize_structure_ensemble(ensemble: StructureEnsemble) -> dict[str, Any]:
    return {
        "records": [_serialize_structure_record(record) for record in ensemble.records],
        "data": _json_compatible(ensemble.data),
        "temperature": ensemble.temperature,
        "metadata": _json_compatible(ensemble.metadata),
    }


def _deserialize_structure_ensemble(data: dict[str, Any] | None) -> StructureEnsemble | None:
    if data is None:
        return None
    return StructureEnsemble(
        records=[
            _deserialize_structure_record(dict(record_data))
            for record_data in cast(list[dict[str, Any]], data.get("records") or [])
        ],
        data=list(data.get("data") or []),
        temperature=float(data.get("temperature") or 298.15),
        metadata=dict(data.get("metadata") or {}),
    )


@dataclass(frozen=True)
class Provenance:
    """Provider/strategy provenance for a derived result."""

    provider: str
    provider_version: str
    provider_commit: str
    strategy: str
    strategy_version: str
    profile_id: str
    schema_version: str
    input_signature: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_version": self.provider_version,
            "provider_commit": self.provider_commit,
            "strategy": self.strategy,
            "strategy_version": self.strategy_version,
            "profile_id": self.profile_id,
            "schema_version": self.schema_version,
            "input_signature": self.input_signature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provenance:
        return cls(
            provider=str(data.get("provider") or ""),
            provider_version=str(data.get("provider_version") or ""),
            provider_commit=str(data.get("provider_commit") or ""),
            strategy=str(data.get("strategy") or ""),
            strategy_version=str(data.get("strategy_version") or ""),
            profile_id=str(data.get("profile_id") or ""),
            schema_version=str(data.get("schema_version") or ""),
            input_signature=str(data.get("input_signature") or ""),
        )


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to a persisted artifact with a checksum."""

    path: str
    sha256: str
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "kind": self.kind}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactRef:
        return cls(
            path=str(data.get("path") or ""),
            sha256=str(data.get("sha256") or ""),
            kind=str(data.get("kind") or ""),
        )


@dataclass(frozen=True)
class AtomIdentityMap:
    """Stable atom-identity mapping across structures and providers."""

    uid_to_structure_index: dict[str, int]
    mapping: dict[str, dict[str, int]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid_to_structure_index": dict(self.uid_to_structure_index),
            "mapping": {
                key: {inner_key: int(inner_value) for inner_key, inner_value in inner.items()}
                for key, inner in self.mapping.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AtomIdentityMap:
        return cls(
            uid_to_structure_index={
                str(key): int(value)
                for key, value in dict(data.get("uid_to_structure_index") or {}).items()
            },
            mapping={
                str(key): {
                    str(inner_key): int(inner_value) for inner_key, inner_value in inner.items()
                }
                for key, inner in dict(data.get("mapping") or {}).items()
            },
        )


@dataclass(frozen=True)
class ThermoCorrection:
    """Ensemble/standard-state thermochemistry correction container."""

    ensemble_delta_g_hartree: float | None = None
    standard_state_delta_g_hartree: float | None = None
    qrrho_delta_g_hartree: float | None = None
    temperature: float = 298.15
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ensemble_delta_g_hartree": self.ensemble_delta_g_hartree,
            "standard_state_delta_g_hartree": self.standard_state_delta_g_hartree,
            "qrrho_delta_g_hartree": self.qrrho_delta_g_hartree,
            "temperature": self.temperature,
            "metadata": _json_compatible(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThermoCorrection:
        return cls(
            ensemble_delta_g_hartree=_opt_float(data.get("ensemble_delta_g_hartree")),
            standard_state_delta_g_hartree=_opt_float(data.get("standard_state_delta_g_hartree")),
            qrrho_delta_g_hartree=_opt_float(data.get("qrrho_delta_g_hartree")),
            temperature=float(data.get("temperature") or 298.15),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class MechanismRoute:
    """One reaction pathway: coordinate plan + path strategy + fidelity."""

    route_id: str
    coordinate_plan: ReactionCoordinatePlan
    path_strategy: str = "guided-scan"
    fidelity: str = "s3"
    reactant_id: str | None = None
    product_id: str | None = None
    ts_guess_id: str | None = None
    label: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MechanismRoute:
        plan_data = data.get("coordinate_plan")
        if isinstance(plan_data, dict):
            plan = ReactionCoordinatePlan.from_dict(plan_data)
        elif isinstance(plan_data, ReactionCoordinatePlan):
            plan = plan_data
        else:
            raise ValueError("MechanismRoute: missing 'coordinate_plan'")
        return cls(
            route_id=str(data.get("route_id") or "route-1"),
            coordinate_plan=plan,
            path_strategy=str(data.get("path_strategy") or "guided-scan"),
            fidelity=str(data.get("fidelity") or "s3"),
            reactant_id=_opt_str(data.get("reactant_id")),
            product_id=_opt_str(data.get("product_id")),
            ts_guess_id=_opt_str(data.get("ts_guess_id")),
            label=str(data.get("label") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "coordinate_plan": _serialize_coordinate_plan(self.coordinate_plan),
            "path_strategy": self.path_strategy,
            "fidelity": self.fidelity,
            "reactant_id": self.reactant_id,
            "product_id": self.product_id,
            "ts_guess_id": self.ts_guess_id,
            "label": self.label,
        }


@dataclass
class PathPoint:
    """One frame on the reaction path."""

    point_id: str
    progress: float
    coordinate_values: dict[str, float] = field(default_factory=dict)
    geometry: NDArray[np.float64] | None = None
    energies_hartree: dict[str, float | None] = field(default_factory=dict)
    topology: dict[str, Any] | None = None
    frame_index: int | None = None
    reaction_coordinates: dict[str, float] = field(default_factory=dict)
    arc_length: float | None = None
    topology_valid: bool | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        if self.geometry is not None and not isinstance(self.geometry, np.ndarray):
            self.geometry = np.asarray(self.geometry, dtype=float)
        if self.coordinate_values and not self.reaction_coordinates:
            self.reaction_coordinates = dict(self.coordinate_values)
        elif self.reaction_coordinates and not self.coordinate_values:
            self.coordinate_values = dict(self.reaction_coordinates)
        if self.topology_valid is None and self.topology is not None:
            valid = self.topology.get("valid")
            self.topology_valid = bool(valid) if valid is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "point_id": self.point_id,
            "progress": self.progress,
            "coordinate_values": dict(self.coordinate_values),
            "reaction_coordinates": dict(self.reaction_coordinates),
            "geometry": self.geometry.tolist() if self.geometry is not None else None,
            "energies_hartree": dict(self.energies_hartree),
            "topology": _json_compatible(self.topology),
            "frame_index": self.frame_index,
            "arc_length": self.arc_length,
            "topology_valid": self.topology_valid,
            "diagnostics": _json_compatible(self.diagnostics),
            "provenance": self.provenance.to_dict() if self.provenance is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PathPoint:
        geometry = data.get("geometry")
        return cls(
            point_id=str(data.get("point_id") or ""),
            progress=float(data.get("progress") or 0.0),
            coordinate_values={
                str(key): float(value)
                for key, value in dict(data.get("coordinate_values") or {}).items()
            },
            geometry=np.asarray(geometry, dtype=float) if geometry is not None else None,
            energies_hartree={
                str(key): (_opt_float(value) if value is not None else None)
                for key, value in dict(data.get("energies_hartree") or {}).items()
            },
            topology=cast(dict[str, Any] | None, data.get("topology")),
            frame_index=_opt_int(data.get("frame_index")),
            reaction_coordinates={
                str(key): float(value)
                for key, value in dict(
                    data.get("reaction_coordinates") or data.get("coordinate_values") or {}
                ).items()
            },
            arc_length=_opt_float(data.get("arc_length")),
            topology_valid=(
                bool(data.get("topology_valid")) if data.get("topology_valid") is not None else None
            ),
            diagnostics=dict(data.get("diagnostics") or {}),
            provenance=(
                Provenance.from_dict(dict(data.get("provenance") or {}))
                if isinstance(data.get("provenance"), dict)
                else None
            ),
        )


@dataclass
class PathCandidate:
    """A chemically meaningful point selected from a path."""

    candidate_id: str
    kind: Literal["ts_seed", "intermediate_seed", "endpoint"]
    point_id: str
    reason: str
    progress: float
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "point_id": self.point_id,
            "reason": self.reason,
            "progress": self.progress,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PathCandidate:
        return cls(
            candidate_id=str(data.get("candidate_id") or data.get("id") or ""),
            kind=cast(
                Literal["ts_seed", "intermediate_seed", "endpoint"],
                data.get("kind") or "ts_seed",
            ),
            point_id=str(data.get("point_id") or ""),
            reason=str(data.get("reason") or data.get("selection_mode") or ""),
            progress=float(data.get("progress") or 0.0),
            score=_opt_float(data.get("score")),
        )


@dataclass
class SeedCandidate:
    """Unified S2→S3 candidate hand-off schema."""

    id: str
    kind: Literal["ts_seed", "intermediate_seed"]
    geometry: ArtifactRef
    rank: int
    selection_mode: str
    confidence: str
    evidence: dict[str, Any]
    stationary_point_claimed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "geometry": self.geometry.to_dict(),
            "rank": self.rank,
            "selection_mode": self.selection_mode,
            "confidence": self.confidence,
            "evidence": _json_compatible(self.evidence),
            "stationary_point_claimed": self.stationary_point_claimed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeedCandidate:
        geometry_data = data.get("geometry")
        geometry = (
            ArtifactRef.from_dict(dict(geometry_data))
            if isinstance(geometry_data, dict)
            else ArtifactRef(path="", sha256="", kind="")
        )
        return cls(
            id=str(data.get("id") or ""),
            kind=cast(Literal["ts_seed", "intermediate_seed"], data.get("kind") or "ts_seed"),
            geometry=geometry,
            rank=int(data.get("rank") or 0),
            selection_mode=str(data.get("selection_mode") or ""),
            confidence=str(data.get("confidence") or ""),
            evidence=dict(data.get("evidence") or {}),
            stationary_point_claimed=bool(data.get("stationary_point_claimed", False)),
        )


@dataclass
class PathResult:
    """Output of a path-search strategy."""

    points: list[PathPoint]
    candidates: list[PathCandidate]
    strategy: str
    route_id: str
    selected_ts_id: str | None = None
    selected_int_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    seed_candidates: list[SeedCandidate] = field(default_factory=list)
    strategy_id: str | None = None
    strategy_version: str | None = None
    complete: bool | None = None
    endpoint_evidence: dict[str, Any] = field(default_factory=dict)
    topology_segments: list[Any] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    def point_by_id(self, point_id: str) -> PathPoint | None:
        for point in self.points:
            if point.point_id == point_id:
                return point
        return None

    def energies_as_table(self, method: str) -> list[tuple[str, float]]:
        """Return (point_id, energy) rows for one method."""
        rows: list[tuple[str, float]] = []
        for point in sorted(self.points, key=lambda item: item.progress):
            energy = point.energies_hartree.get(method)
            if energy is not None:
                rows.append((point.point_id, energy))
        return rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "route_id": self.route_id,
            "points": [point.to_dict() for point in self.points],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "selected_ts_id": self.selected_ts_id,
            "selected_int_id": self.selected_int_id,
            "metadata": _json_compatible(self.metadata),
            "seed_candidates": [candidate.to_dict() for candidate in self.seed_candidates],
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "complete": self.complete,
            "endpoint_evidence": _json_compatible(self.endpoint_evidence),
            "topology_segments": _json_compatible(self.topology_segments),
            "artifacts": _json_compatible(self.artifacts),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PathResult:
        return cls(
            points=[
                PathPoint.from_dict(dict(point_data))
                for point_data in cast(list[dict[str, Any]], data.get("points") or [])
            ],
            candidates=[
                PathCandidate.from_dict(dict(candidate_data))
                for candidate_data in cast(list[dict[str, Any]], data.get("candidates") or [])
            ],
            strategy=str(data.get("strategy") or data.get("strategy_id") or "guided-scan"),
            route_id=str(data.get("route_id") or "route-1"),
            selected_ts_id=_opt_str(data.get("selected_ts_id")),
            selected_int_id=_opt_str(data.get("selected_int_id")),
            metadata=dict(data.get("metadata") or {}),
            seed_candidates=[
                SeedCandidate.from_dict(dict(candidate_data))
                for candidate_data in cast(list[dict[str, Any]], data.get("seed_candidates") or [])
            ],
            strategy_id=_opt_str(data.get("strategy_id")),
            strategy_version=_opt_str(data.get("strategy_version")),
            complete=(bool(data.get("complete")) if data.get("complete") is not None else None),
            endpoint_evidence=dict(data.get("endpoint_evidence") or {}),
            topology_segments=list(data.get("topology_segments") or []),
            artifacts=dict(data.get("artifacts") or {}),
        )


@dataclass
class StableStateNode:
    """Network node payload for one stable state."""

    state_id: str
    canonical_geometry: ArtifactRef
    ensemble: StructureEnsemble | None
    charge: int
    multiplicity: int
    identity_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "canonical_geometry": self.canonical_geometry.to_dict(),
            "ensemble": (
                _serialize_structure_ensemble(self.ensemble) if self.ensemble is not None else None
            ),
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "identity_fingerprint": self.identity_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StableStateNode:
        geometry_data = data.get("canonical_geometry")
        if not isinstance(geometry_data, dict):
            raise ValueError("StableStateNode: missing canonical_geometry")
        return cls(
            state_id=str(data.get("state_id") or ""),
            canonical_geometry=ArtifactRef.from_dict(dict(geometry_data)),
            ensemble=_deserialize_structure_ensemble(
                cast(dict[str, Any] | None, data.get("ensemble"))
            ),
            charge=int(data.get("charge") or 0),
            multiplicity=int(data.get("multiplicity") or 1),
            identity_fingerprint=str(data.get("identity_fingerprint") or ""),
        )


@dataclass
class StableState:
    """Persisted stable chemical state used by the study layer."""

    state_id: str
    role: Literal["reactant", "product", "intermediate"]
    canonical_geometry: ArtifactRef
    charge: int
    multiplicity: int
    identity_fingerprint: str
    ensemble: StructureEnsemble | None = None
    provenance: Provenance | None = None
    artifacts: list[ArtifactRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_node(self) -> StableStateNode:
        return StableStateNode(
            state_id=self.state_id,
            canonical_geometry=self.canonical_geometry,
            ensemble=self.ensemble,
            charge=self.charge,
            multiplicity=self.multiplicity,
            identity_fingerprint=self.identity_fingerprint,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "role": self.role,
            "canonical_geometry": self.canonical_geometry.to_dict(),
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "identity_fingerprint": self.identity_fingerprint,
            "ensemble": (
                _serialize_structure_ensemble(self.ensemble) if self.ensemble is not None else None
            ),
            "provenance": self.provenance.to_dict() if self.provenance is not None else None,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "metadata": _json_compatible(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StableState:
        geometry_data = data.get("canonical_geometry")
        if not isinstance(geometry_data, dict):
            raise ValueError("StableState: missing canonical_geometry")
        return cls(
            state_id=str(data.get("state_id") or ""),
            role=cast(
                Literal["reactant", "product", "intermediate"],
                data.get("role") or "intermediate",
            ),
            canonical_geometry=ArtifactRef.from_dict(dict(geometry_data)),
            charge=int(data.get("charge") or 0),
            multiplicity=int(data.get("multiplicity") or 1),
            identity_fingerprint=str(data.get("identity_fingerprint") or ""),
            ensemble=_deserialize_structure_ensemble(
                cast(dict[str, Any] | None, data.get("ensemble"))
            ),
            provenance=(
                Provenance.from_dict(dict(data.get("provenance") or {}))
                if isinstance(data.get("provenance"), dict)
                else None
            ),
            artifacts=[
                ArtifactRef.from_dict(dict(artifact_data))
                for artifact_data in cast(list[dict[str, Any]], data.get("artifacts") or [])
            ],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class StationaryPointRequest:
    """Provider-agnostic stationary-point refinement request."""

    id: str
    role: Literal["reactant", "product", "intermediate", "transition_state"]
    kind: Literal["minimum", "ts"]
    input_geometry: ArtifactRef
    coordinate_plan: ReactionCoordinatePlan | None
    fallback_geometries: list[ArtifactRef]
    source_stage: str
    charge: int
    multiplicity: int
    atom_mapping: AtomIdentityMap | None
    parent_state_id: str | None
    route_id: str | None
    ensemble_correction: ThermoCorrection | None
    provenance: Provenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "kind": self.kind,
            "input_geometry": self.input_geometry.to_dict(),
            "coordinate_plan": (
                _serialize_coordinate_plan(self.coordinate_plan)
                if self.coordinate_plan is not None
                else None
            ),
            "fallback_geometries": [artifact.to_dict() for artifact in self.fallback_geometries],
            "source_stage": self.source_stage,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "atom_mapping": self.atom_mapping.to_dict() if self.atom_mapping is not None else None,
            "parent_state_id": self.parent_state_id,
            "route_id": self.route_id,
            "ensemble_correction": (
                self.ensemble_correction.to_dict() if self.ensemble_correction is not None else None
            ),
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StationaryPointRequest:
        geometry_data = data.get("input_geometry")
        if not isinstance(geometry_data, dict):
            raise ValueError("StationaryPointRequest: missing input_geometry")
        coordinate_plan_data = data.get("coordinate_plan")
        atom_mapping_data = data.get("atom_mapping")
        ensemble_correction_data = data.get("ensemble_correction")
        provenance_data = data.get("provenance")
        if not isinstance(provenance_data, dict):
            raise ValueError("StationaryPointRequest: missing provenance")
        return cls(
            id=str(data.get("id") or ""),
            role=cast(
                Literal["reactant", "product", "intermediate", "transition_state"],
                data.get("role") or "reactant",
            ),
            kind=cast(Literal["minimum", "ts"], data.get("kind") or "minimum"),
            input_geometry=ArtifactRef.from_dict(dict(geometry_data)),
            coordinate_plan=(
                _deserialize_coordinate_plan(dict(coordinate_plan_data))
                if isinstance(coordinate_plan_data, dict)
                else None
            ),
            fallback_geometries=[
                ArtifactRef.from_dict(dict(artifact_data))
                for artifact_data in cast(
                    list[dict[str, Any]], data.get("fallback_geometries") or []
                )
            ],
            source_stage=str(data.get("source_stage") or ""),
            charge=int(data.get("charge") or 0),
            multiplicity=int(data.get("multiplicity") or 1),
            atom_mapping=(
                AtomIdentityMap.from_dict(dict(atom_mapping_data))
                if isinstance(atom_mapping_data, dict)
                else None
            ),
            parent_state_id=_opt_str(data.get("parent_state_id")),
            route_id=_opt_str(data.get("route_id")),
            ensemble_correction=(
                ThermoCorrection.from_dict(dict(ensemble_correction_data))
                if isinstance(ensemble_correction_data, dict)
                else None
            ),
            provenance=Provenance.from_dict(dict(provenance_data)),
        )


@dataclass
class TsIdentity:
    """TS-identity verdict for one optimized candidate."""

    imaginary_count: int
    imaginary_frequency_cm1: float | None = None
    mode_match_score: float | None = None
    topology_sane: bool = False
    valid: bool = False
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "imaginary_count": self.imaginary_count,
            "imaginary_frequency_cm1": self.imaginary_frequency_cm1,
            "mode_match_score": self.mode_match_score,
            "topology_sane": self.topology_sane,
            "valid": self.valid,
            "messages": list(self.messages),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TsIdentity:
        return cls(
            imaginary_count=int(data.get("imaginary_count") or 0),
            imaginary_frequency_cm1=_opt_float(data.get("imaginary_frequency_cm1")),
            mode_match_score=_opt_float(data.get("mode_match_score")),
            topology_sane=bool(data.get("topology_sane", False)),
            valid=bool(data.get("valid", False)),
            messages=[str(message) for message in data.get("messages") or []],
        )


@dataclass
class TsValidation:
    """Aggregated TS validation across optimized candidates."""

    identities: list[TsIdentity] = field(default_factory=list)
    selected_candidate_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "identities": [identity.to_dict() for identity in self.identities],
            "selected_candidate_id": self.selected_candidate_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TsValidation:
        return cls(
            identities=[
                TsIdentity.from_dict(dict(identity_data))
                for identity_data in cast(list[dict[str, Any]], data.get("identities") or [])
            ],
            selected_candidate_id=_opt_str(data.get("selected_candidate_id")),
        )


@dataclass
class StationaryPoint:
    """Refined stationary point (minimum or transition state)."""

    point_id: str
    role: Literal["reactant", "product", "intermediate", "transition_state"]
    kind: Literal["minimum", "ts"]
    geometry: ArtifactRef
    charge: int
    multiplicity: int
    state_id: str | None = None
    route_id: str | None = None
    energy_hartree: float | None = None
    identity: TsIdentity | None = None
    validation: TsValidation | None = None
    provenance: Provenance | None = None
    artifacts: list[ArtifactRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "point_id": self.point_id,
            "role": self.role,
            "kind": self.kind,
            "geometry": self.geometry.to_dict(),
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "state_id": self.state_id,
            "route_id": self.route_id,
            "energy_hartree": self.energy_hartree,
            "identity": self.identity.to_dict() if self.identity is not None else None,
            "validation": self.validation.to_dict() if self.validation is not None else None,
            "provenance": self.provenance.to_dict() if self.provenance is not None else None,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "metadata": _json_compatible(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StationaryPoint:
        geometry_data = data.get("geometry")
        if not isinstance(geometry_data, dict):
            raise ValueError("StationaryPoint: missing geometry")
        identity_data = data.get("identity")
        validation_data = data.get("validation")
        provenance_data = data.get("provenance")
        return cls(
            point_id=str(data.get("point_id") or ""),
            role=cast(
                Literal["reactant", "product", "intermediate", "transition_state"],
                data.get("role") or "intermediate",
            ),
            kind=cast(Literal["minimum", "ts"], data.get("kind") or "minimum"),
            geometry=ArtifactRef.from_dict(dict(geometry_data)),
            charge=int(data.get("charge") or 0),
            multiplicity=int(data.get("multiplicity") or 1),
            state_id=_opt_str(data.get("state_id")),
            route_id=_opt_str(data.get("route_id")),
            energy_hartree=_opt_float(data.get("energy_hartree")),
            identity=(
                TsIdentity.from_dict(dict(identity_data))
                if isinstance(identity_data, dict)
                else None
            ),
            validation=(
                TsValidation.from_dict(dict(validation_data))
                if isinstance(validation_data, dict)
                else None
            ),
            provenance=(
                Provenance.from_dict(dict(provenance_data))
                if isinstance(provenance_data, dict)
                else None
            ),
            artifacts=[
                ArtifactRef.from_dict(dict(artifact_data))
                for artifact_data in cast(list[dict[str, Any]], data.get("artifacts") or [])
            ],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class ElementaryStepEdge:
    """Directed elementary step in the chemical multigraph."""

    step_id: str
    source_state_id: str
    sink_state_id: str
    ts_id: str
    path_strategy: str
    coordinate_plan: ReactionCoordinatePlan
    irc_connectivity: dict[str, Any]
    barrier_forward: float | None
    barrier_reverse: float | None
    fidelity: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "source_state_id": self.source_state_id,
            "sink_state_id": self.sink_state_id,
            "ts_id": self.ts_id,
            "path_strategy": self.path_strategy,
            "coordinate_plan": _serialize_coordinate_plan(self.coordinate_plan),
            "irc_connectivity": _json_compatible(self.irc_connectivity),
            "barrier_forward": self.barrier_forward,
            "barrier_reverse": self.barrier_reverse,
            "fidelity": self.fidelity,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ElementaryStepEdge:
        coordinate_plan_data = data.get("coordinate_plan")
        if not isinstance(coordinate_plan_data, dict):
            raise ValueError("ElementaryStepEdge: missing coordinate_plan")
        return cls(
            step_id=str(data.get("step_id") or ""),
            source_state_id=str(data.get("source_state_id") or ""),
            sink_state_id=str(data.get("sink_state_id") or ""),
            ts_id=str(data.get("ts_id") or ""),
            path_strategy=str(data.get("path_strategy") or ""),
            coordinate_plan=_deserialize_coordinate_plan(dict(coordinate_plan_data)),
            irc_connectivity=dict(data.get("irc_connectivity") or {}),
            barrier_forward=_opt_float(data.get("barrier_forward")),
            barrier_reverse=_opt_float(data.get("barrier_reverse")),
            fidelity=str(data.get("fidelity") or "s3"),
            status=str(data.get("status") or "discovered"),
        )


@dataclass
class ReactionNetwork:
    """Directed multigraph of stable states and elementary steps."""

    nodes: dict[str, StableStateNode] = field(default_factory=dict)
    edges: list[ElementaryStepEdge] = field(default_factory=list)

    def add_node(self, node: StableStateNode) -> None:
        self.nodes[node.state_id] = node

    def add_edge(self, edge: ElementaryStepEdge) -> None:
        self.edges.append(edge)

    def edges_between(self, source_state_id: str, sink_state_id: str) -> list[ElementaryStepEdge]:
        return [
            edge
            for edge in self.edges
            if edge.source_state_id == source_state_id and edge.sink_state_id == sink_state_id
        ]

    def neighbors(self, state_id: str) -> list[StableStateNode]:
        neighbor_ids: list[str] = []
        for edge in self.edges:
            if edge.source_state_id == state_id and edge.sink_state_id not in neighbor_ids:
                neighbor_ids.append(edge.sink_state_id)
        return [
            self.nodes[neighbor_id] for neighbor_id in neighbor_ids if neighbor_id in self.nodes
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReactionNetwork:
        network = cls()
        for node_data in cast(list[dict[str, Any]], data.get("nodes") or []):
            node = StableStateNode.from_dict(dict(node_data))
            network.add_node(node)
        for edge_data in cast(list[dict[str, Any]], data.get("edges") or []):
            network.add_edge(ElementaryStepEdge.from_dict(dict(edge_data)))
        return network


@dataclass
class ExplorationFrontier:
    """Persistent non-recursive frontier queue for network expansion."""

    queue: deque[tuple[str, str]] = field(default_factory=deque)
    max_depth: int = 5
    depths: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def _key(state_id: str, route_id: str) -> str:
        return f"{state_id}::{route_id}"

    def push(self, state_id: str, route_id: str, *, depth: int = 0) -> bool:
        if depth > self.max_depth:
            return False
        item = (state_id, route_id)
        if item not in self.queue:
            self.queue.append(item)
        self.depths[self._key(state_id, route_id)] = depth
        return True

    def pop(self) -> tuple[str, str]:
        return self.queue.popleft()

    def empty(self) -> bool:
        return len(self.queue) == 0

    def depth_for(self, state_id: str, route_id: str) -> int:
        return self.depths.get(self._key(state_id, route_id), 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "queue": [
                {
                    "state_id": state_id,
                    "route_id": route_id,
                    "depth": self.depth_for(state_id, route_id),
                }
                for state_id, route_id in self.queue
            ],
            "max_depth": self.max_depth,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExplorationFrontier:
        frontier = cls(max_depth=int(data.get("max_depth") or 5))
        for item in cast(list[dict[str, Any]], data.get("queue") or []):
            frontier.push(
                str(item.get("state_id") or ""),
                str(item.get("route_id") or ""),
                depth=int(item.get("depth") or 0),
            )
        return frontier


@dataclass
class DecisionPoint:
    """Persisted manual review gate for frontier expansion."""

    id: str
    type: Literal["mechanism_frontier_review", "sr_cycle_review"]
    status: Literal["waiting", "resolved", "superseded"]
    options: list[str]
    payload: dict[str, Any]
    created_at: str
    resolved_at: str | None = None
    resolution: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "options": list(self.options),
            "payload": _json_compatible(self.payload),
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionPoint:
        return cls(
            id=str(data.get("id") or ""),
            type=cast(
                Literal["mechanism_frontier_review"],
                data.get("type") or "mechanism_frontier_review",
            ),
            status=cast(
                Literal["waiting", "resolved", "superseded"],
                data.get("status") or "waiting",
            ),
            options=[str(option) for option in data.get("options") or []],
            payload=dict(data.get("payload") or {}),
            created_at=str(data.get("created_at") or ""),
            resolved_at=_opt_str(data.get("resolved_at")),
            resolution=_opt_str(data.get("resolution")),
        )


@dataclass
class QualityGateResult:
    """Study-level quality gate verdict."""

    gate_id: str
    status: Literal["pass", "warn", "fail"]
    evidence: dict[str, Any]
    thresholds: dict[str, Any]
    missing_evidence: list[str]
    suggested_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": self.status,
            "evidence": _json_compatible(self.evidence),
            "thresholds": _json_compatible(self.thresholds),
            "missing_evidence": list(self.missing_evidence),
            "suggested_action": self.suggested_action,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QualityGateResult:
        return cls(
            gate_id=str(data.get("gate_id") or ""),
            status=cast(Literal["pass", "warn", "fail"], data.get("status") or "fail"),
            evidence=dict(data.get("evidence") or {}),
            thresholds=dict(data.get("thresholds") or {}),
            missing_evidence=[str(item) for item in data.get("missing_evidence") or []],
            suggested_action=_opt_str(data.get("suggested_action")),
        )


@dataclass(frozen=True)
class SelectedBond:
    """User- or strategy-selected bond intervention for a study revision."""

    atoms: tuple[int, int]
    action: Literal["stretch", "form", "keep"]
    start: float | None = None
    target: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "atoms": list(self.atoms),
            "action": self.action,
            "start": self.start,
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelectedBond:
        atoms = tuple(int(atom) for atom in data.get("atoms") or [])
        if len(atoms) != 2:
            raise ValueError("SelectedBond.atoms requires exactly two indices")
        return cls(
            atoms=(int(atoms[0]), int(atoms[1])),
            action=cast(
                Literal["stretch", "form", "keep"],
                data.get("action") or "keep",
            ),
            start=_opt_float(data.get("start")),
            target=_opt_float(data.get("target")),
        )


@dataclass(frozen=True)
class MechanismRevision:
    """Persisted route-edit / continuation decision for one study cycle."""

    revision_id: str
    study_id: str
    cycle: int
    parent_state: str
    selected_bonds: list[SelectedBond] = field(default_factory=list)
    decision: Literal["continue", "accept_network", "reject_path"] = "continue"
    comment: str = ""
    config_hash: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "study_id": self.study_id,
            "cycle": self.cycle,
            "parent_state": self.parent_state,
            "selected_bonds": [bond.to_dict() for bond in self.selected_bonds],
            "decision": self.decision,
            "comment": self.comment,
            "config_hash": self.config_hash,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MechanismRevision:
        return cls(
            revision_id=str(data.get("revision_id") or ""),
            study_id=str(data.get("study_id") or ""),
            cycle=int(data.get("cycle") or 0),
            parent_state=str(data.get("parent_state") or ""),
            selected_bonds=[
                SelectedBond.from_dict(dict(item))
                for item in cast(list[dict[str, Any]], data.get("selected_bonds") or [])
            ],
            decision=cast(
                Literal["continue", "accept_network", "reject_path"],
                data.get("decision") or "continue",
            ),
            comment=str(data.get("comment") or ""),
            config_hash=str(data.get("config_hash") or ""),
            created_at=str(data.get("created_at") or ""),
        )


@dataclass
class StudyCycle:
    """One non-recursive frontier cycle in the study loop."""

    cycle_index: int
    revision_id: str | None = None
    seeded_from_state: str = ""
    route_ids: list[str] = field(default_factory=list)
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_index": self.cycle_index,
            "revision_id": self.revision_id,
            "seeded_from_state": self.seeded_from_state,
            "route_ids": list(self.route_ids),
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StudyCycle:
        return cls(
            cycle_index=int(data.get("cycle_index") or 0),
            revision_id=_opt_str(data.get("revision_id")),
            seeded_from_state=str(data.get("seeded_from_state") or ""),
            route_ids=[str(route_id) for route_id in data.get("route_ids") or []],
            status=str(data.get("status") or "pending"),
        )


@dataclass
class MechanismStudy:
    """A complete mechanism study: workflow + study-layer state."""

    study_id: str
    reactant_id: str | None = None
    product_id: str | None = None
    ts_guess_id: str | None = None
    routes: list[MechanismRoute] = field(default_factory=list)
    atom_identity_map: AtomIdentityMap | None = None
    stable_states: list[StableState] = field(default_factory=list)
    stationary_points: list[StationaryPoint] = field(default_factory=list)
    elementary_steps: list[ElementaryStepEdge] = field(default_factory=list)
    network: ReactionNetwork = field(default_factory=ReactionNetwork)
    frontier: ExplorationFrontier = field(default_factory=ExplorationFrontier)
    cycle_index: int = 0
    cycles: list[StudyCycle] = field(default_factory=list)
    revisions: list[MechanismRevision] = field(default_factory=list)
    decision_points: list[DecisionPoint] = field(default_factory=list)
    quality_gates: list[QualityGateResult] = field(default_factory=list)
    quality: str | None = None
    status: str = "pending"
    study_dir: str | None = None
    phase_fingerprints: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_route(self, route: MechanismRoute) -> None:
        self.routes.append(route)

    def get_state(self, state_id: str) -> StableState | None:
        for state in self.stable_states:
            if state.state_id == state_id:
                return state
        return None

    def effective_fidelity(self) -> str | None:
        high_fidelity = self.metadata.get("high_fidelity")
        if isinstance(high_fidelity, dict) and self.quality == "high":
            profile = _opt_str(high_fidelity.get("profile"))
            if profile is not None:
                return profile

        runner_meta = self.metadata.get("study_runner")
        if isinstance(runner_meta, dict):
            if self.quality == "high":
                profile = _opt_str(runner_meta.get("high_fidelity_profile_name"))
                if profile is not None:
                    return profile
            profile = _opt_str(runner_meta.get("fidelity_profile_name"))
            if profile is not None:
                return profile
            profile = _opt_str(runner_meta.get("fidelity"))
            if profile is not None:
                return profile

        if isinstance(high_fidelity, dict):
            profile = _opt_str(high_fidelity.get("profile"))
            if profile is not None:
                return profile

        for route in self.routes:
            if route.fidelity:
                return route.fidelity
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_id": self.study_id,
            "reactant_id": self.reactant_id,
            "product_id": self.product_id,
            "ts_guess_id": self.ts_guess_id,
            "routes": [route.to_dict() for route in self.routes],
            "atom_identity_map": (
                self.atom_identity_map.to_dict() if self.atom_identity_map is not None else None
            ),
            "stable_states": [state.to_dict() for state in self.stable_states],
            "stationary_points": [point.to_dict() for point in self.stationary_points],
            "elementary_steps": [edge.to_dict() for edge in self.elementary_steps],
            "network": self.network.to_dict(),
            "frontier": self.frontier.to_dict(),
            "cycle_index": self.cycle_index,
            "cycles": [cycle.to_dict() for cycle in self.cycles],
            "revisions": [revision.to_dict() for revision in self.revisions],
            "decision_points": [decision.to_dict() for decision in self.decision_points],
            "quality_gates": [gate.to_dict() for gate in self.quality_gates],
            "quality": self.quality,
            "status": self.status,
            "study_dir": self.study_dir,
            "phase_fingerprints": dict(self.phase_fingerprints),
            "metadata": _json_compatible(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MechanismStudy:
        atom_identity_map_data = data.get("atom_identity_map")
        network_data = data.get("network")
        frontier_data = data.get("frontier")
        study = cls(
            study_id=str(data.get("study_id") or ""),
            reactant_id=_opt_str(data.get("reactant_id")),
            product_id=_opt_str(data.get("product_id")),
            ts_guess_id=_opt_str(data.get("ts_guess_id")),
            routes=[
                MechanismRoute.from_dict(dict(route_data))
                for route_data in cast(list[dict[str, Any]], data.get("routes") or [])
            ],
            atom_identity_map=(
                AtomIdentityMap.from_dict(dict(atom_identity_map_data))
                if isinstance(atom_identity_map_data, dict)
                else None
            ),
            stable_states=[
                StableState.from_dict(dict(state_data))
                for state_data in cast(list[dict[str, Any]], data.get("stable_states") or [])
            ],
            stationary_points=[
                StationaryPoint.from_dict(dict(point_data))
                for point_data in cast(list[dict[str, Any]], data.get("stationary_points") or [])
            ],
            elementary_steps=[
                ElementaryStepEdge.from_dict(dict(edge_data))
                for edge_data in cast(list[dict[str, Any]], data.get("elementary_steps") or [])
            ],
            network=(
                ReactionNetwork.from_dict(dict(network_data))
                if isinstance(network_data, dict)
                else ReactionNetwork()
            ),
            frontier=(
                ExplorationFrontier.from_dict(dict(frontier_data))
                if isinstance(frontier_data, dict)
                else ExplorationFrontier()
            ),
            cycle_index=int(data.get("cycle_index") or 0),
            cycles=[
                StudyCycle.from_dict(dict(cycle_data))
                for cycle_data in cast(list[dict[str, Any]], data.get("cycles") or [])
            ],
            revisions=[
                MechanismRevision.from_dict(dict(revision_data))
                for revision_data in cast(list[dict[str, Any]], data.get("revisions") or [])
            ],
            decision_points=[
                DecisionPoint.from_dict(dict(decision_data))
                for decision_data in cast(list[dict[str, Any]], data.get("decision_points") or [])
            ],
            quality_gates=[
                QualityGateResult.from_dict(dict(gate_data))
                for gate_data in cast(list[dict[str, Any]], data.get("quality_gates") or [])
            ],
            quality=_opt_str(data.get("quality")),
            status=str(data.get("status") or "pending"),
            study_dir=_opt_str(data.get("study_dir")),
            phase_fingerprints={
                str(key): str(value)
                for key, value in dict(data.get("phase_fingerprints") or {}).items()
            },
            metadata=dict(data.get("metadata") or {}),
        )
        if not study.network.nodes and study.stable_states:
            for state in study.stable_states:
                study.network.add_node(state.to_node())
        if not study.network.edges and study.elementary_steps:
            for edge in study.elementary_steps:
                study.network.add_edge(edge)
        return study


@dataclass(frozen=True)
class MechanismInput:
    """Raw mechanism input payload (frontend/API contract)."""

    reactant: dict[str, Any] | str
    product: dict[str, Any] | str | None = None
    ts_guess: dict[str, Any] | str | None = None
    routes: list[MechanismRoute] = field(default_factory=list)
    charge: int | None = None
    multiplicity: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MechanismInput:
        reactant = data.get("reactant")
        if reactant is None:
            raise ValueError("MechanismInput: 'reactant' is required")
        routes_raw = data.get("routes") or []
        routes = [
            route if isinstance(route, MechanismRoute) else MechanismRoute.from_dict(route)
            for route in routes_raw
        ]
        return cls(
            reactant=reactant,
            product=data.get("product"),
            ts_guess=data.get("ts_guess"),
            routes=routes,
            charge=_opt_int(data.get("charge")),
            multiplicity=_opt_int(data.get("multiplicity")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reactant": _json_compatible(self.reactant),
            "product": _json_compatible(self.product),
            "ts_guess": _json_compatible(self.ts_guess),
            "routes": [route.to_dict() for route in self.routes],
            "charge": self.charge,
            "multiplicity": self.multiplicity,
        }


__all__ = [
    "ArtifactRef",
    "AtomIdentityMap",
    "CoordinateSpec",
    "DecisionPoint",
    "ElementaryStepEdge",
    "ExplorationFrontier",
    "Fidelity",
    "MechanismInput",
    "MechanismRevision",
    "MechanismRoute",
    "MechanismStudy",
    "PathCandidate",
    "PathPoint",
    "PathResult",
    "PathStrategy",
    "Provenance",
    "QualityGateResult",
    "ReactionCoordinatePlan",
    "ReactionNetwork",
    "SelectedBond",
    "SeedCandidate",
    "StableState",
    "StableStateNode",
    "StationaryPoint",
    "StationaryPointRequest",
    "StudyCycle",
    "ThermoCorrection",
    "TsIdentity",
    "TsValidation",
]
