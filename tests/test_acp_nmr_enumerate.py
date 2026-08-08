"""Unit tests for stereoisomer enumeration (DevDoc §5 stage 1, P2).

Exercises RDKit-driven diastereomer enumeration: fully-unspecified inputs,
enantiomer dedup (DP4 cannot distinguish enantiomers), stereocenter
filtering, the max-isomers cap, and error paths (XYZ has no bond table).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.nmr.enumerate import (
    EnumerateOptions,
    enumerate_candidates,
    enumerate_to_smiles,
)


def _smiles_set(candidates) -> set[str]:
    return {c.smiles for c in candidates}


# ---------------------------------------------------------------------------
# Core enumeration
# ---------------------------------------------------------------------------


def test_two_centers_unspecified_yields_two_diastereomers() -> None:
    # 2,3-dichlorobutane: 2 stereocenters → 4 isomers, enantiomer pairs
    # collapse to 2 distinct diastereomers (meso + the racemic pair rep).
    cands = enumerate_candidates("CC(Cl)C(Cl)C")
    assert len(cands) == 2
    assert all(c.stereocenters == 2 for c in cands)
    assert all(c.enumerated_centers == 2 for c in cands)
    # labels are stable + 1-based
    assert [c.label for c in cands] == ["diastereomer_1", "diastereomer_2"]


def test_no_stereocenters_returns_single_candidate() -> None:
    # Ethanol has no stereocenters → one candidate, zero enumerated.
    cands = enumerate_candidates("CCO")
    assert len(cands) == 1
    assert cands[0].stereocenters == 0
    assert cands[0].enumerated_centers == 0
    assert cands[0].label == "diastereomer_1"


def test_fully_specified_input_not_enumerated() -> None:
    # A fully-specified chiral input stays as one candidate (onlyUnassigned).
    cands = enumerate_candidates("C[C@@H](O)[C@H](O)C")
    assert len(cands) == 1


def test_three_centers_collapse_to_four_diastereomers() -> None:
    # 3 stereocenters on an asymmetric skeleton → 2^3 = 8 isomers → 4
    # enantiomer pairs. (A symmetric skeleton would give fewer due to meso
    # degeneracy, so we pick distinct substituents at each centre.)
    cands = enumerate_candidates("OCC(N)C(O)C(Cl)F")
    assert all(c.stereocenters == 3 for c in cands)
    assert len(cands) == 4


# ---------------------------------------------------------------------------
# Enantiomer dedup
# ---------------------------------------------------------------------------


def test_enantiomer_dedup_default_true() -> None:
    # A molecule with a single UNSPECIFIED stereocenter has only enantiomers —
    # under the default dedup it must collapse to ONE candidate.
    cands = enumerate_candidates("CC(Cl)Br")
    assert len(cands) == 1


def test_enantiomer_dedup_disabled_doubles_single_center() -> None:
    # With dedup off, a single unspecified center yields both enantiomers.
    cands = enumerate_candidates("CC(Cl)Br", options=EnumerateOptions(dedup_enantiomers=False))
    assert len(cands) == 2


def test_dedup_keeps_diastereomers_distinct() -> None:
    # meso + pair: even with dedup the diastereomers stay distinct.
    dedup = enumerate_candidates("CC(Cl)C(Cl)C")
    full = enumerate_candidates("CC(Cl)C(Cl)C", options=EnumerateOptions(dedup_enantiomers=False))
    assert len(dedup) < len(full)
    assert len(dedup) == 2


# ---------------------------------------------------------------------------
# Stereocenter filter
# ---------------------------------------------------------------------------


def test_stereocenter_filter_restricts_enumeration() -> None:
    # 3-center molecule: enumerating only ONE center yields 2 isomers
    # (the R/S pair at that center, others pinned) → dedup → could be 1 or 2.
    cands = enumerate_candidates("CC(Cl)C(Cl)C(Cl)C", stereocenters="C2")
    assert 1 <= len(cands) <= 2
    assert all(c.enumerated_centers == 1 for c in cands)


def test_stereocenter_filter_list_form() -> None:
    # list argument must behave the same as the comma string.
    a = _smiles_set(enumerate_candidates("CC(Cl)C(Cl)C", stereocenters="C2,C3"))
    b = _smiles_set(enumerate_candidates("CC(Cl)C(Cl)C", stereocenters=["C2", "C3"]))
    assert a == b


def test_stereocenter_filter_unknown_label_raises() -> None:
    with pytest.raises(ValueError, match="matched no heavy atoms"):
        enumerate_candidates("CC(Cl)C(Cl)C", stereocenters="Z9")


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


def test_max_isomers_cap() -> None:
    cands = enumerate_candidates("CC(Cl)C(Cl)C(Cl)C", options=EnumerateOptions(max_isomers=2))
    assert len(cands) <= 2


def test_reproducible_with_seed() -> None:
    a = _smiles_set(enumerate_candidates("CC(Cl)C(Cl)C(Cl)C", options=EnumerateOptions(seed=7)))
    b = _smiles_set(enumerate_candidates("CC(Cl)C(Cl)C(Cl)C", options=EnumerateOptions(seed=7)))
    assert a == b


# ---------------------------------------------------------------------------
# Input formats
# ---------------------------------------------------------------------------


def test_sdf_file_input(tmp_path: Path) -> None:
    pytest.importorskip("rdkit")
    from rdkit import Chem

    mol = Chem.AddHs(Chem.MolFromSmiles("CC(Cl)C(Cl)C"))
    # assign a conformer so MolToMolBlock is well-formed
    from rdkit.Chem import AllChem

    AllChem.EmbedMolecule(mol, randomSeed=0)
    sdf = tmp_path / "cands.sdf"
    with Chem.SDWriter(str(sdf)) as w:
        w.write(mol)
    cands = enumerate_candidates(sdf)
    assert len(cands) >= 1
    assert cands[0].metadata.get("source_kind") == "sdf"


def test_molblock_text_input() -> None:
    pytest.importorskip("rdkit")
    from rdkit import Chem

    # Heavy-atom molblock (no explicit H, no coords). The parse path now
    # falls back through SDMolSupplier, tolerating RDKit's version-flaky
    # counts-line handling.
    mol = Chem.MolFromSmiles("CC(Cl)C(Cl)C")
    block = Chem.MolToMolBlock(mol)
    cands = enumerate_candidates(block)
    assert len(cands) == 2
    assert cands[0].metadata.get("source_kind") == "molblock"


def test_enumerate_to_smiles_returns_strings() -> None:
    smiles = enumerate_to_smiles("CC(Cl)C(Cl)C")
    assert isinstance(smiles, list)
    assert all(isinstance(s, str) for s in smiles)
    assert len(smiles) == 2


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_xyz_input_raises() -> None:
    with pytest.raises(ValueError, match="no bond table|XYZ"):
        enumerate_candidates("/tmp/does_not_exist_xyz.xyz")


def test_missing_file_raises() -> None:
    with pytest.raises(ValueError, match="not found|Unsupported|Invalid"):
        enumerate_candidates(Path("/tmp/acp_nmr_does_not_exist.mol"))


def test_invalid_smiles_raises() -> None:
    # garbage that is not SMILES, not a file, not a mol block
    with pytest.raises(ValueError):
        enumerate_candidates("this is not a molecule at all")


def test_empty_input_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        enumerate_candidates("")
