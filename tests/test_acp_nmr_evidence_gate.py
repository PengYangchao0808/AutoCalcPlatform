"""Evidence gate: empty / insufficient-evidence candidates never win DP4 (G05, todo 8).

BEFORE (gap §12.1): ``compute_dp4({}, model)`` returns ``0.0`` and the raw
``normalize_dp4`` softmax ranks the *empty* candidate at ``0.9342245062343815``
above the real one — a fabricated probability. This suite pins that raw
number (the DP4 math itself must stay unchanged) and verifies the gate that
excludes non-valid candidates from the normalization instead of scoring them.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.nmr.error_model import GoodmanErrorModel
from acp.nmr.models import (
    CandidateEvidence,
    CandidateResult,
    ConformerShielding,
    NmrReport,
    NucleusEvidence,
)
from acp.nmr.probability import (
    compute_dp4,
    normalize_dp4,
    normalize_dp4_gated,
)

# ---------------------------------------------------------------------------
# BEFORE — raw DP4 math (unchanged by the gate)
# ---------------------------------------------------------------------------


def test_before_raw_normalize_dp4_ranks_empty_candidate_first() -> None:
    """BEFORE capture (gap §12.1): the empty-evidence candidate wins the raw softmax.

    This is the pre-gate behavior the evidence gate exists to stop; the raw
    ``normalize_dp4`` math itself must keep producing this number.
    """
    model = GoodmanErrorModel()
    probs = normalize_dp4([compute_dp4({}, model), compute_dp4({"13C": [2, 3]}, model)])
    assert probs == pytest.approx([0.9342245062343815, 0.06577549376561845])
    assert probs[0] > probs[1]  # empty evidence WINS the ungated normalization


# ---------------------------------------------------------------------------
# AFTER — the gate helper
# ---------------------------------------------------------------------------


def test_normalize_dp4_gated_excludes_invalid_and_normalizes_valid() -> None:
    model = GoodmanErrorModel()
    ll = [compute_dp4({}, model), compute_dp4({"13C": [2, 3]}, model)]
    gated = normalize_dp4_gated(ll, ["invalid", "valid"])
    assert gated[0] is None  # excluded — NOT 0.934
    assert gated[1] == pytest.approx(1.0)  # sole valid candidate absorbs all mass


def test_normalize_dp4_gated_valid_only_matches_raw_math() -> None:
    """Gating never perturbs the DP4 math for comparable (valid) candidates."""
    model = GoodmanErrorModel()
    ll = [compute_dp4({"13C": [0.1]}, model), compute_dp4({"13C": [2.0]}, model)]
    gated = normalize_dp4_gated(ll, ["valid", "valid"])
    assert gated == pytest.approx(normalize_dp4(ll))


def test_normalize_dp4_gated_zero_valid_returns_all_none() -> None:
    assert normalize_dp4_gated([-1.0, -2.0], ["invalid", "evidence_insufficient"]) == [
        None,
        None,
    ]


def test_normalize_dp4_gated_single_valid_is_one() -> None:
    assert normalize_dp4_gated([-5.0, -100.0], ["valid", "invalid"]) == [1.0, None]


def test_normalize_dp4_gated_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError):
        normalize_dp4_gated([0.0], ["valid", "valid"])


# ---------------------------------------------------------------------------
# AFTER — CandidateEvidence / CandidateResult serialization
# ---------------------------------------------------------------------------


def _valid_evidence(total_matched: int = 5) -> CandidateEvidence:
    return CandidateEvidence(
        status="valid",
        per_nucleus={
            "13C": NucleusEvidence(expected=1, matched=1),
            "1H": NucleusEvidence(expected=4, matched=total_matched - 1),
        },
        observation_ids=("H:0", "H:1", "H:2", "H:3", "C:0")[:total_matched],
        exclusion_reasons=(),
        total_matched=total_matched,
    )


def _invalid_evidence() -> CandidateEvidence:
    return CandidateEvidence(
        status="invalid",
        per_nucleus={
            "13C": NucleusEvidence(expected=1, matched=0),
            "1H": NucleusEvidence(expected=4, matched=0),
        },
        observation_ids=(),
        exclusion_reasons=("no_matched_signals",),
        total_matched=0,
    )


def test_candidate_evidence_as_dict_is_json_safe() -> None:
    payload = json.dumps(_valid_evidence().as_dict())
    data = json.loads(payload)
    assert data["status"] == "valid"
    assert data["total_matched"] == 5
    assert data["per_nucleus"]["13C"] == {"expected": 1, "matched": 1}
    assert data["observation_ids"] == ["H:0", "H:1", "H:2", "H:3", "C:0"]
    assert data["exclusion_reasons"] == []


def test_candidate_evidence_rejects_unknown_status() -> None:
    with pytest.raises(ValueError):
        CandidateEvidence(
            status="maybe",  # type: ignore[arg-type]
            per_nucleus={},
            observation_ids=(),
            exclusion_reasons=(),
            total_matched=0,
        )


def test_candidate_result_probabilities_default_to_none() -> None:
    cr = CandidateResult(index=0, label="cand")
    assert cr.dp4_probability is None
    assert cr.dp5_probability is None
    assert cr.evidence is None
    data = cr.as_dict()
    assert data["dp4_probability"] is None
    assert data["dp5_probability"] is None
    assert data["evidence"] is None


# ---------------------------------------------------------------------------
# AFTER — NmrReport winner / dp4_ranking
# ---------------------------------------------------------------------------


def test_winner_never_picks_invalid_even_with_higher_dp4() -> None:
    empty = CandidateResult(
        index=0,
        label="empty",
        dp4_probability=0.9342245062343815,  # the fabricated pre-gate score
        evidence=_invalid_evidence(),
    )
    real = CandidateResult(
        index=1,
        label="real",
        dp4_probability=0.06577549376561845,
        dp5_probability=0.5,
        evidence=_valid_evidence(),
    )
    report = NmrReport(candidates=[empty, real])
    assert report.winner is real
    # only `real` is rankable — the invalid candidate never counts as ranked
    assert report.dp4_ranking == "not_applicable"
    summary = report.as_dict()["summary"]
    assert summary["dp4_ranking"] == "not_applicable"
    assert summary["winner"]["index"] == 1  # type: ignore[index]


def test_winner_none_when_all_candidates_invalid() -> None:
    a = CandidateResult(index=0, label="a", dp4_probability=0.5, evidence=_invalid_evidence())
    b = CandidateResult(index=1, label="b", dp4_probability=0.5, evidence=_invalid_evidence())
    report = NmrReport(candidates=[a, b])
    assert report.winner is None
    assert report.dp4_ranking == "not_applicable"
    summary = report.as_dict()["summary"]
    assert summary["winner"] is None
    assert summary["dp4_ranking"] == "not_applicable"


def test_winner_requires_a_probability_not_just_valid_evidence() -> None:
    pending = CandidateResult(index=0, label="pending", evidence=_valid_evidence())
    scored = CandidateResult(index=1, label="scored", dp4_probability=0.4)
    report = NmrReport(candidates=[pending, scored])
    assert report.winner is scored  # dp4_probability is None → not rankable


def test_dp4_ranking_single_ranked_candidate_is_not_applicable() -> None:
    only = CandidateResult(index=0, label="only", dp4_probability=1.0, evidence=_valid_evidence())
    excluded = CandidateResult(index=1, label="x", evidence=_invalid_evidence())
    report = NmrReport(candidates=[only, excluded])
    assert report.winner is only
    assert report.dp4_ranking == "not_applicable"  # single candidate DP4 is meaningless


def test_evidence_insufficient_candidate_is_not_ranked() -> None:
    two_point = CandidateResult(
        index=0,
        label="two-point",
        dp4_probability=0.7,
        evidence=CandidateEvidence(
            status="evidence_insufficient",
            per_nucleus={
                "13C": NucleusEvidence(expected=1, matched=1),
                "1H": NucleusEvidence(expected=4, matched=1),
            },
            observation_ids=("C:0", "H:0"),
            exclusion_reasons=("two_point_calibration",),
            total_matched=2,
        ),
    )
    good = CandidateResult(index=1, label="good", dp4_probability=0.3, evidence=_valid_evidence())
    report = NmrReport(candidates=[two_point, good])
    assert report.winner is good
    assert report.dp4_ranking == "not_applicable"  # only one ranked candidate


# ---------------------------------------------------------------------------
# Workflow-level gate (stages 0–8 through the mocked task cores)
# ---------------------------------------------------------------------------


def _shielding_result(shieldings: dict[int, dict[str, str | float]]) -> object:
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import NmrShielding, NmrShieldingPayload, TaskResult

    payload = NmrShieldingPayload(
        shieldings={
            int(index): NmrShielding(
                symbol=str(values.get("symbol", "")), isotropic=float(values.get("isotropic", 0.0))
            )
            for index, values in shieldings.items()
        }
    )
    return TaskResult(
        task=TaskKind.NMR_SHIELDING, status="completed", complete=True, payload=payload
    )


def _structure(sid: str, symbols: list[str]) -> Structure:
    coords = np.array([(0.0, 0.0, 0.0)] * len(symbols), dtype=float)
    return Structure(id=sid, charge=0, multiplicity=1, symbols=symbols, coordinates=coords)


def _ensemble(
    structure: Structure, shieldings: dict[int, dict[str, str | float]]
) -> StructureEnsemble:
    ens = StructureEnsemble(
        records=[
            StructureRecord(
                structure=structure, energy_hartree=-1.0, free_energy_hartree=-1.0, weight=1.0
            )
        ]
    )
    ens.data = [ConformerShielding("conf_000", 1.0, {i: dict(v) for i, v in shieldings.items()})]
    return ens


_H_DELTAS = (4.0, 3.0, 1.0, 0.0)


def _shieldings(
    symbols: list[str], carbon_delta: float = 40.0
) -> dict[int, dict[str, str | float]]:
    """Canned shieldings: C → δ=carbon_delta, H → δ cycles 4/3/1/0."""
    out: dict[int, dict[str, str | float]] = {}
    h_seen = 0
    for i, sym in enumerate(symbols):
        if sym == "C":
            out[i] = {"symbol": "C", "isotropic": 188.452125 - carbon_delta}
        else:
            out[i] = {"symbol": sym, "isotropic": 32.1243166667 - _H_DELTAS[h_seen % 4]}
            h_seen += 1
    return out


def _run_workflow(
    tmp_path: Path,
    structures: list[Structure],
    spectrum: str,
    carbon_delta_by_candidate: list[float],
):
    ensembles = [
        _ensemble(st, _shieldings(list(st.symbols), carbon_delta_by_candidate[i]))
        for i, st in enumerate(structures)
    ]
    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch(
            "acp.workflows.nmr.run_nmr_shielding",
            side_effect=[
                _shielding_result(_shieldings(list(st.symbols), carbon_delta_by_candidate[i]))
                for i, st in enumerate(structures)
            ],
        ),
    ):
        reader = MagicMock()
        reader.read.side_effect = list(structures)
        reader_cls.return_value = reader

        from acp.workflows.nmr import run_nmr_analysis

        return run_nmr_analysis(
            input_sources=[st.id for st in structures],
            spectrum=spectrum,
            output_dir=str(tmp_path),
            skip_conformers=True,
            prebuilt_ensembles=ensembles,
            error_model="placeholder-student-t",
        )


def _load_report(result) -> dict:
    return json.loads(Path(result.metadata["report_json"]).read_text(encoding="utf-8"))


def test_workflow_empty_evidence_candidate_is_invalid_and_loses(tmp_path: Path) -> None:
    """Happy + failure in one run: A has zero matched signals, B has five.

    BEFORE the gate A (empty residuals) got 0.934 and won; AFTER it is
    ``invalid`` with ``dp4_probability = None`` and B normalizes to 1.0.
    """
    # A carries labels C1/H1..H4; the spectrum only references C2/H5..H8 →
    # every peak is unknown on A (no matched signals) but locks on B
    # (3 carbons + 11 hydrogens → labels C1..C3 / H1..H11).
    struct_a = _structure("candA", ["C", "H", "H", "H", "H"])
    struct_b = _structure("candB", ["C", "C", "C", *["H"] * 11])
    spectrum = "C: 40.0(C2)\nH: 4.0(H5), 3.0(H6), 1.0(H7), 0.0(H8)"

    result = _run_workflow(tmp_path, [struct_a, struct_b], spectrum, [40.0, 40.0])
    assert result.status == "completed", result.error

    report = _load_report(result)
    cand_a, cand_b = report["candidates"]

    # excluded candidate: no fabricated probability, reasons visible
    assert cand_a["dp4_probability"] is None
    assert cand_a["evidence"]["status"] == "invalid"
    assert "no_matched_signals" in cand_a["evidence"]["exclusion_reasons"]

    # valid candidate: normalized among the valid subset only
    assert cand_b["evidence"]["status"] == "valid"
    assert cand_b["dp4_probability"] == pytest.approx(1.0)
    assert cand_b["evidence"]["total_matched"] == 5
    assert set(cand_b["evidence"]["observation_ids"]) == {"C:0", "H:0", "H:1", "H:2", "H:3"}

    # the empty candidate must never win
    assert report["summary"]["winner"] is not None
    assert report["summary"]["winner"]["index"] == 1

    summary = json.loads(
        (Path(result.metadata["report_json"]).parent / "nmr_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["winner"]["index"] == 1
    assert summary["candidates"][0]["exclusion_reasons"]  # reasons visible in the summary
    assert summary["candidates"][0]["dp4_probability"] is None


def test_workflow_all_candidates_empty_report_has_no_winner(tmp_path: Path) -> None:
    """Failure QA: every candidate without evidence → no winner + per-candidate reasons."""
    struct_a = _structure("candA", ["C", "H", "H", "H", "H"])
    struct_b = _structure("candB", ["C", "H", "H", "H", "H"])
    # none of C9/H6..H9 exist on either candidate → zero matched signals each
    spectrum = "C: 40.0(C9)\nH: 4.0(H9), 3.0(H8), 1.0(H7), 0.0(H6)"

    result = _run_workflow(tmp_path, [struct_a, struct_b], spectrum, [40.0, 30.0])
    assert result.status == "completed", result.error
    assert result.metadata["winner"] is None

    report = _load_report(result)
    assert report["summary"]["winner"] is None
    assert report["summary"]["dp4_ranking"] == "not_applicable"
    for cand in report["candidates"]:
        assert cand["dp4_probability"] is None
        assert cand["evidence"]["status"] == "invalid"
        assert cand["evidence"]["exclusion_reasons"]  # non-empty per-candidate reasons

    summary = json.loads(
        (Path(result.metadata["report_json"]).parent / "nmr_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["winner"] is None
    assert summary["dp4_ranking"] == "not_applicable"
    assert all(entry["exclusion_reasons"] for entry in summary["candidates"])


def test_workflow_two_point_candidate_marked_evidence_insufficient(tmp_path: Path) -> None:
    """1–2 matched signals cannot calibrate → excluded as evidence_insufficient."""
    struct = _structure("candA", ["C", "H", "H", "H", "H"])
    spectrum = "C: 40.0(C1)\nH: 4.0(H1)"  # exactly two observations

    result = _run_workflow(tmp_path, [struct], spectrum, [40.0])
    assert result.status == "completed", result.error

    report = _load_report(result)
    cand = report["candidates"][0]
    assert cand["evidence"]["status"] == "evidence_insufficient"
    assert "two_point_calibration" in cand["evidence"]["exclusion_reasons"]
    assert cand["dp4_probability"] is None
    assert report["summary"]["winner"] is None
    assert report["summary"]["dp4_ranking"] == "not_applicable"


def test_workflow_incomparable_nuclei_candidate_excluded(tmp_path: Path) -> None:
    """B matches only 1H while every other candidate also matches 13C.

    Their log-likelihoods sum over different nucleus sets, so B cannot be
    ranked against them (``incomparable_nuclei``) even though it has enough
    raw signal count on its own.
    """
    struct_a = _structure("candA", ["C", "H", "H", "H", "H"])  # C1 + H1..H4
    struct_b = _structure("candB", ["H", "H", "H", "H", "H"])  # H1..H5, no carbon
    spectrum = "C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"

    result = _run_workflow(tmp_path, [struct_a, struct_b], spectrum, [40.0, 40.0])
    assert result.status == "completed", result.error

    report = _load_report(result)
    cand_a, cand_b = report["candidates"]

    assert cand_a["evidence"]["status"] == "valid"
    assert cand_a["dp4_probability"] == pytest.approx(1.0)

    assert cand_b["evidence"]["status"] == "invalid"
    assert "incomparable_nuclei" in cand_b["evidence"]["exclusion_reasons"]
    assert cand_b["dp4_probability"] is None

    assert report["summary"]["winner"] is not None
    assert report["summary"]["winner"]["index"] == 0


def test_workflow_valid_candidates_normalize_normally(tmp_path: Path) -> None:
    """Happy QA: comparable candidates with evidence keep the classic DP4 ranking."""
    struct_a = _structure("candA", ["C", "H", "H", "H", "H"])
    struct_b = _structure("candB", ["C", "H", "H", "H", "H"])
    spectrum = "C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"

    # candidate B is deliberately worse (carbon off by 10 ppm)
    result = _run_workflow(tmp_path, [struct_a, struct_b], spectrum, [40.0, 30.0])
    assert result.status == "completed", result.error

    report = _load_report(result)
    probs = [c["dp4_probability"] for c in report["candidates"]]
    assert all(p is not None for p in probs)
    assert sum(probs) == pytest.approx(1.0)
    assert report["summary"]["dp4_ranking"] == "normal"
    assert report["summary"]["winner"]["index"] == 0
    for cand in report["candidates"]:
        assert cand["evidence"]["status"] == "valid"
        assert cand["evidence"]["exclusion_reasons"] == []
