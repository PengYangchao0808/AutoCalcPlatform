"""Typed probability state with null/unavailable semantics (G07 / gap §8.2, todo 10).

``ProbabilityResult`` wraps the T8 flat ``dp4_probability``/``dp5_probability``
fields with a closed status vocabulary, mode, calibration status and reasons.
Guards pinned here:

* ``nmr_report.json`` round-trip keeps ``probability.dp5.probability`` as JSON
  ``null`` — never ``0`` — for unavailable/placeholder-less DP5;
* a ``ProbabilityResult`` with a blank ``model_id`` or an unknown ``status``
  raises (typed ``ValueError``);
* the flat DP4/DP5 numbers stay byte-identical to the pre-change serialization
  (the DP4/DP5 formulas themselves were not touched by this todo).
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
    PROBABILITY_STATUSES,
    CandidateEvidence,
    CandidateProbability,
    CandidateResult,
    ConformerShielding,
    NmrReport,
    NucleusEvidence,
    ProbabilityResult,
)
from acp.nmr.probability import (
    compute_dp4,
    compute_dp5,
    dp5_log_to_probability,
    normalize_dp4,
    normalize_dp4_gated,
)
from acp.nmr.report import write_json_report

# ---------------------------------------------------------------------------
# Fixtures (pinned BEFORE todo 10 — these numbers must not move)
# ---------------------------------------------------------------------------

_PINNED_LL = (-0.03577998447122769, -0.972451280288905)
_PINNED_GATED_DP4 = (0.7184267845659725, 0.2815732154340274)
_PINNED_DP5 = 0.49105595804747604
_PINNED_FLAT_DP4 = 0.718427  # round(_PINNED_GATED_DP4[0], 6)
_PINNED_FLAT_DP5 = 0.491056  # round(_PINNED_DP5, 6)


def _valid_evidence(total_matched: int = 5) -> CandidateEvidence:
    return CandidateEvidence(
        status="valid",
        per_nucleus={
            "13C": NucleusEvidence(expected=1, matched=1),
            "1H": NucleusEvidence(expected=4, matched=total_matched - 1),
        },
        observation_ids=("C:0", "H:0", "H:1", "H:2", "H:3")[:total_matched],
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


def _dp4_result(
    status: str = "valid", probability: float | None = _PINNED_GATED_DP4[0], reasons=()
) -> ProbabilityResult:
    return ProbabilityResult(
        model_id="goodman-dp4",
        model_version="goodman-legacy",
        status=status,  # type: ignore[arg-type]
        probability=probability,
        mode=None,
        calibration_status="valid",
        reasons=tuple(reasons),
    )


def _dp5_result(
    status: str = "placeholder",
    probability: float | None = _PINNED_DP5,
    reasons=(),
) -> ProbabilityResult:
    return ProbabilityResult(
        model_id="placeholder-dp5",
        model_version="placeholder-student-t",
        status=status,  # type: ignore[arg-type]
        probability=probability,
        mode="fallback",
        calibration_status="placeholder_parameters",
        reasons=tuple(reasons),
    )


def _valid_candidate() -> CandidateResult:
    return CandidateResult(
        index=0,
        label="fixture",
        dp4_probability=_PINNED_GATED_DP4[0],
        dp5_probability=_PINNED_DP5,
        evidence=_valid_evidence(),
        probability=CandidateProbability(dp4=_dp4_result(), dp5=_dp5_result()),
    )


def _excluded_candidate() -> CandidateResult:
    """Excluded by the evidence gate: probabilities None, block still present."""
    return CandidateResult(
        index=1,
        label="excluded",
        evidence=_invalid_evidence(),
        probability=CandidateProbability(
            dp4=_dp4_result(
                status="invalid",
                probability=None,
                reasons=("no_matched_signals",),
            ),
            dp5=_dp5_result(
                status="unavailable",
                probability=None,
                reasons=("no_matched_signals",),
            ),
        ),
    )


def _report() -> NmrReport:
    return NmrReport(candidates=[_valid_candidate(), _excluded_candidate()])


# ---------------------------------------------------------------------------
# Round-trip: null stays null (never 0)
# ---------------------------------------------------------------------------


def test_nmr_report_round_trip_keeps_dp5_probability_null() -> None:
    payload = json.dumps(_report().as_dict())
    data = json.loads(payload)

    excluded = data["candidates"][1]
    dp5 = excluded["probability"]["dp5"]
    assert dp5["probability"] is None  # JSON null — never 0
    assert "dp5_probability" in excluded and excluded["dp5_probability"] is None
    # full status fields survive the round-trip
    assert dp5["status"] == "unavailable"
    assert dp5["model_id"] == "placeholder-dp5"
    assert dp5["model_version"] == "placeholder-student-t"
    assert dp5["mode"] == "fallback"
    assert dp5["calibration_status"] == "placeholder_parameters"
    assert dp5["reasons"] == ["no_matched_signals"]

    dp4 = excluded["probability"]["dp4"]
    assert dp4["probability"] is None
    assert dp4["status"] == "invalid"


def test_write_json_report_round_trip_keeps_null(tmp_path: Path) -> None:
    """The real ``nmr_report.json`` artifact preserves null through file IO."""
    path = write_json_report(_report(), tmp_path / "nmr_report.json")
    data = json.loads(path.read_text(encoding="utf-8"))

    valid, excluded = data["candidates"]
    assert valid["probability"]["dp5"]["probability"] == pytest.approx(_PINNED_DP5)
    assert valid["probability"]["dp4"]["status"] == "valid"
    assert excluded["probability"]["dp5"]["probability"] is None
    assert excluded["probability"]["dp4"]["probability"] is None
    assert '"probability": null' in path.read_text(encoding="utf-8")


def test_candidate_without_attached_probability_emits_null_block() -> None:
    cr = CandidateResult(index=0, label="legacy")
    data = cr.as_dict()
    assert "probability" in data
    assert data["probability"] is None


def test_probability_block_present_for_every_candidate() -> None:
    data = _report().as_dict()
    for candidate in data["candidates"]:
        block = candidate["probability"]
        assert set(block) == {"dp4", "dp5"}
        for side in ("dp4", "dp5"):
            assert set(block[side]) == {
                "model_id",
                "model_version",
                "status",
                "probability",
                "mode",
                "calibration_status",
                "reasons",
                "atom_diagnostics",
            }


def test_null_never_becomes_zero_in_serialization() -> None:
    payload = json.dumps(_report().as_dict())
    data = json.loads(payload)
    for candidate in data["candidates"]:
        if candidate["dp5_probability"] is None:
            assert candidate["probability"]["dp5"]["probability"] is None
        if candidate["dp4_probability"] is None:
            assert candidate["probability"]["dp4"]["probability"] is None


# ---------------------------------------------------------------------------
# Schema validation (typed errors)
# ---------------------------------------------------------------------------


def test_probability_status_vocabulary_is_closed() -> None:
    assert PROBABILITY_STATUSES == (
        "valid",
        "invalid",
        "evidence_insufficient",
        "unavailable",
        "not_applicable",
        "placeholder",
    )


@pytest.mark.parametrize("model_id", ["", "   "])
def test_probability_result_rejects_blank_model_id(model_id: str) -> None:
    with pytest.raises(ValueError, match="model_id"):
        ProbabilityResult(
            model_id=model_id,
            model_version="v1",
            status="valid",
            probability=0.5,
            mode=None,
            calibration_status="valid",
            reasons=(),
        )


def test_probability_result_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="status"):
        ProbabilityResult(
            model_id="goodman-dp4",
            model_version="v1",
            status="maybe",  # type: ignore[arg-type]
            probability=None,
            mode=None,
            calibration_status="valid",
            reasons=(),
        )


def test_probability_result_is_frozen() -> None:
    result = _dp4_result()
    with pytest.raises(AttributeError):
        result.status = "invalid"  # type: ignore[misc]


def test_probability_result_as_dict_is_json_safe() -> None:
    data = json.loads(json.dumps(_dp4_result(reasons=("x",)).as_dict()))
    assert data["model_id"] == "goodman-dp4"
    assert data["status"] == "valid"
    assert data["probability"] == pytest.approx(_PINNED_GATED_DP4[0])
    assert data["mode"] is None
    assert data["reasons"] == ["x"]
    assert data["atom_diagnostics"] == []


def test_candidate_probability_is_frozen_pair() -> None:
    pair = CandidateProbability(dp4=_dp4_result(), dp5=_dp5_result())
    with pytest.raises(AttributeError):
        pair.dp5 = pair.dp4  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Numeric guard: DP4/DP5 formulas byte-identical to pre-change
# ---------------------------------------------------------------------------


def test_dp4_formulas_unchanged_pinned_values() -> None:
    em = GoodmanErrorModel()
    ll = [compute_dp4({"13C": [0.1]}, em), compute_dp4({"13C": [2.0]}, em)]
    assert ll == pytest.approx(list(_PINNED_LL))
    gated = normalize_dp4_gated(ll, ["valid", "valid"])
    assert gated == pytest.approx(list(_PINNED_GATED_DP4))
    assert normalize_dp4(ll) == pytest.approx(list(_PINNED_GATED_DP4))


def test_dp5_formula_unchanged_pinned_value() -> None:
    em = GoodmanErrorModel()
    dp5 = dp5_log_to_probability(compute_dp5({"13C": [0.1]}, em))
    assert dp5 == pytest.approx(_PINNED_DP5)


def test_flat_probability_bytes_identical_to_prechange() -> None:
    """The nested block is layered on top — flat keys keep their exact bytes."""
    em = GoodmanErrorModel()
    gated = normalize_dp4_gated(
        [compute_dp4({"13C": [0.1]}, em), compute_dp4({"13C": [2.0]}, em)],
        ["valid", "valid"],
    )
    dp5 = dp5_log_to_probability(compute_dp5({"13C": [0.1]}, em))
    cr = CandidateResult(
        index=0,
        label="fixture",
        dp4_probability=gated[0],
        dp5_probability=dp5,
        evidence=_valid_evidence(),
        probability=CandidateProbability(
            dp4=_dp4_result(probability=gated[0]), dp5=_dp5_result(probability=dp5)
        ),
    )
    data = cr.as_dict()
    assert repr(data["dp4_probability"]) == repr(_PINNED_FLAT_DP4)
    assert repr(data["dp5_probability"]) == repr(_PINNED_FLAT_DP5)
    # excluded candidate: flat keys stay null (T8 semantics untouched)
    excluded = _excluded_candidate().as_dict()
    assert excluded["dp4_probability"] is None
    assert excluded["dp5_probability"] is None


# ---------------------------------------------------------------------------
# Workflow-level: stage 7 attaches the block (placeholder DP5 path)
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


def _ensemble(structure: Structure, shieldings: dict[int, dict[str, str | float]]) -> object:
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
    out: dict[int, dict[str, str | float]] = {}
    h_seen = 0
    for i, sym in enumerate(symbols):
        if sym == "C":
            out[i] = {"symbol": "C", "isotropic": 188.452125 - carbon_delta}
        else:
            out[i] = {"symbol": sym, "isotropic": 32.1243166667 - _H_DELTAS[h_seen % 4]}
            h_seen += 1
    return out


def test_workflow_stage7_attaches_probability_block(tmp_path: Path) -> None:
    """Valid candidate gets a full block; the excluded one keeps None (never 0)."""
    struct_a = _structure("candA", ["C", "H", "H", "H", "H"])
    struct_b = _structure("candB", ["C", "C", "C", *["H"] * 11])
    spectrum = "C: 40.0(C2)\nH: 4.0(H5), 3.0(H6), 1.0(H7), 0.0(H8)"
    structures = [struct_a, struct_b]
    ensembles = [
        _ensemble(st, _shieldings(list(st.symbols), delta))
        for st, delta in zip(structures, [40.0, 40.0], strict=True)
    ]
    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch(
            "acp.workflows.nmr.run_nmr_shielding",
            side_effect=[
                _shielding_result(_shieldings(list(st.symbols), delta))
                for st, delta in zip(structures, [40.0, 40.0], strict=True)
            ],
        ),
    ):
        reader = MagicMock()
        reader.read.side_effect = list(structures)
        reader_cls.return_value = reader

        from acp.workflows.nmr import run_nmr_analysis

        result = run_nmr_analysis(
            input_sources=[st.id for st in structures],
            spectrum=spectrum,
            output_dir=str(tmp_path),
            skip_conformers=True,
            prebuilt_ensembles=ensembles,  # type: ignore[arg-type]
            error_model="placeholder-student-t",
        )
    assert result.status == "completed", result.error

    data = json.loads(Path(result.metadata["report_json"]).read_text(encoding="utf-8"))
    cand_a, cand_b = data["candidates"]

    # excluded candidate (A): block present, probabilities null, statuses honest
    block_a = cand_a["probability"]
    assert block_a is not None
    assert block_a["dp4"]["status"] == "invalid"
    assert block_a["dp4"]["probability"] is None
    assert block_a["dp4"]["model_id"] == "goodman-dp4"
    assert block_a["dp5"]["status"] == "unavailable"
    assert block_a["dp5"]["probability"] is None
    assert "no_matched_signals" in block_a["dp5"]["reasons"]

    # valid candidate (B): real numbers + placeholder-DP5 status
    block_b = cand_b["probability"]
    assert block_b is not None
    assert block_b["dp4"]["status"] == "valid"
    assert block_b["dp4"]["probability"] == pytest.approx(1.0)
    assert block_b["dp4"]["calibration_status"] == "valid"
    assert block_b["dp5"]["model_id"] == "placeholder-dp5"
    assert block_b["dp5"]["status"] == "placeholder"
    assert block_b["dp5"]["probability"] == pytest.approx(cand_b["dp5_probability"])
    assert block_b["dp5"]["mode"] == "fallback"
    # flat keys unchanged for consumers
    assert cand_b["dp4_probability"] == pytest.approx(1.0)
    assert cand_a["dp4_probability"] is None
