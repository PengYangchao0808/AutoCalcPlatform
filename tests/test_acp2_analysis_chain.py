"""M2-02 — artifact chain, science discovery and the real ``vibration.analyze``.

The suite runs the **real** application (uvicorn, real ``JobManager``/``JobStore``,
real prepare-operation service, real artifact store, real MCP adapter over the
real Controller HTTP commands) and pins the three things M2-02 changes:

1. **Discovery is one projection.** ``GET /api/capabilities`` forwards A's
   capability descriptors (no more ``capability_descriptors: None``) and the
   frozen ``registered/executable/unavailable`` analysis projection from the
   injected application registry; ``GET /api/v2/science/capabilities`` reads the
   same cached catalog, so the executable seam is probed exactly once.
2. **``vibration.analyze`` is executable, over prepared artifacts only.** The
   handler verifies the prepare operation + published body digest and delegates
   to A's pure-analysis entry point; HTTP and MCP return the same product digest.
3. **``vibration.compute`` is a compute capability.** It is registered but not
   executable on the analysis surface, and the analyze handler never creates a
   task, calls a submit path or starts a subprocess.

Science is the declared stub boundary of the acceptance slice: the job's
``RESULT/freq.out`` is the repository's real ORCA frequency fixture, and no QC
binary, LSF node or model is reached.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

REPO = Path(__file__).resolve().parents[1]
ORCA_FREQUENCY_FIXTURE = REPO / "tests" / "fixtures" / "orca_optfreq_real_sections.txt"
CONTRACT = REPO / "docs" / "reference" / "next-change" / "acp2-contracts" / "runtime"
FREQUENCY_BODY = ORCA_FREQUENCY_FIXTURE.read_bytes()
FREQUENCY_DIGEST = "sha256:" + hashlib.sha256(FREQUENCY_BODY).hexdigest()
PROJECT = "uncategorized"
JOB_ID = "20261027_120000_000_frequency"
ARTIFACT_ID = "freq_out"
ATTEMPT = 1
MEMORY_BOUND_EXCEEDED = 1 << 40


# ---------------------------------------------------------------------------
# Live application fixture (real Controller + real MCP adapter)
# ---------------------------------------------------------------------------


class _LiveApp:
    def __init__(self, base_url: str, app: Any, manager: Any) -> None:
        self.base_url = base_url
        self.app = app
        self.manager = manager
        self.http = httpx.Client(base_url=base_url, timeout=30.0)

    def close(self) -> None:
        self.http.close()


@pytest.fixture()
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[_LiveApp, None, None]:
    import acp.scheduler.capabilities as capabilities_module
    import acp.scheduler.manager as manager_module

    # The local node is assumed capable (never probed); no QC is reachable.
    monkeypatch.setattr(capabilities_module, "local_satisfies", lambda required: True)
    monkeypatch.setattr(manager_module, "local_satisfies", lambda required: True)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=2)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="m2-02-uvicorn")
    thread.start()
    try:
        deadline = time.monotonic() + 20.0
        while not server.started:
            if not thread.is_alive():
                pytest.fail("uvicorn thread exited before startup")
            if time.monotonic() > deadline:
                pytest.fail("uvicorn did not start within 20 s")
            time.sleep(0.05)
        port = int(server.servers[0].sockets[0].getsockname()[1])
        manager = app.state.job_manager
        # Controlled science stub: submitted tasks are persisted but never
        # executed (no QC subprocess, no LSF, no model).
        manager._start_submission_thread = lambda *args, **kwargs: None
        live_app = _LiveApp(f"http://127.0.0.1:{port}", app, manager)
        try:
            yield live_app
        finally:
            live_app.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=10)


def _client(live_app: _LiveApp) -> Any:
    from acp_services.entrypoints.controller_client import ControllerClient

    return ControllerClient(live_app.base_url)


def _mcp(live_app: _LiveApp) -> Any:
    from acp.services_mcp_bootstrap import build_mcp_runtime

    return build_mcp_runtime(live_app.base_url)


def _tool(runtime: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = runtime.server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None and "result" in response, response
    return response["result"]


def _tool_error(result: dict[str, Any]) -> dict[str, Any]:
    assert result["isError"] is True, result
    return result["structuredContent"]["error"]


def _submit(live_app: _LiveApp, *, key: str) -> dict[str, Any]:
    """Submit one task through the real Controller (science stubbed)."""
    runtime = _mcp(live_app)
    result = _tool(
        runtime,
        "acp_submit",
        {
            "workflow": "singlepoint",
            "input": {"source": "CCO", "source_type": "smiles"},
            "method": {},
            "resources": {"cores": 1},
            "idempotency_key": key,
        },
    )
    assert result["isError"] is False, result
    return result["structuredContent"]["data"]


def _seed_completed_frequency_job(live_app: _LiveApp, *, job_id: str = JOB_ID) -> Path:
    """Seed one completed job whose attempt produced the ORCA fixture artifact.

    This is the declared stub boundary: the *science output* is the repository's
    real ORCA frequency fixture, while the task record is created directly (the
    submission thread is disabled in this suite).
    """

    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    work_dir = Path(live_app.manager.run_root) / PROJECT / f"{job_id}-{ATTEMPT}"
    (work_dir / "RESULT").mkdir(parents=True, exist_ok=True)
    (work_dir / "RESULT" / "freq.out").write_bytes(FREQUENCY_BODY)
    live_app.manager.store.create(
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="frequency", name=job_id, project_id=PROJECT),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            attempt=ATTEMPT,
            project_id=PROJECT,
        )
    )
    return work_dir


def _wait_operation(live_app: _LiveApp, operation_id: str, *, timeout: float = 10.0) -> dict:
    client = _client(live_app)
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = dict(client.operation(operation_id))
        if body.get("state") in {"ready", "failed", "cancelled"}:
            return body
        time.sleep(0.01)
    raise AssertionError(f"operation did not settle: {body}")


def _prepare_frequency_artifact(live_app: _LiveApp, *, job_id: str = JOB_ID) -> dict[str, Any]:
    """Run the real prepare chain and return the ready operation projection."""

    prepared = dict(
        _client(live_app).prepare(
            {
                "schema_version": 1,
                "purpose": "artifact",
                "job_id": job_id,
                "artifact_id": ARTIFACT_ID,
                "attempt": ATTEMPT,
                "generation": 1,
                "relative_path": "RESULT/freq.out",
                "media_type": "text/plain",
            }
        )
    )
    operation = _wait_operation(live_app, str(prepared["operation_id"]))
    assert operation["state"] == "ready", operation
    assert operation["artifact_ref"]["digest"] == FREQUENCY_DIGEST
    return operation


def _artifact_ref(*, job_id: str = JOB_ID, digest: str = FREQUENCY_DIGEST) -> dict[str, Any]:
    return {
        "task_id": job_id,
        "attempt": ATTEMPT,
        "artifact_id": ARTIFACT_ID,
        "generation": 1,
        "digest": digest,
    }


def _analyze_http(live_app: _LiveApp, body: dict[str, Any], *, job_id: str = JOB_ID) -> Any:
    return live_app.http.post(f"/api/v1/jobs/{job_id}/analyze", json=body)


def _side_effects(live_app: _LiveApp) -> tuple[dict[str, int], int]:
    return live_app.manager.store.counts(), len(live_app.manager._prepare_operations.list())


# ---------------------------------------------------------------------------
# 1. Discovery: one catalog, one projection, registered != executable
# ---------------------------------------------------------------------------


def test_discovery_forwards_descriptors_and_the_frozen_analysis_states(
    live: _LiveApp,
) -> None:
    capabilities = live.http.get("/api/capabilities")
    assert capabilities.status_code == 200, capabilities.text
    body = capabilities.json()

    descriptors = body["capability_descriptors"]
    assert isinstance(descriptors, list) and descriptors, "capability_descriptors is no longer None"
    by_id = {entry["capability_id"]: entry for entry in descriptors}
    assert set(by_id) == {
        "electronic.energy",
        "geometry.optimize",
        "vibration.analyze",
        "vibration.compute",
    }
    # The descriptors are A's catalog objects forwarded verbatim, with their
    # execution semantics (compute vs pure analysis) intact.
    assert by_id["vibration.analyze"]["execution_semantics"]["kind"] == "pure_analysis"
    assert by_id["vibration.analyze"]["execution_semantics"]["starts_subprocess"] is False
    assert by_id["vibration.compute"]["execution_semantics"]["kind"] == "compute"
    assert by_id["vibration.compute"]["execution_semantics"]["delegates_to_task_kind"] == (
        "frequency"
    )

    states = {entry["analysis_id"]: entry for entry in body["analysis_capabilities"]}
    assert states["vibration.analyze"] == {
        "analysis_id": "vibration.analyze",
        "state": "executable",
        "registered": True,
        "executable": True,
        "version": "1.0.0",
        "owner": "A",
    }
    assert states["vibration.compute"]["state"] == "registered"
    assert states["vibration.compute"]["registered"] is True
    assert states["vibration.compute"]["executable"] is False
    assert {entry["state"] for entry in states.values()} <= {
        "registered",
        "executable",
        "unavailable",
    }

    # The MCP discovery tool forwards the same projection (no second registry).
    runtime = _mcp(live)
    mcp_capabilities = _tool(runtime, "acp_capabilities", {})
    assert mcp_capabilities["isError"] is False
    mcp_data = mcp_capabilities["structuredContent"]["data"]
    assert mcp_data["capability_descriptors"] == descriptors
    assert {
        entry["analysis_id"]: entry["state"] for entry in mcp_data["analysis_capabilities"]
    } == {
        "vibration.analyze": "executable",
        "vibration.compute": "registered",
    }


def test_both_discovery_surfaces_share_one_catalog_and_probe_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single executable probe serves `/api/capabilities` and the v2 read surface."""
    from fastapi.testclient import TestClient

    import cccp.calculation.capabilities as capability_module

    calls = {"builtin": 0, "domain": 0}
    real_builtin = capability_module.builtin_catalog
    real_domain = capability_module.domain_catalog

    def counting_builtin(*args: Any, **kwargs: Any) -> Any:
        calls["builtin"] += 1
        # Keep the document small/stable: probing the real seam is irrelevant to
        # the "one build" assertion and its cost is the point of the cache.
        document = real_builtin(*args, **kwargs)
        return document

    def counting_domain(*args: Any, **kwargs: Any) -> Any:
        calls["domain"] += 1
        return real_domain(*args, **kwargs)

    monkeypatch.setattr(capability_module, "builtin_catalog", counting_builtin)
    monkeypatch.setattr(capability_module, "domain_catalog", counting_domain)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as client:
        application = client.get("/api/capabilities").json()
        first = client.get("/api/v2/science/capabilities").json()
        second = client.get("/api/capabilities").json()
        third = client.get("/api/v2/science/capabilities").json()

    assert calls == {"builtin": 1, "domain": 1}, calls
    assert (
        application["capability_descriptors"]
        == first["capabilities"]["descriptors"]["capabilities"]
    )
    assert second["capability_descriptors"] == application["capability_descriptors"]
    assert third["capabilities"]["descriptors"] == first["capabilities"]["descriptors"]
    # Same analysis projection on both surfaces (one registry, one function).
    assert application["analysis_capabilities"] == first["capabilities"]["analysis"]
    assert second["analysis_capabilities"] == third["capabilities"]["analysis"]


def test_application_registry_is_injected_and_the_global_default_is_untouched(
    live: _LiveApp,
) -> None:
    from acp_services.runtime.analysis_capabilities import (
        DEFAULT_ANALYSIS_REGISTRY,
        AnalysisCapabilityRegistry,
        capability_projection,
    )

    registry = live.app.state.analysis_registry
    assert isinstance(registry, AnalysisCapabilityRegistry)
    assert registry is not DEFAULT_ANALYSIS_REGISTRY
    assert registry.ids() == ("vibration.analyze", "vibration.compute")
    assert DEFAULT_ANALYSIS_REGISTRY.ids() == ()
    assert {entry["state"] for entry in capability_projection(DEFAULT_ANALYSIS_REGISTRY)} == {
        "unavailable"
    }
    assert {entry["state"] for entry in capability_projection(registry)} == {
        "executable",
        "registered",
    }


def test_empty_registry_is_unavailable_and_never_a_positive(
    live: _LiveApp,
) -> None:
    """The empty/uninjected registry answers ``unavailable`` — it executes nothing."""
    from acp.api import science_catalog

    class _StateWithoutRegistry:
        pass

    projection = science_catalog.analysis_capabilities(_StateWithoutRegistry())
    assert {entry["state"] for entry in projection} == {"unavailable"}
    assert all(entry["executable"] is False for entry in projection)
    assert science_catalog.analysis_registry(_StateWithoutRegistry()) is None

    original = live.app.state.analysis_registry
    try:
        live.app.state.analysis_registry = None
        refused = _analyze_http(
            live,
            {"analysis_id": "vibration.analyze", "input_refs": [_artifact_ref()], "explicit": True},
        )
        assert refused.status_code == 503
        detail = refused.json()["detail"]
        assert detail["code"] == "backend_unavailable"
        assert detail["params"]["rule"] == "analysis_registry_unavailable"
    finally:
        live.app.state.analysis_registry = original


# ---------------------------------------------------------------------------
# 2. Full chain: submit → status → prepare → operation status → read → analyze
# ---------------------------------------------------------------------------


def test_submit_status_prepare_operation_read_then_analyze(live: _LiveApp) -> None:
    tasks_before = len(live.manager.store.list(limit=1000))
    submitted = _submit(live, key="m2-02-chain-submit-0001")
    task_ref = submitted["task_ref"]
    assert task_ref["task_id"] and task_ref["attempt"] == ATTEMPT
    assert len(live.manager.store.list(limit=1000)) == tasks_before + 1

    runtime = _mcp(live)
    status = _tool(runtime, "acp_status", {"reference": task_ref["task_id"]})
    assert status["isError"] is False
    assert status["structuredContent"]["data"]["task_ref"]["task_id"] == task_ref["task_id"]
    http_status = live.http.get(f"/api/v1/jobs/{task_ref['task_id']}")
    assert http_status.status_code == 200

    # The artifact chain runs on the seeded completed attempt (declared science
    # stub): submit/status above is the real task leg, this is the real
    # prepare/read/analyze leg.
    job_id = JOB_ID
    _seed_completed_frequency_job(live, job_id=job_id)
    operation = _prepare_frequency_artifact(live, job_id=job_id)

    # Operation status is queryable through both entries (one projection).
    via_mcp = _tool(
        runtime, "acp_status", {"reference": {"kind": "operation", "id": operation["operation_id"]}}
    )
    assert via_mcp["isError"] is False
    assert via_mcp["structuredContent"]["data"]["state"] == "ready"
    via_http = live.http.get(f"/api/v1/operations/{operation['operation_id']}")
    assert via_http.status_code == 200
    assert via_http.json()["state"] == "ready"

    # Read: MCP adapter, the v1 prepared read and the v2 science artifact read.
    ref = _artifact_ref(job_id=job_id)
    mcp_read = _tool(runtime, "acp_read_artifact", {"artifact_ref": ref})
    assert mcp_read["isError"] is False
    mcp_read_data = mcp_read["structuredContent"]["data"]
    assert mcp_read_data["digest"] == FREQUENCY_DIGEST
    assert mcp_read_data["body"].encode("utf-8") == FREQUENCY_BODY

    v1_read = live.http.get(
        f"/api/v1/jobs/{job_id}/prepared/{ARTIFACT_ID}",
        params={"attempt": ATTEMPT, "expected_revision": 1, "digest": FREQUENCY_DIGEST},
    )
    assert v1_read.status_code == 200, v1_read.text
    assert v1_read.json()["digest"] == FREQUENCY_DIGEST

    v2_read = live.http.get(
        f"/api/v2/science/tasks/{job_id}/attempts/{ATTEMPT}/artifacts/{ARTIFACT_ID}/read",
        params={"project_id": PROJECT, "generation": 1, "digest": FREQUENCY_DIGEST},
    )
    assert v2_read.status_code == 200, v2_read.text
    assert v2_read.json()["digest"] == FREQUENCY_DIGEST

    # Analyze over HTTP and MCP: the same digest-bound product, no task.
    counts_before, prepares_before = _side_effects(live)
    http_analyze = _analyze_http(
        live,
        {"analysis_id": "vibration.analyze", "input_refs": [ref], "explicit": True},
        job_id=job_id,
    )
    assert http_analyze.status_code == 200, http_analyze.text
    http_body = http_analyze.json()
    assert http_body["analysis_id"] == "vibration.analyze"
    assert http_body["started"] is False
    assert http_body["task_ref"] is None and http_body["operation_ref"] is None
    assert http_body["evidence_refs"][0]["digest"] == FREQUENCY_DIGEST

    mcp_analyze = _tool(
        runtime,
        "acp_analyze",
        {"analysis_id": "vibration.analyze", "input_refs": [ref], "explicit": True},
    )
    assert mcp_analyze["isError"] is False, mcp_analyze
    mcp_body = mcp_analyze["structuredContent"]["data"]

    http_product = http_body["result"]["product"]
    mcp_product = mcp_body["result"]["product"]
    assert http_product["digest"] == mcp_product["digest"] == http_body["product_digest"]
    assert http_product["digest"].startswith("sha256:")
    assert http_body["result"] == mcp_body["result"]

    # The report is A's real frequency science over the fixture bytes.
    report = http_product["report"]
    assert report["frequencies"] == [1615.84, 3795.36, 3896.58]
    assert report["normal_modes_available"] is True
    assert all("mode_index" in mode for mode in report["normal_modes"]["modes"])
    provenance = http_body["result"]["provenance"]
    assert provenance["starts_subprocess"] is False
    assert provenance["starts_task"] is False
    assert provenance["delegation_target"] == (
        "cccp.analysis.frequency_products.build_frequency_report"
    )

    # Nothing about a read/analyze created a task or prepared more artifacts.
    assert _side_effects(live) == (counts_before, prepares_before)


def test_analyze_is_deterministic_and_digest_bound(live: _LiveApp) -> None:
    _seed_completed_frequency_job(live)
    _prepare_frequency_artifact(live)
    ref = _artifact_ref()
    first = _analyze_http(
        live, {"analysis_id": "vibration.analyze", "input_refs": [ref], "explicit": True}
    )
    second = _analyze_http(
        live, {"analysis_id": "vibration.analyze", "input_refs": [ref], "explicit": True}
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["product_digest"] == second.json()["product_digest"]


# ---------------------------------------------------------------------------
# 3. Typed refusals (never the "unsupported" default)
# ---------------------------------------------------------------------------


def test_analyze_refuses_unprepared_old_digest_and_stale_attempt(live: _LiveApp) -> None:
    _seed_completed_frequency_job(live)
    _prepare_frequency_artifact(live)
    runtime = _mcp(live)
    counts_before, prepares_before = _side_effects(live)

    mismatched = _analyze_http(
        live,
        {
            "analysis_id": "vibration.analyze",
            "input_refs": [_artifact_ref(digest="sha256:" + "0" * 64)],
            "explicit": True,
        },
    )
    assert mismatched.status_code == 409, mismatched.text
    assert mismatched.json()["detail"]["code"] == "revision_conflict"
    assert mismatched.json()["detail"]["params"]["rule"] == "digest_mismatch"

    stale = _tool(
        runtime,
        "acp_analyze",
        {
            "analysis_id": "vibration.analyze",
            "input_refs": [dict(_artifact_ref(), attempt=ATTEMPT + 1)],
            "explicit": True,
        },
    )
    stale_error = _tool_error(stale)
    assert stale_error["code"] == "resource_not_ready"
    assert "not_prepared" in stale_error["message"]

    unprepared = _tool(
        runtime,
        "acp_analyze",
        {
            "analysis_id": "vibration.analyze",
            "input_refs": [dict(_artifact_ref(), artifact_id="never_prepared")],
            "explicit": True,
        },
    )
    unprepared_error = _tool_error(unprepared)
    assert unprepared_error["code"] == "resource_not_ready"
    assert "not_prepared" in unprepared_error["message"]

    assert _side_effects(live) == (counts_before, prepares_before)


def test_analyze_refuses_a_stale_generation_when_a_newer_one_is_authoritative(
    live: _LiveApp,
) -> None:
    """A generation-1 reference is stale once generation 2 is prepared."""
    job_id = "20261027_120001_000_frequency"
    _seed_completed_frequency_job(live, job_id=job_id)
    client = _client(live)
    prepared = dict(
        client.prepare(
            {
                "schema_version": 1,
                "purpose": "artifact",
                "job_id": job_id,
                "artifact_id": ARTIFACT_ID,
                "attempt": ATTEMPT,
                "generation": 2,
                "relative_path": "RESULT/freq.out",
                "media_type": "text/plain",
            }
        )
    )
    newer = _wait_operation(live, str(prepared["operation_id"]))
    assert newer["state"] == "ready"
    assert newer["generation"] == 2

    stale = _analyze_http(
        live,
        {
            "analysis_id": "vibration.analyze",
            "input_refs": [_artifact_ref(job_id=job_id)],
            "explicit": True,
        },
        job_id=job_id,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "revision_conflict"
    assert stale.json()["detail"]["params"]["rule"] == "generation_stale"

    current = _analyze_http(
        live,
        {
            "analysis_id": "vibration.analyze",
            "input_refs": [dict(_artifact_ref(job_id=job_id), generation=2)],
            "explicit": True,
        },
        job_id=job_id,
    )
    assert current.status_code == 200, current.text


def test_artifact_error_mapping_is_one_vocabulary() -> None:
    """The Data artifact refusals keep their frozen rules on the command surface."""
    from fastapi import HTTPException

    from acp.api.v1_routes import _artifact_error
    from acp_services.data.artifacts import (
        ArtifactAttemptMismatchError,
        ArtifactDigestMismatchError,
        ArtifactGenerationMismatchError,
        ArtifactNotPreparedError,
        ArtifactPathRefusedError,
    )

    cases = (
        (ArtifactAttemptMismatchError("old attempt"), 409, "revision_conflict", "attempt_mismatch"),
        (ArtifactDigestMismatchError("changed bytes"), 409, "revision_conflict", "digest_mismatch"),
        (
            ArtifactGenerationMismatchError("old generation"),
            409,
            "revision_conflict",
            "generation_stale",
        ),
        (ArtifactNotPreparedError("cold cache"), 409, "resource_not_ready", "not_prepared"),
        (ArtifactPathRefusedError("bad token"), 400, "input_invalid", "path_traversal"),
    )
    for exc, status, code, rule in cases:
        error: HTTPException = _artifact_error(exc)
        detail = error.detail
        assert error.status_code == status, type(exc).__name__
        assert detail["code"] == code, type(exc).__name__
        params = dict(detail.get("params") or {})
        assert params.get("rule", params.get("reason")) == rule, type(exc).__name__
    not_prepared: HTTPException = _artifact_error(ArtifactNotPreparedError("cold cache"))
    assert dict(not_prepared.detail)["retryable"] is True


def test_analyze_refuses_foreign_task_and_incomplete_references(live: _LiveApp) -> None:
    _seed_completed_frequency_job(live)
    _prepare_frequency_artifact(live)
    runtime = _mcp(live)

    foreign = _analyze_http(
        live,
        {
            "analysis_id": "vibration.analyze",
            "input_refs": [_artifact_ref(job_id="another-task")],
            "explicit": True,
        },
    )
    assert foreign.status_code == 400, foreign.text
    assert foreign.json()["detail"]["code"] == "input_invalid"
    assert foreign.json()["detail"]["params"]["rule"] == "task_id_mismatch"

    incomplete = _tool(
        runtime,
        "acp_analyze",
        {
            "analysis_id": "vibration.analyze",
            "input_refs": [{"task_id": JOB_ID}],
            "explicit": True,
        },
    )
    incomplete_error = _tool_error(incomplete)
    assert incomplete_error["code"] == "input_invalid"
    assert incomplete_error["params"]["rule"] == "missing_artifact_ref"
    assert incomplete_error["params"]["missing"] == [
        "attempt",
        "artifact_id",
        "generation",
        "digest",
    ]

    undeclared = _analyze_http(
        live,
        {
            "analysis_id": "vibration.analyze",
            "input_refs": [_artifact_ref()],
            "explicit": True,
            "implicit": True,
        },
    )
    assert undeclared.status_code == 422, undeclared.text
    assert undeclared.json()["detail"]["params"]["rule"] == "unknown_field"

    unknown = _analyze_http(
        live,
        {"analysis_id": "nmr.invented", "input_refs": [_artifact_ref()], "explicit": True},
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "unsupported_capability"


def test_compute_is_never_a_read_only_analysis(live: _LiveApp) -> None:
    """``vibration.compute`` is a task, refused by name on the analysis surface."""
    _seed_completed_frequency_job(live)
    _prepare_frequency_artifact(live)
    runtime = _mcp(live)
    counts_before, prepares_before = _side_effects(live)

    http_refused = _analyze_http(
        live,
        {"analysis_id": "vibration.compute", "input_refs": [_artifact_ref()], "explicit": True},
    )
    assert http_refused.status_code == 422
    detail = http_refused.json()["detail"]
    assert detail["code"] == "unsupported_capability"
    assert detail["params"]["rule"] == "analysis_capability_not_executable"
    assert detail["params"]["state"] == "registered"
    assert detail["params"]["owner"] == "B12"

    mcp_refused = _tool(
        runtime,
        "acp_analyze",
        {"analysis_id": "vibration.compute", "input_refs": [_artifact_ref()], "explicit": True},
    )
    mcp_error = _tool_error(mcp_refused)
    assert mcp_error["code"] == "unsupported_capability"
    assert mcp_error["params"]["rule"] == "analysis_capability_not_executable"

    # Refused before any task/prepare: the compute path is the task submit.
    assert _side_effects(live) == (counts_before, prepares_before)


def test_read_bound_is_refused_not_clamped(live: _LiveApp) -> None:
    _seed_completed_frequency_job(live)
    _prepare_frequency_artifact(live)
    runtime = _mcp(live)

    mcp_result = _tool(
        runtime,
        "acp_read_artifact",
        {"artifact_ref": _artifact_ref(), "max_bytes": MEMORY_BOUND_EXCEEDED},
    )
    mcp_error = _tool_error(mcp_result)
    assert mcp_error["code"] == "input_invalid"
    assert mcp_error["params"]["rule"] == "max_read_bytes_exceeded"
    assert mcp_error["params"]["limit"] == 1 << 20

    v1 = live.http.get(
        f"/api/v1/jobs/{JOB_ID}/prepared/{ARTIFACT_ID}",
        params={
            "attempt": ATTEMPT,
            "expected_revision": 1,
            "digest": FREQUENCY_DIGEST,
            "max_bytes": MEMORY_BOUND_EXCEEDED,
        },
    )
    assert v1.status_code == 400
    assert v1.json()["detail"]["params"]["rule"] == "max_read_bytes_exceeded"

    v2 = live.http.get(
        f"/api/v2/science/tasks/{JOB_ID}/attempts/{ATTEMPT}/artifacts/{ARTIFACT_ID}/read",
        params={
            "project_id": PROJECT,
            "generation": 1,
            "digest": FREQUENCY_DIGEST,
            "max_bytes": MEMORY_BOUND_EXCEEDED,
        },
    )
    assert v2.status_code == 400
    assert v2.json()["detail"]["params"]["rule"] == "max_read_bytes_exceeded"


# ---------------------------------------------------------------------------
# 4. Guards: the handler is pure analysis, and the advertised schema is the table
# ---------------------------------------------------------------------------

#: Imported modules that would mean the analysis handler reached an execution station.
_FORBIDDEN_HANDLER_IMPORTS = (
    "subprocess",
    "cccp.calculation.tasks",
    "acp.scheduler",
    "acp.workflows",
)
#: Calls that would mean the analysis handler starts work.
_FORBIDDEN_HANDLER_CALLS = frozenset(
    {"submit", "enqueue", "create_task", "Popen", "system", "run_frequency", "run"}
)


def test_analyze_handler_source_reaches_no_execution_station() -> None:
    """AST guard: the handler composition root imports/calls no execution station."""
    import ast

    source = (REPO / "src" / "acp" / "services_analysis_bootstrap.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.add(func.id)
            elif isinstance(func, ast.Attribute):
                calls.add(func.attr)
    for module in _FORBIDDEN_HANDLER_IMPORTS:
        assert not any(name == module or name.startswith(module + ".") for name in imported), (
            f"analysis handler imports {module!r}: {imported}"
        )
    reached = calls & _FORBIDDEN_HANDLER_CALLS
    assert not reached, f"analysis handler calls an execution station: {sorted(reached)}"
    assert "bind_capability" in calls
    assert "resolve_artifact_read" in calls
    # The pure-analysis entry point is A's declared binding, not a task core.
    assert "from cccp.calculation.capabilities import bind_capability" in source


def test_analyze_handler_never_submits_or_starts_a_task(
    live: _LiveApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cccp.analysis.frequency_products as frequency_products

    _seed_completed_frequency_job(live)
    _prepare_frequency_artifact(live)
    calls: list[str] = []
    delegated: list[Any] = []
    real_report = frequency_products.build_frequency_report

    def forbidden_submit(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not run
        calls.append("submit")
        raise AssertionError("the analysis handler must never submit a task")

    def forbidden_enqueue(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not run
        calls.append("enqueue")
        raise AssertionError("the analysis handler must never prepare a resource")

    def spying_report(calc: Any) -> dict[str, Any]:
        delegated.append(calc)
        return real_report(calc)

    monkeypatch.setattr(live.manager, "submit", forbidden_submit)
    monkeypatch.setattr(live.manager._prepare_operations, "enqueue", forbidden_enqueue)
    monkeypatch.setattr(frequency_products, "build_frequency_report", spying_report)

    counts_before, prepares_before = _side_effects(live)
    response = _analyze_http(
        live,
        {"analysis_id": "vibration.analyze", "input_refs": [_artifact_ref()], "explicit": True},
    )
    assert response.status_code == 200, response.text
    assert calls == []
    assert len(delegated) == 1
    assert list(delegated[0].frequencies) == [1615.84, 3795.36, 3896.58]
    assert _side_effects(live) == (counts_before, prepares_before)


def test_advertised_analyze_schema_is_the_application_field_table() -> None:
    """One table: MCP ``tools/list`` == application-api ``operations.analyze``."""
    from acp.api.v1_schemas import V1AnalyzeRequest
    from acp_services.entrypoints import mcp
    from acp_services.entrypoints.application_params import (
        ANALYZE_REQUEST_FIELDS,
        ANALYZE_REQUEST_REQUIRED,
        ARTIFACT_REF_IDENTITY_FIELDS,
    )

    contract = json.loads((CONTRACT / "application-api.v1.json").read_text(encoding="utf-8"))
    request = contract["operations"]["analyze"]["request"]

    assert set(ANALYZE_REQUEST_FIELDS) == set(request["fields"])
    assert tuple(ANALYZE_REQUEST_REQUIRED) == tuple(request["required"])
    assert set(mcp._ANALYZE_SCHEMA["properties"]) == set(ANALYZE_REQUEST_FIELDS)
    assert tuple(mcp._ANALYZE_SCHEMA["required"]) == tuple(ANALYZE_REQUEST_REQUIRED)
    assert mcp._ANALYZE_SCHEMA["additionalProperties"] is False

    # The HTTP DTO is the same table (extra="forbid" so an undeclared field is
    # the shared input_invalid envelope, never a silent drop).
    assert set(V1AnalyzeRequest.model_fields) == set(ANALYZE_REQUEST_FIELDS)
    assert V1AnalyzeRequest.model_config.get("extra") == "forbid"

    # Every input_refs entry is the frozen ArtifactRef identity tuple.
    item_schema = mcp._ANALYZE_SCHEMA["properties"]["input_refs"]["items"]
    assert tuple(item_schema["required"]) == tuple(ARTIFACT_REF_IDENTITY_FIELDS)
    assert set(item_schema["properties"]) == {
        *ARTIFACT_REF_IDENTITY_FIELDS,
        "size_bytes",
        "media_type",
        "relative_path",
    }

    # The frozen capability states are the only ones that exist.
    from acp_services.runtime.analysis_capabilities import ANALYSIS_CAPABILITY_STATES

    assert tuple(ANALYSIS_CAPABILITY_STATES) == ("registered", "executable", "unavailable")
    described = mcp._ANALYZE_SCHEMA["description"]
    assert "analysis_capability_not_executable" in described
    assert "no QC" in described or "starts no QC" in described


def test_read_schema_declares_the_frozen_read_bound() -> None:
    from acp_services.entrypoints import mcp
    from acp_services.entrypoints.application_params import MAX_READ_BYTES

    max_bytes = mcp._READ_SCHEMA["properties"]["max_bytes"]
    assert max_bytes["maximum"] == MAX_READ_BYTES == 1 << 20


def test_published_rules_survive_redaction_but_secrets_do_not() -> None:
    """A typed ``params.rule`` stays readable; an opaque token does not."""
    from acp_services.entrypoints import machine_output as mo

    for rule in (
        "analysis_capability_not_executable",
        "analysis_capability_not_registered",
        "max_read_bytes_exceeded",
    ):
        assert mo.redact(rule) == rule
    assert mo.redact("AbCdEf0123456789AbCdEf0123456789") == mo.REDACTED_SECRET


def test_analysis_product_digest_is_stable_across_encodings() -> None:
    """The product digest covers the report canonical JSON (not the transport)."""
    from cccp.calculation.products.contracts import compute_input_digest

    report = {"frequencies": [1.0, 2.0], "normal_modes_available": False}
    assert compute_input_digest(report) == compute_input_digest(json.loads(json.dumps(report)))
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", compute_input_digest(report))
