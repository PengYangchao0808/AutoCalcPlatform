"""Round-5 PR-07: cache-only selection status and the MCP selection tools.

The acceptance list separates three non-callable states (not negotiated / not
assembled / capability off), a read that never writes/prepares/fetches, the
publication receipt, and the error vocabulary.  This suite covers the HTTP
read surface first; the MCP tool wiring lives in the same PR and is exercised
through the adapter below once assembled.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_acp_api_v1 import make_client


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with make_client(tmp_path, monkeypatch, max_running=1) as test_client:
        yield test_client


def _register_job(client: TestClient, tmp_path: Path, work_dir: Path) -> str:
    from tests.test_acp_api_pes_review import _register_job as register

    return register(client, tmp_path, work_dir)


def _make_task(tmp_path: Path) -> Path:
    from tests.test_acp_api_pes_review import _make_pes_task

    return _make_pes_task(tmp_path / "tree")


def _counts(client: TestClient) -> dict:
    return dict(client.app.state.job_manager.store.counts())


def test_status_reads_are_cache_only_and_side_effect_free(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = _register_job(client, tmp_path, _make_task(tmp_path))
    before = _counts(client)
    response = client.get(f"/api/v1/jobs/{job_id}/pes/review/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["cache_only"] is True
    assert body["remote"] is False
    assert body["job_id"] == job_id
    assert body["capability"]["publish_endpoint"].endswith("/pes/review")
    assert body["publication"] is None
    assert _counts(client) == before


def test_remote_uncached_status_reports_not_prepared_without_fetch(
    client: TestClient, tmp_path: Path
) -> None:
    from tests.test_acp_api_pes_review import _RemoteTreeFetcher, _register_remote_job

    client.app.state.job_manager._remote_fetcher = _RemoteTreeFetcher({})
    job_id, _local = _register_remote_job(client, tmp_path)
    response = client.get(f"/api/v1/jobs/{job_id}/pes/review/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["remote"] is True
    assert body["status"] == "not_prepared"
    assert "prepare" in str(body["capability"]["prepare_endpoint"])
    assert client.app.state.job_manager._remote_fetcher.calls == []


def test_confirmed_status_exposes_revision_and_publication(client: TestClient, tmp_path: Path) -> None:
    job_id = _register_job(client, tmp_path, _make_task(tmp_path))
    saved = client.post(
        f"/api/v1/jobs/{job_id}/pes/review",
        json={
            "candidates": [
                {"frame_index": 1, "role": "TS"},
                {"frame_index": 2, "role": "INT"},
            ],
            "idempotency_key": "status-1",
        },
    )
    assert saved.status_code == 200, saved.text
    response = client.get(f"/api/v1/jobs/{job_id}/pes/review/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["revision"] == 1
    assert body["selector"] == "manual"
    assert len(body["selected"]) == 2
    assert body["selected"][0]["selection_source"] == "manual"
    assert body["publication"]["status"] == "published"
    assert body["source"]["task_status"] == "COMPLETED"


def test_status_rejects_non_pes_workflows(client: TestClient, tmp_path: Path) -> None:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    manager = client.app.state.job_manager
    work_dir = tmp_path / "freq_task"
    work_dir.mkdir(parents=True)
    spec = JobSpec(workflow="frequency", name="freq", input={"smiles": "CCO"}, method={})
    record = JobRecord(
        id="20261031_001_frequency",
        spec=spec,
        status=JobStatus.COMPLETED,
        work_dir=str(work_dir),
        created_at="2026-10-31T00:00:00",
        updated_at="2026-10-31T00:00:00",
    )
    manager.store.create(record)
    response = client.get("/api/v1/jobs/20261031_001_frequency/pes/review/status")
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# tool registration / visibility / forwarding
# --------------------------------------------------------------------------- #


def test_selection_tools_are_registered_and_assembled_only_with_the_port() -> None:
    from acp_services.entrypoints import mcp

    assert mcp.McpTool.GET_PES_REVIEW in mcp.TOOL_ALLOWLIST
    assert mcp.McpTool.PUBLISH_PES_REVIEW in mcp.TOOL_ALLOWLIST
    assert mcp.McpTool.PUBLISH_PES_REVIEW in mcp._ARGUMENT_SCHEMAS
    assert mcp.McpTool.GET_PES_REVIEW in mcp._ARGUMENT_SCHEMAS
    # the port is optional: absence means not visible (checked via the server)
    import dataclasses

    fields = {field.name for field in dataclasses.fields(mcp.McpBackends)}
    assert "selection" in fields


def test_controller_selection_port_forwards_and_keeps_errors() -> None:
    from acp.services_mcp_bootstrap import _ControllerSelection

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict | None]] = []

        def get_pes_review_status(self, job_id: str) -> dict:
            self.calls.append(("get", job_id, None))
            return {"job_id": job_id, "status": "confirmed", "revision": 3}

        def publish_pes_review(self, job_id: str, request: dict) -> dict:
            self.calls.append(("publish", job_id, request))
            return {"job_id": job_id, "revision": 4, "publication": {"status": "published"}}

    client = _Client()
    port = _ControllerSelection(client)
    status = port.get_review_status("job-1", workspace_id="ws")
    assert status["revision"] == 3
    published = port.publish_review(
        "job-1",
        {"authorization_id": "auth-1", "candidates": [{"frame_index": 1, "role": "TS"}]},
        workspace_id="ws",
    )
    assert published["publication"]["status"] == "published"
    assert [call[0] for call in client.calls] == ["get", "publish"]


def test_publish_tool_requires_the_authorization_arguments() -> None:
    from acp_services.entrypoints import mcp

    schema = mcp._ARGUMENT_SCHEMAS[mcp.McpTool.PUBLISH_PES_REVIEW]
    assert set(schema["required"]) == {"job_id", "authorization_id", "candidates"}
    assert schema["additionalProperties"] is False
    get_schema = mcp._ARGUMENT_SCHEMAS[mcp.McpTool.GET_PES_REVIEW]
    assert set(get_schema["required"]) == {"job_id"}


# --------------------------------------------------------------------------- #
# typed error mapping for the selection tools
# --------------------------------------------------------------------------- #


def test_controller_selection_maps_not_prepared_to_a_typed_mcp_error() -> None:
    from acp.services_mcp_bootstrap import _ControllerSelection
    from acp_services.entrypoints import mcp as mcp_mod
    from acp_services.entrypoints.controller_client import NotPreparedError

    class _Client:
        def get_pes_review_status(self, job_id: str):
            raise NotPreparedError(f"Job not found: {job_id}")

        def publish_pes_review(self, job_id: str, request: dict):
            raise NotPreparedError("selection not prepared")

    port = _ControllerSelection(_Client())
    import pytest as _pytest

    with _pytest.raises(mcp_mod.McpToolError) as excinfo:
        port.get_review_status("20990101_000_missing", workspace_id="")
    error = excinfo.value
    assert error.code == mcp_mod.mo.ErrorCode.RESOURCE_NOT_READY
    assert error.params["field"] == "job_id"
    assert error.params["rule"] == "not_found"
    assert error.params["command"] == "selection.status"
    assert error.retryable is True


def test_selection_read_end_to_end_returns_the_typed_error() -> None:
    from tests.test_services_mcp import _Fakes
    from acp.services_mcp_bootstrap import _ControllerSelection
    from acp_services.entrypoints import mcp as mcp_mod
    from acp_services.entrypoints.controller_client import NotPreparedError

    class _Client:
        def get_pes_review_status(self, job_id: str):
            raise NotPreparedError(f"Job not found: {job_id}")

        def publish_pes_review(self, job_id: str, request: dict):
            raise NotPreparedError("selection not prepared")

    import dataclasses

    fakes = _Fakes()
    backends = dataclasses.replace(
        fakes.backends(), selection=_ControllerSelection(_Client())
    )
    server = mcp_mod.McpServer(backends=backends)
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": mcp_mod.McpTool.GET_PES_REVIEW,
                "arguments": {"job_id": "20990101_000_missing"},
            },
        }
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == mcp_mod.mo.ErrorCode.RESOURCE_NOT_READY
    assert error["params"]["field"] == "job_id"
    assert error["params"]["rule"] == "not_found"


def test_publish_tool_forwards_the_server_owned_agent_selector() -> None:
    """The MCP publish tool is the agent path; it must send selector=agent.

    A client omitting the selector used to land on the manual REST default and
    bypass the authorization entirely (round-6 complaint #1); the selector is
    now server-owned and never a schema field.
    """
    import dataclasses

    from tests.test_services_mcp import _Fakes
    from acp_services.entrypoints import mcp as mcp_mod

    class _Port:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def get_review_status(self, job_id: str, *, workspace_id: str) -> dict:
            return {"job_id": job_id}

        def publish_review(
            self, job_id: str, request: dict, *, workspace_id: str
        ) -> dict:
            self.calls.append((job_id, dict(request)))
            return {"job_id": job_id, "revision": 1, "publication": {"status": "published"}}

    port = _Port()
    backends = dataclasses.replace(_Fakes().backends(), selection=port)
    server = mcp_mod.McpServer(backends=backends)
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": mcp_mod.McpTool.PUBLISH_PES_REVIEW,
                "arguments": {
                    "job_id": "job-1",
                    "authorization_id": "auth-1",
                    "candidates": [{"frame_index": 1, "role": "TS"}],
                },
            },
        }
    )
    assert response is not None
    assert response["result"]["isError"] is False
    job_id, request = port.calls[0]
    assert job_id == "job-1"
    assert request["selector"] == "agent"
    assert request["authorization_id"] == "auth-1"
    schema = mcp_mod._ARGUMENT_SCHEMAS[mcp_mod.McpTool.PUBLISH_PES_REVIEW]
    assert "selector" not in schema["properties"]
