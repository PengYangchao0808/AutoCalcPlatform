"""``acp_get_guidance`` MCP tool (plan 2026-10-30 §5: 指南读取).

Unit-level pins over the protocol owner (``acp_services.entrypoints.mcp``):

* the tool is registered in the P1 allowlist and always advertised (U01 needs
  it before any extension negotiation);
* its declared argument table is closed (``additionalProperties: false`` with
  a required ``guide_id``), so an undeclared field is a typed ``input_invalid``
  before any port is touched;
* a wired port returns the versioned payload; an unwired composition root is a
  typed ``unsupported_capability`` refusal, never a transport error;
* the bootstrap adapter (``services_mcp_bootstrap``) binds the tool to one
  Controller command and maps Controller errors onto typed tool errors.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from acp_services.entrypoints import machine_output as mo
from acp_services.entrypoints import mcp
from acp_services.entrypoints.controller_client import ControllerClientError


class _FakeCapabilities:
    def capabilities(self) -> dict[str, Any]:
        return {"workflows": [], "backends": []}


class _Unused:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"guidance read must not reach the {name} port")


class _FakeGuidance:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def guidance(self, request: dict[str, Any], *, workspace_id: str) -> dict[str, Any]:
        self.requests.append(dict(request))
        if not str(request.get("guide_id") or "").strip():
            return {"format": "acp2.guidance.v1", "guides": []}
        if str(request.get("guide_id")) == "workflow.unknown":
            raise mcp.McpToolError(
                mo.ErrorCode.RESOURCE_UNAVAILABLE,
                "unknown guide",
                params={"field": "guide_id", "allowed": ["workflow.PESsearch"]},
                retryable=False,
            )
        return {
            "guide_id": request["guide_id"],
            "version": "1.0.0",
            "digest": "sha256:" + "0" * 64,
            "sections": {"purpose": "p"},
        }


def _server(guidance: Any = ..., extension: mcp.ExtensionSpec | None = None) -> mcp.McpServer:
    unused = _Unused()
    backends = mcp.McpBackends(
        capabilities=_FakeCapabilities(),
        preflight=unused,
        submit=unused,
        status=unused,
        cancel=unused,
        artifacts=unused,
        analysis=unused,
        guidance=None if guidance is ... else guidance,
    )
    return mcp.McpServer(backends=backends, extension=extension)


def _call(server: mcp.McpServer, arguments: dict[str, Any]) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": mcp.McpTool.GUIDANCE, "arguments": arguments},
        }
    )
    assert response is not None and "result" in response, response
    return response["result"]


def _tools_list(server: mcp.McpServer) -> list[str]:
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response is not None
    return [tool["name"] for tool in response["result"]["tools"]]


def test_guidance_is_a_base_tool_always_advertised() -> None:
    assert mcp.McpTool.GUIDANCE in mcp.TOOL_ALLOWLIST
    server = _server(extension=None)
    names = _tools_list(server)
    assert mcp.McpTool.GUIDANCE in names
    assert len(names) == 10
    # even a research-negotiated session keeps exactly the P1 + research union
    spec = mcp.ExtensionSpec(
        extension_id="acp.research.v1", version="1.0.0", promised_tools=mcp.RESEARCH_TOOL_NAMES
    )
    negotiated = _server(_FakeGuidance(), extension=spec)
    _call_capabilities_negotiate(negotiated)
    assert len(_tools_list(negotiated)) == 20


def _call_capabilities_negotiate(server: mcp.McpServer) -> None:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": mcp.McpTool.CAPABILITIES,
                "arguments": {"research_extension": {"id": "acp.research.v1", "version": "1.0.0"}},
            },
        }
    )
    assert response is not None
    data = response["result"]["structuredContent"]["data"]
    assert data["research_tools"]["negotiated"] is True


def test_declared_argument_table_is_closed() -> None:
    spec = {s["name"]: s for s in mcp._tool_specs()}[mcp.McpTool.GUIDANCE]
    assert spec["inputSchema"]["additionalProperties"] is False
    # round-6: no id means "return the discovery index", so guide_id is optional
    assert "required" not in spec["inputSchema"] or "guide_id" not in spec["inputSchema"]["required"]
    port = _FakeGuidance()
    server = _server(port)
    result = _call(server, {"guide_id": "workflow.PESsearch", "surprise": 1})
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "input_invalid"
    assert error["params"]["rule"] == "unknown_field"
    # nothing reached the port
    assert port.requests == []


def test_missing_guide_id_returns_the_discovery_index() -> None:
    port = _FakeGuidance()
    result = _call(_server(port), {})
    assert result["isError"] is False
    data = result["structuredContent"]["data"]
    assert data["format"] == "acp2.guidance.v1"
    assert port.requests == [{}]


def test_wired_port_returns_versioned_payload() -> None:
    port = _FakeGuidance()
    result = _call(_server(port), {"guide_id": "workflow.PESsearch", "section": "purpose"})
    assert result["isError"] is False
    data = result["structuredContent"]["data"]
    assert data["guide_id"] == "workflow.PESsearch"
    assert data["version"] == "1.0.0"
    assert port.requests == [{"guide_id": "workflow.PESsearch", "section": "purpose"}]


def test_wire_content_keeps_public_identifiers_under_the_secret_net() -> None:
    """Serialized guidance must equal the pack body (codex P2, 2026-10-30).

    The opaque-token net used to rewrite ``pes2ts_*_request_v1`` to
    ``<redacted>`` inside the tool result while the digest stayed unchanged —
    an information loss on the MCP face only.  The wire body must match the
    pack payload exactly.
    """
    from acp.api.guidance_pack import read_guide as pack_read

    class _PassThrough:
        def guidance(self, request: dict[str, Any], *, workspace_id: str) -> dict[str, Any]:
            return dict(pack_read(str(request["guide_id"])))

    server = _server(_PassThrough())
    result = _call(server, {"guide_id": "workflow.XtbConstrainedOpt"})
    assert result["isError"] is False
    serialized = json.dumps(result, ensure_ascii=False)
    assert "pes2ts_xtb_constrained_opt_request_v1" in serialized
    assert "<redacted>" not in serialized
    data = result["structuredContent"]["data"]
    pack_payload = pack_read("workflow.XtbConstrainedOpt")
    assert data["sections"] == pack_payload["sections"]
    assert data["digest"] == pack_payload["digest"]


def test_unwired_port_is_a_typed_refusal() -> None:
    result = _call(_server(), {"guide_id": "workflow.PESsearch"})
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "unsupported_capability"
    assert error["params"]["rule"] == "guidance_service_unavailable"


def test_port_error_maps_to_a_typed_tool_error() -> None:
    result = _call(_server(_FakeGuidance()), {"guide_id": "workflow.unknown"})
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "resource_unavailable"
    assert error["params"]["allowed"] == ["workflow.PESsearch"]


def test_bootstrap_adapter_binds_one_controller_command() -> None:
    """``_ControllerGuidance`` forwards verbatim and maps Controller errors."""
    from acp.services_mcp_bootstrap import _ControllerGuidance

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def guidance(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self.calls.append((args, kwargs))
            return {"guide_id": args[0], "ok": True}

        def guidance_index(self) -> dict[str, Any]:
            return {"format": "acp2.guidance.v1", "guides": []}

    client = _Client()
    port = _ControllerGuidance(client)  # type: ignore[arg-type]
    out = port.guidance(
        {"guide_id": "shared.execution", "section": "body", "version": "1.0.0", "max_bytes": 512},
        workspace_id="ws",
    )
    assert out == {"guide_id": "shared.execution", "ok": True}
    assert client.calls == [
        (
            ("shared.execution",),
            {"section": "body", "version": "1.0.0", "max_bytes": 512},
        )
    ]

    class _FailingClient:
        def guidance(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            # HTTP 404 arrives through the shared _HTTP_ERRORS table as
            # resource_not_ready (NotPreparedError) — one mapping, no guidance
            # special case.
            raise ControllerClientError("unknown guide", code="resource_not_ready")

    failing = _ControllerGuidance(_FailingClient())  # type: ignore[arg-type]
    with pytest.raises(mcp.McpToolError) as excinfo:
        failing.guidance({"guide_id": "workflow.nope"}, workspace_id="ws")
    assert excinfo.value.code == "resource_not_ready"

    assert _ControllerGuidance(client).guidance({"guide_id": ""}, workspace_id="ws") == {
        "format": "acp2.guidance.v1",
        "guides": [],
    }
