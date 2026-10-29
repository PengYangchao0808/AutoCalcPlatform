"""M0-09 — real research-extension handshake, application-level carrier.

Proves the M0-09 outcome recorded in ADR-006 (§D7): the research extension is
**advertised and negotiated through ``tools/call acp_capabilities``**, never
through ``initialize``, and advertising is never turned into a delivery claim.

Acceptance clauses:

1. **One authority.** ``p2-research-operations.v1.json`` freezes
   ``acp.research.v1@1.0.0``; ``application-api.v1.json::mcp_extensions``
   projects it, ``acp.mcp_extension`` declares it for the server/API, and the RPH
   bridge declares the same pair. A coordinated bump is the only way to change it.
2. **Advertised ≠ implemented ≠ negotiated.** ``extensions[]`` always carries
   ``supported``/``requested``/``negotiated_by``/``implemented``/``negotiated``/
   ``refusal``. The ten promised research tools are absent from the allowlist, so
   ``implemented`` and ``negotiated`` are ``false`` even for an exact-version
   request — and a covering registered set flips both (the M1-01 rule, unit-level).
3. **Typed in-band refusals.** A version mismatch / unknown id / unimplemented
   extension is ``isError=true`` with ``unsupported_capability`` (the result still
   carries ``extensions[]``), never a transport error and never a silent accept;
   a project hint that disagrees with the server binding is ``access_denied``.
4. **Server-resolved identity.** ``_meta`` identity/tier is still ignored, the
   project hint cannot select the scope, and the ``client`` argument is audit
   metadata only.
5. **HTTP equivalence + real client params.** ``GET``/``POST /api/capabilities``
   answer the same projection, and ``ControllerClient.capabilities`` really sends
   the request instead of dropping it.
6. **No ``initialize`` claim.** The standard handshake result makes no extension
   claim; it only points at the carrier.

The real-SDK half (real ``acp mcp serve`` + frozen DSH client calling
``acp_capabilities``) lives in ``tests/test_acp2_mcp_sdk_contract.py`` (``-m
contract``); this module is the deterministic unit/HTTP/client half.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from acp import mcp_extension
from acp_services.contracts.access import LocalPrincipal
from acp_services.entrypoints import mcp
from acp_services.entrypoints.controller_client import (
    Capabilities,
    ControllerClient,
    InvalidParameterError,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONTRACTS = _REPO_ROOT / "docs" / "reference" / "next-change" / "acp2-contracts"
_SPEC = mcp_extension.RESEARCH_EXTENSION_SPEC


# --------------------------------------------------------------------------- #
# Fakes / helpers
# --------------------------------------------------------------------------- #


class _FakeCapabilities:
    def capabilities(self) -> dict[str, Any]:
        return {"workflows": ["singlepoint"], "backends": [{"name": "orca"}]}


class _Unused:
    """Any non-discovery port: reaching it from this module is a bug."""

    def preflight(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("capability discovery must not reach another port")

    def submit(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("capability discovery must not reach another port")

    def get(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("capability discovery must not reach another port")

    def cancel(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("capability discovery must not reach another port")

    def prepare(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("capability discovery must not reach another port")

    def read(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("capability discovery must not reach another port")

    def analyze(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("capability discovery must not reach another port")


def _server(
    *,
    extension: mcp.ExtensionSpec | None = _SPEC,
    identity: mcp.IdentityMapper | None = None,
) -> mcp.McpServer:
    unused = _Unused()
    backends = mcp.McpBackends(
        capabilities=_FakeCapabilities(),
        preflight=unused,
        submit=unused,
        status=unused,
        cancel=unused,
        artifacts=unused,
        analysis=unused,
    )
    return mcp.McpServer(backends=backends, identity=identity, extension=extension)


def _call_tool(
    server: mcp.McpServer,
    arguments: dict[str, Any] | None = None,
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "name": mcp.McpTool.CAPABILITIES,
        "arguments": arguments or {},
    }
    if meta is not None:
        params["_meta"] = meta
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    assert response is not None
    assert "result" in response, response
    return response["result"]


def _entry(result: dict[str, Any]) -> dict[str, Any]:
    entries = result["structuredContent"]["data"]["extensions"]
    assert len(entries) == 1, entries
    return entries[0]


# --------------------------------------------------------------------------- #
# 1. One authority
# --------------------------------------------------------------------------- #


def test_extension_identity_has_one_authority() -> None:
    p2 = json.loads(
        (_CONTRACTS / "runtime" / "p2-research-operations.v1.json").read_text(encoding="utf-8")
    )
    api = json.loads(
        (_CONTRACTS / "runtime" / "application-api.v1.json").read_text(encoding="utf-8")
    )
    negotiation = p2["client_path"]["negotiation"]
    assert mcp_extension.RESEARCH_EXTENSION_ID == negotiation["extension_id"] == "acp.research.v1"
    assert (
        mcp_extension.RESEARCH_EXTENSION_VERSION
        == negotiation["extension_version"]
        == api["mcp_extensions"]["acp.research.v1"]["version"]
        == "1.0.0"
    )
    # The application contract *version* is not the extension version (B06 drift).
    # M0-06 -> 1.2.0 (field policy); D-IDENT-10 -> 1.3.0 (prepare field table);
    # M1-01 -> 1.4.0 (negotiation relation projection + required submit key);
    # M2-01c -> 1.5.0 (implemented projection + research tool visibility rule).
    assert api["contract_version"] == "1.7.0"
    assert api["contract_version"] != mcp_extension.RESEARCH_EXTENSION_VERSION
    # M1-01a: the projection states the advertised/implemented relation and never
    # promotes advertising to delivery.  M2-01c registers all ten promised tools,
    # so the projection flips to implemented=true with the tool visibility rule.
    projection = api["mcp_extensions"]["acp.research.v1"]
    assert projection["advertised"] is True
    assert projection["implemented"] is True
    assert set(projection["implemented_tools"]) == set(mcp.RESEARCH_TOOL_NAMES)
    assert projection["implemented_by"] == "M2-01c"
    assert projection["negotiated_default"] is False
    assert projection["negotiated_requires"] == ["implemented", "requested_version == version"]
    assert projection["partial_implementation_negotiated"] is False
    assert "extension_not_negotiated" in projection["tool_visibility"]
    assert set(projection["promised_tools"]) == set(mcp.RESEARCH_TOOL_NAMES)


def test_research_promise_set_matches_the_contract_rows() -> None:
    p2 = json.loads(
        (_CONTRACTS / "runtime" / "p2-research-operations.v1.json").read_text(encoding="utf-8")
    )
    promised = {tool["name"] for row in p2["rows"] for tool in row["tools"]}
    assert set(mcp.RESEARCH_TOOL_NAMES) == promised
    # Ten unique names, all registered by M2-01c, none of them a P1 name: the
    # registered set covers the promise set without widening the P1 allowlist.
    assert len(mcp.RESEARCH_TOOL_NAMES) == 10
    assert mcp.RESEARCH_TOOL_SET <= mcp.REGISTERED_TOOL_NAMES
    assert not (set(mcp.RESEARCH_TOOL_NAMES) & mcp.TOOL_ALLOWLIST)
    assert set(mcp.RESEARCH_TOOL_OPERATIONS) == set(mcp.RESEARCH_TOOL_NAMES)


def test_carrier_is_the_p1_discovery_tool_not_initialize() -> None:
    assert mcp.EXTENSION_HANDSHAKE_CARRIER == "tools/call:acp_capabilities"
    result = _server().handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert result is not None
    initialize = result["result"]
    assert "extensions" not in initialize
    encoded = json.dumps(initialize)
    assert "acp.research.v1" not in encoded
    guidance = initialize["capabilities"]["extensions"]["acp.negotiation.v1"]
    assert guidance["extension_handshake"] == {
        "carrier": "tools/call",
        "tool": "acp_capabilities",
        "result_field": "extensions",
    }


# --------------------------------------------------------------------------- #
# 2. Advertised / implemented / negotiated
# --------------------------------------------------------------------------- #


def test_capabilities_always_advertises_the_extension_without_claiming_it() -> None:
    result = _call_tool(_server())
    assert result["isError"] is False
    entry = _entry(result)
    assert entry["id"] == "acp.research.v1"
    assert entry["version"] == "1.0.0"
    assert entry["supported"] is True
    assert entry["supported_versions"] == ["1.0.0"]
    assert entry["requested"] is None
    assert entry["negotiated_by"] == "tools/call:acp_capabilities"
    # M2-01c registers the ten promised tools, so `implemented` is true — while a
    # plain discovery read still makes no negotiated claim.
    assert entry["implemented"] is True
    assert entry["negotiated"] is False
    assert entry["refusal"] is None
    data = result["structuredContent"]["data"]
    assert data["research_tools"]["visible"] is False
    assert len(data["tools"]) == 8


def test_exact_version_request_negotiates_only_a_covering_implementation() -> None:
    server = _server()
    before = server.visible_tool_names()
    assert len(before) == 8
    result = _call_tool(
        server, {"research_extension": {"id": "acp.research.v1", "version": "1.0.0"}}
    )
    # The covering registration plus the matching version is the only way to
    # negotiate; the P1 allowlist itself is untouched.
    assert result["isError"] is False
    entry = _entry(result)
    assert entry["requested"] == {"id": "acp.research.v1", "version": "1.0.0"}
    assert entry["implemented"] is True
    assert entry["negotiated"] is True
    assert entry["refusal"] is None
    assert len(mcp.TOOL_ALLOWLIST) == 8
    # Only now does the model-visible surface grow.
    assert server.research_negotiated is True
    assert len(server.visible_tool_names()) == 18


def test_a_covering_implementation_set_is_the_only_way_to_negotiate() -> None:
    """The M1-01 rule, proven at unit level: cover the promise set -> negotiated."""
    complete = mcp.ExtensionSpec(
        extension_id="acp.research.v1",
        version="1.0.0",
        promised_tools=mcp.RESEARCH_TOOL_NAMES,
    )
    registered = frozenset(mcp.TOOL_ALLOWLIST) | frozenset(mcp.RESEARCH_TOOL_NAMES)
    handshake = mcp.negotiate_research_extension(
        complete, {"id": "acp.research.v1", "version": "1.0.0"}, registered=registered
    )
    assert handshake.negotiated is True
    assert handshake.refusal is None
    assert handshake.entries[0]["implemented"] is True
    assert handshake.entries[0]["negotiated"] is True
    # A partial set is still refused: no partial negotiation.
    partial = registered - {"acp_research_resume_v1"}
    refused = mcp.negotiate_research_extension(
        complete, {"id": "acp.research.v1", "version": "1.0.0"}, registered=partial
    )
    assert refused.negotiated is False
    assert refused.refusal is not None
    assert refused.refusal["params"]["rule"] == "extension_not_implemented"


def test_version_mismatch_and_unknown_extension_are_typed_refusals() -> None:
    mismatch = _call_tool(
        _server(), {"research_extension": {"id": "acp.research.v1", "version": "1.1.0"}}
    )
    assert mismatch["isError"] is True
    assert mismatch["structuredContent"]["error"]["code"] == "unsupported_capability"
    assert _entry(mismatch)["refusal"]["params"]["rule"] == "extension_version_mismatch"
    assert _entry(mismatch)["refusal"]["params"]["supported_version"] == "1.0.0"

    unknown = _call_tool(
        _server(), {"research_extension": {"id": "acp.research.v2", "version": "1.0.0"}}
    )
    assert unknown["isError"] is True
    assert _entry(unknown)["refusal"]["params"]["rule"] == "extension_not_advertised"


def test_server_without_an_extension_advertises_none_and_refuses_requests() -> None:
    bare = _call_tool(_server(extension=None))
    assert bare["isError"] is False
    assert bare["structuredContent"]["data"]["extensions"] == []

    refused = _call_tool(_server(extension=None), {"research_extension": {"id": "acp.research.v1"}})
    assert refused["isError"] is True
    assert refused["structuredContent"]["data"]["extensions"] == []
    assert refused["structuredContent"]["error"]["code"] == "unsupported_capability"
    assert refused["structuredContent"]["error"]["params"]["rule"] == "extension_not_advertised"


def test_malformed_handshake_request_is_input_invalid() -> None:
    for arguments in (
        # the MCP tool schema declares an object; the compact `id@version`
        # spelling belongs to the HTTP query surface only.
        {"research_extension": "acp.research.v1"},
        {"research_extension": {}},
        {"research_extension": {"id": ""}},
        {"research_extension": {"id": "acp.research.v1", "version": ""}},
        {"project_id": 7},
        {"client": "dsh"},
    ):
        result = _call_tool(_server(), arguments)
        assert result["isError"] is True, arguments
        assert result["structuredContent"]["error"]["code"] == "input_invalid", arguments


# --------------------------------------------------------------------------- #
# 3. Server-resolved identity
# --------------------------------------------------------------------------- #


def test_identity_comes_from_the_server_binding_and_meta_is_not_trusted() -> None:
    result = _call_tool(
        _server(),
        {"client": {"name": "dsh-mcp-client", "version": "0.0.1"}},
        meta={"tenant_id": "attacker-tenant", "project_id": "proj-a", "display_name": "nobody"},
    )
    data = result["structuredContent"]["data"]
    assert data["identity_binding"]["binding"] == "local-single"
    assert "attacker-tenant" not in json.dumps(data)
    assert "nobody" not in json.dumps(data)
    # The audit echo is exactly what the caller sent, nothing derived from _meta.
    assert data["capability_request"]["client"] == {
        "name": "dsh-mcp-client",
        "version": "0.0.1",
    }
    assert data["capability_request"]["authorization"].startswith("none")


def test_project_hint_cannot_select_the_scope_and_a_mismatch_is_denied() -> None:
    # A hint on its own is advisory and never an error: the scope stays the
    # server-resolved one (no project chosen here).
    advisory = _call_tool(_server(), {"project_id": "proj-a"})
    assert advisory["isError"] is False
    data = advisory["structuredContent"]["data"]
    assert data["capability_request"]["project_id"] == "proj-a"
    assert data["identity_binding"]["binding"] == "local-single"
    assert data["identity_binding"]["project_id"] is None

    bound = mcp.IdentityMapper(
        bound_principal=LocalPrincipal.resolve(),
        bound_project="proj-a",
        bound_actions=("read", "write"),
    )
    denied = _call_tool(_server(identity=bound), {"project_id": "proj-b"})
    assert denied["isError"] is True
    error = denied["structuredContent"]["error"]
    assert error["code"] == "access_denied"
    assert error["params"]["rule"] == "project_scope_mismatch"
    # extensions[] stays readable even on a denial.
    assert _entry(denied)["id"] == "acp.research.v1"

    allowed = _call_tool(_server(identity=bound), {"project_id": "proj-a"})
    assert allowed["isError"] is False
    assert allowed["structuredContent"]["data"]["identity_binding"]["project_id"] == "proj-a"


def test_tier_override_in_meta_is_still_refused() -> None:
    result = _call_tool(_server(), meta={"deployment_tier": "multi-user"})
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "access_denied"


# --------------------------------------------------------------------------- #
# 4. HTTP equivalents + Controller client
# --------------------------------------------------------------------------- #


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    run_root = tmp_path / "run-root"
    run_root.mkdir()
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    monkeypatch.delenv("ACP_IDENTITY_MODE", raising=False)
    from fastapi.testclient import TestClient

    from acp.api.server import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client


def _http_detail(response: Any) -> dict[str, Any]:
    body = response.json()
    assert "detail" in body, body
    return body["detail"]


def test_http_query_and_body_surfaces_are_equivalent(api_client: Any) -> None:
    body = api_client.post(
        "/api/capabilities",
        json={
            "schema_version": 1,
            "research_extension": {"id": "acp.research.v1", "version": "1.0.0"},
            "client": {"name": "rph", "version": "0.0.0"},
        },
    )
    query = api_client.get(
        "/api/capabilities",
        params={"research_extension": "acp.research.v1@1.0.0", "client": "rph/0.0.0"},
    )
    assert body.status_code == query.status_code == 200
    body_json = body.json()
    query_json = query.json()
    assert body_json["extensions"] == query_json["extensions"]
    assert body_json["extensions"][0]["implemented"] is True
    assert body_json["extensions"][0]["negotiated"] is True
    assert body_json["extensions"][0]["refusal"] is None


def test_http_plain_read_still_answers_the_advertisement(api_client: Any) -> None:
    response = api_client.get("/api/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["extensions"][0]["id"] == "acp.research.v1"
    assert payload["extensions"][0]["implemented"] is True
    # No request in this read: nothing is negotiated, so the P1 surface stands.
    assert payload["extensions"][0]["negotiated"] is False
    assert payload["capability_request"]["research_extension"] is None
    assert len(payload["tools"]) == 8


def test_http_version_mismatch_and_unknown_fields_are_typed(api_client: Any) -> None:
    mismatch = api_client.get(
        "/api/capabilities", params={"research_extension": "acp.research.v1@1.1.0"}
    )
    assert mismatch.status_code == 422
    assert _http_detail(mismatch)["params"]["rule"] == "extension_version_mismatch"

    unknown_field = api_client.post("/api/capabilities", json={"bogus": 1})
    assert unknown_field.status_code == 422
    assert _http_detail(unknown_field)["code"] == "input_invalid"

    bad_shape = api_client.post("/api/capabilities", json={"research_extension": "nope"})
    assert bad_shape.status_code == 422
    assert _http_detail(bad_shape)["code"] == "input_invalid"


def test_controller_client_capabilities_sends_the_real_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, Any]] = []

    def fake_request(
        self: ControllerClient, method: str, path: str, payload: Any = None, **_: Any
    ) -> Any:
        calls.append((method, path, payload))
        return {
            "schema_version": 1,
            "task_kinds": ["singlepoint"],
            "workflows": ["singlepoint"],
            "backends": [],
            "limits": {},
            "tools": ["acp_capabilities"],
            "protocol_version": "2025-06-18",
            "extensions": [
                {
                    "id": "acp.research.v1",
                    "version": "1.0.0",
                    "implemented": False,
                    "negotiated": False,
                }
            ],
        }

    monkeypatch.setattr(ControllerClient, "_request_json", fake_request)
    client = ControllerClient("http://127.0.0.1:1")

    plain = client.capabilities()
    assert calls[-1] == ("GET", "/api/capabilities", None)
    assert isinstance(plain, Capabilities)
    assert plain.extensions[0]["id"] == "acp.research.v1"

    client.capabilities(
        research_extension={"id": "acp.research.v1", "version": "1.0.0"},
        project_id="proj-a",
        client={"name": "rph", "version": "0.0.0"},
    )
    method, path, payload = calls[-1]
    assert (method, path) == ("POST", "/api/capabilities")
    assert payload == {
        "schema_version": 1,
        "research_extension": {"id": "acp.research.v1", "version": "1.0.0"},
        "project_id": "proj-a",
        "client": {"name": "rph", "version": "0.0.0"},
    }

    client.capabilities(research_extension="acp.research.v1@1.0.0")
    assert calls[-1][2]["research_extension"] == {"id": "acp.research.v1", "version": "1.0.0"}

    with pytest.raises(InvalidParameterError):
        client.capabilities(research_extension={"version": "1.0.0"})
