"""Input-layer molecular-graph capture + topology provenance (gap G01).

Locks the ``nmr_topology_source`` contract (smiles | sdf | xyz_inferred |
xyz_unavailable): the same molecule parsed three ways yields an identical
``atom_uid -> element`` map, XYZ without an explicit charge is marked
``xyz_unavailable`` (strict mode rejects it with a typed error), and an
unavailable graph never degrades into an element-merged stand-in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from acp.core.models import Structure
from acp.io.structures import (
    NMR_TOPOLOGY_SDF,
    NMR_TOPOLOGY_SMILES,
    NMR_TOPOLOGY_XYZ_INFERRED,
    NMR_TOPOLOGY_XYZ_UNAVAILABLE,
    TopologyUnavailableError,
    capture_nmr_topology,
)
from acp.nmr.structure_map import NmrStructureMap
from acp.workflows.nmr import (
    _parse_candidates,
    nmr_structure_map_for,
    nmr_topology_mol_for,
    nmr_topology_source_for,
)

ETHANOL_SMILES = "CCO"


def _ethanol_mol() -> Chem.Mol:
    mol = Chem.AddHs(Chem.MolFromSmiles(ETHANOL_SMILES))
    assert AllChem.EmbedMolecule(mol, randomSeed=0xF00D) == 0
    return mol


def _write_sdf(mol: Chem.Mol, path: Path) -> Path:
    writer = Chem.SDWriter(str(path))
    writer.write(mol)
    writer.close()
    return path


def _write_xyz(mol: Chem.Mol, path: Path, comment: str) -> Path:
    conf = mol.GetConformer()
    lines = [str(mol.GetNumAtoms()), comment]
    for atom in mol.GetAtoms():
        pos = conf.GetAtomPosition(atom.GetIdx())
        lines.append(f"{atom.GetSymbol()} {pos.x:.6f} {pos.y:.6f} {pos.z:.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _uid_elements(structure_map: NmrStructureMap) -> dict[str, str]:
    return dict(zip(structure_map.atom_uids, structure_map.elements))


# ---------------------------------------------------------------------------
# Cross-format identity: SMILES vs SDF vs mapped XYZ
# ---------------------------------------------------------------------------


def test_three_formats_give_identical_uid_element_maps(tmp_path: Path) -> None:
    mol = _ethanol_mol()
    sdf = _write_sdf(mol, tmp_path / "ethanol.sdf")
    xyz = _write_xyz(mol, tmp_path / "ethanol.xyz", "ethanol charge=0")

    candidates = _parse_candidates([ETHANOL_SMILES, str(sdf), str(xyz)], None, None)

    assert [c.metadata["nmr_topology_source"] for c in candidates] == [
        NMR_TOPOLOGY_SMILES,
        NMR_TOPOLOGY_SDF,
        NMR_TOPOLOGY_XYZ_INFERRED,
    ]
    maps = [nmr_structure_map_for(c) for c in candidates]
    assert all(m is not None for m in maps)
    uid_elements = [_uid_elements(m) for m in maps if m is not None]
    assert uid_elements[0] == uid_elements[1] == uid_elements[2]
    # map element order is the candidate's atom order (QC join stays valid)
    for candidate, structure_map in zip(candidates, maps):
        assert structure_map is not None
        assert list(structure_map.elements) == list(candidate.symbols)


def test_inline_molblock_captures_sdf_topology(tmp_path: Path) -> None:
    molblock = Chem.MolToMolBlock(_ethanol_mol())
    candidates = _parse_candidates([molblock], None, None)
    assert candidates[0].metadata["nmr_topology_source"] == NMR_TOPOLOGY_SDF
    assert nmr_structure_map_for(candidates[0]) is not None
    assert nmr_topology_mol_for(candidates[0]) is not None


def test_missing_structure_file_is_unavailable_not_smiles(tmp_path: Path) -> None:
    capture = capture_nmr_topology(str(tmp_path / "absent.xyz"))
    assert capture.topology_source == NMR_TOPOLOGY_XYZ_UNAVAILABLE
    assert capture.mol is None
    assert "not found" in (capture.reason or "")


# ---------------------------------------------------------------------------
# XYZ: explicit charge required, never guessed
# ---------------------------------------------------------------------------


def test_xyz_without_charge_is_unavailable(tmp_path: Path) -> None:
    xyz = _write_xyz(_ethanol_mol(), tmp_path / "ethanol.xyz", "ethanol")
    candidates = _parse_candidates([str(xyz)], None, None)

    candidate = candidates[0]
    assert candidate.metadata["nmr_topology_source"] == NMR_TOPOLOGY_XYZ_UNAVAILABLE
    assert candidate.metadata["nmr_structure_map"] is None
    assert candidate.metadata["nmr_topology_mol"] is None
    assert "explicit charge" in (candidate.metadata["nmr_topology_reason"] or "")
    assert nmr_structure_map_for(candidate) is None
    assert nmr_topology_mol_for(candidate) is None
    assert nmr_topology_source_for(candidate) == NMR_TOPOLOGY_XYZ_UNAVAILABLE


def test_xyz_charge_argument_enables_inference(tmp_path: Path) -> None:
    xyz = _write_xyz(_ethanol_mol(), tmp_path / "ethanol.xyz", "ethanol")
    candidates = _parse_candidates([str(xyz)], 0, 1)
    assert candidates[0].metadata["nmr_topology_source"] == NMR_TOPOLOGY_XYZ_INFERRED
    assert nmr_structure_map_for(candidates[0]) is not None


def test_determine_bonds_failure_marks_unavailable(tmp_path: Path) -> None:
    # two carbons 10 A apart with charge=0: bond ordering cannot satisfy the
    # charge, so DetermineBonds raises and provenance must fall back to
    # "xyz_unavailable" instead of any fabricated graph.
    xyz = tmp_path / "far.xyz"
    xyz.write_text("2\ncharge=0\nC 0.0 0.0 0.0\nC 10.0 0.0 0.0\n", encoding="utf-8")

    capture = capture_nmr_topology(str(xyz))
    assert capture.topology_source == NMR_TOPOLOGY_XYZ_UNAVAILABLE
    assert capture.mol is None
    assert "DetermineBonds" in (capture.reason or "")

    candidates = _parse_candidates([str(xyz)], None, None)
    assert candidates[0].metadata["nmr_topology_source"] == NMR_TOPOLOGY_XYZ_UNAVAILABLE
    assert nmr_structure_map_for(candidates[0]) is None


def test_open_shell_xyz_bond_orders_never_guessed(tmp_path: Path) -> None:
    xyz = _write_xyz(_ethanol_mol(), tmp_path / "ethanol.xyz", "ethanol charge=0")
    capture = capture_nmr_topology(str(xyz), multiplicity=2)
    assert capture.topology_source == NMR_TOPOLOGY_XYZ_UNAVAILABLE
    assert capture.mol is None
    assert "multiplicity=2" in (capture.reason or "")


def test_unavailable_topology_never_yields_element_merged_graph(tmp_path: Path) -> None:
    # propane: three distinct carbons; without a graph the capture must expose
    # no mol/map at all, so no downstream stage can merge on element alone.
    xyz = tmp_path / "propane.xyz"
    xyz.write_text(
        "11\n\n"
        "C -0.5 0.0 0.0\nC 0.9 0.3 0.0\nC 1.9 -0.6 0.4\n"
        "H -0.9 -0.4 0.9\nH -0.9 -0.4 -0.9\nH -0.9 0.9 0.0\n"
        "H 1.1 1.0 0.8\nH 1.1 0.5 -1.0\nH 1.8 -1.4 1.0\nH 2.8 -0.2 0.4\nH 2.0 -0.8 -0.6\n",
        encoding="utf-8",
    )
    candidates = _parse_candidates([str(xyz)], None, None)
    candidate = candidates[0]
    assert candidate.metadata["nmr_topology_source"] == NMR_TOPOLOGY_XYZ_UNAVAILABLE
    assert candidate.metadata["nmr_structure_map"] is None
    assert candidate.metadata["nmr_topology_mol"] is None
    assert nmr_structure_map_for(candidate) is None
    assert nmr_topology_mol_for(candidate) is None


# ---------------------------------------------------------------------------
# Strict mode: typed rejection
# ---------------------------------------------------------------------------


def test_strict_topology_raises_typed_error(tmp_path: Path) -> None:
    xyz = _write_xyz(_ethanol_mol(), tmp_path / "ethanol.xyz", "ethanol")

    with pytest.raises(TopologyUnavailableError, match="topology unavailable"):
        _parse_candidates([str(xyz)], None, None, strict_topology=True)
    with pytest.raises(ValueError):
        _parse_candidates([str(xyz)], None, None, strict_topology=True)


def test_strict_topology_accepts_available_graphs() -> None:
    candidates = _parse_candidates([ETHANOL_SMILES], None, None, strict_topology=True)
    assert nmr_structure_map_for(candidates[0]) is not None


def test_non_strict_default_never_raises(tmp_path: Path) -> None:
    xyz = _write_xyz(_ethanol_mol(), tmp_path / "ethanol.xyz", "ethanol")
    candidates = _parse_candidates([str(xyz)], None, None)
    assert len(candidates) == 1


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------


def test_map_accessor_roundtrips_stored_payload(tmp_path: Path) -> None:
    candidates = _parse_candidates([ETHANOL_SMILES], None, None)
    candidate = candidates[0]
    structure_map = nmr_structure_map_for(candidate)
    assert structure_map is not None

    mol = nmr_topology_mol_for(candidate)
    assert mol is not None
    direct = NmrStructureMap.from_mol(mol)
    assert structure_map.atom_uids == direct.atom_uids
    assert _uid_elements(structure_map) == _uid_elements(direct)


def test_accessors_on_uncaptured_structure_default_to_unavailable() -> None:
    bare = Structure(id="bare", symbols=["C"], coordinates=[[0.0, 0.0, 0.0]])
    assert nmr_topology_source_for(bare) == NMR_TOPOLOGY_XYZ_UNAVAILABLE
    assert nmr_structure_map_for(bare) is None
    assert nmr_topology_mol_for(bare) is None
