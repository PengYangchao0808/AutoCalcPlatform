"""MCP thin-entry gate (B08-2 / todo 43).

Proves the acceptance clauses of ``acp_services.entrypoints.mcp``:

1. **Locked protocol** — ``initialize`` returns one version and refuses any other;
   ``tools/list`` + ``tools/call`` + ``ping`` answer at that version; unknown
   methods are JSON-RPC errors; notifications return ``None``.
2. **Closed allowlist** — ``tools/list`` advertises exactly the eight frozen
   tools, covering the six families; ``tools/call`` rejects an unknown /
   shell-ish tool at the protocol layer (never dispatches it).
3. **Server-enforced identity** — a client-supplied ``tenant_id`` is ignored, a
   tier override is refused, and a project outside the verified scope is denied
   ``access_denied``.
4. **Server-enforced budget** — an over-cap submission is refused before the
   backend is reached (``access_denied`` with structured ``exceeded``).
5. **Server-enforced idempotency** — the same key yields exactly one operation
   (explicit and derived keys), and permission scope participates in the key.
6. **Long-task references** — ``acp_submit`` returns immediately; ``acp_status``
   queries it; a *dropped connection* (a fresh server sharing the ports) still
   queries the same operation.
7. **No arbitrary dispatch** — a static AST guard forbids ``getattr``/``eval``/
   ``exec``/``__import__``/``subprocess``; the dispatch table is a literal
   allowlist; a mutation with an injected ``getattr`` dispatch is detected.
8. **Protocol-level client integration record** — one simulated MCP session
   (initialize -> tools/list -> submit -> status -> cancel); the real-client
   integration is marked ``NOT_VERIFIED`` (none is wired in this slice).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from acp_services.entrypoints import machine_output as mo
from acp_services.entrypoints import mcp

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MCP_PATH = _REPO_ROOT / "src" / "acp_services" / "entrypoints" / "mcp.py"

_EXPECTED_TOOL_FAMILIES = {
    "capability_discovery",
    "preflight_estimate",
    "controlled_submit",
    "status_cancel",
    "artifact_prepare_read",
    "explicit_analysis",
}


# --------------------------------------------------------------------------- #
# Fakes (injected Service use cases)
# --------------------------------------------------------------------------- #


class _FakeCapabilities:
    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self) -> dict[str, Any]:
        self.calls += 1
        return {"workflows": ["singlepoint", "optimize"], "backends": ["orca"]}


class _FakePreflight:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], str]] = []

    def preflight(self, request: dict[str, Any], *, workspace_id: str) -> dict[str, Any]:
        self.calls.append((dict(request), workspace_id))
        return {"executes_science": False, "estimate": {"cores": 4}}


class _JobStore:
    """Tiny in-memory operation table shared across connections."""

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}

    def put(self, reference: str, record: dict[str, Any]) -> None:
        self.records[reference] = record

    def get(self, reference: str) -> dict[str, Any] | None:
        record = self.records.get(reference)
        return None if record is None else dict(record)


class _FakeSubmit:
    def __init__(self, store: _JobStore) -> None:
        self.store = store
        self.calls: list[tuple[dict[str, Any], str, str]] = []
        self.fail_next: mo.ErrorCode | None = None

    def submit(
        self, spec: dict[str, Any], *, idempotency_key: str, workspace_id: str
    ) -> dict[str, Any]:
        self.calls.append((dict(spec), idempotency_key, workspace_id))
        if self.fail_next is not None:
            code, self.fail_next = self.fail_next, None
            raise mcp.McpToolError(code, "backend refused")
        job_id = f"job-{len(self.calls)}"
        self.store.put(job_id, {"state": "running", "reference": job_id})
        return {"job_id": job_id}


class _FakeStatus:
    def __init__(self, store: _JobStore) -> None:
        self.store = store
        self.calls: list[str] = []

    def get(self, reference: str, *, workspace_id: str) -> dict[str, Any] | None:
        self.calls.append(reference)
        return self.store.get(reference)


class _FakeCancel:
    def __init__(self, store: _JobStore) -> None:
        self.store = store
        self.calls: list[str] = []

    def cancel(self, reference: str, *, workspace_id: str) -> dict[str, Any]:
        self.calls.append(reference)
        self.store.put(reference, {"state": "cancelled", "reference": reference})
        return {"state": "cancelled", "reference": reference}


class _FakeArtifacts:
    def __init__(self) -> None:
        self.prepare_calls: list[tuple[dict[str, Any], str, str]] = []
        self.read_calls: list[dict[str, Any]] = []

    def prepare(
        self, request: dict[str, Any], *, idempotency_key: str, workspace_id: str
    ) -> dict[str, Any]:
        self.prepare_calls.append((dict(request), idempotency_key, workspace_id))
        return {"operation_id": f"op-{len(self.prepare_calls)}"}

    def read(self, request: dict[str, Any], *, workspace_id: str) -> dict[str, Any]:
        self.read_calls.append(dict(request))
        return {"relative_path": request.get("relative_path", ""), "data": "prepared"}


class _FakeAnalysis:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def analyze(self, request: dict[str, Any], *, workspace_id: str) -> dict[str, Any]:
        self.calls.append(dict(request))
        return {"analysed": True, "job_id": "analysis-job-1"}


class _Fakes:
    def __init__(self) -> None:
        self.store = _JobStore()
        self.capabilities = _FakeCapabilities()
        self.preflight = _FakePreflight()
        self.submit = _FakeSubmit(self.store)
        self.status = _FakeStatus(self.store)
        self.cancel = _FakeCancel(self.store)
        self.artifacts = _FakeArtifacts()
        self.analysis = _FakeAnalysis()

    def backends(self) -> mcp.McpBackends:
        return mcp.McpBackends(
            capabilities=self.capabilities,
            preflight=self.preflight,
            submit=self.submit,
            status=self.status,
            cancel=self.cancel,
            artifacts=self.artifacts,
            analysis=self.analysis,
        )


def _server(
    *,
    fakes: _Fakes | None = None,
    budget: mcp.WorkspaceBudget | None = None,
    idempotency: mcp.IdempotencyRegistry | None = None,
    identity: mcp.IdentityMapper | None = None,
) -> mcp.McpServer:
    fakes = fakes or _Fakes()
    return mcp.McpServer(
        backends=fakes.backends(),
        budget=budget,
        idempotency=idempotency,
        identity=identity,
    )


def _call(
    server: mcp.McpServer,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    request_id: int = 1,
) -> dict[str, Any]:
    response = server.handle(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    assert response is not None
    return response


def _call_tool(
    server: mcp.McpServer,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"name": name, "arguments": arguments or {}}
    if meta is not None:
        params["_meta"] = meta
    response = _call(server, "tools/call", params)
    assert "result" in response, response
    return response["result"]


# --------------------------------------------------------------------------- #
# 1. Locked protocol
# --------------------------------------------------------------------------- #


def test_initialize_returns_locked_version() -> None:
    server = _server()
    result = _call(server, "initialize", {"protocolVersion": mcp.MCP_PROTOCOL_VERSION})["result"]
    assert result["protocolVersion"] == mcp.MCP_PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == mcp.MCP_SERVER_NAME
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert mcp.STDIO_PERMISSION_SCOPE in result["instructions"]


def test_initialize_without_version_defaults_to_locked() -> None:
    result = _call(_server(), "initialize", {})["result"]
    assert result["protocolVersion"] == mcp.MCP_PROTOCOL_VERSION


def test_initialize_rejects_other_protocol_version() -> None:
    response = _call(_server(), "initialize", {"protocolVersion": "1999-01-01"})
    assert response["error"]["code"] == mcp.JsonRpcErrorCode.INVALID_PARAMS
    assert response["error"]["data"]["code"] == "unsupported_protocol_version"
    assert response["error"]["data"]["locked"] == mcp.MCP_PROTOCOL_VERSION


def test_unknown_method_is_jsonrpc_error() -> None:
    response = _call(_server(), "does/not/exist")
    assert response["error"]["code"] == mcp.JsonRpcErrorCode.METHOD_NOT_FOUND


def test_ping_returns_empty_result() -> None:
    assert _call(_server(), "ping")["result"] == {}


def test_notifications_return_none() -> None:
    server = _server()
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "method": "initialize"}) is None


def test_handle_line_frames_json_and_reports_parse_error() -> None:
    server = _server()
    line = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert json.loads(server.handle_line(line))["result"] == {}
    assert server.handle_line("{not json") is not None
    assert json.loads(server.handle_line("{not json"))["error"]["code"] == (
        mcp.JsonRpcErrorCode.PARSE_ERROR
    )


# --------------------------------------------------------------------------- #
# 2. Closed allowlist
# --------------------------------------------------------------------------- #


def test_tools_list_is_the_frozen_allowlist() -> None:
    tools = _call(_server(), "tools/list")["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert names == set(mcp.TOOL_ALLOWLIST)
    assert names == set(mcp.McpTool.ALL)
    for tool in tools:
        assert tool["description"]
        assert isinstance(tool["inputSchema"], dict)


def test_tool_allowlist_covers_six_families() -> None:
    assert set(mcp.TOOL_FAMILIES) == set(mcp.TOOL_ALLOWLIST)
    assert set(mcp.TOOL_FAMILIES.values()) == _EXPECTED_TOOL_FAMILIES
    assert len(_EXPECTED_TOOL_FAMILIES) == 6


def test_unknown_tool_rejected_at_protocol_layer() -> None:
    for name in ("shell.exec", "acp_no_such_tool", "subprocess"):
        response = _call(_server(), "tools/call", {"name": name, "arguments": {}})
        assert response["error"]["code"] == mcp.JsonRpcErrorCode.INVALID_PARAMS, name
        assert response["error"]["data"]["code"] == "unknown_tool"
        assert set(response["error"]["data"]["allowlist"]) == set(mcp.TOOL_ALLOWLIST)


def test_tools_call_requires_a_name() -> None:
    response = _call(_server(), "tools/call", {"arguments": {}})
    assert response["error"]["code"] == mcp.JsonRpcErrorCode.INVALID_PARAMS


def test_dispatch_table_is_exactly_the_allowlist() -> None:
    server = _server()
    assert set(server._tool_dispatch) == set(mcp.TOOL_ALLOWLIST)


# --------------------------------------------------------------------------- #
# 3. Server-enforced identity
# --------------------------------------------------------------------------- #


def test_client_tenant_is_ignored() -> None:
    from acp_services.contracts.access import LOCAL_TENANT_ID

    mapper = mcp.IdentityMapper()
    context = mapper.map({"tenant_id": "attacker-tenant", "project_id": "proj-x"})
    assert context.tenant_id == LOCAL_TENANT_ID
    assert context.workspace_id == "proj-x"
    assert context.principal.tenant_id == LOCAL_TENANT_ID


def test_workspace_default_is_tenant_when_no_project() -> None:
    context = mcp.IdentityMapper().map({})
    assert context.workspace_id.startswith("tenant:")


def test_denied_project_returns_access_denied() -> None:
    mapper = mcp.IdentityMapper(known_projects={"p1": "other-tenant"})
    with pytest.raises(mcp.McpToolError) as excinfo:
        mapper.map({"project_id": "p1"})
    assert excinfo.value.code == mo.ErrorCode.ACCESS_DENIED
    assert excinfo.value.params["reason_code"] == "scope_mismatch"


def test_tier_override_refused() -> None:
    mapper = mcp.IdentityMapper(configured_tier="local-single")
    with pytest.raises(mcp.McpToolError) as excinfo:
        mapper.map({"deployment_tier": "hosted"})
    assert excinfo.value.code == mo.ErrorCode.ACCESS_DENIED
    assert excinfo.value.params["reason_code"] == "tier_override_attempt"


def test_tools_call_maps_identity_and_records_workspace() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes)
    result = _call_tool(
        server, mcp.McpTool.SUBMIT, {"workflow": "singlepoint"}, meta={"project_id": "proj-7"}
    )
    assert result["isError"] is False
    assert fakes.submit.calls[0][2] == "proj-7"


# --------------------------------------------------------------------------- #
# 4. Server-enforced budget
# --------------------------------------------------------------------------- #


def test_budget_cap_refuses_over_cap_submission() -> None:
    fakes = _Fakes()
    server = _server(
        fakes=fakes, budget=mcp.WorkspaceBudget(mcp.BudgetCaps(max_cores_per_submission=4))
    )
    result = _call_tool(
        server, mcp.McpTool.SUBMIT, {"workflow": "singlepoint", "resources": {"cores": 8}}
    )
    assert result["isError"] is True
    envelope = result["structuredContent"]
    assert envelope["error"]["code"] == mo.ErrorCode.ACCESS_DENIED
    assert envelope["error"]["params"]["exceeded"][0]["dimension"] == "cores_per_submission"
    assert fakes.submit.calls == []


def test_budget_in_flight_cap_refuses_second_submission() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes, budget=mcp.WorkspaceBudget(mcp.BudgetCaps(max_in_flight=1)))
    first = _call_tool(
        server, mcp.McpTool.SUBMIT, {"workflow": "singlepoint", "idempotency_key": "k1"}
    )
    assert first["isError"] is False
    second = _call_tool(
        server, mcp.McpTool.SUBMIT, {"workflow": "singlepoint", "idempotency_key": "k2"}
    )
    assert second["isError"] is True
    assert second["structuredContent"]["error"]["code"] == mo.ErrorCode.ACCESS_DENIED
    assert len(fakes.submit.calls) == 1


def test_cancel_releases_in_flight_budget() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes, budget=mcp.WorkspaceBudget(mcp.BudgetCaps(max_in_flight=1)))
    _call_tool(server, mcp.McpTool.SUBMIT, {"workflow": "singlepoint", "idempotency_key": "k1"})
    reference = fakes.submit.calls[0][0].get("workflow") and "job-1"
    _call_tool(server, mcp.McpTool.CANCEL, {"reference": reference})
    again = _call_tool(
        server, mcp.McpTool.SUBMIT, {"workflow": "singlepoint", "idempotency_key": "k2"}
    )
    assert again["isError"] is False


def test_status_terminal_releases_in_flight_budget() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes, budget=mcp.WorkspaceBudget(mcp.BudgetCaps(max_in_flight=1)))
    _call_tool(server, mcp.McpTool.SUBMIT, {"workflow": "singlepoint", "idempotency_key": "k1"})
    fakes.store.put("job-1", {"state": "completed", "reference": "job-1"})
    _call_tool(server, mcp.McpTool.STATUS, {"reference": "job-1"})
    again = _call_tool(
        server, mcp.McpTool.SUBMIT, {"workflow": "singlepoint", "idempotency_key": "k2"}
    )
    assert again["isError"] is False


# --------------------------------------------------------------------------- #
# 5. Server-enforced idempotency
# --------------------------------------------------------------------------- #


def test_idempotent_submission_same_key_one_operation() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes)
    args = {"workflow": "singlepoint", "idempotency_key": "retry-1"}

    first = _call_tool(server, mcp.McpTool.SUBMIT, args)["structuredContent"]["data"]
    second = _call_tool(server, mcp.McpTool.SUBMIT, args)["structuredContent"]["data"]

    assert first["reference"] == second["reference"]
    assert second["idempotent_replay"] is True
    assert len(fakes.submit.calls) == 1


def test_idempotent_submission_derived_key_for_identical_args() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes)
    args = {"workflow": "optimize", "input": {"source": "CCO"}}

    first = _call_tool(server, mcp.McpTool.SUBMIT, args)["structuredContent"]["data"]
    second = _call_tool(server, mcp.McpTool.SUBMIT, args)["structuredContent"]["data"]

    assert first["reference"] == second["reference"]
    assert len(fakes.submit.calls) == 1


def test_permission_scope_participates_in_idempotency_key() -> None:
    registry = mcp.IdempotencyRegistry()
    scoped_a = registry.scoped_key(permission_scope="local-single", raw_key="k")
    scoped_b = registry.scoped_key(permission_scope="lab", raw_key="k")
    assert scoped_a != scoped_b


def test_derive_idempotency_key_ignores_volatile_key() -> None:
    base = {"workflow": "singlepoint"}
    assert mcp.derive_idempotency_key(base) == mcp.derive_idempotency_key(
        {**base, "idempotency_key": "ignored"}
    )


def test_prepare_artifact_is_idempotent() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes)
    args = {"job_id": "j1", "relative_path": "RESULT/x.json", "idempotency_key": "prep-1"}
    first = _call_tool(server, mcp.McpTool.PREPARE_ARTIFACT, args)["structuredContent"]["data"]
    second = _call_tool(server, mcp.McpTool.PREPARE_ARTIFACT, args)["structuredContent"]["data"]
    assert first["reference"] == second["reference"]
    assert second["idempotent_replay"] is True
    assert len(fakes.artifacts.prepare_calls) == 1


# --------------------------------------------------------------------------- #
# 6. Long-task references + connection independence
# --------------------------------------------------------------------------- #


def test_submit_returns_reference_immediately_and_status_queries_it() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes)
    submitted = _call_tool(server, mcp.McpTool.SUBMIT, {"workflow": "singlepoint"})
    data = submitted["structuredContent"]["data"]
    assert data["reference"] == {"kind": "job", "id": "job-1"}
    assert data["query_tool"] == mcp.McpTool.STATUS
    assert data["cancel_tool"] == mcp.McpTool.CANCEL

    status = _call_tool(server, mcp.McpTool.STATUS, {"reference": "job-1"})
    assert status["structuredContent"]["data"]["state"] == "running"

    fakes.store.put("job-1", {"state": "completed", "reference": "job-1"})
    later = _call_tool(server, mcp.McpTool.STATUS, {"reference": "job-1"})
    assert later["structuredContent"]["data"]["state"] == "completed"


def test_unknown_reference_reports_not_ready() -> None:
    result = _call_tool(_server(), mcp.McpTool.STATUS, {"reference": "missing"})
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == mo.ErrorCode.RESOURCE_NOT_READY


def test_disconnect_does_not_affect_operation() -> None:
    """A dropped connection is a closed server; a fresh one still queries the job."""
    fakes = _Fakes()
    idempotency = mcp.IdempotencyRegistry()
    budget = mcp.WorkspaceBudget()
    connection_a = _server(fakes=fakes, idempotency=idempotency, budget=budget)

    submitted = _call_tool(connection_a, mcp.McpTool.SUBMIT, {"workflow": "singlepoint"})
    reference = submitted["structuredContent"]["data"]["reference"]["id"]
    del connection_a  # simulate the MCP connection dropping

    fakes.store.put(reference, {"state": "running", "reference": reference})
    connection_b = _server(fakes=fakes, idempotency=idempotency, budget=budget)
    status = _call_tool(connection_b, mcp.McpTool.STATUS, {"reference": reference})
    assert status["isError"] is False
    assert status["structuredContent"]["data"]["state"] == "running"

    # The same key on the new connection still collapses onto one operation.
    replay = _call_tool(connection_b, mcp.McpTool.SUBMIT, {"workflow": "singlepoint"})
    assert replay["structuredContent"]["data"]["idempotent_replay"] is True
    assert len(fakes.submit.calls) == 1


def test_cancel_calls_backend() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes)
    _call_tool(server, mcp.McpTool.SUBMIT, {"workflow": "singlepoint"})
    result = _call_tool(server, mcp.McpTool.CANCEL, {"reference": "job-1"})
    assert result["isError"] is False
    assert result["structuredContent"]["data"]["state"] == "cancelled"
    assert fakes.cancel.calls == ["job-1"]


# --------------------------------------------------------------------------- #
# Capability / preflight / analysis / artifact read
# --------------------------------------------------------------------------- #


def test_capabilities_tool_reports_descriptor_and_tools() -> None:
    fakes = _Fakes()
    data = _call_tool(_server(fakes=fakes), mcp.McpTool.CAPABILITIES)["structuredContent"]["data"]
    assert data["protocol_version"] == mcp.MCP_PROTOCOL_VERSION
    assert set(data["tools"]) == set(mcp.TOOL_ALLOWLIST)
    assert fakes.capabilities.calls == 1


def test_preflight_is_zero_side_effect_dry_run() -> None:
    fakes = _Fakes()
    result = _call_tool(_server(fakes=fakes), mcp.McpTool.PREFLIGHT, {"workflow": "singlepoint"})
    envelope = result["structuredContent"]
    assert envelope["dry_run"] is True
    assert envelope["data"]["executed"] is False
    assert envelope["data"]["metering_consumed"] is False
    assert envelope["data"]["transfers_attempted"] is False
    assert fakes.submit.calls == [] and fakes.analysis.calls == []


def test_analyze_requires_explicit_opt_in() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes)
    refused = _call_tool(server, mcp.McpTool.ANALYZE, {"workflow": "singlepoint"})
    assert refused["isError"] is True
    assert refused["structuredContent"]["error"]["code"] == mo.ErrorCode.INPUT_INVALID
    assert fakes.analysis.calls == []

    accepted = _call_tool(
        server, mcp.McpTool.ANALYZE, {"workflow": "singlepoint", "explicit": True}
    )
    assert accepted["isError"] is False
    assert len(fakes.analysis.calls) == 1


def test_read_artifact_returns_prepared_data() -> None:
    fakes = _Fakes()
    result = _call_tool(
        _server(fakes=fakes), mcp.McpTool.READ_ARTIFACT, {"job_id": "j1", "relative_path": "a.json"}
    )
    assert result["isError"] is False
    assert result["structuredContent"]["data"]["data"] == "prepared"
    assert fakes.artifacts.read_calls[0]["relative_path"] == "a.json"


def test_backend_tool_error_becomes_is_error_result() -> None:
    fakes = _Fakes()
    fakes.submit.fail_next = mo.ErrorCode.BACKEND_UNAVAILABLE
    result = _call_tool(_server(fakes=fakes), mcp.McpTool.SUBMIT, {"workflow": "singlepoint"})
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == mo.ErrorCode.BACKEND_UNAVAILABLE


# --------------------------------------------------------------------------- #
# 7. No arbitrary shell / function-name dispatch (static guards)
# --------------------------------------------------------------------------- #

_FORBIDDEN_CALL_NAMES = frozenset(
    {"getattr", "setattr", "eval", "exec", "compile", "__import__", "globals", "locals"}
)
_FORBIDDEN_IMPORT_ROOTS = frozenset({"subprocess", "importlib", "os", "shlex", "ctypes"})


def scan_for_arbitrary_dispatch(source: str) -> list[str]:
    """Return descriptors of any dynamic/shell dispatch (empty == clean)."""
    violations: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _FORBIDDEN_IMPORT_ROOTS:
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
            if module in _FORBIDDEN_IMPORT_ROOTS:
                violations.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else None
            if name in _FORBIDDEN_CALL_NAMES:
                violations.append(f"call {name}(...)")
            attr = func.attr if isinstance(func, ast.Attribute) else None
            if attr in {
                "system",
                "popen",
                "spawn",
                "spawnv",
                "execv",
                "run",
                "call",
                "check_output",
            }:
                value = func.value
                if isinstance(value, ast.Name) and value.id in {"os", "subprocess", "shlex"}:
                    violations.append(f"{value.id}.{attr}(...)")
    return violations


def test_module_has_no_arbitrary_dispatch() -> None:
    violations = scan_for_arbitrary_dispatch(_MCP_PATH.read_text(encoding="utf-8"))
    assert violations == [], f"arbitrary dispatch detected: {violations}"


def test_dispatch_scanner_has_teeth() -> None:
    injected = "def f(self, name, a, c):\n    return getattr(self, name)(a, c)\n"
    assert any("getattr" in item for item in scan_for_arbitrary_dispatch(injected))
    injected_shell = "import subprocess\nsubprocess.run(['rm', '-rf', '/'])\n"
    assert any("subprocess" in item for item in scan_for_arbitrary_dispatch(injected_shell))


def test_module_never_imports_acp() -> None:
    tree = ast.parse(_MCP_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(not (a.name == "acp" or a.name.startswith("acp.")) for a in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not (node.module == "acp" or node.module.startswith("acp."))


# --------------------------------------------------------------------------- #
# 8. Protocol-level client integration record
# --------------------------------------------------------------------------- #


def test_protocol_level_client_session_record() -> None:
    """Simulate one MCP stdio client session end to end (protocol-level mock)."""
    fakes = _Fakes()
    server = _server(fakes=fakes)

    handshake = json.loads(
        server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": mcp.MCP_PROTOCOL_VERSION,
                        "clientInfo": {"name": "mock-stdio-client", "version": "0.0.1"},
                    },
                }
            )
        )
    )
    assert handshake["result"]["protocolVersion"] == mcp.MCP_PROTOCOL_VERSION
    server.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    listed = json.loads(
        server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
    )
    assert len(listed["result"]["tools"]) == len(mcp.TOOL_ALLOWLIST)

    submitted = json.loads(
        server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": mcp.McpTool.SUBMIT,
                        "arguments": {"workflow": "singlepoint"},
                    },
                }
            )
        )
    )
    reference = submitted["result"]["structuredContent"]["data"]["reference"]["id"]

    queried = json.loads(
        server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": mcp.McpTool.STATUS, "arguments": {"reference": reference}},
                }
            )
        )
    )
    assert queried["result"]["structuredContent"]["data"]["state"] == "running"

    cancelled = json.loads(
        server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": mcp.McpTool.CANCEL, "arguments": {"reference": reference}},
                }
            )
        )
    )
    assert cancelled["result"]["structuredContent"]["data"]["state"] == "cancelled"

    integration_record = {
        "client": "mock-stdio-client",
        "transport": mcp.STDIO_TRANSPORT,
        "protocol_version": mcp.MCP_PROTOCOL_VERSION,
        "session": [
            "initialize",
            "notifications/initialized",
            "tools/list",
            "acp_submit",
            "acp_status",
            "acp_cancel",
        ],
        "real_client_integration": "NOT_VERIFIED",
    }
    assert integration_record["real_client_integration"] == "NOT_VERIFIED"

    matrix = mcp.client_matrix()
    assert any(row["protocol_version"] == mcp.MCP_PROTOCOL_VERSION for row in matrix)
    assert any("NOT_VERIFIED" in str(row["status"]) for row in matrix)
    assert all(row["session_coupled"] is False for row in matrix)
