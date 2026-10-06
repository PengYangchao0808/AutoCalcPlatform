"""Labeled spectra fixtures + metrics harness acceptance (todo 48 / G10).

The benchmark interface (``tests/nmr_spectra_benchmark.py``) consumes the
committed fixtures under ``tests/fixtures/nmr/`` and the real Bruker fixture
under ``tests/fixtures/bruker_real_group_delay/`` through the production
processors, and emits a deterministic METRICS JSON. This module asserts:

* the metrics schema + per-fixture provenance (id/category/source/hash);
* category coverage — every todo-48 category is represented, with different
  nuclei, kinds and gating outcomes (no single synthetic peak stand-in);
* binding precision/recall values on the SYNTHETIC layer (the acceptance
  layer);
* the explicit rejection/gating outcomes (2D rejected, unphased failed,
  digital filter degraded, solvent excluded, overlap flagged, same-nucleus
  duplicates never concatenated implicitly);
* the REAL layer is measured but explicitly ``NOT_VERIFIED`` (three-state
  rule) — the pipeline does not recover the TopSpin reference positions;
* nmrglue-gated layers skip explicitly when the capability is absent;
* deterministic re-runs and JSON persistence.

Fixtures invoke no QC binaries; only pure spectrum processing runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import NOT_VERIFIED
from tests.nmr_spectra_benchmark import (
    FIXTURES_ROOT,
    GATING_OUTCOMES,
    MANIFEST_PATH,
    REQUIRED_CATEGORIES,
    SCHEMA,
    canonical_json_bytes,
    load_manifest,
    nmrglue_available,
    run_benchmark,
    write_metrics,
)

_AVAILABLE, _REASON = nmrglue_available()
requires_nmrglue = pytest.mark.skipif(not _AVAILABLE, reason=_REASON)

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = FIXTURES_ROOT / "nmr" / "generate_fixtures.py"


@pytest.fixture(scope="module")
def metrics() -> dict[str, Any]:
    return run_benchmark()


def _by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in payload["fixtures"]}


def _annotation_targets(entry: dict[str, Any]) -> list[dict[str, Any]]:
    annotations = entry.get("annotations") or {}
    return list(annotations.get("resonances", ())) + list(annotations.get("multiplets", ()))


# ---------------------------------------------------------------------------
# schema + provenance
# ---------------------------------------------------------------------------


def test_metrics_schema_and_summary(metrics: dict[str, Any]) -> None:
    manifest = load_manifest()
    assert metrics["schema"] == SCHEMA
    assert set(metrics) == {
        "schema",
        "manifest",
        "nmrglue",
        "tolerances",
        "summary",
        "fixtures",
    }
    assert metrics["manifest"]["schema"] == manifest["schema"]
    assert len(metrics["manifest"]["sha256"]) == 64
    assert metrics["tolerances"] == manifest["tolerances"]
    assert metrics["tolerances"] == {
        "carbon_resonance_match_ppm": 0.05,
        "proton_multiplet_position_ppm": 0.05,
    }
    assert isinstance(metrics["nmrglue"]["available"], bool)
    assert metrics["nmrglue"]["reason"] is None or isinstance(metrics["nmrglue"]["reason"], str)
    summary = metrics["summary"]
    assert summary["n_fixtures"] == len(manifest["fixtures"]) == len(metrics["fixtures"])
    assert set(summary["categories"]) == set(REQUIRED_CATEGORIES)
    assert set(summary["layers"]) == {"synthetic", "real"}
    assert set(summary["statuses"]) <= {"measured", "not_verified"}
    assert set(summary["verification"]) <= {"verified", "not_verified"}
    assert set(summary["gating_outcomes"]) <= set(GATING_OUTCOMES)
    assert summary["gating_outcomes"]["gate_failed"] >= 1
    assert summary["gating_outcomes"]["rejected_not_1d"] >= 1
    assert summary["gating_outcomes"]["gate_degraded"] >= 1


def test_every_fixture_has_provenance(metrics: dict[str, Any]) -> None:
    for entry in metrics["fixtures"]:
        identifier = entry["id"]
        assert entry["category"] in REQUIRED_CATEGORIES, identifier
        assert entry["layer"] in ("synthetic", "real"), identifier
        assert entry["kind"] in (
            "bruker_experiment",
            "bruker_tree",
            "processed_spectrum",
            "not_1d",
        ), identifier
        assert entry["source"], identifier
        assert entry["files"], identifier
        for record in entry["files"]:
            assert record["bytes"] > 0, identifier
            assert len(record["sha256"]) == 64, identifier
            assert (FIXTURES_ROOT / entry["path"] / record["path"]).exists() or (
                FIXTURES_ROOT / entry["path"]
            ).is_file(), identifier
        assert len(entry["sha256"]) == 64, identifier
        assert entry["status"] in ("measured", "not_verified"), identifier
        assert entry["verification"]["status"] in ("verified", "not_verified"), identifier
        outcome = entry["gating"]["outcome"]
        assert outcome is None or outcome in GATING_OUTCOMES, identifier
        if entry["status"] == "not_verified":
            assert entry["verification"]["reasons"], identifier
        for reason in entry["verification"]["reasons"]:
            assert NOT_VERIFIED in reason, identifier
        if entry["verification"]["status"] == "not_verified":
            assert entry["verification"]["reasons"], identifier


def test_fixture_digest_is_recomputable(metrics: dict[str, Any]) -> None:
    import hashlib

    for entry in metrics["fixtures"]:
        digest = hashlib.sha256()
        for record in entry["files"]:
            digest.update(f"{record['path']}:{record['sha256']}\n".encode())
        assert digest.hexdigest() == entry["sha256"], entry["id"]


def test_category_coverage_is_diverse(metrics: dict[str, Any]) -> None:
    by_category: dict[str, list[dict[str, Any]]] = {}
    for entry in metrics["fixtures"]:
        by_category.setdefault(entry["category"], []).append(entry)
    for category in REQUIRED_CATEGORIES:
        assert by_category.get(category), f"category carries no fixture: {category}"
    assert len(_by_id(metrics)) == len(metrics["fixtures"]) >= 10
    kinds = {entry["kind"] for entry in metrics["fixtures"]}
    assert kinds == {"bruker_experiment", "bruker_tree", "processed_spectrum", "not_1d"}
    nuclei = {
        entry["nucleus"] for entry in metrics["fixtures"] if entry["kind"] == "bruker_experiment"
    }
    assert {"1H", "13C"} <= nuclei
    layers = {entry["layer"] for entry in metrics["fixtures"]}
    assert layers == {"synthetic", "real"}
    annotation_fixtures = [entry for entry in metrics["fixtures"] if _annotation_targets(entry)]
    targets = sum(len(_annotation_targets(entry)) for entry in annotation_fixtures)
    assert targets >= 15, "annotations look like a single-peak stand-in"
    assert len([e for e in annotation_fixtures if len(_annotation_targets(e)) >= 3]) >= 4
    centers = {
        round(round(float(target.get("position_ppm", target.get("center_ppm"))), 2), 2)
        for entry in annotation_fixtures
        for target in _annotation_targets(entry)
    }
    assert len(centers) >= 12


# ---------------------------------------------------------------------------
# synthetic layer binding metrics
# ---------------------------------------------------------------------------


@requires_nmrglue
def test_synthetic_layer_binding_metrics(metrics: dict[str, Any]) -> None:
    by_id = _by_id(metrics)

    phase = by_id["phase_deviation_proton"]
    assert phase["processing"]["gate_status"] == "ok"
    assert phase["processing"]["phase_method"] != "unphased"
    assert phase["metrics"]["kind"] == "multiplet_grouping"
    assert phase["metrics"]["precision"] == 1.0
    assert phase["metrics"]["recall"] == 1.0
    assert phase["metrics"]["f1"] == 1.0
    assert phase["metrics"]["atom_count_accuracy"] == 1.0

    filt = by_id["digital_filter_proton"]
    assert filt["gating"]["outcome"] == "gate_degraded"
    assert filt["gating"]["reasons"] == ["digital_filter_unverified"]
    assert filt["processing"]["digital_filter"]["status"] == "not_compensated"
    assert filt["processing"]["digital_filter"]["group_delay_points"] == pytest.approx(67.986)
    assert filt["processing"]["digital_filter"]["dspfvs"] == 12
    assert filt["metrics"]["f1"] == 1.0

    solvent = by_id["solvent_large_carbon"]
    assert solvent["metrics"]["kind"] == "resonance_precision_recall"
    assert solvent["metrics"]["precision"] == 1.0
    assert solvent["metrics"]["recall"] == 1.0
    assert solvent["metrics"]["spurious_resonances"] == []
    assert any(
        assessment["status"] == "excluded" and assessment["solvent"] == "CDCl3"
        for assessment in solvent["metrics"]["solvent_assessments"]
    )
    assert all(
        abs(resonance["shift_ppm"] - 77.16) > 0.3 for resonance in solvent["metrics"]["resonances"]
    )

    impurity = by_id["impurity_carbon"]
    assert impurity["metrics"]["recall"] == 1.0
    assert impurity["metrics"]["precision"] < 1.0
    assert len(impurity["metrics"]["spurious_resonances"]) >= 2

    low_c = by_id["low_snr_carbon"]
    assert low_c["metrics"]["recall"] == 1.0
    assert any("low_snr" in resonance["flags"] for resonance in low_c["metrics"]["resonances"])
    assert "low_snr" in {
        flag for resonance in low_c["metrics"]["resonances"] for flag in resonance["flags"]
    }

    low_h = by_id["low_snr_proton"]
    assert low_h["metrics"]["f1"] == 1.0
    assert any(
        "low_snr" in multiplet["uncertainty_reasons"]
        for multiplet in low_h["metrics"]["multiplets"]
    )
    assert low_h["metrics"]["constraints"]["total_hydrogens"] == 10
    assert low_h["metrics"]["constraints"]["total_hydrogens_resolved"] == 10
    assert low_h["metrics"]["constraints"]["total_constraint_applied"] is True

    overlap = by_id["overlap_proton"]
    assert overlap["metrics"]["overlap_regions"]
    assert all(
        region["kind"] in ("ambiguous_split", "interleaved", "near_overlap")
        for region in overlap["metrics"]["overlap_regions"]
    )
    assert any(region["resolved"] is False for region in overlap["metrics"]["overlap_regions"])
    assert 0.0 < overlap["metrics"]["f1"] <= 1.0

    for identifier in (
        "phase_deviation_proton",
        "digital_filter_proton",
        "solvent_large_carbon",
        "impurity_carbon",
        "low_snr_carbon",
        "low_snr_proton",
        "overlap_proton",
    ):
        entry = by_id[identifier]
        assert entry["status"] == "measured"
        assert entry["verification"]["status"] == "verified"
        assert entry["metrics"] is not None


# ---------------------------------------------------------------------------
# rejection / gating outcomes
# ---------------------------------------------------------------------------


def test_two_d_ser_is_rejected_everywhere(metrics: dict[str, Any]) -> None:
    entry = _by_id(metrics)["two_d_ser"]
    assert entry["gating"]["outcome"] == "rejected_not_1d"
    assert entry["gating"]["reasons"] == ["ser_file"]
    assert entry["processing"]["not_1d"]["ser_experiment"] == "ser_file"
    assert entry["processing"]["not_1d"]["proton_1d"] is None
    assert entry["tree_rejection"]["error"] == "Not1DExperimentError"
    assert entry["tree_rejection"]["reason"] == "ser_file"
    assert entry["explicit_selection_rejection"]["error"] == "Not1DExperimentError"
    assert entry["explicit_selection_rejection"]["reason"] == "ser_file"
    dispositions = {
        record["label"]: (record["status"], record["reason"])
        for record in entry["selection"]["default"]["experiments"]
    }
    assert dispositions == {
        "proton_1d": ("selected", None),
        "ser_experiment": ("rejected", "ser_file"),
    }


def test_unphased_gate_fails_and_processor_refuses(metrics: dict[str, Any]) -> None:
    entry = _by_id(metrics)["phase_unphased_gate"]
    assert entry["status"] == "measured"
    assert entry["processing"]["gate_status"] == "failed"
    assert entry["processing"]["phase_method"] == "unphased"
    assert entry["gating"]["outcome"] == "gate_failed"
    assert entry["gating"]["reasons"] == ["phase_failed"]
    rejection = entry["processor_rejection"]
    assert rejection["processor"] == "proton_processor_v1"
    assert rejection["error"] == "ValueError"
    assert "failed the processing gate" in rejection["message"]


@requires_nmrglue
def test_same_nucleus_duplicates_never_concatenate_implicitly(metrics: dict[str, Any]) -> None:
    entry = _by_id(metrics)["same_nucleus_duplicates"]
    default = entry["selection"]["default"]
    proton_default = next(record for record in default["nuclei"] if record["element"] == "H")
    assert proton_default["selected_labels"] == ["11"]
    assert proton_default["policy"] == "default_deterministic"
    assert proton_default["combined"] is False
    dispositions = {record["label"]: record for record in default["experiments"]}
    assert dispositions["12"]["status"] == "not_selected"
    assert dispositions["12"]["reason"] == "default_deterministic"
    assert sorted(default["peaks_by_element"]["H"]) == pytest.approx([3.4998, 7.1204], abs=0.02)

    explicit = entry["selection"]["explicit_multi"]
    assert explicit["selection_keys"] == ["11", "12"]
    proton_explicit = next(record for record in explicit["nuclei"] if record["element"] == "H")
    assert proton_explicit["selected_labels"] == ["11", "12"]
    assert proton_explicit["policy"] == "explicit_multi"
    assert proton_explicit["combined"] is True
    assert sorted(explicit["peaks_by_element"]["H"]) == pytest.approx(
        [2.0996, 3.4998, 7.1204, 8.02], abs=0.02
    )


# ---------------------------------------------------------------------------
# real layer / three-state
# ---------------------------------------------------------------------------


def test_real_layer_is_declared_not_verified(metrics: dict[str, Any]) -> None:
    real = [entry for entry in metrics["fixtures"] if entry["layer"] == "real"]
    assert len(real) == 1
    entry = real[0]
    assert entry["category"] == "real_acquisition"
    assert entry["verification"]["status"] == "not_verified"
    reasons = entry["verification"]["reasons"]
    assert reasons
    assert all(NOT_VERIFIED in reason for reason in reasons)
    joined = " ".join(reasons)
    assert "instrument_storage_convention_unverified" in joined
    assert "digital_filter_effect_unverified" in joined


@requires_nmrglue
def test_real_layer_metrics_are_measured(metrics: dict[str, Any]) -> None:
    entry = next(entry for entry in metrics["fixtures"] if entry["layer"] == "real")
    assert entry["status"] == "measured"
    assert entry["metrics"] is not None
    assert entry["metrics"]["kind"] == "multiplet_grouping"
    assert entry["metrics"]["n_annotated"] == 6
    assert 0.0 <= entry["metrics"]["precision"] <= 1.0
    assert 0.0 <= entry["metrics"]["recall"] <= 1.0
    assert entry["processing"]["digital_filter"]["status"] == "not_compensated"


# ---------------------------------------------------------------------------
# nmrglue-free callability
# ---------------------------------------------------------------------------


def test_callable_without_nmrglue_skips_gated_layers_explicitly() -> None:
    payload = run_benchmark(nmrglue=False)
    assert payload["schema"] == SCHEMA
    assert payload["nmrglue"]["available"] is False
    manifest = load_manifest()
    requirements = {entry["id"]: entry["requires"] for entry in manifest["fixtures"]}
    by_id = _by_id(payload)
    for identifier, requires in requirements.items():
        entry = by_id[identifier]
        if requires == "nmrglue":
            assert entry["status"] == "not_verified", identifier
            assert any("nmrglue" in reason for reason in entry["verification"]["reasons"])
            assert all(NOT_VERIFIED in reason for reason in entry["verification"]["reasons"])
        else:
            assert entry["status"] == "measured", identifier
    two_d = by_id["two_d_ser"]
    assert two_d["gating"]["outcome"] == "rejected_not_1d"
    assert two_d["tree_rejection"]["error"] == "Not1DExperimentError"
    unphased = by_id["phase_unphased_gate"]
    assert unphased["gating"]["outcome"] == "gate_failed"
    duplicates = by_id["same_nucleus_duplicates"]
    assert duplicates["selection"]["default"]["nuclei"]
    assert duplicates["selection"]["default"]["peaks_by_element"] is None


@requires_nmrglue
def test_missing_annotations_marks_fixture_not_verified(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    stripped = False
    for entry in manifest["fixtures"]:
        if entry["id"] == "solvent_large_carbon":
            entry["annotations"] = {}
            stripped = True
    assert stripped
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    payload = run_benchmark(manifest_path=manifest_path)
    entry = _by_id(payload)["solvent_large_carbon"]
    assert entry["status"] == "not_verified"
    assert any("no_annotations" in reason for reason in entry["verification"]["reasons"])
    assert entry["metrics"] is None


# ---------------------------------------------------------------------------
# determinism / persistence / generator
# ---------------------------------------------------------------------------


def test_deterministic_re_run() -> None:
    first = run_benchmark()
    second = run_benchmark()
    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_write_metrics_round_trip(metrics: dict[str, Any], tmp_path: Path) -> None:
    target = write_metrics(metrics, tmp_path / "nested" / "metrics.json")
    payload = target.read_bytes()
    assert payload == canonical_json_bytes(metrics)
    assert json.loads(payload) == metrics
    assert not list(target.parent.glob(".*.tmp"))


def test_generator_reproduces_committed_fixtures() -> None:
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    completed = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "FIXTURE CHECK OK" in completed.stdout
