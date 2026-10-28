"""M4-03 — LSF submission, execution limits and control (real + unit).

The acceptance matrix lives in ``scripts/acceptance/acp2_m403.py`` (real
Controller + real Openlava).  This suite pins the parts that must never regress
silently:

* the pure deadline decision (frozen precedence, PAUSED time, restart
  remaining time) and its receipt shape;
* the approval deadline reaching ``#BSUB -W`` through the durable admission
  projection — and an unbounded approval never inventing one;
* the manager sweep terminating a **real** local process group at the deadline
  and persisting a verified receipt (no mocks around the kill);
* a terminal row never being revived by the sweep or by the poll scan;
* (``slow``) the native Openlava ``-W`` → ``EXIT 140`` termination the LSF
  three-way check depends on.

The real end-to-end matrix (Controller, bsub, bjobs, queue deadline, restart,
multiuser) is executed by the acceptance script, not duplicated here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.remote.script_gen import build_lsf_script_spec, generate_lsf_script
from acp_services.runtime.deadline_enforcement import (
    DECISION_EXPIRE_QUEUE,
    DECISION_NONE,
    DECISION_TERMINATE_WALL,
    attempt_clock_from_record,
    evaluate_deadline,
    receipt_from_decision,
)
from acp_services.runtime.deadline_wiring import (
    admission_walltime_minutes,
    deadline_wiring,
    lsf_walltime_string,
)
from acp_services.runtime.execution_limits import (
    compute_attempt_clock,
    limits_from_execution_limits_block,
    resolve_resource_limits,
)

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# pure decision + receipt
# ---------------------------------------------------------------------------


def test_queue_clock_governs_before_start_and_wall_after() -> None:
    now = datetime(2026, 10, 27, 12, 0, 0, tzinfo=_UTC)
    clock = compute_attempt_clock(
        admitted_at=now - timedelta(seconds=40),
        wall_seconds=120,
        queue_seconds=30,
    )
    before = evaluate_deadline(clock, now)
    assert before.decision == DECISION_EXPIRE_QUEUE
    assert before.terminal_status == "failed"

    healthy = compute_attempt_clock(
        admitted_at=now - timedelta(seconds=5),
        wall_seconds=120,
        queue_seconds=30,
    )
    assert evaluate_deadline(healthy, now).decision == DECISION_NONE

    started = compute_attempt_clock(
        admitted_at=now - timedelta(seconds=200),
        started_at=now - timedelta(seconds=130),
        wall_seconds=120,
        queue_seconds=30,
    )
    wall = evaluate_deadline(started, now)
    assert wall.decision == DECISION_TERMINATE_WALL
    assert wall.deadline_at == now - timedelta(seconds=10)


def test_unbounded_attempt_never_expires() -> None:
    clock = compute_attempt_clock(
        admitted_at=datetime.now(_UTC) - timedelta(days=3),
        started_at=datetime.now(_UTC) - timedelta(days=2),
    )
    assert evaluate_deadline(clock, datetime.now(_UTC)).decision == DECISION_NONE


def test_paused_time_counts_and_restart_restores_only_the_remainder() -> None:
    now = datetime(2026, 10, 27, 12, 0, 0, tzinfo=_UTC)
    started_at = now - timedelta(seconds=90)
    clock = compute_attempt_clock(
        admitted_at=now - timedelta(seconds=100),
        started_at=started_at,
        wall_seconds=120,
        paused_seconds=40,  # a 40 s pause does NOT extend the deadline
    )
    assert clock.wall_deadline_at == started_at + timedelta(seconds=120)
    assert clock.remaining_seconds(now) == 30
    # A restart rebuilds the same absolute deadline from the durable columns.
    rebuilt = compute_attempt_clock(
        admitted_at=now - timedelta(seconds=100),
        started_at=started_at,
        wall_seconds=120,
        paused_seconds=40,
    )
    assert rebuilt.wall_deadline_at == clock.wall_deadline_at


def test_receipt_carries_the_clock_the_deadline_and_verification() -> None:
    now = datetime(2026, 10, 27, 12, 0, 0, tzinfo=_UTC)
    clock = compute_attempt_clock(
        admitted_at=now - timedelta(seconds=70),
        started_at=now - timedelta(seconds=60),
        wall_seconds=60,
    )
    decision = evaluate_deadline(clock, now)
    receipt = receipt_from_decision(
        job_id="job-1",
        attempt=2,
        decision=decision,
        clock=clock,
        observed_at=now,
        surface="lsf",
        native={"surface": "lsf", "confirmed_stopped": True, "bjobs_l_after": "EXIT"},
        verified=True,
    ).to_dict()
    assert receipt["schema"] == "acp2.runtime.deadline-receipt.v1"
    assert receipt["decision"] == DECISION_TERMINATE_WALL
    assert receipt["wall_seconds"] == 60
    assert receipt["clock"] == "wall"
    assert receipt["attempt"] == 2
    assert receipt["verified"] is True
    assert receipt["surface"] == "lsf"


# ---------------------------------------------------------------------------
# approval deadline -> bsub -W
# ---------------------------------------------------------------------------


def test_admission_projection_is_the_only_walltime_source() -> None:
    limits = resolve_resource_limits({"wall_seconds": 60})
    admission = {"execution_limits": limits.to_dict()}
    assert admission_walltime_minutes(admission) == 1
    assert lsf_walltime_string(admission_walltime_minutes(admission)) == "00:01"
    # An empty cluster walltime is "not limited", never a zero-minute limit.
    assert admission_walltime_minutes({"execution_limits": {}}) is None
    assert admission_walltime_minutes(None) is None
    assert deadline_wiring({}, cluster_walltime_s=None).lsf_walltime is None
    # A resolved-but-unbounded projection reads back as unbounded.
    assert limits_from_execution_limits_block({}).lsf_walltime_minutes is None


def test_runner_renders_the_approval_deadline_as_bsub_w() -> None:
    spec = JobSpec(
        workflow="singlepoint",
        name="m403-wall",
        input={"source": "CCO"},
        resources={"cores": 1, "mem_mb": 1024},
    )
    from acp.scheduler.remote.config import RemoteNode

    node = RemoteNode(
        name="n",
        host="127.0.0.1",
        username="u",
        remote_work_dir="/tmp/jobs",
        remote_code_dir="/tmp/code",
    )
    approval = {"execution_limits": resolve_resource_limits({"wall_seconds": 60}).to_dict()}
    lsf_spec, _ = build_lsf_script_spec(
        spec,
        "job-1",
        node,
        walltime="",
        walltime_minutes=admission_walltime_minutes(approval),
    )
    script = generate_lsf_script(lsf_spec)
    assert "#BSUB -W 00:01" in script
    unbounded, _ = build_lsf_script_spec(spec, "job-1", node, walltime="")
    assert "#BSUB -W" not in generate_lsf_script(unbounded)


# ---------------------------------------------------------------------------
# manager sweep: real local process group termination
# ---------------------------------------------------------------------------


def _wait_dead(pid: int, timeout: float = 10.0) -> bool:
    from acp.scheduler.processctl import pid_is_alive

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_is_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_is_alive(pid)


def _running_job(
    manager: JobManager,
    tmp_path: Path,
    *,
    job_id: str,
    wall_seconds: int | None = None,
    queue_seconds: int | None = None,
    pid: int | None = None,
    started: bool = True,
) -> JobRecord:
    work_dir = tmp_path / job_id
    # v2 task layout: runtime files (events/receipts) live under WORK/00_RUNTIME.
    (work_dir / "WORK" / "00_RUNTIME").mkdir(parents=True, exist_ok=True)
    limits = (
        resolve_resource_limits({"wall_seconds": wall_seconds} if wall_seconds else {})
        if (wall_seconds or queue_seconds)
        else None
    )
    block = limits.to_dict() if limits is not None else {}
    if queue_seconds is not None:
        block["queue_deadline_s"] = queue_seconds
    admission = {"execution_limits": block} if block else {}
    record = JobRecord(
        id=job_id,
        spec=JobSpec(workflow="singlepoint", name=job_id, input={"source": "CCO"}),
        status=JobStatus.RUNNING if started else JobStatus.QUEUED,
        work_dir=str(work_dir),
        created_at=(datetime.now(_UTC) - timedelta(seconds=30)).isoformat(),
        started_at=((datetime.now(_UTC) - timedelta(seconds=15)).isoformat() if started else None),
        pid=pid,
        result={"admission": admission} if admission else {},
        project_id="default",
    )
    manager.store.create(record)
    return record


def test_sweep_terminates_a_real_local_process_group_at_the_deadline(
    tmp_path: Path,
) -> None:
    store_root = tmp_path / "run-root"
    manager = JobManager(run_root=store_root, poll_interval=60, max_running=1)
    work_dir = tmp_path / "local-wall"
    work_dir.mkdir(parents=True, exist_ok=True)
    sleeper = subprocess.Popen(["sleep", "300"], cwd=str(work_dir), start_new_session=True)
    try:
        record = _running_job(
            manager,
            tmp_path,
            job_id="local-wall",
            wall_seconds=2,
            pid=sleeper.pid,
        )
        assert attempt_clock_from_record(record).wall_deadline_at is not None
        manager._enforce_execution_deadlines()
        stored = manager.store.get("local-wall")
        assert stored is not None
        assert stored.status == JobStatus.FAILED
        assert "WALLTIME_EXCEEDED" in (stored.error or "")
        receipt_path = Path(stored.work_dir) / "WORK" / "00_RUNTIME" / "deadline_receipt.json"
        assert receipt_path.is_file()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt["decision"] == DECISION_TERMINATE_WALL
        assert receipt["verified"] is True
        assert receipt["native"]["confirmed_stopped"] is True
        assert _wait_dead(sleeper.pid)
        # A terminal row is never revived, and a second sweep is a no-op.
        manager._enforce_execution_deadlines()
        again = manager.store.get("local-wall")
        assert again is not None and again.status == JobStatus.FAILED
        assert again.revision == stored.revision
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
        sleeper.wait(timeout=10)
        manager.shutdown()


def test_sweep_expires_a_queued_attempt_without_any_process(tmp_path: Path) -> None:
    manager = JobManager(run_root=tmp_path / "run-root", poll_interval=60, max_running=1)
    try:
        _running_job(
            manager,
            tmp_path,
            job_id="queued-expired",
            queue_seconds=5,
            started=False,
        )
        manager._enforce_execution_deadlines()
        stored = manager.store.get("queued-expired")
        assert stored is not None
        assert stored.status == JobStatus.FAILED
        assert "QUEUE_DEADLINE_EXPIRED" in (stored.error or "")
        receipt = json.loads(
            (Path(stored.work_dir) / "WORK" / "00_RUNTIME" / "deadline_receipt.json").read_text(
                encoding="utf-8"
            )
        )
        assert receipt["decision"] == DECISION_EXPIRE_QUEUE
        assert receipt["queue_seconds"] == 5
    finally:
        manager.shutdown()


def test_an_unbounded_attempt_is_never_touched_by_the_sweep(tmp_path: Path) -> None:
    manager = JobManager(run_root=tmp_path / "run-root", poll_interval=60, max_running=1)
    try:
        _running_job(manager, tmp_path, job_id="unbounded", started=True)
        manager._enforce_execution_deadlines()
        stored = manager.store.get("unbounded")
        assert stored is not None and stored.status == JobStatus.RUNNING
        assert not (
            Path(stored.work_dir) / "WORK" / "00_RUNTIME" / "deadline_receipt.json"
        ).exists()
    finally:
        manager.shutdown()


# ---------------------------------------------------------------------------
# native LSF: the -W termination the three-way check relies on
# ---------------------------------------------------------------------------


def _lsf_available() -> bool:
    return bool(shutil.which("bsub")) and bool(shutil.which("bjobs"))


@pytest.mark.slow
def test_native_openlava_walltime_terminates_with_a_native_record(tmp_path: Path) -> None:
    if not _lsf_available():
        pytest.skip("Openlava client is not on PATH")
    script = tmp_path / "wall.lsf"
    script.write_text(
        "#!/bin/bash\n"
        "#BSUB -J m403_pytest_wall\n"
        "#BSUB -q normal\n"
        "#BSUB -n 1\n"
        "#BSUB -W 00:01\n"
        "#BSUB -o /dev/null\n"
        "#BSUB -e /dev/null\n"
        "sleep 180\n",
        encoding="utf-8",
    )
    submitted = subprocess.run(
        f"bsub < {script}", shell=True, capture_output=True, text=True, timeout=120
    )
    assert submitted.returncode == 0, submitted.stderr
    lsf_id = ""
    for token in submitted.stdout.replace("<", " ").replace(">", " ").split():
        if token.isdigit():
            lsf_id = token
            break
    assert lsf_id, submitted.stdout
    try:
        detail = ""
        deadline = time.time() + 240
        while time.time() < deadline:
            detail = subprocess.run(
                ["bjobs", "-l", lsf_id], capture_output=True, text=True, timeout=60
            ).stdout
            if "Exited with exit code 140" in detail or "RUNLIMIT" in detail:
                if "Exited with exit code 140" in detail:
                    break
            time.sleep(3)
        assert "RUNLIMIT" in detail
        assert "Exited with exit code 140" in detail, detail
    finally:
        subprocess.run(["bkill", lsf_id], capture_output=True, text=True, timeout=60)
