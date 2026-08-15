# pyright: reportMissingImports=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnannotatedClassAttribute=false, reportExplicitAny=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportUnusedParameter=false
"""Endpoint classification and default IRC-backed provider.

This module owns the M3 stable-state endpoint semantics:

* IRC endpoint snapshots are classified against known stable states via a
  pure, numpy-only matcher.
* Minimum/frequency validation is expressed as provider hooks, not as direct
  orchestration-side QC calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.backends import require_backend
from acp.backends.base import FrequencyCalculator, GeometryOptimizer
from acp.core.models import Structure, StructureRecord
from acp.mechanism._helpers import mapping_pairs_from_occurrence as _mapping_pairs_from_occurrence
from acp.mechanism._helpers import opt_float as _opt_float
from acp.mechanism.models import ArtifactRef, StableState, StationaryPoint
from acp.mechanism.providers.contracts import EndpointMatchResult, IrcResult

logger = logging.getLogger(__name__)

_COVALENT_RADII_ANGSTROM: dict[str, float] = {
    "H": 0.31,
    "B": 0.84,
    "C": 0.76,
    "N": 0.71,
    "O": 0.66,
    "F": 0.57,
    "Si": 1.11,
    "P": 1.07,
    "S": 1.05,
    "Cl": 1.02,
    "Br": 1.20,
    "I": 1.39,
    "Li": 1.28,
    "Na": 1.66,
    "K": 2.03,
    "Mg": 1.41,
    "Ca": 1.76,
    "Al": 1.21,
    "Zn": 1.22,
    "Cu": 1.32,
    "Fe": 1.24,
    "Co": 1.26,
    "Ni": 1.24,
    "Pd": 1.39,
    "Pt": 1.36,
    "Ag": 1.45,
    "Au": 1.36,
}
_DEFAULT_COVALENT_RADIUS = 0.77


def _rmsd_sort_key(payload: dict[str, Any]) -> float:
    value = _opt_float(payload.get("rmsd_A"))
    return value if value is not None else 1.0e9


def _stable_hash(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def _normalize_coordinates(
    coordinates: NDArray[np.float64] | list[list[float]],
) -> NDArray[np.float64]:
    array = np.asarray(coordinates, dtype=float)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError("Coordinates must have shape (N, 3)")
    return array


def _normalize_symbols(symbols: Sequence[str]) -> list[str]:
    normalized = [str(symbol).strip().capitalize() for symbol in symbols]
    if any(not symbol for symbol in normalized):
        raise ValueError("Atomic symbols must not be empty")
    return normalized


def _is_heavy_atom(symbol: str) -> bool:
    return symbol.upper() != "H"


@dataclass(frozen=True)
class EndpointMatchThresholds:
    """Threshold bundle for endpoint classification.

    Args:
        rmsd_match_A: Heavy-atom RMSD threshold for a confident match.
        rmsd_ambiguous_A: Heavy-atom RMSD threshold for a borderline match.
        energy_match_hartree: Energy-neighborhood threshold for a confident match.
        energy_ambiguous_hartree: Energy-neighborhood threshold for a borderline match.
        covalent_scale: Multiplicative bond-perception factor on covalent radii.
        covalent_tolerance_A: Additive bond-perception slack in Å.
        source_rmsd_match_A: RMSD cutoff used to identify which IRC endpoint
            corresponds to the source state.
        tie_rmsd_A: When two states score within this RMSD window the result is
            treated as ambiguous.
    """

    rmsd_match_A: float = 0.3  # noqa: N815
    rmsd_ambiguous_A: float = 0.75  # noqa: N815
    energy_match_hartree: float = 0.003
    energy_ambiguous_hartree: float = 0.008
    covalent_scale: float = 1.25
    covalent_tolerance_A: float = 0.12  # noqa: N815
    source_rmsd_match_A: float = 0.5  # noqa: N815
    tie_rmsd_A: float = 0.05  # noqa: N815


@dataclass(frozen=True)
class EndpointCandidate:
    """One candidate stable-state geometry extracted from an IRC endpoint."""

    coordinates: NDArray[np.float64] | list[list[float]]
    symbols: list[str] | tuple[str, ...]
    charge: int
    multiplicity: int
    frequencies_cm1: tuple[float, ...] | list[float] | None = None
    energy_hartree: float | None = None
    label: str = "endpoint"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "coordinates", _normalize_coordinates(self.coordinates))
        object.__setattr__(self, "symbols", _normalize_symbols(self.symbols))
        frequencies = self.frequencies_cm1
        if frequencies is not None:
            object.__setattr__(
                self,
                "frequencies_cm1",
                tuple(float(value) for value in frequencies),
            )
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation."""
        return {
            "coordinates": np.asarray(self.coordinates, dtype=float).tolist(),
            "symbols": list(self.symbols),
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "frequencies_cm1": (
                list(self.frequencies_cm1) if self.frequencies_cm1 is not None else None
            ),
            "energy_hartree": self.energy_hartree,
            "label": self.label,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EndpointCandidate:
        """Build an endpoint candidate from serialized data."""
        return cls(
            coordinates=data.get("coordinates") or [],
            symbols=data.get("symbols") or [],
            charge=int(data.get("charge") or 0),
            multiplicity=int(data.get("multiplicity") or 1),
            frequencies_cm1=data.get("frequencies_cm1"),
            energy_hartree=_opt_float(data.get("energy_hartree")),
            label=str(data.get("label") or "endpoint"),
            metadata=dict(data.get("metadata") or {}),
        )


def needs_minimum_validation(candidate: EndpointCandidate) -> bool:
    """Return ``True`` when an IRC endpoint still lacks minimum validation.

    IRC endpoints are trajectory termini, not chemically validated stable
    states. The caller is responsible for scheduling the actual unconstrained
    optimization/frequency work; this helper only exposes the semantic gate.

    Args:
        candidate: Endpoint candidate to inspect.

    Returns:
        ``True`` when post-IRC minimum/frequency evidence is still missing.
    """
    if (
        candidate.metadata.get("validated_minimum") is True
        and candidate.frequencies_cm1 is not None
    ):
        return any(float(freq) < 0.0 for freq in candidate.frequencies_cm1)
    return True


def minimum_validation_missing_evidence(candidate: EndpointCandidate) -> list[str]:
    """Return missing evidence labels for stable-state validation.

    Args:
        candidate: Endpoint candidate to inspect.

    Returns:
        Missing evidence labels. An empty list means the endpoint carries a
        validated minimum/frequency annotation.
    """
    missing: list[str] = []
    if candidate.metadata.get("validated_minimum") is not True:
        missing.append("minimum_optimization")
    if candidate.frequencies_cm1 is None:
        missing.append("frequencies")
    if candidate.energy_hartree is None:
        missing.append("energy")
    return missing


def _coerce_atom_mapping(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    mapping: dict[str, int] = {}
    for key, raw_index in value.items():
        if not isinstance(key, str):
            return None
        if not isinstance(raw_index, (int, float, str)):
            return None
        try:
            mapping[key] = int(raw_index)
        except (TypeError, ValueError):
            return None
    return mapping


def _mapping_pairs_from_metadata(
    candidate: EndpointCandidate,
    reference_mapping: dict[str, int] | None,
) -> list[tuple[int, int]] | None:
    candidate_mapping = _coerce_atom_mapping(candidate.metadata.get("atom_mapping"))
    if candidate_mapping is None or reference_mapping is None:
        return None
    shared = sorted(set(candidate_mapping) & set(reference_mapping))
    if not shared:
        return None
    pairs = [(candidate_mapping[label], reference_mapping[label]) for label in shared]
    if len(pairs) != len(candidate.symbols):
        return None
    return pairs


def _mapping_evidence(
    candidate: EndpointCandidate,
    reference_symbols: list[str],
    reference_metadata: dict[str, Any],
) -> tuple[list[tuple[int, int]] | None, dict[str, Any]]:
    reference_mapping = _coerce_atom_mapping(reference_metadata.get("atom_mapping"))
    pairs = _mapping_pairs_from_metadata(candidate, reference_mapping)
    if pairs is not None:
        return pairs, {
            "atom_mapping_compatible": True,
            "atom_mapping_source": "metadata",
            "mapped_atom_count": len(pairs),
        }
    pairs = _mapping_pairs_from_occurrence(candidate.symbols, reference_symbols)
    return pairs, {
        "atom_mapping_compatible": pairs is not None,
        "atom_mapping_source": "symbol_occurrence",
        "mapped_atom_count": len(pairs or []),
    }


def perceive_connectivity(
    symbols: list[str] | tuple[str, ...],
    coordinates: NDArray[np.float64] | list[list[float]],
    thresholds: EndpointMatchThresholds,
) -> set[tuple[int, int]]:
    """Infer an undirected bond graph from covalent radii.

    Args:
        symbols: Atomic symbols.
        coordinates: Geometry in Å.
        thresholds: Bond-perception thresholds.

    Returns:
        Undirected edges encoded as sorted ``(i, j)`` index pairs.
    """
    coords = _normalize_coordinates(coordinates)
    normalized_symbols = _normalize_symbols(symbols)
    if len(normalized_symbols) != len(coords):
        raise ValueError("Atom count mismatch between symbols and coordinates")
    edges: set[tuple[int, int]] = set()
    for i in range(len(normalized_symbols)):
        radius_i = _COVALENT_RADII_ANGSTROM.get(normalized_symbols[i], _DEFAULT_COVALENT_RADIUS)
        for j in range(i + 1, len(normalized_symbols)):
            radius_j = _COVALENT_RADII_ANGSTROM.get(
                normalized_symbols[j],
                _DEFAULT_COVALENT_RADIUS,
            )
            cutoff = (
                thresholds.covalent_scale * (radius_i + radius_j) + thresholds.covalent_tolerance_A
            )
            distance = float(np.linalg.norm(np.asarray(coords[i] - coords[j], dtype=float)))
            if distance <= cutoff:
                edges.add((i, j))
    return edges


def connectivity_fingerprint(
    symbols: list[str] | tuple[str, ...],
    coordinates: NDArray[np.float64] | list[list[float]],
    thresholds: EndpointMatchThresholds,
) -> str:
    """Return a stable connectivity fingerprint string."""
    normalized_symbols = _normalize_symbols(symbols)
    edges = perceive_connectivity(normalized_symbols, coordinates, thresholds)
    labels = [
        f"{normalized_symbols[i]}{i + 1}-{normalized_symbols[j]}{j + 1}" for i, j in sorted(edges)
    ]
    return _stable_hash({"symbols": normalized_symbols, "edges": labels})


def _map_edges(
    edges: set[tuple[int, int]],
    mapping_pairs: list[tuple[int, int]],
) -> set[tuple[int, int]] | None:
    mapping = {
        candidate_index: reference_index for candidate_index, reference_index in mapping_pairs
    }
    mapped: set[tuple[int, int]] = set()
    for left, right in edges:
        if left not in mapping or right not in mapping:
            return None
        mapped_left = mapping[left]
        mapped_right = mapping[right]
        if mapped_left <= mapped_right:
            mapped.add((mapped_left, mapped_right))
        else:
            mapped.add((mapped_right, mapped_left))
    return mapped


def mapped_heavy_atom_rmsd(
    candidate_coordinates: NDArray[np.float64] | list[list[float]],
    candidate_symbols: list[str] | tuple[str, ...],
    reference_coordinates: NDArray[np.float64] | list[list[float]],
    reference_symbols: list[str] | tuple[str, ...],
    mapping_pairs: list[tuple[int, int]],
) -> float:
    """Return heavy-atom RMSD after Kabsch alignment on mapped atoms.

    Degenerate cases with fewer than three mapped heavy atoms fall back to a
    centroid-aligned RMSD (translation-only). This avoids singular Kabsch fits
    for one- and two-atom heavy frameworks while keeping the comparison purely
    geometry-based.
    """
    candidate = _normalize_coordinates(candidate_coordinates)
    reference = _normalize_coordinates(reference_coordinates)
    normalized_candidate_symbols = _normalize_symbols(candidate_symbols)
    normalized_reference_symbols = _normalize_symbols(reference_symbols)
    selected_candidate: list[int] = []
    selected_reference: list[int] = []
    for candidate_index, reference_index in mapping_pairs:
        symbol = normalized_candidate_symbols[candidate_index]
        if not _is_heavy_atom(symbol):
            continue
        if normalized_reference_symbols[reference_index] != symbol:
            continue
        selected_candidate.append(candidate_index)
        selected_reference.append(reference_index)
    if not selected_candidate:
        selected_candidate = [pair[0] for pair in mapping_pairs]
        selected_reference = [pair[1] for pair in mapping_pairs]
    mobile = candidate[selected_candidate]
    target = reference[selected_reference]
    if len(mobile) < 3:
        mobile_centered = mobile - np.mean(mobile, axis=0)
        target_centered = target - np.mean(target, axis=0)
        diff = mobile_centered - target_centered
        return float(np.sqrt(np.mean(np.sum(diff**2, axis=1))))
    mobile_centered = mobile - np.mean(mobile, axis=0)
    target_centered = target - np.mean(target, axis=0)
    covariance = mobile_centered.T @ target_centered
    u_matrix, _, vt_matrix = np.linalg.svd(covariance)
    rotation = u_matrix @ vt_matrix
    if np.linalg.det(rotation) < 0.0:
        u_matrix[:, -1] *= -1.0
        rotation = u_matrix @ vt_matrix
    aligned_mobile = mobile_centered @ rotation
    diff = aligned_mobile - target_centered
    return float(np.sqrt(np.mean(np.sum(diff**2, axis=1))))


def reaction_coordinate_signature(
    source_symbols: list[str] | tuple[str, ...],
    source_coordinates: NDArray[np.float64] | list[list[float]],
    candidate: EndpointCandidate,
    thresholds: EndpointMatchThresholds,
) -> dict[str, Any]:
    """Return a bond-change signature from source state to endpoint."""
    source_symbol_list = _normalize_symbols(source_symbols)
    mapping = _mapping_pairs_from_occurrence(candidate.symbols, source_symbol_list)
    if mapping is None:
        return {
            "formed_bonds": [],
            "broken_bonds": [],
            "atom_mapping_compatible": False,
        }
    source_edges = perceive_connectivity(source_symbol_list, source_coordinates, thresholds)
    candidate_edges = perceive_connectivity(candidate.symbols, candidate.coordinates, thresholds)
    mapped_candidate_edges = _map_edges(candidate_edges, mapping)
    if mapped_candidate_edges is None:
        return {
            "formed_bonds": [],
            "broken_bonds": [],
            "atom_mapping_compatible": False,
        }
    formed = sorted(mapped_candidate_edges - source_edges)
    broken = sorted(source_edges - mapped_candidate_edges)
    return {
        "formed_bonds": [
            f"{source_symbol_list[i]}{i + 1}-{source_symbol_list[j]}{j + 1}" for i, j in formed
        ],
        "broken_bonds": [
            f"{source_symbol_list[i]}{i + 1}-{source_symbol_list[j]}{j + 1}" for i, j in broken
        ],
        "atom_mapping_compatible": True,
    }


def _state_conformers(state: StableState) -> tuple[list[StructureRecord], list[str]]:
    records: list[StructureRecord] = []
    missing: list[str] = []
    if state.ensemble is None:
        missing.append("ensemble")
    else:
        for record in state.ensemble.records:
            if record.coordinates is None:
                continue
            records.append(record)
    if not records:
        coordinates = state.metadata.get("coordinates")
        symbols = state.metadata.get("symbols")
        if coordinates is not None and symbols is not None:
            structure = Structure(
                id=f"{state.state_id}_canonical",
                charge=state.charge,
                multiplicity=state.multiplicity,
                symbols=[str(symbol) for symbol in symbols],
                coordinates=coordinates,
                metadata=dict(state.metadata),
            )
            records.append(
                StructureRecord(
                    structure=structure,
                    energy_hartree=_opt_float(state.metadata.get("energy_hartree")),
                )
            )
        else:
            missing.append("coordinates")
    return records, missing


def _state_reference_energy(state: StableState) -> float | None:
    if state.ensemble is not None:
        minimum = state.ensemble.global_minimum()
        if minimum is not None and minimum.energy_hartree is not None:
            return minimum.energy_hartree
    return _opt_float(state.metadata.get("energy_hartree"))


@dataclass(frozen=True)
class EndpointMatcher:
    """Pure endpoint/state classifier."""

    thresholds: EndpointMatchThresholds = field(default_factory=EndpointMatchThresholds)

    def compare_candidate_to_state(
        self,
        candidate: EndpointCandidate,
        state: StableState,
    ) -> dict[str, Any]:
        """Compare one endpoint candidate against one known state.

        Args:
            candidate: Endpoint candidate.
            state: Stable state to compare against.

        Returns:
            Rich comparison evidence used by the classifier.
        """
        comparisons, missing = _state_conformers(state)
        comparison: dict[str, Any] = {
            "state_id": state.state_id,
            "role": state.role,
            "charge_match": candidate.charge == state.charge,
            "multiplicity_match": candidate.multiplicity == state.multiplicity,
            "connectivity_match": False,
            "rmsd_A": None,
            "energy_delta_hartree": None,
            "atom_mapping_compatible": False,
            "atom_mapping_source": None,
            "mapped_atom_count": 0,
            "missing": list(missing),
        }
        if not comparison["charge_match"] or not comparison["multiplicity_match"]:
            return comparison

        candidate_edges = perceive_connectivity(
            candidate.symbols,
            candidate.coordinates,
            self.thresholds,
        )
        candidate_fingerprint = connectivity_fingerprint(
            candidate.symbols,
            candidate.coordinates,
            self.thresholds,
        )
        comparison["candidate_connectivity_fingerprint"] = candidate_fingerprint
        best_rmsd: float | None = None
        best_connectivity = False
        best_mapping: dict[str, Any] | None = None
        best_reference_fingerprint: str | None = None
        best_reference_label: str | None = None
        for record_index, record in enumerate(comparisons):
            reference_symbols = list(record.symbols)
            reference_coordinates = np.asarray(record.coordinates, dtype=float)
            mapping_pairs, mapping_info = _mapping_evidence(
                candidate,
                reference_symbols,
                record.metadata,
            )
            if mapping_pairs is None:
                continue
            mapped_edges = _map_edges(candidate_edges, mapping_pairs)
            reference_edges = perceive_connectivity(
                reference_symbols,
                reference_coordinates,
                self.thresholds,
            )
            reference_fingerprint = connectivity_fingerprint(
                reference_symbols,
                reference_coordinates,
                self.thresholds,
            )
            connectivity_match = (
                mapped_edges == reference_edges if mapped_edges is not None else False
            )
            rmsd_value = mapped_heavy_atom_rmsd(
                candidate.coordinates,
                candidate.symbols,
                reference_coordinates,
                reference_symbols,
                mapping_pairs,
            )
            if best_rmsd is None or rmsd_value < best_rmsd:
                best_rmsd = rmsd_value
                best_connectivity = connectivity_match
                best_mapping = mapping_info
                best_reference_fingerprint = reference_fingerprint
                best_reference_label = f"conformer_{record_index + 1}"
        if best_mapping is None:
            return comparison
        comparison.update(best_mapping)
        comparison["connectivity_match"] = best_connectivity
        comparison["rmsd_A"] = best_rmsd
        comparison["reference_connectivity_fingerprint"] = best_reference_fingerprint
        comparison["reference_label"] = best_reference_label
        state_energy = _state_reference_energy(state)
        if state_energy is not None and candidate.energy_hartree is not None:
            comparison["energy_delta_hartree"] = abs(candidate.energy_hartree - state_energy)
        return comparison

    def classify(
        self,
        candidate: EndpointCandidate,
        known_states: list[StableState],
    ) -> EndpointMatchResult:
        """Classify an IRC endpoint against known stable states.

        Args:
            candidate: Endpoint candidate.
            known_states: Current stable-state registry.

        Returns:
            Four-state endpoint verdict with detailed evidence.
        """
        if len(candidate.symbols) != len(candidate.coordinates):
            return EndpointMatchResult(
                verdict="FAILED",
                evidence={
                    "reason": "atom_count_mismatch",
                    "missing": ["coordinates"],
                    "candidate": candidate.to_dict(),
                },
            )
        comparisons = [self.compare_candidate_to_state(candidate, state) for state in known_states]
        strong_matches: list[dict[str, Any]] = []
        ambiguous_matches: list[dict[str, Any]] = []
        for comparison in comparisons:
            rmsd_value = _opt_float(comparison.get("rmsd_A"))
            energy_delta = _opt_float(comparison.get("energy_delta_hartree"))
            connectivity_match = bool(comparison.get("connectivity_match"))
            if not (
                bool(comparison.get("charge_match"))
                and bool(comparison.get("multiplicity_match"))
                and bool(comparison.get("atom_mapping_compatible"))
                and connectivity_match
                and rmsd_value is not None
            ):
                continue
            if energy_delta is None:
                comparison["energy_neighborhood"] = "missing"
            elif energy_delta <= self.thresholds.energy_match_hartree:
                comparison["energy_neighborhood"] = "match"
            elif energy_delta <= self.thresholds.energy_ambiguous_hartree:
                comparison["energy_neighborhood"] = "borderline"
            else:
                comparison["energy_neighborhood"] = "out_of_window"
            if rmsd_value <= self.thresholds.rmsd_match_A:
                strong_matches.append(comparison)
                continue
            if rmsd_value <= self.thresholds.rmsd_ambiguous_A:
                ambiguous_matches.append(comparison)

        candidate_state = self._candidate_state_payload(candidate, known_states)
        missing = minimum_validation_missing_evidence(candidate)
        evidence: dict[str, Any] = {
            "candidate": candidate.to_dict(),
            "comparisons": comparisons,
            "missing": list(missing),
            "missing_evidence": list(missing),
            "candidate_state": candidate_state,
            "reaction_coordinate_signature": dict(
                candidate.metadata.get("reaction_coordinate_signature") or {}
            ),
        }
        if strong_matches:
            strong_matches.sort(key=_rmsd_sort_key)
            if len(strong_matches) > 1:
                best = _rmsd_sort_key(strong_matches[0])
                second = _rmsd_sort_key(strong_matches[1])
                if abs(best - second) <= self.thresholds.tie_rmsd_A:
                    evidence["reason"] = "multiple_strong_matches"
                    evidence["selected_state_ids"] = [
                        item["state_id"] for item in strong_matches[:2] if item.get("state_id")
                    ]
                    return EndpointMatchResult(verdict="AMBIGUOUS", evidence=evidence)
            best_match = strong_matches[0]
            evidence["reason"] = "matched_existing_state"
            evidence["selected_state_id"] = best_match["state_id"]
            evidence["rmsd_A"] = best_match.get("rmsd_A")
            evidence["energy_delta_hartree"] = best_match.get("energy_delta_hartree")
            return EndpointMatchResult(
                verdict="MATCH_EXISTING",
                state_id=str(best_match["state_id"]),
                evidence=evidence,
            )
        if ambiguous_matches:
            ambiguous_matches.sort(key=_rmsd_sort_key)
            evidence["reason"] = "borderline_existing_state_match"
            evidence["selected_state_ids"] = [
                item["state_id"] for item in ambiguous_matches if item.get("state_id")
            ]
            evidence["rmsd_A"] = ambiguous_matches[0].get("rmsd_A")
            return EndpointMatchResult(verdict="AMBIGUOUS", evidence=evidence)
        evidence["reason"] = "novel_connectivity_or_charge_state"
        return EndpointMatchResult(
            verdict="NEW_STATE",
            state_id=str(candidate_state["state_id"]),
            evidence=evidence,
        )

    def _candidate_state_payload(
        self,
        candidate: EndpointCandidate,
        known_states: list[StableState],
    ) -> dict[str, Any]:
        fingerprint = connectivity_fingerprint(
            candidate.symbols,
            candidate.coordinates,
            self.thresholds,
        )
        state_id = self._next_state_id(candidate, fingerprint, known_states)
        geometry_hash = _stable_hash(
            {
                "symbols": list(candidate.symbols),
                "coordinates": np.asarray(candidate.coordinates, dtype=float).tolist(),
                "charge": candidate.charge,
                "multiplicity": candidate.multiplicity,
            }
        )
        metadata = dict(candidate.metadata)
        metadata.setdefault("symbols", list(candidate.symbols))
        metadata.setdefault("coordinates", np.asarray(candidate.coordinates, dtype=float).tolist())
        metadata.setdefault("energy_hartree", candidate.energy_hartree)
        metadata.setdefault(
            "frequencies_cm1",
            list(candidate.frequencies_cm1) if candidate.frequencies_cm1 is not None else None,
        )
        metadata.setdefault("validated_minimum", candidate.metadata.get("validated_minimum", False))
        metadata.setdefault("missing", minimum_validation_missing_evidence(candidate))
        return {
            "state_id": state_id,
            "role": str(candidate.metadata.get("state_role") or "intermediate"),
            "canonical_geometry": {
                "path": str(candidate.metadata.get("artifact_path") or f"memory://{state_id}"),
                "sha256": geometry_hash,
                "kind": "stable_state_geometry",
            },
            "charge": candidate.charge,
            "multiplicity": candidate.multiplicity,
            "identity_fingerprint": _stable_hash(
                {
                    "symbols": list(candidate.symbols),
                    "connectivity_fingerprint": fingerprint,
                    "charge": candidate.charge,
                    "multiplicity": candidate.multiplicity,
                }
            ),
            "ensemble": None,
            "metadata": metadata,
        }

    def _next_state_id(
        self,
        candidate: EndpointCandidate,
        fingerprint: str,
        known_states: list[StableState],
    ) -> str:
        existing = {state.state_id for state in known_states}
        digest = fingerprint.split(":", maxsplit=1)[-1][:10]
        base = str(candidate.metadata.get("state_id_hint") or f"state_{digest}")
        if base not in existing:
            return base
        suffix = 2
        while f"{base}_{suffix}" in existing:
            suffix += 1
        return f"{base}_{suffix}"


@dataclass
class _EndpointSelection:
    candidate: EndpointCandidate | None
    evidence: dict[str, Any]
    failed: bool = False


class DefaultEndpointProvider:
    """Default ``EndpointProvider`` backed by an IRC-capable QC backend."""

    def __init__(
        self,
        *,
        backend: object | None = None,
        backend_config: dict[str, Any] | None = None,
        matcher: EndpointMatcher | None = None,
        thresholds: EndpointMatchThresholds | None = None,
        validate_minimum: bool = True,
        work_root: Path | str | None = None,
    ) -> None:
        self.thresholds = thresholds or EndpointMatchThresholds()
        self.matcher = matcher or EndpointMatcher(self.thresholds)
        self.backend = self._resolve_backend(backend, backend_config)
        self.validate_minimum = validate_minimum
        self.work_root = Path(work_root) if work_root is not None else Path.cwd()

    def run_irc(self, ts: StationaryPoint, fidelity: Any) -> IrcResult:
        """Run IRC with the injected backend and persist endpoint geometry evidence.

        Args:
            ts: Validated TS candidate.
            fidelity: Backend/profile selector.

        Returns:
            Contract-layer IRC result.
        """
        if self.backend is None:
            raise ValueError("DefaultEndpointProvider.run_irc requires an injected IRC backend")
        if not callable(getattr(self.backend, "irc", None)):
            raise TypeError("Injected backend does not provide the 'irc' capability")
        coordinates, symbols = self._extract_stationary_point_geometry(ts)
        output_dir = self.work_root / "irc" / ts.point_id
        output_dir.mkdir(parents=True, exist_ok=True)
        backend_result = self.backend.irc(
            coordinates,
            symbols,
            charge=ts.charge,
            multiplicity=ts.multiplicity,
            output_dir=output_dir,
            output_name=f"irc_{ts.point_id}",
            fidelity=fidelity,
        )
        irc_id = f"irc_{ts.point_id}"
        final_geometries = {
            direction: np.asarray(geometry, dtype=float)
            for direction, geometry in dict(
                getattr(backend_result, "final_geometries", {}) or {}
            ).items()
        }
        forward_endpoint = self._endpoint_artifact(
            irc_id,
            "forward",
            final_geometries.get("forward"),
            getattr(backend_result, "endpoints", None),
        )
        reverse_endpoint = self._endpoint_artifact(
            irc_id,
            "reverse",
            final_geometries.get("reverse"),
            getattr(backend_result, "endpoints", None),
        )
        evidence = {
            "source_state_id": ts.state_id,
            "route_id": ts.route_id,
            "symbols": list(symbols),
            "charge": ts.charge,
            "multiplicity": ts.multiplicity,
            "fidelity": getattr(fidelity, "name", str(fidelity)),
            "final_geometries": {
                direction: geometry.tolist() for direction, geometry in final_geometries.items()
            },
        }
        return IrcResult(
            irc_id=irc_id,
            ts_id=ts.point_id,
            success=bool(getattr(backend_result, "success", False)),
            complete=bool(getattr(backend_result, "success", False)),
            forward_endpoint=forward_endpoint,
            reverse_endpoint=reverse_endpoint,
            evidence=evidence,
        )

    def classify_endpoints(
        self,
        irc_result: IrcResult,
        known_states: list[StableState],
    ) -> EndpointMatchResult:
        """Classify the sink IRC endpoint against known stable states.

        Args:
            irc_result: Contract-layer IRC result.
            known_states: Current stable-state registry.

        Returns:
            Endpoint match verdict.
        """
        candidates = self._endpoint_candidates_from_irc(irc_result)
        if not candidates:
            return EndpointMatchResult(
                verdict="FAILED",
                evidence={
                    "reason": "missing_endpoint_geometries",
                    "irc_id": irc_result.irc_id,
                    "missing": ["final_geometries"],
                },
            )
        source_state = self._resolve_source_state(irc_result, known_states)
        selection = self._select_sink_candidate(irc_result, candidates, source_state)
        if selection.failed or selection.candidate is None:
            verdict = "FAILED" if selection.failed else "AMBIGUOUS"
            selection.evidence.setdefault("irc_id", irc_result.irc_id)
            return EndpointMatchResult(verdict=verdict, evidence=selection.evidence)
        candidate = selection.candidate
        validation = self._validate_candidate_minimum(candidate, irc_result.irc_id)
        selection.evidence["minimum_validation"] = validation["summary"]
        minimum_stationary_point = validation.get("stationary_point")
        if isinstance(minimum_stationary_point, dict):
            selection.evidence["minimum_stationary_point"] = minimum_stationary_point
        if validation["verdict"] == "FAILED":
            evidence = {
                **selection.evidence,
                "candidate": candidate.to_dict(),
                "missing": list(validation["missing"]),
                "missing_evidence": list(validation["missing"]),
                "reason": "minimum_validation_failed",
            }
            return EndpointMatchResult(verdict="FAILED", evidence=evidence)
        validated_candidate = validation["candidate"]
        match = self.matcher.classify(validated_candidate, known_states)
        evidence = dict(match.evidence)
        evidence.update(selection.evidence)
        evidence["minimum_validation"] = validation["summary"]
        evidence["irc_id"] = irc_result.irc_id
        if source_state is not None:
            source_records, _ = _state_conformers(source_state)
            if source_records:
                evidence["reaction_coordinate_signature"] = reaction_coordinate_signature(
                    source_records[0].symbols,
                    np.asarray(source_records[0].coordinates, dtype=float),
                    validated_candidate,
                    self.thresholds,
                )
        match.evidence = evidence
        return match

    def _resolve_backend(
        self,
        backend: object | None,
        backend_config: dict[str, Any] | None,
    ) -> object | None:
        if backend is not None:
            return backend
        if backend_config is None:
            return None
        backend_cls = require_backend("irc")
        return backend_cls(backend_config)

    def _extract_stationary_point_geometry(
        self,
        ts: StationaryPoint,
    ) -> tuple[NDArray[np.float64], list[str]]:
        symbols = ts.metadata.get("symbols")
        coordinates = ts.metadata.get("coordinates")
        if symbols is not None and coordinates is not None:
            return _normalize_coordinates(coordinates), _normalize_symbols(symbols)
        geometry_path = Path(ts.geometry.path)
        if geometry_path.exists() and geometry_path.suffix.lower() == ".json":
            payload = json.loads(geometry_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"Unexpected geometry payload in {geometry_path}")
            coords = payload.get("geometry") or payload.get("coordinates")
            symbols = payload.get("symbols") or ts.metadata.get("symbols")
            if coords is None or symbols is None:
                raise ValueError(
                    f"Stationary point geometry JSON {geometry_path} lacks coordinates/symbols"
                )
            return _normalize_coordinates(coords), _normalize_symbols(symbols)
        if geometry_path.exists() and geometry_path.suffix.lower() == ".xyz":
            return self._read_xyz_geometry(geometry_path)
        raise ValueError(
            "Stationary point must provide coordinates/symbols in metadata or a "
            + "JSON/XYZ geometry artifact"
        )

    def _read_xyz_geometry(self, path: Path) -> tuple[NDArray[np.float64], list[str]]:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) < 3:
            raise ValueError(f"XYZ file is too short: {path}")
        try:
            n_atoms = int(lines[0].strip())
        except ValueError as exc:
            raise ValueError(f"Invalid XYZ atom count in {path}") from exc
        data_lines = lines[2 : 2 + n_atoms]
        symbols: list[str] = []
        coordinates: list[list[float]] = []
        for line in data_lines:
            parts = line.split()
            if len(parts) < 4:
                raise ValueError(f"Invalid XYZ line in {path}: {line!r}")
            symbols.append(str(parts[0]))
            coordinates.append([float(parts[1]), float(parts[2]), float(parts[3])])
        return _normalize_coordinates(coordinates), _normalize_symbols(symbols)

    def _endpoint_artifact(
        self,
        irc_id: str,
        direction: str,
        geometry: NDArray[np.float64] | None,
        endpoint_files: object,
    ) -> ArtifactRef | None:
        endpoint_path = None
        if isinstance(endpoint_files, dict):
            endpoint_path = endpoint_files.get(direction)
        if endpoint_path is not None:
            path = Path(endpoint_path)
            return ArtifactRef(
                path=str(path),
                sha256=_stable_hash({"path": str(path)}),
                kind="irc_endpoint",
            )
        if geometry is None:
            return None
        return ArtifactRef(
            path=str(self.work_root / "irc" / irc_id / f"{direction}.xyz"),
            sha256=_stable_hash({"geometry": geometry.tolist(), "direction": direction}),
            kind="irc_endpoint",
        )

    def _endpoint_candidates_from_irc(self, irc_result: IrcResult) -> dict[str, EndpointCandidate]:
        evidence = dict(irc_result.evidence or {})
        symbols = _normalize_symbols(evidence.get("symbols") or [])
        if not symbols:
            return {}
        charge = int(evidence.get("charge") or 0)
        multiplicity = int(evidence.get("multiplicity") or 1)
        candidates: dict[str, EndpointCandidate] = {}
        geometries = dict(evidence.get("final_geometries") or {})
        for direction, raw_geometry in geometries.items():
            coords = _normalize_coordinates(raw_geometry)
            metadata = {
                "direction": direction,
                "artifact_path": str(
                    getattr(getattr(irc_result, f"{direction}_endpoint", None), "path", "")
                ),
                "source_state_id": evidence.get("source_state_id"),
                "route_id": evidence.get("route_id"),
            }
            candidates[str(direction)] = EndpointCandidate(
                coordinates=coords,
                symbols=symbols,
                charge=charge,
                multiplicity=multiplicity,
                label=f"{irc_result.irc_id}:{direction}",
                metadata=metadata,
            )
        return candidates

    def _resolve_source_state(
        self,
        irc_result: IrcResult,
        known_states: list[StableState],
    ) -> StableState | None:
        source_state_id = str(irc_result.evidence.get("source_state_id") or "")
        if source_state_id:
            for state in known_states:
                if state.state_id == source_state_id:
                    return state
        reactants = [state for state in known_states if state.role == "reactant"]
        return reactants[0] if len(reactants) == 1 else None

    def _select_sink_candidate(
        self,
        irc_result: IrcResult,
        candidates: dict[str, EndpointCandidate],
        source_state: StableState | None,
    ) -> _EndpointSelection:
        evidence: dict[str, Any] = {
            "source_state_id": source_state.state_id if source_state else None
        }
        if source_state is None and len(candidates) > 1:
            evidence["reason"] = "source_state_missing"
            evidence["missing"] = ["source_state"]
            evidence["missing_evidence"] = ["source_state"]
            return _EndpointSelection(candidate=None, evidence=evidence, failed=False)
        if len(candidates) == 1 or source_state is None:
            candidate = next(iter(candidates.values()))
            evidence["sink_direction"] = str(candidate.metadata.get("direction") or "")
            return _EndpointSelection(candidate=candidate, evidence=evidence)
        source_matches: list[tuple[str, dict[str, Any]]] = []
        for direction, candidate in candidates.items():
            comparison = self.matcher.compare_candidate_to_state(candidate, source_state)
            evidence.setdefault("source_endpoint_comparisons", {})[direction] = comparison
            rmsd_value = _opt_float(comparison.get("rmsd_A"))
            if not (
                bool(comparison.get("charge_match"))
                and bool(comparison.get("multiplicity_match"))
                and bool(comparison.get("atom_mapping_compatible"))
                and bool(comparison.get("connectivity_match"))
                and rmsd_value is not None
                and rmsd_value <= self.thresholds.source_rmsd_match_A
            ):
                continue
            source_matches.append((direction, comparison))
        if len(source_matches) != 1:
            evidence["reason"] = "source_endpoint_not_resolved"
            evidence["missing"] = ["source_endpoint_resolution"]
            evidence["missing_evidence"] = ["source_endpoint_resolution"]
            return _EndpointSelection(candidate=None, evidence=evidence, failed=False)
        source_direction = source_matches[0][0]
        sink_candidates = {
            direction: candidate
            for direction, candidate in candidates.items()
            if direction != source_direction
        }
        if len(sink_candidates) != 1:
            evidence["reason"] = "sink_endpoint_not_resolved"
            evidence["missing"] = ["sink_endpoint"]
            evidence["missing_evidence"] = ["sink_endpoint"]
            return _EndpointSelection(candidate=None, evidence=evidence, failed=True)
        sink_direction, sink_candidate = next(iter(sink_candidates.items()))
        evidence["source_direction"] = source_direction
        evidence["sink_direction"] = sink_direction
        return _EndpointSelection(candidate=sink_candidate, evidence=evidence)

    def _validate_candidate_minimum(
        self,
        candidate: EndpointCandidate,
        irc_id: str,
    ) -> dict[str, Any]:
        if not self.validate_minimum:
            skipped = self._with_candidate_metadata(
                candidate,
                {
                    "validated_minimum": False,
                    "validation_skipped": True,
                },
            )
            return {
                "candidate": skipped,
                "summary": {
                    "status": "skipped",
                    "reason": "disabled",
                    "missing": ["minimum_optimization", "frequencies"],
                },
                "stationary_point": self._minimum_stationary_point_payload(skipped, irc_id),
                "missing": ["minimum_optimization", "frequencies"],
                "verdict": "OK",
            }
        if self.backend is None:
            logger.warning(
                "DefaultEndpointProvider: no backend injected for minimum validation "
                + "of %s; classifying raw IRC endpoint",
                irc_id,
            )
            skipped = self._with_candidate_metadata(
                candidate,
                {
                    "validated_minimum": False,
                    "validation_skipped": True,
                    "validation_warning": "no_backend",
                },
            )
            return {
                "candidate": skipped,
                "summary": {
                    "status": "skipped",
                    "reason": "no_backend",
                    "missing": ["minimum_optimization", "frequencies"],
                },
                "stationary_point": self._minimum_stationary_point_payload(skipped, irc_id),
                "missing": ["minimum_optimization", "frequencies"],
                "verdict": "OK",
            }
        if not (
            isinstance(self.backend, GeometryOptimizer)
            and isinstance(self.backend, FrequencyCalculator)
        ):
            logger.warning(
                "DefaultEndpointProvider: backend lacks minimum/frequency capabilities "
                + "for %s; classifying raw IRC endpoint",
                irc_id,
            )
            skipped = self._with_candidate_metadata(
                candidate,
                {
                    "validated_minimum": False,
                    "validation_skipped": True,
                    "validation_warning": "missing_capabilities",
                },
            )
            return {
                "candidate": skipped,
                "summary": {
                    "status": "skipped",
                    "reason": "missing_capabilities",
                    "missing": ["minimum_optimization", "frequencies"],
                },
                "stationary_point": self._minimum_stationary_point_payload(skipped, irc_id),
                "missing": ["minimum_optimization", "frequencies"],
                "verdict": "OK",
            }
        output_dir = (
            self.work_root
            / "minimum_validation"
            / irc_id
            / str(candidate.metadata.get("direction") or "endpoint")
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        opt_result = self.backend.optimize(
            np.asarray(candidate.coordinates, dtype=float),
            list(candidate.symbols),
            charge=candidate.charge,
            multiplicity=candidate.multiplicity,
            output_dir=output_dir,
            output_name="minimum",
        )
        optimized_coordinates = np.asarray(
            opt_result.coordinates if opt_result.coordinates is not None else candidate.coordinates,
            dtype=float,
        )
        freq_result = self.backend.frequency(
            optimized_coordinates,
            list(candidate.symbols),
            charge=candidate.charge,
            multiplicity=candidate.multiplicity,
            output_dir=output_dir,
            output_name="minimum_freq",
        )
        frequencies = tuple(float(freq) for freq in (freq_result.frequencies or []))
        validated = bool(opt_result.success and freq_result.success and frequencies) and all(
            freq >= 0.0 for freq in frequencies
        )
        updated_candidate = EndpointCandidate(
            coordinates=optimized_coordinates,
            symbols=list(candidate.symbols),
            charge=candidate.charge,
            multiplicity=candidate.multiplicity,
            frequencies_cm1=frequencies if frequencies else None,
            energy_hartree=(
                freq_result.energy
                if freq_result.energy is not None
                else (
                    opt_result.energy if opt_result.energy is not None else candidate.energy_hartree
                )
            ),
            label=candidate.label,
            metadata={
                **candidate.metadata,
                "validated_minimum": validated,
                "validation_skipped": False,
            },
        )
        missing = minimum_validation_missing_evidence(updated_candidate)
        summary = {
            "status": "validated" if validated else "failed",
            "missing": list(missing),
            "lowest_frequency_cm1": min(frequencies) if frequencies else None,
            "energy_hartree": updated_candidate.energy_hartree,
        }
        return {
            "candidate": updated_candidate,
            "summary": summary,
            "stationary_point": self._minimum_stationary_point_payload(updated_candidate, irc_id),
            "missing": list(missing),
            "verdict": "OK" if validated else "FAILED",
        }

    def _minimum_stationary_point_payload(
        self,
        candidate: EndpointCandidate,
        irc_id: str,
    ) -> dict[str, Any]:
        direction = str(candidate.metadata.get("direction") or "endpoint")
        geometry = ArtifactRef(
            path=str(
                candidate.metadata.get("artifact_path") or f"memory://{irc_id}_{direction}_minimum"
            ),
            sha256=_stable_hash(
                {
                    "irc_id": irc_id,
                    "direction": direction,
                    "coordinates": np.asarray(candidate.coordinates, dtype=float).tolist(),
                }
            ),
            kind="stable_state_geometry",
        )
        return StationaryPoint(
            point_id=f"minimum_{irc_id}_{direction}",
            role="intermediate",
            kind="minimum",
            geometry=geometry,
            charge=candidate.charge,
            multiplicity=candidate.multiplicity,
            state_id=None,
            route_id=str(candidate.metadata.get("route_id") or "") or None,
            energy_hartree=candidate.energy_hartree,
            metadata={
                **candidate.metadata,
                "symbols": list(candidate.symbols),
                "coordinates": np.asarray(candidate.coordinates, dtype=float).tolist(),
                "validated": candidate.metadata.get("validated_minimum", False),
                "frequencies_cm1": (
                    list(candidate.frequencies_cm1)
                    if candidate.frequencies_cm1 is not None
                    else None
                ),
            },
        ).to_dict()

    def _with_candidate_metadata(
        self,
        candidate: EndpointCandidate,
        metadata_update: dict[str, Any],
    ) -> EndpointCandidate:
        return EndpointCandidate(
            coordinates=np.asarray(candidate.coordinates, dtype=float),
            symbols=list(candidate.symbols),
            charge=candidate.charge,
            multiplicity=candidate.multiplicity,
            frequencies_cm1=candidate.frequencies_cm1,
            energy_hartree=candidate.energy_hartree,
            label=candidate.label,
            metadata={**candidate.metadata, **metadata_update},
        )


__all__ = [
    "DefaultEndpointProvider",
    "EndpointCandidate",
    "EndpointMatcher",
    "EndpointMatchThresholds",
    "connectivity_fingerprint",
    "mapped_heavy_atom_rmsd",
    "minimum_validation_missing_evidence",
    "needs_minimum_validation",
    "perceive_connectivity",
    "reaction_coordinate_signature",
]
