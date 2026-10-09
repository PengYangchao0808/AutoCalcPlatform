# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Atomic/signal-level diagnostics + per-nucleus DP4 split (todo 39 / G16).

Covers the pure ``acp.nmr.atomic_diagnostics`` builder and its workflow
wiring: the ``dp4_13c``/``dp4_1h``/``dp4_combined`` decomposition, per-signal
residual risk records, consumption of the todo-38 typed DP5 records
(``Dp5ProbabilityRecord``/``FchlSupport``/``AtomFchlProbability``), per-atom
neighbour support, leave-one-signal-out ranking deltas, the inter-candidate
claim/conflict matrix, and the risk-vs-calibrated namespace separation.

All fixtures are small synthetic candidates built in memory — no QC, no
RDKit, no real DP5 assets.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from acp.nmr.atomic_diagnostics import (
    DIAGNOSTICS_KIND,
    AtomDp5Support,
    aggregate_atom_support,
    build_atomic_diagnostics,
)
from acp.nmr.error_model import (
    DP5_CALIBRATION_OUT_OF_DOMAIN,
    DP5_CALIBRATION_UNWEIGHTED,
    DP5_CALIBRATION_WEIGHTED,
    DP5_PATH_FALLBACK,
    DP5_PATH_FCHL,
    Dp5FchlDiagnostics,
    Dp5ProbabilityRecord,
    GoodmanErrorModel,
)
from acp.nmr.fchl import (
    AtomFchlProbability,
    FchlSupport,
    kernel_backend,
    kernel_similarity_support,
)
from acp.nmr.models import (
    Assignment,
    CandidateEvidence,
    CandidateProbability,
    CandidateResult,
    ConformerShielding,
    ExperimentalNmr,
    ExperimentalPeak,
    NmrConfig,
    NmrReport,
    NucleusEvidence,
    ProbabilityResult,
    element_of_nucleus,
)
from acp.nmr.probability import compute_dp4, normalize_dp4_gated

# Goodman DP4 sigmas (error_model.GoodmanErrorModel.SIGMA, verified DP4.py)
SIGMA_C = 2.269372270818724
SIGMA_H = 0.18731058105269952

EM = GoodmanErrorModel()


# ---------------------------------------------------------------------------
# synthetic fixtures
# ---------------------------------------------------------------------------


def _assignment(
    atom_label: str,
    element: str,
    residual: float,
    *,
    exp_ppm: float = 40.0,
    calc_ppm: float | None = None,
    observation_id: str | None = None,
) -> Assignment:
    calc = exp_ppm + residual if calc_ppm is None else calc_ppm
    return Assignment(
        atom_label=atom_label,
        element=element,
        exp_ppm=exp_ppm,
        calc_ppm=calc,
        scaled_ppm=exp_ppm + residual,
        residual=residual,
        observation_id=observation_id,
    )


def _candidate(
    index: int, assignments: list[Assignment], label: str | None = None
) -> CandidateResult:
    return CandidateResult(
        index=index, label=label or f"cand{index}", assignments=list(assignments)
    )


def _carbon_candidate(index: int, residuals: list[float]) -> CandidateResult:
    return _candidate(
        index,
        [
            _assignment(
                f"C{position + 1}",
                "C",
                residual,
                exp_ppm=40.0 + position,
                observation_id=f"C:{position}",
            )
            for position, residual in enumerate(residuals)
        ],
    )


def _experiment_carbon(n: int) -> ExperimentalNmr:
    return ExperimentalNmr(
        peaks={
            "C": [
                ExperimentalPeak(shift_ppm=40.0 + position, element="C", index=position)
                for position in range(n)
            ]
        }
    )


def _combined_ll(candidate: CandidateResult, config: NmrConfig = NmrConfig()) -> float:
    return compute_dp4(
        {
            nucleus: [
                a.residual
                for a in candidate.assignments
                if a.element == element_of_nucleus(nucleus)
            ]
            for nucleus in config.nuclei
        },
        EM,
    )


def _rank(probabilities: list[float | None]) -> tuple[int, ...]:
    ranked = [(index, value) for index, value in enumerate(probabilities) if value is not None]
    ranked.sort(key=lambda pair: (-pair[1], pair[0]))
    return tuple(index for index, _ in ranked)


# ---------------------------------------------------------------------------
# per-nucleus DP4 decomposition
# ---------------------------------------------------------------------------


def test_dp4_split_recomputes_from_same_likelihoods() -> None:
    """dp4_13c / dp4_1h / dp4_combined are candidate-set normalizations of
    the SAME per-nucleus log-likelihoods (combined = product over nuclei)."""
    cand0 = _candidate(
        0,
        [
            _assignment("C1", "C", 0.10, exp_ppm=40.0, observation_id="C:0"),
            _assignment("C2", "C", -0.30, exp_ppm=30.0, observation_id="C:1"),
            _assignment("H1", "H", 0.02, exp_ppm=3.0, observation_id="H:0"),
        ],
    )
    cand1 = _candidate(
        1,
        [
            _assignment("C1", "C", 1.00, exp_ppm=40.0, observation_id="C:0"),
            _assignment("C2", "C", -1.20, exp_ppm=30.0, observation_id="C:1"),
            _assignment("H1", "H", -0.05, exp_ppm=3.0, observation_id="H:0"),
        ],
    )
    config = NmrConfig()
    bundle = build_atomic_diagnostics([cand0, cand1], config, EM, experiment=_experiment_carbon(2))

    c0 = [a.residual for a in cand0.assignments if a.element == "C"]
    c1 = [a.residual for a in cand1.assignments if a.element == "C"]
    h0 = [a.residual for a in cand0.assignments if a.element == "H"]
    h1 = [a.residual for a in cand1.assignments if a.element == "H"]

    expected_13c = normalize_dp4_gated(
        [compute_dp4({"13C": c0}, EM), compute_dp4({"13C": c1}, EM)], ["valid", "valid"]
    )
    expected_1h = normalize_dp4_gated(
        [compute_dp4({"1H": h0}, EM), compute_dp4({"1H": h1}, EM)], ["valid", "valid"]
    )
    expected_combined = normalize_dp4_gated(
        [
            compute_dp4({"13C": c0, "1H": h0}, EM),
            compute_dp4({"13C": c1, "1H": h1}, EM),
        ],
        ["valid", "valid"],
    )

    d0, d1 = bundle.candidates[0].dp4, bundle.candidates[1].dp4
    assert d0.dp4_13c == pytest.approx(expected_13c[0])
    assert d1.dp4_13c == pytest.approx(expected_13c[1])
    assert d0.dp4_1h == pytest.approx(expected_1h[0])
    assert d1.dp4_1h == pytest.approx(expected_1h[1])
    assert d0.dp4_combined == pytest.approx(expected_combined[0])
    assert d1.dp4_combined == pytest.approx(expected_combined[1])

    # the combined likelihood IS the product over nuclei: sum of per-nucleus
    # log-likelihoods
    assert compute_dp4({"13C": c0, "1H": h0}, EM) == pytest.approx(
        compute_dp4({"13C": c0}, EM) + compute_dp4({"1H": h0}, EM)
    )
    # each normalization is a probability distribution over candidates
    assert d0.dp4_combined + d1.dp4_combined == pytest.approx(1.0)
    assert d0.dp4_13c + d1.dp4_13c == pytest.approx(1.0)
    assert d0.dp4_1h + d1.dp4_1h == pytest.approx(1.0)
    # per-nucleus records carry the risk-scale (unnormalized) log-likelihood
    rec_13c = next(record for record in d0.nuclei if record.nucleus == "13C")
    assert rec_13c.log_likelihood == pytest.approx(compute_dp4({"13C": c0}, EM))
    assert rec_13c.n_signals == 2


def test_missing_nucleus_evidence_is_null_not_zero() -> None:
    """A candidate without that nucleus gets None (and is excluded from the
    per-nucleus normalization) — never a zero-likelihood free win."""
    cand0 = _carbon_candidate(0, [0.1, 0.2, 0.3])
    cand1 = _candidate(
        1,
        [
            _assignment("C1", "C", 0.4, exp_ppm=40.0, observation_id="C:0"),
            _assignment("H1", "H", 0.01, exp_ppm=3.0, observation_id="H:0"),
        ],
    )
    bundle = build_atomic_diagnostics(
        [cand0, cand1], NmrConfig(), EM, experiment=_experiment_carbon(2)
    )
    d0, d1 = bundle.candidates[0].dp4, bundle.candidates[1].dp4
    assert d0.dp4_1h is None
    assert d1.dp4_1h == pytest.approx(1.0)  # only candidate with 1H evidence
    h_record = next(record for record in d0.nuclei if record.nucleus == "1H")
    assert h_record.n_signals == 0
    assert h_record.log_likelihood is None
    assert h_record.probability is None
    assert h_record.status is None


def test_evidence_invalid_candidate_excluded_from_per_nucleus_normalization() -> None:
    """The evidence gate applies to every nucleus separately (G05 + G16)."""
    cand0 = _carbon_candidate(0, [0.1, 0.2, 0.3])
    cand1 = _carbon_candidate(1, [1.0, 1.0, 1.0])
    cand1.evidence = CandidateEvidence(
        status="invalid",
        per_nucleus={"13C": NucleusEvidence(expected=3, matched=0)},
        observation_ids=(),
        exclusion_reasons=("no_matched_signals",),
        total_matched=0,
    )
    bundle = build_atomic_diagnostics([cand0, cand1], NmrConfig(), EM)
    assert bundle.candidates[0].dp4.dp4_13c == pytest.approx(1.0)
    assert bundle.candidates[1].dp4.dp4_13c is None
    record = next(item for item in bundle.candidates[1].dp4.nuclei if item.nucleus == "13C")
    assert record.status == "invalid"
    assert record.probability is None


# ---------------------------------------------------------------------------
# per-signal risk records (atomic risk indicators, NOT calibrated names)
# ---------------------------------------------------------------------------


def test_signal_risk_records_carry_residual_and_z_score() -> None:
    cand = _candidate(
        0,
        [
            _assignment("C1", "C", 2.0, exp_ppm=40.0, observation_id="C:0"),
            _assignment("H1", "H", 0.1, exp_ppm=3.0, observation_id="H:0"),
        ],
    )
    bundle = build_atomic_diagnostics([cand], NmrConfig(), EM, experiment=_experiment_carbon(2))
    signals = {record.signal_id: record for record in bundle.candidates[0].signals}
    c_record = signals["C:0"]
    assert c_record.nucleus == "13C"
    assert c_record.residual_ppm == pytest.approx(2.0)
    assert c_record.abs_residual_ppm == pytest.approx(2.0)
    assert c_record.sigma_ppm == pytest.approx(SIGMA_C)
    assert c_record.z_score == pytest.approx(2.0 / SIGMA_C)
    assert c_record.observation_id == "C:0"
    h_record = signals["H:0"]
    assert h_record.nucleus == "1H"
    assert h_record.sigma_ppm == pytest.approx(SIGMA_H)
    assert h_record.z_score == pytest.approx(0.1 / SIGMA_H)
    # risk records serialize raw (recomputable) values
    assert c_record.as_dict()["z_score"] == pytest.approx(2.0 / SIGMA_C)
    assert c_record.as_dict()["residual_ppm"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# todo-38 typed DP5 records are consumed verbatim
# ---------------------------------------------------------------------------


def _unweighted_record(probability: float = 0.42) -> Dp5ProbabilityRecord:
    return Dp5ProbabilityRecord(
        probability=probability,
        path=DP5_PATH_FALLBACK,
        calibration_status=DP5_CALIBRATION_UNWEIGHTED,
        out_of_domain=False,
        n_atoms=1,
        out_of_domain_atoms=0,
    )


def test_typed_dp5_record_surfaces_verbatim_with_formal_probability() -> None:
    """Dp5ProbabilityRecord (todo 38) flows into diagnostics untouched; an
    out-of-domain record keeps its raw value but has formal_probability None."""
    record = Dp5ProbabilityRecord(
        probability=0.31,
        path=DP5_PATH_FCHL,
        calibration_status=DP5_CALIBRATION_OUT_OF_DOMAIN,
        out_of_domain=True,
        n_atoms=3,
        out_of_domain_atoms=1,
        min_effective_neighbors=0.0,
        min_support_fraction=0.0,
        reasons=("out_of_domain",),
    )
    cand = _carbon_candidate(0, [0.1, 0.2, 0.3])
    bundle = build_atomic_diagnostics([cand], NmrConfig(), EM, dp5_records={0: record})
    diag = bundle.candidates[0]
    assert diag.dp5 is record
    payload = diag.as_dict()["dp5"]
    assert payload == record.as_dict()
    assert payload["probability"] == pytest.approx(0.31)  # raw diagnostic value
    assert payload["formal_probability"] is None  # never presented as calibrated
    assert payload["calibration_status"] == DP5_CALIBRATION_OUT_OF_DOMAIN
    assert payload["out_of_domain"] is True


def test_no_dp5_record_means_no_fabricated_dp5_block() -> None:
    bundle = build_atomic_diagnostics([_carbon_candidate(0, [0.1])], NmrConfig(), EM)
    assert bundle.candidates[0].dp5 is None
    assert bundle.candidates[0].as_dict()["dp5"] is None
    assert bundle.candidates[0].atom_support == ()


# ---------------------------------------------------------------------------
# per-atom neighbour support (todo-38 kernel_similarity_support records)
# ---------------------------------------------------------------------------


def _support(weights: tuple[float, ...]) -> FchlSupport:
    return kernel_similarity_support(np.array(weights, dtype=float), n_train=len(weights))


def test_aggregate_atom_support_uses_todo38_records_and_weights() -> None:
    good = _support((1.0, 0.5))
    atoms_conf0 = [
        AtomFchlProbability(probability=0.60, mode="fchl", support=good),
        AtomFchlProbability(probability=0.20, mode="fchl", support=good),
    ]
    atoms_conf1 = [
        AtomFchlProbability(probability=0.70, mode="fchl", support=good),
        AtomFchlProbability(probability=0.30, mode="fchl", support=good),
    ]
    assignments = [
        _assignment("C1", "C", 0.1, exp_ppm=40.0),
        _assignment("C2", "C", 0.2, exp_ppm=30.0),
    ]
    support_records = aggregate_atom_support(
        assignments, [40.0, 30.0], [atoms_conf0, atoms_conf1], [0.25, 0.75]
    )
    assert len(support_records) == 2
    first = support_records[0]
    assert isinstance(first, AtomDp5Support)
    # Boltzmann-weighted per-atom diagnostic value
    assert first.probability == pytest.approx(0.25 * 0.60 + 0.75 * 0.70)
    # support is the todo-38 record (worst-case conformer) — not re-derived
    assert first.support is good
    assert first.mode == "fchl"
    assert first.out_of_domain is False
    assert first.n_conformers == 2
    payload = first.as_dict()
    assert payload["support"] == good.as_dict()
    assert payload["out_of_domain"] is False


def test_aggregate_atom_support_flags_out_of_domain() -> None:
    empty = _support((0.0, 0.0, 0.0))
    populated = _support((1.0, 0.0, 0.0))
    assert empty.out_of_domain is True
    assert populated.out_of_domain is False
    atoms_ok = [AtomFchlProbability(probability=0.5, mode="fchl", support=populated)]
    atoms_bad = [AtomFchlProbability(probability=0.1, mode="fallback", support=empty)]
    records = aggregate_atom_support(
        [_assignment("C1", "C", 0.1, exp_ppm=40.0)], [40.0], [atoms_ok, atoms_bad], [0.5, 0.5]
    )
    record = records[0]
    # worst-case conformer support defines the risk flag
    assert record.support is empty
    assert record.out_of_domain is True
    assert record.mode == "fallback"
    assert record.probability == pytest.approx(0.5 * 0.5 + 0.5 * 0.1)


def test_aggregate_atom_support_rejects_length_mismatch() -> None:
    atoms = [AtomFchlProbability(probability=0.5, mode="fchl", support=_support((1.0,)))]
    with pytest.raises(ValueError, match="length mismatch"):
        aggregate_atom_support(
            [_assignment("C1", "C", 0.1)],
            [40.0, 41.0],
            [atoms],
            [1.0],
        )
    with pytest.raises(ValueError, match="length mismatch"):
        aggregate_atom_support(
            [_assignment("C1", "C", 0.1), _assignment("C2", "C", 0.2)],
            [40.0, 41.0],
            [atoms],
            [1.0],
        )


# ---------------------------------------------------------------------------
# leave-one-signal-out
# ---------------------------------------------------------------------------


def test_leave_one_signal_out_is_recomputable_from_recorded_fields() -> None:
    cand0 = _carbon_candidate(0, [0.1, -0.2, 0.3, 0.5])
    cand1 = _carbon_candidate(1, [1.0, 1.1, 0.9, 1.2])
    config = NmrConfig()
    bundle = build_atomic_diagnostics([cand0, cand1], config, EM, experiment=_experiment_carbon(4))
    records = bundle.candidates[0].leave_one_signal_out
    assert len(records) == len(cand0.assignments) == 4
    record = records[1]
    assert record.candidate_index == 0
    assert record.signal_id == "C:1"
    assert record.nucleus == "13C"

    # manual recompute from the recorded signal identity alone
    baseline_ll = [_combined_ll(cr, config) for cr in (cand0, cand1)]
    perturbed_ll = list(baseline_ll)
    perturbed_ll[0] = compute_dp4(
        {"13C": [a.residual for position, a in enumerate(cand0.assignments) if position != 1]},
        EM,
    )
    probabilities = normalize_dp4_gated(perturbed_ll, ["valid", "valid"])
    assert record.probability_after == pytest.approx(probabilities[0])
    assert record.probability_before == pytest.approx(
        normalize_dp4_gated(baseline_ll, ["valid", "valid"])[0]
    )
    assert record.probability_delta == pytest.approx(
        record.probability_after - record.probability_before
    )
    assert record.ranking_after == _rank(probabilities)
    assert record.ranking_before == _rank(normalize_dp4_gated(baseline_ll, ["valid", "valid"]))
    assert record.winner_before == record.ranking_before[0]
    assert record.winner_after == record.ranking_after[0]
    assert record.winner_changed is False


def test_leave_one_signal_out_detects_winner_flip() -> None:
    # cand1 wins on total likelihood; removing cand0's one bad carbon makes
    # cand0 win (count stays >= 3, so the evidence gate keeps it rankable)
    cand0 = _carbon_candidate(0, [0.0, 0.0, 0.0, 3.0])
    cand1 = _carbon_candidate(1, [1.0, 1.0, 1.0, 1.0])
    bundle = build_atomic_diagnostics(
        [cand0, cand1], NmrConfig(), EM, experiment=_experiment_carbon(4)
    )
    records = bundle.candidates[0].leave_one_signal_out
    baseline_record = next(r for r in records if r.signal_id == "C:3")
    assert baseline_record.winner_before == 1
    assert baseline_record.winner_after == 0
    assert baseline_record.winner_changed is True
    assert baseline_record.probability_delta > 0
    # other signals are no-ops for cand0's ranking
    assert all(r.winner_changed is False for r in records if r.signal_id != "C:3")


def test_leave_one_signal_out_excludes_candidate_when_evidence_drops() -> None:
    """Dropping a signal can push a 3-signal candidate under the evidence
    threshold — recorded as a typed status, not a silently kept ranking."""
    cand0 = _carbon_candidate(0, [0.1, 0.2, 0.3])
    cand1 = _carbon_candidate(1, [1.0, 1.0, 1.0])
    bundle = build_atomic_diagnostics(
        [cand0, cand1], NmrConfig(), EM, experiment=_experiment_carbon(3)
    )
    records = bundle.candidates[0].leave_one_signal_out
    assert all(record.status_after == "evidence_insufficient" for record in records)
    assert all(record.probability_after is None for record in records)
    assert all(record.probability_delta is None for record in records)


# ---------------------------------------------------------------------------
# inter-candidate claim / conflict matrix
# ---------------------------------------------------------------------------


def test_conflict_matrix_lists_claims_and_conflicts() -> None:
    exp = ExperimentalNmr(
        peaks={
            "C": [
                ExperimentalPeak(shift_ppm=40.0, element="C", index=0),
                ExperimentalPeak(shift_ppm=30.0, element="C", index=1),
            ]
        }
    )
    cand0 = _candidate(0, [_assignment("C1", "C", 0.1, exp_ppm=40.0, observation_id="C:0")])
    cand1 = _candidate(
        1,
        [
            _assignment("C1", "C", 0.2, exp_ppm=40.0, observation_id="C:0"),
            _assignment("C2", "C", 0.3, exp_ppm=30.0, observation_id="C:1"),
        ],
    )
    matrix = build_atomic_diagnostics(
        [cand0, cand1], NmrConfig(), EM, experiment=exp
    ).conflict_matrix
    assert matrix.candidate_indices == (0, 1)
    assert matrix.observation_ids == ("C:0", "C:1")
    assert matrix.conflicting_observation_ids == ("C:0",)
    claims = {claim.observation_id: claim for claim in matrix.claims}
    assert claims["C:0"].candidate_indices == (0, 1)
    assert claims["C:0"].atom_labels == ("C1", "C1")
    assert claims["C:0"].n_claims == 2
    assert claims["C:0"].conflict is True
    assert claims["C:1"].candidate_indices == (1,)
    assert claims["C:1"].conflict is False
    payload = matrix.as_dict()
    assert payload["conflicting_observation_ids"] == ["C:0"]


def test_conflict_matrix_includes_unclaimed_experimental_signals() -> None:
    exp = ExperimentalNmr(
        peaks={
            "C": [ExperimentalPeak(shift_ppm=40.0, element="C", index=0)],
            "H": [ExperimentalPeak(shift_ppm=3.0, element="H", index=0)],
        }
    )
    cand = _candidate(0, [_assignment("C1", "C", 0.1, exp_ppm=40.0, observation_id="C:0")])
    matrix = build_atomic_diagnostics([cand], NmrConfig(), EM, experiment=exp).conflict_matrix
    assert matrix.observation_ids == ("C:0", "H:0")
    h_claim = next(claim for claim in matrix.claims if claim.observation_id == "H:0")
    assert h_claim.candidate_indices == ()
    assert h_claim.atom_labels == ()
    assert h_claim.conflict is False
    assert h_claim.n_claims == 0
    assert matrix.conflicting_observation_ids == ()


# ---------------------------------------------------------------------------
# namespace separation: risk indicators vs calibrated probabilities
# ---------------------------------------------------------------------------


def test_risk_and_calibrated_namespaces_are_separate() -> None:
    cand = _carbon_candidate(0, [0.1, 0.2, 0.3])
    bundle = build_atomic_diagnostics([cand], NmrConfig(), EM, experiment=_experiment_carbon(3))
    cand.atomic_diagnostics = bundle.candidates[0]
    cand.probability = CandidateProbability(
        dp4=ProbabilityResult(
            model_id="goodman-dp4",
            model_version="goodman-legacy",
            status="valid",
            probability=bundle.candidates[0].dp4.dp4_combined,
            mode=None,
            calibration_status="valid",
        ),
        dp5=ProbabilityResult(
            model_id="goodman-dp5",
            model_version="goodman-dp5",
            status="unavailable",
            probability=None,
            mode=None,
            calibration_status="not_evaluated",
            reasons=("dp5_model_unavailable",),
        ),
    )
    blob = cand.as_dict()
    assert "diagnostics" in blob and "probability" in blob
    diag = blob["diagnostics"]
    assert diag["kind"] == DIAGNOSTICS_KIND == "atomic_risk_indicators"

    # risk-indicator names live under diagnostics, never under probability
    probability_text = json.dumps(blob["probability"], sort_keys=True)
    for risk_name in (
        "z_score",
        "residual_ppm",
        "abs_residual_ppm",
        "sigma_ppm",
        "support",
        "out_of_domain",
    ):
        assert risk_name not in probability_text
    # calibrated-probability names do not appear inside the risk block
    diagnostics_text = json.dumps(diag, sort_keys=True)
    assert "dp4_probability" not in diagnostics_text
    assert "dp5_probability" not in diagnostics_text
    # the calibrated combined probability IS the diagnostic decomposition value
    assert blob["probability"]["dp4"]["probability"] == pytest.approx(diag["dp4"]["dp4_combined"])
    # per-nucleus DP4 fields are present by the gap-doc names
    assert {"dp4_13c", "dp4_1h", "dp4_combined"} <= set(diag["dp4"])


def test_report_level_diagnostics_block_serializes_conflict_matrix() -> None:
    # 4 signals: dropping one keeps the count at 3, so no winner flip here
    cand = _carbon_candidate(0, [0.1, 0.2, 0.3, 0.4])
    bundle = build_atomic_diagnostics([cand], NmrConfig(), EM, experiment=_experiment_carbon(4))
    report = NmrReport(candidates=[cand], config=NmrConfig())
    report.metadata["atomic_diagnostics"] = bundle.report_block()
    payload = report.as_dict()
    block = payload["diagnostics"]
    assert block["kind"] == DIAGNOSTICS_KIND
    assert block["conflict_matrix"]["observation_ids"] == ["C:0", "C:1", "C:2", "C:3"]
    loo = block["leave_one_signal_out"]
    assert loo["n_cases"] == 4
    assert loo["n_winner_flips"] == 0
    assert loo["winner_flips"] == []
    # report without diagnostics stays null — never invented
    bare = NmrReport(candidates=[]).as_dict()
    assert bare["diagnostics"] is None


def test_build_nmr_report_surfaces_diagnostics_metadata() -> None:
    """Stage 8 puts the bundle's report block into metadata → JSON."""
    from acp.core.models import Structure
    from acp.workflows.nmr import _build_nmr_report

    cand0 = _carbon_candidate(0, [0.1, 0.2, 0.3, 0.4])
    cand1 = _carbon_candidate(1, [1.0, 1.1, 0.9, 1.2])
    bundle = build_atomic_diagnostics(
        [cand0, cand1], NmrConfig(), EM, experiment=_experiment_carbon(4)
    )
    for candidate, diagnostics in zip([cand0, cand1], bundle.candidates, strict=True):
        candidate.atomic_diagnostics = diagnostics
    structures = [
        Structure(
            id=f"cand{index}",
            charge=0,
            multiplicity=1,
            symbols=["C"],
            coordinates=np.zeros((1, 3)),
        )
        for index in range(2)
    ]
    stage = _build_nmr_report(
        structures,
        [cand0, cand1],
        NmrConfig(),
        EM,
        [False, False],
        False,
        {"status": "skipped"},
        bundle,
    )
    payload = stage.report.as_dict()
    assert payload["diagnostics"]["kind"] == DIAGNOSTICS_KIND
    assert payload["diagnostics"]["conflict_matrix"]["observation_ids"] == [
        "C:0",
        "C:1",
        "C:2",
        "C:3",
    ]
    assert payload["candidates"][0]["diagnostics"]["dp4"]["dp4_combined"] is not None


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_build_is_deterministic() -> None:
    cand0 = _carbon_candidate(0, [0.1, -0.2, 0.3])
    cand1 = _carbon_candidate(1, [1.0, 0.5, 0.9])
    exp = _experiment_carbon(3)
    first = build_atomic_diagnostics([cand0, cand1], NmrConfig(), EM, experiment=exp)
    second = build_atomic_diagnostics([cand0, cand1], NmrConfig(), EM, experiment=exp)
    dump_first = json.dumps([c.as_dict() for c in first.candidates], sort_keys=True)
    dump_second = json.dumps([c.as_dict() for c in second.candidates], sort_keys=True)
    assert dump_first == dump_second
    assert first.conflict_matrix.as_dict() == second.conflict_matrix.as_dict()


# ---------------------------------------------------------------------------
# workflow wiring: stage-7 attaches diagnostics (no QC)
# ---------------------------------------------------------------------------


def test_stage7_attaches_diagnostics_and_matches_calibrated_dp4(monkeypatch) -> None:
    from unittest.mock import patch

    from acp.workflows.nmr import _score_candidate_probabilities

    cand0 = _carbon_candidate(0, [0.1, -0.2, 0.3])
    cand1 = _carbon_candidate(1, [1.0, 0.5, 0.9])
    # single carbon signal for the stage-7 likelihood is per configured
    # nuclei; both candidates have 3 carbon residuals → valid evidence
    with (
        patch("acp.workflows.nmr.dp5_model_available", return_value=False),
    ):
        stage = _score_candidate_probabilities(
            [cand0, cand1],
            [],
            [[], []],
            _experiment_carbon(3),
            NmrConfig(),
            EM,
        )
    for cr in (cand0, cand1):
        assert cr.atomic_diagnostics is not None
        assert cr.atomic_diagnostics.dp4.dp4_combined == pytest.approx(cr.dp4_probability)
        assert cr.atomic_diagnostics.dp4.dp4_13c is not None
        # DP5 assets unavailable → typed record absent, no fabricated block
        assert cr.atomic_diagnostics.dp5 is None
    assert stage.diagnostics is not None
    assert len(stage.diagnostics.candidates) == 2
    assert stage.diagnostics.conflict_matrix.observation_ids == ("C:0", "C:1", "C:2")


# ---------------------------------------------------------------------------
# workflow wiring: _compute_candidate_dp5 carries todo-38 typed records
# ---------------------------------------------------------------------------


class _RecordOnlyDP5Model:
    """Fake with ONLY the todo-38 diagnostic entry points (no float views)."""

    model_id = "goodman-dp5"
    fchl_available = False

    def __init__(self, record: Dp5ProbabilityRecord) -> None:
        self.record = record
        self.calls: list[list[float]] = []

    def probability_per_conformer_diagnostic(self, shifts, exp, weights) -> Dp5ProbabilityRecord:
        self.calls.append([float(value) for row in shifts for value in row])
        return self.record


class _FloatOnlyDP5Model:
    """Legacy stand-in with only the float view — record stays None."""

    model_id = "goodman-dp5"
    fchl_available = False

    def probability_per_conformer(self, shifts, exp, weights) -> float:
        return 0.25


def _dp5_candidate_and_structure():
    from acp.core.models import Structure

    structure = Structure(
        id="cand",
        charge=0,
        multiplicity=1,
        symbols=["C", "H", "H", "H", "H"],
        coordinates=np.zeros((5, 3)),
    )
    assignment = Assignment(
        atom_label="C1",
        element="C",
        exp_ppm=40.0,
        calc_ppm=40.0,
        scaled_ppm=40.0,
        residual=0.1,
        observation_id="C:0",
    )
    conformer = ConformerShielding(
        conformer_id="conf_000",
        boltzmann_weight=1.0,
        shieldings={0: {"symbol": "C", "isotropic": 150.0}},
    )
    candidate = CandidateResult(
        index=0, label="cand", assignments=[assignment], conformer_shieldings=[conformer]
    )
    return candidate, structure


def test_compute_candidate_dp5_consumes_typed_diagnostic_record() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    candidate, structure = _dp5_candidate_and_structure()
    record = _unweighted_record(probability=0.42)
    model = _RecordOnlyDP5Model(record)
    outcome = _compute_candidate_dp5(candidate, structure, NmrConfig(), model)
    assert outcome.record is record
    assert outcome.probability == pytest.approx(0.42)
    assert outcome.mode == "fallback"
    assert model.calls  # the same geometry-weighted pipeline ran


def test_compute_candidate_dp5_legacy_float_model_has_no_record() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    candidate, structure = _dp5_candidate_and_structure()
    outcome = _compute_candidate_dp5(candidate, structure, NmrConfig(), _FloatOnlyDP5Model())
    assert outcome.record is None
    assert outcome.atom_support == ()
    assert outcome.probability == pytest.approx(0.25)


def test_compute_candidate_dp5_fchl_path_collects_atom_support(monkeypatch) -> None:
    import acp.nmr.fchl as fchl_module
    from acp.core.models import Structure
    from acp.nmr.models import ConformerShielding
    from acp.workflows.nmr import _compute_candidate_dp5

    monkeypatch.setattr(
        fchl_module,
        "build_atom_representations",
        lambda coords, symbols, indices: [np.zeros(4) for _ in indices],
    )

    support = _support((1.0, 0.5))
    atoms_conf0 = [AtomFchlProbability(probability=0.60, mode="fchl", support=support)]
    atoms_conf1 = [AtomFchlProbability(probability=0.70, mode="fchl", support=support)]
    record = Dp5ProbabilityRecord(
        probability=0.55,
        path=DP5_PATH_FCHL,
        calibration_status=DP5_CALIBRATION_WEIGHTED,
        out_of_domain=False,
        n_atoms=1,
        out_of_domain_atoms=0,
        min_effective_neighbors=support.effective_neighbors,
        min_support_fraction=support.support_fraction,
    )

    class _FchlDiagModel:
        model_id = "goodman-dp5"
        fchl_available = True

        def probability_per_conformer_fchl_atom_diagnostics(
            self, shifts, exp, weights, reps, *, use_fragment_reps=False
        ) -> Dp5FchlDiagnostics:
            return Dp5FchlDiagnostics(
                record=record, atom_records=(tuple(atoms_conf0), tuple(atoms_conf1))
            )

    structure = Structure(
        id="cand",
        charge=0,
        multiplicity=1,
        symbols=["C", "H", "H", "H", "H"],
        coordinates=np.zeros((5, 3)),
    )
    assignment = Assignment(
        atom_label="C1",
        element="C",
        exp_ppm=40.0,
        calc_ppm=40.0,
        scaled_ppm=40.0,
        residual=0.1,
        observation_id="C:0",
    )
    conformers = [
        ConformerShielding(
            conformer_id=f"conf_{index:03d}",
            boltzmann_weight=0.5,
            shieldings={0: {"symbol": "C", "isotropic": 150.0 + index}},
            coordinates=np.zeros((5, 3)),
            symbols=["C", "H", "H", "H", "H"],
        )
        for index in range(2)
    ]
    candidate = CandidateResult(
        index=0, label="cand", assignments=[assignment], conformer_shieldings=conformers
    )

    outcome = _compute_candidate_dp5(candidate, structure, NmrConfig(), _FchlDiagModel())
    assert outcome.record is record
    assert outcome.mode == "fchl"
    assert outcome.kernel == kernel_backend()  # read at call time, never model state
    assert len(outcome.atom_support) == 1
    atom = outcome.atom_support[0]
    assert atom.support is support
    assert atom.mode == "fchl"
    assert atom.out_of_domain is False
    assert atom.probability == pytest.approx(0.5 * 0.60 + 0.5 * 0.70)
