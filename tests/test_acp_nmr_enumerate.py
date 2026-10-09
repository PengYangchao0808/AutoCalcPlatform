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


# ---------------------------------------------------------------------------
# todo 9: labels via the stable atom map, charge preservation
# ---------------------------------------------------------------------------


def _embedded_mol(smiles: str, seed: int = 0xF00D):
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=seed) == 0
    return Chem, mol


def test_shuffled_candidate_resolves_labels_like_source_order(tmp_path: Path) -> None:
    """OMIT/EQ labels resolve through NmrStructureMap provenance, not ordinals."""
    pytest.importorskip("rdkit")
    from rdkit import Chem

    from acp.nmr.io import parse_experimental_nmr
    from acp.nmr.structure_map import NmrStructureMap
    from acp.workflows.nmr import _explicit_eq_to_indices, _omit_atom_indices, _parse_candidates

    chem, mol = _embedded_mol("CC(=O)OC")
    orig_sdf = tmp_path / "orig.sdf"
    writer = chem.SDWriter(str(orig_sdf))
    writer.write(mol)
    writer.close()

    order = list(range(mol.GetNumAtoms()))
    # deterministic shuffle that moves the first carbon off position 0
    order[0], order[-1] = order[-1], order[0]
    order[2], order[5] = order[5], order[2]
    shuffled = Chem.RenumberAtoms(mol, order)
    shuf_sdf = tmp_path / "shuf.sdf"
    writer = chem.SDWriter(str(shuf_sdf))
    writer.write(shuffled)
    writer.close()

    orig = _parse_candidates([str(orig_sdf)], None, None)[0]
    shuf = _parse_candidates([str(shuf_sdf)], None, None)[0]
    provenance_map = NmrStructureMap.from_mol(shuffled, source_atom_indices=order)
    shuf.metadata["nmr_structure_map"] = {
        "elements": list(provenance_map.elements),
        "source_atom_indices": list(provenance_map.source_atom_indices),
        "canonical_ranks": list(provenance_map.canonical_ranks),
    }
    shuf.metadata["nmr_topology_source"] = "sdf"

    exp = parse_experimental_nmr(
        "C: 40.0(C1), 175.0(C2), 52.0(C3)\n"
        "H: 3.7(H1), 3.6(H2), 2.1(H3), 2.0(H4), 2.1(H5), 2.0(H6)\n"
        "EQ: H3,H4\n"
        "OMIT: H6\n"
    )

    orig_map = NmrStructureMap.from_mol(mol)
    # source-space uid for each label, resolved through each candidate's own map
    for label in ("C1", "C2", "C3", "H3", "H4", "H6"):
        source_idx = orig_map.source_index_for_label(label)
        expected_uid = orig_map.atom_uid_for_source(source_idx)
        shuf_idx = provenance_map.mol_index_for_source(provenance_map.source_index_for_label(label))
        assert provenance_map.atom_uid_for_mol(shuf_idx) == expected_uid, label

    # EQ groups: same physical atoms (uid set) in both candidates
    orig_groups = _explicit_eq_to_indices(exp, orig)
    shuf_groups = _explicit_eq_to_indices(exp, shuf)
    assert orig_groups and shuf_groups
    for orig_grp, shuf_grp in zip(orig_groups, shuf_groups):
        assert {orig_map.atom_uid_for_mol(i) for i in orig_grp} == {
            provenance_map.atom_uid_for_mol(i) for i in shuf_grp
        }

    # OMIT: same physical atom
    orig_omit = _omit_atom_indices(exp, orig)
    shuf_omit = _omit_atom_indices(exp, shuf)
    assert len(orig_omit) == len(shuf_omit) == 1
    assert orig_map.atom_uid_for_mol(orig_omit[0]) == provenance_map.atom_uid_for_mol(shuf_omit[0])

    # old-style ordinal resolution over the shuffled symbol order picks the
    # wrong physical atom (regression anchor for the map-based resolution)
    from acp.nmr.equivalence import _build_label_index

    shuffled_symbols = list(shuf.symbols)
    old_idx = _build_label_index(shuffled_symbols)["C1"]
    assert orig_map.atom_uid_for_mol(old_idx) != orig_map.atom_uid_for_source(
        orig_map.source_index_for_label("C1")
    )


def test_shuffled_candidate_spectrum_gives_identical_pairs_and_dp4(tmp_path: Path) -> None:
    """Shuffling atom order + map relabeling reproduces pairs and DP4."""
    pytest.importorskip("rdkit")
    from rdkit import Chem

    from acp.nmr.assignment import collect_residual_inputs, match_assigned
    from acp.nmr.averaging import boltzmann_average_shieldings
    from acp.nmr.error_model import load_error_model
    from acp.nmr.io import parse_experimental_nmr
    from acp.nmr.models import ConformerShielding, NmrConfig
    from acp.nmr.probability import compute_dp4
    from acp.nmr.scaling import fit_scaling_goodman
    from acp.nmr.structure_map import NmrStructureMap
    from acp.workflows.nmr import (
        _explicit_eq_to_indices,
        _omit_atom_indices,
        _parse_candidates,
        _relabel_shifts_with_map,
    )

    chem, mol = _embedded_mol("CC(=O)OC")
    source_symbols = [a.GetSymbol() for a in mol.GetAtoms()]
    orig_sdf = tmp_path / "orig.sdf"
    writer = chem.SDWriter(str(orig_sdf))
    writer.write(mol)
    writer.close()

    order = list(range(mol.GetNumAtoms()))
    order[0], order[-1] = order[-1], order[0]
    order[2], order[5] = order[5], order[2]
    shuffled = Chem.RenumberAtoms(mol, order)
    shuf_sdf = tmp_path / "shuf.sdf"
    writer = chem.SDWriter(str(shuf_sdf))
    writer.write(shuffled)
    writer.close()

    orig = _parse_candidates([str(orig_sdf)], None, None)[0]
    shuf = _parse_candidates([str(shuf_sdf)], None, None)[0]
    provenance_map = NmrStructureMap.from_mol(shuffled, source_atom_indices=order)
    shuf.metadata["nmr_structure_map"] = {
        "elements": list(provenance_map.elements),
        "source_atom_indices": list(provenance_map.source_atom_indices),
        "canonical_ranks": list(provenance_map.canonical_ranks),
    }
    shuf.metadata["nmr_topology_source"] = "sdf"
    orig_map = NmrStructureMap.from_mol(mol)

    exp = parse_experimental_nmr(
        "C: 40.0(C1), 175.0(C2), 52.0(C3)\n"
        "H: 3.7(H1), 3.6(H2), 2.1(H3), 2.0(H4), 2.1(H5), 2.0(H6)\n"
        "EQ: H3,H4\n"
        "OMIT: H6\n"
    )

    # per-SOURCE shielding values; each candidate indexes by its own atom order
    shielding_by_source: dict[int, dict[str, object]] = {}
    for src, sym in enumerate(source_symbols):
        if sym == "C":
            shielding_by_source[src] = {
                "symbol": sym,
                "isotropic": 188.452125 - (40.0 + 50.0 * src),
            }
        elif sym == "H":
            shielding_by_source[src] = {
                "symbol": sym,
                "isotropic": 32.1243166667 - (1.0 + 0.3 * src),
            }
        else:
            shielding_by_source[src] = {"symbol": sym, "isotropic": 0.0}
    sh_orig = {i: shielding_by_source[i] for i in range(len(source_symbols))}
    sh_shuf = {i: shielding_by_source[order[i]] for i in range(len(order))}

    cfg = NmrConfig()
    shifts_orig = boltzmann_average_shieldings(
        [ConformerShielding("c0", 1.0, sh_orig)],
        list(orig.symbols),
        cfg,
        equivalence_groups=_explicit_eq_to_indices(exp, orig),
        omit_atom_indices=_omit_atom_indices(exp, orig),
    )
    shifts_shuf = boltzmann_average_shieldings(
        [ConformerShielding("c0", 1.0, sh_shuf)],
        list(shuf.symbols),
        cfg,
        equivalence_groups=_explicit_eq_to_indices(exp, shuf),
        omit_atom_indices=_omit_atom_indices(exp, shuf),
    )
    shifts_orig = _relabel_shifts_with_map(shifts_orig, orig)
    shifts_shuf = _relabel_shifts_with_map(shifts_shuf, shuf)

    res_orig = match_assigned(shifts_orig, exp)
    res_shuf = match_assigned(shifts_shuf, exp)

    def pairs_key(result, structure_map):
        keyed = []
        for nucleus, group in result.pairs.items():
            for shift, peak in group:
                keyed.append(
                    (
                        nucleus,
                        round(peak.shift_ppm, 6),
                        structure_map.atom_uid_for_mol(shift.atom_index),
                        round(shift.shift_ppm, 6),
                    )
                )
        return sorted(keyed)

    assert pairs_key(res_orig, orig_map) == pairs_key(res_shuf, provenance_map)

    def dp4_of(result) -> float:
        residuals = {}
        for nucleus, arrays in collect_residual_inputs(result.pairs).items():
            _, _, res = fit_scaling_goodman(arrays["calc"], arrays["exp"], nucleus)
            residuals[nucleus] = res
        return compute_dp4(residuals, load_error_model("placeholder-student-t"))

    assert dp4_of(res_orig) == dp4_of(res_shuf)


def test_enumerate_input_keeps_auto_charge_none_and_candidate_charge() -> None:
    from acp.workflows.nmr import _enumerate_input

    result = _enumerate_input(["CC(O)C(O)C(=O)[O-]"], None, None, None)
    assert not isinstance(result, str)
    _sources, candidates, charge = result
    assert charge is None
    assert charge != 0
    assert candidates
    assert all(c.charge == -1 for c in candidates)
    # enumerated candidates carry topology provenance (T3 gap closed)
    assert all(c.metadata.get("nmr_structure_map") is not None for c in candidates)
    assert all(c.metadata.get("enumerated") is True for c in candidates)


def test_enumerate_input_keeps_explicit_charge() -> None:
    from acp.workflows.nmr import _enumerate_input

    result = _enumerate_input(["CC(=O)[O-]"], None, 1, None)
    assert not isinstance(result, str)
    _sources, candidates, charge = result
    assert charge == 1
    assert candidates
    assert all(c.charge == 1 for c in candidates)


def test_stereocenter_filter_resolves_via_structure_map() -> None:
    pytest.importorskip("rdkit")
    from rdkit import Chem

    from acp.nmr.enumerate import _resolve_stereocenter_labels
    from acp.nmr.structure_map import NmrStructureMap

    mol = Chem.MolFromSmiles("CC(Cl)C(Cl)C")
    Chem.SanitizeMol(mol)
    structure_map = NmrStructureMap.from_mol(mol)
    # C2 is a real tetrahedral centre in this molecule
    resolved = _resolve_stereocenter_labels(["C2"], mol, Chem)
    assert resolved == {structure_map.source_index_for_label("C2")}
    # token normalization stays: "C 2" / "c2" → same atom
    assert _resolve_stereocenter_labels(["C 2"], mol, Chem) == resolved
    assert _resolve_stereocenter_labels(["c2"], mol, Chem) == resolved
    # empty label list → empty set (no filter)
    assert _resolve_stereocenter_labels([], mol, Chem) == set()
    # unknown label never resolves, and the enumerator surfaces it
    assert _resolve_stereocenter_labels(["C99"], mol, Chem) == set()
    with pytest.raises(ValueError, match="matched no heavy atoms"):
        enumerate_candidates("CC(Cl)C(Cl)C", stereocenters="C99")


def test_stereocenter_filter_selects_only_the_mapped_center() -> None:
    cands = enumerate_candidates("CC(Cl)C(Cl)C", stereocenters="C2")
    assert 1 <= len(cands) <= 2
    assert all(c.enumerated_centers == 1 for c in cands)
