"""``acp_list_jobs`` (U08 first slice) and the cancel already-terminal marker.

Agent feedback round 2 (2026-10-30): concurrent submissions were unmappable
without walking the run root, and cancelling an already-finished job answered
with a bare ``completed`` status that read as an error.
"""

from __future__ import annotations

from typing import Any

from acp_services.entrypoints import mcp


class _FakeCapabilities:
    def capabilities(self) -> dict[str, Any]:
        return {"workflows": [], "backends": []}


class _Unused:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"must not reach the {name} port")


class _FakeStatusList:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def get(self, reference: Any, *, workspace_id: str) -> Any:  # pragma: no cover
        raise AssertionError("list must not call get")

    def list_tasks(
        self, *, status: str | None = None, limit: int = 50, workspace_id: str = ""
    ) -> dict[str, Any]:
        self.calls.append({"status": status, "limit": limit})
        return self.payload


class _FakeCancel:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record

    def cancel(self, reference: str, *, workspace_id: str) -> dict[str, Any]:
        return self.record


def _server(status: Any = None, cancel: Any = None) -> mcp.McpServer:
    unused = _Unused()
    return mcp.McpServer(
        backends=mcp.McpBackends(
            capabilities=_FakeCapabilities(),
            preflight=unused,
            submit=unused,
            status=status or _Unused(),
            cancel=cancel or unused,
            artifacts=unused,
            analysis=unused,
        )
    )


def _call(server: mcp.McpServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
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


def test_list_jobs_is_a_registered_read_only_base_tool() -> None:
    assert mcp.McpTool.LIST_JOBS in mcp.TOOL_ALLOWLIST
    assert mcp.TOOL_REQUIRED_ACTION[mcp.McpTool.LIST_JOBS] == mcp.AccessAction.READ
    spec = {s["name"]: s for s in mcp._tool_specs()}[mcp.McpTool.LIST_JOBS]
    assert spec["inputSchema"]["additionalProperties"] is False


def test_list_jobs_forwards_the_query_and_adds_the_status_hint() -> None:
    port = _FakeStatusList({"jobs": [{"id": "j1", "state": "completed"}]})
    result = _call(_server(status=port), mcp.McpTool.LIST_JOBS, {"status": "completed", "limit": 5})
    assert result["isError"] is False
    data = result["structuredContent"]["data"]
    assert data["jobs"][0]["id"] == "j1"
    assert data["query_tool"] == mcp.McpTool.STATUS
    assert port.calls == [{"status": "completed", "limit": 5}]


def test_list_jobs_rejects_undeclared_fields_and_bad_limit() -> None:
    server = _server(status=_FakeStatusList({"jobs": []}))
    bad_field = _call(server, mcp.McpTool.LIST_JOBS, {"surprise": 1})
    assert bad_field["isError"] is True
    assert bad_field["structuredContent"]["error"]["params"]["rule"] == "unknown_field"
    bad_limit = _call(server, mcp.McpTool.LIST_JOBS, {"limit": "many"})
    assert bad_limit["isError"] is True
    assert bad_limit["structuredContent"]["error"]["params"]["field"] == "limit"


def test_cancel_on_a_terminal_job_says_already_terminal() -> None:
    server = _server(cancel=_FakeCancel({"state": "completed", "artifacts": ["x"]}))
    result = _call(server, mcp.McpTool.CANCEL, {"reference": "j1"})
    data = result["structuredContent"]["data"]
    assert data["cancel_requested"] is True
    assert data["cancelled"] is False
    assert data["already_terminal"] is True
    assert data["terminal_state"] == "completed"


def test_cancel_on_a_live_job_reports_cancelled() -> None:
    server = _server(cancel=_FakeCancel({"state": "running"}))
    data = _call(server, mcp.McpTool.CANCEL, {"reference": "j1"})["structuredContent"]["data"]
    assert data["cancelled"] is True
    assert "already_terminal" not in data


def test_list_jobs_summary_is_compact_by_default() -> None:
    """Round 3: a 20-job listing returned 213 KB of xyz/spec/result payloads."""
    rows = [
        {
            "id": "20261030_095632_004_PESsearch",
            "spec": {
                "workflow": "PESsearch",
                "name": "d05scan",
                "input": {"source": {"xyz_text": "3\n\nO 0 0 0\nH 1 0 0\nH 0 1 0" * 200}},
            },
            "state": "completed",
            "created_at": "2026-10-30T01:00:00Z",
            "result": {"huge": "x" * 50000},
            "work_dir": "/var/lib/acp/runs/uncategorized/mol_PESsearch__04",
            "error": "boom " * 120,
        }
    ]
    port = _FakeStatusList({"jobs": rows})
    result = _call(_server(status=port), mcp.McpTool.LIST_JOBS, {})
    row = result["structuredContent"]["data"]["jobs"][0]
    assert row["id"] == "20261030_095632_004_PESsearch"
    assert row["workflow"] == "PESsearch"
    assert row["name"] == "d05scan"
    assert row["directory_name"] == "mol_PESsearch__04"
    assert "spec" not in row and "result" not in row
    assert len(row["error"]) <= 301  # capped, not a log reader


def test_list_jobs_fields_full_passes_everything_through() -> None:
    port = _FakeStatusList({"jobs": [{"id": "j1", "spec": {"workflow": "x"}}]})
    result = _call(_server(status=port), mcp.McpTool.LIST_JOBS, {"fields": "full"})
    assert result["structuredContent"]["data"]["jobs"][0]["spec"] == {"workflow": "x"}


def test_list_jobs_rejects_unknown_fields_value() -> None:
    result = _call(
        _server(status=_FakeStatusList({"jobs": []})),
        mcp.McpTool.LIST_JOBS,
        {"fields": "wide"},
    )
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["params"]["rule"] == "invalid_enum"
