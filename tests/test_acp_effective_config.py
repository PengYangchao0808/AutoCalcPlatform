"""Tests for BatchOptimize effective-config traceability (plan §5.5 / P2c).

Covers:
- Submission-time: effective_config.json written for BatchOptimize, absent for others
- Engine provenance: batch_provenance.json with per-item effective config + rescue events
- Detail API: effective_config field in job detail response
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from acp.backends.base import QCResult
from acp.calculations.batch.engine import BatchOptimizeEngine
from acp.calculations.batch.models import BatchStructureItem
from acp.calculations.batch.options import BatchMethodOptions


class TestEffectiveConfigModule:
    def test_build_opts_from_method_dict_basic(self) -> None:
        from acp.calculations.batch.effective_config import build_opts_from_method_dict

        opts = build_opts_from_method_dict({
            "functional": "wB97X-D4",
            "basis": "def2-TZVPP",
        })
        assert opts.optimization_method == "wB97X-D4"
        assert opts.optimization_basis == "def2-TZVPP"

    def test_build_opts_from_method_dict_advanced(self) -> None:
        from acp.calculations.batch.effective_config import build_opts_from_method_dict

        opts = build_opts_from_method_dict({
            "functional": "wB97X-D4",
            "basis": "def2-TZVPP",
            "opt_max_iter": 400,
            "opt_trust_radius": 0.1,
            "opt_initial_hessian": "calculate",
            "opt_recalc_hess": 10,
            "scf_max_iter": 500,
            "scf_convergence": "verytight",
            "scf_strategy": "slowconv",
        })
        assert opts.opt_max_iter == 400
        assert opts.opt_trust_radius == 0.1
        assert opts.opt_initial_hessian == "calculate"
        assert opts.opt_recalc_hess == 10
        assert opts.scf_max_iter == 500
        assert opts.scf_convergence == "verytight"
        assert opts.scf_strategy == "slowconv"

    def test_build_batch_effective_config_roles(self) -> None:
        from acp.calculations.batch.effective_config import build_batch_effective_config

        opts = BatchMethodOptions(
            optimization_method="wB97X-D4",
            optimization_basis="def2-TZVPP",
            opt_trust_radius=0.15,
        )
        config = build_batch_effective_config(opts)
        assert config["schema"] == "batch_optimize_effective_v1"
        assert "int" in config["roles"]
        assert "ts" in config["roles"]
        assert config["roles"]["int"]["opt_trust_radius"] == 0.15
        assert config["roles"]["ts"]["opt_trust_radius"] == 0.15

    def test_ts_role_default_when_common_unset(self) -> None:
        from acp.calculations.batch.effective_config import build_batch_effective_config

        opts = BatchMethodOptions(
            optimization_method="wB97X-D4",
            opt_trust_radius=None,
        )
        config = build_batch_effective_config(opts)
        assert "opt_trust_radius" not in config["roles"]["int"]
        assert config["roles"]["ts"]["opt_trust_radius"] == 0.3

    def test_compute_effective_from_method(self) -> None:
        from acp.calculations.batch.effective_config import compute_effective_from_method

        config = compute_effective_from_method({
            "functional": "r2SCAN-3c",
            "opt_max_iter": 300,
        })
        assert config["schema"] == "batch_optimize_effective_v1"
        assert config["roles"]["int"]["max_cycles"] == 300
        assert config["roles"]["ts"]["max_cycles"] == 300

    def test_orca_summary_int(self) -> None:
        from acp.calculations.batch.effective_config import (
            build_batch_effective_config,
            build_orca_summary,
        )

        opts = BatchMethodOptions(
            opt_convergence="tight",
            scf_convergence="tight",
            opt_max_iter=400,
        )
        config = build_batch_effective_config(opts)
        summary = build_orca_summary(config["roles"]["int"])
        assert "TightOpt" in summary
        assert "TightSCF" in summary
        assert "MaxIter 400" in summary

    def test_orca_summary_ts_with_hessian(self) -> None:
        from acp.calculations.batch.effective_config import (
            build_batch_effective_config,
            build_orca_summary,
        )

        opts = BatchMethodOptions(
            opt_convergence="tight",
            scf_convergence="tight",
            opt_max_iter=400,
            transition_state_opt_trust_radius=0.1,
            transition_state_opt_initial_hessian="calculate",
            transition_state_opt_recalc_hess=5,
        )
        config = build_batch_effective_config(opts)
        summary = build_orca_summary(config["roles"]["ts"])
        assert "Trust 0.1" in summary
        assert "Calc_Hess" in summary
        assert "Recalc_Hess 5" in summary

    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        from acp.calculations.batch.effective_config import (
            read_effective_config,
            write_effective_config,
        )

        config = {"schema": "test", "roles": {"int": {}, "ts": {}}}
        write_effective_config(tmp_path, config)
        loaded = read_effective_config(tmp_path)
        assert loaded is not None
        assert loaded["schema"] == "test"

    def test_read_missing_returns_none(self, tmp_path: Path) -> None:
        from acp.calculations.batch.effective_config import read_effective_config

        assert read_effective_config(tmp_path) is None


def _wait_for_manager(manager: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with manager._lock:
            if not manager._submission_jobs:
                return
        time.sleep(0.05)


class TestSubmissionEffectiveConfig:
    def test_batch_optimize_writes_effective_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
        from acp.scheduler.jobs import JobSpec
        from acp.scheduler.manager import JobManager

        manager = JobManager(run_root=tmp_path, max_running=2)
        spec = JobSpec(
            workflow="BatchOptimize",
            name="test_batch",
            input={"source_type": "smiles", "input_string": "CCO"},
            method={
                "functional": "wB97X-D4",
                "basis": "def2-TZVPP",
                "opt_max_iter": 400,
                "opt_trust_radius": 0.1,
                "scf_convergence": "verytight",
            },
            resources={"nproc": 4, "mem": "8GB"},
        )
        record = manager.submit(spec)
        _wait_for_manager(manager)

        work_dir = Path(record.work_dir)
        effective_path = work_dir / "effective_config.json"
        if not effective_path.is_file():
            files = list(work_dir.iterdir()) if work_dir.exists() else []
            msg = f"effective_config.json not found; files: {files}"
            pytest.fail(msg)

        loaded = json.loads(effective_path.read_text(encoding="utf-8"))
        assert loaded["schema"] == "batch_optimize_effective_v1"
        assert "int" in loaded["roles"]
        assert "ts" in loaded["roles"]
        assert loaded["roles"]["int"]["max_cycles"] == 400
        assert loaded["roles"]["int"]["opt_trust_radius"] == 0.1
        assert loaded["roles"]["int"]["scf_convergence"] == "verytight"

    def test_non_batch_workflow_no_effective_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
        from acp.scheduler.jobs import JobSpec
        from acp.scheduler.manager import JobManager

        manager = JobManager(run_root=tmp_path, max_running=2)
        spec = JobSpec(
            workflow="Confsearch",
            name="test_conf",
            input={"source_type": "smiles", "input_string": "CCO"},
            method={"protocol": "xtb-crest"},
            resources={"nproc": 4, "mem": "8GB"},
        )
        record = manager.submit(spec)
        _wait_for_manager(manager)

        work_dir = Path(record.work_dir)
        effective_path = work_dir / "effective_config.json"
        assert not effective_path.is_file()


_ITEM_TS = BatchStructureItem(
    item_id="ts_001",
    name="TS candidate",
    tag="TS",
    xyz="2\nTAG: TS\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
    candidate_id="ts_001",
)

_ITEM_INT = BatchStructureItem(
    item_id="int_001",
    name="INT candidate",
    tag="INT",
    xyz="2\nTAG: INT\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
    candidate_id="int_001",
)


class TestEngineProvenance:
    def test_provenance_written(self, tmp_path: Path, fake_backend: Any) -> None:
        work_root = tmp_path / "task" / "WORK"
        result_root = tmp_path / "task" / "RESULT"
        engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)
        methods = BatchMethodOptions(
            optimization_method="wB97X-D4",
            optimization_basis="def2-TZVPP",
            opt_max_iter=400,
            opt_trust_radius=0.1,
        )
        engine.run([_ITEM_INT], profile="opt_only", charge=0, methods=methods)
        provenance_path = result_root / "batch_provenance.json"
        assert provenance_path.is_file()

        payload = json.loads(provenance_path.read_text(encoding="utf-8"))
        assert payload["schema"] == "batch_provenance_v1"
        assert len(payload["items"]) == 1
        item_prov = payload["items"][0]
        assert item_prov["item_id"] == "int_001"
        assert item_prov["role"] == "int"
        assert item_prov["effective_config"]["max_cycles"] == 400
        assert item_prov["effective_config"]["opt_level"] == "tight"
        assert item_prov["effective_config"]["method"] == "wB97X-D4"

    def test_provenance_ts_role_resolution(
        self, tmp_path: Path, fake_backend: Any
    ) -> None:
        work_root = tmp_path / "task" / "WORK"
        result_root = tmp_path / "task" / "RESULT"
        engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)
        methods = BatchMethodOptions(
            optimization_method="wB97X-D4",
            optimization_basis="def2-TZVPP",
            opt_trust_radius=0.1,
            transition_state_opt_trust_radius=0.15,
            transition_state_opt_initial_hessian="calculate",
            transition_state_opt_recalc_hess=3,
        )
        engine.run([_ITEM_TS], profile="opt_only", charge=0, methods=methods)

        payload = json.loads(
            (result_root / "batch_provenance.json").read_text(encoding="utf-8")
        )
        ts_prov = payload["items"][0]
        assert ts_prov["role"] == "ts"
        assert ts_prov["effective_config"]["trust_radius"] == 0.15
        assert ts_prov["effective_config"]["initial_hessian"] == "calculate"
        assert ts_prov["effective_config"]["recalc_hess"] == 3
        assert ts_prov["role_resolution"]["opt_trust_radius"] == 0.15

    def test_no_provenance_when_empty_items(
        self, tmp_path: Path, fake_backend: Any
    ) -> None:
        work_root = tmp_path / "task" / "WORK"
        result_root = tmp_path / "task" / "RESULT"
        engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)
        with pytest.raises(ValueError, match="at least one"):
            engine.run([], profile="opt_only", charge=0)


class TestRescueProvenance:
    def test_rescue_event_recorded(
        self, tmp_path: Path, fake_backend: Any
    ) -> None:
        coords = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]])

        fake_backend.set_results(
            "transition_state_opt",
            [
                RuntimeError("geometry_not_converged"),
                QCResult(
                    success=True,
                    energy=-1.0,
                    coordinates=coords,
                    symbols=["H", "H"],
                    converged=True,
                ),
            ],
        )
        work_root = tmp_path / "task" / "WORK"
        result_root = tmp_path / "task" / "RESULT"
        engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)
        methods = BatchMethodOptions(
            optimization_method="wB97X-D4",
            optimization_basis="def2-TZVPP",
            opt_rescue_policy="adaptive",
            opt_max_rescue=2,
        )
        engine.run([_ITEM_TS], profile="opt_only", charge=0, methods=methods)

        payload = json.loads(
            (result_root / "batch_provenance.json").read_text(encoding="utf-8")
        )
        ts_prov = payload["items"][0]
        assert "rescue" in ts_prov
        assert ts_prov["rescue"]["attempts"] >= 1

    def test_no_rescue_when_policy_off(
        self, tmp_path: Path, fake_backend: Any
    ) -> None:
        coords = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]])

        fake_backend.set_result(
            "transition_state_opt",
            QCResult(
                success=True,
                energy=-1.0,
                coordinates=coords,
                symbols=["H", "H"],
                converged=True,
            ),
        )
        work_root = tmp_path / "task" / "WORK"
        result_root = tmp_path / "task" / "RESULT"
        engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)
        methods = BatchMethodOptions(
            optimization_method="wB97X-D4",
            opt_rescue_policy="off",
        )
        engine.run([_ITEM_TS], profile="opt_only", charge=0, methods=methods)

        payload = json.loads(
            (result_root / "batch_provenance.json").read_text(encoding="utf-8")
        )
        ts_prov = payload["items"][0]
        assert "rescue" not in ts_prov


class TestDetailAPIEffectiveConfig:
    def test_batch_job_detail_has_effective_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
        from fastapi.testclient import TestClient

        from acp.api.server import create_app
        from acp.scheduler.jobs import JobSpec

        with TestClient(create_app(run_root=tmp_path, max_running=2)) as client:
            manager = client.app.state.job_manager
            spec = JobSpec(
                workflow="BatchOptimize",
                name="test_batch",
                input={"source_type": "smiles", "input_string": "CCO"},
                method={
                    "functional": "wB97X-D4",
                    "basis": "def2-TZVPP",
                    "opt_max_iter": 400,
                },
                resources={"nproc": 4, "mem": "8GB"},
            )
            record = manager.submit(spec)
            _wait_for_manager(manager)

            response = client.get(f"/api/v1/jobs/{record.id}/detail")
            assert response.status_code == 200
            data = response.json()
            assert data["effective_config"] is not None
            assert data["effective_config"]["schema"] == "batch_optimize_effective_v1"
            assert "int" in data["effective_config"]["roles"]
            assert "ts" in data["effective_config"]["roles"]

    def test_non_batch_job_detail_effective_config_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
        from fastapi.testclient import TestClient

        from acp.api.server import create_app
        from acp.scheduler.jobs import JobSpec

        with TestClient(create_app(run_root=tmp_path, max_running=2)) as client:
            manager = client.app.state.job_manager
            spec = JobSpec(
                workflow="Confsearch",
                name="test_conf",
                input={"source_type": "smiles", "input_string": "CCO"},
                method={"protocol": "xtb-crest"},
                resources={"nproc": 4, "mem": "8GB"},
            )
            record = manager.submit(spec)
            _wait_for_manager(manager)

            response = client.get(f"/api/v1/jobs/{record.id}/detail")
            assert response.status_code == 200
            data = response.json()
            assert data["effective_config"] is None


class TestEffectiveConfigRoundtrip:
    def test_advanced_fields_roundtrip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
        from fastapi.testclient import TestClient

        from acp.api.server import create_app
        from acp.scheduler.jobs import JobSpec

        with TestClient(create_app(run_root=tmp_path, max_running=2)) as client:
            manager = client.app.state.job_manager
            spec = JobSpec(
                workflow="BatchOptimize",
                name="test_batch",
                input={"source_type": "smiles", "input_string": "CCO"},
                method={
                    "functional": "wB97X-D4",
                    "basis": "def2-TZVPP",
                    "opt_max_iter": 400,
                    "opt_trust_radius": 0.1,
                    "opt_initial_hessian": "calculate",
                    "opt_recalc_hess": 5,
                    "scf_max_iter": 500,
                    "scf_convergence": "verytight",
                    "scf_strategy": "slowconv",
                },
                resources={"nproc": 4, "mem": "8GB"},
            )
            record = manager.submit(spec)
            _wait_for_manager(manager)

            response = client.get(f"/api/v1/jobs/{record.id}/detail")
            assert response.status_code == 200
            ec = response.json()["effective_config"]
            assert ec is not None
            int_role = ec["roles"]["int"]
            assert int_role["max_cycles"] == 400
            assert int_role["opt_level"] == "tight"
            assert int_role["opt_trust_radius"] == 0.1
            assert int_role["opt_initial_hessian"] == "calculate"
            assert int_role["opt_recalc_hess"] == 5
            assert int_role["scf_maxiter"] == 500
            assert int_role["scf_convergence"] == "verytight"
            assert int_role["scf_strategy"] == "slowconv"
