# ruff: noqa: F811 - the imported pytest fixture is requested by name
"""Round-9 M9: ``acp_cancel`` accepts an optional audit-only ``reason``.

The external agent sent a ``reason`` and got ``unknown_field``: the tool surface
and the client's expectation disagreed.  Round-9 accepts the field, bounded to
200 characters, and treats it as **audit metadata only**:

* it is echoed back in the acknowledgement as ``cancel_reason``;
* it is never forwarded to the Controller (whose cancel route does not accept
  it) and never changes the control path, CAS or idempotency;
* without it the acknowledgement keeps exactly its previous shape;
* a non-string / over-long value is a typed ``input_invalid`` with
  ``params.field``/``params.rule``.
"""

from __future__ import annotations

from typing import Any

import pytest

from acp_services.entrypoints import mcp
from tests.test_acp2_runtime_mcp_backends import (  # noqa: F401 - pytest fixture
    _SUBMIT,
    _client,
    _LiveController,
    _runtime,
    _tool,
    live_controller,
)


class _CancelPort:
    """Records what reached the control path (the Controller never sees reason)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def cancel(self, reference: str, *, workspace_id: str) -> dict[str, Any]:
        self.calls.append((reference, workspace_id))
        return {"job_id": reference, "status": "cancelled", "attempt": 1}


def _server(port: Any) -> mcp.McpServer:
    return mcp.McpServer(
        backends=mcp.McpBackends(
            capabilities=object(),  # type: ignore[arg-type]
            preflight=object(),  # type: ignore[arg-type]
            submit=object(),  # type: ignore[arg-type]
            status=object(),  # type: ignore[arg-type]
            cancel=port,
            artifacts=object(),  # type: ignore[arg-type]
            analysis=object(),  # type: ignore[arg-type]
        )
    )


def _call(server: mcp.McpServer, arguments: dict[str, Any]) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": mcp.McpTool.CANCEL, "arguments": arguments},
        }
    )
    assert response is not None and "result" in response, response
    return response["result"]


# ---------------------------------------------------------------------------
# Schema surface
# ---------------------------------------------------------------------------


def test_the_cancel_schema_declares_a_bounded_reason() -> None:
    reason = mcp._CANCEL_SCHEMA["properties"]["reason"]
    assert reason["type"] == "string"
    assert reason["maxLength"] == mcp._CANCEL_REASON_MAX_LENGTH == 200
    assert "audit" in reason["description"]


def test_the_declared_reason_reaches_tools_list() -> None:
    server = _server(_CancelPort())
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response is not None
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    schema = tools[mcp.McpTool.CANCEL]["inputSchema"]
    assert "reason" in schema["properties"]
    assert schema["properties"]["reason"]["maxLength"] == 200


# ---------------------------------------------------------------------------
# Happy path: echoed as cancel_reason, never forwarded
# ---------------------------------------------------------------------------


def test_cancel_with_a_reason_echoes_it_and_keeps_the_ack_shape() -> None:
    port = _CancelPort()
    server = _server(port)
    plain = _call(server, {"reference": "job-1"})
    with_reason = _call(server, {"reference": "job-1", "reason": "superseded by the TS rerun"})

    assert plain["isError"] is False and with_reason["isError"] is False
    plain_data = plain["structuredContent"]["data"]
    reason_data = with_reason["structuredContent"]["data"]
    assert reason_data["cancel_reason"] == "superseded by the TS rerun"
    assert set(reason_data) - {"cancel_reason"} == set(plain_data)
    for key, value in plain_data.items():
        assert reason_data[key] == value


def test_the_reason_never_reaches_the_control_path() -> None:
    port = _CancelPort()
    _call(_server(port), {"reference": "job-1", "reason": "audit only"})
    assert [call[0] for call in port.calls] == ["job-1"]
    assert len(port.calls) == 1


def test_a_blank_reason_is_treated_as_absent() -> None:
    result = _call(_server(_CancelPort()), {"reference": "job-1", "reason": "   "})
    assert result["isError"] is False
    assert "cancel_reason" not in result["structuredContent"]["data"]


def test_the_reason_is_bounded_at_two_hundred_characters() -> None:
    accepted = _call(_server(_CancelPort()), {"reference": "job-1", "reason": "x" * 200})
    assert accepted["isError"] is False
    assert accepted["structuredContent"]["data"]["cancel_reason"] == "x" * 200

    rejected = _call(_server(_CancelPort()), {"reference": "job-1", "reason": "x" * 201})
    assert rejected["isError"] is True
    error = rejected["structuredContent"]["error"]
    assert error["code"] == "input_invalid"
    assert error["params"]["field"] == "reason"
    assert error["params"]["rule"] == "max_length"
    assert error["params"]["max_length"] == 200


def test_a_non_string_reason_is_a_typed_input_error() -> None:
    for value in (123, {"text": "why"}, ["why"], True):
        result = _call(_server(_CancelPort()), {"reference": "job-1", "reason": value})
        assert result["isError"] is True, value
        error = result["structuredContent"]["error"]
        assert error["code"] == "input_invalid"
        assert error["params"]["field"] == "reason"
        assert error["params"]["rule"] == "invalid_type"


# ---------------------------------------------------------------------------
# Unknown fields still refuse, and now name the legal surface
# ---------------------------------------------------------------------------


def test_an_unknown_field_reports_the_allowed_surface() -> None:
    result = _call(
        _server(_CancelPort()),
        {"reference": "job-1", "reason": "why", "cancelled_by": "agent"},
    )
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["params"]["rule"] == "unknown_field"
    assert error["params"]["field"] == "cancelled_by"
    allowed = error["params"]["allowed"]
    assert "reference" in allowed
    assert "expected_revision" in allowed
    assert "reason" in allowed


# ---------------------------------------------------------------------------
# Real Controller: the audited cancel is still a normal cancel
# ---------------------------------------------------------------------------


def test_cancel_with_reason_over_the_real_controller(
    live_controller: _LiveController,
) -> None:
    created = dict(_client(live_controller).submit({**_SUBMIT, "name": "round9-cancel"}))
    job_id = str(created["job_id"])

    result = _tool(
        _runtime(live_controller),
        "acp_cancel",
        {"reference": job_id, "reason": "round-9 audit-only reason"},
    )
    assert result["isError"] is False, result
    data = result["structuredContent"]["data"]
    assert data["job_id"] == job_id
    assert data["cancel_reason"] == "round-9 audit-only reason"
    assert data["cancel_requested"] is True
    assert data["state"] in {"cancelled", "cancelling"}


if __name__ == "__main__":  # pragma: no cover - manual probe
    pytest.main([__file__, "-q"])
