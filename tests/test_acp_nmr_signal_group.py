"""SignalGroup contract through averaging → assignment → scaling → DP5 (G08, todo 34).

The gap: ``boltzmann_average_shieldings`` collapsed each equivalence group to
a representative label and ``_compute_candidate_dp5`` later re-read that
representative's raw shielding, so DP4 (averaged residual) and DP5
(per-conformer reconstruction) consumed different signal definitions.
These tests lock the replacement contract:

* an averaged shift carries the FULL group membership (uids + coefficients +
  basis), not just the representative label;
* DP4 residual rows and DP5-style reconstruction from the same ``SignalGroup``
  produce the same signal value even when members' single-conformer shieldings
  are unequal;
* swapping the representative member/label leaves every result unchanged.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from acp.nmr.assignment import collect_residual_inputs, match_assigned
from acp.nmr.averaging import boltzmann_average_shieldings
from acp.nmr.equivalence import (
    EQ_BASIS_EXPLICIT,
    EQ_BASIS_TOPOLOGY,
    EQ_BASIS_UNKNOWN,
    EquivalenceGroup,
    EquivalenceResult,
)
from acp.nmr.io import parse_experimental_nmr
from acp.nmr.models import (
    SIGNAL_GROUP_BASES,
    Assignment,
    AtomShift,
    CandidateResult,
    ConformerShielding,
    NmrConfig,
    SignalGroup,
)
from acp.nmr.scaling import build_assignments, fit_scaling_goodman

# ---------------------------------------------------------------------------
# fixtures: explicit EQ group over two CARBONS with UNEQUAL shieldings
# ---------------------------------------------------------------------------

SYMBOLS = ["C", "H", "C", "H"]  # labels C1, H1, C2, H2
C_ISO_REPRESENTATIVE = 100.0  # atom 0 (the representative / min index)
C_ISO_PARTNER = 106.0  # atom 2
C_GROUP_MEAN = (C_ISO_REPRESENTATIVE + C_ISO_PARTNER) / 2.0  # 103.0
H1_ISO = 25.0
H2_ISO = 26.0


def _conformer() -> ConformerShielding:
    return ConformerShielding(
        "conf_0",
        1.0,
        {
            0: {"symbol": "C", "isotropic": C_ISO_REPRESENTATIVE},
            1: {"symbol": "H", "isotropic": H1_ISO},
            2: {"symbol": "C", "isotropic": C_ISO_PARTNER},
            3: {"symbol": "H", "isotropic": H2_ISO},
        },
    )


def _eq_carbon_members(members: tuple[int, ...] = (0, 2)) -> EquivalenceResult:
    return EquivalenceResult(
        (
            EquivalenceGroup(members, EQ_BASIS_EXPLICIT),
            EquivalenceGroup((1,), EQ_BASIS_TOPOLOGY),
            EquivalenceGroup((3,), EQ_BASIS_TOPOLOGY),
        )
    )


def _shift_for(shielding: float, nucleus: str, config: NmrConfig) -> float:
    """Independent reference: Goodman TMS conversion (NMR.py:392)."""
    ref = float(config.tms_for(nucleus))
    return (ref - shielding) / (1.0 - ref / 1e6)


def _average(members: tuple[int, ...] = (0, 2)) -> list[AtomShift]:
    return boltzmann_average_shieldings(
        [_conformer()],
        SYMBOLS,
        NmrConfig(),
        equivalence_groups=_eq_carbon_members(members),
    )


def _carbon_shift(shifts: list[AtomShift]) -> AtomShift:
    matches = [s for s in shifts if s.symbol == "C"]
    assert len(matches) == 1
    return matches[0]


# ---------------------------------------------------------------------------
# (a) full membership + one shared signal definition for DP4 and DP5
# ---------------------------------------------------------------------------


def test_explicit_eq_group_carries_full_membership_not_only_representative() -> None:
    shift = _carbon_shift(_average())
    group = shift.signal_group
    assert group is not None
    # membership uids (element label fallback: no structure map passed)
    assert group.atom_uids == ("C1", "C2")
    # equal averaging coefficients, one per member
    assert group.coefficients == pytest.approx((0.5, 0.5))
    assert sum(group.coefficients) == pytest.approx(1.0)
    assert group.equivalence_basis == EQ_BASIS_EXPLICIT
    # unknown is never fabricated
    assert group.peak_capacity is None
    assert group.experiment_ref is None
    # the emitted shift IS the group mean over UNEQUAL member shieldings —
    # not the representative atom's raw value (100.0)
    assert shift.shielding_ppm == pytest.approx(C_GROUP_MEAN)
    assert shift.shift_ppm == pytest.approx(_shift_for(C_GROUP_MEAN, "13C", NmrConfig()))
    assert shift.shielding_ppm != pytest.approx(C_ISO_REPRESENTATIVE)


def test_singleton_groups_keep_their_equivalence_basis() -> None:
    shifts = _average()
    by_index = {s.atom_index: s for s in shifts}
    assert by_index[1].signal_group is not None
    assert by_index[1].signal_group.atom_uids == ("H1",)
    assert by_index[1].signal_group.coefficients == (1.0,)
    assert by_index[1].signal_group.equivalence_basis == EQ_BASIS_TOPOLOGY
    assert by_index[3].signal_group.atom_uids == ("H2",)


def test_dp4_and_dp5_share_one_signal_definition_per_group() -> None:
    shifts = _average()
    carbon = _carbon_shift(shifts)
    group = carbon.signal_group
    assert group is not None

    exp = parse_experimental_nmr("C: 85.0(C1)\nH: 7.0(H1), 6.5(H2)")
    assign_result = match_assigned(shifts, exp)
    arrays = collect_residual_inputs(assign_result.pairs)

    # ONE row per group — two members do not produce two DP4/DP5 rows
    assert arrays["13C"]["labels"] == ["C1"]
    assert arrays["13C"]["signal_groups"] == [group]

    reg, scaled, residuals = fit_scaling_goodman(arrays["13C"]["calc"], arrays["13C"]["exp"], "13C")
    assert len(scaled) == 1 and len(residuals) == 1
    assignments = build_assignments(
        arrays["13C"]["labels"],
        arrays["13C"]["elements"],
        arrays["13C"]["exp"],
        arrays["13C"]["calc"],
        scaled,
        residuals,
        signal_groups=arrays["13C"]["signal_groups"],
    )
    assert len(assignments) == 1
    assignment = assignments[0]
    # the Assignment used for the DP4 residual carries the group identity
    assert assignment.signal_group == group

    candidate = CandidateResult(
        index=0, label="cand_0", atom_shifts=shifts, assignments=assignments
    )
    # _compute_candidate_dp5 consumes this candidate — the group is part of
    # its inputs, so T35 can reconstruct instead of reversing the label.
    assert candidate.signal_groups_used() == (group,)

    # DP4 path: one residual row from the averaged group signal
    dp4_rows = [a.residual for a in candidate.assignments if a.element == "C"]
    assert len(dp4_rows) == 1

    # DP5 path: per-conformer reconstruction FROM THE SAME GROUP membership
    # reproduces the exact same signal value the DP4 residual was built on.
    conf = _conformer()
    from acp.nmr.equivalence import build_all_labels

    label_to_idx = {label: i for i, label in enumerate(build_all_labels(SYMBOLS))}
    resolved = [label_to_idx[uid] for uid in group.atom_uids]
    reconstructed = sum(
        coeff * float(conf.shieldings[idx]["isotropic"])
        for coeff, idx in zip(group.coefficients, resolved, strict=True)
    )
    assert reconstructed == pytest.approx(carbon.shielding_ppm)
    assert reconstructed == pytest.approx(C_GROUP_MEAN)
    assert reconstructed != pytest.approx(C_ISO_REPRESENTATIVE)


def test_atom_uids_use_structure_map_when_available() -> None:
    from rdkit import Chem

    from acp.nmr.structure_map import NmrStructureMap

    mol = Chem.MolFromSmiles("CCO")  # atoms: C, C, O (no explicit H)
    structure_map = NmrStructureMap.from_mol(mol)
    symbols = list(structure_map.elements)
    conformer = ConformerShielding(
        "conf_0",
        1.0,
        {
            0: {"symbol": "C", "isotropic": 100.0},
            1: {"symbol": "C", "isotropic": 106.0},
            2: {"symbol": "O", "isotropic": 10.0},
        },
    )
    groups = EquivalenceResult(
        (EquivalenceGroup((0, 1), EQ_BASIS_EXPLICIT), EquivalenceGroup((2,), EQ_BASIS_TOPOLOGY))
    )
    shifts = boltzmann_average_shieldings(
        [conformer], symbols, NmrConfig(), equivalence_groups=groups, structure_map=structure_map
    )
    carbon = _carbon_shift(shifts)
    assert carbon.signal_group is not None
    assert carbon.signal_group.atom_uids == (
        structure_map.atom_uid_for_mol(0),
        structure_map.atom_uid_for_mol(1),
    )


# ---------------------------------------------------------------------------
# (b) representative swap leaves every result unchanged
# ---------------------------------------------------------------------------


def test_reordered_group_members_leave_results_unchanged() -> None:
    canonical = _average((0, 2))
    reordered = _average((2, 0))
    assert [s.as_dict() for s in canonical] == [s.as_dict() for s in reordered]
    assert _carbon_shift(canonical).signal_group == _carbon_shift(reordered).signal_group


def test_reordered_group_members_leave_downstream_residuals_unchanged() -> None:
    exp = parse_experimental_nmr("C: 85.0(C1)\nH: 7.0(H1), 6.5(H2)")
    canonical = collect_residual_inputs(match_assigned(_average((0, 2)), exp).pairs)
    reordered = collect_residual_inputs(match_assigned(_average((2, 0)), exp).pairs)
    assert canonical["13C"]["calc"] == reordered["13C"]["calc"]
    assert canonical["13C"]["exp"] == reordered["13C"]["exp"]
    assert canonical["13C"]["signal_groups"] == reordered["13C"]["signal_groups"]


def test_swapping_the_representative_atom_keeps_the_signal_definition() -> None:
    """Same group, same numerical signal — only the representative changes."""
    group = SignalGroup(
        atom_uids=("C1", "C2"), coefficients=(0.5, 0.5), equivalence_basis=EQ_BASIS_EXPLICIT
    )
    base = AtomShift(0, "C", "13C", C_GROUP_MEAN, 85.0, "C1", signal_group=group)
    swapped = replace(base, atom_index=2, atom_label="C2")
    assert swapped.signal_group == group
    assert swapped.shielding_ppm == base.shielding_ppm
    assert swapped.shift_ppm == base.shift_ppm

    # unlabeled-spectrum path: Hungarian matching of the two representatives
    # produces identical residual rows (calc/exp) and group identity
    h1 = AtomShift(1, "H", "1H", H1_ISO, 7.0, "H1")
    h2 = AtomShift(3, "H", "1H", H2_ISO, 6.0, "H2")
    exp = parse_experimental_nmr("C: 85.0\nH: 7.0, 6.0")
    arrays_base = collect_residual_inputs(match_assigned([base, h1, h2], exp).pairs)
    arrays_swap = collect_residual_inputs(match_assigned([swapped, h1, h2], exp).pairs)
    assert arrays_base["13C"]["calc"] == arrays_swap["13C"]["calc"]
    assert arrays_base["13C"]["exp"] == arrays_swap["13C"]["exp"]
    assert arrays_base["13C"]["signal_groups"] == [group]

    # labeled-spectrum path: a lock on the representative label yields the
    # same residual and the same group for either representative atom
    labeled = parse_experimental_nmr("C: 85.0(C1)")
    relock = replace(swapped, atom_label="C1")
    pair_base = match_assigned([base], labeled).pairs["13C"][0][0]
    pair_relock = match_assigned([relock], labeled).pairs["13C"][0][0]
    assert pair_base.shift_ppm == pair_relock.shift_ppm
    assert pair_base.signal_group == pair_relock.signal_group


# ---------------------------------------------------------------------------
# (c) JSON-safe round-trip
# ---------------------------------------------------------------------------


def test_signal_group_as_dict_round_trip_is_json_safe() -> None:
    group = SignalGroup(
        atom_uids=("C:3", "C:7"),
        coefficients=(0.25, 0.75),
        equivalence_basis=EQ_BASIS_EXPLICIT,
        peak_capacity=2,
        experiment_ref="13C:0",
    )
    payload = json.loads(json.dumps(group.as_dict()))
    assert payload == {
        "atom_uids": ["C:3", "C:7"],
        "coefficients": [0.25, 0.75],
        "equivalence_basis": "explicit",
        "peak_capacity": 2,
        "experiment_ref": "13C:0",
    }
    assert SignalGroup(**payload) == group


def test_atom_shift_and_assignment_serialize_the_group() -> None:
    group = SignalGroup(
        atom_uids=("C1", "C2"), coefficients=(0.5, 0.5), equivalence_basis=EQ_BASIS_EXPLICIT
    )
    shift = AtomShift(0, "C", "13C", C_GROUP_MEAN, 85.0, "C1", signal_group=group)
    assert shift.as_dict()["signal_group"] == group.as_dict()
    assignment = Assignment(
        atom_label="C1",
        element="C",
        exp_ppm=85.0,
        calc_ppm=85.1,
        scaled_ppm=85.0,
        residual=0.0,
        signal_group=group,
    )
    assert assignment.as_dict()["signal_group"] == group.as_dict()


# ---------------------------------------------------------------------------
# (d) validation
# ---------------------------------------------------------------------------


def test_signal_group_basis_vocabulary_matches_equivalence_module() -> None:
    # one definition, no drift: the tuple is what equivalence validates against
    assert set(SIGNAL_GROUP_BASES) == {
        EQ_BASIS_TOPOLOGY,
        EQ_BASIS_EXPLICIT,
        EQ_BASIS_UNKNOWN,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"atom_uids": (), "coefficients": (1.0,), "equivalence_basis": EQ_BASIS_EXPLICIT},
        {"atom_uids": ("C1",), "coefficients": (), "equivalence_basis": EQ_BASIS_EXPLICIT},
        {"atom_uids": ("C1", "C2"), "coefficients": (1.0,), "equivalence_basis": EQ_BASIS_EXPLICIT},
        {
            "atom_uids": ("C1",),
            "coefficients": (float("nan"),),
            "equivalence_basis": EQ_BASIS_EXPLICIT,
        },
        {
            "atom_uids": ("C1",),
            "coefficients": (float("inf"),),
            "equivalence_basis": EQ_BASIS_EXPLICIT,
        },
        {
            "atom_uids": ("C1", "C1"),
            "coefficients": (0.5, 0.5),
            "equivalence_basis": EQ_BASIS_EXPLICIT,
        },
        {"atom_uids": ("C1",), "coefficients": (1.0,), "equivalence_basis": "guessed"},
        {"atom_uids": ("",), "coefficients": (1.0,), "equivalence_basis": EQ_BASIS_EXPLICIT},
        {"atom_uids": (1,), "coefficients": (1.0,), "equivalence_basis": EQ_BASIS_EXPLICIT},
        {
            "atom_uids": ("C1",),
            "coefficients": (1.0,),
            "equivalence_basis": EQ_BASIS_EXPLICIT,
            "peak_capacity": 0,
        },
        {
            "atom_uids": ("C1",),
            "coefficients": (1.0,),
            "equivalence_basis": EQ_BASIS_EXPLICIT,
            "experiment_ref": "  ",
        },
    ],
)
def test_signal_group_validation_rejects_malformed_input(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SignalGroup(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# threading through assignment/scaling + CandidateResult record
# ---------------------------------------------------------------------------


def test_collect_residual_inputs_exposes_groups_with_none_for_legacy_shifts() -> None:
    legacy = [
        AtomShift(0, "C", "13C", 170.0, 20.5, "C1"),
        AtomShift(1, "C", "13C", 180.0, 10.4, "C2"),
    ]
    exp = parse_experimental_nmr("C: 20.5(C1), 10.4(C2)")
    arrays = collect_residual_inputs(match_assigned(legacy, exp).pairs)
    assert arrays["13C"]["signal_groups"] == [None, None]


def test_build_assignments_rejects_mismatched_signal_groups() -> None:
    with pytest.raises(ValueError, match="signal_groups length mismatch"):
        build_assignments(["C1"], ["C"], [10.0], [11.0], [10.5], [-0.5], signal_groups=[])


def test_candidate_signal_groups_used_prefers_record_and_derives_otherwise() -> None:
    group = SignalGroup(
        atom_uids=("C1", "C2"), coefficients=(0.5, 0.5), equivalence_basis=EQ_BASIS_EXPLICIT
    )
    assignment = Assignment(
        atom_label="C1",
        element="C",
        exp_ppm=85.0,
        calc_ppm=85.1,
        scaled_ppm=85.0,
        residual=0.0,
        signal_group=group,
    )
    derived = CandidateResult(index=0, label="derived", assignments=[assignment])
    assert derived.signal_groups_used() == (group,)

    explicit = CandidateResult(index=1, label="explicit", signal_groups=(group,))
    assert explicit.signal_groups_used() == (group,)

    empty = CandidateResult(index=2, label="empty")
    assert empty.signal_groups_used() == ()
    assert empty.as_dict()["signal_groups"] == []


def test_nmr_package_exports_signal_group() -> None:
    import acp.nmr

    assert acp.nmr.SignalGroup is SignalGroup
    assert acp.nmr.SIGNAL_GROUP_BASES == SIGNAL_GROUP_BASES
