"""Tests for the Unified Execution Target (Phase 1 + W2-T4).

Covers DevDoc ``docs/ACP_Unified_Execution_Target_DevDoc.txt`` §16:
NodeRegistry construction, conflict validation, resolution priority,
``_is_remote_job`` provenance routing, M3 poll transport tolerance,
M5 local admission, and the exception dichotomy.

W2-T4 additions (plan node-selection-at-submission): capability-filtered
``select_remote`` (D8/D13), ``require`` hard check, in-flight reservations
(G2), and the D14 auto-escalation orchestration in
``JobManager._resolve_execution_target``.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.nodes import (
    ExecutionCapacityUnavailable,
    ExecutionTargetError,
    NodeRegistry,
    NodeSpec,
    validate_execution_request,
    validate_submission_target,
)
from acp.scheduler.store import JobStore

try:
    from acp.scheduler.remote.config import (
        NodeCapabilities,
        RemoteExecutionConfig,
        RemoteNode,
    )
except ImportError:  # paramiko not installed — remote-config tests skip
    NodeCapabilities = None
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
        capabilities=None,
    )


def _cap_node(
    name: str,
    software: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    max_jobs: int = 4,
    enabled: bool = True,
):
    """Duck-typed RemoteNode stand-in with a declared capability block."""
    return SimpleNamespace(
        name=name,
        host=f"{name}.example.com",
        max_concurrent_jobs=max_jobs,
        enabled=enabled,
        capabilities=NodeCapabilities(software=software, tags=tags),
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


def _status(
    name: str,
    running: int,
    status: str = "online",
    software: dict | None = None,
    max_jobs: int | None = None,
    disk_usage_pct: int | None = None,
) -> SimpleNamespace:
    ns = SimpleNamespace(name=name, status=status, running_jobs=running)
    if software is not None:
        ns.software = software
    if max_jobs is not None:
        ns.max_jobs = max_jobs
    if disk_usage_pct is not None:
        ns.disk_usage_pct = disk_usage_pct
    return ns


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
    # W2-T4 capability carriage: declared capabilities + tri-state summary
    # (live probe-inferred state is resolved at match time via the status
    # provider — the static field only records the declaration half).
    assert hasattr(spec, "capabilities")
    assert hasattr(spec, "capability_state")
    assert spec.capabilities is None
    assert spec.capability_state == "unknown"
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


def test_dispatch_unknown_target_still_fails_fast_no_retry(tmp_path: Path) -> None:
    # W2-T5: creation-time validation now rejects unknown targets with 400,
    # but a spec that slips past it (e.g. a stage-copied target) must still
    # fail fast at dispatch — permanent, never a capacity retry.
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
        detail = resp.json()["detail"]
        # Detail shape is API-version-dependent (plain string vs D12 dict).
        assert "conflicts" in (detail["message"] if isinstance(detail, dict) else detail)

        resp = client.post(
            "/api/v1/jobs",
            json={"workflow": "fake", "execution_mode": "local", "target_node": "compute-01"},
        )
        assert resp.status_code == 400

        # Non-conflicting request passes validation (fake workflow completes).
        resp = client.post("/api/v1/jobs", json={"workflow": "fake", "target_node": "local"})
        assert resp.status_code == 201


# --------------------------------------------------------------------- #
# W2-T5: creation-time target validation (design §3.1 D9/D12/D14)
# --------------------------------------------------------------------- #


@requires_remote_config
def test_validate_submission_target_unknown_node_code() -> None:
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[])
    spec = _confsearch_spec(target_node="ghost")
    with pytest.raises(ExecutionTargetError) as ei:
        validate_submission_target(spec, registry=reg)
    assert ei.value.code == "unknown_target_node"


@requires_remote_config
def test_validate_submission_target_disabled_node_code() -> None:
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[_cap_node("off", enabled=False)])
    spec = _confsearch_spec(target_node="off")
    with pytest.raises(ExecutionTargetError) as ei:
        validate_submission_target(spec, registry=reg)
    assert ei.value.code == "target_node_disabled"


@requires_remote_config
def test_validate_submission_target_incapable_node_code_and_missing() -> None:
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[_cap_node("xtb-only", software=("xtb",))])
    reg.status_provider = lambda name: _status(name, running=0)
    spec = _confsearch_spec(target_node="xtb-only")  # requires xtb + crest
    with pytest.raises(ExecutionTargetError) as ei:
        validate_submission_target(spec, registry=reg)
    assert ei.value.code == "target_node_incapable"
    assert ei.value.missing_software == ("crest",)


@requires_remote_config
def test_validate_submission_target_local_node_and_mode_never_checked() -> None:
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[])
    # local target / local mode are never capability-checked (§3.1 note).
    validate_submission_target(_confsearch_spec(target_node="local"), registry=reg)
    validate_submission_target(_confsearch_spec(execution_mode="local"), registry=reg)


@requires_remote_config
def test_validate_submission_target_auto_no_capable_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("acp.scheduler.capabilities.local_satisfies", lambda required: False)
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[_cap_node("xtb-only", software=("xtb",))])
    reg.status_provider = lambda name: _status(name, running=0)
    from acp.scheduler.capabilities import NoCapableNodeError

    with pytest.raises(NoCapableNodeError) as ei:
        validate_submission_target(_confsearch_spec(), registry=reg)
    assert ei.value.code == "no_capable_node"
    assert ei.value.missing_software == ("crest",)


@requires_remote_config
def test_validate_submission_target_auto_local_satisfies_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("acp.scheduler.capabilities.local_satisfies", lambda required: True)
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[_cap_node("xtb-only", software=("xtb",))])
    reg.status_provider = lambda name: _status(name, running=0)
    validate_submission_target(_confsearch_spec(), registry=reg)


@requires_remote_config
def test_validate_submission_target_auto_capacity_is_not_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A matching node at full load must NOT block creation — dispatch handles
    # capacity with ExecutionCapacityUnavailable retries (D11).
    monkeypatch.setattr("acp.scheduler.capabilities.local_satisfies", lambda required: False)
    reg = NodeRegistry(
        local_max_jobs=1, remote_nodes=[_cap_node("full", software=("xtb", "crest"))]
    )
    reg.status_provider = lambda name: _status(name, running=2, max_jobs=2)
    validate_submission_target(_confsearch_spec(), registry=reg)


@requires_remote_config
def test_validate_submission_target_auto_zero_remote_nodes_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # m8: zero enabled remotes + software the local machine lacks is a
    # permanent condition — fail fast at creation (HTTP 400) instead of
    # letting the job spin STARTING on the dispatch capacity-retry loop.
    monkeypatch.setattr("acp.scheduler.capabilities.local_satisfies", lambda required: False)
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[])
    from acp.scheduler.capabilities import NoCapableNodeError

    with pytest.raises(NoCapableNodeError) as ei:
        validate_submission_target(_confsearch_spec(), registry=reg)
    assert set(ei.value.missing_software) == {"xtb", "crest"}


@requires_remote_config
def test_validate_submission_target_auto_zero_remote_local_satisfies_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Local execution is a real auto outcome — zero remotes stays creatable.
    monkeypatch.setattr("acp.scheduler.capabilities.local_satisfies", lambda required: True)
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[])
    validate_submission_target(_confsearch_spec(), registry=reg)


@requires_remote_config
def test_validate_submission_target_explicit_remote_no_capable_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acp.scheduler.capabilities import NoCapableNodeError

    # Local satisfaction is irrelevant for an explicit remote choice —
    # the job is going remote regardless (M2b).
    monkeypatch.setattr("acp.scheduler.capabilities.local_satisfies", lambda required: True)
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[_cap_node("xtb-only", software=("xtb",))])
    reg.status_provider = lambda name: _status(name, running=0)
    with pytest.raises(NoCapableNodeError) as ei:
        validate_submission_target(_confsearch_spec(execution_mode="remote"), registry=reg)
    assert ei.value.missing_software == ("crest",)


@requires_remote_config
def test_validate_submission_target_explicit_remote_capable_node_passes() -> None:
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[_cap_node("ok", software=("xtb", "crest"))])
    reg.status_provider = lambda name: _status(name, running=0)
    validate_submission_target(_confsearch_spec(execution_mode="remote"), registry=reg)


@requires_remote_config
def test_validate_submission_target_explicit_remote_zero_nodes_rejected() -> None:
    from acp.scheduler.capabilities import NoCapableNodeError

    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[])
    with pytest.raises(NoCapableNodeError):
        validate_submission_target(_confsearch_spec(execution_mode="remote"), registry=reg)


# --------------------------------------------------------------------- #
# W2-T5: API-level creation-time validation → HTTP 400 with codes
# --------------------------------------------------------------------- #


def _app_client(tmp_path: Path):
    pytest.importorskip("paramiko")  # v1_routes imports remote fetcher at module level
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path))


def test_api_create_job_unknown_target_node_400(tmp_path: Path) -> None:
    with _app_client(tmp_path) as client:
        resp = client.post(
            "/api/v1/jobs",
            json={"workflow": "fake", "target_node": "ghost", "name": "n"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["code"] == "unknown_target_node"


@requires_remote_config
def test_api_create_job_target_node_incapable_400(tmp_path: Path) -> None:
    with _app_client(tmp_path) as client:
        manager = client.app.state.job_manager
        reg = NodeRegistry(
            local_max_jobs=1,
            remote_nodes=[_cap_node("xtb-only", software=("xtb",))],
        )
        reg.status_provider = lambda name: _status(name, running=0)
        manager.registry = reg
        resp = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "Confsearch",
                "method": {"protocol": "xtb-crest"},
                "target_node": "xtb-only",
                "name": "n",
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["code"] == "target_node_incapable"
        assert body["detail"]["missing_software"] == ["crest"]


@requires_remote_config
def test_api_create_job_auto_no_capable_400(tmp_path: Path) -> None:
    with _app_client(tmp_path) as client:
        manager = client.app.state.job_manager
        reg = NodeRegistry(
            local_max_jobs=1,
            remote_nodes=[_cap_node("xtb-only", software=("xtb",))],
        )
        reg.status_provider = lambda name: _status(name, running=0)
        manager.registry = reg
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr("acp.scheduler.capabilities.local_satisfies", lambda required: False)
        try:
            resp = client.post(
                "/api/v1/jobs",
                json={
                    "workflow": "Confsearch",
                    "method": {"protocol": "xtb-crest"},
                    "name": "n",
                },
            )
        finally:
            monkeypatch.undo()
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["code"] == "no_capable_node"
        assert body["detail"]["missing_software"] == ["crest"]


@requires_remote_config
def test_api_create_job_capable_target_full_node_201(tmp_path: Path) -> None:
    # Capacity (node full) must NOT block creation: helper only rejects empty
    # match sets; dispatch converts full nodes to a capacity retry (STARTING).
    with _app_client(tmp_path) as client:
        manager = client.app.state.job_manager
        reg = NodeRegistry(
            local_max_jobs=1,
            remote_nodes=[_cap_node("full", software=("xtb", "crest"))],
        )
        reg.status_provider = lambda name: _status(name, running=2, max_jobs=2)
        manager.registry = reg
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr("acp.scheduler.capabilities.local_satisfies", lambda required: False)
        try:
            resp = client.post(
                "/api/v1/jobs",
                json={
                    "workflow": "Confsearch",
                    "method": {"protocol": "xtb-crest"},
                    "target_node": "full",
                    "name": "n",
                },
            )
        finally:
            monkeypatch.undo()
        assert resp.status_code == 201


# --------------------------------------------------------------------- #
# W2-T4: capability-filtered select_remote (D8/D13) + affinity
# --------------------------------------------------------------------- #


@requires_remote_config
def test_select_remote_excludes_declared_node_missing_software() -> None:
    reg = NodeRegistry(
        local_max_jobs=1,
        remote_nodes=[
            _cap_node("xtb-only", software=("xtb",)),
            _cap_node("full", software=("xtb", "orca")),
        ],
    )
    reg.status_provider = lambda name: _status(name, running=0)
    assert reg.select_remote(required=frozenset({"orca"})).name == "full"


@requires_remote_config
def test_select_remote_unknown_node_software_fallback_included() -> None:
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[_node("mystery")])
    reg.status_provider = lambda name: _status(name, running=0, software={})
    # Unknown capability state: software requirements fall back to generic (D8).
    assert reg.select_remote(required=frozenset({"orca"})).name == "mystery"


@requires_remote_config
def test_select_remote_probe_inferred_judged_by_probe_set() -> None:
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[_node("probed")])
    software = {"orca": {"resolved": True}}
    reg.status_provider = lambda name: _status(name, running=0, software=software)
    assert reg.select_remote(required=frozenset({"orca"})).name == "probed"
    with pytest.raises(Exception) as ei:  # NoCapableNodeError: probe set lacks crest
        reg.select_remote(required=frozenset({"crest"}))
    assert ei.value.missing_software == ("crest",)


@requires_remote_config
def test_select_remote_tag_insufficient_excluded() -> None:
    reg = NodeRegistry(
        local_max_jobs=1,
        remote_nodes=[_cap_node("plain", software=("orca",))],
    )
    reg.status_provider = lambda name: _status(name, running=0)
    with pytest.raises(Exception) as ei:  # NoCapableNodeError: no declared gpu tag
        reg.select_remote(required=frozenset({"orca"}), required_tags=frozenset({"gpu"}))
    assert ei.value.missing_tags == ("gpu",)

    reg2 = NodeRegistry(
        local_max_jobs=1,
        remote_nodes=[_cap_node("tagged", software=("orca",), tags=("gpu",))],
    )
    reg2.status_provider = lambda name: _status(name, running=0)
    picked = reg2.select_remote(required=frozenset({"orca"}), required_tags=frozenset({"gpu"}))
    assert picked.name == "tagged"


@requires_remote_config
def test_select_remote_undeclared_node_never_satisfies_tags() -> None:
    # D13: tags are not probeable — even a fully probed undeclared node fails.
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[_node("probed")])
    software = {"orca": {"resolved": True}}
    reg.status_provider = lambda name: _status(name, running=0, software=software)
    with pytest.raises(Exception) as ei:
        reg.select_remote(required_tags=frozenset({"gpu"}))
    assert ei.value.missing_tags == ("gpu",)


@requires_remote_config
def test_select_remote_match_empty_is_permanent_candidates_empty_is_temporary() -> None:
    match_but_offline = NodeRegistry(
        local_max_jobs=1,
        remote_nodes=[_cap_node("full", software=("orca",))],
    )
    match_but_offline.status_provider = lambda name: _status(name, running=0, status="offline")
    with pytest.raises(ExecutionCapacityUnavailable):
        match_but_offline.select_remote(required=frozenset({"orca"}))

    match_but_degraded = NodeRegistry(
        local_max_jobs=1,
        remote_nodes=[_cap_node("full", software=("orca",))],
    )
    match_but_degraded.status_provider = lambda name: _status(name, running=0, disk_usage_pct=95)
    with pytest.raises(ExecutionCapacityUnavailable):
        match_but_degraded.select_remote(required=frozenset({"orca"}))

    no_match = NodeRegistry(
        local_max_jobs=1,
        remote_nodes=[_cap_node("xtb-only", software=("xtb",))],
    )
    no_match.status_provider = lambda name: _status(name, running=0)
    with pytest.raises(Exception) as ei:  # NoCapableNodeError — permanent
        no_match.select_remote(required=frozenset({"orca", "crest"}))
    assert set(ei.value.missing_software) == {"orca", "crest"}


@requires_remote_config
def test_select_remote_affinity_preferred_over_emptier_node() -> None:
    reg = NodeRegistry(
        local_max_jobs=1,
        remote_nodes=[_node("n1", max_jobs=4), _node("n2", max_jobs=4)],
    )
    loads = {"n1": _status("n1", running=0), "n2": _status("n2", running=2)}
    reg.status_provider = lambda name: loads[name]
    assert reg.select_remote(affinity_node="n2").name == "n2"
    # Affinity to a node outside the candidate set (offline) falls through
    # to least-loaded.
    loads = {"n1": _status("n1", running=1), "n2": _status("n2", running=0, status="offline")}
    reg.status_provider = lambda name: loads[name]
    assert reg.select_remote(affinity_node="n2").name == "n1"


# --------------------------------------------------------------------- #
# W2-T4: require() capability hard check
# --------------------------------------------------------------------- #


@requires_remote_config
def test_require_rejects_incapable_target_with_code_and_missing() -> None:
    reg = NodeRegistry(
        local_max_jobs=1,
        remote_nodes=[_cap_node("xtb-only", software=("xtb",))],
    )
    reg.status_provider = lambda name: _status(name, running=0)
    with pytest.raises(ExecutionTargetError) as ei:
        reg.require("xtb-only", required=frozenset({"orca"}), required_tags=frozenset({"gpu"}))
    assert ei.value.code == "target_node_incapable"
    assert ei.value.missing_software == ("orca",)
    assert ei.value.missing_tags == ("gpu",)


@requires_remote_config
def test_require_capable_and_local_pass() -> None:
    reg = NodeRegistry(
        local_max_jobs=1,
        remote_nodes=[_cap_node("full", software=("xtb", "crest"), tags=("gpu",))],
    )
    reg.status_provider = lambda name: _status(name, running=0)
    assert (
        reg.require("full", required=frozenset({"crest"}), required_tags=frozenset({"gpu"})).name
        == "full"
    )
    # Local is never capability-checked (design §3.1 ③).
    assert reg.require("local", required=frozenset({"orca"})).name == "local"


# --------------------------------------------------------------------- #
# W2-T4: in-flight reservations (G2) — soft cap, lock-free registry
# --------------------------------------------------------------------- #


def test_reservations_counted_against_max() -> None:
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[_node("n1", max_jobs=1)])
    reg.status_provider = lambda name: _status(name, running=0)
    first = reg.select_remote()
    reg.reserve(first.name, "job-a")
    # The reservation covers the select→bsub window: a concurrent second
    # select on the same node must see it at capacity.
    with pytest.raises(ExecutionCapacityUnavailable):
        reg.select_remote()


def test_reservations_shift_least_loaded_choice() -> None:
    reg = NodeRegistry(
        local_max_jobs=1,
        remote_nodes=[_node("n1", max_jobs=4), _node("n2", max_jobs=4)],
    )
    reg.status_provider = lambda name: _status(name, running=0)
    a = reg.select_remote()
    reg.reserve(a.name, "job-a")
    # n1 now counts one in-flight job (ratio 1/4) — n2 (0/4) wins.
    assert reg.select_remote().name == "n2"


def test_reservation_release_is_idempotent() -> None:
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[_node("n1")])
    reg.reserve("n1", "job-a")
    reg.release("n1", "job-a")
    reg.release("n1", "job-a")
    reg.release_job("job-a")
    assert reg.reservations == {}


def test_release_job_drops_reservation_on_whichever_node() -> None:
    reg = NodeRegistry(local_max_jobs=1, remote_nodes=[_node("n1"), _node("n2")])
    reg.reserve("n2", "job-a")
    reg.reserve("n2", "job-b")
    reg.release_job("job-a")
    assert reg.reservations == {"n2": {"job-b"}}


# --------------------------------------------------------------------- #
# W2-T4: D14 auto escalation in JobManager._resolve_execution_target
# --------------------------------------------------------------------- #


def _confsearch_spec(**kwargs) -> JobSpec:
    """Confsearch xtb-crest spec → derived requirements {xtb, crest}."""
    return JobSpec(workflow="Confsearch", method={"protocol": "xtb-crest"}, **kwargs)


def _idle_provider(registry: NodeRegistry) -> None:
    registry.status_provider = lambda name: _status(name, running=0)


@requires_remote_config
def test_auto_prefers_local_when_derived_satisfied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acp.scheduler.manager.local_satisfies", lambda required: True)
    cfg = RemoteExecutionConfig(execution_mode="local", nodes=[_real_node("compute-01")])
    mgr = JobManager(run_root=tmp_path, remote_config=cfg)
    try:
        rec = JobRecord(id="a1", spec=_confsearch_spec())
        assert mgr._resolve_execution_target(rec).name == "local"
        assert rec.result["required_software"] == ["crest", "xtb"]
        assert mgr.registry.reservations == {}
    finally:
        mgr.shutdown()


@requires_remote_config
def test_auto_empty_derived_follows_server_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acp.scheduler.manager.local_satisfies", lambda required: False)
    cfg = RemoteExecutionConfig(execution_mode="local", nodes=[_real_node("compute-01")])
    mgr = JobManager(run_root=tmp_path, remote_config=cfg)
    try:
        rec = JobRecord(id="a2", spec=JobSpec(workflow="fake"))
        assert mgr._resolve_execution_target(rec).kind == "local"
        assert "required_software" not in (rec.result or {})
    finally:
        mgr.shutdown()


@requires_remote_config
def test_auto_escalates_to_capability_matched_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acp.scheduler.manager.local_satisfies", lambda required: False)
    node = _real_node("compute-01")
    node.capabilities = NodeCapabilities(software=("xtb", "crest"), tags=("gpu",))
    cfg = RemoteExecutionConfig(execution_mode="local", nodes=[node])
    mgr = JobManager(run_root=tmp_path, remote_config=cfg)
    try:
        _idle_provider(mgr.registry)
        original = mgr.registry.select_remote
        calls: dict[str, object] = {}

        def spy(**kwargs):
            calls.update(kwargs)
            return original(**kwargs)

        mgr.registry.select_remote = spy  # type: ignore[assignment]
        rec = JobRecord(id="a3", spec=_confsearch_spec(node_tags=["gpu"]))
        target = mgr._resolve_execution_target(rec)
        assert target.name == "compute-01"
        assert calls["required"] == frozenset({"xtb", "crest"})
        assert calls["required_tags"] == frozenset({"gpu"})
        assert calls["affinity_node"] is None
        assert mgr.registry.reservations.get("compute-01") == {"a3"}
    finally:
        mgr.shutdown()


@requires_remote_config
def test_auto_no_capable_node_raises_no_capable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.scheduler.capabilities import NoCapableNodeError

    monkeypatch.setattr("acp.scheduler.manager.local_satisfies", lambda required: False)
    node = _real_node("orca-only")
    node.capabilities = NodeCapabilities(software=("orca",))
    cfg = RemoteExecutionConfig(execution_mode="local", nodes=[node])
    mgr = JobManager(run_root=tmp_path, remote_config=cfg)
    try:
        _idle_provider(mgr.registry)
        rec = JobRecord(id="a4", spec=_confsearch_spec())
        with pytest.raises(NoCapableNodeError) as ei:
            mgr._resolve_execution_target(rec)
        assert set(ei.value.missing_software) == {"xtb", "crest"}
        assert mgr.registry.reservations == {}
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# W2-T4: submission wiring — audit field, reservation release paths
# --------------------------------------------------------------------- #


class _FakeRemoteRunner:
    def __init__(
        self,
        lsf_id: str = "424242",
        error: Exception | None = None,
        lsf_status: str = "running",
    ) -> None:
        self.lsf_id = lsf_id
        self.error = error
        self.lsf_status = lsf_status

    def submit_remote(self, record, event_log, target_node=None) -> str:
        if self.error is not None:
            raise self.error
        return self.lsf_id

    def poll_remote(self, record, event_log, cancel_event):
        # Mirrors RemoteJobRunner.poll_remote: the record flips to RUNNING
        # exactly when bjobs reports the LSF RUN state.
        if self.lsf_status == "running" and record.status in (
            JobStatus.PENDING,
            JobStatus.PAUSED,
        ):
            record.status = JobStatus.RUNNING
        return (False, None)


def _manager_for_remote_submit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    software: tuple[str, ...] = ("xtb", "crest"),
) -> JobManager:
    monkeypatch.setattr("acp.scheduler.manager.local_satisfies", lambda required: False)
    node = _real_node("compute-01")
    node.capabilities = NodeCapabilities(software=software)
    cfg = RemoteExecutionConfig(execution_mode="local", nodes=[node])
    mgr = JobManager(run_root=tmp_path, remote_config=cfg)
    _idle_provider(mgr.registry)
    return mgr


def _seed_queued(mgr: JobManager, tmp_path: Path, job_id: str, spec: JobSpec) -> None:
    work_dir = tmp_path / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    mgr.store.create(
        JobRecord(id=job_id, spec=spec, status=JobStatus.QUEUED, work_dir=str(work_dir))
    )


@requires_remote_config
def test_submit_job_persists_required_software_and_holds_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = _manager_for_remote_submit(tmp_path, monkeypatch)
    try:
        mgr.remote_runner = _FakeRemoteRunner()  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "sub1", _confsearch_spec())
        assert mgr._submit_job("sub1") is True
        stored = mgr.store.get("sub1")
        assert stored.status == JobStatus.PENDING
        assert stored.result["required_software"] == ["crest", "xtb"]
        assert stored.result["execution_target"] == "compute-01"
        assert mgr.registry.reservations.get("compute-01") == {"sub1"}
    finally:
        mgr.shutdown()


@requires_remote_config
def test_submit_failure_releases_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = _manager_for_remote_submit(tmp_path, monkeypatch)
    try:
        mgr.remote_runner = _FakeRemoteRunner(error=RuntimeError("ssh down"))  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "sub2", _confsearch_spec())
        with pytest.raises(RuntimeError, match="ssh down"):
            mgr._submit_job("sub2")
        assert mgr.registry.reservations == {}
    finally:
        mgr.shutdown()


@requires_remote_config
def test_poll_releases_reservation_once_lsf_run_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = _manager_for_remote_submit(tmp_path, monkeypatch)
    try:
        mgr.remote_runner = _FakeRemoteRunner(lsf_status="running")  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "sub3", _confsearch_spec())
        mgr._submit_job("sub3")
        assert mgr.registry.reservations.get("compute-01") == {"sub3"}
        # LSF reports RUN: the node's running-jobs probe now counts this
        # job — the select-window reservation is redundant.
        mgr._poll_job("sub3")
        assert "compute-01" not in mgr.registry.reservations
        assert mgr.store.get("sub3").status == JobStatus.RUNNING
    finally:
        mgr.shutdown()


@requires_remote_config
def test_poll_keeps_reservation_while_lsf_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = _manager_for_remote_submit(tmp_path, monkeypatch)
    try:
        mgr.remote_runner = _FakeRemoteRunner(lsf_status="pending")  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "pend-1", _confsearch_spec())
        mgr._submit_job("pend-1")
        assert mgr.registry.reservations.get("compute-01") == {"pend-1"}
        # PEND does not count toward the node's running-jobs probe — an
        # early release would under-count in-flight work (m1).
        mgr._poll_job("pend-1")
        assert mgr.registry.reservations.get("compute-01") == {"pend-1"}
        assert mgr.store.get("pend-1").status == JobStatus.PENDING
    finally:
        mgr.shutdown()


@requires_remote_config
def test_dispatch_no_capable_node_degrades_to_capacity_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = _manager_for_remote_submit(tmp_path, monkeypatch, software=("orca",))
    try:
        _seed_queued(mgr, tmp_path, "nc1", _confsearch_spec())
        cancel = threading.Event()
        cancel.set()
        mgr._cancel_events["nc1"] = cancel
        mgr._execute_submission_impl("nc1")
        stored = mgr.store.get("nc1")
        assert stored.status == JobStatus.CANCELLED
        events = mgr.event_log("nc1").read_all()
        assert any(e["type"] == "execution.no_capable_node" for e in events)
        assert not any(e["type"] == "job.failed" for e in events)
        assert mgr.registry.reservations == {}
    finally:
        mgr.shutdown()


@requires_remote_config
def test_dispatch_config_drift_after_creation_degrades_to_capacity_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # T5 dispatch backstop: a spec whose target passes creation-time
    # validation must not permanently fail when the node config drifts
    # (capability removed) before dispatch — it degrades to capacity
    # semantics (no_capable_node event, retry), never job.failed.
    mgr = _manager_for_remote_submit(tmp_path, monkeypatch, software=("xtb", "crest"))
    try:
        validate_submission_target(_confsearch_spec(), registry=mgr.registry)

        # Config drift between creation and dispatch: the node loses its
        # capability. NodeSpec is frozen, so swap in a drifted registry.
        drifted = NodeRegistry(
            local_max_jobs=1,
            remote_nodes=[_cap_node("compute-01", software=("orca",))],
        )
        drifted.status_provider = lambda name: _status(name, running=0)
        mgr.registry = drifted

        _seed_queued(mgr, tmp_path, "drift1", _confsearch_spec())
        cancel = threading.Event()
        cancel.set()
        mgr._cancel_events["drift1"] = cancel
        mgr._execute_submission_impl("drift1")
        stored = mgr.store.get("drift1")
        assert stored.status == JobStatus.CANCELLED
        events = mgr.event_log("drift1").read_all()
        assert any(e["type"] == "execution.no_capable_node" for e in events)
        assert not any(e["type"] == "job.failed" for e in events)
        assert mgr.registry.reservations == {}
    finally:
        mgr.shutdown()


@requires_remote_config
def test_startup_rebuilds_reservations_from_persisted_targets(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "acp_jobs.db")
    work_dir = tmp_path / "running-remote"
    work_dir.mkdir(parents=True, exist_ok=True)
    store.create(
        JobRecord(
            id="res-1",
            spec=JobSpec(workflow="Confsearch"),
            status=JobStatus.RUNNING,
            work_dir=str(work_dir),
            remote_job_id="111",
            result={"execution_target": "compute-01", "execution_kind": "remote"},
        )
    )
    cfg = RemoteExecutionConfig(execution_mode="local", nodes=[_real_node("compute-01")])
    with patch.object(JobManager, "_try_recover_remote_job", return_value=True):
        mgr = JobManager(run_root=tmp_path, store=store, remote_config=cfg)
    try:
        assert mgr.registry.reservations.get("compute-01") == {"res-1"}
    finally:
        mgr.shutdown()


# --------------------------------------------------------------------- #
# Post-review batch ① (M1/M2/m2/m3): lock-external status prefetch,
# explicit-remote capability parity, reservation coverage for pinned
# dispatches, and null node_tags tolerance.
# --------------------------------------------------------------------- #


class _InstrumentedLock:
    """Context-manager wrapper recording whether the wrapped lock is held."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.held = False

    def __enter__(self) -> None:
        self._inner.acquire()
        self.held = True

    def __exit__(self, *exc: object) -> None:
        self.held = False
        self._inner.release()


@requires_remote_config
def test_auto_selection_probes_node_status_outside_manager_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acp.scheduler.manager.local_satisfies", lambda required: False)
    node = _real_node("compute-01")
    node.capabilities = NodeCapabilities(software=("xtb", "crest"))
    cfg = RemoteExecutionConfig(execution_mode="local", nodes=[node])
    mgr = JobManager(run_root=tmp_path, remote_config=cfg)
    try:
        lock_probe = _InstrumentedLock(mgr._lock)
        mgr._lock = lock_probe  # type: ignore[assignment]
        cache: dict[str, object] = {}
        misses: list[bool] = []  # lock-held flag per cache-miss probe

        def caching_provider(name: str):
            # Models NodeManager.get_node_status: one network probe per
            # node per TTL window; repeat calls are cache hits.
            if name not in cache:
                misses.append(lock_probe.held)
                cache[name] = _status(name, running=0)
            return cache[name]

        mgr.registry.status_provider = caching_provider
        rec = JobRecord(id="lock-1", spec=_confsearch_spec())
        assert mgr._resolve_execution_target(rec).name == "compute-01"
        assert misses  # the node status was actually probed
        assert not any(misses)  # …and never while holding the manager lock
    finally:
        mgr.shutdown()


@requires_remote_config
def test_explicit_remote_mode_filters_by_derived_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.scheduler.capabilities import NoCapableNodeError

    mgr = _manager_for_remote_submit(tmp_path, monkeypatch, software=("orca",))
    try:
        rec = JobRecord(id="rem-f1", spec=_confsearch_spec(execution_mode="remote"))
        with pytest.raises(NoCapableNodeError):
            mgr._resolve_execution_target(rec)
        assert mgr.registry.reservations == {}
    finally:
        mgr.shutdown()


@requires_remote_config
def test_explicit_remote_mode_filters_by_node_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.scheduler.capabilities import NoCapableNodeError

    mgr = _manager_for_remote_submit(tmp_path, monkeypatch)  # declares no tags
    try:
        rec = JobRecord(
            id="rem-f2", spec=_confsearch_spec(execution_mode="remote", node_tags=["gpu"])
        )
        with pytest.raises(NoCapableNodeError) as ei:
            mgr._resolve_execution_target(rec)
        assert ei.value.missing_tags == ("gpu",)
    finally:
        mgr.shutdown()


@requires_remote_config
def test_explicit_target_dispatch_holds_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = _manager_for_remote_submit(tmp_path, monkeypatch)
    try:
        mgr.remote_runner = _FakeRemoteRunner()  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "pin-1", _confsearch_spec(target_node="compute-01"))
        assert mgr._submit_job("pin-1") is True
        assert mgr.registry.reservations.get("compute-01") == {"pin-1"}
    finally:
        mgr.shutdown()


@requires_remote_config
def test_explicit_remote_mode_dispatch_holds_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = _manager_for_remote_submit(tmp_path, monkeypatch)
    try:
        mgr.remote_runner = _FakeRemoteRunner()  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "rem-1", _confsearch_spec(execution_mode="remote"))
        assert mgr._submit_job("rem-1") is True
        assert mgr.registry.reservations.get("compute-01") == {"rem-1"}
    finally:
        mgr.shutdown()


@requires_remote_config
def test_concurrent_explicit_target_dispatches_respect_max_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("acp.scheduler.manager.local_satisfies", lambda required: False)
    node = _real_node("compute-01", max_jobs=1)
    node.capabilities = NodeCapabilities(software=("xtb", "crest"))
    cfg = RemoteExecutionConfig(execution_mode="local", nodes=[node])
    mgr = JobManager(run_root=tmp_path, remote_config=cfg)
    try:
        _idle_provider(mgr.registry)
        mgr.remote_runner = _FakeRemoteRunner()  # type: ignore[assignment]
        _seed_queued(mgr, tmp_path, "pin-a", _confsearch_spec(target_node="compute-01"))
        _seed_queued(mgr, tmp_path, "pin-b", _confsearch_spec(target_node="compute-01"))
        assert mgr._submit_job("pin-a") is True
        assert mgr.registry.reservations.get("compute-01") == {"pin-a"}
        # max_jobs=1 and pin-a's LSF job is not running yet: its in-flight
        # reservation must block the second explicit dispatch (soft cap).
        with pytest.raises(ExecutionCapacityUnavailable):
            mgr._submit_job("pin-b")
        assert mgr.registry.reservations.get("compute-01") == {"pin-a"}
    finally:
        mgr.shutdown()


@requires_remote_config
def test_null_node_tags_row_dispatches_without_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = _manager_for_remote_submit(tmp_path, monkeypatch)
    try:
        # A persisted spec_json with "node_tags": null deserializes to
        # node_tags=None (the store's .get default only covers a missing
        # key) — dispatch must not crash on frozenset(None).
        _seed_queued(mgr, tmp_path, "null-tags", _confsearch_spec(node_tags=None))
        reloaded = mgr.store.get("null-tags")
        assert reloaded is not None and reloaded.spec.node_tags is None
        assert mgr._resolve_execution_target(reloaded).name == "compute-01"

        pinned = JobRecord(
            id="null-tags-2",
            spec=_confsearch_spec(target_node="compute-01", node_tags=None),
        )
        assert mgr._resolve_execution_target(pinned).name == "compute-01"
    finally:
        mgr.shutdown()
