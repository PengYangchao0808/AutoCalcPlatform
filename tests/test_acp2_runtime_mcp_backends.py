"""B06 — MCP/HTTP/CLI bind to the same real Controller application commands.

These tests run a **real** FastAPI Controller (uvicorn on a dynamic loopback
port, real ``JobManager``/``JobStore``) and the real stdlib ``ControllerClient``
+ ``acp.services_mcp_bootstrap`` composition.  They are not the ``_Fakes`` from
``test_services_mcp.py``: every tool call crosses HTTP into the Controller.

Covered clauses:

* discovery (``acp_capabilities``) reads the Controller and creates nothing;
* ``acp_preflight`` is zero-side-effect (no task / prepare / reservation);
* ``acp_submit`` returns a durable ``task_ref`` before QC completion;
* ``acp_status``/``acp_read_artifact`` are query-only (no status change, no
  prepare operation, no catalog fetch, no task);
* ``acp_prepare_artifact`` creates an explicit persistent operation ref and
  ``GET /api/v1/operations/{id}`` projects it; a missing operation is
  ``resource_not_ready`` (never an empty 200);
* ``acp_analyze`` requires ``explicit=true`` and is registry-gated
  (``unsupported_capability``; no science);
* MCP and HTTP produce the same parameters for the same logical action.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from typing import Any

import pytest
import uvicorn

_XYZ = "2\nH2\nH 0 0 0\nH 0 0 0.74\n"
_METHOD = {"levels": {"single_point": {"functional": "r2SCAN-3c"}}}
_SUBMIT = {
    "workflow": "singlepoint",
    "input": {"xyz_text": _XYZ, "charge": 0, "multiplicity": 1},
    "method": _METHOD,
    "resources": {"cores": 1},
    # M1-01b: acp_submit requires the retry key; the HTTP body does not.
    "idempotency_key": "b06-submit-parity-0001",
}


class _LiveController:
    def __init__(self, base_url: str, app: Any, manager: Any) -> None:
        self.base_url = base_url
        self.app = app
        self.manager = manager


@pytest.fixture()
def live_controller(tmp_path, monkeypatch) -> Generator[_LiveController, None, None]:
    import acp.scheduler.capabilities as capabilities_module
    import acp.scheduler.manager as manager_module

    monkeypatch.setattr(capabilities_module, "local_satisfies", lambda required: True)
    monkeypatch.setattr(manager_module, "local_satisfies", lambda required: True)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=2)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="b06-uvicorn")
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
        manager._start_submission_thread = lambda *args, **kwargs: None
        yield _LiveController(f"http://127.0.0.1:{port}", app, manager)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=10)


def _client(live: _LiveController):
    from acp_services.entrypoints.controller_client import ControllerClient

    return ControllerClient(live.base_url)


def _runtime(live: _LiveController):
    from acp.services_mcp_bootstrap import build_mcp_runtime

    return build_mcp_runtime(live.base_url)


def _tool(mcp_runtime: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = mcp_runtime.server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None and "result" in response, response
    return response["result"]


def _task_count(live: _LiveController) -> int:
    return len(live.manager.store.list(limit=1000))


def _prepare_count(live: _LiveController) -> int:
    return len(live.manager._prepare_operations.list())


def _submit(live: _LiveController, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return dict(_client(live).submit(payload or _SUBMIT))


def test_discovery_reads_controller_and_creates_nothing(live_controller: _LiveController) -> None:
    before = _task_count(live_controller)
    mcp_runtime = _runtime(live_controller)
    result = _tool(mcp_runtime, "acp_capabilities", {})
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert structured["status"] == "ok"
    data = structured["data"]
    assert set(data["tools"]) == set(mcp_runtime.client.capabilities().tools or ())
    assert _task_count(live_controller) == before
    assert _prepare_count(live_controller) == 0


def test_preflight_is_zero_side_effect(live_controller: _LiveController) -> None:
    client = _client(live_controller)
    before_tasks = _task_count(live_controller)
    before_prepares = _prepare_count(live_controller)
    http_result = client.preflight(_SUBMIT)
    assert http_result["would_create_task"] is False
    assert http_result["would_reserve"] is False
    assert str(http_result["plan_digest"]).startswith("sha256:")
    assert _task_count(live_controller) == before_tasks
    assert _prepare_count(live_controller) == before_prepares

    mcp_runtime = _runtime(live_controller)
    tool_result = _tool(mcp_runtime, "acp_preflight", _SUBMIT)
    assert tool_result["isError"] is False
    assert tool_result["structuredContent"]["dry_run"] is True
    estimate = tool_result["structuredContent"]["data"]["estimate"]
    assert estimate["would_create_task"] is False
    assert _task_count(live_controller) == before_tasks


def test_submit_returns_before_completion(live_controller: _LiveController) -> None:
    mcp_runtime = _runtime(live_controller)
    started = time.monotonic()
    result = _tool(mcp_runtime, "acp_submit", dict(_SUBMIT, name="b06-submit"))
    elapsed = time.monotonic() - started
    assert result["isError"] is False
    data = result["structuredContent"]["data"]
    task_ref = data["task_ref"]
    job_id = task_ref["task_id"]
    assert task_ref["job_id"] == job_id
    assert task_ref["attempt"] == 1
    assert elapsed < 10.0
    record = live_controller.manager.store.get(job_id)
    assert record is not None
    assert not record.status.is_terminal
    assert record.status.value in {"queued", "starting", "pending"}

    status = _tool(mcp_runtime, "acp_status", {"reference": {"kind": "job", "id": job_id}})
    assert status["isError"] is False
    assert status["structuredContent"]["data"]["task_ref"]["task_id"] == job_id


def test_status_and_read_have_no_side_effects(live_controller: _LiveController) -> None:
    created = _submit(live_controller)
    job_id = created["job_id"]
    record_before = live_controller.manager.store.get(job_id)
    assert record_before is not None
    status_before = record_before.status.value
    tasks_before = _task_count(live_controller)
    prepares_before = _prepare_count(live_controller)

    client = _client(live_controller)
    snapshot = client.status(job_id)
    assert snapshot.task_id == job_id
    client.status(job_id)

    from acp_services.entrypoints.controller_client import NotPreparedError

    with pytest.raises(NotPreparedError):
        client.read(
            {
                "artifact_ref": {
                    "task_id": job_id,
                    "attempt": 1,
                    "artifact_id": "catalog",
                    "generation": 1,
                    "digest": "sha256:" + "0" * 64,
                }
            }
        )

    mcp_runtime = _runtime(live_controller)
    status = _tool(mcp_runtime, "acp_status", {"reference": job_id})
    assert status["isError"] is False
    read = _tool(
        mcp_runtime,
        "acp_read_artifact",
        {
            "artifact_ref": {
                "task_id": job_id,
                "attempt": 1,
                "artifact_id": "catalog",
                "generation": 1,
                "digest": "sha256:" + "0" * 64,
            }
        },
    )
    assert read["isError"] is True
    assert read["structuredContent"]["error"]["code"] == "resource_not_ready"

    record_after = live_controller.manager.store.get(job_id)
    assert record_after is not None
    assert record_after.status.value == status_before
    assert _task_count(live_controller) == tasks_before
    assert _prepare_count(live_controller) == prepares_before


def test_prepare_operation_projection_and_not_prepared(live_controller: _LiveController) -> None:
    created = _submit(live_controller)
    job_id = created["job_id"]
    mcp_runtime = _runtime(live_controller)
    prepared = _tool(
        mcp_runtime,
        "acp_prepare_artifact",
        {
            "purpose": "artifact",
            "job_id": job_id,
            "artifact_id": "catalog",
            "idempotency_key": "b06-prepare-0001",
        },
    )
    assert prepared["isError"] is False
    data = prepared["structuredContent"]["data"]
    operation_id = data["operation_ref"]["id"]
    assert operation_id.startswith("op_")
    assert data["availability"] in {"absent", "preparing"}

    projection = _client(live_controller).operation(operation_id)
    assert projection["operation_id"] == operation_id
    assert projection["job_id"] == job_id
    assert projection["purpose"] == "artifact"

    missing = None
    try:
        _client(live_controller).operation("op_b06_unknown")
    except Exception as exc:  # noqa: BLE001 - typed client error asserted below
        missing = exc
    assert missing is not None
    assert getattr(missing, "code", "") == "resource_not_ready"
    assert "not_prepared" in str(missing)


def test_analyze_requires_explicit_and_is_registry_gated(
    live_controller: _LiveController,
) -> None:
    """M2-02: the analyze surface is executable, and refusals stay typed.

    ``vibration.analyze`` is executable since M2-02, so an *incomplete*
    ArtifactRef is now the input error (never the "unsupported" default it used
    to be), ``vibration.compute`` is a registered-but-not-executable compute
    capability, and an unprepared reference is ``resource_not_ready``.  None of
    them creates a task or a prepare operation.
    """
    created = _submit(live_controller)
    job_id = created["job_id"]
    mcp_runtime = _runtime(live_controller)
    tasks_before = _task_count(live_controller)
    prepares_before = _prepare_count(live_controller)

    implicit = _tool(
        mcp_runtime,
        "acp_analyze",
        {
            "analysis_id": "vibration.analyze",
            "input_refs": [{"task_id": job_id}],
            "explicit": False,
        },
    )
    assert implicit["isError"] is True
    assert implicit["structuredContent"]["error"]["code"] == "input_invalid"

    # A partial reference can never prove which artifact was meant.
    partial = _tool(
        mcp_runtime,
        "acp_analyze",
        {
            "analysis_id": "vibration.analyze",
            "input_refs": [{"task_id": job_id}],
            "explicit": True,
        },
    )
    assert partial["isError"] is True
    partial_error = partial["structuredContent"]["error"]
    assert partial_error["code"] == "input_invalid"
    assert partial_error["params"]["rule"] == "missing_artifact_ref"

    prepared_ref = {
        "task_id": job_id,
        "attempt": 1,
        "artifact_id": "freq_out",
        "generation": 1,
        "digest": "sha256:" + "0" * 64,
    }
    # ``vibration.compute`` is registered (owner B12) but not executable as an
    # analysis: it runs one frequency task, submitted as a task.
    compute = _tool(
        mcp_runtime,
        "acp_analyze",
        {"analysis_id": "vibration.compute", "input_refs": [prepared_ref], "explicit": True},
    )
    assert compute["isError"] is True
    compute_error = compute["structuredContent"]["error"]
    assert compute_error["code"] == "unsupported_capability"
    assert compute_error["params"]["rule"] == "analysis_capability_not_executable"
    assert compute_error["params"]["state"] == "registered"

    # Nothing is prepared for this task yet: the executable capability refuses
    # with the named not-prepared class instead of fabricating a product.
    unprepared = _tool(
        mcp_runtime,
        "acp_analyze",
        {"analysis_id": "vibration.analyze", "input_refs": [prepared_ref], "explicit": True},
    )
    assert unprepared["isError"] is True
    assert unprepared["structuredContent"]["error"]["code"] == "resource_not_ready"
    assert "not_prepared" in unprepared["structuredContent"]["error"]["message"]

    assert _task_count(live_controller) == tasks_before
    assert _prepare_count(live_controller) == prepares_before


def test_mcp_and_http_submit_parameters_match(live_controller: _LiveController) -> None:
    from acp_services.entrypoints.application_params import normalize_submit

    captured: list[Any] = []
    manager = live_controller.manager
    original_submit = manager.submit

    def capture(
        spec: Any,
        group_id: str | None = None,
        *,
        idempotency: Any | None = None,
        budget: Any | None = None,
        identity: Any | None = None,
    ) -> Any:
        captured.append(spec)
        return original_submit(
            spec,
            group_id=group_id,
            idempotency=idempotency,
            budget=budget,
            identity=identity,
        )

    manager.submit = capture  # type: ignore[method-assign]
    try:
        payload = dict(_SUBMIT, name="parity")
        # M1-01b: both entries need their own retry key; the two bodies are the
        # same logical request but the HTTP normaliser does not carry ``name``
        # into the digest material, so reusing one key across the two entries
        # would be a (correct) idempotency conflict rather than a parity check.
        normalized = normalize_submit({**payload, "idempotency_key": "b06-http-parity-0001"})
        http_created = _client(live_controller).submit(normalized)
        mcp_runtime = _runtime(live_controller)
        mcp_created = _tool(
            mcp_runtime, "acp_submit", {**payload, "idempotency_key": "b06-mcp-parity-0001"}
        )
    finally:
        manager.submit = original_submit  # type: ignore[method-assign]

    assert http_created["job_id"]
    assert mcp_created["isError"] is False
    assert len(captured) == 2
    first, second = captured
    assert first.workflow == second.workflow == "singlepoint"
    assert first.method == second.method
    assert first.input == second.input
    assert first.resources == second.resources


def test_error_matrix_is_structured_and_consistent(live_controller: _LiveController) -> None:
    from acp_services.entrypoints.controller_client import (
        ConflictError,
        InvalidParameterError,
        NotPreparedError,
        UnsupportedError,
    )

    client = _client(live_controller)
    with pytest.raises(InvalidParameterError):
        client.preflight({"workflow": "not-a-workflow", "input": {}})
    with pytest.raises(NotPreparedError):
        client.operation("op_b06_missing")
    created = _submit(live_controller)
    import json
    import urllib.error
    import urllib.request

    conflict_request = urllib.request.Request(
        f"{live_controller.base_url}/api/v1/jobs/{created['job_id']}/prepare",
        data=json.dumps(
            {
                "purpose": "artifact",
                "job_id": "some-other-job",
                "artifact_id": "catalog",
            }
        ).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as conflict:
        urllib.request.urlopen(conflict_request, timeout=10)
    assert conflict.value.code == 409
    assert json.loads(conflict.value.read())["detail"]["code"] == "revision_conflict"
    assert ConflictError.code == "revision_conflict"
    with pytest.raises(UnsupportedError):
        client.analyze(
            {
                "analysis_id": "vibration.compute",
                "input_refs": [
                    {
                        "task_id": created["job_id"],
                        "attempt": 1,
                        "artifact_id": "freq_out",
                        "generation": 1,
                        "digest": "sha256:" + "0" * 64,
                    }
                ],
                "explicit": True,
            }
        )

    mcp_runtime = _runtime(live_controller)
    missing_workflow = _tool(mcp_runtime, "acp_preflight", {})
    assert missing_workflow["isError"] is True
    assert missing_workflow["structuredContent"]["error"]["code"] == "input_invalid"


def test_research_launch_validates_budget_and_project(live_controller: _LiveController) -> None:
    from acp_services.entrypoints.controller_client import (
        AuthorizationError,
        InvalidParameterError,
    )

    client = _client(live_controller)
    with pytest.raises(InvalidParameterError):
        client.submit(dict(_SUBMIT, budget={"unit": "bogus", "timeout_s": 10}))
    with pytest.raises(AuthorizationError):
        client.submit(
            dict(
                _SUBMIT,
                proposal_ref="proposal-b06",
                plan_digest="sha256:" + "0" * 64,
                budget={"unit": "task_count", "count": 1, "timeout_s": 60},
                project_id="project-that-does-not-exist",
            )
        )
