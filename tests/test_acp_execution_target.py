"""Tests for the Unified Execution Target (Phase 1).

Covers DevDoc ``docs/ACP_Unified_Execution_Target_DevDoc.txt`` §16:
NodeRegistry construction, conflict validation, resolution priority,
``_is_remote_job`` provenance routing, M3 poll transport tolerance,
M5 local admission, and the exception dichotomy.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.nodes import (
    ExecutionCapacityUnavailable,
    ExecutionTargetError,
    NodeRegistry,
    NodeSpec,
    validate_execution_request,
)

try:
    from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
except ImportError:  # paramiko not installed — remote-config tests skip
    RemoteExecutionConfig = None
    RemoteNode = None

requires_remote_config = pytest.mark.skipif(RemoteNode is None, reason="paramiko not installed")


def _node(name: str, max_jobs: int = 4, enabled: bool = True):
    """Duck-typed RemoteNode stand-in (NodeRegistry only reads attributes)."""
    return SimpleNamespace(
        name=name,
        host=f"{name}.example.com",
        max_concurrent_jobs=max_jobs,
        enabled=enabled,
    )


def _real_node(name: str, max_jobs: int = 4, enabled: bool = True) -> RemoteNode:
    return RemoteNode(
        name=name,
        host=f"{name}.example.com",
        username="qc",
        remote_work_dir="/scratch/qc/acp",
        remote_code_dir="/home/qc/acp_code",
        max_concurrent_jobs=max_jobs,
        enabled=enabled,
    )


def _status(name: str, running: int, status: str = "online") -> SimpleNamespace:
    return SimpleNamespace(name=name, status=status, running_jobs=running)


# --------------------------------------------------------------------- #
# NodeRegistry construction & mapping
# --------------------------------------------------------------------- #


def test_registry_constructs_local_automatically() -> None:
    reg = NodeRegistry(local_max_jobs=4, remote_nodes=[])
    assert reg.local.name == "local"
    assert reg.local.kind == "local"
    assert reg.local.max_jobs == 4
    assert [n.name for n in reg.nodes] == ["local"]


def test_registry_maps_remote_nodes() -> None:
    reg = NodeRegistry(local_max_jobs=2, remote_nodes=[_node("compute-01", max_jobs=20)])
    names = [n.name for n in reg.nodes]
    assert names == ["local", "compute-01"]
    remote = reg.nodes[1]
    assert remote.kind == "remote"
    assert remote.max_jobs == 20
    assert remote.host == "compute-01.example.com"


def test_registry_nodespec_field_subtraction() -> None:
    spec = NodeSpec(name="local", kind="local")
    assert not hasattr(spec, "priority")
    assert not hasattr(spec, "tags")
    assert spec.max_jobs > 0


def test_derive_local_state_ready_busy() -> None:
    reg = NodeRegistry(local_max_jobs=2, remote_nodes=[])
    assert reg.derive_local_state(0).status == "ready"
    assert reg.derive_local_state(1).status == "ready"
    assert reg.derive_local_state(2).status == "busy"


# --------------------------------------------------------------------- #
# validate_execution_request — conflict matrix (§8)
# --------------------------------------------------------------------- #


def test_validate_remote_mode_with_local_target_conflicts() -> None:
    spec = JobSpec(workflow="fake", execution_mode="remote", target_node="local")
    with pytest.raises(ExecutionTargetError):
        validate_execution_request(spec)


def test_validate_local_mode_with_remote_target_conflicts() -> None:
    spec = JobSpec(workflow="fake", execution_mode="local", target_node="compute-01")
    with pytest.raises(ExecutionTargetError):
        validate_execution_request(spec)


def test_validate_allowed_combinations() -> None:
    validate_execution_request(JobSpec(workflow="fake", target_node="compute-01"))
    validate_execution_request(
        JobSpec(workflow="fake", execution_mode="remote", target_node="compute-01")
    )
    validate_execution_request(JobSpec(workflow="fake", execution_mode="local"))
    validate_execution_request(
        JobSpec(workflow="fake", execution_mode="local", target_node="local")
    )
    validate_execution_request(JobSpec(workflow="fake", target_node="local"))
    validate_execution_request(JobSpec(workflow="fake"))


# --------------------------------------------------------------------- #
# NodeRegistry.require / select_remote
# --------------------------------------------------------------------- #


def test_require_unknown_or_disabled_fails_fast() -> None:
    reg = NodeRegistry(
        local_max_jobs=1,
        remote_nodes=[_node("compute-01"), _node("compute-02", enabled=False)],
    )
    assert reg.require("local").kind == "local"
    assert reg.require("compute-01").name == "compute-01"
    with pytest.raises(ExecutionTargetError):
        reg.require("ghost")
    with pytest.raises(ExecutionTargetError):
        reg.require("compute-02")


def test_select_remote_no_enabled_nodes_is_permanent_error() -> None:
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[])
    with pytest.raises(ExecutionTargetError):
        reg.select_remote()
    reg2 = NodeRegistry(local_max_jobs=1, remote_nodes=[_node("n1", enabled=False)])
    with pytest.raises(ExecutionTargetError):
        reg2.select_remote()


def test_select_remote_all_full_is_temporary_error() -> None:
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[_node("n1", max_jobs=2)])
    reg.status_provider = lambda name: _status(name, running=2)
    with pytest.raises(ExecutionCapacityUnavailable):
        reg.select_remote()


def test_select_remote_least_loaded_with_yaml_tiebreak() -> None:
    reg = NodeRegistry(
        local_max_jobs=1,
        remote_nodes=[_node("n1", max_jobs=4), _node("n2", max_jobs=4)],
    )
    loads = {"n1": _status("n1", running=3), "n2": _status("n2", running=1)}
    reg.status_provider = lambda name: loads[name]
    assert reg.select_remote().name == "n2"
    # Equal load → YAML order wins (deterministic tie-break).
    loads = {"n1": _status("n1", running=1), "n2": _status("n2", running=1)}
    reg.status_provider = lambda name: loads[name]
    assert reg.select_remote().name == "n1"


def test_select_remote_skips_offline_nodes() -> None:
    reg = NodeRegistry(
        local_max_jobs=1,
        remote_nodes=[_node("n1", max_jobs=4), _node("n2", max_jobs=4)],
    )
    loads = {"n1": _status("n1", running=0, status="offline"), "n2": _status("n2", running=2)}
    reg.status_provider = lambda name: loads[name]
    assert reg.select_remote().name == "n2"
    # All offline → temporary, not permanent.
    loads = {k: _status(k, running=0, status="offline") for k in ("n1", "n2")}
    reg.status_provider = lambda name: loads[name]
    with pytest.raises(ExecutionCapacityUnavailable):
        reg.select_remote()


# --------------------------------------------------------------------- #
# JobManager: _is_remote_job provenance routing (M2 / B3)
# --------------------------------------------------------------------- #


def test_is_remote_job_covers_three_sources() -> None:
    rec = JobRecord(id="a", spec=JobSpec(workflow="fake"), remote_job_id="123")
    assert JobManager._is_remote_job(rec) is True

    rec = JobRecord(id="b", spec=JobSpec(workflow="fake"), result={"lsf_job_id": "456"})
    assert JobManager._is_remote_job(rec) is True

    rec = JobRecord(id="c", spec=JobSpec(workflow="fake"), result={"execution_kind": "remote"})
    assert JobManager._is_remote_job(rec) is True

    rec = JobRecord(id="d", spec=JobSpec(workflow="fake"), result={"execution_kind": "local"})
    assert JobManager._is_remote_job(rec) is False

    rec = JobRecord(id="e", spec=JobSpec(workflow="fake"))
    assert JobManager._is_remote_job(rec) is False


# --------------------------------------------------------------------- #
# JobManager: resolution priority (§8) + provenance (§9)
# --------------------------------------------------------------------- #


@requires_remote_config
def test_resolve_priority_target_over_mode_over_default(tmp_path: Path) -> None:
    cfg = RemoteExecutionConfig(
        execution_mode="local", nodes=[_real_node("compute-01", max_jobs=8)]
    )
    mgr = JobManager(run_root=tmp_path, remote_config=cfg)
    try:
        # ① explicit target_node wins over everything (incl. "local")
        rec = JobRecord(
            id="r1",
            spec=JobSpec(workflow="fake", execution_mode="remote", target_node="compute-01"),
        )
        assert mgr._resolve_execution_target(rec).name == "compute-01"

        rec = JobRecord(id="r2", spec=JobSpec(workflow="fake", target_node="local"))
        assert mgr._resolve_execution_target(rec).kind == "local"

        # ② execution_mode preference beats the server default
        mgr.registry.status_provider = lambda name: _status(name, running=0)
        rec = JobRecord(id="r3", spec=JobSpec(workflow="fake", execution_mode="remote"))
        assert mgr._resolve_execution_target(rec).name == "compute-01"

        # ③ server default when neither is given
        rec = JobRecord(id="r4", spec=JobSpec(workflow="fake"))
        assert mgr._resolve_execution_target(rec).kind == "local"

        # unknown explicit target → permanent error, never silent fallback
        rec = JobRecord(id="r5", spec=JobSpec(workflow="fake", target_node="ghost"))
        with pytest.raises(ExecutionTargetError):
            mgr._resolve_execution_target(rec)
    finally:
        mgr.shutdown()


def test_record_execution_target_provenance(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path)
    try:
        work_dir = tmp_path / "prov"
        work_dir.mkdir(parents=True, exist_ok=True)
        rec = JobRecord(id="prov", spec=JobSpec(workflow="fake"), work_dir=str(work_dir))
        mgr.store.create(rec)
        mgr._record_execution_target(rec, mgr.registry.local)
        stored = mgr.store.get("prov")
        assert stored.result["execution_target"] == "local"
        assert stored.result["execution_kind"] == "local"
        events = mgr.event_log("prov").read_all()
        assert any(e["type"] == "execution.target_resolved" for e in events)
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# M1: capability vs default mode split (A1)
# --------------------------------------------------------------------- #


@requires_remote_config
def test_remote_runner_created_when_nodes_configured_despite_local_default(
    tmp_path: Path,
) -> None:
    cfg = RemoteExecutionConfig(execution_mode="local", nodes=[_real_node("compute-01")])
    mgr = JobManager(run_root=tmp_path, remote_config=cfg)
    try:
        assert mgr.remote_runner is not None
        assert mgr.default_execution_mode == "local"
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# M3: remote poll transport failure is NOT a job failure (C1/C2)
# --------------------------------------------------------------------- #


class _FlakyRemoteRunner:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def poll_remote(self, record, event_log, cancel_event):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("SSH transport down")
        return True, 0


def test_remote_poll_transport_failure_keeps_status(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path)
    try:
        fake = _FlakyRemoteRunner(fail_times=3)
        mgr.remote_runner = fake  # type: ignore[assignment]

        work_dir = tmp_path / "remote-job"
        work_dir.mkdir(parents=True, exist_ok=True)
        rec = JobRecord(
            id="rj",
            spec=JobSpec(workflow="fake"),
            status=JobStatus.RUNNING,
            work_dir=str(work_dir),
            remote_job_id="987654",
        )
        mgr.store.create(rec)

        for _ in range(3):
            mgr._poll_job("rj")
            cur = mgr.store.get("rj")
            assert cur.status == JobStatus.RUNNING
            assert cur.error is None
        assert mgr._poll_failures["rj"] == 3

        # Transport recovers → counter cleared, normal completion resumes.
        mgr._poll_job("rj")
        assert "rj" not in mgr._poll_failures
        assert mgr.store.get("rj").status == JobStatus.COMPLETED

        events = mgr.event_log("rj").read_all()
        unreachable = [e for e in events if e["type"] == "remote.poll_unreachable"]
        assert len(unreachable) == 3
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# M5: local admission gate (E1/E2/E3)
# --------------------------------------------------------------------- #


def test_local_admission_blocks_when_full(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, local_max_jobs=1)
    try:
        assert mgr.registry.local.max_jobs == 1

        work_dir = tmp_path / "running-local"
        work_dir.mkdir(parents=True, exist_ok=True)
        mgr.store.create(
            JobRecord(
                id="holder",
                spec=JobSpec(workflow="fake"),
                status=JobStatus.RUNNING,
                work_dir=str(work_dir),
            )
        )
        assert mgr.count_local_running_jobs() == 1

        newcomer = JobRecord(id="new", spec=JobSpec(workflow="fake"))
        with pytest.raises(ExecutionCapacityUnavailable):
            mgr._admit_local(newcomer)

        # Slot frees → admission passes again.
        holder = mgr.store.get("holder")
        holder.status = JobStatus.COMPLETED
        mgr.store.update(holder)
        mgr._admit_local(newcomer)
    finally:
        mgr.shutdown()


def test_local_admission_ignores_remote_starting_jobs(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, local_max_jobs=1)
    try:
        work_dir = tmp_path / "starting-remote"
        work_dir.mkdir(parents=True, exist_ok=True)
        mgr.store.create(
            JobRecord(
                id="remote-starting",
                spec=JobSpec(workflow="fake"),
                status=JobStatus.STARTING,
                work_dir=str(work_dir),
                result={"execution_kind": "remote"},
            )
        )
        assert mgr.count_local_running_jobs() == 0
        mgr._admit_local(JobRecord(id="new", spec=JobSpec(workflow="fake")))
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# Exception dichotomy (F1): permanent → fast FAILED via submission thread
# --------------------------------------------------------------------- #


def test_invalid_target_node_fails_fast_no_retry(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path)
    try:
        record = mgr.submit(JobSpec(workflow="Confsearch", target_node="ghost"))
        deadline = time.time() + 15
        cur = None
        while time.time() < deadline:
            cur = mgr.get(record.id)
            if cur is not None and cur.status.is_terminal:
                break
            time.sleep(0.2)
        assert cur is not None
        assert cur.status == JobStatus.FAILED
        assert "ghost" in (cur.error or "")
        # Failed fast — no waiting_for_capacity retry loop.
        events = mgr.event_log(record.id).read_all()
        assert not any(e["type"] == "execution.waiting_for_capacity" for e in events)
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# API surface: conflict → HTTP 400 (F2)
# --------------------------------------------------------------------- #


def test_api_rejects_conflicting_execution_request(tmp_path: Path) -> None:
    pytest.importorskip("paramiko")  # v1_routes imports remote fetcher at module level
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from acp.api.server import create_app

    app = create_app(run_root=tmp_path)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/jobs",
            json={"workflow": "fake", "execution_mode": "remote", "target_node": "local"},
        )
        assert resp.status_code == 400
        assert "conflicts" in resp.json()["detail"]

        resp = client.post(
            "/api/v1/jobs",
            json={"workflow": "fake", "execution_mode": "local", "target_node": "compute-01"},
        )
        assert resp.status_code == 400

        # Non-conflicting request passes validation (fake workflow completes).
        resp = client.post("/api/v1/jobs", json={"workflow": "fake", "target_node": "local"})
        assert resp.status_code == 201
