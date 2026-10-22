"""B09 — local identity and single resource admission at submit and dispatch.

The Controller process is the authority: a request body, a tool argument or an
MCP ``_meta`` field can only *hint* a project and can never widen the verified
scope.  The one admission ledger reserves a queued slot before any work
directory exists, re-validates immediately before dispatch, releases exactly
once and records trusted usage once per ``(job, attempt, submission)``.

Run with: pytest tests/test_acp2_runtime_admission.py -q
"""

from __future__ import annotations

import inspect
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp_services.entrypoints import mcp
from acp_services.runtime.admission import AdmissionRequest
from acp_services.runtime.idempotency import resolve_scope_id
from acp_services.runtime.local_admission import AdmissionLimits, build_local_admission

PROJECT = "proj-a"
OTHER_PROJECT = "proj-b"


@contextmanager
def _client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    **env: str,
) -> Iterator[TestClient]:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as client:
        yield client


def _side_effects(client: TestClient, run_root: Path) -> dict[str, int]:
    manager = client.app.state.job_manager
    admission = client.app.state.admission
    return {
        "jobs": len(manager.store.list(limit=10000)),
        "work_dirs": len(list(run_root.rglob("job.json"))),
        "reservations": admission.ledger.count(),
        "active_reservations": admission.ledger.count(active_only=True),
        "usage_receipts": admission.controller.usage_totals()["receipts"],
    }


def _no_qc(monkeypatch: pytest.MonkeyPatch, manager: JobManager) -> list[int]:
    spawns: list[int] = []

    def _submit(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        spawns.append(1)

    monkeypatch.setattr(manager.runner, "submit", _submit)
    return spawns


def _submit_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "workflow": "fake",
        "input": {"source": "C"},
        "project_id": PROJECT,
    }
    body.update(overrides)
    return body


def _seed_job(
    client: TestClient,
    *,
    project_id: str,
    status: JobStatus = JobStatus.COMPLETED,
) -> str:
    manager = client.app.state.job_manager
    job_id = "seed-" + uuid.uuid4().hex[:10]
    work_dir = Path(client.app.state.run_root) / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    manager.store.create(
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="optimize", name=job_id, project_id=project_id),
            status=status,
            work_dir=str(work_dir),
            project_id=project_id,
        )
    )
    return job_id


# ---------------------------------------------------------------------------
# Capabilities: honest enforcement labels
# ---------------------------------------------------------------------------


def test_capabilities_expose_enforced_and_not_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(
        tmp_path,
        monkeypatch,
        ACP_SERVER_PROJECT=PROJECT,
        ACP_ADMISSION_MAX_CORES="8",
        ACP_ADMISSION_MAX_TASKS="16",
    ) as client:
        payload = client.get("/api/capabilities").json()
        assert payload["admission_enforcement"] == "local-single-enforced"
        assert payload["model_token_enforcement"] == "NOT_VERIFIED"
        assert payload["multi_tenant_security"] is False
        admission = payload["admission"]
        assert admission["mode"] == "quota-only"
        assert admission["quota_enforced"] is True
        assert admission["recorded_only"] is False
        assert admission["limits"]["max_cores"] == 8
        assert admission["limits"]["max_tasks"] == 16
        assert admission["verified_project"] == PROJECT
        assert payload["identity"]["principal_id"] == "principal_local"
        assert payload["identity"]["project_hint_policy"] == "narrow-only"


# ---------------------------------------------------------------------------
# Per-dimension denial before any work directory / QC / bsub
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_name", "env_value", "resources", "reason"),
    [
        ("ACP_ADMISSION_MAX_CORES", "2", {"nproc": 4}, "exceeds_cores"),
        ("ACP_ADMISSION_MAX_MEMORY_MB", "1024", {"mem": "4GB"}, "exceeds_memory"),
        ("ACP_ADMISSION_MAX_WALLTIME_S", "60", {"walltime_s": 120}, "exceeds_duration"),
        ("ACP_ADMISSION_MAX_STORAGE_BYTES", "1000", {"storage_bytes": 2000}, "exceeds_storage"),
    ],
)
def test_over_limit_submit_denied_before_work_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str,
    resources: dict[str, Any],
    reason: str,
) -> None:
    with _client(tmp_path, monkeypatch, **{env_name: env_value}) as client:
        manager = client.app.state.job_manager
        spawns = _no_qc(monkeypatch, manager)
        remote_calls: list[int] = []
        if manager.remote_runner is not None:
            monkeypatch.setattr(
                manager.remote_runner,
                "submit_remote",
                lambda *args, **kwargs: remote_calls.append(1),
            )
        before = _side_effects(client, tmp_path)
        response = client.post("/api/v1/jobs", json=_submit_body(resources=resources))
        assert response.status_code == 429, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "admission_denied"
        assert detail["params"]["reason_code"] == reason
        assert detail["params"]["mode"] == "quota-only"
        assert _side_effects(client, tmp_path) == before
        assert spawns == []
        assert remote_calls == []


# ---------------------------------------------------------------------------
# Claim-then-deny: rejected claim, deterministic same-key retry
# ---------------------------------------------------------------------------


def test_claim_then_deny_marks_rejected_and_retries_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch, ACP_ADMISSION_MAX_CORES="2") as client:
        manager = client.app.state.job_manager
        spawns = _no_qc(monkeypatch, manager)
        before = _side_effects(client, tmp_path)
        headers = {"Idempotency-Key": "b09-over-cores"}
        body = _submit_body(resources={"nproc": 4})

        first = client.post("/api/v1/jobs", json=body, headers=headers)
        assert first.status_code == 429
        assert first.json()["detail"]["params"]["reason_code"] == "exceeds_cores"
        claim = manager.store.find_idempotency_claim(
            resolve_scope_id("principal_local", PROJECT), "submit", "b09-over-cores"
        )
        assert claim is not None and claim.state == "rejected"

        retry = client.post("/api/v1/jobs", json=body, headers=headers)
        assert retry.status_code == 409
        assert retry.json()["detail"]["code"] == "idempotency_conflict"
        assert "rejected by admission" in retry.json()["detail"]["message"]
        assert _side_effects(client, tmp_path) == before
        assert spawns == []


# ---------------------------------------------------------------------------
# Replay: one reservation, one dispatch, one usage receipt
# ---------------------------------------------------------------------------


def test_replay_does_not_double_reserve_or_charge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch) as client:
        manager = client.app.state.job_manager
        headers = {"Idempotency-Key": "b09-replay"}
        first = client.post("/api/v1/jobs", json=_submit_body(), headers=headers)
        assert first.status_code == 201, first.text
        job_id = first.json()["job_id"]
        assert first.json()["admission"]["enforcement"] == "local-single-enforced"

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            record = manager.store.get(job_id)
            if record is not None and record.status.is_terminal:
                break
            time.sleep(0.05)
        assert record is not None and record.status == JobStatus.COMPLETED
        assert client.app.state.admission.ledger.count() == 1
        assert client.app.state.admission.ledger.count(active_only=True) == 0
        assert client.app.state.admission.controller.usage_totals()["receipts"] == 1

        replay = client.post("/api/v1/jobs", json=_submit_body(), headers=headers)
        assert replay.status_code == 201
        assert replay.json()["job_id"] == job_id
        assert replay.json()["idempotent_replay"] is True
        assert client.app.state.admission.ledger.count() == 1
        assert client.app.state.admission.controller.usage_totals()["receipts"] == 1


# ---------------------------------------------------------------------------
# Over-jobs (active task ceiling)
# ---------------------------------------------------------------------------


def test_over_jobs_denied_before_work_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _client(tmp_path, monkeypatch, ACP_ADMISSION_MAX_TASKS="1") as client:
        admission = client.app.state.admission
        blocker = admission.controller.admit(
            AdmissionRequest(
                principal_id="principal_local",
                project_id=PROJECT,
                job_id="blocker",
                cores=1,
            ),
            queued=True,
            reservation_id="res_blocker",
        )
        assert blocker.allowed
        manager = client.app.state.job_manager
        spawns = _no_qc(monkeypatch, manager)
        before = _side_effects(client, tmp_path)

        response = client.post("/api/v1/jobs", json=_submit_body())
        assert response.status_code == 429, response.text
        assert response.json()["detail"]["params"]["reason_code"] == "exceeds_concurrency"
        assert _side_effects(client, tmp_path) == before
        assert spawns == []
        admission.release_reservation_id("res_blocker")


# ---------------------------------------------------------------------------
# Server identity: cross-project denial, hint cannot widen
# ---------------------------------------------------------------------------


def test_cross_project_control_prepare_and_read_are_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch, ACP_SERVER_PROJECT=PROJECT) as client:
        foreign = _seed_job(client, project_id=OTHER_PROJECT)
        unknown = "does-not-exist-" + uuid.uuid4().hex[:8]
        before = _side_effects(client, tmp_path)

        for job_id in (foreign, unknown):
            response = client.get(f"/api/v1/jobs/{job_id}/prepared/catalog")
            assert response.status_code == 404
            assert response.json()["detail"] == f"Job not found: {job_id}"

        prepare = client.post(
            f"/api/v1/jobs/{foreign}/prepare",
            json={"purpose": "structure-input", "job_id": foreign},
        )
        assert prepare.status_code == 404
        assert prepare.json()["detail"]["code"] == "resource_not_ready"

        cancel = client.post(f"/api/v1/jobs/{foreign}/cancel")
        assert cancel.status_code == 404
        assert cancel.json()["detail"] == f"Job not found: {foreign}"
        assert _side_effects(client, tmp_path) == before


def test_project_hint_cannot_widen_verified_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch, ACP_SERVER_PROJECT=PROJECT) as client:
        denied = client.post(
            "/api/v1/jobs",
            json=_submit_body(project_id=OTHER_PROJECT),
            headers={"Idempotency-Key": "b09-widen"},
        )
        assert denied.status_code == 403
        assert denied.json()["detail"]["code"] == "access_denied"
        assert client.app.state.job_manager.store.list(limit=10) == []

        allowed = client.post(
            "/api/v1/jobs",
            json=_submit_body(project_id=PROJECT),
            headers={"Idempotency-Key": "b09-narrow"},
        )
        assert allowed.status_code == 201, allowed.text
        assert allowed.json()["project_id"] == PROJECT
        assert allowed.json()["identity"]["verified_project"] == PROJECT


# ---------------------------------------------------------------------------
# Dispatch-time revalidation and release-exactly-once
# ---------------------------------------------------------------------------


def _queued_job_with_reservation(
    tmp_path: Path,
    *,
    job_id: str,
    spec: JobSpec,
) -> tuple[Any, JobManager]:
    admission = build_local_admission(
        tmp_path / "admission.db",
        limits=AdmissionLimits(max_cores=4, max_memory_mb=8192, max_tasks=8),
        verified_project=PROJECT,
    )
    manager = JobManager(
        run_root=tmp_path / "run-root",
        max_running=1,
        idempotency_reservations=admission.reservation_port(),
        admission=admission,
    )
    decision = admission.admit_job(job_id, spec, project_id=PROJECT)
    assert decision.allowed
    work_dir = tmp_path / "run-root" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    manager.store.create(
        JobRecord(
            id=job_id,
            spec=spec,
            status=JobStatus.QUEUED,
            work_dir=str(work_dir),
            project_id=PROJECT,
            result={
                "admission": admission.result_for_reservation(
                    decision.reservation_id, decision=decision
                )
            },
        )
    )
    return admission, manager


def test_dispatch_revalidation_failure_is_non_dispatching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = JobSpec(workflow="fake", name="revalidate", input={"source": "C"}, project_id=PROJECT)
    admission, manager = _queued_job_with_reservation(tmp_path, job_id="revalidate", spec=spec)
    try:
        spawns = _no_qc(monkeypatch, manager)
        admission.capacity.set_capacity(cores=0, memory_mb=0, concurrency=0)
        manager._submit_job("revalidate")
        record = manager.store.get("revalidate")
        assert record is not None
        assert record.status == JobStatus.FAILED
        assert "admission_revalidation_failed" in (record.error or "")
        assert spawns == []
        reservation = admission.controller.reservation("res_job_revalidate")
        assert reservation is not None and reservation.state == "released"
        assert admission.release_job(record) is False
    finally:
        manager.shutdown()


def test_cancel_releases_and_records_usage_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del monkeypatch
    spec = JobSpec(workflow="fake", name="cancel-me", input={"source": "C"}, project_id=PROJECT)
    admission, manager = _queued_job_with_reservation(tmp_path, job_id="cancel-me", spec=spec)
    try:
        cancelled = manager.cancel("cancel-me")
        assert cancelled is not None and cancelled.status == JobStatus.CANCELLED
        reservation = admission.controller.reservation("res_job_cancel-me")
        assert reservation is not None and reservation.state == "released"
        assert admission.release_job(cancelled) is False
        assert admission.controller.usage_totals()["receipts"] == 1
        assert admission.record_usage(cancelled) is False
        assert admission.controller.usage_totals()["receipts"] == 1
    finally:
        manager.shutdown()


# ---------------------------------------------------------------------------
# MCP: the in-process budget is not on the final admission path
# ---------------------------------------------------------------------------


class _Capabilities:
    def capabilities(self) -> dict[str, Any]:
        return {"workflows": [], "backends": []}


class _Preflight:
    def preflight(self, request: Any, *, workspace_id: str) -> dict[str, Any]:
        del request, workspace_id
        return {}


class _Submit:
    def submit(self, spec: Any, *, idempotency_key: str, workspace_id: str) -> dict[str, Any]:
        del spec, idempotency_key, workspace_id
        return {"job_id": "job-mcp", "task_id": "job-mcp"}


class _Status:
    def get(self, reference: Any, *, workspace_id: str) -> None:
        del reference, workspace_id
        return None


class _Cancel:
    def cancel(self, reference: Any, *, workspace_id: str) -> dict[str, Any]:
        del reference, workspace_id
        return {}


class _Artifacts:
    def prepare(self, request: Any, *, idempotency_key: str, workspace_id: str) -> dict[str, Any]:
        del request, idempotency_key, workspace_id
        return {"operation_id": "op-mcp"}

    def read(self, request: Any, *, workspace_id: str) -> dict[str, Any]:
        del request, workspace_id
        return {}


class _Analysis:
    def analyze(self, request: Any, *, workspace_id: str) -> dict[str, Any]:
        del request, workspace_id
        return {}


def _mcp_backends() -> mcp.McpBackends:
    return mcp.McpBackends(
        capabilities=_Capabilities(),
        preflight=_Preflight(),
        submit=_Submit(),
        status=_Status(),
        cancel=_Cancel(),
        artifacts=_Artifacts(),
        analysis=_Analysis(),
    )


def _mcp_submit(server: mcp.McpServer, cores: int) -> dict[str, Any]:
    return server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": mcp.McpTool.SUBMIT,
                "arguments": {
                    "workflow": "singlepoint",
                    "input": {"source": "C"},
                    "resources": {"cores": cores},
                },
            },
        }
    )


def test_mcp_budget_is_not_in_the_final_admission_path() -> None:
    default = inspect.signature(mcp.McpServer.__init__).parameters["budget"].default
    assert default is None

    server = mcp.McpServer(backends=_mcp_backends())
    assert server._budget is None
    huge_hint = _mcp_submit(server, 999_999)
    assert huge_hint["result"]["isError"] is False
    assert huge_hint["result"]["structuredContent"]["status"] == "ok"

    capped = mcp.McpServer(
        backends=_mcp_backends(),
        budget=mcp.WorkspaceBudget(mcp.BudgetCaps(max_cores_per_submission=1)),
    )
    refused = _mcp_submit(capped, 999_999)
    assert refused["result"]["isError"] is True
