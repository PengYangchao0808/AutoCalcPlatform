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
    TsmodeError,
    TsmodeOptimizationSettings,
    TsmodeRequest,
)
from acp.calculations.tsmode.engine import TsmodeEngine, compute_engine_fingerprint
from acp.calculations.tsmode.source import load_bundle_from_files
from tests.tsmode_synthetic import make_consistent_pair


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
    artifacts = (
        [ArtifactRef(path=log_path, type="log")] if log_path is not None else []
    )
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
        final_vectors = {
            mode.source_mode_index: np.asarray(mode.vectors)
            for mode in bundle.modes
        }
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
            return _ok_frequency_result(
                list(final_freqs.values()), log_text=None, tmp_path=None
            )

        monkeypatch.setattr(
            "acp.calculations.tsmode.engine.run_optimize", fake_optimize
        )
        monkeypatch.setattr(
            "acp.calculations.tsmode.engine.run_frequency", fake_frequency
        )

        engine = TsmodeEngine(config={})
        result = engine.run(_request(), bundle, tmp_path / "task")

        assert result.workflow_result.status == "completed"
        result_dir = tmp_path / "task" / "RESULT" / "tsmode"
        assert (result_dir / "target_resolution.json").is_file()
        assert (result_dir / "tsmode_report.json").is_file()
        assert (result_dir / "optimized.xyz").is_file()
        assert (result_dir / "normal_modes.json").is_file()
        manifest = json.loads(
            (tmp_path / "task" / "RESULT" / "result_manifest.json").read_text()
        )
        product_ids = {product["id"] for product in manifest["products"]}
        assert "tsmode_optimized" in product_ids
        assert "tsmode_normal_modes" in product_ids
        assert "tsmode_report" in product_ids
        structure = next(
            p for p in manifest["products"] if p["id"] == "tsmode_optimized"
        )
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
        assert payload["optimization"]["status"] == "completed"
        assert payload["frequency"]["status"] == "completed"

        optimize_req = calls["optimize"]
        assert optimize_req.resources["ts_mode"] == 0
        assert optimize_req.resources["initial_hessian"] == "read"
        assert optimize_req.resources["structure_kind"] == "ts"
        assert Path(optimize_req.resources["hess_file"]).name == "source.hess"

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
        del payload["frequency"]
        checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")

        engine.run(request, bundle, task_root)
        assert len(optimize_calls) == 1  # resumed, not re-run
        assert len(frequency_calls) == 2


class TestEngineFailures:
    def test_gate_blocks_when_verification_required(self, tmp_path, bundle, monkeypatch):
        def must_not_run(req):
            raise AssertionError("optimize must not run behind the gate")

        monkeypatch.setattr(
            "acp.calculations.tsmode.engine.run_optimize", must_not_run
        )
        engine = TsmodeEngine(config={})
        with pytest.raises(TsmodeError) as excinfo:
            engine.run(_request(require_verified_mapping=True), bundle, tmp_path / "t")
        assert excinfo.value.error_code == MODE_MAPPING_UNSUPPORTED

    def test_optimize_failure_publishes_failed_report(self, tmp_path, bundle, monkeypatch):
        monkeypatch.setattr(
            "acp.calculations.tsmode.engine.run_optimize",
            lambda req: CalculationResult(
                status="failed", errors=["ORCA crashed [crash_timeout]"]
            ),
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

    def test_optimize_success_frequency_failure_is_recoverable(
        self, tmp_path, bundle, monkeypatch
    ):
        optimized = np.asarray(bundle.coordinates_angstrom) + 0.01
        monkeypatch.setattr(
            "acp.calculations.tsmode.engine.run_optimize",
            lambda req: _ok_optimize_result(optimized),
        )
        monkeypatch.setattr(
            "acp.calculations.tsmode.engine.run_frequency",
            lambda req: CalculationResult(
                status="failed", errors=["SCF failure"]
            ),
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
            (
                tmp_path / "task" / "WORK" / "tsmode" / "tsmode_checkpoint.json"
            ).read_text()
        )
        assert checkpoint["optimization"]["status"] == "completed"
        assert "frequency" not in checkpoint or checkpoint["frequency"]["status"] != "completed"


class TestFingerprint:
    def test_target_change_invalidates(self, bundle):
        from acp.calculations.tsmode.mode_mapping import resolve_target_mode

        first = resolve_target_mode(
            bundle, bundle.imaginary_modes()[0].source_mode_index
        )
        second_mode = bundle.imaginary_modes()[1]
        second = resolve_target_mode(bundle, second_mode.source_mode_index)
        base = TsmodeOptimizationSettings(require_verified_mapping=False)
        fp1 = compute_engine_fingerprint(bundle, first, base)
        fp2 = compute_engine_fingerprint(bundle, second, base)
        assert fp1 != fp2

    def test_settings_change_invalidates(self, bundle):
        from acp.calculations.tsmode.mode_mapping import resolve_target_mode

        resolution = resolve_target_mode(
            bundle, bundle.imaginary_modes()[0].source_mode_index
        )
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
