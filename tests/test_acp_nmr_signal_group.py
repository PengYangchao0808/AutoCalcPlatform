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

import numpy as np
import pytest

from acp.core.models import Structure
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


# ---------------------------------------------------------------------------
# (e) DP5 per-conformer reconstruction from SignalGroup membership (todo 35)
#
# BEFORE: ``_compute_candidate_dp5`` looked the representative label back up
# (``label_to_idx[a.atom_label]``) and read that atom's raw per-conformer
# shielding, so a multi-member EQ group fed DP5 a different signal than the
# group-averaged one DP4 was calibrated on (G08).
#
# AFTER: each conformer's signal is reconstructed as the coefficient-weighted
# mean of its group members' shieldings — the same linear definition
# ``boltzmann_average_shieldings`` used — so swapping the representative
# (same membership) leaves every DP5 probability unchanged. Unresolvable
# members/length mismatches are typed degradations (logged + fallback reason),
# never a representative substitution.
# ---------------------------------------------------------------------------


class _ReconstructionDP5Model:
    """DP5 stand-in returning the mean |reconstructed - exp| and recording inputs."""

    model_id = "goodman-dp5"

    def __init__(self) -> None:
        self.fchl_available = False
        self.conformer_shifts: list[list[float]] = []
        self.exp_shifts: list[float] = []
        self.weights: list[float] = []
        self.averaged_calls: list[list[float]] = []

    def probability(self, carbon_errors: list[float]) -> float:
        self.averaged_calls.append([float(error) for error in carbon_errors])
        return 0.5

    def probability_per_conformer(self, shifts, exp, weights) -> float:
        self.conformer_shifts = [list(row) for row in shifts]
        self.exp_shifts = [float(value) for value in exp]
        self.weights = [float(weight) for weight in weights]
        errors = [abs(s - e) for row in shifts for s, e in zip(row, exp, strict=True)]
        return sum(errors) / len(errors)


def _dp5_structure(*, with_map: bool = False) -> Structure:
    metadata: dict[str, object] = {}
    if with_map:
        metadata["nmr_structure_map"] = {
            "elements": list(SYMBOLS),
            "source_atom_indices": [0, 1, 2, 3],
            "canonical_ranks": [0, 1, 2, 3],
        }
    return Structure(
        id="cand",
        charge=0,
        multiplicity=1,
        symbols=list(SYMBOLS),
        coordinates=np.zeros((len(SYMBOLS), 3)),
        metadata=metadata,
    )


def _carbon_assignment(
    group: SignalGroup | None,
    *,
    atom_label: str = "C1",
    exp_ppm: float = 85.0,
) -> Assignment:
    return Assignment(
        atom_label=atom_label,
        element="C",
        exp_ppm=exp_ppm,
        calc_ppm=exp_ppm,
        scaled_ppm=exp_ppm,
        residual=0.0,
        signal_group=group,
    )


def _dp5_candidate(*assignments: Assignment) -> CandidateResult:
    return CandidateResult(
        index=0,
        label="cand",
        assignments=list(assignments),
        conformer_shieldings=[_conformer()],
    )


def _group(uids=("C1", "C2"), coefficients=(0.5, 0.5)) -> SignalGroup:
    return SignalGroup(
        atom_uids=uids, coefficients=coefficients, equivalence_basis=EQ_BASIS_EXPLICIT
    )


def test_dp5_reconstruction_uses_coefficient_weighted_group_members() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    group = _group(coefficients=(0.25, 0.75))
    candidate = _dp5_candidate(_carbon_assignment(group))
    model = _ReconstructionDP5Model()

    outcome = _compute_candidate_dp5(candidate, _dp5_structure(), NmrConfig(), model)

    weighted = 0.25 * C_ISO_REPRESENTATIVE + 0.75 * C_ISO_PARTNER
    expected_signal = _shift_for(weighted, "13C", NmrConfig())
    assert outcome.mode == "fallback"
    assert outcome.status == "valid"
    assert model.conformer_shifts == [[pytest.approx(expected_signal)]]
    # the representative's RAW shielding never reaches DP5
    representative_signal = _shift_for(C_ISO_REPRESENTATIVE, "13C", NmrConfig())
    assert model.conformer_shifts[0][0] != pytest.approx(representative_signal)
    assert outcome.probability == pytest.approx(abs(expected_signal - 85.0))


def test_dp5_representative_swap_leaves_probability_unchanged() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    group = _group()
    model_a = _ReconstructionDP5Model()
    model_b = _ReconstructionDP5Model()
    out_a = _compute_candidate_dp5(
        _dp5_candidate(_carbon_assignment(group, atom_label="C1")),
        _dp5_structure(),
        NmrConfig(),
        model_a,
    )
    out_b = _compute_candidate_dp5(
        _dp5_candidate(_carbon_assignment(group, atom_label="C2")),
        _dp5_structure(),
        NmrConfig(),
        model_b,
    )

    # same membership → same reconstructed signal → same probability
    assert model_a.conformer_shifts == model_b.conformer_shifts
    assert model_a.exp_shifts == model_b.exp_shifts
    assert out_a.probability == pytest.approx(out_b.probability)
    assert model_a.conformer_shifts == [
        [pytest.approx(_shift_for(C_GROUP_MEAN, "13C", NmrConfig()))]
    ]

    # sensitivity proof: the old representative-label lookup would have fed a
    # DIFFERENT probability for each representative (unequal member shieldings)
    old_error_a = abs(_shift_for(C_ISO_REPRESENTATIVE, "13C", NmrConfig()) - 85.0)
    old_error_b = abs(_shift_for(C_ISO_PARTNER, "13C", NmrConfig()) - 85.0)
    assert old_error_a != pytest.approx(old_error_b)
    assert out_a.probability != pytest.approx(old_error_a)
    assert out_a.probability != pytest.approx(old_error_b)


def test_dp5_resolves_signal_group_map_uids() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    group = _group(uids=("C:0", "C:2"))
    candidate = _dp5_candidate(_carbon_assignment(group))
    model = _ReconstructionDP5Model()

    outcome = _compute_candidate_dp5(candidate, _dp5_structure(with_map=True), NmrConfig(), model)

    expected_signal = _shift_for(C_GROUP_MEAN, "13C", NmrConfig())
    assert outcome.mode == "fallback"
    assert model.conformer_shifts == [[pytest.approx(expected_signal)]]


def test_dp5_singleton_group_matches_legacy_representative_path() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    grouped = _dp5_candidate(_carbon_assignment(_group(uids=("C1",), coefficients=(1.0,))))
    legacy = _dp5_candidate(_carbon_assignment(None))
    model_grouped = _ReconstructionDP5Model()
    model_legacy = _ReconstructionDP5Model()

    out_grouped = _compute_candidate_dp5(grouped, _dp5_structure(), NmrConfig(), model_grouped)
    out_legacy = _compute_candidate_dp5(legacy, _dp5_structure(), NmrConfig(), model_legacy)

    assert model_grouped.conformer_shifts == model_legacy.conformer_shifts
    assert out_grouped.probability == pytest.approx(out_legacy.probability)
    assert model_grouped.conformer_shifts == [
        [pytest.approx(_shift_for(C_ISO_REPRESENTATIVE, "13C", NmrConfig()))]
    ]


def test_dp5_unresolvable_group_member_degrades_typed_not_silent() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    group = _group(uids=("C1", "C9"))  # C9 does not exist in the structure
    candidate = _dp5_candidate(_carbon_assignment(group))
    model = _ReconstructionDP5Model()

    outcome = _compute_candidate_dp5(candidate, _dp5_structure(), NmrConfig(), model)

    assert outcome.mode == "averaged"
    assert outcome.status == "valid"
    assert outcome.diagnostics[0]["fallback_reason"] == "signal_group_unresolved"
    # the group-residual path ran; no representative substitution reached DP5
    assert model.conformer_shifts == []
    assert model.averaged_calls == [[0.0]]


def test_dp5_partial_unresolvable_group_skips_only_that_signal() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    good = _group()
    bad = _group(uids=("C9",), coefficients=(1.0,))
    candidate = _dp5_candidate(
        _carbon_assignment(good, atom_label="C1", exp_ppm=85.0),
        _carbon_assignment(bad, atom_label="C9", exp_ppm=80.0),
    )
    model = _ReconstructionDP5Model()

    outcome = _compute_candidate_dp5(candidate, _dp5_structure(), NmrConfig(), model)

    assert outcome.mode == "fallback"
    assert model.exp_shifts == [85.0]
    assert model.conformer_shifts == [[pytest.approx(_shift_for(C_GROUP_MEAN, "13C", NmrConfig()))]]
    skipped = outcome.diagnostics[0]["skipped_signal_groups"]
    assert isinstance(skipped, list) and len(skipped) == 1
    assert skipped[0] == {
        "atom_label": "C9",
        "atom_uids": ["C9"],
        "reason": "member_unresolved",
    }


def test_dp5_group_length_mismatch_degrades_typed() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    group = _group()
    # simulate a malformed external payload: bypass the frozen dataclass
    # invariant so the resolver's explicit length check is exercised
    object.__setattr__(group, "coefficients", (0.5,))
    candidate = _dp5_candidate(_carbon_assignment(group))
    model = _ReconstructionDP5Model()

    outcome = _compute_candidate_dp5(candidate, _dp5_structure(), NmrConfig(), model)

    assert outcome.mode == "averaged"
    assert outcome.diagnostics[0]["fallback_reason"] == "signal_group_unresolved"
    assert model.conformer_shifts == []
    skipped = outcome.diagnostics[0]["skipped_signal_groups"]
    assert isinstance(skipped, list) and skipped[0]["reason"] == "length_mismatch"


def test_analyze_candidate_threads_signal_groups_end_to_end() -> None:
    from acp.workflows.nmr import _analyze_candidate, _compute_candidate_dp5

    structure = _dp5_structure(with_map=True)
    experiment = parse_experimental_nmr("C: 85.0(C1)\nEQ: C1,C2")
    candidate = _analyze_candidate(0, structure, [_conformer()], experiment, NmrConfig())

    carbon = [a for a in candidate.assignments if a.element == "C"]
    assert len(carbon) == 1
    assignment_group = carbon[0].signal_group
    assert assignment_group is not None
    assert assignment_group.atom_uids == ("C:0", "C:2")
    assert assignment_group.coefficients == pytest.approx((0.5, 0.5))
    # the explicit candidate record is populated from the emitted shifts
    assert candidate.signal_groups[0] == assignment_group
    assert candidate.signal_groups_used() == candidate.signal_groups

    # the DP5 reconstruction consumes that same group identity
    model = _ReconstructionDP5Model()
    outcome = _compute_candidate_dp5(candidate, structure, NmrConfig(), model)
    assert outcome.mode == "fallback"
    assert model.conformer_shifts == [[pytest.approx(_shift_for(C_GROUP_MEAN, "13C", NmrConfig()))]]
