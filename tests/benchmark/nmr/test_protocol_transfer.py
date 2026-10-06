"""Acceptance suite for the protocol-transfer A/B harness (todo 52 / gap §10.3 trial B).

TDD acceptance for ``tests/benchmark/nmr/protocol_transfer.py``:

* axis vocabulary + single-variable discipline: every comparison changes
  exactly one axis; multi-axis / no-axis changes raise the typed
  ``MultiAxisChangeError`` / ``NoAxisChangeError`` BEFORE any run executes;
* the impact table is a deterministic, recomputable canonical JSON document:
  per axis baseline-vs-variant metric deltas plus provenance on both sides
  (axis value, protocol/model ids, candidate-set + experimental-spectrum
  hashes, seed); the table is byte-identical across processes;
* synthetic/mock only — no QC subprocess is ever started here (the real-run
  procedure lives in the module docstring runbook);
* a missing real axis ends as ``NOT_VERIFIED`` (three-state rule), never a
  green pass;
* todo-50 metrics/harness (``run_harness`` / ``KNOWN_METRIC_KEYS`` /
  ``metric_value`` / paired bootstrap) and todo-53 layer loaders are reused,
  never forked.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from acp.nmr.method_config import NmrMethodConfig
from tests.benchmark.nmr import (
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_DATASET_MANIFEST,
    MANIFEST_SCHEMA,
    canonical_dataset_hash,
    canonical_json_bytes,
    load_assigned_statistical,
)
from tests.benchmark.nmr.protocol_transfer import (
    AXES,
    AXIS_SPECS,
    DEFAULT_SETTINGS,
    AxisComparison,
    FixedInputsChangedError,
    MultiAxisChangeError,
    NoAxisChangeError,
    ProtocolRun,
    ProtocolRunError,
    ProtocolSettings,
    ProtocolTransferError,
    ProtocolUnavailableError,
    UnknownAxisError,
    assert_single_axis_change,
    build_impact_table,
    candidate_set_sha256,
    changed_axes,
    experimental_spectrum_sha256,
    run_ab_comparison,
    write_impact_table,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_LAYER2_FIXTURE = _FIXTURES / "layer2_assigned_statistical.json"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PINNED_NOW = "2026-10-06T00:00:00+00:00"
_N_RESAMPLES = 100


# ---------------------------------------------------------------------------
# synthetic manifest + runner helpers (mock only; no QC)
# ---------------------------------------------------------------------------


def _read_manifest(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _resign(manifest: dict[str, Any]) -> None:
    for dataset in manifest["datasets"]:
        dataset["hash"] = canonical_dataset_hash(dataset["items"])


def _shift_13c(manifest: dict[str, Any], delta: float) -> None:
    for dataset in manifest["datasets"]:
        for item in dataset["items"]:
            for candidate in item["candidates"]:
                for signal in candidate["signals"].get("13C", ()):
                    signal["predicted_ppm"] = float(signal["predicted_ppm"]) + delta


def _invalidate_candidate(manifest: dict[str, Any], candidate_id: str) -> None:
    for dataset in manifest["datasets"]:
        for item in dataset["items"]:
            for candidate in item["candidates"]:
                if candidate["candidate_id"] == candidate_id:
                    candidate["status"] = "invalid"
                    candidate["dp4_probability"] = None
                    candidate["dp5_probability"] = None
                    candidate["signals"] = {nucleus: [] for nucleus in candidate["signals"]}


def _relabel_true_signals(manifest: dict[str, Any], label: str) -> None:
    for dataset in manifest["datasets"]:
        for item in dataset["items"]:
            for candidate in item["candidates"]:
                if candidate["is_true_structure"]:
                    for signals in candidate["signals"].values():
                        for signal in signals:
                            signal["atom_label"] = label


def _synthetic_runner(
    *,
    shift_13c: float | None = None,
    invalidate: str | None = None,
    relabel: str | None = None,
    unavailable_on_engine: bool = False,
):
    """Deterministic mock runner: one transform per axis (no QC subprocess)."""

    def runner(settings: ProtocolSettings, base_manifest: Path, *, seed: int) -> ProtocolRun:
        if unavailable_on_engine and settings.engine != DEFAULT_SETTINGS.engine:
            raise ProtocolUnavailableError(
                "engine axis not runnable here: export CONFSEARCH_ORCA_PATH and use --run-slow"
            )
        manifest = copy.deepcopy(_read_manifest(base_manifest))
        touched = False
        if shift_13c is not None and settings.geometry != DEFAULT_SETTINGS.geometry:
            _shift_13c(manifest, shift_13c)
            touched = True
        if invalidate is not None and settings.solvent != DEFAULT_SETTINGS.solvent:
            _invalidate_candidate(manifest, invalidate)
            touched = True
        if relabel is not None and settings.assignment != DEFAULT_SETTINGS.assignment:
            _relabel_true_signals(manifest, relabel)
            touched = True
        if touched:
            _resign(manifest)
        return ProtocolRun(
            settings=settings,
            protocol_id="synthetic-selftest-v1",
            model_ids={
                "shielding_model": "synthetic",
                "dp5_model": "unavailable",
                "error_model": "goodman-legacy",
            },
            manifest=manifest,
        )

    return runner


def _experimental_tamper_runner(settings: ProtocolSettings, base_manifest: Path, *, seed: int):
    """Single-axis runner that also moves the (fixed) experimental spectrum."""
    manifest = copy.deepcopy(_read_manifest(base_manifest))
    for dataset in manifest["datasets"]:
        for item in dataset["items"]:
            for signals in item["experimental"].values():
                for signal in signals:
                    signal["observed_ppm"] = float(signal["observed_ppm"]) + 0.5
    _resign(manifest)
    return ProtocolRun(
        settings=settings,
        protocol_id="synthetic-tampered-v1",
        model_ids={"shielding_model": "synthetic"},
        manifest=manifest,
    )


def _small_item(
    item_id: str,
    molecule_id: str,
    true_id: str,
    decoy_id: str,
    observed: float,
    true_predicted: float,
    decoy_predicted: float,
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "molecule_id": molecule_id,
        "true_structure_present": True,
        "true_structure_candidate_id": true_id,
        "experimental": {
            "13C": [{"signal_id": "C1", "observed_ppm": observed, "atom_label": "C1"}]
        },
        "candidates": [
            {
                "candidate_id": true_id,
                "is_true_structure": True,
                "status": "valid",
                "dp4_probability": 0.8,
                "dp5_probability": 0.75,
                "signals": {
                    "13C": [
                        {"signal_id": "C1", "predicted_ppm": true_predicted, "atom_label": "C1"}
                    ]
                },
            },
            {
                "candidate_id": decoy_id,
                "is_true_structure": False,
                "status": "valid",
                "dp4_probability": 0.2,
                "dp5_probability": 0.25,
                "signals": {
                    "13C": [
                        {"signal_id": "C1", "predicted_ppm": decoy_predicted, "atom_label": "C1"}
                    ]
                },
            },
        ],
    }


def _small_manifest() -> dict[str, Any]:
    """Two molecules with known +0.5 / +0.3 ppm true-candidate residuals."""
    items = [
        _small_item("item-a", "mol-a", "true-a", "decoy-a", 10.0, 10.5, 12.0),
        _small_item("item-b", "mol-b", "true-b", "decoy-b", 20.0, 20.3, 22.0),
    ]
    return {
        "schema": MANIFEST_SCHEMA,
        "datasets": [
            {
                "dataset_id": "protocol-transfer-selftest-v1",
                "title": "protocol transfer synthetic selftest",
                "layer": "synthetic",
                "source": "synthetic in-repo fixture",
                "license": "CC0-1.0",
                "version": "1.0.0",
                "hash": canonical_dataset_hash(items),
                "distribution": {
                    "note": "balanced by construction; NOT a realistic prevalence",
                    "balanced_by_construction": True,
                },
                "items": items,
            }
        ],
    }


def _write_small(tmp_path: Path) -> Path:
    path = tmp_path / "small_manifest.json"
    path.write_bytes(canonical_json_bytes(_small_manifest()))
    return path


# ---------------------------------------------------------------------------
# axis vocabulary + single-variable discipline
# ---------------------------------------------------------------------------


def test_axis_vocabulary_matches_specs_and_method_config_fields():
    assert AXES == ("geometry", "energy", "solvent", "engine", "assignment")
    assert tuple(AXIS_SPECS) == AXES
    config_fields = {field.name for field in dataclasses.fields(NmrMethodConfig)}
    for axis, spec in AXIS_SPECS.items():
        assert spec.axis == axis
        assert spec.title.strip()
        assert spec.real_knob.strip()
        assert spec.example_values
        for field_name in spec.method_config_fields:
            assert field_name in config_fields, (axis, field_name)


def test_default_settings_documented_in_axis_specs():
    values = DEFAULT_SETTINGS.to_dict()
    assert tuple(values) == AXES
    for axis in AXES:
        assert DEFAULT_SETTINGS.axis_value(axis) == values[axis]
        assert values[axis] in AXIS_SPECS[axis].example_values


def test_settings_fingerprint_is_stable_and_axis_sensitive():
    fingerprint = DEFAULT_SETTINGS.fingerprint()
    assert len(fingerprint) == 64
    assert DEFAULT_SETTINGS.fingerprint() == fingerprint
    assert DEFAULT_SETTINGS.with_axis("solvent", "none").fingerprint() != fingerprint


def test_with_axis_rejects_unknown_axis_and_blank_value():
    with pytest.raises(UnknownAxisError):
        DEFAULT_SETTINGS.with_axis("bogus", "x")
    with pytest.raises(ProtocolTransferError):
        DEFAULT_SETTINGS.with_axis("solvent", "   ")
    with pytest.raises(UnknownAxisError):
        DEFAULT_SETTINGS.axis_value("bogus")


def test_changed_axes_and_single_axis_guard():
    variant = DEFAULT_SETTINGS.with_axis("energy", "dft-sp")
    assert changed_axes(DEFAULT_SETTINGS, variant) == ("energy",)
    assert_single_axis_change("energy", DEFAULT_SETTINGS, variant)


def test_multi_axis_change_rejected_before_any_run(tmp_path: Path):
    base = _write_small(tmp_path)
    calls: list[ProtocolSettings] = []

    def spy(settings: ProtocolSettings, base_manifest: Path, *, seed: int) -> ProtocolRun:
        calls.append(settings)
        return _synthetic_runner()(settings, base_manifest, seed=seed)

    variant = DEFAULT_SETTINGS.with_axis("solvent", "none").with_axis("engine", "other-engine")
    with pytest.raises(MultiAxisChangeError) as excinfo:
        run_ab_comparison("solvent", DEFAULT_SETTINGS, variant, base, spy)
    assert calls == []
    message = str(excinfo.value)
    assert "solvent" in message and "engine" in message


def test_no_axis_change_rejected(tmp_path: Path):
    base = _write_small(tmp_path)
    with pytest.raises(NoAxisChangeError):
        run_ab_comparison("solvent", DEFAULT_SETTINGS, DEFAULT_SETTINGS, base, _synthetic_runner())


def test_unknown_comparison_axis_rejected(tmp_path: Path):
    base = _write_small(tmp_path)
    with pytest.raises(UnknownAxisError):
        run_ab_comparison("bogus", DEFAULT_SETTINGS, DEFAULT_SETTINGS, base, _synthetic_runner())


# ---------------------------------------------------------------------------
# measured comparisons: metric deltas + provenance
# ---------------------------------------------------------------------------


def test_measured_comparison_reports_exact_metric_deltas(tmp_path: Path):
    base = _write_small(tmp_path)
    variant = DEFAULT_SETTINGS.with_axis("geometry", "censo-default")
    entry = run_ab_comparison(
        "geometry",
        DEFAULT_SETTINGS,
        variant,
        base,
        _synthetic_runner(shift_13c=0.25),
        seed=DEFAULT_BOOTSTRAP_SEED,
        n_resamples=_N_RESAMPLES,
        now=_PINNED_NOW,
    )
    assert entry["status"] == "measured"
    assert entry["axis"] == "geometry"
    assert entry["axis_change"] == {"from": "censo-light", "to": "censo-default"}
    metrics = {item["key"]: item for item in entry["metrics"]}
    mae = metrics["shift_mae_13c_ppm"]
    assert mae["status"] == "measured"
    assert mae["baseline"] == pytest.approx(0.40, abs=1e-12)
    assert mae["variant"] == pytest.approx(0.65, abs=1e-12)
    assert mae["delta"] == pytest.approx(0.25, abs=1e-12)
    assert metrics["shift_mae_1h_ppm"]["status"] == "not_verified"
    paired = entry["paired_metrics"]["13C"]
    assert paired["status"] == "measured"
    assert paired["a"] == "baseline" and paired["b"] == "variant"
    assert paired["result"]["delta"]["point"] == pytest.approx(-0.25, abs=1e-12)


def test_refusal_delta_on_candidate_status_flip(tmp_path: Path):
    base = _write_small(tmp_path)
    variant = DEFAULT_SETTINGS.with_axis("solvent", "none")
    entry = run_ab_comparison(
        "solvent",
        DEFAULT_SETTINGS,
        variant,
        base,
        _synthetic_runner(invalidate="decoy-a"),
        n_resamples=_N_RESAMPLES,
        now=_PINNED_NOW,
    )
    assert entry["status"] == "measured"
    metrics = {item["key"]: item for item in entry["metrics"]}
    refusal = metrics["refusal_rate"]
    assert refusal["baseline"] == pytest.approx(0.0)
    assert refusal["variant"] == pytest.approx(0.25)
    assert refusal["delta"] == pytest.approx(0.25)


def test_provenance_complete_and_input_fingerprints_pinned(tmp_path: Path):
    base = _write_small(tmp_path)
    variant = DEFAULT_SETTINGS.with_axis("geometry", "censo-default")
    entry = run_ab_comparison(
        "geometry",
        DEFAULT_SETTINGS,
        variant,
        base,
        _synthetic_runner(shift_13c=0.25),
        n_resamples=_N_RESAMPLES,
        now=_PINNED_NOW,
    )
    expected_keys = {
        "axis_value",
        "settings",
        "settings_fingerprint",
        "protocol_id",
        "model_ids",
        "dataset_id",
        "layer",
        "dataset_hash",
        "manifest_file_sha256",
        "candidate_set_sha256",
        "experimental_spectrum_sha256",
        "seed",
        "status",
        "reasons",
    }
    baseline_side = entry["baseline"]
    variant_side = entry["variant"]
    assert set(baseline_side) == expected_keys
    assert set(variant_side) == expected_keys
    assert baseline_side["status"] == variant_side["status"] == "measured"
    assert baseline_side["axis_value"] == DEFAULT_SETTINGS.geometry
    assert variant_side["axis_value"] == "censo-default"
    assert baseline_side["seed"] == variant_side["seed"] == DEFAULT_BOOTSTRAP_SEED
    assert baseline_side["protocol_id"] == variant_side["protocol_id"] == "synthetic-selftest-v1"
    assert baseline_side["model_ids"] == {
        "dp5_model": "unavailable",
        "error_model": "goodman-legacy",
        "shielding_model": "synthetic",
    }
    assert baseline_side["dataset_id"] == "protocol-transfer-selftest-v1"
    assert baseline_side["layer"] == "synthetic"
    assert len(baseline_side["manifest_file_sha256"]) == 64
    assert len(baseline_side["settings_fingerprint"]) == 64
    assert baseline_side["candidate_set_sha256"] == variant_side["candidate_set_sha256"]
    assert (
        baseline_side["experimental_spectrum_sha256"]
        == variant_side["experimental_spectrum_sha256"]
    )
    assert baseline_side["dataset_hash"] != variant_side["dataset_hash"]
    base_doc = _read_manifest(base)
    assert baseline_side["candidate_set_sha256"] == candidate_set_sha256(base_doc)
    assert baseline_side["experimental_spectrum_sha256"] == experimental_spectrum_sha256(base_doc)


def test_fixed_inputs_change_rejected_even_with_single_axis(tmp_path: Path):
    base = _write_small(tmp_path)
    variant = DEFAULT_SETTINGS.with_axis("geometry", "censo-default")
    with pytest.raises(FixedInputsChangedError):
        run_ab_comparison(
            "geometry",
            DEFAULT_SETTINGS,
            variant,
            base,
            _experimental_tamper_runner,
            n_resamples=_N_RESAMPLES,
            now=_PINNED_NOW,
        )


# ---------------------------------------------------------------------------
# NOT_VERIFIED (real axis cannot run) + summary
# ---------------------------------------------------------------------------


def test_not_verified_when_real_axis_unavailable(tmp_path: Path):
    base = _write_small(tmp_path)
    variant = DEFAULT_SETTINGS.with_axis("engine", "unavailable-engine")
    entry = run_ab_comparison(
        "engine",
        DEFAULT_SETTINGS,
        variant,
        base,
        _synthetic_runner(unavailable_on_engine=True),
        n_resamples=_N_RESAMPLES,
        now=_PINNED_NOW,
    )
    assert entry["status"] == "not_verified"
    assert entry["metrics"] is None
    assert entry["paired_metrics"] is None
    assert entry["reasons"]
    assert all(reason.startswith("NOT_VERIFIED") for reason in entry["reasons"])
    assert entry["baseline"]["status"] == "measured"
    assert entry["variant"]["status"] == "not_verified"


def test_build_impact_table_not_verified_summary_never_green(tmp_path: Path):
    base = _write_small(tmp_path)
    table = build_impact_table(
        base,
        [
            AxisComparison(
                "engine", DEFAULT_SETTINGS, DEFAULT_SETTINGS.with_axis("engine", "unavailable")
            )
        ],
        _synthetic_runner(unavailable_on_engine=True),
        n_resamples=_N_RESAMPLES,
        now=_PINNED_NOW,
    )
    assert table["summary"]["statuses"] == {"not_verified": 1}
    assert table["summary"]["n_measured"] == 0
    assert table["summary"]["n_not_verified"] == 1
    assert all(comparison["status"] == "not_verified" for comparison in table["comparisons"])


# ---------------------------------------------------------------------------
# impact table: multi-axis end-to-end + determinism/recomputation
# ---------------------------------------------------------------------------


def test_build_impact_table_multi_axis_measured_and_recomputable(tmp_path: Path):
    runner = _synthetic_runner(shift_13c=0.25, invalidate="ethanol-decoy", relabel="X1")
    comparisons = [
        AxisComparison(
            "geometry", DEFAULT_SETTINGS, DEFAULT_SETTINGS.with_axis("geometry", "censo-default")
        ),
        AxisComparison("solvent", DEFAULT_SETTINGS, DEFAULT_SETTINGS.with_axis("solvent", "none")),
        AxisComparison(
            "assignment", DEFAULT_SETTINGS, DEFAULT_SETTINGS.with_axis("assignment", "locked-only")
        ),
    ]
    first = build_impact_table(
        DEFAULT_DATASET_MANIFEST,
        comparisons,
        runner,
        seed=DEFAULT_BOOTSTRAP_SEED,
        n_resamples=_N_RESAMPLES,
        now=_PINNED_NOW,
        work_dir=tmp_path / "work",
    )
    second = build_impact_table(
        DEFAULT_DATASET_MANIFEST,
        comparisons,
        runner,
        seed=DEFAULT_BOOTSTRAP_SEED,
        n_resamples=_N_RESAMPLES,
        now=_PINNED_NOW,
        work_dir=tmp_path / "work",
    )
    assert first == second
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert [entry["axis"] for entry in first["comparisons"]] == [
        "geometry",
        "solvent",
        "assignment",
    ]
    assert all(entry["status"] == "measured" for entry in first["comparisons"])
    assert first["summary"]["axes_covered"] == ["assignment", "geometry", "solvent"]
    assert first["base_manifest"]["dataset_ids"] == ["synthetic-harness-selftest-v1"]
    metrics = {item["key"]: item for item in first["comparisons"][0]["metrics"]}
    assert metrics["shift_mae_13c_ppm"]["delta"] > 0


def test_build_impact_table_rejects_empty_comparisons(tmp_path: Path):
    base = _write_small(tmp_path)
    with pytest.raises(ProtocolTransferError):
        build_impact_table(base, [], _synthetic_runner(), n_resamples=_N_RESAMPLES, now=_PINNED_NOW)


_CROSS_PROCESS_SCRIPT = """
import sys

from tests.benchmark.nmr.protocol_transfer import (
    AxisComparison,
    DEFAULT_SETTINGS,
    build_impact_table,
    write_impact_table,
)
from tests.benchmark.nmr.test_protocol_transfer import _synthetic_runner

table = build_impact_table(
    sys.argv[1],
    [
        AxisComparison(
            "geometry",
            DEFAULT_SETTINGS,
            DEFAULT_SETTINGS.with_axis("geometry", "censo-default"),
        )
    ],
    _synthetic_runner(shift_13c=0.25),
    n_resamples=100,
    now="2026-10-06T00:00:00+00:00",
)
write_impact_table(table, sys.argv[2])
"""


def test_impact_table_byte_identical_across_processes(tmp_path: Path):
    outputs: list[bytes] = []
    for hash_seed in ("1", "98765"):
        destination = tmp_path / f"impact-{hash_seed}.json"
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
                str(DEFAULT_DATASET_MANIFEST),
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
    in_process = build_impact_table(
        DEFAULT_DATASET_MANIFEST,
        [
            AxisComparison(
                "geometry",
                DEFAULT_SETTINGS,
                DEFAULT_SETTINGS.with_axis("geometry", "censo-default"),
            )
        ],
        _synthetic_runner(shift_13c=0.25),
        n_resamples=100,
        now="2026-10-06T00:00:00+00:00",
    )
    assert outputs[0] == canonical_json_bytes(in_process)


def test_write_impact_table_atomic_canonical_roundtrip(tmp_path: Path):
    table = build_impact_table(
        DEFAULT_DATASET_MANIFEST,
        [
            AxisComparison(
                "geometry",
                DEFAULT_SETTINGS,
                DEFAULT_SETTINGS.with_axis("geometry", "censo-default"),
            )
        ],
        _synthetic_runner(shift_13c=0.25),
        n_resamples=_N_RESAMPLES,
        now=_PINNED_NOW,
    )
    path = write_impact_table(table, tmp_path / "impact.json")
    assert path.read_bytes() == canonical_json_bytes(table)
    assert json.loads(path.read_text(encoding="utf-8")) == table


# ---------------------------------------------------------------------------
# todo-53 layer composition + ProtocolRun validation + runbook guard
# ---------------------------------------------------------------------------


def test_layer2_fixture_composes_with_impact_harness(tmp_path: Path):
    loaded = load_assigned_statistical(_LAYER2_FIXTURE)
    assert loaded[0].layer == "assigned_statistical"
    entry = run_ab_comparison(
        "solvent",
        DEFAULT_SETTINGS,
        DEFAULT_SETTINGS.with_axis("solvent", "none"),
        _LAYER2_FIXTURE,
        _synthetic_runner(),
        n_resamples=_N_RESAMPLES,
        now=_PINNED_NOW,
    )
    assert entry["status"] == "measured"
    assert entry["baseline"]["layer"] == "assigned_statistical"
    assert entry["variant"]["layer"] == "assigned_statistical"


def test_protocol_run_validation_is_typed(tmp_path: Path):
    base = _write_small(tmp_path)
    manifest = _read_manifest(base)
    with pytest.raises(ProtocolRunError):
        ProtocolRun(
            settings=DEFAULT_SETTINGS, protocol_id="", model_ids={"m": "1"}, manifest=manifest
        )
    with pytest.raises(ProtocolRunError):
        ProtocolRun(settings=DEFAULT_SETTINGS, protocol_id="p", model_ids={"m": "1"})
    with pytest.raises(ProtocolRunError):
        ProtocolRun(
            settings=DEFAULT_SETTINGS,
            protocol_id="p",
            model_ids={"m": "1"},
            manifest=manifest,
            status="done",
        )
    with pytest.raises(ProtocolRunError):
        ProtocolRun(
            settings=DEFAULT_SETTINGS,
            protocol_id="p",
            model_ids={"m": "1"},
            status="not_verified",
            reasons=("missing binary",),
        )


def test_runner_returning_mismatched_settings_rejected(tmp_path: Path):
    base = _write_small(tmp_path)

    def mismatched(settings: ProtocolSettings, base_manifest: Path, *, seed: int) -> ProtocolRun:
        return ProtocolRun(
            settings=DEFAULT_SETTINGS,
            protocol_id="synthetic-mismatch-v1",
            model_ids={"m": "1"},
            manifest=_read_manifest(base_manifest),
        )

    with pytest.raises(ProtocolRunError):
        run_ab_comparison(
            "geometry",
            DEFAULT_SETTINGS,
            DEFAULT_SETTINGS.with_axis("geometry", "censo-default"),
            base,
            mismatched,
            n_resamples=_N_RESAMPLES,
            now=_PINNED_NOW,
        )


def test_module_runbook_disclaimer_and_axis_coverage():
    import tests.benchmark.nmr.protocol_transfer as protocol_transfer

    docstring = protocol_transfer.__doc__ or ""
    for axis in AXES:
        assert axis in docstring
    assert "applicability illustration, NOT evidence of model suitability" in docstring
