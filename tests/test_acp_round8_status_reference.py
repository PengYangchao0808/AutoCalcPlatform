"""Round-8 三.2 / K6: an unknown reference is NOT_FOUND, never "not ready".

The old mapping turned "I have never heard of this id" into
``resource_not_ready`` + ``retryable=True``: an agent was told to retry forever.
A status call for an unknown id now answers ``not_found`` (exit 2, not
retryable) and names the two recovery surfaces instead of sending the caller to
the run root.

Pinned here:

* MCP ``acp_status`` -> ``not_found`` for an unresolved reference (and a normal
  answer for a known one);
* the Controller 404 split: a task-status 404 whose body says the job row is
  missing becomes ``NotFoundError``; every other 404 keeps
  ``resource_not_ready``;
* the 404 body carries the read-only ``run_root_task_found`` probe (never an
  adoption, never a written ``task.json``).
"""

from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp_services.entrypoints import controller_client, mcp
from acp_services.entrypoints.controller_client import (
    NotFoundError,
    NotPreparedError,
    _error_from_http,
)

_UNKNOWN = "20261031_120000_999_frequency"


# --------------------------------------------------------------------------- #
# MCP status
# --------------------------------------------------------------------------- #


class _FakeCapabilities:
    def capabilities(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {"workflows": []}


class _Unused:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"must not reach the {name} port")


class _StatusPort:
    def __init__(self, record: Any) -> None:
        self.record = record

    def get(self, reference: Any, *, workspace_id: str) -> Any:
        del reference, workspace_id
        return self.record

    def list_tasks(self, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("status must not list")


def _server(port: _StatusPort) -> Any:
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


def _status(server: Any, reference: Any) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": mcp.McpTool.STATUS, "arguments": {"reference": reference}},
        }
    )
    assert response is not None and "result" in response, response
    return response["result"]


def test_unknown_reference_is_not_found_and_not_retryable() -> None:
    result = _status(_server(_StatusPort(None)), _UNKNOWN)
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == mcp.mo.ErrorCode.NOT_FOUND == "not_found"
    assert error["retryable"] is False
    assert error["category"] == "usage"
    assert error["params"]["rule"] == "unknown_reference"
    assert error["params"]["note"] == "job_id != operation_id"
    assert error["params"]["suggested_tools"] == [mcp.McpTool.LIST_JOBS]
    message = error["message"]
    assert "acp_list_jobs" in message
    assert "task.json.task_id" in message
    assert "job_id" in message and "operation_id" in message


def test_known_reference_still_answers() -> None:
    result = _status(_server(_StatusPort({"id": _UNKNOWN, "state": "completed"})), _UNKNOWN)
    assert result["isError"] is False
    data = result["structuredContent"]["data"]
    assert data["state"] == "completed"
    assert data["reference"] == _UNKNOWN


def test_not_found_is_non_retryable_in_the_catalog() -> None:
    spec = mcp.mo.error_spec(mcp.mo.ErrorCode.NOT_FOUND)
    assert spec.retryable is False
    assert spec.code == "not_found"
    assert mcp.mo.exit_code_for("not_found") == mcp.mo.ExitCode.USAGE


# --------------------------------------------------------------------------- #
# Controller 404 classification split
# --------------------------------------------------------------------------- #


def _http_error(status: int, url: str, body: Any) -> urllib.error.HTTPError:
    raw = json.dumps(body).encode("utf-8")
    return urllib.error.HTTPError(url, status, "error", {}, io.BytesIO(raw))


def test_missing_job_row_on_the_status_read_is_not_found() -> None:
    exc = _error_from_http(
        _http_error(
            404,
            "http://127.0.0.1:8765/api/v1/jobs/" + _UNKNOWN,
            {
                "detail": {
                    "code": "job_row_missing",
                    "message": f"Job not found: {_UNKNOWN}",
                    "params": {"job_id": _UNKNOWN, "run_root_task_found": False},
                }
            },
        )
    )
    assert isinstance(exc, NotFoundError)
    assert isinstance(exc, NotPreparedError)  # every existing except-clause still works
    assert exc.code == "not_found"
    assert exc.retryable is False


def test_historical_prose_404_on_the_status_read_is_not_found() -> None:
    exc = _error_from_http(
        _http_error(
            404,
            f"http://127.0.0.1:8765/api/v1/jobs/{_UNKNOWN}",
            {"detail": f"Job not found: {_UNKNOWN}"},
        )
    )
    assert isinstance(exc, NotFoundError)


def test_not_prepared_404_keeps_resource_not_ready() -> None:
    exc = _error_from_http(
        _http_error(
            404,
            "http://127.0.0.1:8765/api/v1/jobs/x/prepared/artifact",
            {"detail": {"code": "resource_not_ready", "message": "not prepared"}},
        )
    )
    assert isinstance(exc, NotPreparedError)
    assert not isinstance(exc, NotFoundError)
    assert exc.code == "resource_not_ready"
    assert exc.retryable is True


def test_control_404_on_another_route_keeps_its_classification() -> None:
    """The split is scoped to the task-status read, not to every job 404."""
    exc = _error_from_http(
        _http_error(
            404, "http://127.0.0.1:8765/api/v1/jobs/x/cancel", {"detail": "Job not found: x"}
        )
    )
    assert not isinstance(exc, NotFoundError)
    assert exc.code == "resource_not_ready"


def test_425_is_still_resource_not_ready() -> None:
    exc = _error_from_http(
        _http_error(425, "http://127.0.0.1:8765/api/v1/jobs/x", {"detail": "too early"})
    )
    assert exc.code == "resource_not_ready"


# --------------------------------------------------------------------------- #
# the read-only 404 diagnostic probe
# --------------------------------------------------------------------------- #


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


def test_unknown_id_404_reports_the_probe_and_keeps_the_code(client: TestClient) -> None:
    response = client.get(f"/api/v1/jobs/{_UNKNOWN}")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "job_row_missing"
    assert detail["message"] == f"Job not found: {_UNKNOWN}"
    assert detail["params"]["job_id"] == _UNKNOWN
    assert detail["params"]["run_root_task_found"] is False
    assert "acp_list_jobs" in detail["hint"]


def test_probe_reports_an_orphan_task_directory_without_adopting_it(
    client: TestClient, tmp_path: Path
) -> None:
    task_dir = tmp_path / "uncategorized" / "mol_frequency__3"
    task_dir.mkdir(parents=True)
    task_json = task_dir / "task.json"
    task_json.write_text(
        json.dumps({"task_id": _UNKNOWN, "workflow": "frequency"}), encoding="utf-8"
    )
    before = sorted(path.name for path in task_dir.iterdir())

    response = client.get(f"/api/v1/jobs/{_UNKNOWN}")
    assert response.status_code == 404
    assert response.json()["detail"]["params"]["run_root_task_found"] is True

    # read-only: no adoption, no new marker, no rewritten task.json
    assert sorted(path.name for path in task_dir.iterdir()) == before
    assert json.loads(task_json.read_text(encoding="utf-8"))["task_id"] == _UNKNOWN
    assert client.get(f"/api/v1/jobs/{_UNKNOWN}").status_code == 404


def test_probe_is_not_run_for_a_non_task_id_shape(client: TestClient) -> None:
    detail = client.get("/api/v1/jobs/not-a-task-id").json()["detail"]
    assert detail["params"]["run_root_task_found"] is False


def test_controller_status_client_surfaces_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client half of the chain: a Controller 404 becomes NotFoundError."""

    def _boom(self: Any, method: str, path: str, payload: Any = None, **kwargs: Any) -> Any:
        del method, payload, kwargs
        raise _error_from_http(
            _http_error(
                404,
                "http://127.0.0.1:8765" + path,
                {
                    "detail": {
                        "code": "job_row_missing",
                        "message": f"Job not found: {_UNKNOWN}",
                        "params": {"job_id": _UNKNOWN},
                    }
                },
            )
        )

    monkeypatch.setattr(controller_client.ControllerClient, "_request_json", _boom)
    client = controller_client.ControllerClient("http://127.0.0.1:8765")
    with pytest.raises(NotFoundError):
        client.status(_UNKNOWN)
