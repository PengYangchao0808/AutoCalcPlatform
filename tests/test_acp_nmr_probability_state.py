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
import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.nmr.error_model import GoodmanErrorModel
from acp.nmr.models import (
    PROBABILITY_STATUSES,
    Assignment,
    CandidateEvidence,
    CandidateProbability,
    CandidateResult,
    ConformerShielding,
    NmrConfig,
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
    # placeholder never fills dp5_probability — the value is the diagnostic
    assert block_b["dp5"]["probability"] is None
    assert cand_b["dp5_probability"] is None
    assert cand_b["dp5_diagnostic_score"] is not None
    assert math.isfinite(cand_b["dp5_diagnostic_score"])
    assert block_b["dp5"]["mode"] == "fallback"
    assert block_b["dp5"]["reasons"] == ["placeholder_error_model"]
    # flat keys unchanged for consumers
    assert cand_b["dp4_probability"] == pytest.approx(1.0)
    assert cand_a["dp4_probability"] is None


# ---------------------------------------------------------------------------
# todo 13: DP5 degradation semantics (G05/G07) — missing assets / placeholder
# config / no carbon must never fabricate a value in ``dp5_probability``; the
# placeholder path writes the separately-named ``dp5_diagnostic_score``.
# ---------------------------------------------------------------------------


class _FakeDP5Model:
    """Minimal Goodman-DP5 stand-in for the workflow's ``load_dp5_model`` seam.

    Deliberately carries NO ``dp5_mode``/``fchl_kernel`` attributes (G07:
    mode is reported per call, never as shared model state).
    """

    model_id = "goodman-dp5"
    fchl_available = False

    def __init__(self, value: float = 0.7) -> None:
        self._value = value
        self.calls: list[list[float]] = []
        self.weight_calls: list[list[float]] = []  # todo 15: spy on weights

    def probability(self, carbon_errors: list[float]) -> float:
        self.calls.append(list(carbon_errors))
        return self._value

    def probability_per_conformer(self, shifts, exp, weights) -> float:
        """Geometry-weighted fallback path (todo 15: single conformers route here)."""
        self.weight_calls.append(list(weights))
        return self._value


def _run_workflow(
    tmp_path: Path,
    structures: list[Structure],
    spectrum: str,
    carbon_deltas: list[float],
    *,
    error_model: str = "goodman-legacy",
    dp5_available: bool = False,
    load_result: object = None,
    load_side_effect: BaseException | None = None,
):
    """Run the workflow with the DP5 asset seams patched at the workflow."""
    ensembles = [
        _ensemble(st, _shieldings(list(st.symbols), carbon_deltas[i]))
        for i, st in enumerate(structures)
    ]
    shielding_results = [
        _shielding_result(_shieldings(list(st.symbols), carbon_deltas[i]))
        for i, st in enumerate(structures)
    ]
    load_kwargs: dict[str, object] = {"return_value": load_result}
    if load_side_effect is not None:
        load_kwargs = {"side_effect": load_side_effect}
    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch("acp.workflows.nmr.run_nmr_shielding", side_effect=shielding_results),
        patch("acp.workflows.nmr.dp5_model_available", return_value=dp5_available),
        patch("acp.workflows.nmr.load_dp5_model", **load_kwargs),
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
            prebuilt_ensembles=ensembles,  # type: ignore[arg-type]
            error_model=error_model,
        )


def _load_report(result) -> dict:
    return json.loads(Path(result.metadata["report_json"]).read_text(encoding="utf-8"))


def test_workflow_dp5_assets_missing_reports_unavailable_null(tmp_path: Path) -> None:
    """G05/G07: missing DP5 assets → ``unavailable`` + JSON null, never 0.5."""
    struct = _structure("candA", ["C", "H", "H", "H", "H"])
    spectrum = "C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"
    result = _run_workflow(
        tmp_path,
        [struct],
        spectrum,
        [40.0],
        error_model="goodman-legacy",
        dp5_available=False,
    )
    assert result.status == "completed", result.error

    data = _load_report(result)
    cand = data["candidates"][0]
    block = cand["probability"]
    assert block["dp4"]["status"] == "valid"  # the candidate itself ranks
    assert block["dp5"]["status"] == "unavailable"
    assert block["dp5"]["probability"] is None
    assert cand["dp5_probability"] is None  # JSON null — the old leak was 0.5-ish
    assert cand["dp5_diagnostic_score"] is None  # no placeholder path ran at all
    assert "dp5_model_unavailable" in block["dp5"]["reasons"]
    assert block["dp5"]["mode"] is None
    raw = Path(result.metadata["report_json"]).read_text(encoding="utf-8")
    assert '"dp5_probability": null' in raw


def test_workflow_dp5_load_failure_reports_unavailable(tmp_path: Path) -> None:
    """Load exception → ``unavailable`` + reason, not the placeholder sigmoid."""
    struct = _structure("candA", ["C", "H", "H", "H", "H"])
    spectrum = "C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"
    result = _run_workflow(
        tmp_path,
        [struct],
        spectrum,
        [40.0],
        error_model="goodman-legacy",
        dp5_available=True,
        load_side_effect=OSError("corrupt DP5 assets"),
    )
    assert result.status == "completed", result.error

    cand = _load_report(result)["candidates"][0]
    block = cand["probability"]
    assert block["dp5"]["status"] == "unavailable"
    assert block["dp5"]["probability"] is None
    assert cand["dp5_probability"] is None
    assert cand["dp5_diagnostic_score"] is None
    assert "dp5_model_load_failed" in block["dp5"]["reasons"]


def test_workflow_placeholder_config_writes_diagnostic_not_probability(
    tmp_path: Path,
) -> None:
    """Explicit placeholder mode: value goes to ``dp5_diagnostic_score`` only."""
    struct = _structure("candA", ["C", "H", "H", "H", "H"])
    spectrum = "C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"
    result = _run_workflow(
        tmp_path,
        [struct],
        spectrum,
        [40.0],
        error_model="placeholder-student-t",
        dp5_available=False,
    )
    assert result.status == "completed", result.error

    data = _load_report(result)
    cand = data["candidates"][0]
    block = cand["probability"]
    assert block["dp5"]["status"] == "placeholder"
    assert cand["dp5_probability"] is None  # never again a fake 0.5-style probability
    assert block["dp5"]["probability"] is None
    diag = cand["dp5_diagnostic_score"]
    assert diag is not None and math.isfinite(diag)
    assert block["dp5"]["reasons"]  # placeholder is explicitly named as a reason
    assert data["note"]  # placeholder warning fires off the typed dp5 status


def test_workflow_no_carbon_with_real_model_is_not_applicable(tmp_path: Path) -> None:
    """Valid evidence without ¹³C residuals → ``not_applicable``, model never called."""
    model = _FakeDP5Model(0.7)
    struct = _structure("candH", ["H", "H", "H", "H"])
    spectrum = "H: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"
    result = _run_workflow(
        tmp_path,
        [struct],
        spectrum,
        [40.0],
        error_model="goodman-legacy",
        dp5_available=True,
        load_result=model,
    )
    assert result.status == "completed", result.error

    cand = _load_report(result)["candidates"][0]
    block = cand["probability"]
    assert block["dp4"]["status"] == "valid"  # ranks on DP4 evidence
    assert block["dp5"]["status"] == "not_applicable"
    assert cand["dp5_probability"] is None
    assert block["dp5"]["probability"] is None
    assert cand["dp5_diagnostic_score"] is None
    assert "no_carbon_evidence" in block["dp5"]["reasons"]
    assert block["dp5"]["mode"] is None
    assert model.calls == []  # the DP5 model was never invoked for this candidate


def test_workflow_real_dp5_model_reports_valid_float(tmp_path: Path) -> None:
    """Real model loaded + assets present → ``valid`` with a float probability."""
    model = _FakeDP5Model(0.7)
    struct = _structure("candA", ["C", "H", "H", "H", "H"])
    spectrum = "C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"
    result = _run_workflow(
        tmp_path,
        [struct],
        spectrum,
        [40.0],
        error_model="goodman-legacy",
        dp5_available=True,
        load_result=model,
    )
    assert result.status == "completed", result.error

    cand = _load_report(result)["candidates"][0]
    block = cand["probability"]
    assert block["dp5"]["status"] == "valid"
    assert block["dp5"]["probability"] == pytest.approx(0.7)
    assert cand["dp5_probability"] == pytest.approx(0.7)
    assert cand["dp5_diagnostic_score"] is None  # diagnostic is placeholder-only
    # why-changed (todo 15): the single prebuilt conformer no longer takes
    # the averaged-residual shortcut — it runs the geometry-weighted
    # fallback with weight [1.0] (FCHL unreachable here: fchl_available is
    # False on the stand-in and the prebuilt conformer carries no geometry)
    assert block["dp5"]["mode"] == "fallback"
    assert cand["dp5_kernel"] is None
    assert model.weight_calls == [[1.0]]
    assert model.calls == []  # compute_dp5_goodman never ran for this path


def test_winner_ignores_placeholder_dp5_diagnostic() -> None:
    """A DP4 tie never breaks toward a placeholder candidate's diagnostic score."""
    placeholder = CandidateResult(
        index=0,
        label="placeholder",
        dp4_probability=0.5,
        dp5_probability=None,  # stays None — diagnostic_score is not rankable
        dp5_diagnostic_score=0.5,
        evidence=_valid_evidence(),
        probability=CandidateProbability(
            dp4=_dp4_result(probability=0.5),
            dp5=_dp5_result(status="placeholder", probability=None),
        ),
    )
    real = CandidateResult(
        index=1,
        label="real",
        dp4_probability=0.5,
        dp5_probability=0.9,
        evidence=_valid_evidence(),
        probability=CandidateProbability(
            dp4=_dp4_result(probability=0.5),
            dp5=ProbabilityResult(
                model_id="goodman-dp5",
                model_version="goodman-dp5",
                status="valid",
                probability=0.9,
                mode="fallback",
                calibration_status="goodman_kde",
            ),
        ),
    )
    report = NmrReport(candidates=[placeholder, real])
    assert report.winner is real  # tie broken by the real DP5, never 0.5 placeholder
    data = report.as_dict()
    assert data["candidates"][0]["dp5_probability"] is None  # type: ignore[index]
    assert data["candidates"][0]["dp5_diagnostic_score"] == 0.5  # type: ignore[index]


def test_report_note_reads_typed_dp5_state_with_prefix_fallback() -> None:
    # typed placeholder status → warning, even with a non-placeholder error_model
    typed = NmrReport(candidates=[_valid_candidate()], error_model="goodman-legacy")
    assert typed.as_dict()["note"]
    # typed state wins over the error_model prefix when both are present
    real_dp5 = ProbabilityResult(
        model_id="goodman-dp5",
        model_version="goodman-dp5",
        status="valid",
        probability=0.7,
        mode="fallback",
        calibration_status="goodman_kde",
    )
    valid = CandidateResult(
        index=0,
        label="real",
        dp4_probability=0.5,
        dp5_probability=0.7,
        evidence=_valid_evidence(),
        probability=CandidateProbability(dp4=_dp4_result(probability=0.5), dp5=real_dp5),
    )
    typed_real = NmrReport(candidates=[valid], error_model="placeholder-student-t")
    assert typed_real.as_dict()["note"] == ""
    # no typed state attached → legacy error_model prefix fallback
    legacy = NmrReport(
        candidates=[CandidateResult(index=0, label="legacy")],
        error_model="placeholder-student-t",
    )
    assert legacy.as_dict()["note"]
    legacy_real = NmrReport(
        candidates=[CandidateResult(index=0, label="legacy")],
        error_model="goodman-legacy",
    )
    assert legacy_real.as_dict()["note"] == ""


# ---------------------------------------------------------------------------
# todo 14: per-candidate immutable DP5 mode (G07) — no shared model state.
#
# BEFORE (raw capture: .omo/evidence/.../task-14-before-shared-mode-leak.txt):
# ``_compute_candidate_dp5`` returned a bare float and stage 7 read
# ``dp5_model.dp5_mode`` after each call. Shared mutable state leaked across
# candidates: the averaged-residual candidate recorded mode ``"fchl"`` or
# ``"fallback"`` purely depending on whether an FCHL candidate ran before it,
# and stage 8 reported only the last candidate's mode.
#
# AFTER: every call returns a frozen ``Dp5Outcome`` — swapping the call order
# never changes any candidate's mode; mixed FCHL/fallback runs summarise as
# ``"mixed"`` with an expanded per-candidate ``dp5_modes`` list.
# ---------------------------------------------------------------------------


class _PathDP5Model:
    """Goodman-DP5 stand-in recording which per-conformer path ran."""

    model_id = "goodman-dp5"

    def __init__(self, *, fchl_available: bool = True) -> None:
        self.fchl_available = fchl_available
        self.calls: list[str] = []
        self.weight_calls: list[list[float]] = []  # todo 15: spy on weights

    def probability(self, carbon_errors: list[float]) -> float:
        self.calls.append("averaged")
        return 0.6

    def probability_per_conformer(self, shifts, exp, weights) -> float:
        self.calls.append("fallback")
        self.weight_calls.append(list(weights))
        return 0.55

    def probability_per_conformer_fchl(self, shifts, exp, weights, reps) -> float:
        self.calls.append("fchl")
        self.weight_calls.append(list(weights))
        return 0.65


def _dp5_candidate(
    name: str,
    *,
    geometry: bool,
    n_conformers: int = 2,
    atom_label: str = "C1",
    carbon: bool = True,
) -> tuple[CandidateResult, Structure]:
    st = _structure(name, ["C", "H", "H", "H", "H"])
    assignments = []
    if carbon:
        assignments.append(
            Assignment(
                atom_label=atom_label,
                element="C",
                exp_ppm=40.0,
                calc_ppm=40.0,
                scaled_ppm=40.0,
                residual=0.1,
            )
        )
    conformers = []
    for i in range(n_conformers):
        kwargs: dict[str, object] = {}
        if geometry:
            kwargs = {
                "coordinates": np.array(
                    [
                        [0.0, 0.0, 0.0],
                        [1.0, 0.0, 0.0],
                        [0.0, 1.0, 0.0],
                        [0.0, 0.0, 1.0],
                        [-1.0, 0.0, 0.0],
                    ]
                ),
                "symbols": ["C", "H", "H", "H", "H"],
            }
        conformers.append(
            ConformerShielding(
                conformer_id=f"{name}_conf_{i:03d}",
                boltzmann_weight=1.0 / n_conformers,
                shieldings={0: {"symbol": "C", "isotropic": 148.0}},
                **kwargs,  # type: ignore[arg-type]
            )
        )
    cand = CandidateResult(
        index=0, label=name, assignments=assignments, conformer_shieldings=conformers
    )
    return cand, st


def test_compute_candidate_dp5_mode_is_order_invariant() -> None:
    """G07 acceptance: swapping the candidate call order changes no mode."""
    from acp.workflows.nmr import _compute_candidate_dp5

    fchl = _dp5_candidate("fchl-cand", geometry=True)
    fallback = _dp5_candidate("fallback-cand", geometry=False)
    # why-changed (todo 15): n_conformers=1 no longer takes the averaged
    # shortcut — it runs the geometry-weighted path. The averaged mode in
    # this order-invariance check now comes from the label-mismatch branch,
    # which is the only averaged producer left besides no-carbon.
    averaged = _dp5_candidate("averaged-cand", geometry=True, atom_label="C9")

    def run(order: list[tuple[CandidateResult, Structure]]) -> dict[str, object]:
        model = _PathDP5Model()
        return {
            cand.label: _compute_candidate_dp5(cand, st, NmrConfig(), model) for cand, st in order
        }

    forward = run([fchl, fallback, averaged])
    reverse = run([averaged, fallback, fchl])

    assert {label: out.mode for label, out in forward.items()} == {  # type: ignore[attr-defined]
        "fchl-cand": "fchl",
        "fallback-cand": "fallback",
        "averaged-cand": "averaged",
    }
    for label, out in forward.items():
        assert out.mode == reverse[label].mode  # type: ignore[attr-defined]
    # each outcome carries the probability of the path that actually ran
    assert forward["fchl-cand"].probability == pytest.approx(0.65)  # type: ignore[attr-defined]
    assert forward["fallback-cand"].probability == pytest.approx(0.55)  # type: ignore[attr-defined]
    assert forward["averaged-cand"].probability == pytest.approx(0.6)  # type: ignore[attr-defined]


def test_dp5_outcome_frozen_with_closed_mode_vocabulary() -> None:
    from acp.workflows.nmr import DP5_OUTCOME_MODES, Dp5Outcome

    outcome = Dp5Outcome(probability=0.5, mode="fchl", kernel="numpy")
    with pytest.raises(AttributeError):
        outcome.mode = "fallback"  # type: ignore[misc]
    assert DP5_OUTCOME_MODES == ("fchl", "fallback", "averaged")
    with pytest.raises(ValueError, match="mode"):
        Dp5Outcome(probability=0.5, mode="bogus")


def test_dp5_outcome_kernel_read_at_call_time_and_diagnostics_json_safe() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    fchl = _dp5_candidate("fchl-cand", geometry=True)
    fallback = _dp5_candidate("fallback-cand", geometry=False)
    model = _PathDP5Model()
    with patch("acp.nmr.fchl.kernel_backend", return_value="numpy"):
        out_fchl = _compute_candidate_dp5(fchl[0], fchl[1], NmrConfig(), model)
    out_fb = _compute_candidate_dp5(fallback[0], fallback[1], NmrConfig(), model)

    assert out_fchl.kernel == "numpy"
    assert out_fb.kernel == ""
    assert model.calls == ["fchl", "fallback"]

    for outcome in (out_fchl, out_fb):
        assert isinstance(outcome.diagnostics, tuple)
        payload = json.loads(json.dumps(list(outcome.diagnostics)))
        assert payload and all(isinstance(d, dict) for d in payload)
        assert set(payload[0]) >= {"n_conformers_used", "fchl_attempted", "fallback_reason"}
    assert out_fchl.diagnostics[0]["n_conformers_used"] == 2
    assert out_fchl.diagnostics[0]["fchl_attempted"] is True
    assert out_fchl.diagnostics[0]["fallback_reason"] is None
    assert out_fb.diagnostics[0]["fallback_reason"]


def test_compute_candidate_dp5_averaged_fallback_reasons() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    mismatch = _dp5_candidate("mismatch", geometry=True, atom_label="C9")
    no_carbon = _dp5_candidate("no-carbon", geometry=True, carbon=False)
    model = _PathDP5Model()

    out_mismatch = _compute_candidate_dp5(*mismatch, NmrConfig(), model)
    assert out_mismatch.mode == "averaged"
    assert out_mismatch.diagnostics[0]["fallback_reason"] == "label_mismatch"

    out_no_carbon = _compute_candidate_dp5(*no_carbon, NmrConfig(), model)
    assert out_no_carbon.mode == "averaged"
    assert out_no_carbon.probability == 0.0
    assert out_no_carbon.diagnostics[0]["fallback_reason"] == "no_carbon"
    # only the label-mismatch path invoked the model (averaged DP5);
    # the no-carbon shortcut returns 0.0 without calling it
    assert model.calls == ["averaged"]


def test_dp5_model_has_no_shared_mode_state() -> None:
    """G07: the model object carries no dp5_mode/fchl_kernel last-state attrs."""
    from acp.nmr.error_model import GoodmanDP5Model, dp5_model_available

    if not dp5_model_available():
        pytest.skip("Goodman DP5 model files not present")
    model = GoodmanDP5Model()
    assert not hasattr(model, "dp5_mode")
    assert not hasattr(model, "fchl_kernel")


def _multi_conformer_ensemble(
    structure: Structure,
    shieldings: dict[int, dict[str, str | float]],
    *,
    geometry: bool,
    n_conformers: int = 2,
) -> object:
    ens = StructureEnsemble(
        records=[
            StructureRecord(
                structure=structure, energy_hartree=-1.0, free_energy_hartree=-1.0, weight=1.0
            )
        ]
    )
    data = []
    for i in range(n_conformers):
        kwargs: dict[str, object] = {}
        if geometry:
            kwargs = {
                "coordinates": np.array(
                    [
                        [0.0, 0.0, 0.0],
                        [1.0, 0.0, 0.0],
                        [0.0, 1.0, 0.0],
                        [0.0, 0.0, 1.0],
                        [-1.0, 0.0, 0.0],
                    ]
                ),
                "symbols": list(structure.symbols),
            }
        data.append(
            ConformerShielding(
                conformer_id=f"{structure.id}_conf_{i:03d}",
                boltzmann_weight=1.0 / n_conformers,
                shieldings={int(k): dict(v) for k, v in shieldings.items()},
                **kwargs,  # type: ignore[arg-type]
            )
        )
    ens.data = data
    return ens


def _run_two_candidate_workflow(
    tmp_path: Path,
    *,
    geometry_a: bool,
    geometry_b: bool,
    delta_a: float = 40.0,
    delta_b: float = 40.0,
):
    from acp.workflows.nmr import run_nmr_analysis

    struct_a = _structure("candA", ["C", "H", "H", "H", "H"])
    struct_b = _structure("candB", ["C", "H", "H", "H", "H"])
    spectrum = "C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"
    ensembles = [
        _multi_conformer_ensemble(
            struct_a, _shieldings(list(struct_a.symbols), delta_a), geometry=geometry_a
        ),
        _multi_conformer_ensemble(
            struct_b, _shieldings(list(struct_b.symbols), delta_b), geometry=geometry_b
        ),
    ]
    shielding_results = [
        _shielding_result(_shieldings(list(st.symbols), delta))
        for st, delta in ((struct_a, delta_a), (struct_b, delta_b))
    ]
    model = _PathDP5Model()
    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch("acp.workflows.nmr.run_nmr_shielding", side_effect=shielding_results),
        patch("acp.workflows.nmr.dp5_model_available", return_value=True),
        patch("acp.workflows.nmr.load_dp5_model", return_value=model),
        patch("acp.nmr.fchl.kernel_backend", return_value="numpy"),
    ):
        reader = MagicMock()
        reader.read.side_effect = [struct_a, struct_b]
        reader_cls.return_value = reader
        result = run_nmr_analysis(
            input_sources=[struct_a.id, struct_b.id],
            spectrum=spectrum,
            output_dir=str(tmp_path),
            skip_conformers=True,
            prebuilt_ensembles=ensembles,  # type: ignore[arg-type]
            error_model="goodman-legacy",
        )
    return result, model


def _load_summary(result) -> dict:
    return json.loads(
        (Path(result.metadata["report_json"]).parent / "nmr_summary.json").read_text(
            encoding="utf-8"
        )
    )


def test_workflow_mixed_dp5_modes_summary_reports_mixed(tmp_path: Path) -> None:
    """FCHL candidate + fallback candidate → summary mode "mixed" + dp5_modes."""
    result, model = _run_two_candidate_workflow(tmp_path, geometry_a=True, geometry_b=False)
    assert result.status == "completed", result.error
    assert model.calls == ["fchl", "fallback"]

    report = _load_report(result)
    cand_a, cand_b = report["candidates"]
    assert cand_a["probability"]["dp5"]["status"] == "valid"
    assert cand_b["probability"]["dp5"]["status"] == "valid"
    assert cand_a["probability"]["dp5"]["mode"] == "fchl"
    assert cand_b["probability"]["dp5"]["mode"] == "fallback"
    assert cand_a["dp5_kernel"] == "numpy"
    assert cand_b["dp5_kernel"] is None

    summary = _load_summary(result)
    assert summary["dp5_mode"] == "mixed"
    assert summary["fchl_kernel"] == ""
    assert summary["dp5_modes"] == [
        {"index": 0, "mode": "fchl", "kernel": "numpy"},
        {"index": 1, "mode": "fallback", "kernel": None},
    ]


def test_workflow_single_dp5_mode_summary_stays_that_mode(tmp_path: Path) -> None:
    """All candidates on one path → summary keeps that mode (no "mixed")."""
    result, model = _run_two_candidate_workflow(tmp_path, geometry_a=True, geometry_b=True)
    assert result.status == "completed", result.error
    assert model.calls == ["fchl", "fchl"]

    report = _load_report(result)
    for cand in report["candidates"]:
        assert cand["probability"]["dp5"]["mode"] == "fchl"
        assert cand["dp5_kernel"] == "numpy"

    summary = _load_summary(result)
    assert summary["dp5_mode"] == "fchl"
    assert summary["fchl_kernel"] == "numpy"
    assert summary["dp5_modes"] == [
        {"index": 0, "mode": "fchl", "kernel": "numpy"},
        {"index": 1, "mode": "fchl", "kernel": "numpy"},
    ]


# ---------------------------------------------------------------------------
# todo 15: single/zero conformers on the geometry-weighted DP5 path (G07).
#
# BEFORE (raw capture: .omo/evidence/.../task-15-red-before.txt):
# the ``len(conformer_shifts) <= 1`` branch bundled two distinct situations
# into the averaged-residual shortcut — a SINGLE conformer called
# ``compute_dp5_goodman`` (mode "averaged", FCHL unreachable), and ZERO
# complete conformers returned a valid-looking averaged outcome that stage 7
# reported as ``valid``.
#
# AFTER: 0 complete conformers → ``status="invalid"`` + probability None
# (stage 7 maps it to the typed "invalid" ProbabilityStatus); exactly 1
# conformer runs the same geometry-weighted pipeline as the multi-conformer
# case with weight [1.0] — FCHL stays reachable.
# ---------------------------------------------------------------------------


def test_single_conformer_runs_geometry_weighted_fchl_with_unit_weight() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    single = _dp5_candidate("single-fchl", geometry=True, n_conformers=1)
    model = _PathDP5Model()
    with patch("acp.nmr.fchl.kernel_backend", return_value="numpy"):
        out = _compute_candidate_dp5(single[0], single[1], NmrConfig(), model)

    # BEFORE: model.calls == ["averaged"] — compute_dp5_goodman shortcut,
    # FCHL unreachable for a single conformer.
    assert model.calls == ["fchl"]
    assert model.weight_calls == [[1.0]]
    assert out.mode == "fchl"
    assert out.kernel == "numpy"
    assert out.status == "valid"
    assert out.probability == pytest.approx(0.65)
    assert out.diagnostics[0]["n_conformers_used"] == 1
    assert out.diagnostics[0]["fallback_reason"] is None


def test_single_conformer_without_geometry_runs_fallback_with_unit_weight() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    single = _dp5_candidate("single-fallback", geometry=False, n_conformers=1)
    model = _PathDP5Model()
    out = _compute_candidate_dp5(single[0], single[1], NmrConfig(), model)

    # BEFORE: model.calls == ["averaged"] (compute_dp5_goodman shortcut)
    assert model.calls == ["fallback"]
    assert model.weight_calls == [[1.0]]
    assert out.mode == "fallback"
    assert out.status == "valid"
    assert out.probability == pytest.approx(0.55)
    assert out.diagnostics[0]["n_conformers_used"] == 1


def test_zero_conformer_candidate_is_invalid_not_averaged() -> None:
    from acp.workflows.nmr import _compute_candidate_dp5

    zero = _dp5_candidate("zero-cand", geometry=True, n_conformers=0)
    model = _PathDP5Model()
    out = _compute_candidate_dp5(zero[0], zero[1], NmrConfig(), model)

    # BEFORE: a valid-looking averaged outcome (probability 0.6) — zero
    # conformers were silently treated as a legal DP5 input.
    assert out.probability is None
    assert out.status == "invalid"
    assert model.calls == []  # compute_dp5_goodman never ran
    assert out.diagnostics[0]["fallback_reason"] == "no_complete_conformers"
    assert out.diagnostics[0]["n_conformers_used"] == 0


def test_dp5_outcome_status_is_closed_and_invalid_carries_no_probability() -> None:
    from acp.workflows.nmr import DP5_OUTCOME_STATUSES, Dp5Outcome

    assert Dp5Outcome(probability=0.5, mode="fallback").status == "valid"
    assert DP5_OUTCOME_STATUSES == ("valid", "invalid")
    invalid = Dp5Outcome(status="invalid", probability=None, mode="averaged")
    assert invalid.probability is None
    with pytest.raises(ValueError, match="status"):
        Dp5Outcome(probability=0.5, mode="fallback", status="bogus")
    # no ambiguity: an invalid outcome may never carry a probability
    with pytest.raises(ValueError, match="invalid"):
        Dp5Outcome(probability=0.5, mode="averaged", status="invalid")


def test_stage7_maps_invalid_dp5_outcome_to_typed_invalid(tmp_path: Path) -> None:
    """Stage 7 propagates the outcome's invalid status — typed, null, unranked."""
    from acp.workflows.nmr import Dp5Outcome, run_nmr_analysis

    struct = _structure("candA", ["C", "H", "H", "H", "H"])
    spectrum = "C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"
    model = _FakeDP5Model(0.7)
    invalid_outcome = Dp5Outcome(
        status="invalid",
        probability=None,
        mode="averaged",
        diagnostics=(
            {
                "n_conformers_used": 0,
                "fchl_attempted": False,
                "fallback_reason": "no_complete_conformers",
            },
        ),
    )
    ensembles = [_ensemble(struct, _shieldings(list(struct.symbols), 40.0))]
    shielding_results = [_shielding_result(_shieldings(list(struct.symbols), 40.0))]
    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch("acp.workflows.nmr.run_nmr_shielding", side_effect=shielding_results),
        patch("acp.workflows.nmr.dp5_model_available", return_value=True),
        patch("acp.workflows.nmr.load_dp5_model", return_value=model),
        # seam: stage 7 is the consumer under test; the producer (0
        # conformers → invalid) is covered by the direct test above.
        patch("acp.workflows.nmr._compute_candidate_dp5", return_value=invalid_outcome),
    ):
        reader = MagicMock()
        reader.read.side_effect = [struct]
        reader_cls.return_value = reader
        result = run_nmr_analysis(
            input_sources=[struct.id],
            spectrum=spectrum,
            output_dir=str(tmp_path),
            skip_conformers=True,
            prebuilt_ensembles=ensembles,  # type: ignore[arg-type]
            error_model="goodman-legacy",
        )
    assert result.status == "completed", result.error

    cand = _load_report(result)["candidates"][0]
    block = cand["probability"]["dp5"]
    assert block["status"] == "invalid"  # typed ProbabilityStatus, not "valid"
    assert block["probability"] is None
    assert cand["dp5_probability"] is None  # JSON null — never ranked on DP5
    assert block["mode"] is None
    assert cand["dp5_kernel"] is None
    assert block["calibration_status"] == "not_evaluated"
    assert "no_complete_conformers" in block["reasons"]
    # invalid candidates never surface in the stage-8 mode aggregation
    summary = _load_summary(result)
    assert summary["dp5_modes"] == []


# ---------------------------------------------------------------------------
# todo 16: winner/ranking never influenced by invalid/unavailable/placeholder
# DP5 state (G05) + per-candidate typed statuses in the stage-8 summary.
#
# BEFORE (raw capture: .omo/evidence/.../task-16-before-tiebreak-stale-dp5.txt):
# ``_dp4_rank_key`` read the flat ``dp5_probability`` unconditionally — a
# stale float left on the flat field while the typed block said
# ``placeholder`` won a DP4 tie against an honest ``unavailable`` candidate
# (keys (0.5, 0.99) beat (0.5, -inf); winner = "placeholder-stale").
#
# AFTER: DP5 enters the tie-break only when ``probability.dp5.status ==
# "valid"`` AND the float is set; a full tie falls to the smallest index
# (explicit ``-index`` key, never raw input order).
# ---------------------------------------------------------------------------


def test_winner_tie_break_never_uses_non_valid_dp5() -> None:
    """Equal DP4: a placeholder diagnostic (even with a stale float) vs unavailable."""
    from acp.nmr.models import _dp4_rank_key

    unavailable = CandidateResult(
        index=0,
        label="unavailable",
        dp4_probability=0.5,
        dp5_probability=None,
        evidence=_valid_evidence(),
        probability=CandidateProbability(
            dp4=_dp4_result(probability=0.5),
            dp5=ProbabilityResult(
                model_id="goodman-dp5",
                model_version="goodman-dp5",
                status="unavailable",
                probability=None,
                mode=None,
                calibration_status="not_evaluated",
                reasons=("dp5_model_unavailable",),
            ),
        ),
    )
    stale = CandidateResult(
        index=1,
        label="placeholder-stale",
        dp4_probability=0.5,
        dp5_probability=0.99,  # stale flat float; the typed block says placeholder
        evidence=_valid_evidence(),
        probability=CandidateProbability(
            dp4=_dp4_result(probability=0.5),
            dp5=_dp5_result(status="placeholder", probability=None),
        ),
    )
    # BEFORE: winner was `placeholder-stale` — the stale 0.99 decided the tie
    key_unavailable = _dp4_rank_key(unavailable)
    key_stale = _dp4_rank_key(stale)
    assert key_unavailable[:2] == key_stale[:2] == (0.5, float("-inf"))
    assert (key_unavailable[2], key_stale[2]) == (0, -1)  # documented -index rule

    report = NmrReport(candidates=[unavailable, stale])
    assert report.winner is unavailable  # decided by index, never by the diagnostic
    # order invariance: reordering the candidate list cannot flip a full tie —
    # the -index key decides, not insertion order (and not the 0.99 float)
    swapped = NmrReport(candidates=[stale, unavailable])
    assert swapped.winner is unavailable
    assert _dp4_rank_key(stale)[:2] == (0.5, float("-inf"))  # the 0.99 never votes


def test_stale_dp4_float_with_non_valid_typed_status_never_ranks() -> None:
    """A typed dp4.status != valid excludes a candidate even with a legacy float."""
    stale_dp4 = CandidateResult(
        index=0,
        label="stale-dp4",
        dp4_probability=0.99,  # stale float slipped onto the flat field
        dp5_probability=None,
        evidence=_valid_evidence(),
        probability=CandidateProbability(
            dp4=_dp4_result(status="invalid", probability=None),
            dp5=_dp5_result(status="unavailable", probability=None),
        ),
    )
    good = CandidateResult(
        index=1,
        label="good",
        dp4_probability=0.1,
        evidence=_valid_evidence(),
        probability=CandidateProbability(
            dp4=_dp4_result(probability=0.1),
            dp5=_dp5_result(status="unavailable", probability=None),
        ),
    )
    report = NmrReport(candidates=[stale_dp4, good])
    assert stale_dp4 not in report.ranked_candidates
    assert report.winner is good  # never the fabricated 0.99
    assert report.dp4_ranking == "not_applicable"  # only one ranked candidate


def test_workflow_summary_carries_typed_statuses_per_candidate(tmp_path: Path) -> None:
    """Stage-8 summary exposes dp4_status/dp5_status next to evidence_status."""
    struct_a = _structure("candA", ["C", "H", "H", "H", "H"])
    struct_b = _structure("candB", ["C", "C", "C", *["H"] * 11])
    spectrum = "C: 40.0(C2)\nH: 4.0(H5), 3.0(H6), 1.0(H7), 0.0(H8)"
    result = _run_workflow(
        tmp_path,
        [struct_a, struct_b],
        spectrum,
        [40.0, 40.0],
        error_model="placeholder-student-t",
    )
    assert result.status == "completed", result.error

    summary = _load_summary(result)
    excluded, valid = summary["candidates"]
    assert excluded["evidence_status"] == "invalid"
    assert excluded["dp4_status"] == "invalid"
    assert excluded["dp5_status"] == "unavailable"
    assert excluded["exclusion_reasons"]
    assert excluded["dp4_probability"] is None  # null-never-0
    assert valid["evidence_status"] == "valid"
    assert valid["dp4_status"] == "valid"
    assert valid["dp5_status"] == "placeholder"
    assert valid["dp5_probability"] is None  # placeholder never becomes a probability
    # only the valid candidate ranks → winner present, ranking not applicable
    assert summary["winner"] is not None
    assert summary["winner"]["index"] == 1
    assert summary["dp4_ranking"] == "not_applicable"


def test_workflow_all_invalid_summary_has_no_winner_and_statuses(tmp_path: Path) -> None:
    """Failure QA: every candidate invalid → no winner, statuses + reasons visible."""
    struct_a = _structure("candA", ["C", "H", "H", "H", "H"])
    struct_b = _structure("candB", ["C", "H", "H", "H", "H"])
    # none of C9/H6..H9 exist on either candidate → zero matched signals each
    spectrum = "C: 40.0(C9)\nH: 4.0(H9), 3.0(H8), 1.0(H7), 0.0(H6)"
    result = _run_workflow(
        tmp_path,
        [struct_a, struct_b],
        spectrum,
        [40.0, 30.0],
        error_model="placeholder-student-t",
    )
    assert result.status == "completed", result.error
    assert result.metadata["winner"] is None

    summary = _load_summary(result)
    assert summary["winner"] is None  # never invented
    assert summary["dp4_ranking"] == "not_applicable"
    for entry in summary["candidates"]:
        assert entry["dp4_status"] == "invalid"
        assert entry["dp5_status"] == "unavailable"
        assert entry["exclusion_reasons"]
        assert entry["dp4_probability"] is None


def test_workflow_happy_winner_competes_only_among_valid_candidates(tmp_path: Path) -> None:
    """Happy QA: two valid candidates — statuses valid, winner is a valid one."""
    result, _model = _run_two_candidate_workflow(tmp_path, geometry_a=True, geometry_b=False)
    assert result.status == "completed", result.error

    summary = _load_summary(result)
    assert summary["dp4_ranking"] == "normal"  # two ranked candidates
    for entry in summary["candidates"]:
        assert entry["dp4_status"] == "valid"
        assert entry["dp5_status"] == "valid"
    assert summary["winner"] is not None
    winning = summary["candidates"][summary["winner"]["index"]]
    assert winning["dp4_status"] == "valid"
    assert winning["dp5_status"] == "valid"
    # equal DP4 (identical candidates) → the real valid DP5 decides: A 0.65 > B 0.55
    assert summary["winner"]["index"] == 0
