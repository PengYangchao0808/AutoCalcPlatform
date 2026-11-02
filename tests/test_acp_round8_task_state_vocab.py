"""Round-8 二.2 / K5: the ten-value task-state vocabulary and pre-filter counts.

The published vocabulary listed seven states while the store has ten
(``starting``/``pending``/``cancelling`` were invisible), ``status`` was matched
by exact equality with no validation, and ``counts`` was computed **after** the
filter — so an empty page returned ``{"counts": {}, "jobs": []}`` and an agent
could not tell "nothing running" from "everything running, filtered away".

Pinned here:

* one vocabulary, derived from the store enum, used by the HTTP route and the
  MCP tool alike;
* ``status=starting`` and ``status_group=active`` both find a ``STARTING`` job;
* ``status`` + ``status_group`` is a typed 422 (``mutually_exclusive``);
* an unknown ``status`` is a 422 with the full ``allowed`` list, never a silent
  empty page;
* ``counts`` is pre-filter and always carries all ten keys.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp_services.entrypoints import mcp
from acp_services.store.records import JobRecord, JobSpec, JobStatus

_JOB_ID = "20261031_120000_007_frequency"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as test_client:
        yield test_client


def _seed(client: TestClient, tmp_path: Path, *, job_id: str, status: JobStatus) -> None:
    manager = client.app.state.job_manager
    work_dir = tmp_path / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    manager.store.create(
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="frequency", name=job_id),
            status=status,
            work_dir=str(work_dir),
        )
    )


# --------------------------------------------------------------------------- #
# the vocabulary itself
# --------------------------------------------------------------------------- #


def test_vocabulary_is_the_ten_real_states() -> None:
    assert set(mcp.TASK_STATE_VOCABULARY) == {status.value for status in JobStatus}
    assert len(mcp.TASK_STATE_VOCABULARY) == 10
    for state in ("starting", "pending", "cancelling"):
        assert state in mcp.TASK_STATE_VOCABULARY


def test_groups_partition_the_vocabulary() -> None:
    active = set(mcp.STATUS_GROUPS["active"])
    terminal = set(mcp.STATUS_GROUPS["terminal"])
    assert active | terminal == set(mcp.TASK_STATE_VOCABULARY)
    assert active & terminal == set()
    assert set(mcp.STATUS_GROUPS["running_now"]) == {"starting", "pending", "running"}
    assert set(mcp.STATUS_GROUPS["running_now"]) <= active
    assert "starting" in active and "cancelling" in active


# --------------------------------------------------------------------------- #
# HTTP list route
# --------------------------------------------------------------------------- #


def test_empty_scope_counts_still_have_all_ten_keys(client: TestClient) -> None:
    body = client.get("/api/v1/jobs").json()
    assert body["jobs"] == []
    assert set(body["counts"]) == set(mcp.TASK_STATE_VOCABULARY)
    assert all(value == 0 for value in body["counts"].values())


def test_exact_starting_status_finds_the_job(client: TestClient, tmp_path: Path) -> None:
    _seed(client, tmp_path, job_id=_JOB_ID, status=JobStatus.STARTING)
    body = client.get("/api/v1/jobs", params={"status": "starting"}).json()
    assert [job["id"] for job in body["jobs"]] == [_JOB_ID]
    # counts are pre-filter: the completed count is a real zero, not a missing key
    assert body["counts"]["starting"] == 1
    assert body["counts"]["completed"] == 0


def test_status_group_active_finds_starting(client: TestClient, tmp_path: Path) -> None:
    _seed(client, tmp_path, job_id=_JOB_ID, status=JobStatus.STARTING)
    body = client.get("/api/v1/jobs", params={"status_group": "active"}).json()
    assert [job["id"] for job in body["jobs"]] == [_JOB_ID]
    body = client.get("/api/v1/jobs", params={"status_group": "running_now"}).json()
    assert [job["id"] for job in body["jobs"]] == [_JOB_ID]
    body = client.get("/api/v1/jobs", params={"status_group": "terminal"}).json()
    assert body["jobs"] == []
    assert body["counts"]["starting"] == 1


def test_counts_are_computed_before_the_filter(client: TestClient, tmp_path: Path) -> None:
    _seed(client, tmp_path, job_id=_JOB_ID, status=JobStatus.COMPLETED)
    body = client.get("/api/v1/jobs", params={"status": "running"}).json()
    assert body["jobs"] == []
    assert body["counts"]["completed"] == 1
    assert body["counts"]["running"] == 0


def test_unknown_status_is_typed_and_names_the_allowed_values(client: TestClient) -> None:
    response = client.get("/api/v1/jobs", params={"status": "dispatched"})
    assert response.status_code == 422
    params = response.json()["detail"]["params"]
    assert params["rule"] == "invalid_enum"
    assert params["field"] == "status"
    assert list(params["allowed"]) == list(mcp.TASK_STATE_VOCABULARY)


def test_status_and_status_group_are_mutually_exclusive(client: TestClient) -> None:
    response = client.get("/api/v1/jobs", params={"status": "running", "status_group": "active"})
    assert response.status_code == 422
    params = response.json()["detail"]["params"]
    assert params["rule"] == "mutually_exclusive"


def test_unknown_status_group_is_typed(client: TestClient) -> None:
    response = client.get("/api/v1/jobs", params={"status_group": "in_flight"})
    assert response.status_code == 422
    assert response.json()["detail"]["params"]["allowed"] == list(mcp.STATUS_GROUPS)


def test_project_scope_uses_the_same_semantics(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/v1/projects", json={"name": "Alpha"}).json()
    project_id = created["project_id"]
    manager = client.app.state.job_manager
    job_id = "20261031_120001_008_frequency"
    work_dir = tmp_path / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    manager.store.create(
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="frequency", name=job_id, project_id=project_id),
            status=JobStatus.PENDING,
            work_dir=str(work_dir),
        )
    )
    body = client.get(
        f"/api/v1/projects/{project_id}/jobs", params={"status_group": "active"}
    ).json()
    assert [job["id"] for job in body["jobs"]] == [job_id]
    assert body["counts"]["pending"] == 1
    assert set(body["counts"]) == set(mcp.TASK_STATE_VOCABULARY)


# --------------------------------------------------------------------------- #
# MCP list_jobs
# --------------------------------------------------------------------------- #


class _FakeCapabilities:
    def capabilities(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"workflows": []}


class _Unused:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"must not reach the {name} port")


class _ListPort:
    """Status port that records the forwarded query (and applies the group)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    def get(self, reference: Any, *, workspace_id: str) -> Any:  # pragma: no cover
        raise AssertionError("list must not call get")

    def list_tasks(
        self,
        *,
        status: str | None = None,
        status_group: str | None = None,
        limit: int = 50,
        workspace_id: str = "",
    ) -> dict[str, Any]:
        del workspace_id
        self.calls.append({"status": status, "status_group": status_group, "limit": limit})
        rows = self.rows
        if status is not None:
            rows = [row for row in rows if row.get("state") == status]
        if status_group is not None:
            allowed = set(mcp.STATUS_GROUPS[status_group])
            rows = [row for row in rows if row.get("state") in allowed]
        return {"jobs": rows, "counts": {state: 0 for state in mcp.TASK_STATE_VOCABULARY}}


def _mcp_server(port: _ListPort) -> Any:
    unused = _Unused()
    return mcp.McpServer(
        backends=mcp.McpBackends(
            capabilities=_FakeCapabilities(),
            preflight=unused,
            submit=unused,
            status=port,
            cancel=unused,
            artifacts=unused,
            analysis=unused,
        )
    )


def _call(server: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None and "result" in response, response
    return response["result"]


def test_mcp_accepts_starting_and_rejects_unknown_states() -> None:
    port = _ListPort([{"id": _JOB_ID, "state": "starting"}])
    server = _mcp_server(port)

    ok = _call(server, mcp.McpTool.LIST_JOBS, {"status": "starting"})
    assert ok["isError"] is False
    assert port.calls[-1]["status"] == "starting"
    assert ok["structuredContent"]["data"]["filter"]["status"] == "starting"

    bad = _call(server, mcp.McpTool.LIST_JOBS, {"status": "dispatched"})
    assert bad["isError"] is True
    params = bad["structuredContent"]["error"]["params"]
    assert params["rule"] == "invalid_enum"
    assert list(params["allowed"]) == list(mcp.TASK_STATE_VOCABULARY)


def test_mcp_status_group_is_mutually_exclusive_with_status() -> None:
    server = _mcp_server(_ListPort([]))
    result = _call(server, mcp.McpTool.LIST_JOBS, {"status": "queued", "status_group": "active"})
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["params"]["rule"] == "mutually_exclusive"


def test_mcp_forwards_status_group_and_echoes_the_family() -> None:
    port = _ListPort([{"id": _JOB_ID, "state": "pending"}])
    server = _mcp_server(port)
    result = _call(server, mcp.McpTool.LIST_JOBS, {"status_group": "running_now"})
    data = result["structuredContent"]["data"]
    assert [row["id"] for row in data["jobs"]] == [_JOB_ID]
    assert port.calls[-1]["status_group"] == "running_now"
    assert data["filter"]["applied_by"] == "controller"
    assert data["filter"]["states"] == list(mcp.STATUS_GROUPS["running_now"])
    assert set(data["counts"]) == set(mcp.TASK_STATE_VOCABULARY)


def test_mcp_filters_the_page_when_the_port_cannot_forward_the_group() -> None:
    class _LegacyListPort:
        """Pre-round-8 adapter signature: no ``status_group`` keyword."""

        def list_tasks(
            self, *, status: str | None = None, limit: int = 50, workspace_id: str = ""
        ) -> dict[str, Any]:
            del status, limit, workspace_id
            return {
                "jobs": [
                    {"id": "a", "state": "running"},
                    {"id": "b", "state": "completed"},
                ]
            }

    server = _mcp_server(_LegacyListPort())  # type: ignore[arg-type]
    data = _call(server, mcp.McpTool.LIST_JOBS, {"status_group": "running_now"})[
        "structuredContent"
    ]["data"]
    assert [row["id"] for row in data["jobs"]] == ["a"]
    assert data["filter"]["applied_by"] == "mcp-page"
    assert set(data["counts"]) == set(mcp.TASK_STATE_VOCABULARY)
