"""IRC endpoint geometry classification and TS identity judgment.

Migrated from the former endpoint/identity modules.  Stripped of
study/stage/promotion semantics — pure geometry algorithms only.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Covalent radii (Å) — same table as mechanism/endpoint.py
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EndpointMatchThresholds:
    """Threshold bundle for endpoint geometry classification.

    Attributes:
        rmsd_match_A: Heavy-atom RMSD threshold for a confident match.
        rmsd_ambiguous_A: Heavy-atom RMSD for a borderline match.
        covalent_scale: Multiplicative bond-perception factor on covalent radii.
        covalent_tolerance_A: Additive bond-perception slack in Å.
    """

    rmsd_match_A: float = 0.3  # noqa: N815
    rmsd_ambiguous_A: float = 0.75  # noqa: N815
    covalent_scale: float = 1.25
    covalent_tolerance_A: float = 0.12  # noqa: N815


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EndpointClassification:
    """Pure-geometry endpoint classification result.

    Attributes:
        connectivity_fingerprint: Hash of the perceived bond graph.
        rmsd_to_reference: Heavy-atom RMSD to the reference geometry (Å).
        connectivity_matches_reference: Whether bond graphs are identical.
        verdict: One of ``MATCH``, ``CLOSE``, ``DIFFERENT``.
    """

    connectivity_fingerprint: str = ""
    rmsd_to_reference: float | None = None
    connectivity_matches_reference: bool = False
    verdict: str = "UNCLASSIFIED"


# ---------------------------------------------------------------------------
# TS identity (migrated from mechanism/identity.py:175-229)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TsIdentity:
    """Transition-state identity verdict from frequency evidence.

    Attributes:
        imaginary_count: Number of imaginary frequencies.
        imaginary_frequency_cm1: Most-negative frequency (cm⁻¹).
        mode_match_score: Reaction-coordinate mode overlap (if computed).
        topology_sane: Whether the topology check passed.
        valid: ``True`` when the TS is validated.
        messages: Human-readable diagnostics.
    """

    imaginary_count: int = 0
    imaginary_frequency_cm1: float | None = None
    mode_match_score: float | None = None
    topology_sane: bool = True
    valid: bool = False
    messages: list[str] = field(default_factory=list)


def classify_ts_identity(
    imaginary_frequencies: Sequence[float],
    *,
    mode_match_score: float | None = None,
    topology_sane: bool = True,
    imaginary_cutoff_cm1: float = -50.0,
    mode_match_threshold: float = 0.05,
    rc_alignment: float | None = None,
    rc_alignment_threshold: float = 0.5,
) -> TsIdentity:
    """Build a :class:`TsIdentity` from frequency + mode-overlap evidence.

    Valid when exactly one imaginary frequency exists, it is below
    *imaginary_cutoff_cm1*, the mode-match score (when computed) is at or
    above *mode_match_threshold*, and the topology is sane.
    """
    imaginary = [float(f) for f in imaginary_frequencies if float(f) < 0.0]
    count = len(imaginary)
    lowest = min(imaginary) if imaginary else None
    messages: list[str] = []

    if count != 1:
        messages.append(f"imaginary frequency count = {count} (expected 1)")
    elif lowest is not None and lowest > imaginary_cutoff_cm1:
        messages.append(
            f"imaginary frequency {lowest:.1f} cm⁻¹ above cutoff {imaginary_cutoff_cm1:.1f}"
        )
    if mode_match_score is not None and mode_match_score < mode_match_threshold:
        messages.append(
            f"reaction-coordinate mode overlap {mode_match_score:.3f} "
            f"below threshold {mode_match_threshold:.3f}"
        )
    if rc_alignment is not None and rc_alignment < rc_alignment_threshold:
        messages.append(
            f"reaction-coordinate alignment {rc_alignment:.3f} "
            f"below threshold {rc_alignment_threshold:.3f}"
        )
    if not topology_sane:
        messages.append("topology check failed")

    valid = (
        count == 1
        and (lowest is None or lowest <= imaginary_cutoff_cm1)
        and (mode_match_score is None or mode_match_score >= mode_match_threshold)
        and (rc_alignment is None or rc_alignment >= rc_alignment_threshold)
        and topology_sane
    )
    return TsIdentity(
        imaginary_count=count,
        imaginary_frequency_cm1=lowest,
        mode_match_score=mode_match_score,
        topology_sane=topology_sane,
        valid=valid,
        messages=messages,
    )


# ---------------------------------------------------------------------------
# Connectivity perception (migrated from mechanism/endpoint.py:307-340)
# ---------------------------------------------------------------------------


def perceive_connectivity(
    symbols: list[str] | tuple[str, ...],
    coordinates: NDArray[np.float64] | list[list[float]],
    thresholds: EndpointMatchThresholds | None = None,
) -> set[tuple[int, int]]:
    """Infer an undirected bond graph from covalent radii.

    Args:
        symbols: Atomic symbols.
        coordinates: Geometry in Å.
        thresholds: Bond-perception thresholds.

    Returns:
        Undirected edges encoded as sorted ``(i, j)`` index pairs.
    """
    if thresholds is None:
        thresholds = EndpointMatchThresholds()
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
    thresholds: EndpointMatchThresholds | None = None,
) -> str:
    """Return a stable connectivity fingerprint string."""
    if thresholds is None:
        thresholds = EndpointMatchThresholds()
    normalized_symbols = _normalize_symbols(symbols)
    edges = perceive_connectivity(normalized_symbols, coordinates, thresholds)
    labels = [
        f"{normalized_symbols[i]}{i + 1}-{normalized_symbols[j]}{j + 1}" for i, j in sorted(edges)
    ]
    return _stable_hash({"symbols": normalized_symbols, "edges": labels})


# ---------------------------------------------------------------------------
# Heavy-atom RMSD (migrated from mechanism/endpoint.py:377-425)
# ---------------------------------------------------------------------------


def mapped_heavy_atom_rmsd(
    candidate_coordinates: NDArray[np.float64] | list[list[float]],
    candidate_symbols: list[str] | tuple[str, ...],
    reference_coordinates: NDArray[np.float64] | list[list[float]],
    reference_symbols: list[str] | tuple[str, ...],
    mapping_pairs: list[tuple[int, int]],
) -> float:
    """Return heavy-atom RMSD after Kabsch alignment on mapped atoms.

    Degenerate cases with fewer than three mapped heavy atoms fall back to a
    centroid-aligned RMSD (translation-only).  This avoids singular Kabsch
    fits for one- and two-atom heavy frameworks while keeping the comparison
    purely geometry-based.
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


# ---------------------------------------------------------------------------
# Endpoint geometry classifier
# ---------------------------------------------------------------------------


def classify_endpoint_geometry(
    candidate_symbols: list[str] | tuple[str, ...],
    candidate_coordinates: NDArray[np.float64] | list[list[float]],
    reference_symbols: list[str] | tuple[str, ...],
    reference_coordinates: NDArray[np.float64] | list[list[float]],
    *,
    thresholds: EndpointMatchThresholds | None = None,
    atom_mapping: list[tuple[int, int]] | None = None,
) -> EndpointClassification:
    """Classify an IRC endpoint geometry against a reference structure.

    This is a pure geometry comparison — no study/promotion semantics.

    Args:
        candidate_symbols: Endpoint element symbols.
        candidate_coordinates: Endpoint geometry (Å).
        reference_symbols: Reference (product/reactant) element symbols.
        reference_coordinates: Reference geometry (Å).
        thresholds: Classification thresholds.
        atom_mapping: Optional pre-computed atom mapping pairs
            (candidate_index, reference_index).  When ``None``, an
            identity mapping is attempted for equal-length symbol lists.

    Returns:
        Classification verdict.
    """
    if thresholds is None:
        thresholds = EndpointMatchThresholds()

    cand_fingerprint = connectivity_fingerprint(
        candidate_symbols, candidate_coordinates, thresholds
    )
    ref_fingerprint = connectivity_fingerprint(reference_symbols, reference_coordinates, thresholds)

    # Determine mapping pairs
    if atom_mapping is not None:
        pairs = atom_mapping
    else:
        norm_cand = _normalize_symbols(candidate_symbols)
        norm_ref = _normalize_symbols(reference_symbols)
        if len(norm_cand) == len(norm_ref):
            pairs = [(i, i) for i in range(len(norm_cand))]
        else:
            pairs = _mapping_pairs_by_occurrence(candidate_symbols, reference_symbols)

    if pairs:
        cand_edges = perceive_connectivity(candidate_symbols, candidate_coordinates, thresholds)
        mapped_edges = _map_edges(cand_edges, pairs)
        ref_edges = perceive_connectivity(reference_symbols, reference_coordinates, thresholds)
        connectivity_match = mapped_edges == ref_edges if mapped_edges is not None else False
        rmsd_value = mapped_heavy_atom_rmsd(
            candidate_coordinates,
            candidate_symbols,
            reference_coordinates,
            reference_symbols,
            pairs,
        )
    else:
        connectivity_match = cand_fingerprint == ref_fingerprint
        rmsd_value = None

    if rmsd_value is not None and rmsd_value <= thresholds.rmsd_match_A:
        verdict = "MATCH"
    elif rmsd_value is not None and rmsd_value <= thresholds.rmsd_ambiguous_A:
        verdict = "CLOSE"
    else:
        verdict = "DIFFERENT"

    return EndpointClassification(
        connectivity_fingerprint=cand_fingerprint,
        rmsd_to_reference=rmsd_value,
        connectivity_matches_reference=connectivity_match,
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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


def _stable_hash(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


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


def _mapping_pairs_by_occurrence(
    candidate_symbols: Sequence[str],
    reference_symbols: Sequence[str],
) -> list[tuple[int, int]]:
    """Map atoms by symbol occurrence when no explicit mapping is provided."""
    norm_cand = _normalize_symbols(candidate_symbols)
    norm_ref = _normalize_symbols(reference_symbols)
    available = list(range(len(norm_ref)))
    pairs: list[tuple[int, int]] = []
    for ci, symbol in enumerate(norm_cand):
        for ai, ri in enumerate(available):
            if norm_ref[ri] == symbol:
                pairs.append((ci, ri))
                available.pop(ai)
                break
    return pairs


__all__ = [
    "EndpointClassification",
    "EndpointMatchThresholds",
    "TsIdentity",
    "classify_endpoint_geometry",
    "classify_ts_identity",
    "connectivity_fingerprint",
    "mapped_heavy_atom_rmsd",
    "perceive_connectivity",
]
