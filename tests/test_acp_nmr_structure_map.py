"""Tests for stable atom identity + label-scheme mapping (gap G03)."""

from __future__ import annotations

import random
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from acp.nmr.equivalence import build_all_labels
from acp.nmr.structure_map import (
    LABEL_SCHEME_GOODMAN,
    LABEL_SCHEME_PER_ELEMENT,
    AtomIdentity,
    NmrStructureMap,
    StructureMapError,
    parse_ambiguous_labels,
)

ETHANOL_SMILES = "CCO"
MIXED_ELEMENTS = ["C", "O", "C", "H"]


def _ethanol_mol() -> Chem.Mol:
    mol = Chem.AddHs(Chem.MolFromSmiles(ETHANOL_SMILES))
    assert AllChem.EmbedMolecule(mol, randomSeed=0xF00D) == 0
    return mol


def _write_sdf(mol: Chem.Mol, path: Path) -> None:
    writer = Chem.SDWriter(str(path))
    writer.write(mol)
    writer.close()


def _read_sdf(path: Path) -> Chem.Mol:
    mol = Chem.MolFromMolFile(str(path), removeHs=False)
    assert mol is not None
    return mol


# ---------------------------------------------------------------------------
# Round-trip: source index ↔ mol index ↔ rank ↔ labels ↔ uid
# ---------------------------------------------------------------------------


def test_round_trip_identity_provenance() -> None:
    m = NmrStructureMap.from_elements(["C", "H", "H", "C", "H"])
    assert len(m) == 5
    for source in range(len(m)):
        assert m.source_index_for_mol(m.mol_index_for_source(source)) == source
        rank = m.canonical_rank_for_source(source)
        assert m.source_index_for_rank(rank) == source
        uid = m.atom_uid_for_source(source)
        assert m.source_index_for_atom_uid(uid) == source
        assert m.mol_index_for_atom_uid(uid) == m.mol_index_for_source(source)


def test_round_trip_both_label_schemes() -> None:
    m = NmrStructureMap.from_elements(MIXED_ELEMENTS)
    for scheme in (LABEL_SCHEME_PER_ELEMENT, LABEL_SCHEME_GOODMAN):
        labels = m.labels_by_source(scheme)
        assert len(labels) == len(m)
        for source, label in enumerate(labels):
            assert m.source_index_for_label(label, scheme) == source
            assert m.label_for_source(source, scheme) == label


def test_identity_fields_and_frozen() -> None:
    m = NmrStructureMap.from_elements(MIXED_ELEMENTS)
    identity = m.identity_for_source(0, LABEL_SCHEME_GOODMAN)
    assert isinstance(identity, AtomIdentity)
    assert identity.atom_uid == m.atom_uid_for_source(0)
    assert identity.source_atom_index == 0
    assert identity.element == "C"
    assert identity.label == "C1"
    assert identity.label_scheme == LABEL_SCHEME_GOODMAN
    with pytest.raises(FrozenInstanceError):
        identity.label = "C9"  # type: ignore[misc]


def test_identities_cover_all_atoms_in_source_order() -> None:
    m = NmrStructureMap.from_elements(MIXED_ELEMENTS)
    identities = m.identities(LABEL_SCHEME_PER_ELEMENT)
    assert [i.source_atom_index for i in identities] == [0, 1, 2, 3]
    assert [i.label for i in identities] == ["C1", "O1", "C2", "H1"]
    assert len({i.atom_uid for i in identities}) == len(m)


# ---------------------------------------------------------------------------
# Label schemes
# ---------------------------------------------------------------------------


def test_goodman_scheme_uses_whole_sequence_ordinal() -> None:
    # G03: for ['C','O','C','H'] Goodman numbers the second carbon C3, not C2.
    m = NmrStructureMap.from_elements(MIXED_ELEMENTS)
    assert m.labels_by_source(LABEL_SCHEME_GOODMAN) == ("C1", "O2", "C3", "H4")
    assert m.labels_by_source(LABEL_SCHEME_PER_ELEMENT) == ("C1", "O1", "C2", "H1")


def test_per_element_scheme_matches_equivalence_labels() -> None:
    symbols = ["C", "H", "H", "C", "H"]
    m = NmrStructureMap.from_elements(symbols)
    assert m.labels_by_source(LABEL_SCHEME_PER_ELEMENT) == tuple(build_all_labels(symbols))


def test_unknown_scheme_rejected() -> None:
    m = NmrStructureMap.from_elements(["C"])
    with pytest.raises(StructureMapError, match="unknown label scheme"):
        m.label_for_source(0, "murphy")
    with pytest.raises(StructureMapError, match="unknown label scheme"):
        m.source_index_for_label("C1", "murphy")


# ---------------------------------------------------------------------------
# Shuffle: same SDF with atoms reordered rebuilds the same mapping
# ---------------------------------------------------------------------------


def test_shuffled_sdf_rebuilds_same_uid_element_mapping(tmp_path: Path) -> None:
    mol = _ethanol_mol()
    orig_path = tmp_path / "ethanol.sdf"
    _write_sdf(mol, orig_path)
    original = _read_sdf(orig_path)
    base = NmrStructureMap.from_mol(original)

    order = list(range(original.GetNumAtoms()))
    random.Random(4).shuffle(order)
    shuffled = Chem.RenumberAtoms(original, order)
    shuffled_path = tmp_path / "ethanol_shuffled.sdf"
    _write_sdf(shuffled, shuffled_path)
    shuffled_mol = _read_sdf(shuffled_path)

    rebuilt = NmrStructureMap.from_mol(shuffled_mol, source_atom_indices=order)
    no_provenance = NmrStructureMap.from_mol(shuffled_mol)

    def uid_to_element(map_: NmrStructureMap) -> dict[str, str]:
        return {
            map_.atom_uid_for_source(s): map_.elements[map_.mol_index_for_source(s)]
            for s in range(len(map_))
        }

    # provenance rebuild: identical uid→element binding
    assert uid_to_element(rebuilt) == uid_to_element(base)
    # even without provenance the uid→element map survives reordering
    assert uid_to_element(no_provenance) == uid_to_element(base)
    # uid set is stable
    assert set(rebuilt.atom_uids) == set(base.atom_uids)


def test_shuffled_sdf_labels_follow_source_provenance(tmp_path: Path) -> None:
    mol = _ethanol_mol()
    orig_path = tmp_path / "ethanol.sdf"
    _write_sdf(mol, orig_path)
    original = _read_sdf(orig_path)
    base = NmrStructureMap.from_mol(original)

    order = list(range(original.GetNumAtoms()))
    random.Random(7).shuffle(order)
    shuffled = Chem.RenumberAtoms(original, order)
    shuffled_path = tmp_path / "ethanol_shuffled.sdf"
    _write_sdf(shuffled, shuffled_path)
    shuffled_mol = _read_sdf(shuffled_path)
    rebuilt = NmrStructureMap.from_mol(shuffled_mol, source_atom_indices=order)

    for scheme in (LABEL_SCHEME_PER_ELEMENT, LABEL_SCHEME_GOODMAN):
        uid_to_label = {
            rebuilt.atom_uid_for_source(s): rebuilt.label_for_source(s, scheme)
            for s in range(len(rebuilt))
        }
        base_uid_to_label = {
            base.atom_uid_for_source(s): base.label_for_source(s, scheme) for s in range(len(base))
        }
        assert uid_to_label == base_uid_to_label

    # mol/QC index of each source atom differs after the shuffle, but the
    # provenance round-trip still recovers the original numbering
    for s in range(len(rebuilt)):
        assert rebuilt.source_index_for_mol(rebuilt.mol_index_for_source(s)) == s


def test_shuffled_sdf_uid_to_shift_mapping_consistent_across_schemes(
    tmp_path: Path,
) -> None:
    mol = _ethanol_mol()
    orig_path = tmp_path / "ethanol.sdf"
    _write_sdf(mol, orig_path)
    original = _read_sdf(orig_path)
    base = NmrStructureMap.from_mol(original)

    order = list(range(original.GetNumAtoms()))
    random.Random(11).shuffle(order)
    shuffled = Chem.RenumberAtoms(original, order)
    shuffled_path = tmp_path / "ethanol_shuffled.sdf"
    _write_sdf(shuffled, shuffled_path)
    shuffled_mol = _read_sdf(shuffled_path)
    rebuilt = NmrStructureMap.from_mol(shuffled_mol, source_atom_indices=order)

    # synthetic per-atom shieldings, one value per physical atom
    shifts_by_uid_base = {base.atom_uid_for_source(s): 100.0 + s * 0.5 for s in range(len(base))}
    shifts_by_uid_rebuilt = {
        rebuilt.atom_uid_for_source(s): 100.0 + s * 0.5 for s in range(len(rebuilt))
    }
    assert shifts_by_uid_rebuilt == shifts_by_uid_base

    # both schemes route label → source → uid → the same shift
    for scheme in (LABEL_SCHEME_PER_ELEMENT, LABEL_SCHEME_GOODMAN):
        via_scheme = {
            uid: shifts_by_uid_base[uid]
            for uid in (
                rebuilt.atom_uid_for_source(s)
                for s in (
                    rebuilt.source_index_for_label(label, scheme)
                    for label in rebuilt.labels_by_source(scheme)
                )
            )
        }
        assert via_scheme == shifts_by_uid_base


# ---------------------------------------------------------------------------
# Ambiguous label parsing
# ---------------------------------------------------------------------------


def test_parse_single_label_returns_one_tuple() -> None:
    assert parse_ambiguous_labels("H32") == ("H32",)


def test_parse_or_joined_ambiguity() -> None:
    assert parse_ambiguous_labels("H32 or H33") == ("H32", "H33")
    assert parse_ambiguous_labels("C1 or C2 or C3") == ("C1", "C2", "C3")


def test_parse_comma_separated_labels() -> None:
    assert parse_ambiguous_labels("H1, H2") == ("H1", "H2")


@pytest.mark.parametrize(
    "garbage",
    ["", "   ", "or", "H32 or", "or H32", "H32 or xyz", "32H", "H", "H32 or 33"],
)
def test_parse_rejects_garbage(garbage: str) -> None:
    with pytest.raises(StructureMapError):
        parse_ambiguous_labels(garbage)


def test_resolve_labels_maps_ambiguity_to_source_indices() -> None:
    m = NmrStructureMap.from_elements(["C", "H", "H", "H"])
    assert m.resolve_labels("H2 or H3", LABEL_SCHEME_PER_ELEMENT) == (2, 3)
    assert m.resolve_labels(["H1", "H3"], LABEL_SCHEME_PER_ELEMENT) == (1, 3)


def test_resolve_labels_never_drops_unresolvable_candidates() -> None:
    m = NmrStructureMap.from_elements(["C", "H"])
    with pytest.raises(StructureMapError, match="missing label"):
        m.resolve_labels("H1 or H9")


# ---------------------------------------------------------------------------
# Typed errors: unknown element / rank out of range / missing labels
# ---------------------------------------------------------------------------


def test_unknown_element_prefix_rejected() -> None:
    m = NmrStructureMap.from_elements(["C", "H"])
    with pytest.raises(StructureMapError, match="unknown element prefix"):
        m.source_index_for_label("F1")


def test_rank_out_of_range_rejected() -> None:
    m = NmrStructureMap.from_elements(["C", "H"])
    with pytest.raises(StructureMapError, match="canonical rank"):
        m.source_index_for_rank(42)
    with pytest.raises(StructureMapError, match="canonical rank"):
        m.source_index_for_rank(-1)


def test_missing_label_rejected() -> None:
    m = NmrStructureMap.from_elements(["C", "H"])
    with pytest.raises(StructureMapError, match="missing label"):
        m.source_index_for_label("C9")
    with pytest.raises(StructureMapError, match="missing label"):
        m.source_index_for_label("H7", LABEL_SCHEME_GOODMAN)


def test_malformed_label_rejected() -> None:
    m = NmrStructureMap.from_elements(["C"])
    with pytest.raises(StructureMapError, match="malformed label"):
        m.source_index_for_label("banana")


def test_unknown_uid_rejected() -> None:
    m = NmrStructureMap.from_elements(["C"])
    with pytest.raises(StructureMapError, match="unknown atom_uid"):
        m.mol_index_for_atom_uid("Xe:99")


def test_index_out_of_range_rejected() -> None:
    m = NmrStructureMap.from_elements(["C", "H"])
    with pytest.raises(StructureMapError, match="out of range"):
        m.source_index_for_mol(7)
    with pytest.raises(StructureMapError, match="out of range"):
        m.mol_index_for_source(-1)


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


def test_from_elements_validates_provenance_permutation() -> None:
    with pytest.raises(StructureMapError, match="permutation"):
        NmrStructureMap.from_elements(["C", "H"], source_atom_indices=[0, 0])
    with pytest.raises(StructureMapError, match="permutation"):
        NmrStructureMap.from_elements(["C", "H"], source_atom_indices=[0, 2])


def test_from_elements_validates_ranks() -> None:
    with pytest.raises(StructureMapError, match="unique"):
        NmrStructureMap.from_elements(["C", "H"], ranks=[0, 0])
    with pytest.raises(StructureMapError, match="non-negative"):
        NmrStructureMap.from_elements(["C", "H"], ranks=[0, -1])


def test_from_elements_rejects_empty_and_bad_elements() -> None:
    with pytest.raises(StructureMapError, match="empty"):
        NmrStructureMap.from_elements([])
    with pytest.raises(StructureMapError, match="invalid element"):
        NmrStructureMap.from_elements(["C", "12"])


def test_from_mol_builds_unique_uids_and_valid_permutation() -> None:
    mol = _ethanol_mol()
    m = NmrStructureMap.from_mol(mol)
    assert len(m.atom_uids) == mol.GetNumAtoms()
    assert len(set(m.atom_uids)) == mol.GetNumAtoms()
    assert sorted(m.source_atom_indices) == list(range(mol.GetNumAtoms()))
    assert m.elements == tuple(a.GetSymbol() for a in mol.GetAtoms())


def test_from_mol_rejects_bad_provenance() -> None:
    mol = Chem.MolFromSmiles("CCO")
    n = mol.GetNumAtoms()
    with pytest.raises(StructureMapError, match="permutation"):
        NmrStructureMap.from_mol(mol, source_atom_indices=[0] * n)


def test_uid_format_carries_element_and_rank() -> None:
    m = NmrStructureMap.from_elements(["C", "H"], ranks=[3, 0])
    uid0 = m.atom_uid_for_source(0)
    assert uid0.startswith("C:")
    assert m.element_for_atom_uid(uid0) == "C"
    assert m.element_for_atom_uid(m.atom_uid_for_source(1)) == "H"
