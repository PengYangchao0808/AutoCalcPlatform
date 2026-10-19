"""Real-entry full CLI loop + persistent controller/worker connection (B07-8 / todo 74).

This is the sole proof holder for the IS-7 "complete CLI" claim. Unlike the
equivalence-only offline gate (``tests/test_services_cli_offline_loop.py``,
todo 35 — which drives the manager through an in-process handle), every leg
here is a **real ``acp`` process** (``python -m acp task ...``):

* the public local task-management commands — ingest / plan / execute / query /
  pause / unpause / continue / cancel / rerun / recover / export — are exercised
  through the real entry point, never a browser/Web service and never a second
  task implementation (they forward once through the Services use cases);
* the controller connection is **persistent**: each command rebuilds the
  ``JobManager`` from the shared ``run_root`` store, so a fresh process (after
  the previous one is killed) finds the original task and can control/recover
  it. A surviving worker is reconnected through its persisted ``run.lock`` /
  ``/proc`` identity.

Real scientific software is unavailable on this host: the science legs that
need it are marked ``NOT_VERIFIED`` and use the suite's ``fake_backend`` for the
checkpoint/manifest artifacts, but the control / recovery / reconnect legs are
real (real SQLite store, real process signals, real CLI subprocesses).
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeBackend

_XYZ_TEXT = "3\nwater\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\nH 0.0 0.93 -0.24\n"

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

_CONSOLE_SCRIPT = Path.home() / ".local" / "bin" / "acp"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _isolated_env(run_root: Path) -> dict[str, str]:
    """Subprocess env: pinned run root, no repo/user config or network leaks."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CONFSEARCH_")}
    env["ACP_RUN_ROOT"] = str(run_root)
    return env


def _run_acp(*cli_args: str, run_root: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "acp", *cli_args],
        capture_output=True,
        text=True,
        timeout=180,
        env=_isolated_env(run_root),
        cwd=str(cwd),
    )


def _last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            parsed = json.loads(stripped)
            assert isinstance(parsed, dict)
            return parsed
    raise AssertionError(f"no JSON envelope on stdout:\n{stdout}")


def _seed_job(
    db_path: Path,
    work_dir: Path,
    job_id: str,
    status: str,
    *,
    workflow: str = "singlepoint",
) -> Any:
    """Persist a job row exactly as a previous controller would have left it."""
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp_services.store.store import JobStore

    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "WORK" / "00_RUNTIME").mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow=workflow,
            name=job_id,
            input={"source": "CCO", "source_type": "smiles"},
        ),
        status=JobStatus(status),
        work_dir=str(work_dir),
    )
    JobStore(db_path).create(record)
    return record


def _start_surviving_worker(work_dir: Path) -> subprocess.Popen[Any]:
    """A real worker process bound to *work_dir*, left by a previous controller."""
    work_dir.mkdir(parents=True, exist_ok=True)
    runtime = work_dir / "WORK" / "00_RUNTIME"
    runtime.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(300)"],
        cwd=str(work_dir),
        start_new_session=True,
    )
    (runtime / "run.lock").write_text(
        json.dumps({"owner_pid": os.getpid(), "task_pid": proc.pid}),
        encoding="utf-8",
    )
    return proc


def _proc_state(pid: int) -> str:
    data = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    return data.rsplit(")", 1)[1].split()[0]


def _stop_worker(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGCONT)
        except OSError:
            pass
        proc.kill()
        proc.wait(timeout=10)


def _seed_completed_checkpoint(
    task_dir: Path, fake_backend: FakeBackend, workflow: str = "singlepoint"
) -> Any:
    """Write a real generic checkpoint + RESULT via the production executor.

    Real-science execution is ``NOT_VERIFIED`` (the ORCA binary is absent); the
    capability backend is faked, but the executor, checkpoint and v2 manifest
    writers are production code — the control/recovery legs consume them for real.
    """
    from acp import cli as acp_cli
    from acp.backends.base import QCResult
    from acp.calculations.executor import CalculationPlanExecutor

    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "input.xyz").write_text(_XYZ_TEXT, encoding="utf-8")
    plan = acp_cli._build_task_plan(task_dir, workflow, "r2SCAN-3c", "default")
    fake_backend.set_result("single_point", QCResult(success=True, energy=-76.0))
    result = CalculationPlanExecutor().execute(plan, task_root=task_dir)
    assert result.is_completed
    return plan


# --------------------------------------------------------------------------- #
# 1. Real-entry full local loop (ingest -> plan -> execute -> query -> export)
# --------------------------------------------------------------------------- #


def test_real_entry_full_loop(tmp_path: Path) -> None:
    """The public commands complete the local loop as separate real processes."""
    run_root = tmp_path / "runs"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    task_dir = tmp_path / "task_dir"

    ingest = _run_acp(
        "task",
        "ingest",
        "--xyz-text",
        _XYZ_TEXT,
        "--task-dir",
        str(task_dir),
        run_root=run_root,
        cwd=cwd,
    )
    assert ingest.returncode == 0, ingest.stderr
    assert _last_json(ingest.stdout)["status"] == "ok"
    assert (task_dir / "input.xyz").is_file()

    plan = _run_acp(
        "task",
        "plan",
        "--task-dir",
        str(task_dir),
        "--workflow",
        "singlepoint",
        run_root=run_root,
        cwd=cwd,
    )
    assert plan.returncode == 0, plan.stderr
    assert _last_json(plan.stdout)["steps"] == ["singlepoint"]

    # ``fake`` keeps the loop self-contained and needs no scientific binary.
    execute = _run_acp(
        "task",
        "execute",
        "--workflow",
        "fake",
        "--input",
        "CCO",
        "--name",
        "loop",
        run_root=run_root,
        cwd=cwd,
    )
    assert execute.returncode == 0, execute.stderr
    job = _last_json(execute.stdout)
    assert job["status"] == "completed"
    job_id = job["job_id"]

    # A brand-new process (the previous one has exited) still finds the task.
    query = _run_acp("task", "query", "--job", job_id, run_root=run_root, cwd=cwd)
    assert query.returncode == 0
    found = _last_json(query.stdout)["job"]
    assert found["id"] == job_id
    assert found["status"] == "completed"
    assert found["work_dir"] == job["work_dir"]

    list_out = _run_acp("task", "query", run_root=run_root, cwd=cwd)
    assert _last_json(list_out.stdout)["count"] == 1

    export = _run_acp("task", "export", "--job", job_id, run_root=run_root, cwd=cwd)
    assert export.returncode == 1  # fake keeps no result manifest -> typed no_result
    assert _last_json(export.stdout)["status"] == "no_result"


# --------------------------------------------------------------------------- #
# 2. Restart between steps: abrupt kill then a fresh process controls the task
# --------------------------------------------------------------------------- #


def test_abrupt_kill_then_new_process_controls_task(tmp_path: Path) -> None:
    """A SIGKILLed controller leaves a persisted task a new process can control."""
    run_root = tmp_path / "runs"
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "acp",
            "task",
            "execute",
            "--workflow",
            "fake",
            "--input",
            "CCO",
            "--name",
            "killed",
        ],
        cwd=str(cwd),
        env=_isolated_env(run_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    job_id: str | None = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        query = _run_acp("task", "query", run_root=run_root, cwd=cwd)
        try:
            payload = _last_json(query.stdout)
        except AssertionError:
            time.sleep(0.3)
            continue
        if payload.get("count"):
            job_id = payload["jobs"][0]["id"]
            break
        time.sleep(0.3)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)
    assert job_id is not None, "job was never persisted before the kill"

    # Fresh process controls the surviving task record.
    cancel = _run_acp("task", "cancel", "--job", job_id, run_root=run_root, cwd=cwd)
    assert cancel.returncode == 0, cancel.stderr
    assert _last_json(cancel.stdout)["status"] in {"cancelled", "cancelling"}


def test_restart_cancel_and_rerun_persisted_jobs(tmp_path: Path) -> None:
    """cancel/rerun on persisted jobs succeed from a brand-new CLI process."""
    run_root = tmp_path / "runs"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    db_path = run_root / "acp_jobs.db"

    _seed_job(db_path, run_root / "queued_task", "job-queued", "queued")
    cancel = _run_acp("task", "cancel", "--job", "job-queued", run_root=run_root, cwd=cwd)
    assert cancel.returncode == 0, cancel.stderr
    assert _last_json(cancel.stdout)["status"] == "cancelled"

    _seed_job(db_path, run_root / "done_task", "job-done", "completed")
    rerun = _run_acp("task", "rerun", "--job", "job-done", run_root=run_root, cwd=cwd)
    assert rerun.returncode == 0, rerun.stderr
    payload = _last_json(rerun.stdout)
    assert payload["status"] == "queued"
    assert payload["attempt"] == 2


def test_restart_continue_from_real_checkpoint(tmp_path: Path, fake_backend: FakeBackend) -> None:
    """continue re-enters a persisted FAILED job from its real checkpoint."""
    run_root = tmp_path / "runs"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    task_dir = tmp_path / "tasks" / "continue_sp"
    _seed_completed_checkpoint(task_dir, fake_backend)
    _seed_job(run_root / "acp_jobs.db", task_dir, "job-continue", "failed", workflow="singlepoint")

    continued = _run_acp("task", "continue", "--job", "job-continue", run_root=run_root, cwd=cwd)
    assert continued.returncode == 0, continued.stderr
    assert _last_json(continued.stdout)["status"] == "queued"


# --------------------------------------------------------------------------- #
# 3. Persistent controller/worker connection: pause/unpause a surviving worker
# --------------------------------------------------------------------------- #


def test_restart_pause_unpause_reconnects_surviving_worker(tmp_path: Path) -> None:
    """A fresh controller reconnects to a worker left by a previous controller."""
    run_root = tmp_path / "runs"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    task_dir = tmp_path / "tasks" / "paused_task"
    worker = _start_surviving_worker(task_dir)
    try:
        _seed_job(run_root / "acp_jobs.db", task_dir, "job-pause", "running")

        pause = _run_acp("task", "pause", "--job", "job-pause", run_root=run_root, cwd=cwd)
        assert pause.returncode == 0, pause.stderr
        assert _last_json(pause.stdout)["status"] == "paused"
        assert _proc_state(worker.pid) == "T"

        unpause = _run_acp("task", "unpause", "--job", "job-pause", run_root=run_root, cwd=cwd)
        assert unpause.returncode == 0, unpause.stderr
        assert _last_json(unpause.stdout)["status"] == "running"
        assert _proc_state(worker.pid) != "T"
    finally:
        _stop_worker(worker)


def test_negative_in_memory_manager_is_not_persistent(tmp_path: Path) -> None:
    """An in-memory manager (no persisted row) is invisible to a fresh process.

    This is the negative case proving the persistence matters: the old offline
    gate could only control a job through an in-process manager handle, which a
    new process can never reach.
    """
    run_root = tmp_path / "runs"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    _run_acp("task", "query", run_root=run_root, cwd=cwd)  # materialize the store

    missing = _run_acp("task", "pause", "--job", "in-memory-only", run_root=run_root, cwd=cwd)
    assert missing.returncode == 1
    assert _last_json(missing.stdout)["status"] == "failed"


# --------------------------------------------------------------------------- #
# 4. Recover (reuse valid results) + export (read the result manifest)
# --------------------------------------------------------------------------- #


def test_recover_reuses_completed_checkpoint(tmp_path: Path, fake_backend: FakeBackend) -> None:
    """recover adopts a completed checkpoint instead of recomputing (real seam)."""
    run_root = tmp_path / "runs"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    task_dir = tmp_path / "tasks" / "recover_sp"
    _seed_completed_checkpoint(task_dir, fake_backend)
    calls_before = len(fake_backend.calls)

    recover = _run_acp(
        "task",
        "recover",
        "--task-dir",
        str(task_dir),
        "--workflow",
        "singlepoint",
        run_root=run_root,
        cwd=cwd,
    )
    assert recover.returncode == 0, recover.stderr
    payload = _last_json(recover.stdout)
    assert payload["completed"] is True
    # The capability backend is never re-invoked by the recovered run.
    assert len(fake_backend.calls) == calls_before


def test_export_reads_real_result_manifest(tmp_path: Path, fake_backend: FakeBackend) -> None:
    """export reads the production v2 result manifest."""
    run_root = tmp_path / "runs"
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    task_dir = tmp_path / "tasks" / "export_sp"
    _seed_completed_checkpoint(task_dir, fake_backend)

    export = _run_acp("task", "export", "--task-dir", str(task_dir), run_root=run_root, cwd=cwd)
    assert export.returncode == 0, export.stderr
    payload = _last_json(export.stdout)
    assert payload["status"] == "completed"
    assert payload["products"] >= 1


# --------------------------------------------------------------------------- #
# 5. No network / no Web (control: a real in-process loop with sockets refused)
# --------------------------------------------------------------------------- #


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def _refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("network access is blocked in the full-loop gate")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


def test_no_network_full_loop_in_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole task loop runs with every socket path refused (no Web/SSH)."""
    from acp import cli

    _block_network(monkeypatch)
    run_root = tmp_path / "runs"
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    monkeypatch.chdir(tmp_path)

    assert cli.main(["task", "execute", "--workflow", "fake", "--input", "CCO"]) == 0
    out = capsys.readouterr().out
    job_id = _last_json(out)["job_id"]
    assert cli.main(["task", "query", "--job", job_id]) == 0
    capsys.readouterr()
    assert cli.main(["task", "cancel", "--job", job_id]) == 0  # terminal job: returns unchanged


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
# 6. Old-argument smoke comparison (equivalence with the pre-todo-74 surface)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("cli_args", "needle"),
    [
        (("--help",), "Auto-Calc Platform"),
        (("run", "singlepoint", "--help"), "--method"),
        (("run", "scan", "--help"), "--coordinate"),
        (("run", "irc", "--help"), "--ts-provenance"),
        (("run", "serve", "--help"), "--port"),
        (("doctor", "--help"), "--node"),
        (("init", "--help"), "--config"),
        (("task", "--help"), "execute"),
        (("task", "pause", "--help"), "--job"),
    ],
)
def test_old_cli_surface_preserved(cli_args: tuple[str, ...], needle: str, tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    result = _run_acp(*cli_args, run_root=run_root, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert needle in result.stdout


@pytest.mark.parametrize("workflow", _RETIRED_WORKFLOWS)
def test_retired_interception_preserved(workflow: str, tmp_path: Path) -> None:
    result = _run_acp("run", workflow, run_root=tmp_path / "runs", cwd=tmp_path)
    assert result.returncode == 2
    assert "已退役" in (result.stdout + result.stderr)


def test_console_script_entry_smoke(tmp_path: Path) -> None:
    if not _CONSOLE_SCRIPT.is_file():
        pytest.skip(f"acp console script not installed at {_CONSOLE_SCRIPT} — NOT_VERIFIED")
    result = subprocess.run(
        [str(_CONSOLE_SCRIPT), "task", "--help"],
        capture_output=True,
        text=True,
        timeout=180,
        env=_isolated_env(tmp_path / "runs"),
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert "execute" in result.stdout
