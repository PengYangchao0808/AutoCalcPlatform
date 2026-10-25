# pyright: reportAny=false, reportUnknownMemberType=false, reportUnknownVariableType=false
"""B08: persistent idempotent submission and unknown-result reconciliation.

The persistent ledger lives in the Controller's own jobs DB (migration 022).
These tests exercise it at three levels:

* the ``JobStore`` CAS/takeover/fencing contract (one process);
* real subprocess crash windows (SIGKILL/``os._exit`` at a named stage) plus a
  Controller restart on the same run root and DB;
* two independent ``acp mcp serve`` OS processes replaying the same key against
  one live Controller.

No test claims remote exactly-once: the remote window is ``NOT_VERIFIED`` here
because LSF is absent.  The local path is exercised without a counter stub in
the live Controller test (the job's ``job.started`` event is the dispatch fact).
"""

from __future__ import annotations

import json
import os
import queue
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import pytest

from acp.scheduler.manager import JobManager
from acp_services.runtime.admission import (
    AdmissionRequest,
    Capacity,
    ReservationLedger,
    ReserveLimits,
)
from acp_services.runtime.approvals import ApprovalService
from acp_services.runtime.canonical_digest import frozen_plan_definition, plan_digest
from acp_services.runtime.idempotency import (
    ClaimState,
    IdempotencyContext,
    IdempotencyLeaseError,
    LedgerReservationPort,
    canonical_request_digest,
    resolve_scope_id,
)
from acp_services.store.records import JobRecord, JobSpec, JobStatus
from acp_services.store.store import JobStore

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
PYTHON = sys.executable
SCOPE = resolve_scope_id("principal_local", "b08-project")
PAYLOAD = {"workflow": "fake", "name": "b08", "input": {"source": "C"}}


QC_SHIM_NAMES = ("orca", "xtb", "crest", "censo", "isostat", "shermo", "molclus")


def _qc_shims(run_root: Path) -> Path:
    """Reachable stand-in QC executables for cluster-less CI runners."""
    shim = run_root.parent / "_qc_shims"
    shim.mkdir(parents=True, exist_ok=True)
    for name in QC_SHIM_NAMES:
        exe = shim / name
        if not exe.exists():
            exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            exe.chmod(0o755)
    return shim


def _env(run_root: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(SRC_ROOT) if not existing else f"{SRC_ROOT}{os.pathsep}{existing}"
    env["ACP_RUN_ROOT"] = str(run_root)
    shim = _qc_shims(run_root)
    for name in QC_SHIM_NAMES:
        env[f"CONFSEARCH_{name.upper()}_PATH"] = str(shim / name)
    env.update(extra)
    return env


def _context(key: str, *, lease: float = 60.0, wait: float = 5.0) -> IdempotencyContext:
    return IdempotencyContext(
        key=key,
        scope_id=SCOPE,
        request_payload=PAYLOAD,
        lease_seconds=lease,
        wait_seconds=wait,
    )


def _reservation_port(ledger: ReservationLedger) -> LedgerReservationPort:
    return LedgerReservationPort(
        ledger,
        request_factory=lambda claim_id, spec: AdmissionRequest(
            principal_id="principal_local",
            project_id="b08-project",
            job_id=claim_id,
            cores=1,
        ),
        limits_factory=lambda request: ReserveLimits(
            capacity=Capacity(cores=8, memory_mb=8192, concurrency=8),
            policy_version="b08-test",
        ),
    )


def _spec() -> JobSpec:
    return JobSpec(workflow="fake", name="b08", input={"source": "C"})


CHILD_SCRIPT = r"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["ACP2_SRC"])
from acp.scheduler.manager import JobManager
from acp_services.runtime.admission import (
    AdmissionRequest,
    Capacity,
    ReservationLedger,
    ReserveLimits,
)
from acp_services.runtime.idempotency import (
    IdempotencyContext,
    LedgerReservationPort,
    resolve_scope_id,
)
from acp_services.store.records import JobSpec

run_root = Path(os.environ["ACP2_RUN_ROOT"])
mode = os.environ["ACP2_MODE"]
counter = Path(os.environ["ACP2_COUNTER"])
ledger = None
port = None
if mode == "reservation":
    ledger = ReservationLedger(str(run_root / "admission.db"))

    def request_factory(claim_id, spec):
        return AdmissionRequest(
            principal_id="principal_local",
            project_id="b08-project",
            job_id=claim_id,
            cores=1,
        )

    def limits_factory(request):
        return ReserveLimits(
            capacity=Capacity(cores=8, memory_mb=8192, concurrency=8),
            policy_version="b08-test",
        )

    port = LedgerReservationPort(
        ledger, request_factory=request_factory, limits_factory=limits_factory
    )

manager = JobManager(run_root=run_root, poll_interval=30, idempotency_reservations=port)
crash_at = os.environ["ACP2_CRASH_AT"]


def fault(name):
    if name == crash_at:
        os._exit(137)


manager._fault_point_hook = fault
if mode == "spawn":

    def counted(*args, **kwargs):
        del args, kwargs
        with counter.open("a", encoding="utf-8") as handle:
            handle.write("spawn\n")

    manager.runner.submit = counted

child_spec = (
    JobSpec(workflow="singlepoint", name="b08", input={"source": "CCO"})
    if crash_at == "dispatch.after_local_spawn"
    else JobSpec(workflow="fake", name="b08", input={"source": "C"})
)

context = IdempotencyContext(
    key=os.environ["ACP2_KEY"],
    scope_id=resolve_scope_id("principal_local", "b08-project"),
    request_payload={"workflow": "fake", "name": "b08", "input": {"source": "C"}},
    lease_seconds=float(os.environ.get("ACP2_LEASE", "60")),
    wait_seconds=2,
)
manager.submit(child_spec, idempotency=context)
if mode == "spawn":
    import time

    time.sleep(10)
print("child completed")
"""


def _run_child(
    tmp_path: Path, *, mode: str, crash_at: str, key: str, lease: str = "60"
) -> subprocess.CompletedProcess[str]:
    counter = tmp_path / "spawn-count.txt"
    env = _env(
        tmp_path / "run-root",
        ACP2_SRC=str(SRC_ROOT),
        ACP2_RUN_ROOT=str(tmp_path / "run-root"),
        ACP2_MODE=mode,
        ACP2_CRASH_AT=crash_at,
        ACP2_KEY=key,
        ACP2_LEASE=lease,
        ACP2_COUNTER=str(counter),
    )
    (tmp_path / "run-root").mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [PYTHON, "-c", CHILD_SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return completed


def _claim_for(store: JobStore, key: str) -> Any:
    return store.find_idempotency_claim(SCOPE, "submit", key)


def _lapse_claim_lease(store: JobStore, claim_id: str) -> None:
    """Persist the claim's lease in the past to model controller downtime.

    The restart sweep is the behaviour under test, not the wall clock. Leaving
    expiry to the child's short lease races the parent's restart path: a warm
    process can reach the sweep within the lease window (observed ~19 ms for a
    50 ms lease), so the claim is still live and the test flips with test order
    and cache state. Ageing the durable row states the fixture fact — the lease
    lapsed while the controller was down — while the sweep, staging cleanup and
    reservation release stay real. Mirrors the direct-SQLite fixture seeding
    already used for time-dependent rows (``test_local_cleanup``).
    """
    lapsed = "2020-01-01T00:00:00+00:00"
    conn = sqlite3.connect(store.db_path)
    try:
        cursor = conn.execute(
            "UPDATE idempotency_records SET lease_expires_at=? WHERE claim_id=? AND state=?",
            (lapsed, claim_id, ClaimState.CLAIMED.value),
        )
        assert cursor.rowcount == 1
        conn.commit()
    finally:
        conn.close()


def test_claim_replay_conflict_and_epoch_takeover(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    digest = canonical_request_digest(PAYLOAD)
    first = store.claim_idempotency(SCOPE, "submit", "key-00000001", digest, lease_seconds=0.01)
    assert first.created and first.claim is not None
    claim = first.claim

    live = store.claim_idempotency(SCOPE, "submit", "key-00000001", digest)
    assert live.in_progress and live.retryable
    assert live.claim is not None and live.claim.owner_token is None

    time.sleep(0.02)
    expired = store.expire_stale_idempotency_claims()
    assert [item.claim_id for item in expired] == [claim.claim_id]

    takeover = store.claim_idempotency(SCOPE, "submit", "key-00000001", digest)
    assert takeover.created and takeover.claim is not None
    assert takeover.claim.lease_epoch == claim.lease_epoch + 1
    assert takeover.claim.owner_token != claim.owner_token

    record = JobRecord(
        id="job-1",
        spec=_spec(),
        status=JobStatus.QUEUED,
        work_dir=str(tmp_path / "work" / "job-1"),
    )
    final = store.finalize_idempotent_job(
        record,
        takeover.claim.claim_id,
        takeover.claim.owner_token or "",
        takeover.claim.lease_epoch,
    )
    assert final.created and final.task_ref is not None
    assert final.task_ref["task_id"] == "job-1"

    replay = store.claim_idempotency(SCOPE, "submit", "key-00000001", digest)
    assert replay.replay and replay.task_ref is not None
    assert replay.task_ref["task_id"] == "job-1"

    conflict = store.claim_idempotency(
        SCOPE, "submit", "key-00000001", canonical_request_digest({"other": True})
    )
    assert conflict.conflict


def test_stale_owner_cannot_finalize_or_release(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    digest = canonical_request_digest(PAYLOAD)
    claim = store.claim_idempotency(SCOPE, "submit", "key-00000002", digest).claim
    assert claim is not None

    with pytest.raises(IdempotencyLeaseError):
        store.release_idempotency(
            claim.claim_id, owner_token="wrong", lease_epoch=claim.lease_epoch
        )
    with pytest.raises(IdempotencyLeaseError):
        store.finalize_idempotent_job(
            JobRecord(id="job-x", spec=_spec(), status=JobStatus.QUEUED, work_dir=str(tmp_path)),
            claim.claim_id,
            "wrong",
            claim.lease_epoch,
        )
    assert store.release_idempotency(
        claim.claim_id,
        owner_token=claim.owner_token,
        lease_epoch=claim.lease_epoch,
    )
    with pytest.raises(IdempotencyLeaseError):
        store.finalize_idempotent_job(
            JobRecord(id="job-y", spec=_spec(), status=JobStatus.QUEUED, work_dir=str(tmp_path)),
            claim.claim_id,
            claim.owner_token or "",
            claim.lease_epoch,
        )


def test_crash_after_reservation_before_job_row(tmp_path: Path) -> None:
    run_root = tmp_path / "run-root"
    key = "key-crash-reservation"
    completed = _run_child(
        tmp_path,
        mode="reservation",
        crash_at="idempotency.after_reservation",
        key=key,
        lease="0.05",
    )
    assert completed.returncode == 137, completed.stderr

    store = JobStore(run_root / "acp_jobs.db")
    assert store.list() == []
    claim = _claim_for(store, key)
    assert claim is not None and claim.state == ClaimState.CLAIMED.value
    ledger = ReservationLedger(str(run_root / "admission.db"))
    reservation_id = ledger.reservation_id_for_claim(claim.claim_id)
    assert reservation_id is not None
    _lapse_claim_lease(store, claim.claim_id)

    port = _reservation_port(ledger)
    manager = JobManager(run_root=run_root, poll_interval=30, idempotency_reservations=port)
    try:
        manager._sweep_idempotency_staging()
        assert store.list() == []
        expired = _claim_for(store, key)
        assert expired is not None and expired.state == ClaimState.EXPIRED.value
        assert ledger.list_active() == []
        reservation = ledger.get(reservation_id)
        assert reservation is not None and reservation.state == "released"
        assert not (run_root / ".staging" / claim.claim_id).exists()
    finally:
        manager.shutdown()


def test_crash_after_directory_binding_reuses_claim_dir(tmp_path: Path) -> None:
    run_root = tmp_path / "run-root"
    key = "key-crash-binding"
    completed = _run_child(
        tmp_path,
        mode="plain",
        crash_at="idempotency.before_finalize",
        key=key,
        lease="0.05",
    )
    assert completed.returncode == 137, completed.stderr

    store = JobStore(run_root / "acp_jobs.db")
    claim = _claim_for(store, key)
    assert claim is not None and claim.state == ClaimState.CLAIMED.value
    project_root = run_root / "uncategorized"
    bound_dirs = sorted(path for path in project_root.iterdir() if path.is_dir())
    assert len(bound_dirs) == 1
    bound = bound_dirs[0]
    assert (bound / ".claim_binding").is_file()

    manager = JobManager(run_root=run_root, poll_interval=30)
    try:
        manager._sweep_idempotency_staging()
        replay = manager.submit(_spec(), idempotency=_context(key))
        assert replay.work_dir == str(bound)
        assert sorted(path for path in project_root.iterdir() if path.is_dir()) == bound_dirs
        assert len(store.list()) == 1
    finally:
        manager.shutdown()


def test_crash_after_job_row_before_dispatch_then_replay(tmp_path: Path) -> None:
    run_root = tmp_path / "run-root"
    key = "key-crash-finalize"
    completed = _run_child(tmp_path, mode="spawn", crash_at="idempotency.after_finalize", key=key)
    assert completed.returncode == 137, completed.stderr

    store = JobStore(run_root / "acp_jobs.db")
    claim = _claim_for(store, key)
    assert claim is not None and claim.is_committed
    assert claim.task_id is not None
    record = store.get(claim.task_id)
    assert record is not None and record.status == JobStatus.QUEUED
    assert not (Path(record.work_dir) / "job.json").exists()

    dispatches: list[str] = []
    from acp.scheduler.runner import JobRunner

    original_submit = JobRunner.submit

    def counted(self: Any, *args: Any, **kwargs: Any) -> Any:
        dispatches.append(str(args[0].id))
        return original_submit(self, *args, **kwargs)

    JobRunner.submit = counted
    manager = None
    try:
        manager = JobManager(run_root=run_root, poll_interval=30)
        manager._dispatch_queued_jobs()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            fresh = store.get(claim.task_id)
            if fresh is not None and fresh.status.is_terminal:
                break
            time.sleep(0.05)
        replay = manager.submit(_spec(), idempotency=_context(key))
        assert getattr(replay, "_idempotent_replay", False)
        assert replay.id == claim.task_id
        assert len(store.list()) == 1
        assert dispatches == [claim.task_id]
        assert (Path(record.work_dir) / "job.json").is_file()
        assert not (run_root / ".staging" / claim.claim_id).exists()
        events = (Path(record.work_dir) / "WORK" / "00_RUNTIME" / "events.jsonl").read_text(
            encoding="utf-8"
        )
        assert events.count('"job.created"') == 1
    finally:
        JobRunner.submit = original_submit
        if manager is not None:
            manager.shutdown()


def test_local_spawn_lost_response_is_not_resubmitted(tmp_path: Path) -> None:
    run_root = tmp_path / "run-root"
    key = "key-crash-spawn"
    completed = _run_child(tmp_path, mode="spawn", crash_at="dispatch.after_local_spawn", key=key)
    assert completed.returncode == 137, completed.stderr
    child_spawns = (tmp_path / "spawn-count.txt").read_text(encoding="utf-8").splitlines()
    assert len(child_spawns) == 1

    from acp.scheduler.runner import JobRunner

    parent_spawns: list[str] = []
    original_submit = JobRunner.submit

    def counted(self: Any, *args: Any, **kwargs: Any) -> Any:
        parent_spawns.append(str(args[0].id))
        return original_submit(self, *args, **kwargs)

    JobRunner.submit = counted
    manager = None
    try:
        manager = JobManager(run_root=run_root, poll_interval=30)
        claim = _claim_for(manager.store, key)
        assert claim is not None and claim.is_committed
        replay = manager.submit(_spec(), idempotency=_context(key))
        assert replay.id == claim.task_id
        assert parent_spawns == []
    finally:
        JobRunner.submit = original_submit
        if manager is not None:
            manager.shutdown()


def test_remote_submission_identity_is_reused_without_fallback_submit(tmp_path: Path) -> None:
    manager = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        key = "key-remote-replay"
        digest = canonical_request_digest(PAYLOAD)
        claim = manager.store.claim_idempotency(SCOPE, "submit", key, digest).claim
        assert claim is not None
        record = JobRecord(
            id="remote-job-1",
            spec=_spec(),
            status=JobStatus.PENDING,
            work_dir=str(tmp_path / "uncategorized" / "remote"),
            result={"remote": {"submission_id": "sub-123", "attempt": 1}},
        )
        manager.store.finalize_idempotent_job(
            record, claim.claim_id, claim.owner_token or "", claim.lease_epoch
        )
        started: list[str] = []
        manager._start_submission_thread = lambda job_id, name: started.append(job_id) or True
        replay = manager.submit(_spec(), idempotency=_context(key))
        assert replay.id == "remote-job-1"
        assert started == []
        fresh = manager.store.get("remote-job-1")
        assert fresh is not None
        assert fresh.result is not None
        assert fresh.result["remote"]["submission_id"] == "sub-123"
    finally:
        manager.shutdown()


class _McpProcess:
    def __init__(self, launch: dict[str, Any], timeout: float = 30) -> None:
        env = {**os.environ, **dict(launch.get("env") or {})}
        self.proc = subprocess.Popen(
            [str(launch["command"]), *list(launch["args"])],
            cwd=str(launch["cwd"]),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            text=False,
        )
        self.timeout = timeout
        self._next_id = 1
        self._frames: queue.Queue[bytes | None] = queue.Queue()
        self._stderr: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._frames.put(line)
        self._frames.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr.append(line.decode("utf-8", errors="replace"))

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        assert self.proc.stdin is not None
        self.proc.stdin.write(
            (
                json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
                + "\n"
            ).encode()
        )
        self.proc.stdin.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(method)
            try:
                line = self._frames.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(method) from None
            if line is None:
                raise RuntimeError("mcp process closed stdout: " + "".join(self._stderr)[-1000:])
            message = json.loads(line)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "b08-test", "version": "1"},
            },
        )
        assert self.proc.stdin is not None
        self.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.proc.stdin.flush()

    def submit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": "acp_submit", "arguments": arguments})

    def close(self) -> None:
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_json(url: str, timeout: float = 10) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_post(url: str, payload: dict[str, Any], timeout: float = 10) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_health(base: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            health = _http_json(base + "/api/health", timeout=1)
            if health.get("status") == "ok":
                return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.1)
    raise RuntimeError("controller did not become healthy")


def _start_controller(run_root: Path, port: int) -> subprocess.Popen[bytes]:
    env = _env(run_root, ACP_HOST="127.0.0.1", ACP_PORT=str(port), ACP_POLL_INTERVAL="1")
    env.setdefault("ACP_CANDIDATE_REVISION", "b08-test")
    return subprocess.Popen(
        [
            PYTHON,
            "-m",
            "uvicorn",
            "acp.api.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_controller(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _launch_descriptor(run_root: Path, base: str) -> dict[str, Any]:
    return {
        "launch_version": 1,
        "protocol_version": "2025-06-18",
        "command": PYTHON,
        "args": ["-m", "acp", "mcp", "serve", "--transport", "stdio", "--controller", base],
        "cwd": str(REPO_ROOT),
        "env": {"PYTHONPATH": str(SRC_ROOT), "ACP_RUN_ROOT": str(run_root)},
    }


def test_two_real_mcp_processes_replay_one_task_and_restart(tmp_path: Path) -> None:
    run_root = tmp_path / "run-root"
    run_root.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    key = "b08-" + uuid.uuid4().hex[:24]
    xyz_text = "2\nH2\nH 0 0 0\nH 0 0 0.74\n"
    arguments = {
        "workflow": "singlepoint",
        "input": {"source_type": "xyz_text", "source": xyz_text},
        "method": {"method": "r2SCAN-3c", "charge": 0, "multiplicity": 1},
        # Deliberately a legacy (non-research) launch: a bare one-off plan digest
        # would make every submit a research launch and the Controller refuses it
        # (403 approval_required) before the idempotency claim is ever reached.
        "idempotency_key": key,
    }
    launch = _launch_descriptor(run_root, base)

    controller = _start_controller(run_root, port)
    probes: list[_McpProcess] = []
    try:
        _wait_health(base)
        before = len(_http_json(base + "/api/v1/jobs?limit=1000")["jobs"])
        probes = [_McpProcess(launch) for _ in range(2)]
        for probe in probes:
            probe.initialize()
        barrier = threading.Barrier(2)
        results: list[dict[str, Any]] = [{} for _ in probes]
        errors: list[str] = ["" for _ in probes]

        def submit(index: int) -> None:
            try:
                barrier.wait(timeout=30)
                results[index] = probes[index].submit(arguments)
            except Exception as exc:  # noqa: BLE001 - recorded as an assertion detail
                errors[index] = f"{type(exc).__name__}: {exc}"

        threads = [threading.Thread(target=submit, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert errors == ["", ""], errors
        task_ids = []
        for result in results:
            data = result.get("structuredContent", {}).get("data") or {}
            ref = data.get("task_ref") or {}
            task_ids.append(ref.get("task_id") or data.get("reference", {}).get("id"))
        assert all(task_ids), results
        assert task_ids[0] and task_ids[0] == task_ids[1]
        after = len(_http_json(base + "/api/v1/jobs?limit=1000")["jobs"])
        assert after - before == 1

        task_id = str(task_ids[0])
        deadline = time.monotonic() + 60
        started = 0
        while time.monotonic() < deadline:
            job = _http_json(base + f"/api/v1/jobs/{task_id}")
            events = Path(job["work_dir"]) / "WORK" / "00_RUNTIME" / "events.jsonl"
            if events.is_file():
                started = events.read_text(encoding="utf-8").count('"job.started"')
            if started >= 1 or job.get("status") in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.1)
        assert started == 1

        changed = dict(arguments)
        changed["input"] = {"source_type": "xyz_text", "source": "2\nH2\nH 0 0 0\nH 0 0 0.9\n"}
        conflict = probes[0].submit(changed)
        assert conflict.get("isError") is True
        assert "idempotency" in json.dumps(conflict).lower()

        project = _http_post(
            base + "/api/v1/projects", {"name": f"b08-scope-{uuid.uuid4().hex[:8]}"}
        )
        changed_scope = dict(arguments)
        changed_scope["project_id"] = project.get("project_id") or project.get("id")
        scope_result = probes[1].submit(changed_scope)
        assert scope_result.get("isError") is True
        assert len(_http_json(base + "/api/v1/jobs?limit=1000")["jobs"]) == after
    finally:
        for probe in probes:
            probe.close()
        _stop_controller(controller)

    _wait_port_free(port)
    controller = _start_controller(run_root, port)
    probe = None
    try:
        _wait_health(base)
        count_before_replay = len(_http_json(base + "/api/v1/jobs?limit=1000")["jobs"])
        probe = _McpProcess(launch)
        probe.initialize()
        replay = probe.submit(arguments)
        data = replay.get("structuredContent", {}).get("data", {})
        ref = data.get("task_ref") or {}
        assert (ref.get("task_id") or data.get("reference", {}).get("id")) == task_id
        assert len(_http_json(base + "/api/v1/jobs?limit=1000")["jobs"]) == count_before_replay
    finally:
        if probe is not None:
            probe.close()
        _stop_controller(controller)


def _wait_port_free(port: int, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"port {port} did not become free")


def test_bare_plan_digest_is_denied_before_idempotency(tmp_path: Path) -> None:
    run_root = tmp_path / "run-root"
    run_root.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    arguments = {
        "workflow": "fake",
        "input": {"source": "C"},
        "plan_digest": "sha256:" + "0" * 64,
        "idempotency_key": "m1-bare-" + uuid.uuid4().hex[:24],
    }
    launch = _launch_descriptor(run_root, base)
    controller = _start_controller(run_root, port)
    probe = None
    try:
        _wait_health(base)
        before = len(_http_json(base + "/api/v1/jobs?limit=1000")["jobs"])
        probe = _McpProcess(launch)
        probe.initialize()
        result = probe.submit(arguments)
        assert result.get("isError") is True, result
        structured = result.get("structuredContent") or {}
        assert structured.get("exit_code") == 5, result
        assert "approval_required" in json.dumps(result).lower(), result
        assert len(_http_json(base + "/api/v1/jobs?limit=1000")["jobs"]) == before
    finally:
        if probe is not None:
            probe.close()
        _stop_controller(controller)


def test_approved_research_launch_submits_one_task(tmp_path: Path) -> None:
    run_root = tmp_path / "run-root"
    run_root.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    workflow = "fake"
    workflow_input = {"source": "C"}
    arguments = {
        "workflow": workflow,
        "input": workflow_input,
        "plan_digest": plan_digest(
            frozen_plan_definition(workflow=workflow, input=workflow_input)
        ).digest,
        "proposal_ref": "prop-" + uuid.uuid4().hex[:16],
        "idempotency_key": "m1-approved-" + uuid.uuid4().hex[:24],
    }
    launch = _launch_descriptor(run_root, base)
    controller = _start_controller(run_root, port)
    probe = None
    try:
        _wait_health(base)
        approvals = ApprovalService(run_root / "acp_jobs.db")
        pending, _ = approvals.request(
            proposal_ref=arguments["proposal_ref"],
            proposal_digest="sha256:" + "a" * 64,
            plan_digest=arguments["plan_digest"],
            project_id="uncategorized",
            requester_principal="principal_local",
        )
        approvals.approve(
            pending.approval_id,
            approver_principal="principal_local",
            expected_revision=pending.revision,
        )
        before = len(_http_json(base + "/api/v1/jobs?limit=1000")["jobs"])
        probe = _McpProcess(launch)
        probe.initialize()
        result = probe.submit(arguments)
        assert result.get("isError") is not True, result
        data = (result.get("structuredContent") or {}).get("data") or {}
        ref = data.get("task_ref") or {}
        assert ref.get("task_id") or data.get("reference", {}).get("id"), result
        assert len(_http_json(base + "/api/v1/jobs?limit=1000")["jobs"]) == before + 1
    finally:
        if probe is not None:
            probe.close()
        _stop_controller(controller)
