"""W-06 — MCP batch parameters are additive, bounded and per-item isolated.

Two read tools grow a mutually-exclusive batch form:

* ``acp_status(job_ids=[...])`` — 1..50 job ids, one Controller
  ``GET /api/v1/jobs?ids=`` round trip through ``ControllerClient.status_many``;
* ``acp_read_artifact(artifact_refs=[...])`` — 1..20 frozen ArtifactRefs, each
  read through the existing single-read port (bodies are never merged).

The single-argument path and its response shape are unchanged.  A batch answers
``{"tool", "items", "count"}`` where every item carries ``status`` plus either
``data`` or ``error``: an unknown id or a failed read never drops the rest of
the batch, and a batch never gains prepare/fetch power.

Pinned here:

* schema truth (``anyOf`` choice, caps, permissive status vs strict read);
* the three-id status batch (2 ok + 1 not_found) over a fake port;
* the degraded per-item ``get`` fallback when a status port has no ``get_many``;
* the two-ref read batch with one refused item;
* single-argument regressions for both tools;
* typed refusals (both/neither/oversized/blank, legacy unknown field);
* the base64 batch refusal that protects the single-read verbatim body;
* ``ControllerClient.status_many`` = ONE request; ``_ControllerStatus.get_many``
  consumes it and leaves unknown ids absent (never fabricated).
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from typing import Any

import pytest

from acp.services_mcp_bootstrap import _ControllerStatus
from acp_services.entrypoints import controller_client, mcp
from acp_services.entrypoints import machine_output as mo
from acp_services.entrypoints.controller_client import InvalidParameterError, RetryableError

_JOB_A = "20261103_000000_001_alpha"
_JOB_B = "20261103_000000_002_beta"
_JOB_MISSING = "20261103_000000_999_missing"


def _artifact_ref(artifact_id: str, *, task_id: str = _JOB_A) -> dict[str, Any]:
    # Digest must stay a real ``sha256:<64 hex>`` token; derive one hex char.
    hex_char = "abcdef0123456789"[len(artifact_id) % 16]
    return {
        "task_id": task_id,
        "attempt": 1,
        "artifact_id": artifact_id,
        "generation": 1,
        "digest": "sha256:" + hex_char * 64,
    }


_REF_ONE = _artifact_ref("catalog")
_REF_TWO = _artifact_ref("energy", task_id=_JOB_B)


class _Unused:
    """Any touch of a port this test does not exercise is a bug, not a mock."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"must not reach the {name} port")


class _StatusPort:
    """Fake status port exposing only the frozen single-reference ``get``."""

    def __init__(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        self.records = dict(records)
        self.get_calls: list[str] = []

    def get(self, reference: str, *, workspace_id: str) -> Mapping[str, Any] | None:
        del workspace_id
        self.get_calls.append(reference)
        return self.records.get(reference)


class _StatusPortWithMany(_StatusPort):
    """Status port with the W-06 ``get_many`` batch surface (preferred path)."""

    def __init__(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        super().__init__(records)
        self.many_calls: list[list[str]] = []

    def get_many(
        self, references: list[str], *, workspace_id: str = ""
    ) -> dict[str, Mapping[str, Any]]:
        del workspace_id
        self.many_calls.append(list(references))
        return {task_id: self.records[task_id] for task_id in references if task_id in self.records}


class _ArtifactPort:
    """Fake artifact port: one prepared body, one refused reference."""

    def __init__(self) -> None:
        self.read_calls: list[dict[str, Any]] = []

    def read(self, request: Mapping[str, Any], *, workspace_id: str) -> Mapping[str, Any]:
        del workspace_id
        payload = dict(request)
        self.read_calls.append(payload)
        ref = payload["artifact_ref"]
        if ref.get("artifact_id") == "missing":
            raise mcp.McpToolError(
                mo.ErrorCode.RESOURCE_NOT_READY,
                "artifact is not prepared",
                params={"field": "artifact_ref", "rule": "not_prepared"},
                retryable=True,
            )
        return {"resource": ref.get("artifact_id"), "data": "prepared"}

    def prepare(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ARG002
        raise AssertionError("a read batch must never prepare/fetch")


def _server(
    *,
    status: Any = None,
    artifacts: Any = None,
) -> mcp.McpServer:
    unused = _Unused()
    return mcp.McpServer(
        backends=mcp.McpBackends(
            capabilities=unused,
            preflight=unused,
            submit=unused,
            status=status or _StatusPort({}),
            cancel=unused,
            artifacts=artifacts or _ArtifactPort(),
            analysis=unused,
        )
    )


def _call_tool(server: mcp.McpServer, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": dict(arguments)},
        }
    )
    assert response is not None and "result" in response, response
    return response["result"]


def _declared(name: str) -> dict[str, Any]:
    return next(tool for tool in mcp._tool_specs() if tool["name"] == name)["inputSchema"]


_STATUS_RECORDS = {
    _JOB_A: {"id": _JOB_A, "state": "completed", "attempt": 1},
    _JOB_B: {"id": _JOB_B, "state": "running", "attempt": 1},
}


# --------------------------------------------------------------------------- #
# Declared schema (the advertised half of the contract)
# --------------------------------------------------------------------------- #


def test_status_schema_offers_the_batch_choice_without_dropping_the_old_one() -> None:
    schema = _declared(mcp.McpTool.STATUS)
    assert "required" not in schema
    assert schema["anyOf"] == [{"required": ["reference"]}, {"required": ["job_ids"]}]
    # U2: historical status reads stay permissive about unknown query fields.
    assert schema["additionalProperties"] is True
    job_ids = schema["properties"]["job_ids"]
    assert job_ids["type"] == "array"
    assert job_ids["minItems"] == 1
    assert job_ids["maxItems"] == mcp.MAX_BATCH_JOB_IDS
    assert job_ids["items"] == {"type": "string", "minLength": 1}
    # The old single argument is still declared exactly as before.
    assert schema["properties"]["reference"]["oneOf"] == [{"type": "string"}, {"type": "object"}]


def test_read_schema_offers_the_batch_choice_with_the_frozen_identity_tuple() -> None:
    schema = _declared(mcp.McpTool.READ_ARTIFACT)
    assert "required" not in schema
    assert schema["anyOf"] == [{"required": ["artifact_ref"]}, {"required": ["artifact_refs"]}]
    assert schema["additionalProperties"] is False
    refs = schema["properties"]["artifact_refs"]
    assert refs["type"] == "array"
    assert refs["minItems"] == 1
    assert refs["maxItems"] == mcp.MAX_BATCH_ARTIFACT_REFS
    # Every batch entry is the same frozen tuple, never a summary.
    assert refs["items"] == schema["properties"]["artifact_ref"]
    assert set(refs["items"]["required"]) == {
        "task_id",
        "attempt",
        "artifact_id",
        "generation",
        "digest",
    }


def test_tool_descriptions_advertise_the_batch_forms() -> None:
    specs = {tool["name"]: tool for tool in mcp._tool_specs()}
    assert "job_ids" in specs[mcp.McpTool.STATUS]["description"]
    assert "artifact_refs" in specs[mcp.McpTool.READ_ARTIFACT]["description"]


# --------------------------------------------------------------------------- #
# acp_status batch
# --------------------------------------------------------------------------- #


def test_status_batch_reads_three_ids_with_per_item_isolation() -> None:
    port = _StatusPortWithMany(_STATUS_RECORDS)
    result = _call_tool(
        _server(status=port),
        mcp.McpTool.STATUS,
        {"job_ids": [_JOB_A, _JOB_B, _JOB_MISSING]},
    )
    assert result["isError"] is False
    data = result["structuredContent"]["data"]
    assert data["tool"] == mcp.McpTool.STATUS
    assert data["count"] == 3
    assert [item["reference"] for item in data["items"]] == [_JOB_A, _JOB_B, _JOB_MISSING]
    first, second, missing = data["items"]
    assert first["status"] == "ok" and first["data"]["state"] == "completed"
    assert first["data"]["reference"] == _JOB_A
    assert second["status"] == "ok" and second["data"]["state"] == "running"
    assert missing["status"] == "error"
    assert missing["error"]["code"] == mo.ErrorCode.NOT_FOUND
    assert missing["error"]["retryable"] is False
    assert missing["error"]["params"]["rule"] == "unknown_reference"
    assert "error" not in first and "data" not in missing
    # ONE port round trip, and the per-item get path was never touched.
    assert port.many_calls == [[_JOB_A, _JOB_B, _JOB_MISSING]]
    assert port.get_calls == []
    assert "degraded" not in data


def test_status_batch_falls_back_to_per_item_get_and_records_degradation() -> None:
    port = _StatusPort(_STATUS_RECORDS)
    result = _call_tool(
        _server(status=port),
        mcp.McpTool.STATUS,
        {"job_ids": [_JOB_A, _JOB_MISSING, _JOB_B]},
    )
    assert result["isError"] is False
    data = result["structuredContent"]["data"]
    assert data["count"] == 3
    statuses = [item["status"] for item in data["items"]]
    assert statuses == ["ok", "error", "ok"]
    assert data["items"][1]["error"]["code"] == mo.ErrorCode.NOT_FOUND
    assert port.get_calls == [_JOB_A, _JOB_MISSING, _JOB_B]
    assert data["degraded"] == {
        "reason": "status_port_get_many_missing",
        "fallback": "per_item_get",
    }


def test_status_batch_dedupes_ids_before_the_port_call() -> None:
    port = _StatusPortWithMany(_STATUS_RECORDS)
    result = _call_tool(
        _server(status=port),
        mcp.McpTool.STATUS,
        {"job_ids": [_JOB_A, _JOB_A, _JOB_B]},
    )
    data = result["structuredContent"]["data"]
    assert [item["reference"] for item in data["items"]] == [_JOB_A, _JOB_B]
    assert port.many_calls == [[_JOB_A, _JOB_B]]


def test_status_single_reference_shape_is_unchanged() -> None:
    port = _StatusPortWithMany(_STATUS_RECORDS)
    result = _call_tool(_server(status=port), mcp.McpTool.STATUS, {"reference": _JOB_A})
    assert result["isError"] is False
    data = result["structuredContent"]["data"]
    assert data["state"] == "completed"
    assert data["reference"] == _JOB_A
    # The old flat shape is preserved: no batch envelope on the single path.
    assert "items" not in data and "count" not in data
    assert port.get_calls == [_JOB_A] and port.many_calls == []


def test_status_batch_selector_refusals_never_reach_the_port() -> None:
    port = _StatusPortWithMany(_STATUS_RECORDS)
    server = _server(status=port)

    both = _call_tool(server, mcp.McpTool.STATUS, {"reference": _JOB_A, "job_ids": [_JOB_B]})
    assert both["isError"] is True
    assert both["structuredContent"]["error"]["params"]["rule"] == "conflicting_arguments"

    neither = _call_tool(server, mcp.McpTool.STATUS, {})
    assert neither["isError"] is True
    error = neither["structuredContent"]["error"]
    assert error["params"]["rule"] == "missing_required_field"
    assert error["params"]["fields"] == ["reference", "job_ids"]

    empty = _call_tool(server, mcp.McpTool.STATUS, {"job_ids": []})
    assert empty["structuredContent"]["error"]["params"]["rule"] == "min_items"

    oversized = _call_tool(
        server, mcp.McpTool.STATUS, {"job_ids": [f"job-{index}" for index in range(51)]}
    )
    assert oversized["structuredContent"]["error"]["params"]["rule"] == "max_items"

    blank = _call_tool(server, mcp.McpTool.STATUS, {"job_ids": [_JOB_A, "  "]})
    assert blank["structuredContent"]["error"]["params"]["rule"] == "invalid_type"
    assert blank["structuredContent"]["error"]["params"]["field"] == "job_ids[1]"

    assert port.get_calls == [] and port.many_calls == []


# --------------------------------------------------------------------------- #
# acp_read_artifact batch
# --------------------------------------------------------------------------- #


def test_read_batch_reads_each_reference_and_isolates_failures() -> None:
    port = _ArtifactPort()
    missing = _artifact_ref("missing")
    result = _call_tool(
        _server(artifacts=port),
        mcp.McpTool.READ_ARTIFACT,
        {"artifact_refs": [_REF_ONE, missing, _REF_TWO]},
    )
    assert result["isError"] is False
    data = result["structuredContent"]["data"]
    assert data["tool"] == mcp.McpTool.READ_ARTIFACT
    assert data["count"] == 3
    first, failed, third = data["items"]
    assert first["status"] == "ok" and first["artifact_ref"] == _REF_ONE
    assert first["data"]["data"] == "prepared"
    assert failed["status"] == "error"
    assert failed["error"]["code"] == mo.ErrorCode.RESOURCE_NOT_READY
    assert failed["error"]["params"]["rule"] == "not_prepared"
    assert third["status"] == "ok" and third["artifact_ref"] == _REF_TWO
    # Bodies are never merged: one normal read per reference, no prepare/fetch.
    assert [call["artifact_ref"] for call in port.read_calls] == [_REF_ONE, missing, _REF_TWO]


def test_read_batch_forwards_shared_bounds_per_item() -> None:
    port = _ArtifactPort()
    _call_tool(
        _server(artifacts=port),
        mcp.McpTool.READ_ARTIFACT,
        {"artifact_refs": [_REF_ONE, _REF_TWO], "max_bytes": 128, "encoding": "identity"},
    )
    assert len(port.read_calls) == 2
    for call in port.read_calls:
        assert call["max_bytes"] == 128
        assert call["encoding"] == "identity"
        assert "artifact_refs" not in call


def test_read_batch_refuses_base64_to_protect_the_verbatim_body() -> None:
    port = _ArtifactPort()
    result = _call_tool(
        _server(artifacts=port),
        mcp.McpTool.READ_ARTIFACT,
        {"artifact_refs": [_REF_ONE], "encoding": "base64"},
    )
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == mo.ErrorCode.UNSUPPORTED_CAPABILITY
    assert error["params"]["rule"] == "batch_binary_requires_single_read"
    assert port.read_calls == []


def test_read_single_reference_shape_is_unchanged() -> None:
    port = _ArtifactPort()
    result = _call_tool(
        _server(artifacts=port), mcp.McpTool.READ_ARTIFACT, {"artifact_ref": _REF_ONE}
    )
    assert result["isError"] is False
    data = result["structuredContent"]["data"]
    assert data["data"] == "prepared"
    assert data["resource"] == "catalog"
    assert "items" not in data and "count" not in data
    assert port.read_calls == [{"artifact_ref": _REF_ONE}]


def test_read_batch_selector_and_ref_refusals_never_reach_the_port() -> None:
    port = _ArtifactPort()
    server = _server(artifacts=port)

    both = _call_tool(
        server,
        mcp.McpTool.READ_ARTIFACT,
        {"artifact_ref": _REF_ONE, "artifact_refs": [_REF_TWO]},
    )
    assert both["structuredContent"]["error"]["params"]["rule"] == "conflicting_arguments"

    neither = _call_tool(server, mcp.McpTool.READ_ARTIFACT, {})
    assert neither["structuredContent"]["error"]["params"]["rule"] == "missing_required_field"

    empty = _call_tool(server, mcp.McpTool.READ_ARTIFACT, {"artifact_refs": []})
    assert empty["structuredContent"]["error"]["params"]["rule"] == "min_items"

    oversized = _call_tool(
        server,
        mcp.McpTool.READ_ARTIFACT,
        {"artifact_refs": [_artifact_ref(f"a{index}") for index in range(21)]},
    )
    assert oversized["structuredContent"]["error"]["params"]["rule"] == "max_items"

    # A partial batch identity tuple is refused with its indexed field name.
    partial = _artifact_ref("catalog")
    partial.pop("digest")
    incomplete = _call_tool(server, mcp.McpTool.READ_ARTIFACT, {"artifact_refs": [partial]})
    incomplete_error = incomplete["structuredContent"]["error"]
    assert incomplete_error["code"] == mo.ErrorCode.INPUT_INVALID
    assert incomplete_error["params"]["rule"] == "missing_artifact_ref"
    assert incomplete_error["params"]["field"] == "artifact_refs[0].digest"

    # The strict read table is unchanged for undeclared fields (legacy job_id).
    legacy = _call_tool(
        server, mcp.McpTool.READ_ARTIFACT, {"job_id": "j1", "relative_path": "a.json"}
    )
    assert legacy["structuredContent"]["error"]["params"]["rule"] == "unknown_field"

    assert port.read_calls == []


# --------------------------------------------------------------------------- #
# Controller transport: one request for a batch
# --------------------------------------------------------------------------- #


def _jobs_response(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"jobs": rows, "counts": {"completed": 1, "running": 1}}


def test_controller_client_status_many_is_exactly_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = controller_client.ControllerClient("http://controller.test")
    calls: list[tuple[str, str]] = []

    def fake_request_json(
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        del payload, timeout
        calls.append((method, path))
        return _jobs_response(
            [
                {"id": _JOB_A, "state": "completed", "attempt": 1},
                {"id": _JOB_B, "state": "running", "attempt": 1},
            ]
        )

    monkeypatch.setattr(client, "_request_json", fake_request_json)
    snapshots = client.status_many([_JOB_A, _JOB_B, _JOB_A, " "])

    assert len(calls) == 1
    method, path = calls[0]
    assert method == "GET"
    assert path.startswith("/api/v1/jobs?")
    # One comma-separated selector, duplicates/blanks normalised away.
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    assert query["ids"] == [f"{_JOB_A},{_JOB_B}"]
    assert set(snapshots) == {_JOB_A, _JOB_B}
    assert snapshots[_JOB_A].state == "completed"
    assert snapshots[_JOB_B].state == "running"
    # A requested id the Controller omitted stays absent — never fabricated.
    assert _JOB_MISSING not in snapshots


def test_controller_client_status_many_refuses_empty_and_oversized_sets() -> None:
    client = controller_client.ControllerClient("http://controller.test")
    with pytest.raises(InvalidParameterError):
        client.status_many([" "])
    with pytest.raises(InvalidParameterError):
        client.status_many([f"job-{index}" for index in range(101)])


def test_controller_status_get_many_consumes_the_single_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = controller_client.ControllerClient("http://controller.test")
    calls: list[str] = []

    def fake_request_json(
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        del payload, timeout
        calls.append(f"{method} {path}")
        return _jobs_response([{"id": _JOB_A, "state": "completed", "attempt": 1}])

    monkeypatch.setattr(client, "_request_json", fake_request_json)
    status = _ControllerStatus(client)

    projections = status.get_many([_JOB_A, _JOB_MISSING, _JOB_A])

    assert len(calls) == 1 and calls[0].startswith("GET /api/v1/jobs?")
    assert set(projections) == {_JOB_A, _JOB_MISSING}
    assert projections[_JOB_A] is not None
    assert projections[_JOB_A]["state"] == "completed"
    assert projections[_JOB_A]["reference"] == _JOB_A
    assert projections[_JOB_MISSING] is None


def test_controller_status_get_many_maps_transport_failure_to_a_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = controller_client.ControllerClient("http://controller.test")

    def refuse(*args: Any, **kwargs: Any) -> Any:  # noqa: ARG001
        raise RetryableError("Controller unreachable", code="backend_unavailable")

    monkeypatch.setattr(client, "status_many", refuse)
    status = _ControllerStatus(client)
    with pytest.raises(mcp.McpToolError) as raised:
        status.get_many([_JOB_A])
    assert raised.value.code == mo.ErrorCode.BACKEND_UNAVAILABLE
    assert raised.value.retryable is True
