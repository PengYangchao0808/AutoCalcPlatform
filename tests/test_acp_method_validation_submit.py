# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportCallIssue=false, reportUnknownMemberType=false
"""T14: method-config validation enforced at submit/run time.

Channels covered (plan cccp-correctness-hardening T14, user review item 3):
* SUBMIT hard-reject — POST /api/v1/jobs returns 422 for invalid GFN
  configs (strict entry) and the runner path fails fast on whatever still
  reaches it (non-migratable combos).
* Warning channel 1 — edit-recalculate PREVIEW response ``warnings``.
* Warning channel 2 — CLI stderr (explicit print; CLI logging goes to
  stdout, so logger-only output does NOT satisfy this channel).
* Warning channel 3 — job record/events ``method_validation_warning``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from acp.calculations.pes.scan import run_pes_scan
from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.storage.layout import runtime_file
from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint, RelaxedScanResult

_XYZ = """6
ethylene
C   0.000000   0.000000   0.000000
C   1.339000   0.000000   0.000000
H  -0.506000   0.934000   0.000000
H  -0.506000  -0.934000   0.000000
H   1.845000   0.934000   0.000000
H   1.845000  -0.934000   0.000000
"""

_COORDS = np.array(
    [
        [0.0, 0.0, 0.0],
        [1.339, 0.0, 0.0],
        [-0.506, 0.934, 0.0],
        [-0.506, -0.934, 0.0],
        [1.845, 0.934, 0.0],
        [1.845, -0.934, 0.0],
    ]
)
_SYMBOLS = ["C", "C", "H", "H", "H", "H"]

_COORDINATE = {
    "kind": "distance",
    "atoms": [0, 1],
    "start": 1.2,
    "end": 2.5,
    "n_points": 5,
}


# ---------------------------------------------------------------------------
# shared fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    import acp.scheduler.capabilities as capabilities_module
    import acp.scheduler.manager as manager_module

    monkeypatch.setattr(capabilities_module, "local_satisfies", lambda required: True)
    monkeypatch.setattr(manager_module, "local_satisfies", lambda required: True)
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=2)
    with TestClient(app) as test_client:
        manager: JobManager = test_client.app.state.job_manager
        manager._execute_submission = lambda job_id: None  # type: ignore[method-assign]
        yield test_client


def _pes_input(protocol: dict[str, Any]) -> dict[str, Any]:
    source = {"source_type": "xyz_text", "xyz_text": _XYZ, "charge": 0, "multiplicity": 1}
    return {"source": source, "coordinate": dict(_COORDINATE), "protocol": protocol}


def _pes_payload(protocol: dict[str, Any], name: str = "pes-t14") -> dict[str, Any]:
    return {
        "workflow": "PESsearch",
        "name": name,
        "method": {"mode": "bond_length_scan"},
        "input": _pes_input(protocol),
    }


def _seed_pes_job(client: TestClient, job_id: str, protocol: dict[str, Any]) -> JobRecord:
    manager: JobManager = client.app.state.job_manager
    work_dir = Path(manager.run_root) / "default" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "input.xyz").write_text(_XYZ, encoding="utf-8")
    record = JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow="PESsearch",
            input=dict(_pes_input(protocol), scan_request=dict(_pes_input(protocol))),
            method={"mode": "bond_length_scan"},
            resources={"nproc": 8},
        ),
        status=JobStatus.COMPLETED,
        work_dir=str(work_dir),
        project_id=manager.default_project_id,
    )
    manager.store.create(record)
    return record


def _scan_point(index: int, target: float) -> RelaxedScanPoint:
    coords = _COORDS.copy()
    coords[1, 0] = coords[0, 0] + target
    return RelaxedScanPoint(
        frame_index=index,
        progress=index / 4.0,
        coordinates=coords,
        symbols=list(_SYMBOLS),
        energy_hartree=-1.0,
        success=True,
        coordinate_values={"distance": target},
        metadata={},
    )


def _fake_scan_result(n_points: int, output_dir: Path) -> RelaxedScanResult:
    points = [_scan_point(i, 1.2 + i * (2.5 - 1.2) / max(n_points - 1, 1)) for i in range(n_points)]
    return RelaxedScanResult(
        points=points,
        input_xyz=output_dir / "input.xyz",
        scan_dir=output_dir,
        success=True,
    )


def _pes_args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "log_level": "ERROR",
        "mode": "bond_length_scan",
        "output": str(tmp_path / "task"),
        "config": None,
        "nproc": None,
        "mem": None,
        "scan_config": None,
        "source_type": None,
        "xyz_text": _XYZ,
        "asset_path": None,
        "from_manifest": None,
        "from_job": None,
        "from_frame": None,
        "from_artifact": None,
        "charge": None,
        "multiplicity": None,
        "reaction": None,
        "strategy": None,
        "input_xyz": None,
        "coordinates": None,
        "points": None,
        "scan_kind": None,
        "scan_bond_type": None,
        "selection_kind": None,
        "scan_atoms": "0,1",
        "scan_start": 1.2,
        "scan_end": 2.5,
        "scan_points": 5,
        "scan_method": None,
        "scan_basis": None,
        "scan_dispersion": None,
        "scan_solvent_model": None,
        "scan_solvent": None,
        "scan_grid": None,
        "scan_scf_convergence": None,
        "scan_scf_max_iter": None,
        "scan_ri_approximation": None,
        "sp_method": None,
        "sp_basis": None,
        "no_sp": True,
        "max_iterations": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# SUBMIT hard-reject (strict entry) + runner defense in depth
# ---------------------------------------------------------------------------


class TestSubmitRejection:
    def test_api_submit_rejects_gfn_optimizer_basis(self, client: TestClient) -> None:
        protocol = {"scan_optimizer": {"method": "GFN2-xTB", "basis": "def2-SVP"}}
        response = client.post("/api/v1/jobs", json=_pes_payload(protocol))
        assert response.status_code == 422
        detail = str(response.json()["detail"])
        assert "invalid scan optimizer level" in detail
        assert "basis" in detail
        manager: JobManager = client.app.state.job_manager
        assert manager.list_jobs(limit=10) == [], "rejected submission must not be queued"

    def test_api_submit_rejects_gfn_single_point_basis(self, client: TestClient) -> None:
        protocol = {
            "scan_optimizer": {"method": "GFN2-xTB"},
            "single_point": {"enabled": True, "method": "GFN2-xTB", "basis": "def2-SVP"},
        }
        response = client.post("/api/v1/jobs", json=_pes_payload(protocol))
        assert response.status_code == 422
        assert "invalid single_point level" in str(response.json()["detail"])

    def test_api_submit_rejects_gfn_solvent_model(self, client: TestClient) -> None:
        protocol = {
            "scan_optimizer": {
                "method": "GFN2-xTB",
                "solvent_model": "smd",
                "solvent": "water",
            }
        }
        response = client.post("/api/v1/jobs", json=_pes_payload(protocol))
        assert response.status_code == 422
        assert "ALPB" in str(response.json()["detail"])

    def test_api_submit_accepts_valid_gfn_alpb(self, client: TestClient) -> None:
        protocol = {
            "scan_optimizer": {
                "method": "GFN2-xTB",
                "solvent_model": "ALPB",
                "solvent": "water",
            },
            "single_point": {"enabled": False},
        }
        response = client.post("/api/v1/jobs", json=_pes_payload(protocol, name="pes-ok"))
        assert response.status_code == 201, response.text
        manager: JobManager = client.app.state.job_manager
        record = manager.get(str(response.json()["job_id"]))
        assert record is not None
        scan_request = record.spec.input["scan_request"]
        optimizer = scan_request["protocol"]["scan_optimizer"]
        assert optimizer.get("basis") is None
        assert "level_warnings" not in scan_request


class TestRunnerDefense:
    def test_migration_lane_canonicalizes_historical_config(
        self, fake_backend: Any, tmp_path: Path
    ) -> None:
        fake_backend.set_result("relaxed_scan", _fake_scan_result(5, tmp_path / "scan"))
        request = {
            "mode": "bond_length_scan",
            "source": {
                "source_type": "xyz_text",
                "xyz_text": _XYZ,
                "charge": 0,
                "multiplicity": 1,
            },
            "coordinate": dict(_COORDINATE),
            # Pre-T13 historical payload: explicit GFN basis + CPCM.
            "protocol": {
                "scan_optimizer": {
                    "method": "GFN2-xTB",
                    "basis": "def2-SVP",
                    "solvent_model": "cpcm",
                    "solvent": "water",
                },
                "single_point": {"enabled": False},
            },
        }
        result = run_pes_scan(request=request, output_dir=tmp_path)
        warnings = result["level_warnings"]
        assert len(warnings) == 2
        assert all("GFN2-xTB" in message and "historical" in message for message in warnings)
        optimizer = result["protocol"]["scan_optimizer"]
        assert optimizer["basis"] is None
        assert optimizer["solvent_model"] == "alpb"
        assert optimizer["solvent"] == "water"
        assert result["optimization_level"]["basis"] is None

    def test_non_migratable_config_fails_fast_before_any_io(self, tmp_path: Path) -> None:
        request = {
            "mode": "bond_length_scan",
            "source": {
                "source_type": "xyz_text",
                "xyz_text": _XYZ,
                "charge": 0,
                "multiplicity": 1,
            },
            "coordinate": dict(_COORDINATE),
            "protocol": {
                "scan_optimizer": {"method": "B97-3c", "basis": "def2-SVP"},
                "single_point": {"enabled": False},
            },
        }
        with pytest.raises(ValueError, match="invalid scan optimizer level.*built-in basis"):
            run_pes_scan(request=request, output_dir=tmp_path)
        assert not (tmp_path / "WORK").exists(), "must fail before any stage IO"

    def test_gfn0_orca_policy_rejects_on_runner_path(self, tmp_path: Path) -> None:
        request = {
            "mode": "bond_length_scan",
            "source": {
                "source_type": "xyz_text",
                "xyz_text": _XYZ,
                "charge": 0,
                "multiplicity": 1,
            },
            "coordinate": dict(_COORDINATE),
            "protocol": {
                "scan_optimizer": {"method": "GFN0-xTB"},
                "single_point": {"enabled": False},
            },
        }
        with pytest.raises(ValueError, match="policy"):
            run_pes_scan(request=request, output_dir=tmp_path)


# ---------------------------------------------------------------------------
# Warning channel 1: edit-recalculate PREVIEW response
# ---------------------------------------------------------------------------


_HISTORICAL_PROTOCOL = {
    "scan_optimizer": {
        "method": "GFN2-xTB",
        "basis": "def2-SVP",
        "solvent_model": "smd",
        "solvent": "water",
    },
    "single_point": {"enabled": False},
}


def test_edit_preview_carries_migration_warnings(client: TestClient) -> None:
    from acp.scheduler.job_edit import compute_source_revision

    record = _seed_pes_job(client, "t14prev", _HISTORICAL_PROTOCOL)
    body = {
        "mode": "in_place",
        "input": record.spec.input,
        "method": {"mode": "bond_length_scan"},
        "resources": {"nproc": 8},
        "expected_source_revision": compute_source_revision(record),
    }
    response = client.post("/api/v1/jobs/t14prev/edit-recalculate/preview", json=body)
    assert response.status_code == 200
    preview = response.json()
    assert preview["ok"] is True
    level_warnings = [
        warning
        for warning in preview["warnings"]
        if "GFN method 'GFN2-xTB'" in warning and "historical" in warning
    ]
    assert len(level_warnings) == 2, preview["warnings"]
    assert any("basis" in warning for warning in level_warnings)
    assert any("solvent_model" in warning for warning in level_warnings)


def test_edit_preview_user_changed_field_is_strict_422(client: TestClient) -> None:
    from acp.scheduler.job_edit import compute_source_revision

    record = _seed_pes_job(client, "t14strict", _HISTORICAL_PROTOCOL)
    edited = json.loads(json.dumps(record.spec.input))
    edited["protocol"]["scan_optimizer"]["basis"] = "ma-def2-SVP"
    body = {
        "mode": "in_place",
        "input": edited,
        "method": {"mode": "bond_length_scan"},
        "resources": {"nproc": 8},
        "expected_source_revision": compute_source_revision(record),
    }
    response = client.post("/api/v1/jobs/t14strict/edit-recalculate/preview", json=body)
    assert response.status_code == 422
    assert "basis" in str(response.json()["detail"])


# ---------------------------------------------------------------------------
# Warning channel 3: job record/events persist a warning entry
# ---------------------------------------------------------------------------


def test_edit_submit_persists_warning_event(client: TestClient) -> None:
    from acp.scheduler.job_edit import compute_source_revision

    record = _seed_pes_job(client, "t14evt", _HISTORICAL_PROTOCOL)
    body = {
        "mode": "in_place",
        "input": record.spec.input,
        "method": {"mode": "bond_length_scan"},
        "resources": {"nproc": 8},
        "expected_source_revision": compute_source_revision(record),
        "request_id": "req-t14evt",
    }
    response = client.post("/api/v1/jobs/t14evt/edit-recalculate", json=body)
    assert response.status_code == 200, response.text
    events_path = runtime_file(Path(record.work_dir), "events.jsonl")
    events = JobEventLog(events_path).read_all()
    warning_events = [event for event in events if event.get("type") == "method_validation_warning"]
    assert warning_events, events
    event = warning_events[-1]
    assert event["source"] == "edit_recalculate"
    assert len(event["warnings"]) == 2
    assert all("GFN method 'GFN2-xTB'" in message for message in event["warnings"])


# ---------------------------------------------------------------------------
# Warning channel 2 + runner events: CLI path
# ---------------------------------------------------------------------------


class TestCliChannel:
    def test_direct_cli_submission_rejects_invalid_gfn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import acp.workflows.pes_search as pes_search_module
        from acp.cli import _handle_pessearch

        def _must_not_run(**kwargs: Any) -> Any:
            raise AssertionError("invalid config must never reach the workflow runner")

        monkeypatch.setattr(pes_search_module, "run_bond_length_scan", _must_not_run)
        args = _pes_args(tmp_path, scan_method="GFN2-xTB", scan_basis="def2-SVP")
        rc = _handle_pessearch(args)
        assert rc == 2
        err = capsys.readouterr().err
        assert "invalid scan optimizer level" in err
        assert "basis" in err

    def test_scheduler_scan_config_prints_warnings_to_stderr_and_events(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import acp.workflows.pes_search as pes_search_module
        from acp.cli import _handle_pessearch
        from acp.core.workflow import WorkflowResult

        task_dir = tmp_path / "task"
        task_dir.mkdir()
        (task_dir / "job.json").write_text("{}", encoding="utf-8")
        (task_dir / "task.json").write_text("{}", encoding="utf-8")
        scan_config = {
            "source": {"source_type": "xyz_text", "xyz_text": _XYZ},
            "coordinate": dict(_COORDINATE),
            "protocol": {
                "scan_optimizer": {"method": "GFN2-xTB", "basis": "def2-SVP"},
                "single_point": {"enabled": False},
            },
        }
        config_path = tmp_path / "scan_config.json"
        config_path.write_text(json.dumps(scan_config), encoding="utf-8")

        warnings = [
            "GFN method 'GFN2-xTB': historical basis 'def2-SVP' cleared",
            "GFN method 'GFN2-xTB': historical solvent_model 'smd' migrated to 'alpb'",
        ]

        def _fake_run(**kwargs: Any) -> WorkflowResult:
            return WorkflowResult(
                status="completed",
                metadata={"level_warnings": warnings, "ts_candidates": 0, "int_candidates": 0},
            )

        monkeypatch.setattr(pes_search_module, "run_bond_length_scan", _fake_run)
        args = _pes_args(tmp_path, scan_config=str(config_path), xyz_text=None)
        rc = _handle_pessearch(args)
        assert rc == 0
        err = capsys.readouterr().err
        for message in warnings:
            assert message in err, f"missing CLI stderr warning: {message}"
        assert err.count("[method-config] WARNING:") == 2

        events_path = runtime_file(task_dir, "events.jsonl")
        events = JobEventLog(events_path).read_all()
        warning_events = [
            event for event in events if event.get("type") == "method_validation_warning"
        ]
        assert warning_events
        assert warning_events[-1]["source"] == "pes_bond_scan_runner"
        assert warning_events[-1]["warnings"] == warnings

    def test_plain_cli_output_dir_gets_no_event_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import acp.workflows.pes_search as pes_search_module
        from acp.cli import _handle_pessearch
        from acp.core.workflow import WorkflowResult

        def _fake_run(**kwargs: Any) -> WorkflowResult:
            return WorkflowResult(
                status="completed",
                metadata={
                    "level_warnings": ["GFN method 'GFN2-xTB': historical basis 'x' cleared"],
                },
            )

        monkeypatch.setattr(pes_search_module, "run_bond_length_scan", _fake_run)
        scan_config = {
            "source": {"source_type": "xyz_text", "xyz_text": _XYZ},
            "coordinate": dict(_COORDINATE),
            "protocol": {
                "scan_optimizer": {"method": "GFN2-xTB", "basis": "def2-SVP"},
                "single_point": {"enabled": False},
            },
        }
        config_path = tmp_path / "scan_config.json"
        config_path.write_text(json.dumps(scan_config), encoding="utf-8")
        args = _pes_args(tmp_path, scan_config=str(config_path), xyz_text=None)
        rc = _handle_pessearch(args)
        assert rc == 0
        assert "historical basis" in capsys.readouterr().err
        task_dir = Path(args.output)
        assert not (task_dir / "events.jsonl").exists()
        assert not (task_dir / "job.json").exists()
