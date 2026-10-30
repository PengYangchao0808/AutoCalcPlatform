"""MCP thin-entry gate (B08-2 / todo 43).

Proves the acceptance clauses of ``acp_services.entrypoints.mcp``:

1. **Standard handshake (M0-08/ADR-006)** — ``initialize`` always answers with
   the version the server speaks; an unsupported ``protocolVersion`` is not a
   server error (the client decides), only malformed ``protocolVersion`` /
   ``protocolVersions`` shapes are ``-32602``; the result declares no top-level
   ``extensions`` and no ``negotiated`` claim. ``tools/list`` + ``tools/call`` +
   ``ping`` answer at that version; unknown methods (including a
   ``server/discover`` probe) are JSON-RPC errors; notifications return ``None``.
2. **Closed allowlist** — ``tools/list`` advertises exactly the eight frozen
   tools, covering the six families; ``tools/call`` rejects an unknown /
   shell-ish tool at the protocol layer (never dispatches it).
3. **Server-enforced identity** — a client-supplied ``tenant_id`` is ignored, a
   tier override is refused, and a project outside the verified scope is denied
   ``access_denied``.
4. **Server-enforced budget** — an over-cap submission is refused before the
   backend is reached (``access_denied`` with structured ``exceeded``).
5. **One idempotency authority (M0-05/M1-01b)** — ``acp_submit`` requires the
   client's raw key and forwards it unchanged to the Controller ledger (no
   adapter cache, no ``op_`` scope fold); a key-less submit is refused, and the
   frozen ``auto_`` lookup survives only for historical rows / optional-key
   prepare.
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

from acp_services.contracts.access import Principal
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
    # 2026-10-30 guidance batch (plan §5): additive read-only families.
    "guidance_discovery_read",
    "task_lookup",
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
    """Controller-faithful submit fake: the *server* owns idempotency (M0-05).

    The MCP adapter forwards the raw key every time and holds no result cache;
    this fake mirrors that by deduplicating on ``(workspace_id, key)`` the way
    ``JobStore.claim_idempotency`` does.
    """

    def __init__(self, store: _JobStore) -> None:
        self.store = store
        self.calls: list[tuple[dict[str, Any], str, str]] = []
        self.fail_next: mo.ErrorCode | None = None
        self._claims: dict[tuple[str, str], str] = {}

    def submit(
        self, spec: dict[str, Any], *, idempotency_key: str, workspace_id: str
    ) -> dict[str, Any]:
        self.calls.append((dict(spec), idempotency_key, workspace_id))
        if self.fail_next is not None:
            code, self.fail_next = self.fail_next, None
            raise mcp.McpToolError(code, "backend refused")
        claim = (workspace_id, idempotency_key)
        if idempotency_key and claim in self._claims:
            job_id = self._claims[claim]
            return {"job_id": job_id, "idempotent_replay": True, "task_ref": {"task_id": job_id}}
        job_id = f"job-{len(self.calls)}"
        if idempotency_key:
            self._claims[claim] = job_id
        self.store.put(job_id, {"state": "running", "reference": job_id})
        return {"job_id": job_id, "idempotent_replay": False}


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
    """Prepare fake that deduplicates on the *server* (raw key passthrough)."""

    def __init__(self) -> None:
        self.prepare_calls: list[tuple[dict[str, Any], str, str]] = []
        self.read_calls: list[dict[str, Any]] = []
        self._claims: dict[tuple[str, str], str] = {}

    def prepare(
        self, request: dict[str, Any], *, idempotency_key: str, workspace_id: str
    ) -> dict[str, Any]:
        self.prepare_calls.append((dict(request), idempotency_key, workspace_id))
        claim = (workspace_id, idempotency_key)
        if idempotency_key and claim in self._claims:
            operation_id = self._claims[claim]
            return {"operation_id": operation_id, "idempotent_replay": True}
        operation_id = f"op-{len(self.prepare_calls)}"
        if idempotency_key:
            self._claims[claim] = operation_id
        return {"operation_id": operation_id, "idempotent_replay": False}

    def read(self, request: dict[str, Any], *, workspace_id: str) -> dict[str, Any]:
        self.read_calls.append(dict(request))
        return {"relative_path": request.get("relative_path", ""), "data": "prepared"}


class _FakeAnalysis:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def analyze(self, request: dict[str, Any], *, workspace_id: str) -> dict[str, Any]:
        self.calls.append(dict(request))
        return {"analysed": True, "job_id": "analysis-job-1"}


#: The minimum declared submit body (M1-01b: input + idempotency_key required).
_SUBMIT_BODY: dict[str, Any] = {
    "workflow": "singlepoint",
    "input": {"source": "CCO"},
    "idempotency_key": "m1-submit-0001",
}


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
    identity: mcp.IdentityMapper | None = None,
) -> mcp.McpServer:
    fakes = fakes or _Fakes()
    return mcp.McpServer(
        backends=fakes.backends(),
        budget=budget,
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


def test_initialize_unsupported_version_returns_server_version() -> None:
    """MCP lifecycle: the server answers with a version it speaks; the client decides."""
    response = _call(_server(), "initialize", {"protocolVersion": "2025-11-25"})
    assert "error" not in response, response
    result = response["result"]
    assert result["protocolVersion"] == mcp.MCP_PROTOCOL_VERSION
    assert result["supportedProtocolVersions"] == list(mcp.SUPPORTED_PROTOCOL_VERSIONS)
    guidance = result["capabilities"]["extensions"]["acp.negotiation.v1"]
    assert guidance["requested_version"] == "2025-11-25"
    assert guidance["server_preferred_version"] == mcp.MCP_PROTOCOL_VERSION


def test_initialize_private_protocol_versions_are_recorded_not_gated() -> None:
    """The private ``protocolVersions`` hint is diagnostic only (ADR-006)."""
    response = _call(
        _server(),
        "initialize",
        {"protocolVersion": "1999-01-01", "protocolVersions": ["1999-01-01"]},
    )
    assert "error" not in response, response
    result = response["result"]
    assert result["protocolVersion"] == mcp.MCP_PROTOCOL_VERSION
    guidance = result["capabilities"]["extensions"]["acp.negotiation.v1"]
    assert guidance["client_declared_versions"] == ["1999-01-01"]
    assert guidance["requested_version"] == "1999-01-01"


def test_initialize_rejects_malformed_protocol_version() -> None:
    for malformed in (123, "", "   ", {"version": "2025-06-18"}, ["2025-06-18"]):
        response = _call(_server(), "initialize", {"protocolVersion": malformed})
        assert response["error"]["code"] == mcp.JsonRpcErrorCode.INVALID_PARAMS
        assert response["error"]["data"]["code"] == "invalid_protocol_version"
        assert "result" not in response


def test_initialize_rejects_malformed_protocol_versions() -> None:
    for malformed in ("2025-06-18", [1], ["2025-06-18", 2], {"v": "2025-06-18"}, [""]):
        response = _call(
            _server(),
            "initialize",
            {"protocolVersion": mcp.MCP_PROTOCOL_VERSION, "protocolVersions": malformed},
        )
        assert response["error"]["code"] == mcp.JsonRpcErrorCode.INVALID_PARAMS
        assert "result" not in response


def test_initialize_declares_no_top_level_extensions_or_negotiated_claim() -> None:
    """M0-08/ADR-006: extension negotiation stays an M0-09 decision."""
    result = _call(_server(), "initialize", {"protocolVersion": mcp.MCP_PROTOCOL_VERSION})["result"]
    assert "extensions" not in result
    assert "acp.research.v1" not in json.dumps(result)
    encoded = json.dumps(result["capabilities"])
    assert '"negotiated"' not in encoded
    guidance = result["capabilities"]["extensions"]["acp.negotiation.v1"]
    assert guidance["supported_versions"] == list(mcp.SUPPORTED_PROTOCOL_VERSIONS)
    assert guidance["negotiation"] == "server_advertises_supported_version"
    assert guidance["client_decides_compatibility"] is True
    assert "negotiated" not in guidance


def test_unknown_method_is_jsonrpc_error() -> None:
    response = _call(_server(), "does/not/exist")
    assert response["error"]["code"] == mcp.JsonRpcErrorCode.METHOD_NOT_FOUND


def test_server_discover_is_an_ordinary_unknown_method() -> None:
    """No discover result is implemented; era-negotiating clients must see -32601."""
    response = _call(_server(), "server/discover", {"protocolVersions": ["2025-06-18"]})
    assert response["error"]["code"] == mcp.JsonRpcErrorCode.METHOD_NOT_FOUND
    assert "result" not in response
    assert "not_initialized" != response["error"].get("data", {}).get("code")


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
    assert len(_EXPECTED_TOOL_FAMILIES) == 8


def test_unknown_tool_rejected_at_protocol_layer() -> None:
    for name in ("shell.exec", "acp_no_such_tool", "subprocess"):
        response = _call(_server(), "tools/call", {"name": name, "arguments": {}})
        assert response["error"]["code"] == mcp.JsonRpcErrorCode.INVALID_PARAMS, name
        assert response["error"]["data"]["code"] == "unknown_tool"
        # The refusal names the registered set (P1 + the ten research tools); the
        # research names stay invisible in tools/list until negotiated.
        assert set(response["error"]["data"]["allowlist"]) == set(mcp.REGISTERED_TOOL_NAMES)


def test_tools_call_requires_a_name() -> None:
    response = _call(_server(), "tools/call", {"arguments": {}})
    assert response["error"]["code"] == mcp.JsonRpcErrorCode.INVALID_PARAMS


def test_dispatch_table_is_exactly_the_allowlist() -> None:
    server = _server()
    # The frozen P1 and legacy P2 sets plus additive negotiated consumer tools.
    # Each set is disjoint; tool visibility remains session-bound.
    from acp_services.entrypoints.research_consumption import CONSUMPTION_TOOL_NAMES

    assert set(server._tool_dispatch) == (
        set(mcp.TOOL_ALLOWLIST) | set(mcp.RESEARCH_TOOL_SET) | set(CONSUMPTION_TOOL_NAMES)
    )
    assert set(mcp.TOOL_ALLOWLIST) & set(mcp.RESEARCH_TOOL_SET) == set()


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
        server, mcp.McpTool.SUBMIT, dict(_SUBMIT_BODY), meta={"project_id": "proj-7"}
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
        server,
        mcp.McpTool.SUBMIT,
        {**_SUBMIT_BODY, "resources": {"cores": 8}},
    )
    assert result["isError"] is True
    envelope = result["structuredContent"]
    assert envelope["error"]["code"] == mo.ErrorCode.ACCESS_DENIED
    assert envelope["error"]["params"]["exceeded"][0]["dimension"] == "cores_per_submission"
    assert fakes.submit.calls == []


def test_budget_in_flight_cap_refuses_second_submission() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes, budget=mcp.WorkspaceBudget(mcp.BudgetCaps(max_in_flight=1)))
    first = _call_tool(server, mcp.McpTool.SUBMIT, {**_SUBMIT_BODY, "idempotency_key": "k1"})
    assert first["isError"] is False
    second = _call_tool(server, mcp.McpTool.SUBMIT, {**_SUBMIT_BODY, "idempotency_key": "k2"})
    assert second["isError"] is True
    assert second["structuredContent"]["error"]["code"] == mo.ErrorCode.ACCESS_DENIED
    assert len(fakes.submit.calls) == 1


def test_cancel_releases_in_flight_budget() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes, budget=mcp.WorkspaceBudget(mcp.BudgetCaps(max_in_flight=1)))
    _call_tool(server, mcp.McpTool.SUBMIT, {**_SUBMIT_BODY, "idempotency_key": "k1"})
    reference = fakes.submit.calls[0][0].get("workflow") and "job-1"
    _call_tool(server, mcp.McpTool.CANCEL, {"reference": reference})
    again = _call_tool(server, mcp.McpTool.SUBMIT, {**_SUBMIT_BODY, "idempotency_key": "k2"})
    assert again["isError"] is False


def test_status_terminal_releases_in_flight_budget() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes, budget=mcp.WorkspaceBudget(mcp.BudgetCaps(max_in_flight=1)))
    _call_tool(server, mcp.McpTool.SUBMIT, {**_SUBMIT_BODY, "idempotency_key": "k1"})
    fakes.store.put("job-1", {"state": "completed", "reference": "job-1"})
    _call_tool(server, mcp.McpTool.STATUS, {"reference": "job-1"})
    again = _call_tool(server, mcp.McpTool.SUBMIT, {**_SUBMIT_BODY, "idempotency_key": "k2"})
    assert again["isError"] is False


# --------------------------------------------------------------------------- #
# 5. One idempotency authority: raw key passthrough, server-side dedup
# --------------------------------------------------------------------------- #


def test_same_key_is_forwarded_unchanged_and_the_server_replays() -> None:
    """M0-05: no adapter cache. Both calls reach the port; one operation exists."""
    fakes = _Fakes()
    server = _server(fakes=fakes)
    args = {**_SUBMIT_BODY, "idempotency_key": "retry-1"}

    first = _call_tool(server, mcp.McpTool.SUBMIT, args)["structuredContent"]["data"]
    second = _call_tool(server, mcp.McpTool.SUBMIT, args)["structuredContent"]["data"]

    # The adapter forwarded the *raw* key both times (no ``op_`` fold, no cache).
    assert [call[1] for call in fakes.submit.calls] == ["retry-1", "retry-1"]
    assert first["reference"] == second["reference"]
    assert first["idempotency_key_origin"] == mcp.IDEMPOTENCY_KEY_ORIGIN_CLIENT
    assert second["idempotent_replay"] is True
    assert second["task_ref"]["task_id"] == first["task_ref"]["task_id"]


def test_missing_key_is_refused_for_a_bound_user() -> None:
    """M1-01b: a key-less submit is a typed refusal, never a silent new task."""
    bound = Principal(
        principal_id="principal_bound", kind="user", tenant_id="tenant-bound", roles=("member",)
    )
    identity = mcp.IdentityMapper(
        permission_scope=mcp.STDIO_MULTIUSER_SCOPE,
        credential=mcp.StdioCredentialBinding(
            principal=bound,
            verified_project="project_alpha",
            key_id="key-1",
            key_scope="workspace",
            actions=("read", "write", "control"),
        ),
    )
    fakes = _Fakes()
    server = _server(fakes=fakes, identity=identity)
    args = {"workflow": "optimize", "input": {"source": "CCO"}}

    refused = _call_tool(server, mcp.McpTool.SUBMIT, args)
    assert refused["isError"] is True
    error = refused["structuredContent"]["error"]
    assert error["code"] == mo.ErrorCode.INPUT_INVALID
    assert error["params"]["rule"] == "idempotency_key_required"
    assert error["params"]["field"] == "idempotency_key"
    # Nothing reached the port: a refused submit creates no task at all.
    assert fakes.submit.calls == []


def test_operator_without_a_key_is_refused_on_submit_and_legacy_lookup_stays_prepare_only() -> None:
    """M1-01b: submit requires the key; the frozen ``auto_`` lookup is not submit's path.

    The formula is still exported and still names the pre-M0-05 rows so a
    historical ``auto_`` record stays inspectable, but a key-less submit no longer
    claims that namespace: it is refused before any port call.
    """
    fakes = _Fakes()
    server = _server(fakes=fakes)
    args = {"workflow": "optimize", "input": {"source": "CCO"}}

    refused = _call_tool(server, mcp.McpTool.SUBMIT, args)
    assert refused["isError"] is True
    assert refused["structuredContent"]["error"]["params"]["rule"] == "idempotency_key_required"
    assert fakes.submit.calls == []

    expected = mcp.derive_idempotency_key(args)
    assert expected.startswith(mcp.LEGACY_DERIVED_KEY_PREFIX)
    # The optional-key surface still resolves it (prepare), unchanged by M1-01b.
    prepared = _call_tool(
        server,
        mcp.McpTool.PREPARE_ARTIFACT,
        {"purpose": "artifact", "job_id": "j1", "artifact_id": "freq_log"},
    )
    assert prepared["isError"] is False
    assert fakes.artifacts.prepare_calls[0][1] == mcp.derive_idempotency_key(
        {"purpose": "artifact", "job_id": "j1", "artifact_id": "freq_log"}
    )
    assert (
        prepared["structuredContent"]["data"]["idempotency_key_origin"]
        == mcp.IDEMPOTENCY_KEY_ORIGIN_LEGACY
    )


def test_permission_scope_never_folds_into_the_ledger_key() -> None:
    """The raw key is identical whatever the transport permission scope is."""
    keys: list[str] = []
    for scope in ("local-single", "stdio-api-key", "lab"):
        fakes = _Fakes()
        server = _server(
            fakes=fakes,
            identity=mcp.IdentityMapper(permission_scope=scope),
        )
        _call_tool(server, mcp.McpTool.SUBMIT, {**_SUBMIT_BODY, "idempotency_key": "k"})
        keys.append(fakes.submit.calls[0][1])
    assert keys == ["k", "k", "k"]


def test_derive_idempotency_key_ignores_volatile_key() -> None:
    base = {"workflow": "singlepoint"}
    assert mcp.derive_idempotency_key(base) == mcp.derive_idempotency_key(
        {**base, "idempotency_key": "ignored"}
    )
    assert not mcp.derive_idempotency_key(base).startswith("op_")


def test_prepare_artifact_refuses_to_cache_and_uses_the_raw_key() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes)
    args = {
        "purpose": "artifact",
        "job_id": "j1",
        "artifact_id": "freq_log",
        "relative_path": "RESULT/x.json",
        "idempotency_key": "prep-1",
    }
    first = _call_tool(server, mcp.McpTool.PREPARE_ARTIFACT, args)["structuredContent"]["data"]
    second = _call_tool(server, mcp.McpTool.PREPARE_ARTIFACT, args)["structuredContent"]["data"]
    assert [call[1] for call in fakes.artifacts.prepare_calls] == ["prep-1", "prep-1"]
    assert first["reference"] == second["reference"]
    assert second["idempotent_replay"] is True
    assert second["idempotency_key"] == "prep-1"


# --------------------------------------------------------------------------- #
# 6. Long-task references + connection independence
# --------------------------------------------------------------------------- #


def test_submit_returns_reference_immediately_and_status_queries_it() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes)
    submitted = _call_tool(server, mcp.McpTool.SUBMIT, dict(_SUBMIT_BODY))
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
    budget = mcp.WorkspaceBudget()
    connection_a = _server(fakes=fakes, budget=budget)

    submitted = _call_tool(
        connection_a, mcp.McpTool.SUBMIT, {**_SUBMIT_BODY, "idempotency_key": "drop-1"}
    )
    reference = submitted["structuredContent"]["data"]["reference"]["id"]
    del connection_a  # simulate the MCP connection dropping

    fakes.store.put(reference, {"state": "running", "reference": reference})
    connection_b = _server(fakes=fakes, budget=budget)
    status = _call_tool(connection_b, mcp.McpTool.STATUS, {"reference": reference})
    assert status["isError"] is False
    assert status["structuredContent"]["data"]["state"] == "running"

    # The same key on the new connection still collapses onto one operation —
    # decided by the server-side ledger, not by any adapter cache.
    replay = _call_tool(
        connection_b, mcp.McpTool.SUBMIT, {**_SUBMIT_BODY, "idempotency_key": "drop-1"}
    )
    assert replay["structuredContent"]["data"]["idempotent_replay"] is True
    assert replay["structuredContent"]["data"]["reference"]["id"] == reference
    assert [call[1] for call in fakes.submit.calls] == ["drop-1", "drop-1"]


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
    result = _call_tool(
        _server(fakes=fakes),
        mcp.McpTool.PREFLIGHT,
        {"workflow": "singlepoint", "input": {"source": "CCO"}},
    )
    envelope = result["structuredContent"]
    assert envelope["dry_run"] is True
    assert envelope["data"]["executed"] is False
    assert envelope["data"]["metering_consumed"] is False
    assert envelope["data"]["transfers_attempted"] is False
    assert fakes.submit.calls == [] and fakes.analysis.calls == []


def test_analyze_requires_explicit_opt_in() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes)
    reference = {
        "task_id": "j1",
        "attempt": 1,
        "artifact_id": "freq_log",
        "generation": 1,
        "digest": "sha256:" + "a" * 64,
    }
    refused = _call_tool(
        server,
        mcp.McpTool.ANALYZE,
        {"analysis_id": "vibration.analyze", "input_refs": [reference], "explicit": False},
    )
    assert refused["isError"] is True
    assert refused["structuredContent"]["error"]["code"] == mo.ErrorCode.INPUT_INVALID
    assert fakes.analysis.calls == []

    missing = _call_tool(server, mcp.McpTool.ANALYZE, {"workflow": "singlepoint"})
    assert missing["isError"] is True
    assert missing["structuredContent"]["error"]["params"]["rule"] == "unknown_field"

    accepted = _call_tool(
        server,
        mcp.McpTool.ANALYZE,
        {"analysis_id": "vibration.analyze", "input_refs": [reference], "explicit": True},
    )
    assert accepted["isError"] is False
    assert len(fakes.analysis.calls) == 1


def test_read_artifact_returns_prepared_data() -> None:
    fakes = _Fakes()
    artifact_ref = {
        "task_id": "j1",
        "attempt": 1,
        "artifact_id": "catalog",
        "generation": 1,
        "digest": "sha256:" + "b" * 64,
    }
    result = _call_tool(
        _server(fakes=fakes), mcp.McpTool.READ_ARTIFACT, {"artifact_ref": artifact_ref}
    )
    assert result["isError"] is False
    assert result["structuredContent"]["data"]["data"] == "prepared"
    assert fakes.artifacts.read_calls[0]["artifact_ref"] == artifact_ref

    # artifact_ref is mandatory: a legacy job_id/relative_path read is refused.
    legacy = _call_tool(
        _server(fakes=fakes), mcp.McpTool.READ_ARTIFACT, {"job_id": "j1", "relative_path": "a.json"}
    )
    assert legacy["isError"] is True
    assert legacy["structuredContent"]["error"]["params"]["rule"] == "unknown_field"


def test_backend_tool_error_becomes_is_error_result() -> None:
    fakes = _Fakes()
    fakes.submit.fail_next = mo.ErrorCode.BACKEND_UNAVAILABLE
    result = _call_tool(_server(fakes=fakes), mcp.McpTool.SUBMIT, dict(_SUBMIT_BODY))
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
                        "arguments": dict(_SUBMIT_BODY),
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

    dsh = next(row for row in matrix if row["client"] == "dsh-official-mcp-client")
    assert dsh["package_version"] == "0.2.1-rc.2"
    assert dsh["client_info"] == {"name": "dsh-mcp-client", "version": "0.0.1"}
    assert dsh["sdk_version"] == "2.2.0"
    assert "auto" in dsh["version_negotiation"]
    assert dsh["status"].startswith("PASS handshake")
    assert "NOT_VERIFIED" in dsh["status"]
    assert "full DSH Agent" in dsh["status"]


# --------------------------------------------------------------------------- #
# 9. M1-01b: declared argument tables, typed refusals, no implicit compute
# --------------------------------------------------------------------------- #


def _declared(name: str) -> dict[str, Any]:
    tool = next(tool for tool in mcp._tool_specs() if tool["name"] == name)
    return tool["inputSchema"]


def test_submit_schema_requires_the_idempotency_key_and_forbids_unknown_fields() -> None:
    schema = _declared(mcp.McpTool.SUBMIT)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["workflow", "input", "idempotency_key"]
    assert "idempotency_key" in schema["properties"]
    # The declared table is the submit field policy minus the server-path fields.
    from acp_services.entrypoints.application_params import (
        DISPOSITION_SERVER_PATH_REJECTED,
        SUBMIT_FIELD_POLICY,
    )

    expected = {
        name
        for name, row in SUBMIT_FIELD_POLICY.items()
        if row.disposition != DISPOSITION_SERVER_PATH_REJECTED
    }
    assert set(schema["properties"]) == expected


def test_preflight_shares_the_table_but_not_the_required_key() -> None:
    preflight = _declared(mcp.McpTool.PREFLIGHT)
    submit = _declared(mcp.McpTool.SUBMIT)
    assert set(preflight["properties"]) == set(submit["properties"])
    assert preflight["additionalProperties"] is False
    assert preflight["required"] == ["workflow", "input"]


def test_prepare_schema_is_the_seventeen_field_authority() -> None:
    schema = _declared(mcp.McpTool.PREPARE_ARTIFACT)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["purpose"]
    assert set(schema["properties"]) == {
        "schema_version",
        "purpose",
        "task_ref",
        "job_id",
        "artifact_id",
        "attempt",
        "generation",
        "expected_revision",
        "relative_path",
        "expected_digest",
        "content_digest",
        "expected_size_bytes",
        "schema_ref",
        "source_refs",
        "evidence_report",
        "media_type",
        "idempotency_key",
    }
    assert "resource_id" not in schema["properties"]
    assert "extensions" not in schema["properties"]


def test_read_and_analyze_declare_their_mandatory_identity_fields() -> None:
    read = _declared(mcp.McpTool.READ_ARTIFACT)
    assert read["required"] == ["artifact_ref"]
    assert read["additionalProperties"] is False
    assert set(read["properties"]["artifact_ref"]["required"]) == {
        "task_id",
        "attempt",
        "artifact_id",
        "generation",
        "digest",
    }
    analyze = _declared(mcp.McpTool.ANALYZE)
    assert analyze["required"] == ["analysis_id", "input_refs", "explicit"]
    assert analyze["additionalProperties"] is False
    assert analyze["properties"]["explicit"]["type"] == "boolean"


def test_unknown_and_missing_arguments_are_typed_and_never_forwarded() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes)

    unknown = _call_tool(server, mcp.McpTool.SUBMIT, {**_SUBMIT_BODY, "execution_mod": "remote"})
    assert unknown["isError"] is True
    error = unknown["structuredContent"]["error"]
    assert error["code"] == mo.ErrorCode.INPUT_INVALID
    assert error["params"]["rule"] == "unknown_field"
    assert error["params"]["field"] == "execution_mod"

    missing = _call_tool(
        server, mcp.McpTool.SUBMIT, {"workflow": "singlepoint", "input": {"source": "CCO"}}
    )
    assert missing["isError"] is True
    assert missing["structuredContent"]["error"]["params"]["rule"] == "idempotency_key_required"

    blank = _call_tool(server, mcp.McpTool.SUBMIT, {**_SUBMIT_BODY, "idempotency_key": "   "})
    assert blank["isError"] is True
    assert blank["structuredContent"]["error"]["params"]["rule"] == "idempotency_key_required"

    # No refused call ever reached the submit port.
    assert fakes.submit.calls == []


def test_read_status_prepare_never_start_computation() -> None:
    fakes = _Fakes()
    server = _server(fakes=fakes)
    artifact_ref = {
        "task_id": "j1",
        "attempt": 1,
        "artifact_id": "catalog",
        "generation": 1,
        "digest": "sha256:" + "c" * 64,
    }

    _call_tool(server, mcp.McpTool.READ_ARTIFACT, {"artifact_ref": artifact_ref})
    _call_tool(server, mcp.McpTool.STATUS, {"reference": "j1"})
    _call_tool(
        server,
        mcp.McpTool.PREPARE_ARTIFACT,
        {"purpose": "artifact", "job_id": "j1", "artifact_id": "catalog"},
    )

    assert fakes.submit.calls == []
    assert fakes.analysis.calls == []
    assert fakes.artifacts.read_calls and fakes.status.calls
    assert fakes.artifacts.prepare_calls and len(fakes.artifacts.prepare_calls) == 1
    assert len(fakes.artifacts.prepare_calls) == 1  # only the explicit prepare


def test_capabilities_project_the_three_analysis_states() -> None:
    from acp_services.runtime.analysis_capabilities import (
        ANALYSIS_CAPABILITY_IDS,
        ANALYSIS_CAPABILITY_STATES,
    )

    result = _call_tool(_server(), mcp.McpTool.CAPABILITIES)
    assert result["isError"] is False
    projection = result["structuredContent"]["data"]["analysis_capabilities"]
    assert [entry["analysis_id"] for entry in projection] == list(ANALYSIS_CAPABILITY_IDS)
    assert {entry["state"] for entry in projection} <= set(ANALYSIS_CAPABILITY_STATES)
    # The default registry ships empty: nothing is executable, nothing is "ready".
    assert {entry["state"] for entry in projection} == {"unavailable"}
    for entry in projection:
        assert entry["registered"] is False
        assert entry["executable"] is False
