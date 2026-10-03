# ruff: noqa: E501
"""X1′-D: CLI XtbPathSearch ``--register`` job-visibility tests.

A CLI run launched via ``acp run XtbPathSearch --path-config ... --output
...`` writes ``RESULT/`` products but historically left no row in the
scheduler job store, so the Workbench job list and
``GET /api/v1/jobs/{id}/s2/profile`` could not see it.  ``--register``
(opt-in) binds the finished output directory as a COMPLETED job through
the store-layer registration API.

Covers: opt-in default (no store side effects), successful registration
record shape + task-index sync, Workbench/job-list//s2/profile resolution
over the registered id, failed-run non-registration, missing-product
abort, and the scheduler-task-dir skip guard.
"""

# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportPrivateUsage=false, reportUnusedCallResult=false
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from acp.core.workflow import WorkflowResult
from acp.scheduler.jobs import JobStatus
from acp.scheduler.store import JobStore
from acp.scheduler.tasks import TaskIndex

_WORKFLOW_ID = "XtbPathSearch"
# JobManager.submit shape: ``{ts}_{seq:03d}_{safe_name}`` where
# ``ts = %Y%m%d_%H%M%S`` (itself underscore-separated).
_JOB_ID_RE = re.compile(r"^\d{8}_\d{6}_\d{3}_[A-Za-z0-9_-]+$")


def _frozen_request() -> dict[str, Any]:
    return {
        "schema_version": "pes2ts_xtb_path_request_v1",
        "reaction_id": "rxn_cli_register",
        "source": {
            "source_type": "xyz_text_pair",
            "start_xyz": "2\nstart\nC 0.0 0.0 0.0\nH 1.0 0.0 0.0\n",
            "end_xyz": "2\nend\nC 0.0 0.0 0.0\nH 1.6 0.0 0.0\n",
            "charge": 0,
            "multiplicity": 1,
        },
        "recipe": {
            "path_inp_text": "$path\n 1 10\nend\n",
            "gfn_level": 2,
            "uhf": 0,
            "threads": 2,
            "timeout_seconds": 60,
            "seed": 7,
            "extra_args": [],
        },
    }


def _profile_payload() -> dict[str, Any]:
    """Minimal pes_profile_v2 (xtb_peb-shaped) accepted by /s2/profile."""
    return {
        "schema_version": "pes_profile_v2",
        "workflow": _WORKFLOW_ID,
        "mode": "xtb_path",
        "status": "completed",
        "source": "xtb_peb",
        "stationary_point_claimed": False,
        "coordinate": {"kind": "distance", "atoms": [0, 1], "unit": "angstrom"},
        "coordinates": [{"kind": "distance", "atoms": [0, 1], "unit": "angstrom"}],
        "selection": {},
        "protocol": {"gfn_level": 2},
        "scan_dir": "RESULT/pes_search",
        "frames_count": 2,
        "frames": [
            {
                "index": 0,
                "target_coordinate": 1.0,
                "actual_coordinate": 1.0,
                "geometry_path": "path_frames/path_frame_000.xyz",
                "scan_energy_hartree": -100.1,
            },
            {
                "index": 1,
                "target_coordinate": 1.6,
                "actual_coordinate": 1.6,
                "geometry_path": "path_frames/path_frame_001.xyz",
                "scan_energy_hartree": -100.05,
            },
        ],
        "profile": {"raw_hartree": [-100.1, -100.05]},
        "ts_candidates": [],
        "int_candidates": [],
    }


def _write_fake_outputs(output_dir: Path) -> None:
    """Materialise the workflow's on-disk contract under *output_dir*."""
    pes_dir = output_dir / "RESULT" / "pes_search"
    pes_dir.mkdir(parents=True, exist_ok=True)
    (pes_dir / "pes_profile.json").write_text(
        json.dumps(_profile_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    frames_dir = pes_dir / "path_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for index in range(2):
        (frames_dir / f"path_frame_{index:03d}.xyz").write_text(
            "3\nframe\nC 0.0 0.0 0.0\nH 1.0 0.0 0.0\nH 0.0 1.0 0.0\n",
            encoding="utf-8",
        )
    (output_dir / "RESULT" / "result_manifest.json").write_text(
        json.dumps(
            {
                "version": 2,
                "workflow": _WORKFLOW_ID,
                "status": "completed",
                "products": [
                    {
                        "id": "pes_profile",
                        "label": "xTB PATH PES profile",
                        "path": "pes_search/pes_profile.json",
                        "kind": "pes_profile",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _completed_result(output_dir: Path) -> WorkflowResult:
    return WorkflowResult(
        status="completed",
        stages_completed=["prepare", "run_path_search", "finalize"],
        metadata={
            "workflow": _WORKFLOW_ID,
            "frames_count": 2,
            "output_dir": str(output_dir),
            "pes_profile_path": str(output_dir / "RESULT" / "pes_search" / "pes_profile.json"),
            "result_manifest_path": str(output_dir / "RESULT" / "result_manifest.json"),
        },
    )


def _fake_run(
    *,
    write_outputs: bool = True,
) -> Callable[..., WorkflowResult]:
    """Build a ``run_xtb_path_search`` stand-in (fake xTB, no binaries)."""

    def _run(
        path_request: dict[str, Any],
        *,
        output_dir: Path | str,
        config: Any = None,
        progress_reporter: Any = None,
    ) -> WorkflowResult:
        out = Path(output_dir)
        if write_outputs:
            _write_fake_outputs(out)
        return _completed_result(out)

    return _run


def _request_file(tmp_path: Path) -> Path:
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(_frozen_request()), encoding="utf-8")
    return request_file


def _cli_argv(request_file: Path, output_dir: Path, *extra: str) -> list[str]:
    return [
        "run",
        _WORKFLOW_ID,
        "--path-config",
        str(request_file),
        "--output",
        str(output_dir),
        *extra,
    ]


def _run_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    run_root = tmp_path / "run_root"
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    return run_root


def _store(run_root: Path) -> JobStore:
    return JobStore(run_root / "acp_jobs.db")


# ---------------------------------------------------------------------------
# Opt-in default
# ---------------------------------------------------------------------------


def test_register_flag_defaults_to_false() -> None:
    from acp.cli import build_parser

    args = build_parser().parse_args(
        ["run", _WORKFLOW_ID, "--path-config", "r.json", "--output", "./out"]
    )
    assert args.register is False
    args_registered = build_parser().parse_args(
        ["run", _WORKFLOW_ID, "--path-config", "r.json", "--output", "./out", "--register"]
    )
    assert args_registered.register is True


def test_cli_without_register_leaves_store_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default CLI behaviour is unchanged: no job row, no store file."""
    from acp.cli import main

    run_root = _run_root(tmp_path, monkeypatch)
    request_file = _request_file(tmp_path)
    output_dir = tmp_path / "cli_out"
    with patch(
        "acp.workflows.xtb_path.run_xtb_path_search",
        side_effect=_fake_run(),
    ):
        rc = main(_cli_argv(request_file, output_dir))
    assert rc == 0
    assert not (run_root / "acp_jobs.db").exists()
    # Products still land on disk exactly as before --register existed.
    assert (output_dir / "RESULT" / "pes_search" / "pes_profile.json").is_file()


# ---------------------------------------------------------------------------
# Successful registration
# ---------------------------------------------------------------------------


def test_cli_register_creates_completed_job_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.cli import main

    run_root = _run_root(tmp_path, monkeypatch)
    request_file = _request_file(tmp_path)
    output_dir = tmp_path / "cli_out"
    with patch(
        "acp.workflows.xtb_path.run_xtb_path_search",
        side_effect=_fake_run(),
    ):
        rc = main(_cli_argv(request_file, output_dir, "--register"))
    assert rc == 0

    store = _store(run_root)
    jobs = store.list()
    assert len(jobs) == 1
    record = jobs[0]
    assert record.spec.workflow == _WORKFLOW_ID
    assert record.status == JobStatus.COMPLETED
    assert record.status.is_terminal
    assert Path(record.work_dir) == output_dir.resolve()
    assert record.exit_code == 0
    assert record.progress == 1.0
    assert record.completed_at is not None
    assert _JOB_ID_RE.match(record.id), record.id
    # Frozen request preserved for edit/provenance surfaces.
    assert record.spec.input["path_request"]["schema_version"] == "pes2ts_xtb_path_request_v1"
    assert record.spec.input_hash and record.spec.input_hash.startswith("sha256:")
    assert record.project_id  # default project assigned

    # Task-index row synced (Workbench grouping / name projections).
    task_row = TaskIndex(store.db_path).get(record.id)
    assert task_row is not None
    assert task_row["workflow"] == _WORKFLOW_ID
    assert task_row["status"] == JobStatus.COMPLETED.value
    assert task_row["node_path"] == str(output_dir.resolve())

    # job.json marker written into the CLI output dir (manager convention).
    job_json = output_dir / "job.json"
    assert job_json.is_file()
    payload = json.loads(job_json.read_text(encoding="utf-8"))
    assert payload["id"] == record.id
    assert payload["status"] == "completed"

    # ANTI-PATTERN #15: job id never appears in the disk path.
    assert record.id not in str(output_dir)


def test_cli_register_is_idempotent_on_repeated_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second --register run mints a distinct id (no PK collision)."""
    from acp.cli import main

    run_root = _run_root(tmp_path, monkeypatch)
    request_file = _request_file(tmp_path)
    output_dir = tmp_path / "cli_out"
    with patch(
        "acp.workflows.xtb_path.run_xtb_path_search",
        side_effect=_fake_run(),
    ):
        assert main(_cli_argv(request_file, output_dir, "--register")) == 0
        assert main(_cli_argv(request_file, output_dir, "--register")) == 0
    jobs = _store(run_root).list()
    assert len(jobs) == 2
    assert jobs[0].id != jobs[1].id


# ---------------------------------------------------------------------------
# Workbench visibility: job list + /s2/profile
# ---------------------------------------------------------------------------


def _boot_client(run_root: Path) -> TestClient:
    from acp.api.server import create_app

    app = create_app(run_root=run_root, max_running=1)
    return TestClient(app)


def test_registered_job_visible_in_list_and_s2_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.cli import main

    run_root = _run_root(tmp_path, monkeypatch)
    request_file = _request_file(tmp_path)
    output_dir = tmp_path / "cli_out"
    with patch(
        "acp.workflows.xtb_path.run_xtb_path_search",
        side_effect=_fake_run(),
    ):
        assert main(_cli_argv(request_file, output_dir, "--register")) == 0
    record = _store(run_root).list()[0]

    with _boot_client(run_root) as client:
        listing = client.get("/api/v1/jobs").json()
        jobs = listing["jobs"]
        assert any(job["id"] == record.id for job in jobs), listing
        matched = next(job for job in jobs if job["id"] == record.id)
        assert matched["spec"]["workflow"] == _WORKFLOW_ID
        assert matched["status"] == "completed"

        detail = client.get(f"/api/v1/jobs/{record.id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "completed"

        response = client.get(f"/api/v1/jobs/{record.id}/s2/profile")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["job_id"] == record.id
        assert body["mode"] == "xtb_path"
        assert body["status"] == "completed"
        assert len(body["frames"]) == 2
        assert body["frames"][0]["scan_energy_hartree"] == pytest.approx(-100.1)
        assert body["coordinate"]["atoms"] == [0, 1]


def test_without_register_s2_profile_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.cli import main

    run_root = _run_root(tmp_path, monkeypatch)
    request_file = _request_file(tmp_path)
    output_dir = tmp_path / "cli_out"
    with patch(
        "acp.workflows.xtb_path.run_xtb_path_search",
        side_effect=_fake_run(),
    ):
        assert main(_cli_argv(request_file, output_dir)) == 0
    with _boot_client(run_root) as client:
        assert client.get("/api/v1/jobs").json()["jobs"] == []
        assert client.get("/api/v1/jobs/20260101_000_XtbPathSearch/s2/profile").status_code == 404


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_cli_register_skipped_on_failed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed workflow run must not be registered, even with --register."""
    from acp.cli import main
    from acp.workflows.xtb_path import XtbPathSearchError

    run_root = _run_root(tmp_path, monkeypatch)
    request_file = _request_file(tmp_path)
    output_dir = tmp_path / "cli_out"
    with patch(
        "acp.workflows.xtb_path.run_xtb_path_search",
        side_effect=XtbPathSearchError(code="XTB_PATH_E_XTB", message="xtb died"),
    ):
        rc = main(_cli_argv(request_file, output_dir, "--register"))
    assert rc != 0
    assert not (run_root / "acp_jobs.db").exists()


def test_cli_register_aborts_when_products_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--register without on-disk RESULT products fails fast, no ghost row."""
    from acp.cli import main

    run_root = _run_root(tmp_path, monkeypatch)
    request_file = _request_file(tmp_path)
    output_dir = tmp_path / "cli_out"
    output_dir.mkdir()
    with patch(
        "acp.workflows.xtb_path.run_xtb_path_search",
        side_effect=_fake_run(write_outputs=False),
    ):
        rc = main(_cli_argv(request_file, output_dir, "--register"))
    assert rc == 1
    assert not (run_root / "acp_jobs.db").exists()


def test_cli_register_skips_existing_scheduler_task_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scheduler task dirs are already registered by JobManager.submit."""
    from acp.cli import main

    run_root = _run_root(tmp_path, monkeypatch)
    request_file = _request_file(tmp_path)
    output_dir = tmp_path / "sched_task"
    output_dir.mkdir()
    (output_dir / "job.json").write_text("{}", encoding="utf-8")
    (output_dir / "task.json").write_text("{}", encoding="utf-8")
    with patch(
        "acp.workflows.xtb_path.run_xtb_path_search",
        side_effect=_fake_run(),
    ):
        rc = main(_cli_argv(request_file, output_dir, "--register"))
    assert rc == 0
    assert not (run_root / "acp_jobs.db").exists()


# ---------------------------------------------------------------------------
# Registration helper unit surface
# ---------------------------------------------------------------------------


def test_helper_rejects_missing_work_dir(tmp_path: Path) -> None:
    from acp.scheduler.registration import (
        CliJobRegistrationError,
        register_completed_cli_job,
    )

    with pytest.raises(CliJobRegistrationError, match="work dir"):
        register_completed_cli_job(
            workflow=_WORKFLOW_ID,
            work_dir=tmp_path / "nope",
            run_root=tmp_path / "run_root",
        )


def test_helper_rejects_missing_required_files(tmp_path: Path) -> None:
    from acp.scheduler.registration import (
        CliJobRegistrationError,
        register_completed_cli_job,
    )

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    with pytest.raises(CliJobRegistrationError, match="missing"):
        register_completed_cli_job(
            workflow=_WORKFLOW_ID,
            work_dir=output_dir,
            run_root=tmp_path / "run_root",
            required_files=("RESULT/pes_search/pes_profile.json",),
        )


def test_helper_mints_manager_shaped_job_id(tmp_path: Path) -> None:
    from acp.scheduler.registration import register_completed_cli_job

    output_dir = tmp_path / "out"
    _write_fake_outputs(output_dir)
    record = register_completed_cli_job(
        workflow=_WORKFLOW_ID,
        work_dir=output_dir,
        run_root=tmp_path / "run_root",
        input_payload={"path_request": _frozen_request()},
        required_files=("RESULT/pes_search/pes_profile.json",),
    )
    assert _JOB_ID_RE.match(record.id), record.id
    assert record.status == JobStatus.COMPLETED
    assert Path(record.work_dir) == output_dir.resolve()


__all__ = [
    "test_cli_register_aborts_when_products_missing",
    "test_cli_register_creates_completed_job_record",
    "test_cli_register_is_idempotent_on_repeated_invocation",
    "test_cli_register_skipped_on_failed_run",
    "test_cli_register_skips_existing_scheduler_task_dir",
    "test_cli_without_register_leaves_store_untouched",
    "test_helper_mints_manager_shaped_job_id",
    "test_helper_rejects_missing_required_files",
    "test_helper_rejects_missing_work_dir",
    "test_register_flag_defaults_to_false",
    "test_registered_job_visible_in_list_and_s2_profile",
    "test_without_register_s2_profile_is_404",
]
