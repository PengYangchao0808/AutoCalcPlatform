"""Torsion-aware conformer deduplication (native port of RPH CENSO-LITE).

Preserves chemically distinct rotamers even when Cartesian RMSD is small.
Thresholds and decision order mirror ``rph_core.steps.conformer_search.
{torsion_signature,deduplicator}`` verbatim; geometry comparison is a numpy
Kabsch alignment instead of RDKit conformer bookkeeping so xyz-only inputs
(no RDKit mol) degrade gracefully to signature + RMSD dedup.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from rdkit import Chem
from rdkit.Chem import Lipinski

BondPair = tuple[int, int]

DEFAULT_BIN_WIDTH_DEG = 20.0
DEFAULT_TORSION_TOLERANCE_DEG = 25.0
DEFAULT_RMSD_PREFILTER_A = 0.25


def circular_distance_deg(left: float, right: float) -> float:
    """Shortest distance between two signed dihedral angles."""
    return abs((float(left) - float(right) + 180.0) % 360.0 - 180.0)


def dihedral(
    p0: Sequence[float],
    p1: Sequence[float],
    p2: Sequence[float],
    p3: Sequence[float],
) -> float:
    """Signed dihedral angle in degrees for four points."""
    p0_array = np.asarray(p0, dtype=float)
    p1_array = np.asarray(p1, dtype=float)
    p2_array = np.asarray(p2, dtype=float)
    p3_array = np.asarray(p3, dtype=float)
    b0 = -(p1_array - p0_array)
    b1 = p2_array - p1_array
    b2 = p3_array - p2_array
    b1 /= np.linalg.norm(b1) or 1.0
    v = b0 - np.dot(b0, b1) * b1
    w = b2 - np.dot(b2, b1) * b1
    return math.degrees(math.atan2(np.dot(np.cross(b1, v), w), np.dot(v, w)))


def rotatable_bonds(mol: Chem.Mol) -> tuple[BondPair, ...]:
    """Canonical heavy-atom rotatable bonds from the molecular graph."""
    bonds: list[BondPair] = []
    for match in mol.GetSubstructMatches(Lipinski.RotatableBondSmarts):
        i, j = sorted((int(match[0]), int(match[1])))
        bond = mol.GetBondBetweenAtoms(i, j)
        if bond is None or bond.IsInRing():
            continue
        bonds.append((i, j))
    return tuple(sorted(set(bonds)))


def _outer_atom(mol: Chem.Mol, center: int, other: int) -> int | None:
    candidates = [
        atom.GetIdx()
        for atom in mol.GetAtomWithIdx(center).GetNeighbors()
        if atom.GetIdx() != other
    ]
    heavy = [idx for idx in candidates if mol.GetAtomWithIdx(idx).GetAtomicNum() > 1]
    return min(heavy or candidates, default=None)


def torsion_values(mol: Chem.Mol, coordinates: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """Dihedral values for every rotatable bond (aligned with rotatable_bonds)."""
    coords = np.asarray(coordinates, dtype=float)
    values: list[float] = []
    for i, j in rotatable_bonds(mol):
        a = _outer_atom(mol, i, j)
        d = _outer_atom(mol, j, i)
        if a is None or d is None:
            continue
        values.append(dihedral(coords[a], coords[i], coords[j], coords[d]))
    return tuple(values)


@dataclass(frozen=True)
class TorsionSignature:
    """Binned torsion fingerprint over canonical rotatable bonds."""

    bonds: tuple[BondPair, ...]
    bins: tuple[int, ...]
    values: tuple[float, ...]
    bond_space: str = "atom_index"

    def key(self) -> str:
        parts = [
            f"{i}-{j}:{bucket}"
            for (i, j), bucket in sorted(zip(self.bonds, self.bins), key=lambda item: item[0])
        ]
        return "|".join(parts) or "no_rotatable_bonds"


def build_signature(
    mol: Chem.Mol,
    coordinates: Sequence[Sequence[float]],
    bin_width_deg: float = DEFAULT_BIN_WIDTH_DEG,
) -> TorsionSignature:
    """Build a torsion signature; atom-map labels win over raw indices."""
    if bin_width_deg <= 0.0 or bin_width_deg > 180.0:
        raise ValueError("bin_width_deg must be in the interval (0, 180]")
    idx_to_mapnum = {
        atom.GetIdx(): atom.GetAtomMapNum() for atom in mol.GetAtoms() if atom.GetAtomMapNum() > 0
    }
    has_maps = bool(idx_to_mapnum)
    coords = np.asarray(coordinates, dtype=float)
    entries: list[tuple[BondPair, int, float]] = []
    for i, j in rotatable_bonds(mol):
        a = _outer_atom(mol, i, j)
        d = _outer_atom(mol, j, i)
        if a is None or d is None:
            continue
        value = dihedral(coords[a], coords[i], coords[j], coords[d])
        bond_label: BondPair = (i, j)
        if has_maps and i in idx_to_mapnum and j in idx_to_mapnum:
            bond_label = tuple(sorted((idx_to_mapnum[i], idx_to_mapnum[j])))  # type: ignore[assignment]
        entries.append((bond_label, int(math.floor((value + 180.0) / bin_width_deg)), value))
    entries.sort(key=lambda item: item[0])
    return TorsionSignature(
        bonds=tuple(item[0] for item in entries),
        bins=tuple(item[1] for item in entries),
        values=tuple(item[2] for item in entries),
        bond_space="atom_map" if has_maps else "atom_index",
    )


def signatures_equivalent(
    left: TorsionSignature,
    right: TorsionSignature,
    tolerance_deg: float = DEFAULT_TORSION_TOLERANCE_DEG,
) -> bool:
    """True when both signatures cover identical bonds within tolerance."""
    if left.bond_space != right.bond_space:
        return False
    if left.bonds != right.bonds or len(left.values) != len(right.values):
        return False
    return all(
        circular_distance_deg(a, b) <= tolerance_deg for a, b in zip(left.values, right.values)
    )


def heavy_atom_rmsd(
    coordinates_a: Sequence[Sequence[float]] | np.ndarray,
    coordinates_b: Sequence[Sequence[float]] | np.ndarray,
    symbols: Sequence[str] | None = None,
) -> float:
    """Heavy-atom RMSD after Kabsch alignment (translation-only if <3 atoms)."""
    mobile = np.asarray(coordinates_a, dtype=float)
    target = np.asarray(coordinates_b, dtype=float)
    if mobile.shape != target.shape:
        return float("inf")
    if symbols is not None:
        keep = np.array([str(s).strip().upper() != "H" for s in symbols], dtype=bool)
        if keep.any():
            mobile = mobile[keep]
            target = target[keep]
    if mobile.shape[0] == 0:
        return 0.0
    mobile_centered = mobile - np.mean(mobile, axis=0)
    target_centered = target - np.mean(target, axis=0)
    if mobile_centered.shape[0] < 3:
        diff = mobile_centered - target_centered
        return float(np.sqrt(np.mean(np.sum(diff**2, axis=1))))
    covariance = mobile_centered.T @ target_centered
    u_matrix, _, vt_matrix = np.linalg.svd(covariance)
    rotation = u_matrix @ vt_matrix
    if np.linalg.det(rotation) < 0.0:
        u_matrix[:, -1] *= -1.0
        rotation = u_matrix @ vt_matrix
    aligned = mobile_centered @ rotation
    diff = aligned - target_centered
    return float(np.sqrt(np.mean(np.sum(diff**2, axis=1))))


@dataclass(frozen=True)
class DedupRecord:
    """One deduplication candidate: geometry + score + optional signature."""

    record_id: str
    coordinates: np.ndarray
    symbols: tuple[str, ...]
    score: float
    signature: TorsionSignature | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _merged_metadata(existing: DedupRecord, incoming: DedupRecord) -> dict[str, Any]:
    """Merge sampling provenance while keeping the representative's identity."""
    metadata = dict(existing.metadata)
    merged_from = list(metadata.get("merged_from") or [existing.record_id])
    for source_id in [incoming.record_id, *list(incoming.metadata.get("merged_from") or [])]:
        if source_id not in merged_from:
            merged_from.append(source_id)
    metadata.update(
        {
            "merged_from": merged_from,
            "merge_count": len(merged_from),
            "degeneracy": int(metadata.get("degeneracy", 1)),
            "degeneracy_source": metadata.get("degeneracy_source", "default_unique_minimum"),
        }
    )
    return metadata


class TorsionAwareDeduplicator:
    """Keep chemically distinct rotamers; collapse redundant resamples.

    A candidate is a duplicate of a kept record when (a) both carry
    torsion signatures over identical bonds within the torsion tolerance,
    or both carry no signature, AND (b) heavy-atom RMSD is within the
    prefilter threshold. Duplicates merge their sampling provenance into
    the representative (RPH semantics: CREST frequency is provenance, not
    physical degeneracy).
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        bin_width_deg: float = DEFAULT_BIN_WIDTH_DEG,
        torsion_tolerance_deg: float = DEFAULT_TORSION_TOLERANCE_DEG,
        rmsd_prefilter_a: float = DEFAULT_RMSD_PREFILTER_A,
    ) -> None:
        cfg = dict(config or {})
        self.bin_width_deg = float(cfg.get("torsion_bin_deg", bin_width_deg))
        self.torsion_tolerance_deg = float(cfg.get("torsion_rmsd_deg", torsion_tolerance_deg))
        self.rmsd_prefilter_a = float(cfg.get("heavy_atom_rmsd_prefilter_A", rmsd_prefilter_a))

    def annotate(
        self,
        mol: Chem.Mol | None,
        record_id: str,
        coordinates: Sequence[Sequence[float]] | np.ndarray,
        symbols: Sequence[str],
        score: float,
        metadata: dict[str, Any] | None = None,
    ) -> DedupRecord:
        """Build a record, computing the torsion signature when a mol is given."""
        signature = (
            build_signature(mol, coordinates, self.bin_width_deg) if mol is not None else None
        )
        payload = dict(metadata or {})
        if signature is not None:
            payload["torsion_signature"] = signature.key()
        payload.setdefault("source_conformer_id", record_id)
        payload.setdefault("merged_from", [record_id])
        payload.setdefault("merge_count", 1)
        payload.setdefault("degeneracy", 1)
        payload.setdefault("degeneracy_source", "default_unique_minimum")
        return DedupRecord(
            record_id=record_id,
            coordinates=np.asarray(coordinates, dtype=float),
            symbols=tuple(str(s) for s in symbols),
            score=float(score),
            signature=signature,
            metadata=payload,
        )

    def deduplicate(self, candidates: Sequence[DedupRecord]) -> list[DedupRecord]:
        """Ordered dedup by (score, id); duplicates merge into the keeper."""
        kept: list[DedupRecord] = []
        for candidate in sorted(candidates, key=lambda item: (item.score, item.record_id)):
            duplicate_of: DedupRecord | None = None
            for existing in kept:
                if not self._signatures_match(candidate, existing):
                    continue
                rmsd = heavy_atom_rmsd(
                    candidate.coordinates,
                    existing.coordinates,
                    candidate.symbols if candidate.symbols == existing.symbols else None,
                )
                if rmsd <= self.rmsd_prefilter_a:
                    duplicate_of = existing
                    break
            if duplicate_of is None:
                kept.append(candidate)
                continue
            merged = _merged_metadata(duplicate_of, candidate)
            kept[kept.index(duplicate_of)] = replace(duplicate_of, metadata=merged)
        return kept

    def _signatures_match(self, left: DedupRecord, right: DedupRecord) -> bool:
        if left.signature is None or right.signature is None:
            return left.signature is None and right.signature is None
        return signatures_equivalent(left.signature, right.signature, self.torsion_tolerance_deg)


__all__ = [
    "DEFAULT_BIN_WIDTH_DEG",
    "DEFAULT_RMSD_PREFILTER_A",
    "DEFAULT_TORSION_TOLERANCE_DEG",
    "DedupRecord",
    "TorsionAwareDeduplicator",
    "TorsionSignature",
    "build_signature",
    "circular_distance_deg",
    "dihedral",
    "heavy_atom_rmsd",
    "rotatable_bonds",
    "signatures_equivalent",
    "torsion_values",
]
