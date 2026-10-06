"""NMR benchmark harness acceptance (todo 50 / gap §10.3).

TDD acceptance for ``tests/benchmark/nmr`` + ``scripts/nmr_benchmark.py``:

* dataset-manifest schema rejection with TYPED errors (missing
  source/license/version/hash, hash mismatch, dangling references);
* metric determinism (same input ⇒ byte-identical canonical JSON,
  cross-process with a pinned clock and resource accounting disabled);
* molecule-clustered bootstrap sanity (conformers/atoms of one molecule are
  never independent samples; tighter data ⇒ narrower CI; paired method
  comparisons share resamples);
* pre-registered threshold hash verification (tampering refuses; re-freezing
  requires an explicit re-measurement note);
* end-to-end synthetic run consuming the todo-48 spectra fixtures;
* three-state discipline: missing data is ``not_verified``, never a silent
  pass and never a fabricated number.

All fixtures are synthetic and invoke no QC binaries.
"""

from __future__ import annotations

import copy
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.benchmark.nmr import (
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_N_RESAMPLES,
    HARNESS_SCHEMA,
    MANIFEST_SCHEMA,
    THRESHOLDS_SCHEMA,
    BenchmarkManifestError,
    BootstrapError,
    DatasetHashMismatchError,
    ManifestReferenceError,
    ManifestSchemaError,
    MissingProvenanceError,
    ThresholdIntegrityError,
    ThresholdSchemaError,
    canonical_dataset_hash,
    canonical_json_bytes,
    clustered_bootstrap_ci,
    load_dataset_manifest,
    load_thresholds,
    paired_clustered_bootstrap_ci,
    refreeze_thresholds,
    run_harness,
    seal_thresholds,
    write_metrics,
)
from tests.nmr_spectra_benchmark import SCHEMA as SPECTRA_SCHEMA

REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS_DIR = Path(__file__).resolve().parent
FIXTURE = HARNESS_DIR / "fixtures" / "synthetic_dataset.json"
THRESHOLDS = HARNESS_DIR / "thresholds.json"
SCRIPT = REPO_ROOT / "scripts" / "nmr_benchmark.py"
FIXED_NOW = "2026-10-06T00:00:00+00:00"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fixture_manifest() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _resign(manifest: dict) -> dict:
    for dataset in manifest["datasets"]:
        dataset["hash"] = canonical_dataset_hash(dataset["items"])
    return manifest


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _sealed_thresholds(tmp_path: Path, thresholds: dict) -> Path:
    payload = seal_thresholds(
        {
            "schema": THRESHOLDS_SCHEMA,
            "preregistered_at": "2026-10-06T00:00:00+00:00",
            "preregistered_note": "test-local threshold file",
            "thresholds": thresholds,
            "remeasurements": [],
        }
    )
    return _write_json(tmp_path / "thresholds.json", payload)


@pytest.fixture(scope="module")
def full_metrics() -> dict:
    return run_harness(FIXTURE, THRESHOLDS, now=FIXED_NOW, measure_resources=False)


@pytest.fixture(scope="module")
def fast_metrics() -> dict:
    return run_harness(
        FIXTURE, THRESHOLDS, include_spectra=False, now=FIXED_NOW, measure_resources=False
    )


# ---------------------------------------------------------------------------
# manifest schema — typed rejections
# ---------------------------------------------------------------------------


def test_valid_fixture_manifest_loads() -> None:
    manifest = load_dataset_manifest(FIXTURE)
    assert manifest["schema"] == MANIFEST_SCHEMA
    assert len(manifest["datasets"]) == 1
    dataset = manifest["datasets"][0]
    assert dataset["dataset_id"] == "synthetic-harness-selftest-v1"
    assert dataset["license"]
    assert dataset["source"]
    assert dataset["version"]
    assert len(dataset["hash"]) == 64
    assert dataset["hash"] == canonical_dataset_hash(dataset["items"])
    assert dataset["distribution"]["balanced_by_construction"] is True
    assert "NOT a realistic" in dataset["distribution"]["note"]


@pytest.mark.parametrize("field", ["source", "license", "version", "hash"])
def test_missing_provenance_is_rejected_typed(tmp_path: Path, field: str) -> None:
    manifest = _fixture_manifest()
    del manifest["datasets"][0][field]
    path = _write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(MissingProvenanceError) as excinfo:
        load_dataset_manifest(path)
    assert field in str(excinfo.value)
    assert isinstance(excinfo.value, BenchmarkManifestError)


def test_malformed_hash_is_rejected(tmp_path: Path) -> None:
    manifest = _fixture_manifest()
    manifest["datasets"][0]["hash"] = "not-a-sha256"
    path = _write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ManifestSchemaError):
        load_dataset_manifest(path)


def test_dataset_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    manifest = _fixture_manifest()
    manifest["datasets"][0]["hash"] = "0" * 64
    path = _write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(DatasetHashMismatchError):
        load_dataset_manifest(path)


def test_unknown_manifest_schema_is_rejected(tmp_path: Path) -> None:
    manifest = _fixture_manifest()
    manifest["schema"] = "something-else"
    path = _write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ManifestSchemaError):
        load_dataset_manifest(path)


def test_dangling_true_structure_candidate_is_rejected(tmp_path: Path) -> None:
    manifest = _fixture_manifest()
    manifest["datasets"][0]["items"][0]["true_structure_candidate_id"] = "does-not-exist"
    path = _write_json(tmp_path / "manifest.json", _resign(manifest))
    with pytest.raises(ManifestReferenceError) as excinfo:
        load_dataset_manifest(path)
    assert "does-not-exist" in str(excinfo.value)


def test_absent_item_may_not_carry_true_candidate(tmp_path: Path) -> None:
    manifest = _fixture_manifest()
    absent = manifest["datasets"][0]["items"][2]
    assert absent["true_structure_present"] is False
    absent["true_structure_candidate_id"] = absent["candidates"][0]["candidate_id"]
    path = _write_json(tmp_path / "manifest.json", _resign(manifest))
    with pytest.raises(ManifestSchemaError):
        load_dataset_manifest(path)


def test_unknown_spectra_fixture_link_is_rejected(tmp_path: Path) -> None:
    manifest = _fixture_manifest()
    manifest["datasets"][0]["items"][0]["spectra_fixture_id"] = "no_such_fixture"
    path = _write_json(tmp_path / "manifest.json", _resign(manifest))
    with pytest.raises(ManifestReferenceError) as excinfo:
        load_dataset_manifest(path, spectra_fixture_ids={"phase_deviation_proton"})
    assert "no_such_fixture" in str(excinfo.value)


def test_non_valid_candidate_with_probability_is_rejected(tmp_path: Path) -> None:
    manifest = _fixture_manifest()
    candidate = manifest["datasets"][0]["items"][3]["candidates"][0]
    assert candidate["status"] != "valid"
    candidate["dp4_probability"] = 0.5
    path = _write_json(tmp_path / "manifest.json", _resign(manifest))
    with pytest.raises(ManifestSchemaError):
        load_dataset_manifest(path)


def test_duplicate_candidate_ids_are_rejected(tmp_path: Path) -> None:
    manifest = _fixture_manifest()
    item = manifest["datasets"][0]["items"][0]
    item["candidates"][1]["candidate_id"] = item["candidates"][0]["candidate_id"]
    path = _write_json(tmp_path / "manifest.json", _resign(manifest))
    with pytest.raises(ManifestSchemaError):
        load_dataset_manifest(path)


def test_non_finite_ppm_is_rejected(tmp_path: Path) -> None:
    manifest = _fixture_manifest()
    manifest["datasets"][0]["items"][0]["experimental"]["13C"][0]["observed_ppm"] = math.inf
    path = _write_json(tmp_path / "manifest.json", _resign(manifest))
    with pytest.raises(ManifestSchemaError):
        load_dataset_manifest(path)


# ---------------------------------------------------------------------------
# pre-registered thresholds
# ---------------------------------------------------------------------------


def test_committed_thresholds_are_valid_and_sealed() -> None:
    payload = load_thresholds(THRESHOLDS)
    assert payload["schema"] == THRESHOLDS_SCHEMA
    assert payload["preregistered_at"]
    assert payload["content_sha256"]
    assert payload["remeasurements"] == []
    assert payload["thresholds"], "thresholds file carries no thresholds"
    for spec in payload["thresholds"].values():
        assert spec["op"] in (">=", "<=")
        assert isinstance(spec["value"], (int, float))
        assert spec["rationale"]


def test_threshold_tamper_is_refused() -> None:
    payload = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    payload["thresholds"]["top1_dp4"]["value"] = 0.1
    assert payload["content_sha256"] != seal_thresholds(payload)["content_sha256"]
    # Write the tampered payload with its STALE hash: the loader must refuse.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "thresholds.json"
        path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        with pytest.raises(ThresholdIntegrityError) as excinfo:
            load_thresholds(path)
    assert "re-measurement" in str(excinfo.value)


def test_threshold_missing_hash_is_refused() -> None:
    import tempfile

    payload = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    del payload["content_sha256"]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "thresholds.json"
        path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        with pytest.raises(ThresholdIntegrityError):
            load_thresholds(path)


def test_refreeze_requires_explicit_note(tmp_path: Path) -> None:
    payload = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    payload["thresholds"]["top1_dp4"]["value"] = 0.1
    path = _write_json(tmp_path / "thresholds.json", payload)
    with pytest.raises(ThresholdSchemaError):
        refreeze_thresholds(path, note="", at="2026-10-06T01:00:00+00:00")
    refrozen = refreeze_thresholds(
        path, note="re-measured after protocol change", at="2026-10-06T01:00:00+00:00"
    )
    assert refrozen["remeasurements"][0]["note"] == "re-measured after protocol change"
    assert refrozen["remeasurements"][0]["previous_sha256"] == payload["content_sha256"]
    loaded = load_thresholds(path)
    assert loaded["thresholds"]["top1_dp4"]["value"] == 0.1
    assert loaded["content_sha256"] == refrozen["content_sha256"]


def test_unknown_threshold_metric_is_rejected(tmp_path: Path) -> None:
    thresholds = {"bogus_metric": {"op": ">=", "value": 1.0, "rationale": "x"}}
    path = _sealed_thresholds(tmp_path, thresholds)
    with pytest.raises(ThresholdSchemaError):
        load_thresholds(path)


def test_unknown_threshold_op_is_rejected(tmp_path: Path) -> None:
    path = _sealed_thresholds(tmp_path, {"top1_dp4": {"op": "==", "value": 0.5, "rationale": "x"}})
    with pytest.raises(ThresholdSchemaError):
        load_thresholds(path)


# ---------------------------------------------------------------------------
# clustered bootstrap
# ---------------------------------------------------------------------------


def test_cluster_bootstrap_never_treats_conformers_as_independent() -> None:
    # 2 molecules; molecule A owns 50 identical "conformers", B owns 50.
    clusters = {"mol-a": [0.0] * 50, "mol-b": [1.0] * 50}
    interval = clustered_bootstrap_ci(clusters, n_resamples=4000)
    assert interval.n_clusters == 2
    assert interval.n_observations == 100
    assert interval.point == pytest.approx(0.5)
    # Cluster bootstrap over 2 molecules: the interval must span nearly the
    # full range.  Atom-level resampling would produce a narrow interval
    # (standard error ~ 0.05 → width ~0.2).
    assert interval.high - interval.low > 0.9
    assert interval.low == pytest.approx(0.0)
    assert interval.high == pytest.approx(1.0)


def test_cluster_bootstrap_tighter_data_narrower_interval() -> None:
    wide = clustered_bootstrap_ci({"m1": [0.0], "m2": [1.0]}, n_resamples=4000)
    tight = clustered_bootstrap_ci({"m1": [0.49], "m2": [0.51]}, n_resamples=4000)
    assert (tight.high - tight.low) < (wide.high - wide.low)
    assert tight.high - tight.low < 0.05


def test_paired_bootstrap_shares_resamples() -> None:
    # Cluster means differ (0.0 vs 1.0) so the marginal intervals are wide,
    # while B is exactly A shifted by 0.25 so every paired delta is identical.
    clusters_a = {"m1": [0.0, 0.0], "m2": [1.0, 1.0]}
    clusters_b = {"m1": [0.25, 0.25], "m2": [1.25, 1.25]}
    paired = paired_clustered_bootstrap_ci(clusters_a, clusters_b, n_resamples=4000)
    # Constant per-molecule delta ⇒ a shared-resample paired interval is a
    # point; independent resampling would produce a wide delta interval.
    assert paired.delta.low == paired.delta.high
    assert paired.delta.point == pytest.approx(-0.25, abs=1e-12)
    assert paired.a.high - paired.a.low > 0.5
    assert paired.b.high - paired.b.low > 0.5


def test_paired_bootstrap_requires_matched_clusters() -> None:
    with pytest.raises(BootstrapError):
        paired_clustered_bootstrap_ci({"m1": [0.0]}, {"m2": [0.0]})


def test_bootstrap_rejects_empty_clusters() -> None:
    with pytest.raises(BootstrapError):
        clustered_bootstrap_ci({})


def test_bootstrap_is_deterministic_for_seed() -> None:
    clusters = {"m1": [0.1, 0.2], "m2": [0.9], "m3": [0.4, 0.5]}
    first = clustered_bootstrap_ci(clusters, seed=DEFAULT_BOOTSTRAP_SEED)
    second = clustered_bootstrap_ci(clusters, seed=DEFAULT_BOOTSTRAP_SEED)
    assert first.as_dict() == second.as_dict()
    other = clustered_bootstrap_ci(clusters, seed=DEFAULT_BOOTSTRAP_SEED + 1)
    assert other.as_dict() != first.as_dict()


# ---------------------------------------------------------------------------
# harness metrics
# ---------------------------------------------------------------------------


def test_metrics_schema_and_provenance(full_metrics: dict) -> None:
    assert full_metrics["schema"] == HARNESS_SCHEMA
    provenance = full_metrics["provenance"]
    assert len(provenance["dataset_manifest"]["sha256"]) == 64
    assert provenance["dataset_manifest"]["schema"] == MANIFEST_SCHEMA
    assert provenance["datasets"]["synthetic-harness-selftest-v1"]["declared_hash"]
    assert len(provenance["thresholds"]["file_sha256"]) == 64
    assert len(provenance["thresholds"]["content_sha256"]) == 64
    assert provenance["code"]["git_head"] == _git_head()
    assert provenance["timestamp"] == FIXED_NOW
    assert provenance["bootstrap"] == {
        "seed": DEFAULT_BOOTSTRAP_SEED,
        "n_resamples": DEFAULT_N_RESAMPLES,
        "unit": "molecule",
    }
    dataset = full_metrics["datasets"][0]
    assert dataset["provenance"]["dataset_hash"] == dataset["hash"]
    threshold_sha = provenance["thresholds"]["content_sha256"]
    assert dataset["provenance"]["threshold_content_sha256"] == threshold_sha
    assert dataset["provenance"]["git_head"] == provenance["code"]["git_head"]
    assert dataset["provenance"]["timestamp"] == FIXED_NOW


def test_metrics_counts(fast_metrics: dict) -> None:
    dataset = fast_metrics["datasets"][0]
    assert dataset["counts"] == {
        "n_items": 4,
        "n_molecules": 3,
        "n_candidates": 9,
        "n_valid_candidates": 7,
    }


def test_shift_accuracy_measured_with_clustered_ci(fast_metrics: dict) -> None:
    accuracy = fast_metrics["datasets"][0]["shift_accuracy"]
    carbon = accuracy["13C"]
    proton = accuracy["1H"]
    assert carbon["status"] == "measured"
    assert carbon["n_residuals"] == 5
    assert carbon["mae"] == pytest.approx(0.072, abs=1e-12)
    assert carbon["rmse"] == pytest.approx(math.sqrt(0.0266 / 5), abs=1e-12)
    assert carbon["mae_ci"]["n_clusters"] == 2
    assert carbon["mae_ci"]["low"] <= carbon["mae"] <= carbon["mae_ci"]["high"]
    assert proton["status"] == "measured"
    assert proton["n_residuals"] == 5
    assert proton["mae"] == pytest.approx(0.016, abs=1e-12)


def test_assignment_and_top1_metrics(fast_metrics: dict) -> None:
    dataset = fast_metrics["datasets"][0]
    assignment = dataset["assignment"]
    assert assignment["status"] == "measured"
    assert assignment["n_scored"] == 10
    assert assignment["n_correct"] == 10
    assert assignment["accuracy"] == 1.0
    assert assignment["accuracy_ci"]["low"] == 1.0
    assert assignment["accuracy_ci"]["high"] == 1.0
    ranking = dataset["ranking"]
    assert ranking["top1_dp4"]["status"] == "measured"
    assert ranking["top1_dp4"]["n_items"] == 2
    assert ranking["top1_dp4"]["accuracy"] == 1.0
    assert ranking["top1_dp5"]["status"] == "measured"
    assert ranking["top1_dp5"]["n_items"] == 2
    assert ranking["top1_dp5"]["accuracy"] == 1.0


def test_binary_probability_metrics_and_calibration(fast_metrics: dict) -> None:
    binary = fast_metrics["datasets"][0]["binary_probability"]
    dp5 = binary["dp5"]
    assert dp5["status"] == "measured"
    assert dp5["n_positive"] == 2
    assert dp5["n_negative"] == 4
    assert dp5["n_scored"] == 6
    assert dp5["prevalence"] == pytest.approx(2 / 6)
    assert dp5["brier"] == pytest.approx(0.5965 / 6, abs=1e-12)
    # Three molecules contribute DP5 rows (toluene's two decoys included).
    assert dp5["brier_ci"]["n_clusters"] == 3
    expected_log_loss = (
        -(
            math.log(0.88)
            + math.log(0.81)
            + math.log(0.93)
            + math.log(0.81)
            + math.log(0.45)
            + math.log(0.55)
        )
        / 6
    )
    assert dp5["log_loss"] == pytest.approx(expected_log_loss, abs=1e-12)
    assert dp5["clamped"] == 0
    calibration = dp5["calibration"]
    assert calibration["n_bins"] == 10
    assert sum(entry["n"] for entry in calibration["bins"]) == 6
    occupied = [entry for entry in calibration["bins"] if entry["n"]]
    assert occupied
    for entry in occupied:
        assert entry["mean_predicted"] is not None
        assert entry["observed_frequency"] is not None
    # dp5 null (unavailable at candidate level) is excluded, never 0.
    dp4 = binary["dp4"]
    assert dp4["status"] == "measured"
    assert dp4["n_scored"] == 7
    assert dp4["n_positive"] == 2


def test_absence_and_refusal_metrics(fast_metrics: dict) -> None:
    dataset = fast_metrics["datasets"][0]
    absence = dataset["true_structure_absence"]
    assert absence["status"] == "measured"
    assert absence["n_items"] == 2
    assert absence["n_with_valid_candidates"] == 1
    assert absence["n_refused"] == 1
    assert absence["n_confident_winner"] == 1
    assert absence["confident_winner_rate"] == pytest.approx(0.5)
    refusal = dataset["refusal"]
    assert refusal["n_candidates"] == 9
    assert refusal["n_valid"] == 7
    assert refusal["n_nonvalid"] == 2
    assert refusal["refusal_rate"] == pytest.approx(2 / 9)
    assert refusal["by_status"] == {"evidence_insufficient": 1, "invalid": 1}
    assert refusal["n_items_refused"] == 1
    assert refusal["item_refusal_rate"] == pytest.approx(0.25)


def test_per_item_records_carry_typed_absence_behavior(fast_metrics: dict) -> None:
    items = {item["item_id"]: item for item in fast_metrics["datasets"][0]["items"]}
    absent_confident = items["toluene-absent-confident"]
    assert absent_confident["true_structure_present"] is False
    assert absent_confident["absent_behavior"]["status"] == "true_structure_absent"
    assert absent_confident["absent_behavior"]["confident"] is True
    absent_refused = items["toluene-absent-refused"]
    assert absent_refused["absent_behavior"]["status"] == "refused"
    assert absent_refused["absent_behavior"]["confident"] is False
    measured = items["ethanol-item"]
    assert measured["status"] == "measured"
    assert measured["top1_is_true"] is True
    assert measured["absent_behavior"] is None


def test_threshold_comparisons_all_pass_or_not_verified(full_metrics: dict) -> None:
    dataset = full_metrics["datasets"][0]
    comparisons = dataset["thresholds"]["comparisons"]
    assert len(comparisons) == len(load_thresholds(THRESHOLDS)["thresholds"])
    statuses = {entry["status"] for entry in comparisons}
    assert "fail" not in statuses
    assert statuses == {"pass"}
    for entry in comparisons:
        assert entry["observed"] is not None
        assert entry["metric"] in load_thresholds(THRESHOLDS)["thresholds"]


def test_impossible_threshold_reports_fail(tmp_path: Path) -> None:
    thresholds_path = _sealed_thresholds(
        tmp_path, {"top1_dp4": {"op": ">=", "value": 1.5, "rationale": "impossible on purpose"}}
    )
    payload = run_harness(
        FIXTURE,
        thresholds_path,
        include_spectra=False,
        now=FIXED_NOW,
        measure_resources=False,
    )
    comparison = payload["datasets"][0]["thresholds"]["comparisons"][0]
    assert comparison["status"] == "fail"
    assert comparison["observed"] == 1.0
    assert comparison["threshold"] == 1.5


def test_missing_metric_reports_not_verified(tmp_path: Path) -> None:
    manifest = _fixture_manifest()
    dataset = manifest["datasets"][0]
    for item in dataset["items"]:
        item["experimental"].pop("1H", None)
        for candidate in item["candidates"]:
            candidate["signals"].pop("1H", None)
    manifest_path = _write_json(tmp_path / "manifest.json", _resign(manifest))
    thresholds_path = _sealed_thresholds(
        tmp_path,
        {"shift_mae_1h_ppm": {"op": "<=", "value": 0.1, "rationale": "no 1H data in this variant"}},
    )
    payload = run_harness(
        manifest_path,
        thresholds_path,
        include_spectra=False,
        now=FIXED_NOW,
        measure_resources=False,
    )
    comparison = payload["datasets"][0]["thresholds"]["comparisons"][0]
    assert comparison["status"] == "not_verified"
    assert comparison["observed"] is None
    assert comparison["reasons"] == ["metric_not_verified"]
    assert payload["datasets"][0]["shift_accuracy"]["1H"]["status"] == "not_verified"


def test_resources_accounting_present_by_default() -> None:
    payload = run_harness(
        FIXTURE, THRESHOLDS, include_spectra=False, now=FIXED_NOW, measure_resources=True
    )
    resources = payload["resources"]
    assert resources["measured"] is True
    assert resources["wall_seconds"] >= 0.0
    assert resources["cpu_user_seconds"] >= 0.0
    assert resources["cpu_system_seconds"] >= 0.0
    assert resources["peak_rss_bytes"] > 0
    dataset_resources = payload["datasets"][0]["resources"]
    assert dataset_resources["measured"] is True
    assert dataset_resources["wall_seconds"] >= 0.0


# ---------------------------------------------------------------------------
# spectra layer (todo 48 consumption)
# ---------------------------------------------------------------------------


def test_end_to_end_run_consumes_t48_spectra_fixtures(full_metrics: dict) -> None:
    spectra = full_metrics["spectra"]
    assert spectra["status"] == "measured"
    assert spectra["schema"] == SPECTRA_SCHEMA
    assert spectra["summary"]["n_fixtures"] == 11
    assert len(spectra["payload_sha256"]) == 64
    by_id = {entry["id"]: entry for entry in spectra["fixtures"]}
    # The dataset manifest links items to todo-48 fixtures; the harness must
    # have validated those links against the real fixture manifest.
    assert "phase_deviation_proton" in by_id
    assert "solvent_large_carbon" in by_id
    for entry in spectra["fixtures"]:
        assert entry["status"] in ("measured", "not_verified")
        assert entry["verification_status"] in ("verified", "not_verified")
        assert len(entry["sha256"]) == 64


def test_spectra_layer_can_be_disabled_typed(fast_metrics: dict) -> None:
    spectra = fast_metrics["spectra"]
    assert spectra["status"] == "not_requested"
    assert spectra["fixtures"] == []


# ---------------------------------------------------------------------------
# determinism / persistence / CLI
# ---------------------------------------------------------------------------


def test_metric_determinism_in_process() -> None:
    first = run_harness(FIXTURE, THRESHOLDS, now=FIXED_NOW, measure_resources=False)
    second = run_harness(FIXTURE, THRESHOLDS, now=FIXED_NOW, measure_resources=False)
    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_only_resources_and_timestamp_are_volatile() -> None:
    first = run_harness(FIXTURE, THRESHOLDS, include_spectra=False, now=FIXED_NOW)
    second = run_harness(FIXTURE, THRESHOLDS, include_spectra=False, now=FIXED_NOW)

    def _strip(payload: dict) -> bytes:
        stable = copy.deepcopy(payload)
        stable["provenance"]["timestamp"] = "<ts>"
        stable["resources"] = {"measured": True}
        for dataset in stable["datasets"]:
            dataset["resources"] = {"measured": True}
        return canonical_json_bytes(stable)

    assert _strip(first) == _strip(second)


def test_metric_determinism_cross_process(tmp_path: Path, full_metrics: dict) -> None:
    outputs = []
    for hash_seed in ("1", "98765"):
        target = tmp_path / f"metrics-{hash_seed}.json"
        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src"), "PYTHONHASHSEED": hash_seed}
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--dataset-manifest",
                str(FIXTURE),
                "--thresholds",
                str(THRESHOLDS),
                "--output",
                str(target),
                "--no-resources",
                "--now",
                FIXED_NOW,
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        outputs.append(target.read_bytes())
    assert outputs[0] == outputs[1]
    # The CLI run is byte-identical to the in-process run with the same clock.
    assert outputs[0] == canonical_json_bytes(full_metrics)


def test_write_metrics_round_trip(full_metrics: dict, tmp_path: Path) -> None:
    target = write_metrics(full_metrics, tmp_path / "nested" / "metrics.json")
    payload = target.read_bytes()
    assert payload == canonical_json_bytes(full_metrics)
    assert json.loads(payload) == full_metrics
    assert not list(target.parent.glob(".*.tmp"))


def test_cli_script_writes_canonical_metrics(tmp_path: Path) -> None:
    target = tmp_path / "cli-metrics.json"
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--dataset-manifest",
            str(FIXTURE),
            "--thresholds",
            str(THRESHOLDS),
            "--output",
            str(target),
            "--no-spectra",
            "--no-resources",
            "--now",
            FIXED_NOW,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema"] == HARNESS_SCHEMA
    assert payload["spectra"]["status"] == "not_requested"
    assert "pass" in completed.stdout


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()
