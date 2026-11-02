# ruff: noqa: F811 - the imported pytest fixture is requested by name
"""Round-9 M8: ``contract_sections`` is wired through the MCP adapter.

The CORE workflow contract advertises ``next.arguments = {detail: summary}`` and
``contract_sections`` as the way to fetch one section, but the stdio adapter
(``acp.services_mcp_bootstrap._ControllerCapabilities``) did not accept the
keyword, so the MCP port gate refused every such call with
``capability_not_wired`` — "advertised but not wired".  Round-9 adds the
parameter and forwards it only when the caller asked for it.

Pinned here:

* the adapter forwards ``contract_sections`` (and passes the other arguments
  through unchanged);
* ``mcp._port_accepts`` now accepts the keyword, so the gate passes;
* a no-argument call still uses the plain ``GET /api/capabilities`` read;
* over the real Controller, ``contract_sections="constraints"`` actually returns
  that section — the promise the contract makes.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.test_acp2_runtime_mcp_backends import (  # noqa: F401 - pytest fixture
    _LiveController,
    _runtime,
    _tool,
    live_controller,
)


class _RecordingClient:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.payload = payload or {"workflows": ["singlepoint"]}

    def capabilities(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        return _Snapshot(dict(self.payload))


class _Snapshot:
    """Minimal stand-in for ``controller_client.Capabilities``."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw


# ---------------------------------------------------------------------------
# Adapter layer
# ---------------------------------------------------------------------------


def test_adapter_forwards_contract_sections() -> None:
    from acp.services_mcp_bootstrap import _ControllerCapabilities

    client = _RecordingClient(
        {
            "workflow_contract": {"workflow": "BatchOptimize", "method": {"levels": []}},
            "contract_sections": "method.levels",
        }
    )
    data = _ControllerCapabilities(client).capabilities(
        workflow="BatchOptimize", contract_sections="method.levels"
    )
    assert client.calls == [
        {"workflow": "BatchOptimize", "detail": None, "contract_sections": "method.levels"}
    ]
    assert data["contract_sections"] == "method.levels"
    assert data["workflow_contract"]["workflow"] == "BatchOptimize"


def test_adapter_forwards_none_without_changing_the_call() -> None:
    from acp.services_mcp_bootstrap import _ControllerCapabilities

    client = _RecordingClient()
    _ControllerCapabilities(client).capabilities()
    assert client.calls == [{"workflow": None, "detail": None, "contract_sections": None}]


def test_no_argument_call_still_uses_the_plain_get() -> None:
    """The all-None forwarding must not turn discovery into a POST body."""
    from acp_services.entrypoints.controller_client import ControllerClient

    client = ControllerClient("http://127.0.0.1:1")
    calls: list[tuple[str, str]] = []

    def _fake_request(method: str, path: str, payload: Any = None, **_: Any) -> Any:
        calls.append((method, path))
        assert payload is None, "a no-argument discovery must send no body"
        return {"workflows": ["singlepoint"]}

    client._request_json = _fake_request  # type: ignore[method-assign]
    client.capabilities(workflow=None, detail=None, contract_sections=None)
    assert calls == [("GET", "/api/capabilities")]


def test_the_port_gate_accepts_the_real_adapter() -> None:
    from acp.services_mcp_bootstrap import _ControllerCapabilities
    from acp_services.entrypoints import mcp

    adapter = _ControllerCapabilities(_RecordingClient())
    assert mcp._port_accepts(adapter, "contract_sections", method="capabilities") is True


def test_the_mcp_tool_no_longer_refuses_capability_not_wired() -> None:
    from acp.services_mcp_bootstrap import _ControllerCapabilities
    from acp_services.entrypoints import mcp

    adapter = _ControllerCapabilities(
        _RecordingClient(
            {
                "workflow_contract": {
                    "workflow": "BatchOptimize",
                    "constraints": ["cores <= 8"],
                },
                "contract_sections": "constraints",
            }
        )
    )
    server = mcp.McpServer(
        backends=mcp.McpBackends(
            capabilities=adapter,
            preflight=object(),  # type: ignore[arg-type]
            submit=object(),  # type: ignore[arg-type]
            status=object(),  # type: ignore[arg-type]
            cancel=object(),  # type: ignore[arg-type]
            artifacts=object(),  # type: ignore[arg-type]
            analysis=object(),  # type: ignore[arg-type]
        )
    )
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": mcp.McpTool.CAPABILITIES,
                "arguments": {"workflow": "BatchOptimize", "contract_sections": "constraints"},
            },
        }
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is False, result
    assert result["structuredContent"]["data"]["contract_sections"] == "constraints"


def test_an_invalid_section_is_still_a_typed_input_error() -> None:
    from acp.services_mcp_bootstrap import _ControllerCapabilities
    from acp_services.entrypoints import mcp

    server = mcp.McpServer(
        backends=mcp.McpBackends(
            capabilities=_ControllerCapabilities(_RecordingClient()),
            preflight=object(),  # type: ignore[arg-type]
            submit=object(),  # type: ignore[arg-type]
            status=object(),  # type: ignore[arg-type]
            cancel=object(),  # type: ignore[arg-type]
            artifacts=object(),  # type: ignore[arg-type]
            analysis=object(),  # type: ignore[arg-type]
        )
    )
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": mcp.McpTool.CAPABILITIES,
                "arguments": {"workflow": "BatchOptimize", "contract_sections": "nope"},
            },
        }
    )
    assert response is not None
    error = response["result"]["structuredContent"]["error"]
    assert error["code"] == "input_invalid"
    assert error["params"]["field"] == "contract_sections"
    assert error["params"]["rule"] == "invalid_enum"


# ---------------------------------------------------------------------------
# Real Controller: the advertised section actually comes back
# ---------------------------------------------------------------------------


def test_contract_sections_work_over_the_real_controller(
    live_controller: _LiveController,
) -> None:
    runtime = _runtime(live_controller)
    result = _tool(
        runtime,
        "acp_capabilities",
        {"workflow": "Confsearch", "contract_sections": "constraints"},
    )
    assert result["isError"] is False, result
    data = result["structuredContent"]["data"]
    assert data["contract_sections"] == "constraints"
    block = data["workflow_contract"]
    assert block["constraints"]
    # A section request is bounded: the other sections are not shipped.
    assert "input" not in block
    assert "examples" not in block


def test_the_core_view_still_points_at_the_section_switch(
    live_controller: _LiveController,
) -> None:
    runtime = _runtime(live_controller)
    core = _tool(runtime, "acp_capabilities", {"workflow": "Confsearch"})
    assert core["isError"] is False, core
    next_step = core["structuredContent"]["data"]["next"]
    assert next_step["tool"] == "acp_capabilities"

    full = _tool(
        runtime,
        "acp_capabilities",
        {"workflow": "Confsearch", "contract_sections": "full"},
    )
    assert full["isError"] is False, full
    assert full["structuredContent"]["data"]["contract_sections"] == "full"


if __name__ == "__main__":  # pragma: no cover - manual probe
    pytest.main([__file__, "-q"])
