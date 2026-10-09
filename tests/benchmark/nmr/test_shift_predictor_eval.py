"""Acceptance suite for the ShiftPredictor screening-evaluation hooks (todo 57 / gap G17).

TDD acceptance for ``tests/benchmark/nmr/shift_predictor_eval.py``:

* the evaluation wires a todo-55 ``ShiftPredictor`` (the todo-56 DP5q stub is
  one implementation under test) into the todo-50 benchmark framework as a
  **screening/prioritization aid** — never a DP5 replacement, never a
  DP5q ~= DP5 equivalence claim;
* conclusions exist only over a declared independent evaluation set; without
  one the hook returns ``not_verified`` with explicit reasons (no numbers);
* miss-risk metrics: per-dataset false-drop rate + molecule-clustered
  bootstrap CI (todo-50 ``clustered_bootstrap_ci``) plus the speed/accuracy
  trade-off table (screening fraction vs retained-true rate vs cost proxy);
* out-of-domain / uncertain candidates produce typed escalation DECISION
  records (executed=False — the harness never starts QC);
* the typed screening manifest reuses the todo-50 provenance errors and the
  todo-53 layer loaders for external-layer anchors; misuse (missing
  predictor, non-independent set, invalid k) raises typed errors;
* deterministic canonical JSON — byte-identical across processes with pinned
  seed/now.

No QC subprocess is started anywhere in this file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from acp.nmr.dp5q_stub import (
    Dp5qStubParameterError,
    dp5q_stub_status,
    load_dp5q_stub_predictor,
)
from acp.nmr.shift_predictor import (
    GeometryRequirements,
    InvalidShiftPredictorError,
    PredictedShift,
    ShiftDistribution,
    ShiftModelProvenance,
    ShiftPrediction,
    ShiftPredictionRequest,
    ShiftPredictorError,
)
from tests.benchmark.nmr import (
    DEFAULT_BOOTSTRAP_SEED,
    NOT_VERIFIED,
    DatasetHashMismatchError,
    MissingProvenanceError,
    canonical_json_bytes,
    load_assigned_statistical,
)
from tests.benchmark.nmr.shift_predictor_eval import (
    ESCALATION_REASONS,
    SCREENING_AID_CLAIM,
    SCREENING_DATASET_SCHEMA,
    SCREENING_EVAL_SCHEMA,
    DatasetNotIndependentError,
    InvalidScreeningCutError,
    PredictorNotSuppliedError,
    ScreeningManifestError,
    load_screening_manifest,
    run_screening_evaluation,
    write_screening_evaluation,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_LAYER2_FIXTURE = _FIXTURES / "layer2_assigned_statistical.json"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PINNED_NOW = "2026-10-06T00:00:00+00:00"
_N_RESAMPLES = 100

_SCRIPTED_MODEL = ShiftModelProvenance(
    model_id="scripted-screening-fixture",
    version="1.0.0",
    calibration_status="uncalibrated",
    notes="deterministic test double; values are hand-authored, not calibrated",
)


# ---------------------------------------------------------------------------
# synthetic fixture helpers (mock only; no QC)
# ---------------------------------------------------------------------------


def _candidate_geometry(candidate_id: str, symbols: tuple[str, ...] = ("C",)) -> dict[str, Any]:
    """One predictor geometry whose atom uids encode the candidate id."""
    uids = [
        f"{candidate_id}:{symbol.lower()}{index}" for index, symbol in enumerate(symbols, start=1)
    ]
    return {
        "symbols": list(symbols),
        "coordinates": [[float(index), 0.0, 0.0] for index in range(len(symbols))],
        "atom_uids": uids,
        "level": "synthetic-fixture",
    }


def _signal(signal_id: str, observed_ppm: float) -> dict[str, Any]:
    return {"signal_id": signal_id, "observed_ppm": observed_ppm}


def _candidate(
    candidate_id: str,
    *,
    is_true_structure: bool,
    symbols: tuple[str, ...] = ("C",),
    signal_atoms: dict[str, str] | None = None,
    support: dict[str, Any] | None = None,
) -> dict[str, Any]:
    uid = f"{candidate_id}:{symbols[0].lower()}1"
    payload: dict[str, Any] = {
        "candidate_id": candidate_id,
        "is_true_structure": is_true_structure,
        "predictor_geometries": [_candidate_geometry(candidate_id, symbols)],
        "signal_atoms": {"13C": {"C1": uid} if signal_atoms is None else signal_atoms},
    }
    if support is not None:
        payload["support"] = support
    return payload


def _scripted_predictions() -> dict[str, dict[str, float]]:
    """Hand-authored per-uid shift predictions for the fixture (binary-exact)."""
    return {
        "13C": {
            "s1-true:c1": 10.5,
            "s1-decoy-od:c1": 11.5,
            "s1-decoy-high:c1": 12.0,
            "s2-true:c1": 23.0,
            "s2-decoy-a:c1": 20.5,
            "s2-decoy-b:c1": 21.0,
            "s3-true:c1": 30.5,
            "s3-decoy:c1": 30.0,
            "s4-c1:c1": 41.0,
            "s4-c2:c1": 40.5,
            # s4-c3 deliberately absent -> typed predictor refusal
        }
    }


def _scripted_stds() -> dict[str, dict[str, float | None]]:
    """Per-uid predictive std; ``None`` means "predictor supplied no uncertainty"."""
    return {
        "13C": {
            "s1-true:c1": 0.5,
            "s1-decoy-od:c1": 1.0,
            "s1-decoy-high:c1": 1.0,
            "s2-true:c1": 1.0,
            "s2-decoy-a:c1": 0.5,
            "s2-decoy-b:c1": 0.5,
            "s3-true:c1": 1.0,
            "s3-decoy:c1": 1.0,
            "s4-c1:c1": 1.0,
            "s4-c2:c1": None,
            "s4-c3:c1": None,
        }
    }


class _ScriptedPredictor:
    """Deterministic todo-55 predictor double keyed by explicit atom uids."""

    def __init__(
        self,
        *,
        shifts: dict[str, dict[str, float]] | None = None,
        stds: dict[str, dict[str, float | None]] | None = None,
    ) -> None:
        self._shifts = shifts if shifts is not None else _scripted_predictions()
        self._stds = stds if stds is not None else _scripted_stds()

    @property
    def model_provenance(self) -> ShiftModelProvenance:
        return _SCRIPTED_MODEL

    @property
    def geometry_requirements(self) -> GeometryRequirements:
        return GeometryRequirements(min_conformers=1, geometry_level="synthetic-fixture")

    def predict(self, request: ShiftPredictionRequest) -> ShiftPrediction:
        table = self._shifts.get(request.nucleus)
        if not table:
            raise ShiftPredictorError(
                f"scripted fixture has no table for nucleus {request.nucleus}"
            )
        shifts: dict[str, PredictedShift] = {}
        std_table = self._stds.get(request.nucleus, {})
        for uid in request.geometries[0].atom_uids:
            if uid not in table:
                continue
            shift = table[uid]
            std = std_table.get(uid)
            distribution = (
                ShiftDistribution(mean_ppm=shift, std_ppm=std) if std is not None else None
            )
            shifts[uid] = PredictedShift(shift_ppm=shift, distribution=distribution)
        if not shifts:
            raise ShiftPredictorError(
                f"scripted fixture predicted no atoms for nucleus {request.nucleus}"
            )
        return ShiftPrediction(
            nucleus=request.nucleus,
            model=self.model_provenance,
            geometry_requirements=self.geometry_requirements,
            shifts=shifts,
        )


def _scripted_predictor(**kwargs: Any) -> _ScriptedPredictor:
    return _ScriptedPredictor(**kwargs)


_SUPPORT_OUT_OF_DOMAIN: dict[str, Any] = {
    "n_train": 64,
    "contributing_neighbors": 0,
    "effective_neighbors": 0.0,
    "support_fraction": 0.0,
    "similarity_mass": 0.0,
    "threshold": 0.0,
}


def _fixture_manifest() -> dict[str, Any]:
    """Four items / three molecules / eleven candidates (superscripted 13C only)."""
    return {
        "schema": SCREENING_DATASET_SCHEMA,
        "evaluation_set": {
            "evaluation_set_id": "synthetic-screening-selftest-v1",
            "kind": "synthetic_labeled",
            "source": "authored in-repo for tests/benchmark/nmr (synthetic; no external data)",
            "license": "CC0-1.0 (synthetic test data authored in this repository)",
            "version": "1.0.0",
            "hash": "0" * 64,
            "independent_of_model": True,
            "independence_note": (
                "labels were never used to fit, calibrate or select the predictor; "
                "the synthetic set is an evaluation-only self-test"
            ),
            "distribution": {
                "note": "balanced by construction; machinery self-test, never a model claim",
                "balanced_by_construction": True,
            },
            "layer": None,
            "layer_manifest": None,
            "layer_dataset_hash": None,
        },
        "screening": {"cut_k": 1, "z_threshold": 2.5},
        "items": [
            {
                "item_id": "s1",
                "molecule_id": "mol-alpha",
                "true_structure_present": True,
                "true_structure_candidate_id": "s1-true",
                "experimental": {"13C": [_signal("C1", 10.0)]},
                "candidates": [
                    _candidate("s1-true", is_true_structure=True),
                    _candidate(
                        "s1-decoy-od",
                        is_true_structure=False,
                        support=dict(_SUPPORT_OUT_OF_DOMAIN),
                    ),
                    _candidate("s1-decoy-high", is_true_structure=False),
                ],
            },
            {
                "item_id": "s2",
                "molecule_id": "mol-beta",
                "true_structure_present": True,
                "true_structure_candidate_id": "s2-true",
                "experimental": {"13C": [_signal("C1", 20.0)]},
                "candidates": [
                    _candidate("s2-true", is_true_structure=True),
                    _candidate("s2-decoy-a", is_true_structure=False),
                    _candidate("s2-decoy-b", is_true_structure=False),
                ],
            },
            {
                "item_id": "s3",
                "molecule_id": "mol-gamma",
                "true_structure_present": True,
                "true_structure_candidate_id": "s3-true",
                "experimental": {"13C": [_signal("C1", 30.0)]},
                "candidates": [
                    _candidate("s3-true", is_true_structure=True),
                    _candidate("s3-decoy", is_true_structure=False),
                ],
            },
            {
                "item_id": "s4",
                "molecule_id": "mol-alpha",
                "true_structure_present": False,
                "true_structure_candidate_id": None,
                "experimental": {"13C": [_signal("C1", 40.0)]},
                "candidates": [
                    _candidate("s4-c1", is_true_structure=False),
                    _candidate("s4-c2", is_true_structure=False),
                    _candidate("s4-c3", is_true_structure=False),
                ],
            },
        ],
    }


def _resign(manifest: dict[str, Any]) -> None:
    from tests.benchmark.nmr import canonical_dataset_hash

    manifest["evaluation_set"]["hash"] = canonical_dataset_hash(manifest["items"])


def _write_manifest(tmp_path: Path, manifest: dict[str, Any]) -> Path:
    path = tmp_path / "screening_dataset.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def _signed_fixture(tmp_path: Path) -> Path:
    manifest = _fixture_manifest()
    _resign(manifest)
    return _write_manifest(tmp_path, manifest)


def _screening(payload: dict[str, Any]) -> dict[str, Any]:
    return payload["screening"]


def _ranking(item: dict[str, Any]) -> list[dict[str, Any]]:
    return item["ranking"]


def _item(payload: dict[str, Any], item_id: str) -> dict[str, Any]:
    matches = [entry for entry in _screening(payload)["items"] if entry["item_id"] == item_id]
    assert len(matches) == 1
    return matches[0]


def _decision(payload: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    matches = [
        entry
        for entry in _screening(payload)["escalation"]["decisions"]
        if entry["candidate_id"] == candidate_id
    ]
    assert len(matches) == 1, candidate_id
    return matches[0]


def _run(tmp_path: Path, predictor: Any = None, **kwargs: Any) -> dict[str, Any]:
    manifest = _signed_fixture(tmp_path)
    return run_screening_evaluation(
        _scripted_predictor() if predictor is None else predictor,
        manifest,
        n_resamples=_N_RESAMPLES,
        seed=DEFAULT_BOOTSTRAP_SEED,
        now=_PINNED_NOW,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# manifest loading / provenance discipline
# ---------------------------------------------------------------------------


def test_load_screening_manifest_roundtrip(tmp_path: Path):
    dataset = load_screening_manifest(_signed_fixture(tmp_path))
    assert dataset.evaluation_set.evaluation_set_id == "synthetic-screening-selftest-v1"
    assert dataset.evaluation_set.kind == "synthetic_labeled"
    assert dataset.evaluation_set.independent_of_model is True
    assert dataset.cut_k == 1
    assert dataset.z_threshold == 2.5
    assert [item.item_id for item in dataset.items] == ["s1", "s2", "s3", "s4"]
    assert len(dataset.items[0].candidates) == 3
    od = dataset.items[0].candidates[1]
    assert od.support is not None and od.support.out_of_domain is True


def test_manifest_missing_provenance_rejected(tmp_path: Path):
    manifest = _fixture_manifest()
    _resign(manifest)
    del manifest["evaluation_set"]["source"]
    with pytest.raises(MissingProvenanceError):
        load_screening_manifest(_write_manifest(tmp_path, manifest))


def test_manifest_hash_mismatch_rejected(tmp_path: Path):
    manifest = _fixture_manifest()
    _resign(manifest)
    manifest["items"][0]["candidates"][0]["candidate_id"] = "tampered"
    with pytest.raises(DatasetHashMismatchError):
        load_screening_manifest(_write_manifest(tmp_path, manifest))


def test_manifest_requires_independent_declaration(tmp_path: Path):
    for mutate in ("false", "missing_note"):
        manifest = _fixture_manifest()
        if mutate == "false":
            manifest["evaluation_set"]["independent_of_model"] = False
        else:
            manifest["evaluation_set"]["independence_note"] = "   "
        _resign(manifest)
        with pytest.raises(DatasetNotIndependentError):
            load_screening_manifest(_write_manifest(tmp_path, manifest))


def test_manifest_signal_mapping_must_resolve(tmp_path: Path):
    manifest = _fixture_manifest()
    manifest["items"][0]["candidates"][0]["signal_atoms"] = {"13C": {"C1": "missing-uid"}}
    _resign(manifest)
    with pytest.raises(ScreeningManifestError):
        load_screening_manifest(_write_manifest(tmp_path, manifest))
    manifest = _fixture_manifest()
    manifest["items"][0]["candidates"][0]["signal_atoms"] = {"13C": {"NOPE": "s1-true:c1"}}
    _resign(manifest)
    with pytest.raises(ScreeningManifestError):
        load_screening_manifest(_write_manifest(tmp_path, manifest))


def test_manifest_support_reuses_t38_out_of_domain_rule(tmp_path: Path):
    manifest = _fixture_manifest()
    manifest["items"][1]["candidates"][0]["support"] = {
        "n_train": 64,
        "contributing_neighbors": 3,
        "effective_neighbors": 2.5,
        "support_fraction": 0.0390625,
        "similarity_mass": 7.0,
        "threshold": 0.0,
    }
    _resign(manifest)
    dataset = load_screening_manifest(_write_manifest(tmp_path, manifest))
    support = dataset.items[1].candidates[0].support
    assert support is not None and support.out_of_domain is False
    # A declared flag contradicting the T38 rule is rejected, never trusted.
    manifest["items"][1]["candidates"][0]["support"]["out_of_domain"] = True
    _resign(manifest)
    with pytest.raises(ScreeningManifestError):
        load_screening_manifest(_write_manifest(tmp_path, manifest))


# ---------------------------------------------------------------------------
# ranking / survival
# ---------------------------------------------------------------------------


def test_screening_ranking_and_topk_survival(tmp_path: Path):
    payload = _run(tmp_path)
    assert payload["schema"] == SCREENING_EVAL_SCHEMA
    s1 = _item(payload, "s1")
    assert [entry["candidate_id"] for entry in _ranking(s1)] == [
        "s1-true",
        "s1-decoy-od",
        "s1-decoy-high",
    ]
    assert [entry["screen_score"] for entry in _ranking(s1)] == [1.0, 1.5, 2.0]
    assert s1["true_rank"] == 1 and s1["true_survives_top_k"] is True
    assert s1["false_drop"] is False
    s2 = _item(payload, "s2")
    assert [entry["candidate_id"] for entry in _ranking(s2)] == [
        "s2-decoy-a",
        "s2-decoy-b",
        "s2-true",
    ]
    assert s2["true_rank"] == 3 and s2["true_survives_top_k"] is False
    assert s2["false_drop"] is True
    s3 = _item(payload, "s3")
    assert [entry["candidate_id"] for entry in _ranking(s3)] == ["s3-decoy", "s3-true"]
    assert s3["true_rank"] == 2 and s3["true_survives_top_k"] is False
    s4 = _item(payload, "s4")
    assert s4["true_structure_present"] is False
    assert s4["true_rank"] is None and s4["true_survives_top_k"] is None
    assert s4["false_drop"] is None and s4["unmitigated_false_drop"] is None


def test_screening_tie_break_is_candidate_id_order(tmp_path: Path):
    manifest = _fixture_manifest()
    manifest["items"] = [manifest["items"][2]]
    item = manifest["items"][0]
    item["item_id"] = "tie"
    item["molecule_id"] = "mol-tie"
    # Both candidates predict exactly the observed shift -> identical z -> id tie-break.
    shifts = _scripted_predictions()
    shifts["13C"]["s3-true:c1"] = 30.0
    _resign(manifest)
    payload = run_screening_evaluation(
        _scripted_predictor(shifts=shifts),
        _write_manifest(tmp_path, manifest),
        n_resamples=_N_RESAMPLES,
        seed=DEFAULT_BOOTSTRAP_SEED,
        now=_PINNED_NOW,
    )
    tie_item = _item(payload, "tie")
    assert [entry["screen_score"] for entry in _ranking(tie_item)] == [0.0, 0.0]
    assert [entry["candidate_id"] for entry in _ranking(tie_item)] == ["s3-decoy", "s3-true"]


def test_cut_k_argument_overrides_manifest(tmp_path: Path):
    default = _run(tmp_path)
    assert _screening(default)["cut_k"] == 1
    overridden = _run(tmp_path, cut_k=2)
    assert _screening(overridden)["cut_k"] == 2
    assert _item(overridden, "s3")["true_survives_top_k"] is True
    assert _screening(overridden)["miss_risk"]["false_drop_rate"] == pytest.approx(1.0 / 3.0)


# ---------------------------------------------------------------------------
# miss-risk metrics (false drop + cluster bootstrap CI)
# ---------------------------------------------------------------------------


def test_false_drop_rate_and_ci_measured_deterministic(tmp_path: Path):
    payload = _run(tmp_path)
    miss = _screening(payload)["miss_risk"]
    assert miss["status"] == "measured"
    assert miss["n_items_with_true"] == 3
    assert miss["n_dropped"] == 2
    assert miss["false_drop_rate"] == pytest.approx(2.0 / 3.0)
    ci = miss["ci"]
    assert ci["point"] == pytest.approx(2.0 / 3.0)
    assert ci["n_clusters"] == 3 and ci["n_observations"] == 3
    assert ci["unit"] == "molecule" and ci["seed"] == DEFAULT_BOOTSTRAP_SEED
    assert ci["low"] <= ci["point"] <= ci["high"]
    assert miss["unmitigated_false_drop_rate"] == pytest.approx(1.0 / 3.0)
    assert miss["n_unmitigated_dropped"] == 1
    assert miss["unmitigated_ci"]["point"] == pytest.approx(1.0 / 3.0)
    repeated = _run(tmp_path)
    assert repeated == payload


def test_unmitigated_drop_excludes_escalated_true_structure(tmp_path: Path):
    k1 = _run(tmp_path, cut_k=1)
    assert _item(k1, "s2")["false_drop"] is True
    assert _item(k1, "s2")["unmitigated_false_drop"] is False  # escalated to DFT
    assert _item(k1, "s3")["unmitigated_false_drop"] is True  # not escalated
    k2 = _run(tmp_path, cut_k=2)
    assert _screening(k2)["miss_risk"]["unmitigated_false_drop_rate"] == 0.0
    assert _item(k2, "s2")["unmitigated_false_drop"] is False


def test_tradeoff_table_sweep(tmp_path: Path):
    rows = _screening(_run(tmp_path))["tradeoff"]["rows"]
    assert [row["k"] for row in rows] == [1, 2, 3]
    assert [row["primary"] for row in rows] == [True, False, False]
    assert [row["retained_true_rate"] for row in rows] == pytest.approx([1.0 / 3.0, 2.0 / 3.0, 1.0])
    assert [row["false_drop_rate"] for row in rows] == pytest.approx([2.0 / 3.0, 1.0 / 3.0, 0.0])
    assert rows[0]["screening_fraction"] == pytest.approx(7.0 / 11.0)
    assert rows[1]["screening_fraction"] == pytest.approx(3.0 / 11.0)
    assert rows[2]["screening_fraction"] == 0.0
    assert [row["cost_proxy"] for row in rows] == pytest.approx([7.0 / 11.0, 10.0 / 11.0, 1.0])
    assert rows[0]["n_true_retained"] == 1 and rows[0]["n_items_with_true"] == 3
    definition = _screening(_run(tmp_path))["tradeoff"]["cost_proxy_definition"]
    assert "escalat" in definition


# ---------------------------------------------------------------------------
# escalation decision records (typed, never executed)
# ---------------------------------------------------------------------------


def test_escalation_out_of_domain_support_decision(tmp_path: Path):
    escalation = _screening(_run(tmp_path))["escalation"]
    assert escalation["status"] == "measured"
    assert escalation["executed"] is False
    assert escalation["z_threshold"] == 2.5
    assert escalation["min_effective_neighbors"] == 1.0
    decision = _decision(_run(tmp_path), "s1-decoy-od")
    assert decision["escalated"] is True
    assert decision["reasons"] == ["support_out_of_domain"]
    assert decision["action"] == "escalate_to_dft_path"
    assert decision["executed"] is False
    assert decision["criteria"] == {"z_threshold": 2.5, "min_effective_neighbors": 1.0}
    assert "never executes" in decision["note"]


def test_escalation_reasons_cover_uncertainty_and_z_threshold(tmp_path: Path):
    payload = _run(tmp_path)
    uncertain = _decision(payload, "s4-c2")
    assert uncertain["reasons"] == ["uncertainty_missing"]
    assert uncertain["escalated"] is True
    high_z = _decision(payload, "s2-true")
    assert high_z["reasons"] == ["residual_z_above_threshold"]
    safe = _decision(payload, "s3-true")
    assert safe["escalated"] is False and safe["reasons"] == []
    assert safe["action"] == "proceed_with_screening_aid"
    assert safe["executed"] is False
    assert set(ESCALATION_REASONS) >= {
        "support_out_of_domain",
        "uncertainty_missing",
        "residual_z_above_threshold",
        "predictor_refused",
    }


def test_predictor_refusal_recorded_not_crash(tmp_path: Path):
    payload = _run(tmp_path)
    refusal_decision = _decision(payload, "s4-c3")
    assert refusal_decision["reasons"] == ["predictor_refused"]
    assert refusal_decision["escalated"] is True
    s4 = _item(payload, "s4")
    refusals = s4["predictor_refusals"]
    assert len(refusals) == 1
    assert refusals[0]["nucleus"] == "13C"
    assert refusals[0]["error_type"] == "ShiftPredictorError"
    ranking = _ranking(s4)
    refused_entry = [entry for entry in ranking if entry["candidate_id"] == "s4-c3"][0]
    assert refused_entry["screen_status"] == "predictor_refused"
    assert refused_entry["screen_score"] is None
    assert refused_entry["rank"] == 3


# ---------------------------------------------------------------------------
# not_verified discipline + misuse errors
# ---------------------------------------------------------------------------


def test_not_verified_without_evaluation_set(tmp_path: Path):
    payload = run_screening_evaluation(
        _scripted_predictor(), None, n_resamples=_N_RESAMPLES, now=_PINNED_NOW
    )
    assert payload["schema"] == SCREENING_EVAL_SCHEMA
    assert payload["evaluation_set"] is None
    screening = _screening(payload)
    assert screening["status"] == "not_verified"
    assert screening["reasons"]
    assert all(reason.startswith(NOT_VERIFIED) for reason in screening["reasons"])
    for block in ("miss_risk", "escalation", "tradeoff"):
        assert screening[block]["status"] == "not_verified"
        assert screening[block]["reasons"]
        assert all(reason.startswith(NOT_VERIFIED) for reason in screening[block]["reasons"])
    assert screening["miss_risk"]["false_drop_rate"] is None
    assert screening["miss_risk"]["ci"] is None
    assert screening["escalation"]["decisions"] == []
    assert screening["tradeoff"]["rows"] == []
    assert screening["items"] == []
    assert payload["summary"]["n_items"] == 0
    assert payload["predictor"]["model_provenance"]["model_id"] == "scripted-screening-fixture"


def test_not_verified_payload_is_canonical_and_claim_present(tmp_path: Path):
    payload = run_screening_evaluation(
        _scripted_predictor(), None, n_resamples=_N_RESAMPLES, now=_PINNED_NOW
    )
    path = write_screening_evaluation(payload, tmp_path / "not-verified.json")
    assert path.read_bytes() == canonical_json_bytes(payload)
    assert payload["claim"] == SCREENING_AID_CLAIM


def test_predictor_missing_raises_typed_error(tmp_path: Path):
    manifest = _signed_fixture(tmp_path)
    with pytest.raises(PredictorNotSuppliedError):
        run_screening_evaluation(None, manifest, n_resamples=_N_RESAMPLES, now=_PINNED_NOW)
    with pytest.raises(InvalidShiftPredictorError):
        run_screening_evaluation(
            types.SimpleNamespace(), manifest, n_resamples=_N_RESAMPLES, now=_PINNED_NOW
        )


def test_invalid_cut_k_raises_typed_error(tmp_path: Path):
    manifest = _signed_fixture(tmp_path)
    for bad_k in (0, -1, True, "2", 4):
        with pytest.raises(InvalidScreeningCutError):
            run_screening_evaluation(
                _scripted_predictor(),
                manifest,
                cut_k=bad_k,  # type: ignore[arg-type]
                n_resamples=_N_RESAMPLES,
                now=_PINNED_NOW,
            )


# ---------------------------------------------------------------------------
# honesty discipline (non-equivalence)
# ---------------------------------------------------------------------------


def test_claim_and_scope_lock_screening_aid_boundary(tmp_path: Path):
    payload = _run(tmp_path)
    claim = payload["claim"]
    assert claim == SCREENING_AID_CLAIM
    assert "screening/prioritization aid" in claim
    assert "NOT a DP5 replacement" in claim
    assert "NOT evidence" in claim and "equivalent" in claim
    assert "independent evaluation set" in claim
    scope = payload["scope"]
    assert scope == {
        "role": "screening_aid_priority_ranking",
        "dp5_replacement": False,
        "dp5_equivalence_claim": False,
        "executes_qc": False,
        "conclusions_require_independent_evaluation_set": True,
    }
    assert payload["predictor"]["model_provenance"]["calibration_status"] == "uncalibrated"


# ---------------------------------------------------------------------------
# DP5q stub (todo 56) wiring
# ---------------------------------------------------------------------------


def _stub_predictor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, std: float = 0.5):
    module = types.ModuleType("dp5q")
    module.__version__ = "9.9.9-test"
    monkeypatch.setattr("acp.nmr.dp5q_stub._import_optional_runtime", lambda: module, raising=True)
    params = tmp_path / "dp5q_params.json"
    params.write_text(
        json.dumps(
            {
                "model_id": "dp5q-stub",
                "version": "0.0.0-stub",
                "nuclei": {"13C": {"C": {"mean_ppm": 10.0, "std_ppm": std}}},
            }
        ),
        encoding="utf-8",
    )
    return load_dp5q_stub_predictor(parameters_path=params)


def _stub_manifest(tmp_path: Path, *, elements: tuple[str, ...] = ("C",)) -> Path:
    candidate = _candidate("stub-candidate", is_true_structure=True, symbols=elements)
    manifest = _fixture_manifest()
    manifest["items"] = [
        {
            "item_id": "stub-item",
            "molecule_id": "stub-mol",
            "true_structure_present": True,
            "true_structure_candidate_id": "stub-candidate",
            "experimental": {"13C": [_signal("C1", 10.5)]},
            "candidates": [candidate],
        }
    ]
    _resign(manifest)
    return _write_manifest(tmp_path, manifest)


def test_dp5q_stub_wiring_and_provenance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    predictor = _stub_predictor(monkeypatch, tmp_path)
    payload = run_screening_evaluation(
        predictor,
        _stub_manifest(tmp_path),
        n_resamples=_N_RESAMPLES,
        now=_PINNED_NOW,
    )
    provenance = payload["predictor"]["model_provenance"]
    assert provenance["model_id"] == "dp5q-stub"
    assert provenance["version"] == "0.0.0-stub"
    assert provenance["calibration_status"] == "uncalibrated"
    item = _item(payload, "stub-item")
    assert item["true_survives_top_k"] is True
    entry = _ranking(item)[0]
    assert entry["candidate_id"] == "stub-candidate"
    assert entry["screen_score"] == pytest.approx(1.0)  # |10.5 - 10.0| / 0.5
    assert entry["n_uncertainty_weighted_signals"] == 1
    status = dp5q_stub_status(parameters_path=tmp_path / "dp5q_params.json")
    assert status.available is True and status.parameters_present is True


def test_dp5q_stub_refusal_is_typed_and_escalated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    predictor = _stub_predictor(monkeypatch, tmp_path)
    payload = run_screening_evaluation(
        predictor,
        _stub_manifest(tmp_path, elements=("H",)),
        n_resamples=_N_RESAMPLES,
        now=_PINNED_NOW,
    )
    item = _item(payload, "stub-item")
    assert item["predictor_refusals"]
    assert item["predictor_refusals"][0]["error_type"] == Dp5qStubParameterError.__name__
    assert item["true_survives_top_k"] is False
    assert item["false_drop"] is True
    decision = _decision(payload, "stub-candidate")
    assert decision["reasons"] == ["predictor_refused"] and decision["escalated"] is True


# ---------------------------------------------------------------------------
# external-layer anchor (todo-53 loaders) + coverage gaps
# ---------------------------------------------------------------------------


def _external_manifest() -> dict[str, Any]:
    layer = load_assigned_statistical(_LAYER2_FIXTURE)[0]
    candidate = _candidate("acetone-true", is_true_structure=True)
    manifest = _fixture_manifest()
    manifest["evaluation_set"] = {
        "evaluation_set_id": layer.dataset_id,
        "kind": "external_layer",
        "source": "external layer-2 self-test fixture (provenance anchored via the todo-53 loader)",
        "license": layer.license,
        "version": layer.version,
        "hash": "0" * 64,
        "independent_of_model": True,
        "independence_note": "evaluation-only screening inputs for the loaded layer dataset",
        "distribution": {
            "note": "layer fixture is a loader self-test; never a realistic prevalence",
            "balanced_by_construction": True,
        },
        "layer": layer.layer,
        "layer_manifest": str(_LAYER2_FIXTURE.relative_to(_REPO_ROOT)),
        "layer_dataset_hash": layer.content_hash,
    }
    manifest["items"] = [
        {
            "item_id": "acetone-item",
            "molecule_id": "acetone",
            "true_structure_present": True,
            "true_structure_candidate_id": "acetone-true",
            "experimental": {"13C": [_signal("C1", 10.5)]},
            "candidates": [candidate],
        }
    ]
    _resign(manifest)
    return manifest


def test_external_layer_anchor_validates_against_t53_loader(tmp_path: Path):
    manifest = _external_manifest()
    dataset = load_screening_manifest(_write_manifest(tmp_path, manifest))
    assert dataset.evaluation_set.kind == "external_layer"
    assert dataset.evaluation_set.layer == "assigned_statistical"
    assert (
        dataset.evaluation_set.layer_dataset_hash
        == load_assigned_statistical(_LAYER2_FIXTURE)[0].content_hash
    )
    # Wrong anchor hash -> typed refusal.
    manifest["evaluation_set"]["layer_dataset_hash"] = "f" * 64
    with pytest.raises(DatasetHashMismatchError):
        load_screening_manifest(_write_manifest(tmp_path, manifest))
    # Item ids must correspond to the loaded external dataset.
    manifest = _external_manifest()
    manifest["items"][0]["item_id"] = "not-acetone-item"
    _resign(manifest)
    with pytest.raises(ScreeningManifestError):
        load_screening_manifest(_write_manifest(tmp_path, manifest))


def test_coverage_gap_counts_uncovered_signals(tmp_path: Path):
    manifest = _fixture_manifest()
    item = manifest["items"][0]
    item["experimental"]["13C"].append(_signal("C2", 11.0))
    # Every candidate maps both signals but only C1 has a predictor entry:
    # C2 stays uncovered while the candidate remains scored on C1.
    for candidate in item["candidates"]:
        candidate_id = candidate["candidate_id"]
        candidate["predictor_geometries"] = [_candidate_geometry(candidate_id, ("C", "C"))]
        candidate["signal_atoms"] = {
            "13C": {"C1": f"{candidate_id}:c1", "C2": f"{candidate_id}:c2"}
        }
    _resign(manifest)
    payload = run_screening_evaluation(
        _scripted_predictor(),
        _write_manifest(tmp_path, manifest),
        n_resamples=_N_RESAMPLES,
        now=_PINNED_NOW,
    )
    entry = [
        entry for entry in _ranking(_item(payload, "s1")) if entry["candidate_id"] == "s1-true"
    ][0]
    assert entry["n_scored_signals"] == 1
    assert entry["n_uncovered_signals"] == 1
    assert entry["screen_score"] == 1.0


# ---------------------------------------------------------------------------
# determinism (canonical JSON; byte-identical across processes)
# ---------------------------------------------------------------------------


def test_write_screening_evaluation_atomic_canonical(tmp_path: Path):
    payload = _run(tmp_path)
    path = write_screening_evaluation(payload, tmp_path / "screening.json")
    assert path.read_bytes() == canonical_json_bytes(payload)


_CROSS_PROCESS_SCRIPT = """
import sys

from tests.benchmark.nmr.shift_predictor_eval import (
    run_screening_evaluation,
    write_screening_evaluation,
)
from tests.benchmark.nmr.test_shift_predictor_eval import _scripted_predictor

payload = run_screening_evaluation(
    _scripted_predictor(),
    sys.argv[1],
    n_resamples=100,
    seed=20261006,
    now="2026-10-06T00:00:00+00:00",
)
write_screening_evaluation(payload, sys.argv[2])
"""


def test_screening_payload_byte_identical_across_processes(tmp_path: Path):
    manifest_path = _signed_fixture(tmp_path)
    outputs: list[bytes] = []
    for hash_seed in ("1", "98765"):
        destination = tmp_path / f"screening-{hash_seed}.json"
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hash_seed
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(_REPO_ROOT / "src"), env.get("PYTHONPATH", "")) if part
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                _CROSS_PROCESS_SCRIPT,
                str(manifest_path),
                str(destination),
            ],
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(destination.read_bytes())
    assert outputs[0] == outputs[1]
    in_process = run_screening_evaluation(
        _scripted_predictor(),
        manifest_path,
        n_resamples=100,
        seed=DEFAULT_BOOTSTRAP_SEED,
        now=_PINNED_NOW,
    )
    assert outputs[0] == canonical_json_bytes(in_process)
