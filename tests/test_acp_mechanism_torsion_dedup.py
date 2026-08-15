# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false
"""Tests for the native torsion-aware deduplication primitive."""

from __future__ import annotations

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from acp.mechanism.primitives.torsion_dedup import (
    DedupRecord,
    TorsionAwareDeduplicator,
    build_signature,
    circular_distance_deg,
    heavy_atom_rmsd,
    signatures_equivalent,
)


def _butane_at_dihedral(angle_deg: float) -> tuple[Chem.Mol, np.ndarray]:
    mol = Chem.AddHs(Chem.MolFromSmiles("CCCC"))
    AllChem.EmbedMolecule(mol, randomSeed=42)
    conf = mol.GetConformer()
    AllChem.SetDihedralDeg(conf, 0, 1, 2, 3, angle_deg)
    return mol, np.asarray(conf.GetPositions(), dtype=float)


def test_circular_distance_wraps_at_180() -> None:
    assert circular_distance_deg(179.0, -179.0) == pytest.approx(2.0)
    assert circular_distance_deg(10.0, 10.0) == pytest.approx(0.0)
    assert circular_distance_deg(0.0, 90.0) == pytest.approx(90.0)


def test_build_signature_buckets_and_equivalence() -> None:
    mol, coords = _butane_at_dihedral(180.0)
    signature = build_signature(mol, coords, bin_width_deg=20.0)
    assert signature.bonds, "butane must expose the central rotatable bond"
    same = build_signature(mol, coords + 0.01, bin_width_deg=20.0)
    assert signatures_equivalent(signature, same, tolerance_deg=25.0)


def test_signatures_differ_beyond_torsion_tolerance() -> None:
    mol, anti = _butane_at_dihedral(180.0)
    _, gauche = _butane_at_dihedral(60.0)
    base = build_signature(mol, anti)
    far = build_signature(mol, gauche)
    assert base.bonds == far.bonds
    assert not signatures_equivalent(base, far, tolerance_deg=25.0)


def test_heavy_atom_rmsd_ignores_hydrogens() -> None:
    symbols = ("C", "H", "H", "O")
    coords_a = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.5]])
    coords_b = coords_a.copy()
    coords_b[1] = [5.0, 5.0, 5.0]
    assert heavy_atom_rmsd(coords_a, coords_b, symbols) == pytest.approx(0.0)


def test_deduplicator_keeps_distinct_rotamers() -> None:
    mol, anti = _butane_at_dihedral(180.0)
    _, gauche = _butane_at_dihedral(60.0)
    symbols = tuple(atom.GetSymbol() for atom in mol.GetAtoms())
    dedup = TorsionAwareDeduplicator()
    records = [
        dedup.annotate(mol, "conf_a", anti, symbols, score=-100.0),
        dedup.annotate(mol, "conf_b", anti + 0.01, symbols, score=-99.0),
        dedup.annotate(mol, "conf_c", gauche, symbols, score=-98.0),
    ]
    kept = dedup.deduplicate(records)
    assert [record.record_id for record in kept] == ["conf_a", "conf_c"]
    merged = kept[0].metadata
    assert merged["merged_from"] == ["conf_a", "conf_b"]
    assert merged["merge_count"] == 2


def test_deduplicator_rmsd_only_fallback_without_signatures() -> None:
    symbols = ("C", "C", "O")
    coords_a = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [3.0, 0.0, 0.0]])
    coords_far = coords_a.copy()
    coords_far[1] = [1.5, 1.0, 0.0]
    records = [
        DedupRecord("a", coords_a, symbols, -1.0, None, {}),
        DedupRecord("b", coords_a + 0.01, symbols, -0.9, None, {}),
        DedupRecord("c", coords_far, symbols, -0.8, None, {}),
    ]
    kept = TorsionAwareDeduplicator().deduplicate(records)
    assert [record.record_id for record in kept] == ["a", "c"]
