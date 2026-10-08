"""TS Mode engine orchestration tests (mocked QC primitives)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from acp.calculations.contracts import (
    ArtifactRef,
    CalculationResult,
)
from acp.calculations.tsmode.contracts import (
    MODE_MAPPING_UNSUPPORTED,
    SourceLevelOfTheory,
    TsmodeError,
    TsmodeOptimizationSettings,
    TsmodeRequest,
)
from acp.calculations.tsmode.engine import TsmodeEngine, compute_engine_fingerprint
from acp.calculations.tsmode.source import load_bundle_from_files
from acp.calculations.tsmode.validation import validate_ts_frequencies
from tests.tsmode_synthetic import make_consistent_pair, write_out_file


def test_tsmode_frequency_validation_counts_weak_imaginary_modes() -> None:
    weak = validate_ts_frequencies([-34.22, 100.0])
    assert weak.classification == "first_order_saddle_candidate"
    assert weak.all_imaginary_cm1 == [-34.22]
    assert weak.threshold_cm1 == 0.0

    second = validate_ts_frequencies([-34.22, -0.01, 100.0])
    assert second.classification == "higher_order_saddle"
    assert validate_ts_frequencies([0.0, 100.0]).classification == "no_imaginary"


def _request(**overrides) -> TsmodeRequest:
    settings = TsmodeOptimizationSettings(
        require_verified_mapping=overrides.pop("require_verified_mapping", False),
        recalc_hess=overrides.pop("recalc_hess", None),
    )
    return TsmodeRequest(
        source={"kind": "test"},
        source_mode_index=overrides.pop("source_mode_index", 8),
        optimization=settings,
        resources=overrides.pop("resources", {}),
        request_id="req_test",
    )


def _ok_optimize_result(coords):
    return CalculationResult(
        energy=-100.5,
        coords=[[float(v) for v in row] for row in coords],
        status="completed",
        artifacts=[
            ArtifactRef(path=Path("ts_opt.out"), type="output"),
            ArtifactRef(path=Path("ts_opt.log"), type="log"),
        ],
    )


def _ok_frequency_result(frequencies, vectors=None, log_text=None, tmp_path=None):
    log_path = None
    if log_text is not None and tmp_path is not None:
        log_path = tmp_path / "freq_final.out"
        log_path.write_text(log_text, encoding="utf-8")
    artifacts = [ArtifactRef(path=log_path, type="log")] if log_path is not None else []
    return CalculationResult(
        energy=-100.6,
        frequencies=list(frequencies),
        status="completed",
        artifacts=artifacts,
    )


@pytest.fixture()
def bundle(tmp_path):
    out_path, hess_path, _c, _f, _m = make_consistent_pair(tmp_path)
    return load_bundle_from_files(out_path, hess_path)


class TestEngineHappyPath:
    def test_completed_run_publishes_all_artifacts(self, tmp_path, bundle, monkeypatch):
        from tests.tsmode_synthetic import write_out_file

        optimized = np.asarray(bundle.coordinates_angstrom) + 0.02
        positives = sorted(
            (m.frequency_cm1 for m in bundle.modes if not m.is_imaginary),
            reverse=True,
        )
        final_freqs = {index: freq for index, freq in enumerate(positives)}
        final_freqs[len(positives)] = -430.0
        final_vectors = {mode.source_mode_index: np.asarray(mode.vectors) for mode in bundle.modes}
        freq_log = tmp_path / "final.out"
        write_out_file(
            freq_log,
            optimized,
            final_freqs,
            final_vectors,
        )

        calls = {}

        def fake_optimize(req):
            calls["optimize"] = req
            return _ok_optimize_result(optimized)

        def fake_frequency(req):
            calls["frequency"] = req
            return CalculationResult(
                energy=-100.6,
                frequencies=list(final_freqs.values()),
                status="completed",
                artifacts=[ArtifactRef(path=freq_log, type="log")],
            )

        monkeypatch.setattr("acp.calculations.tsmode.engine.run_optimize", fake_optimize)
        monkeypatch.setattr("acp.calculations.tsmode.engine.run_frequency", fake_frequency)

        engine = TsmodeEngine(config={})
        result = engine.run(_request(), bundle, tmp_path / "task")

        assert result.workflow_result.status == "completed"
        result_dir = tmp_path / "task" / "RESULT" / "tsmode"
        assert (result_dir / "target_resolution.json").is_file()
        assert (result_dir / "tsmode_report.json").is_file()
        assert (result_dir / "optimized.xyz").is_file()
        assert (result_dir / "normal_modes.json").is_file()
        manifest = json.loads((tmp_path / "task" / "RESULT" / "result_manifest.json").read_text())
        product_ids = {product["id"] for product in manifest["products"]}
        assert "tsmode_optimized" in product_ids
        assert "tsmode_normal_modes" in product_ids
        assert "tsmode_report" in product_ids
        structure = next(p for p in manifest["products"] if p["id"] == "tsmode_optimized")
        assert structure["metadata"]["role"] == "transition_state"

        report = json.loads((result_dir / "tsmode_report.json").read_text())
        assert report["schema_version"] == "tsmode_report_v1"
        assert report["execution_status"] == "completed"
        assert report["optimization_status"] == "completed"
        assert report["frequency_status"] == "completed"
        assert report["validation"]["classification"] == "first_order_saddle_candidate"
        assert len(report["imaginary_modes"]) == 1

        input_dir = tmp_path / "task" / "INPUT" / "tsmode"
        assert (input_dir / "source.hess").is_file()
        assert (input_dir / "source.xyz").is_file()
        assert (input_dir / "source_bundle.json").is_file()
        checkpoint = tmp_path / "task" / "WORK" / "tsmode" / "tsmode_checkpoint.json"
        payload = json.loads(checkpoint.read_text())
        assert payload["schema_version"] == "tsmode_checkpoint_v2"
        assert payload["optimize_credential"]["optimized_structure_sha256"]
        assert (
            payload["frequency_credential"]["adopted_optimized_structure_sha256"]
            == payload["optimize_credential"]["optimized_structure_sha256"]
        )

        optimize_req = calls["optimize"]
        assert optimize_req.resources["ts_mode"] == 0
        assert optimize_req.resources["initial_hessian"] == "read"
        assert optimize_req.resources["structure_kind"] == "ts"
        staged_hess = Path(optimize_req.resources["hess_file"])
        assert staged_hess == input_dir / "source.hess"
        # Raw-byte handoff: the path handed to transition_state_opt is the
        # STAGED snapshot, byte-identical to the bundle's source Hessian.
        assert staged_hess.read_bytes() == Path(bundle.hessian_file).read_bytes()

    def test_frequency_stage_resume_skips_optimize(self, tmp_path, bundle, monkeypatch):
        optimized = np.asarray(bundle.coordinates_angstrom) + 0.01
        optimize_calls = []
        frequency_calls = []

        monkeypatch.setattr(
            "acp.calculations.tsmode.engine.run_optimize",
            lambda req: (
                optimize_calls.append(req),
                _ok_optimize_result(optimized),
            )[1],
        )
        monkeypatch.setattr(
            "acp.calculations.tsmode.engine.run_frequency",
            lambda req: (
                frequency_calls.append(req),
                _ok_frequency_result([-400.0, 500.0, 900.0]),
            )[1],
        )

        engine = TsmodeEngine(config={})
        request = _request()
        task_root = tmp_path / "task"
        engine.run(request, bundle, task_root)
        assert len(optimize_calls) == 1
        assert len(frequency_calls) == 1

        # Simulate losing the frequency stage (e.g. crash after optimize):
        checkpoint_path = task_root / "WORK" / "tsmode" / "tsmode_checkpoint.json"
        payload = json.loads(checkpoint_path.read_text())
        del payload["frequency_credential"]
        checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")

        engine.run(request, bundle, task_root)
        assert len(optimize_calls) == 1  # resumed, not re-run
        assert len(frequency_calls) == 2


class TestParseFrequencyProducts:
    FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "qc" / "orca61"

    def test_out_log_not_shadowed_by_output_inp(self, tmp_path):
        # ORCA interface field order: type "output" = .inp route file (no
        # frequency table), type "log" = .out with the real table. The old
        # first-match selection grabbed the .inp and returned ({}, {}).
        inp = tmp_path / "freq.inp"
        inp.write_text(
            "! r2SCAN-3c OptTS NumFreq\n%pal nprocs 4 end\n\n"
            "* xyz 0 1\nC   0.0000000000   0.0000000000   0.0000000000\n*\n",
            encoding="utf-8",
        )
        out = self.FIXTURE_DIR / "ts_opt.out"
        result = CalculationResult(
            status="completed",
            artifacts=[
                ArtifactRef(path=inp, type="output"),
                ArtifactRef(path=out, type="log"),
            ],
        )
        frequency_map, vectors = TsmodeEngine._parse_frequency_products(result)
        assert len(frequency_map) > 0
        assert set(frequency_map) == {6, 7, 8}
        assert frequency_map[6] == pytest.approx(-1122.09, abs=0.01)
        assert 6 in vectors
        assert vectors[6].shape == (3, 3)

    def test_single_output_artifact_behavior_unchanged(self, tmp_path):
        # One matched artifact still wins even when it is the table-less
        # .inp — same ({}, {}) outcome as before the fix.
        inp = tmp_path / "freq.inp"
        inp.write_text("! r2SCAN-3c OptTS NumFreq\n", encoding="utf-8")
        result = CalculationResult(
            status="completed",
            artifacts=[ArtifactRef(path=inp, type="output")],
        )
        assert TsmodeEngine._parse_frequency_products(result) == ({}, {})

    def test_missing_log_falls_through_to_existing_output(self, tmp_path):
        # type "log" path missing on disk → the existing .out-suffixed
        # type "output" artifact must still be parsed.
        out = self.FIXTURE_DIR / "ts_opt.out"
        result = CalculationResult(
            status="completed",
            artifacts=[
                ArtifactRef(path=tmp_path / "vanished.out", type="log"),
                ArtifactRef(path=out, type="output"),
            ],
        )
        frequency_map, vectors = TsmodeEngine._parse_frequency_products(result)
        assert len(frequency_map) > 0
        assert frequency_map[6] == pytest.approx(-1122.09, abs=0.01)
        assert vectors


class TestEngineFailures:
    def test_gate_blocks_when_verification_required(self, tmp_path, bundle, monkeypatch):
        def must_not_run(req):
            raise AssertionError("optimize must not run behind the gate")

        monkeypatch.setattr("acp.calculations.tsmode.engine.run_optimize", must_not_run)
        engine = TsmodeEngine(config={})
        with pytest.raises(TsmodeError) as excinfo:
            engine.run(_request(require_verified_mapping=True), bundle, tmp_path / "t")
        assert excinfo.value.error_code == MODE_MAPPING_UNSUPPORTED

    def test_optimize_failure_publishes_failed_report(self, tmp_path, bundle, monkeypatch):
        monkeypatch.setattr(
            "acp.calculations.tsmode.engine.run_optimize",
            lambda req: CalculationResult(status="failed", errors=["ORCA crashed [crash_timeout]"]),
        )
        engine = TsmodeEngine(config={})
        result = engine.run(_request(), bundle, tmp_path / "task")
        assert result.workflow_result.status == "failed"
        report = json.loads(
            (tmp_path / "task" / "RESULT" / "tsmode" / "tsmode_report.json").read_text()
        )
        assert report["execution_status"] == "failed"
        assert report["optimization_status"] == "failed"
        assert report["frequency_status"] == "skipped"
        assert not (tmp_path / "task" / "RESULT" / "tsmode" / "optimized.xyz").exists()

    def test_optimize_success_frequency_failure_is_recoverable(self, tmp_path, bundle, monkeypatch):
        optimized = np.asarray(bundle.coordinates_angstrom) + 0.01
        monkeypatch.setattr(
            "acp.calculations.tsmode.engine.run_optimize",
            lambda req: _ok_optimize_result(optimized),
        )
        monkeypatch.setattr(
            "acp.calculations.tsmode.engine.run_frequency",
            lambda req: CalculationResult(status="failed", errors=["SCF failure"]),
        )
        engine = TsmodeEngine(config={})
        engine.run(_request(), bundle, tmp_path / "task")
        report = json.loads(
            (tmp_path / "task" / "RESULT" / "tsmode" / "tsmode_report.json").read_text()
        )
        assert report["optimization_status"] == "completed"
        assert report["frequency_status"] == "failed"
        assert report["validation"]["classification"] == "not_verified"
        checkpoint = json.loads(
            (tmp_path / "task" / "WORK" / "tsmode" / "tsmode_checkpoint.json").read_text()
        )
        assert checkpoint["optimize_credential"]["optimized_structure_sha256"]
        assert "frequency_credential" not in checkpoint


def _read_report(tmp_path):
    return json.loads((tmp_path / "task" / "RESULT" / "tsmode" / "tsmode_report.json").read_text())


def _completed_engine_run(tmp_path, level, monkeypatch):
    out_path, hess_path, _coords, _freqs, _modes = make_consistent_pair(tmp_path)
    bundle = load_bundle_from_files(out_path, hess_path, level=level)
    optimized = np.asarray(bundle.coordinates_angstrom) + 0.02
    positives = sorted(
        (mode.frequency_cm1 for mode in bundle.modes if not mode.is_imaginary), reverse=True
    )
    final_freqs = {index: freq for index, freq in enumerate(positives)}
    final_freqs[len(positives)] = -430.0
    final_vectors = {mode.source_mode_index: np.asarray(mode.vectors) for mode in bundle.modes}
    freq_log = tmp_path / "final.out"
    write_out_file(freq_log, optimized, final_freqs, final_vectors)

    monkeypatch.setattr(
        "acp.calculations.tsmode.engine.run_optimize",
        lambda req: _ok_optimize_result(optimized),
    )
    monkeypatch.setattr(
        "acp.calculations.tsmode.engine.run_frequency",
        lambda req: CalculationResult(
            energy=-100.6,
            frequencies=list(final_freqs.values()),
            status="completed",
            artifacts=[ArtifactRef(path=freq_log, type="log")],
        ),
    )
    TsmodeEngine(config={}).run(_request(), bundle, tmp_path / "task")
    return bundle


class TestLevelReportContract:
    def test_report_has_flat_resolved_and_source_level(self, tmp_path, monkeypatch):
        level = SourceLevelOfTheory(method="PBE0", basis="def2-TZVP", dispersion="D4")
        _completed_engine_run(tmp_path, level, monkeypatch)
        report = _read_report(tmp_path)
        assert report["resolved_level"] == {
            "method": "PBE0",
            "basis": "def2-TZVP",
            "dispersion": "D4",
        }
        assert report["source_level"] == report["resolved_level"]
        assert "effective_level" not in report["resolved_level"]
        assert "effective_level" not in report

    def test_report_attaches_stage_input_digests(self, tmp_path, monkeypatch):
        level = SourceLevelOfTheory(method="PBE0", basis="def2-TZVP", dispersion="D4")
        _completed_engine_run(tmp_path, level, monkeypatch)
        report = _read_report(tmp_path)
        staged = {Path(entry["path"]).name: entry for entry in report["artifacts"]}
        expected_inputs = {
            "source.hess",
            "source.xyz",
            "source_modes.json",
            "source_bundle.json",
        }
        assert expected_inputs <= set(staged)
        assert all(len(entry["sha256"]) == 64 for entry in report["artifacts"])
        snapshot = json.loads(
            (tmp_path / "task" / "INPUT" / "tsmode" / "source_bundle.json").read_text()
        )
        assert snapshot["level"]["basis"] == "def2-TZVP"

    def test_empty_level_is_explicitly_unconfirmed(self, tmp_path, monkeypatch):
        level = SourceLevelOfTheory(method="", basis="")
        _completed_engine_run(tmp_path, level, monkeypatch)
        report = _read_report(tmp_path)
        assert report["source_level"]["basis"] == ""
        assert any("unconfirmed" in warning.lower() for warning in report["warnings"])


class TestFingerprint:
    def test_target_change_invalidates(self, bundle):
        from acp.calculations.tsmode.mode_mapping import resolve_target_mode

        first = resolve_target_mode(bundle, bundle.imaginary_modes()[0].source_mode_index)
        second_mode = bundle.imaginary_modes()[1]
        second = resolve_target_mode(bundle, second_mode.source_mode_index)
        base = TsmodeOptimizationSettings(require_verified_mapping=False)
        fp1 = compute_engine_fingerprint(bundle, first, base)
        fp2 = compute_engine_fingerprint(bundle, second, base)
        assert fp1 != fp2

    def test_settings_change_invalidates(self, bundle):
        from acp.calculations.tsmode.mode_mapping import resolve_target_mode

        resolution = resolve_target_mode(bundle, bundle.imaginary_modes()[0].source_mode_index)
        fp1 = compute_engine_fingerprint(
            bundle, resolution, TsmodeOptimizationSettings(recalc_hess=None)
        )
        fp2 = compute_engine_fingerprint(
            bundle, resolution, TsmodeOptimizationSettings(recalc_hess=5)
        )
        assert fp1 != fp2


class TestCliGateSmoke:
    def test_cli_blocks_unverified_mapping_by_default(self, tmp_path, bundle):
        """`acp run tsmode` refuses directed execution while the P0 real-ORCA
        verification matrix is empty (plan §6.3 launch hard gate)."""
        import json
        import subprocess
        import sys

        (tmp_path / "bundle.json").write_text(
            json.dumps(
                {
                    "schema_version": "tsmode_bundle_v1",
                    "files": {"output": "freq.out", "hessian": "freq.hess"},
                    "charge": 0,
                    "multiplicity": 1,
                    "origin": {"kind": "test"},
                }
            ),
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "acp.cli",
                "run",
                "tsmode",
                "--source-bundle",
                str(tmp_path / "bundle.json"),
                "--source-mode-index",
                "8",
                "--output",
                str(tmp_path / "task"),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 2
        combined = result.stdout + result.stderr
        assert "mode_mapping_unsupported" in combined


def _credential_harness(tmp_path, monkeypatch):
    """Mocked stages that rewrite the frequency log on every call.

    The rewrite is what lets a test tamper the on-disk artifact and then see
    whether the engine adopted the stale credential or re-ran the stage.
    """
    import acp.calculations.tsmode.engine as engine_module

    out_path, hess_path, _coords, freqs_by_native, modes_by_native = make_consistent_pair(tmp_path)
    bundle = load_bundle_from_files(out_path, hess_path)
    optimized = np.asarray(bundle.coordinates_angstrom) + 0.02
    log_path = tmp_path / "freq_final.out"
    frequencies = [freqs_by_native[index] for index in sorted(freqs_by_native)]
    calls = {"optimize": 0, "frequency": 0}

    def fake_optimize(req):
        calls["optimize"] += 1
        return CalculationResult(
            energy=-100.5,
            coords=[[float(v) for v in row] for row in optimized],
            status="completed",
            artifacts=[ArtifactRef(path=Path("ts_opt.out"), type="output")],
        )

    def fake_frequency(req):
        calls["frequency"] += 1
        write_out_file(log_path, optimized, freqs_by_native, modes_by_native)
        return CalculationResult(
            energy=-100.6,
            frequencies=list(frequencies),
            status="completed",
            artifacts=[ArtifactRef(path=log_path, type="log")],
        )

    monkeypatch.setattr(engine_module, "run_optimize", fake_optimize)
    monkeypatch.setattr(engine_module, "run_frequency", fake_frequency)
    return bundle, optimized, log_path, freqs_by_native, modes_by_native, calls


def _checkpoint_file(task_root: Path) -> Path:
    return task_root / "WORK" / "tsmode" / "tsmode_checkpoint.json"


def _read_checkpoint(task_root: Path) -> dict:
    return json.loads(_checkpoint_file(task_root).read_text(encoding="utf-8"))


def _write_checkpoint(task_root: Path, payload: dict) -> None:
    _checkpoint_file(task_root).write_text(json.dumps(payload), encoding="utf-8")


class TestCheckpointV2Credentials:
    def test_new_run_freezes_v2_credentials(self, tmp_path, monkeypatch):
        bundle, _opt, _log, _freqs, _modes, calls = _credential_harness(tmp_path, monkeypatch)
        task_root = tmp_path / "task"
        TsmodeEngine(config={}).run(_request(), bundle, task_root)
        assert calls == {"optimize": 1, "frequency": 1}

        payload = _read_checkpoint(task_root)
        assert payload["schema_version"] == "tsmode_checkpoint_v2"
        optimize = payload["optimize_credential"]
        assert optimize["source_content_sha256"] == bundle.source_revision()
        assert optimize["target_mode_id"] == "tm_test123" or optimize["target_mode_id"]
        assert optimize["optimized_structure_sha256"]
        assert optimize["effective_level"] == bundle.level.to_dict()
        frequency = payload["frequency_credential"]
        assert (
            frequency["adopted_optimized_structure_sha256"]
            == optimize["optimized_structure_sha256"]
        )
        assert frequency["artifacts"], "exact frequency artifact path + digest not recorded"
        assert frequency["expected_mode_indices"]
        assert payload["publication"]["status"] == "published"

    def test_coordinate_tamper_triggers_optimize_recompute(self, tmp_path, monkeypatch):
        bundle, _opt, _log, _freqs, _modes, calls = _credential_harness(tmp_path, monkeypatch)
        task_root = tmp_path / "task"
        engine = TsmodeEngine(config={})
        engine.run(_request(), bundle, task_root)
        assert calls == {"optimize": 1, "frequency": 1}

        payload = _read_checkpoint(task_root)
        payload["optimize_credential"]["coordinates"][0][0] += 0.5
        _write_checkpoint(task_root, payload)

        engine.run(_request(), bundle, task_root)
        assert calls == {"optimize": 2, "frequency": 2}

    def test_bad_coordinate_shape_invalid(self, tmp_path, monkeypatch):
        bundle, _opt, _log, _freqs, _modes, calls = _credential_harness(tmp_path, monkeypatch)
        task_root = tmp_path / "task"
        engine = TsmodeEngine(config={})
        engine.run(_request(), bundle, task_root)

        payload = _read_checkpoint(task_root)
        payload["optimize_credential"]["coordinates"] = [[0.0, 0.0, 0.0]]
        _write_checkpoint(task_root, payload)

        engine.run(_request(), bundle, task_root)
        assert calls == {"optimize": 2, "frequency": 2}

    def test_effective_default_config_change_invalidates(self, tmp_path, monkeypatch):
        import acp.calculations.tsmode.engine as engine_module

        bundle, _opt, _log, _freqs, _modes, calls = _credential_harness(tmp_path, monkeypatch)
        task_root = tmp_path / "task"
        monkeypatch.setattr(engine_module, "load_config", lambda overrides=None: {"marker": "A"})
        TsmodeEngine(config={}).run(_request(), bundle, task_root)
        assert calls == {"optimize": 1, "frequency": 1}

        monkeypatch.setattr(engine_module, "load_config", lambda overrides=None: {"marker": "B"})
        TsmodeEngine(config={}).run(_request(), bundle, task_root)
        assert calls == {"optimize": 2, "frequency": 2}

    def test_old_schema_conservative_recompute_despite_parseable_log(self, tmp_path, monkeypatch):
        bundle, optimized, _log, freqs, modes, calls = _credential_harness(tmp_path, monkeypatch)
        task_root = tmp_path / "task"
        tsmode_dir = task_root / "WORK" / "tsmode"
        freq_dir = tsmode_dir / "frequency"
        freq_dir.mkdir(parents=True)
        write_out_file(freq_dir / "freq.out", optimized, freqs, modes)
        (tsmode_dir / "tsmode_checkpoint.json").write_text(
            json.dumps(
                {
                    "schema_version": "tsmode_checkpoint_v1",
                    "fingerprint": "fp_legacy",
                    "optimization": {
                        "status": "completed",
                        "coordinates": optimized.tolist(),
                    },
                    "frequency": {
                        "status": "completed",
                        "frequencies": [freqs[index] for index in sorted(freqs)],
                    },
                }
            ),
            encoding="utf-8",
        )

        TsmodeEngine(config={}).run(_request(), bundle, task_root)
        assert calls == {"optimize": 1, "frequency": 1}

    def test_same_path_log_replacement_invalidates_frequency(self, tmp_path, monkeypatch):
        bundle, optimized, log_path, freqs, modes, calls = _credential_harness(
            tmp_path, monkeypatch
        )
        task_root = tmp_path / "task"
        engine = TsmodeEngine(config={})
        engine.run(_request(), bundle, task_root)
        assert calls == {"optimize": 1, "frequency": 1}

        # Parseable replacement at the recorded path must still be rejected.
        write_out_file(log_path, optimized + 0.25, freqs, modes)

        engine.run(_request(), bundle, task_root)
        assert calls["optimize"] == 1, "optimize credential was needlessly invalidated"
        assert calls["frequency"] == 2

    def test_parseable_old_structure_log_invalidates_frequency(self, tmp_path, monkeypatch):
        bundle, optimized, log_path, freqs, modes, calls = _credential_harness(
            tmp_path, monkeypatch
        )
        task_root = tmp_path / "task"
        engine = TsmodeEngine(config={})
        engine.run(_request(), bundle, task_root)

        other = optimized[::-1, :].copy()
        write_out_file(log_path, other, freqs, modes)

        engine.run(_request(), bundle, task_root)
        assert calls["optimize"] == 1
        assert calls["frequency"] == 2

    def test_incomplete_mode_vectors_invalidates_frequency(self, tmp_path, monkeypatch):
        bundle, _opt, _log, _freqs, _modes, calls = _credential_harness(tmp_path, monkeypatch)
        task_root = tmp_path / "task"
        engine = TsmodeEngine(config={})
        engine.run(_request(), bundle, task_root)

        payload = _read_checkpoint(task_root)
        payload["frequency_credential"]["modes"][0]["vectors"] = []
        _write_checkpoint(task_root, payload)

        engine.run(_request(), bundle, task_root)
        assert calls["optimize"] == 1
        assert calls["frequency"] == 2

    def test_publish_only_failure_republishes_with_zero_qc(self, tmp_path, monkeypatch):
        bundle, _opt, _log, _freqs, _modes, calls = _credential_harness(tmp_path, monkeypatch)
        task_root = tmp_path / "task"
        engine = TsmodeEngine(config={})
        engine.run(_request(), bundle, task_root)
        normal_modes = task_root / "RESULT" / "tsmode" / "normal_modes.json"
        first = json.loads(normal_modes.read_text(encoding="utf-8"))
        assert all(mode["vectors"] for mode in first["modes"])

        normal_modes.unlink()
        engine.run(_request(), bundle, task_root)

        assert calls == {"optimize": 1, "frequency": 1}, "publish retry re-ran QC"
        second = json.loads(normal_modes.read_text(encoding="utf-8"))
        assert second["modes"] == first["modes"]

    def test_wrapper_write_failure_recovers_with_zero_qc(self, tmp_path, monkeypatch):
        bundle, _opt, _log, _freqs, _modes, calls = _credential_harness(tmp_path, monkeypatch)
        task_root = tmp_path / "task"
        engine = TsmodeEngine(config={})
        engine.run(_request(), bundle, task_root)
        assert not (task_root / "WORK" / "tsmode" / "frequency" / "normal_modes.json").exists()

        engine.run(_request(), bundle, task_root)
        assert calls == {"optimize": 1, "frequency": 1}
        normal_modes = json.loads(
            (task_root / "RESULT" / "tsmode" / "normal_modes.json").read_text(encoding="utf-8")
        )
        assert all(mode["vectors"] for mode in normal_modes["modes"])

    def test_final_publish_failure_recovers_with_zero_qc(self, tmp_path, monkeypatch):
        bundle, _opt, _log, _freqs, _modes, calls = _credential_harness(tmp_path, monkeypatch)
        task_root = tmp_path / "task"
        engine = TsmodeEngine(config={})
        real_publish = TsmodeEngine._publish
        state = {"armed": True}

        def flaky_publish(self, root, result_dir, report, *, optimized_xyz, normal_modes):
            if state["armed"] and report.execution_status == "completed":
                state["armed"] = False
                raise OSError("simulated final publish failure")
            return real_publish(
                self,
                root,
                result_dir,
                report,
                optimized_xyz=optimized_xyz,
                normal_modes=normal_modes,
            )

        monkeypatch.setattr(TsmodeEngine, "_publish", flaky_publish)
        with pytest.raises(OSError):
            engine.run(_request(), bundle, task_root)
        assert calls["frequency"] == 1
        assert state["armed"] is False

        monkeypatch.setattr(TsmodeEngine, "_publish", real_publish)
        engine.run(_request(), bundle, task_root)
        assert calls == {"optimize": 1, "frequency": 1}, "publish-only retry re-ran QC"
