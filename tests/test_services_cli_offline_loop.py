"""Offline CLI / Services closed-loop gate (B07-1 / todo 35).

This gate proves the *first half* of IS-7 — the CLI equivalent migration — with
an offline, scripted local loop that never needs a browser, a running Web
service or an online ledger:

1. **ingest -> plan -> execute -> export** — the real ``acp.intake`` parsers,
   the real ``acp.calculations.plans`` builder, the real
   ``CalculationPlanExecutor`` and the real v2 ``result_manifest`` reader.  The
   scientific *capability* backend is faked (via the suite's ``fake_backend``
   fixture) because the QC binaries are unavailable here; every leg around it
   is production code.  Real-science execution is therefore reported
   ``NOT_VERIFIED``.
2. **control (pause / unpause / cancel / continue / rerun)** — a real
   ``JobManager`` on a tmp run_root, driven through ``acp.cli``'s shared
   ``job_control_command`` entry, which enters the *same* Runtime use case the
   Web API uses (``acp_services.runtime.commands.RuntimeCommands``).
3. **recover (resume_source)** — the real attempt archive + resume receipt
   written by the manager and resolved by ``acp.calculations.step_result``;
   the executor then adopts the archived checkpoint instead of recomputing.
4. **no network / no Web** — the loop runs with sockets and paramiko refused,
   and an injected Web call is proven to fail.

The **full** public CLI (persistent controller/worker connection, public local
task-management subcommands) is todo 74's deliverable; this file must never
claim more than the equivalence + offline-loop evidence it actually exercises.
"""

from __future__ import annotations

import ast
import inspect
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from tests.conftest import FakeBackend

#: Retired workflow ids the CLI must keep intercepting with exit code 2.
_RETIRED_WORKFLOWS = (
    "ensemble",
    "energy",
    "xtbmd_censo_energy",
    "mechanism",
    "optfreq",
    "optfreqsp",
    "Lowconfirm",
    "Highconfirm",
)

#: The installed console script (may be off ``PATH``); exercised when present.
_CONSOLE_SCRIPT = Path.home() / ".local" / "bin" / "acp"

_XYZ_TEXT = "3\nwater\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\nH 0.0 0.93 -0.24\n"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every socket/SSH path for the duration of a test.

    The offline loop must not depend on any of these; blocking them turns an
    accidental Web/SSH dependency into a hard :class:`OSError`.
    """

    def _refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("network access is blocked in the offline gate")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    # ``sys.modules[name] = None`` makes a later ``import paramiko`` raise
    # ImportError; a local loop has no reason to touch SSH at all.
    monkeypatch.setitem(sys.modules, "paramiko", None)


def _seed_job(
    manager: JobManager,
    work_dir: Path,
    job_id: str,
    *,
    workflow: str = "singlepoint",
    status: JobStatus = JobStatus.FAILED,
) -> JobRecord:
    """Persist a job row in *manager*'s store (no execution started)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id=job_id,
        spec=JobSpec(workflow=workflow, name=job_id),
        status=status,
        work_dir=str(work_dir),
        error="interrupted",
        exit_code=1,
    )
    manager.store.create(record)
    return record


def _ingest(task_root: Path) -> Any:
    """Leg 1 — ingest a local structure through the real intake parsers."""
    from acp.intake import detect_and_parse

    task_root.mkdir(parents=True, exist_ok=True)
    (task_root / "input.xyz").write_text(_XYZ_TEXT, encoding="utf-8")
    fmt, parsed = detect_and_parse(_XYZ_TEXT, filename="input.xyz")
    assert fmt == "xyz"
    assert parsed.ok
    return parsed.structures[0]


def _build_plan(task_root: Path) -> Any:
    """Leg 2 — build a one-step single-point plan from the ingested input."""
    from acp.calculations.contracts import (
        CalculationRequest,
        StepKind,
        StructureArtifact,
        StructureRole,
    )
    from acp.calculations.plans import build_simple_plan

    artifact = StructureArtifact(
        path=task_root / "input.xyz",
        elements=["O", "H", "H"],
        role=StructureRole.MINIMUM,
        source="intake",
    )
    request = CalculationRequest(
        input_artifact=artifact,
        method="r2SCAN-3c",
        resources={"output_dir": str(task_root / "WORK" / "05_SP")},
        workflow="singlepoint",
        profile="default",
    )
    return build_simple_plan(StepKind.SINGLEPOINT, request)


def _run_python_module(*cli_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "acp", *cli_args],
        capture_output=True,
        text=True,
        timeout=180,
    )


# --------------------------------------------------------------------------- #
# 1+2+3+6. Offline closed loop: ingest -> plan -> execute -> export
# --------------------------------------------------------------------------- #


def test_offline_ingest_plan_execute_export(tmp_path: Path, fake_backend: FakeBackend) -> None:
    """The local loop produces a completed checkpoint and a readable manifest.

    The capability backend is faked (QC binaries unavailable): real-science
    execution is ``NOT_VERIFIED``; ingest/plan/execute/export are real code.
    """
    from acp.backends.base import QCResult
    from acp.calculations.executor import CalculationPlanExecutor
    from acp.calculations.result_publication import load_scientific_result
    from acp.storage.manifest import ResultManifest

    task_root = tmp_path / "tasks" / "water_sp"

    # 1. ingest
    asset = _ingest(task_root)
    assert asset.atom_count == 3
    assert asset.is_valid()

    # 2. plan
    plan = _build_plan(task_root)
    assert plan.workflow == "singlepoint"
    assert len(plan.items) == 1 and len(plan.steps) == 1

    # 3. execute (local) — real executor, faked capability backend
    fake_backend.set_result("single_point", QCResult(success=True, energy=-76.0))
    result = CalculationPlanExecutor().execute(plan, task_root=task_root)
    assert result.is_completed
    assert (task_root / "WORK" / "00_RUNTIME" / "checkpoint.json").is_file()

    # 6. export (result read) — real v2 manifest + scientific result record
    manifest = ResultManifest.read(task_root / "RESULT")
    assert manifest.status == "completed"
    assert manifest.products
    scientific = load_scientific_result(task_root / "WORK" / "05_SP")
    assert scientific is not None


# --------------------------------------------------------------------------- #
# 4. Control: pause / unpause / cancel / continue / rerun through the CLI entry
# --------------------------------------------------------------------------- #


def test_cli_control_roundtrip_via_real_manager(
    tmp_path: Path, fake_backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every control verb routes through the CLI's shared Runtime use case."""
    from acp import cli

    manager = JobManager(run_root=tmp_path / "runs", poll_interval=30)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    try:
        # -- pause / unpause a genuinely RUNNING job with a live process -----
        _seed_job(
            manager,
            manager.run_root / "pause_task",
            "job-pause",
            status=JobStatus.RUNNING,
            workflow="irc",
        )
        manager.runner._processes["job-pause"] = process

        paused = cli.job_control_command("pause", "job-pause", manager=manager)
        assert paused.status == JobStatus.PAUSED
        assert process.poll() is None

        resumed = cli.job_control_command("unpause", "job-pause", manager=manager)
        assert resumed.status == JobStatus.RUNNING
        assert process.poll() is None

        # -- cancel a QUEUED job ---------------------------------------------
        _seed_job(
            manager,
            manager.run_root / "queued_task",
            "job-cancel",
            status=JobStatus.QUEUED,
            workflow="fake",
        )
        cancelled = cli.job_control_command("cancel", "job-cancel", manager=manager)
        assert cancelled.status == JobStatus.CANCELLED

        # submission threads are intercepted so no real execution is launched
        submissions: list[str] = []
        monkeypatch.setattr(
            manager,
            "_start_submission_thread",
            lambda job_id, thread_name: submissions.append(job_id) or True,
        )

        # -- continue a FAILED job (requeue from a real checkpoint) ----------
        from acp.backends.base import QCResult
        from acp.calculations.executor import CalculationPlanExecutor

        cont_root = tmp_path / "tasks" / "continue_sp"
        _ingest(cont_root)
        fake_backend.set_result("single_point", QCResult(success=True, energy=-76.0))
        assert CalculationPlanExecutor().execute(_build_plan(cont_root), task_root=cont_root)
        _seed_job(
            manager,
            cont_root,
            "job-continue",
            status=JobStatus.FAILED,
            workflow="singlepoint",
        )
        continued = cli.job_control_command("continue", "job-continue", manager=manager)
        assert continued.status == JobStatus.QUEUED
        assert submissions == ["job-continue"]

        # -- rerun a terminal job in place (attempt + 1) ---------------------
        _seed_job(
            manager,
            manager.run_root / "done_task",
            "job-rerun",
            status=JobStatus.COMPLETED,
            workflow="singlepoint",
        )
        rerun = cli.job_control_command("rerun", "job-rerun", manager=manager)
        assert rerun is not None
        assert rerun.status == JobStatus.QUEUED
        assert submissions == ["job-continue", "job-rerun"]

        # -- unknown verb is rejected, never silently ignored ----------------
        with pytest.raises(ValueError, match="unknown job control command"):
            cli.job_control_command("explode", "job-rerun", manager=manager)
    finally:
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), 9)
            process.wait(timeout=10)
        manager.shutdown()


def test_cli_control_entry_shares_the_web_runtime_use_case() -> None:
    """The CLI and Web control entries reach the manager through one boundary.

    Both ``acp.cli.job_runtime_commands`` and the API build the *same*
    ``RuntimeCommands`` bundle; the CLI helper therefore forwards each verb
    exactly once to the injected port (single execution owner, todo 25).
    """

    class _SpyControl:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, Any]]] = []

        def pause_job(self, job_id: str) -> str:
            self.calls.append(("pause_job", job_id, {}))
            return "paused"

        def unpause_job(self, job_id: str) -> str:
            self.calls.append(("unpause_job", job_id, {}))
            return "running"

        def cancel(self, job_id: str) -> str:
            self.calls.append(("cancel", job_id, {}))
            return "cancelled"

        def continue_job(self, job_id: str, *, target_node: str | None = None) -> str:
            self.calls.append(("continue_job", job_id, {"target_node": target_node}))
            return "queued"

        def rerun_job(self, job_id: str, project_id: str | None = None) -> str:
            self.calls.append(("rerun_job", job_id, {"project_id": project_id}))
            return "queued"

    from acp import cli

    spy = _SpyControl()
    manager = type("_Mgr", (), {})()
    manager.pause_job = spy.pause_job
    manager.unpause_job = spy.unpause_job
    manager.cancel = spy.cancel
    manager.continue_job = spy.continue_job
    manager.rerun_job = spy.rerun_job
    manager.submit = lambda spec, group_id=None, **_kwargs: None
    manager.edit_recalculate = lambda job_id, **kwargs: None

    assert cli.job_control_command("pause", "j1", manager=manager) == "paused"
    assert cli.job_control_command("unpause", "j1", manager=manager) == "running"
    assert cli.job_control_command("cancel", "j1", manager=manager) == "cancelled"
    assert cli.job_control_command("continue", "j1", manager=manager) == "queued"
    assert cli.job_control_command("rerun", "j1", manager=manager) == "queued"

    assert spy.calls == [
        ("pause_job", "j1", {}),
        ("unpause_job", "j1", {}),
        ("cancel", "j1", {}),
        ("continue_job", "j1", {"target_node": None}),
        ("rerun_job", "j1", {"project_id": None}),
    ]


# --------------------------------------------------------------------------- #
# 5. Recovery: resume_source adoption (no recompute)
# --------------------------------------------------------------------------- #


def test_offline_recovery_via_resume_source(tmp_path: Path, fake_backend: FakeBackend) -> None:
    """A closed attempt is archived; the next attempt adopts its checkpoint."""
    from acp.backends.base import QCResult
    from acp.calculations.executor import CalculationPlanExecutor
    from acp.calculations.step_result import resolve_resume_source

    task_root = tmp_path / "tasks" / "recover_sp"
    _ingest(task_root)
    plan = _build_plan(task_root)

    fake_backend.set_result("single_point", QCResult(success=True, energy=-76.0))
    first = CalculationPlanExecutor().execute(plan, task_root=task_root)
    assert first.is_completed
    calls_before = len(fake_backend.calls)

    # The manager's real attempt-close path archives the receipts and writes
    # the resume receipt (this is exactly what ``continue`` does).
    manager = JobManager(run_root=tmp_path / "runs", poll_interval=30)
    try:
        record = _seed_job(
            manager, task_root, "job-recover", status=JobStatus.FAILED, workflow="singlepoint"
        )
        manager._archive_attempt_receipts(record, previous_attempt=1)
        manager._write_resume_source(record, continued_from="failed", previous_attempt=1)
    finally:
        manager.shutdown()

    source = resolve_resume_source(task_root, kind="executor")
    assert source is not None
    assert source.compatible is True
    assert source.science_root == task_root / "WORK" / "00_RUNTIME" / "attempts" / "1"

    second = CalculationPlanExecutor().execute(plan, task_root=task_root)
    assert second.is_completed
    # Adopted, not recomputed: the capability backend was never called again.
    assert len(fake_backend.calls) == calls_before
    assert all(state.executed_this_run is False for state in second.step_states)


# --------------------------------------------------------------------------- #
# 7. No network / no Web (control: an injected Web call must fail)
# --------------------------------------------------------------------------- #


def test_offline_loop_needs_no_network(
    tmp_path: Path, fake_backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole local loop completes with every socket path refused."""
    from acp.backends.base import QCResult
    from acp.calculations.executor import CalculationPlanExecutor
    from acp.storage.manifest import ResultManifest

    _block_network(monkeypatch)

    task_root = tmp_path / "tasks" / "offline_sp"
    _ingest(task_root)
    plan = _build_plan(task_root)
    fake_backend.set_result("single_point", QCResult(success=True, energy=-76.0))
    result = CalculationPlanExecutor().execute(plan, task_root=task_root)

    assert result.is_completed
    assert ResultManifest.read(task_root / "RESULT").status == "completed"


def test_injected_web_call_fails_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: with the offline guard a Web/HTTP call is refused, not hidden."""
    _block_network(monkeypatch)
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", 8765), timeout=0.5)


def test_cli_module_has_no_top_level_web_import() -> None:
    """The CLI's module-level imports pull in no FastAPI/uvicorn (Web) stack."""
    from acp import cli

    tree = ast.parse(inspect.getsource(cli))
    banned = {"fastapi", "uvicorn"}
    offenders: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name.split(".")[0] in banned]
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
            if module in banned:
                offenders.append(node.module or "")
    assert offenders == [], f"CLI has a top-level Web dependency: {offenders}"


# --------------------------------------------------------------------------- #
# 8. Old-arg compatibility via the REAL installed entry (no mocks)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("cli_args", "needle"),
    [
        (("--help",), "Auto-Calc Platform"),
        (("run", "singlepoint", "--help"), "--method"),
        (("run", "scan", "--help"), "--coordinate"),
        (("run", "nmr", "--help"), "--bruker"),
        (("run", "irc", "--help"), "--ts-provenance"),
        (("run", "XtbPathSearch", "--help"), "--path-config"),
        (("run", "serve", "--help"), "--port"),
        (("doctor", "--help"), "--node"),
        (("init", "--help"), "--config"),
    ],
)
def test_old_cli_surface_preserved_via_real_entry(cli_args: tuple[str, ...], needle: str) -> None:
    """Old subcommands/options still parse and appear on the real entry point."""
    result = _run_python_module(*cli_args)
    assert result.returncode == 0, result.stderr
    assert needle in result.stdout


@pytest.mark.parametrize("workflow", _RETIRED_WORKFLOWS)
def test_retired_interception_preserved_via_real_entry(workflow: str) -> None:
    """Retired workflows are still rejected with exit code 2 (Chinese message)."""
    result = _run_python_module("run", workflow)
    assert result.returncode == 2
    assert "已退役" in (result.stdout + result.stderr)


def test_console_script_entry_smoke() -> None:
    """When installed, ``~/.local/bin/acp`` behaves like ``python -m acp``."""
    if not _CONSOLE_SCRIPT.is_file():
        pytest.skip(f"acp console script not installed at {_CONSOLE_SCRIPT} — NOT_VERIFIED")
    result = subprocess.run(
        [str(_CONSOLE_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0
    assert "Auto-Calc Platform" in result.stdout
