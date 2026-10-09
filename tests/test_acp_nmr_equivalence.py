"""Tests for symmetry-equivalence detection (DevDoc §8.3 / G01)."""

from __future__ import annotations

import pytest

from acp.nmr.equivalence import (
    EQ_BASIS_EXPLICIT,
    EQ_BASIS_TOPOLOGY,
    EQ_BASIS_UNKNOWN,
    EquivalenceError,
    build_all_labels,
    build_label_for_atom,
    detect_equivalence_groups,
    merge_explicit_and_detected,
)


def test_detect_no_topology_yields_single_atom_groups() -> None:
    # G01 regression (todo 4): without a molecular graph the old code
    # merged atoms purely by element ([[0, 1], [2], [3, 4]]). Same-element
    # atoms are NOT proven equivalent, so each atom stays its own group
    # and the result is flagged equivalence_unknown.
    groups = detect_equivalence_groups(["C", "C", "O", "H", "H"])
    assert len(groups) == 5
    assert all(len(x) == 1 for x in groups)
    assert groups.equivalence_unknown is True
    assert {g.basis for g in groups} == {EQ_BASIS_UNKNOWN}


def test_detect_groups_single_element() -> None:
    # Old expectation (pre-G01 fix): the element-only fallback collapsed
    # all 4 hydrogens into one group. Changed because element identity
    # alone never proves equivalence — no topology ⇒ 4 singletons + unknown.
    groups = detect_equivalence_groups(["H", "H", "H", "H"])
    assert len(groups) == 4
    assert all(len(g) == 1 for g in groups)
    assert groups.equivalence_unknown is True


def test_detect_groups_mixed_elements_without_topology_do_not_merge() -> None:
    # Old expectation (enshrined the element-merge defect):
    # by_first[0] == [0, 3] (two carbons) and by_first[1] == [1, 2, 4]
    # (three hydrogens). G01/G03 fix (todo 4): without a graph those are
    # singletons with unknown basis — they were never proven equivalent.
    groups = detect_equivalence_groups(["C", "H", "H", "C", "H"])
    assert len(groups) == 5
    assert all(len(g) == 1 for g in groups)
    assert groups.equivalence_unknown is True


def test_detect_groups_with_topology_preserves_symmetry() -> None:
    from rdkit import Chem

    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    mol = Chem.AddHs(mol)
    symbols = [atom.GetSymbol() for atom in mol.GetAtoms()]
    groups = detect_equivalence_groups(symbols, mol=mol)
    assert groups.equivalence_unknown is False
    assert {g.basis for g in groups} == {EQ_BASIS_TOPOLOGY}
    assert sorted(i for g in groups for i in g) == list(range(len(symbols)))
    # CH3 hydrogens (3,4,5) are one group; CH2 hydrogens (6,7) another;
    # the two carbons stay distinct — graph symmetry, not element merge.
    group_of = {i: idx for idx, g in enumerate(groups) for i in g}
    assert len({group_of[i] for i in (3, 4, 5)}) == 1
    assert len({group_of[i] for i in (6, 7)}) == 1
    assert group_of[3] != group_of[6]
    assert group_of[0] != group_of[1]


def test_strict_mode_without_topology_raises_typed_error() -> None:
    with pytest.raises(EquivalenceError):
        detect_equivalence_groups(["C", "C", "O", "H", "H"], strict=True)


def test_strict_mode_with_topology_succeeds() -> None:
    from rdkit import Chem

    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    groups = detect_equivalence_groups(["C", "C", "O"], mol=mol, strict=True)
    assert groups.equivalence_unknown is False


def test_label_assignment_is_per_element_one_indexed() -> None:
    symbols = ["C", "H", "H", "C", "H"]
    labels = build_all_labels(symbols)
    assert labels == ["C1", "H1", "H2", "C2", "H3"]
    assert build_label_for_atom(3, symbols) == "C2"
    assert build_label_for_atom(4, symbols) == "H3"


def test_merge_explicit_takes_precedence() -> None:
    symbols = ["C", "H", "H", "H", "H"]
    detected = detect_equivalence_groups(symbols)
    merged = merge_explicit_and_detected([["H1", "H2"]], detected, symbols)
    # explicit group survives with basis "explicit"; the atoms it does not
    # cover stay singletons (old fallback re-merged them by element).
    groups = {tuple(g) for g in merged}
    assert (1, 2) in groups
    assert (0,) in groups and (3,) in groups and (4,) in groups
    explicit_group = next(g for g in merged if tuple(g) == (1, 2))
    assert explicit_group.basis == EQ_BASIS_EXPLICIT
    # all atoms covered
    all_idx = sorted(i for g in merged for i in g)
    assert all_idx == [0, 1, 2, 3, 4]


def test_merge_partial_explicit_never_element_merges_rest() -> None:
    # G01 partial-EQ failure mode: EQ covering only C1/C2 used to leave the
    # detected element group [0,1,2,3] minus claimed = [2,3] MERGED. The
    # un-covered atoms must stay singletons with unknown basis.
    symbols = ["C", "C", "C", "C"]
    detected = detect_equivalence_groups(symbols)
    merged = merge_explicit_and_detected([["C1", "C2"]], detected, symbols)
    groups = {tuple(g) for g in merged}
    assert (0, 1) in groups
    assert (2,) in groups
    assert (3,) in groups
    assert merged.equivalence_unknown is True
    assert sorted(i for g in merged for i in g) == [0, 1, 2, 3]


def test_merge_with_topology_keeps_detected_basis() -> None:
    from rdkit import Chem

    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    mol = Chem.AddHs(mol)
    symbols = [atom.GetSymbol() for atom in mol.GetAtoms()]
    detected = detect_equivalence_groups(symbols, mol=mol)
    merged = merge_explicit_and_detected([["C1", "C2"]], detected, symbols)
    bases = {tuple(g): g.basis for g in merged}
    assert bases[(0, 1)] == EQ_BASIS_EXPLICIT
    assert EQ_BASIS_TOPOLOGY in set(bases.values())
    assert merged.equivalence_unknown is False
    assert sorted(i for g in merged for i in g) == list(range(len(symbols)))


def test_merge_no_explicit_returns_detected_unchanged() -> None:
    symbols = ["C", "H", "H"]
    detected = detect_equivalence_groups(symbols)
    merged = merge_explicit_and_detected([], detected, symbols)
    assert merged == detected
