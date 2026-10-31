"""M2-01c — the ten P2 research tools, their transport and the negotiated gate.

The checkpoint's exit condition is "live tool + ControllerClient method +
application use case ship together".  These tests pin all three layers and the
fail-closed rule that makes negotiation meaningful:

* :func:`test_ten_tools_map_one_to_one_to_operations_and_client_methods` — the
  closed mapping (tool → operation → ``ControllerClient`` method → service);
* :func:`test_tools_list_hides_research_tools_until_the_session_negotiates` and
  :func:`test_unnegotiated_research_calls_are_fail_closed` — ``tools/list`` is
  the model-visible surface and an unnegotiated call never reaches a port;
* :func:`test_negotiated_calls_reach_the_application_service` — all ten tools
  forward the declared arguments to the port (the MCP adapter writes nothing);
* :func:`test_the_ten_tool_schemas_keep_the_m1_01_conventions` — frozen
  ``additionalProperties: false`` + required sets + typed unknown-field refusal;
* :func:`test_controller_client_paths_match_the_real_routes` — the client
  transport of every tool;
* :func:`test_http_routes_are_the_tool_transport` — the same operations over
  real HTTP routes (create → snapshot → wait → resume → reconcile);
* :func:`test_wait_is_bounded_and_the_cursor_expires_explicitly` — the frozen
  wait bounds and the cursor reset;
* :func:`test_controller_port_maps_one_method_per_operation` — the Controller
  adapter's method/route matrix.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from acp import mcp_extension
from acp_services.data.research_resources import ResearchResourceStore
from acp_services.entrypoints import mcp
from acp_services.entrypoints.controller_client import (
    P2_OPERATION_METHODS,
    ControllerClient,
    InvalidParameterError,
)
from acp_services.entrypoints.research_port import ControllerResearchPort
from acp_services.runtime.approvals import ApprovalService
from acp_services.runtime.local_outbox import LOCAL_OUTBOX_COVERAGE
from acp_services.runtime.research_saga import SagaStore
from acp_services.runtime.research_service import (
    P2_SERVICE_METHODS,
    WAIT_MAX_LIMIT,
    WAIT_MAX_WAIT_S,
    ResearchOperationError,
    ResearchOperationNotReadyError,
    ResearchOperations,
    decode_wait_cursor,
    encode_wait_cursor,
)
from acp_services.runtime.research_writer import ResearchWriter
from acp_services.workspace.command_identity import CommandIdentity

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = REPO_ROOT / "docs/reference/next-change/acp2-contracts"

PROJECT = "proj-p2"
PRINCIPAL = "alice"
SEED = "seed-p2"
PROPOSAL_ID = "prop-p2-001"

PLAN: dict[str, Any] = {
    "workflow": "fake",
    "input": {"source": "CCO", "source_type": "smiles"},
    "method": {},
    "resources": {"cores": 1},
}


def build_proposal(proposal_id: str = PROPOSAL_ID) -> dict[str, Any]:
    return {
        "proposal_id": proposal_id,
        "project_id": PROJECT,
        "recipe_digest": "sha256:" + "a" * 64,
        "science": {
            "capability_steps": ["singlepoint"],
            "initial_inputs": [
                {
                    "input_id": "in-p2",
                    "sha256": "sha256:" + "b" * 64,
                    "format": "smiles",
                    "role": "minimum",
                }
            ],
            "method": "r2SCAN-3c",
            "mapping": "canonical",
            "charge": 0,
            "multiplicity": 1,
            "allowed_substitutions": [],
        },
        "budget": {
            "model_requests": 4,
            "input_tokens": 1000,
            "output_tokens": 1000,
            "task_refs": 1,
            "recipe_steps": 1,
            "qc_invocations": 1,
            "wall_seconds": 60,
            "queue_seconds": 60,
        },
        "approval_ref": None,
        "allowed_change_scope": {"fields": ["method"], "max_delta": 0},
        "round": 0,
        "decision": "proposed",
        "created_at": "2026-10-27T00:00:00Z",
        "human_edits": [],
    }


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeCapabilities:
    def capabilities(self) -> dict[str, Any]:
        return {"workflows": ["singlepoint"], "backends": []}


class _Unused:
    """Any P1 port: reaching it from these tests is a bug."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected P1 port call: {name}")


class _RecordingResearchPort:
    """A port that records the forwarded arguments (the MCP adapter writes nothing)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any], str]] = []

    def _record(self, method: str, arguments: Mapping[str, Any], workspace_id: str) -> Any:
        self.calls.append((method, dict(arguments), workspace_id))
        return {"method": method, "arguments": dict(arguments)}

    def proposal_create(self, arguments, *, workspace_id):
        return self._record("proposal_create", arguments, workspace_id)

    def proposal_read(self, arguments, *, workspace_id):
        return self._record("proposal_read", arguments, workspace_id)

    def proposal_update_cas(self, arguments, *, workspace_id):
        return self._record("proposal_update_cas", arguments, workspace_id)

    def approval_request(self, arguments, *, workspace_id):
        return self._record("approval_request", arguments, workspace_id)

    def approval_query(self, arguments, *, workspace_id):
        return self._record("approval_query", arguments, workspace_id)

    def approval_decide(self, arguments, *, workspace_id):
        return self._record("approval_decide", arguments, workspace_id)

    def approval_revoke(self, arguments, *, workspace_id):
        return self._record("approval_revoke", arguments, workspace_id)

    def wait(self, arguments, *, workspace_id):
        return self._record("wait", arguments, workspace_id)

    def snapshot(self, arguments, *, workspace_id):
        return self._record("snapshot", arguments, workspace_id)

    def resume(self, arguments, *, workspace_id):
        return self._record("resume", arguments, workspace_id)


def _server(research: Any = None, *, extension: Any = mcp_extension.RESEARCH_EXTENSION_SPEC):
    backends = mcp.McpBackends(
        capabilities=_FakeCapabilities(),
        preflight=_Unused(),
        submit=_Unused(),
        status=_Unused(),
        cancel=_Unused(),
        artifacts=_Unused(),
        analysis=_Unused(),
        research=research,
    )
    return mcp.McpServer(backends=backends, extension=extension)


def _negotiate(server: mcp.McpServer) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": mcp.McpTool.CAPABILITIES,
                "arguments": {"research_extension": {"id": "acp.research.v1", "version": "1.0.0"}},
            },
        }
    )
    assert response is not None and "result" in response, response
    assert response["result"]["isError"] is False, response
    return response["result"]


def _call(server: mcp.McpServer, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": dict(arguments)},
        }
    )
    assert response is not None and "result" in response, response
    return response["result"]


def _tools_list(server: mcp.McpServer) -> list[str]:
    response = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
    assert response is not None
    return [tool["name"] for tool in response["result"]["tools"]]


# ---------------------------------------------------------------------------
# 1. The three layers ship together
# ---------------------------------------------------------------------------


def test_ten_tools_map_one_to_one_to_operations_and_client_methods() -> None:
    p2 = json.loads(
        (CONTRACTS / "runtime" / "p2-research-operations.v1.json").read_text(encoding="utf-8")
    )
    promised = {tool["name"] for row in p2["rows"] for tool in row["tools"]}
    assert set(mcp.RESEARCH_TOOL_NAMES) == promised
    assert set(mcp.RESEARCH_TOOL_OPERATIONS) == promised
    assert set(mcp.RESEARCH_TOOL_OPERATIONS.values()) == set(P2_OPERATION_METHODS)
    assert set(mcp.RESEARCH_TOOL_OPERATIONS.values()) == set(P2_SERVICE_METHODS)
    assert len(promised) == 10
    # Every operation has a ControllerClient method and a service method.
    client = ControllerClient("http://127.0.0.1:1")
    for operation, method in P2_OPERATION_METHODS.items():
        assert callable(getattr(client, method)), operation
        assert callable(getattr(ResearchOperations, P2_SERVICE_METHODS[operation])), operation
        assert callable(getattr(ControllerResearchPort, operation)), operation
    # The adapter's own port protocol declares the same ten operations.
    for operation in P2_OPERATION_METHODS:
        assert hasattr(mcp.ResearchPort, operation), operation
    # Coverage: every research fact crosses the four declared databases.
    assert set(LOCAL_OUTBOX_COVERAGE) == {"jobs", "research", "admission", "coordination"}


def test_the_ten_tool_schemas_keep_the_m1_01_conventions() -> None:
    for tool in mcp.RESEARCH_TOOL_NAMES:
        spec = mcp._ARGUMENT_SCHEMAS[tool]
        assert spec["additionalProperties"] is False, tool
        assert isinstance(spec["required"], list), tool
        assert set(spec["required"]) <= set(spec["properties"]), tool
    # The frozen required sets of the contract rows.
    assert mcp._ARGUMENT_SCHEMAS["acp_research_approval_decide_v1"]["required"] == [
        "approval_ref",
        "decision",
        "expected_revision",
    ]
    assert mcp._ARGUMENT_SCHEMAS["acp_research_resume_v1"]["required"] == [
        "proposal_ref",
        "plan_digest",
    ]
    assert mcp._ARGUMENT_SCHEMAS["acp_research_wait_v1"]["required"] == ["kinds"]
    assert mcp._ARGUMENT_SCHEMAS["acp_research_snapshot_v1"]["required"] == ["refs"]
    # A declared-but-unknown field is a typed refusal before any port is touched.
    server = _server(_RecordingResearchPort())
    _negotiate(server)
    result = _call(
        server,
        "acp_research_wait_v1",
        {"kinds": ["approval"], "approver": "model"},
    )
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "input_invalid"
    assert error["params"]["rule"] == "unknown_field"
    assert error["params"]["field"] == "approver"


# ---------------------------------------------------------------------------
# 2. Negotiation is the visibility and call gate
# ---------------------------------------------------------------------------


def test_tools_list_hides_research_tools_until_the_session_negotiates() -> None:
    server = _server(_RecordingResearchPort())
    before = _tools_list(server)
    assert len(before) == 10
    assert not set(before) & mcp.RESEARCH_TOOL_SET
    payload = _negotiate(server)
    data = payload["structuredContent"]["data"]
    assert data["research_tools"]["visible"] is True
    assert set(data["research_tools"]["registered"]) == mcp.RESEARCH_TOOL_SET
    entry = data["extensions"][0]
    assert (entry["implemented"], entry["negotiated"]) == (True, True)
    after = _tools_list(server)
    # this server has no selection port, so the two PR-07 tools stay invisible
    selection_tools = {
        mcp.McpTool.GET_PES_REVIEW,
        mcp.McpTool.PUBLISH_PES_REVIEW,
    }
    assert len(after) == 20
    assert set(after) == (set(mcp.TOOL_ALLOWLIST) - selection_tools) | mcp.RESEARCH_TOOL_SET


@pytest.mark.parametrize("tool", mcp.RESEARCH_TOOL_NAMES)
def test_unnegotiated_research_calls_are_fail_closed(tool: str) -> None:
    port = _RecordingResearchPort()
    server = _server(port)
    result = _call(server, tool, {})
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "unsupported_capability"
    assert error["params"]["rule"] == "extension_not_negotiated"
    assert error["params"]["tool"] == tool
    # Nothing reached the application service.
    assert port.calls == []


def test_negotiated_calls_reach_the_application_service() -> None:
    port = _RecordingResearchPort()
    server = _server(port)
    _negotiate(server)
    cases = {
        "acp_research_proposal_create_v1": {
            "project_id": PROJECT,
            "proposal": build_proposal(),
            "plan": PLAN,
        },
        "acp_research_proposal_read_v1": {"proposal_ref": PROPOSAL_ID},
        "acp_research_proposal_update_cas_v1": {
            "proposal_ref": PROPOSAL_ID,
            "expected_revision": 1,
            "proposal": build_proposal(),
            "plan": PLAN,
        },
        "acp_research_approval_request_v1": {
            "proposal_ref": PROPOSAL_ID,
            "plan_digest": "sha256:" + "a" * 64,
        },
        "acp_research_approval_query_v1": {"proposal_ref": PROPOSAL_ID},
        "acp_research_approval_decide_v1": {
            "approval_ref": "ap-1",
            "decision": "approve",
            "expected_revision": 1,
        },
        "acp_research_approval_revoke_v1": {"approval_ref": "ap-1", "expected_revision": 2},
        "acp_research_wait_v1": {"kinds": ["approval"], "max_wait_s": 0},
        "acp_research_snapshot_v1": {"refs": ["approval:ap-1"]},
        "acp_research_resume_v1": {
            "proposal_ref": PROPOSAL_ID,
            "plan_digest": "sha256:" + "a" * 64,
        },
    }
    assert set(cases) == set(mcp.RESEARCH_TOOL_NAMES)
    for tool, arguments in cases.items():
        result = _call(server, tool, arguments)
        assert result["isError"] is False, (tool, result)
        data = result["structuredContent"]["data"]
        assert data["tool"] == tool
        assert data["operation"] == mcp.RESEARCH_TOOL_OPERATIONS[tool]
    assert [call[0] for call in port.calls] == [
        mcp.RESEARCH_TOOL_OPERATIONS[tool] for tool in mcp.RESEARCH_TOOL_NAMES
    ]
    # The declared arguments are forwarded verbatim (no adapter-side rewriting).
    assert port.calls[0][1]["proposal"]["proposal_id"] == PROPOSAL_ID
    assert port.calls[0][2]  # the server-resolved workspace id, never a client claim


class _WaitCursorPort(_RecordingResearchPort):
    """A port whose bounded wait returns one real (server-issued) cursor."""

    def __init__(self, cursor: str) -> None:
        super().__init__()
        self.cursor = cursor

    def wait(self, arguments, *, workspace_id):
        self.calls.append(("wait", dict(arguments), workspace_id))
        return {
            "events": [],
            "cursor": self.cursor,
            "snapshot_revision": 1,
            "timed_out": True,
        }


def test_wait_cursor_survives_the_machine_envelope() -> None:
    """M4-01/F8: an issued cursor round-trips through the envelope verbatim."""
    issued = encode_wait_cursor(
        snapshot_revision="sha256:" + "b" * 64,
        project_id=PROJECT,
        issued_at=time.time(),
    )
    server = _server(_WaitCursorPort(issued))
    _negotiate(server)
    result = _call(server, "acp_research_wait_v1", {"kinds": ["approval"], "max_wait_s": 0})
    assert result["isError"] is False, result
    data = result["structuredContent"]["data"]
    assert data["cursor"] == issued
    assert "<redacted" not in json.dumps(data)
    # The text frame is the same envelope: the cursor is not lost in one of them.
    assert issued in result["content"][0]["text"]
    # And it is a *real* cursor: the server decoder accepts the round-tripped value
    # and a replayed wait continues from it instead of failing as undecodable.
    decoded = decode_wait_cursor(data["cursor"], project_id=PROJECT, now=time.time())
    assert decoded["project"] == PROJECT
    assert decoded["rev"] == "sha256:" + "b" * 64


def test_a_credential_under_the_cursor_field_is_still_redacted() -> None:
    """The envelope exemption is shape-checked, not a blanket field whitelist."""
    server = _server(_WaitCursorPort("Bearer " + "c" * 32))
    _negotiate(server)
    result = _call(server, "acp_research_wait_v1", {"kinds": ["approval"], "max_wait_s": 0})
    assert result["isError"] is False, result
    assert "<redacted" in result["structuredContent"]["data"]["cursor"]


def test_missing_research_port_is_a_typed_refusal_not_a_transport_error() -> None:
    server = _server(None)
    _negotiate(server)
    result = _call(server, "acp_research_snapshot_v1", {"refs": ["approval:ap-1"]})
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "unsupported_capability"
    assert error["params"]["rule"] == "research_service_unavailable"


# ---------------------------------------------------------------------------
# 3. ControllerClient transport + Controller port
# ---------------------------------------------------------------------------


def test_controller_client_paths_match_the_real_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, Any]] = []

    def fake_request(self, method, path, payload=None, **kwargs):
        calls.append((method, path, payload))
        return {"ok": True}

    monkeypatch.setattr(ControllerClient, "_request_json", fake_request)
    client = ControllerClient("http://127.0.0.1:1")
    client.create_proposal({"proposal": {}, "plan": {}, "project_id": PROJECT})
    client.read_proposal(PROPOSAL_ID, project_id=PROJECT, revision=1)
    client.update_proposal_cas(PROPOSAL_ID, {"expected_revision": 1, "proposal": {}, "plan": {}})
    client.request_approval({"proposal_ref": PROPOSAL_ID, "plan_digest": "sha256:" + "a" * 64})
    client.query_approval(proposal_ref=PROPOSAL_ID, limit=5)
    client.decide_approval("ap-1", decision="approve", expected_revision=1)
    client.decide_approval("ap-1", decision="rejected", expected_revision=2)
    client.revoke_approval("ap-1", expected_revision=2, reason="changed")
    client.wait_research(["approval"], max_wait_s=0, limit=5)
    client.snapshot_research(["approval:ap-1"], project_id=PROJECT)
    client.resume_research(PROPOSAL_ID, "sha256:" + "a" * 64, diagnostic_only=True)
    client.read_saga("saga-1")
    client.reconcile_saga("saga-1", action="advance", expected_revision=2)

    paths = [(method, path.split("?")[0]) for method, path, _payload in calls]
    assert paths == [
        ("POST", "/api/v2/research/proposals"),
        ("GET", f"/api/v2/research/proposals/{PROPOSAL_ID}"),
        ("POST", f"/api/v2/research/proposals/{PROPOSAL_ID}/cas"),
        ("POST", "/api/v2/research/approvals"),
        ("GET", "/api/v2/research/approvals"),
        ("POST", "/api/v2/research/approvals/ap-1/approve"),
        ("POST", "/api/v2/research/approvals/ap-1/reject"),
        ("POST", "/api/v2/research/approvals/ap-1/revoke"),
        ("POST", "/api/v2/research/wait"),
        ("GET", "/api/v2/research/snapshot"),
        ("POST", "/api/v2/research/resume"),
        ("GET", "/api/v2/research/sagas/saga-1"),
        ("POST", "/api/v2/research/sagas/saga-1/reconcile"),
    ]
    # A missing CAS revision / bad decision is refused before any network call.
    with pytest.raises(InvalidParameterError):
        client.update_proposal_cas(PROPOSAL_ID, {"proposal": {}})
    with pytest.raises(InvalidParameterError):
        client.decide_approval("ap-1", decision="maybe", expected_revision=1)


def test_controller_port_maps_one_method_per_operation() -> None:
    calls: list[tuple[str, Any]] = []

    class FakeClient:
        def __getattr__(self, name: str) -> Any:
            def method(*args: Any, **kwargs: Any) -> Any:
                calls.append((name, (args, kwargs)))
                return {"ok": True}

            return method

    port = ControllerResearchPort(FakeClient())  # type: ignore[arg-type]
    port.proposal_create(
        {"project_id": PROJECT, "proposal": build_proposal(), "plan": PLAN}, workspace_id=PROJECT
    )
    port.proposal_read({"proposal_ref": PROPOSAL_ID}, workspace_id=PROJECT)
    port.proposal_update_cas(
        {"proposal_ref": PROPOSAL_ID, "expected_revision": 1, "proposal": {}, "plan": {}},
        workspace_id=PROJECT,
    )
    port.approval_request(
        {"proposal_ref": PROPOSAL_ID, "plan_digest": "sha256:" + "a" * 64}, workspace_id=PROJECT
    )
    port.approval_query({"proposal_ref": PROPOSAL_ID}, workspace_id=PROJECT)
    port.approval_decide(
        {"approval_ref": "ap-1", "decision": "approve", "expected_revision": 1},
        workspace_id=PROJECT,
    )
    port.approval_revoke({"approval_ref": "ap-1", "expected_revision": 2}, workspace_id=PROJECT)
    port.wait({"kinds": ["approval"], "max_wait_s": 0}, workspace_id=PROJECT)
    port.snapshot({"refs": ["approval:ap-1"]}, workspace_id=PROJECT)
    port.resume(
        {"proposal_ref": PROPOSAL_ID, "plan_digest": "sha256:" + "a" * 64},
        workspace_id=PROJECT,
    )
    assert [name for name, _ in calls] == [
        P2_OPERATION_METHODS[operation] for operation in mcp.RESEARCH_TOOL_OPERATIONS.values()
    ]


# ---------------------------------------------------------------------------
# 4. The application service: wait bounds, cursor, snapshot, HTTP routes
# ---------------------------------------------------------------------------


class Harness:
    def __init__(self, root: Path) -> None:
        self.resources = ResearchResourceStore(root / "acp_research.db")
        self.approvals = ApprovalService(root / "acp_jobs.db")
        self.sagas = SagaStore(root / "acp_research_saga.db")
        self.writer = ResearchWriter(resources=self.resources, approvals=self.approvals)
        self.command = CommandIdentity(principal_id=PRINCIPAL, project_id=PROJECT)
        self.operations = ResearchOperations(
            writer=self.writer,
            resources=self.resources,
            approvals=self.approvals,
            sagas=self.sagas,
            submit=lambda payload: {
                "task_ref": {
                    "task_id": "job-p2-1",
                    "job_id": "job-p2-1",
                    "attempt": 1,
                    "release_id": "rel-p2",
                    "project_id": PROJECT,
                }
            },
        )

    def close(self) -> None:
        self.resources.close()
        self.sagas.close()


def test_wait_is_bounded_and_the_cursor_expires_explicitly(tmp_path: Path) -> None:
    harness = Harness(tmp_path / "run-root")
    try:
        with pytest.raises(ResearchOperationError) as over:
            harness.operations.research_wait(
                command=harness.command, kinds=["approval"], max_wait_s=WAIT_MAX_WAIT_S + 1
            )
        assert over.value.rule == "bound_exceeded"
        with pytest.raises(ResearchOperationError) as limit:
            harness.operations.research_wait(
                command=harness.command, kinds=["approval"], limit=WAIT_MAX_LIMIT + 1
            )
        assert limit.value.rule == "bound_exceeded"
        with pytest.raises(ResearchOperationError):
            harness.operations.research_wait(command=harness.command, kinds=["bogus"])

        first = harness.operations.research_wait(
            command=harness.command, kinds=["approval"], max_wait_s=0
        )
        assert first["timed_out"] is False
        replay = harness.operations.research_wait(
            command=harness.command, kinds=["approval"], cursor=first["cursor"], max_wait_s=0
        )
        # The same revision was already observed: the bounded wait times out rather
        # than fabricating an event.
        assert replay["timed_out"] is True
        assert replay["events"] == []

        expired = encode_wait_cursor(
            snapshot_revision="sha256:" + "a" * 64,
            project_id=PROJECT,
            issued_at=time.time() - 1000,
        )
        with pytest.raises(ResearchOperationNotReadyError) as excinfo:
            harness.operations.research_wait(
                command=harness.command, kinds=["approval"], cursor=expired, max_wait_s=0
            )
        assert excinfo.value.rule == "cursor_expired"
        with pytest.raises(ResearchOperationError):
            decode_wait_cursor("not-a-cursor", project_id=PROJECT, now=time.time())
    finally:
        harness.close()


def test_snapshot_and_wait_observe_the_real_authorities(tmp_path: Path) -> None:
    harness = Harness(tmp_path / "run-root")
    try:
        created = harness.operations.proposal_create(
            command=harness.command,
            proposal=build_proposal(),
            plan=PLAN,
            session_seed=SEED,
        )
        approval_ref = created["approval"]["approval_ref"]
        snapshot = harness.operations.research_snapshot(
            command=harness.command,
            refs=[f"approval:{approval_ref}", f"proposal:{PROPOSAL_ID}", "saga:missing"],
        )
        refs = {entry["ref"]: entry for entry in snapshot["snapshot"]["refs"]}
        assert refs[approval_ref]["state"] == "pending"
        assert refs[PROPOSAL_ID]["state"] == "stored"
        assert refs["missing"]["state"] == "unknown"
        assert snapshot["snapshot_revision"].startswith("sha256:")

        approval = harness.approvals.get(approval_ref)
        assert approval is not None
        harness.approvals.approve(
            approval_ref, approver_principal=PRINCIPAL, expected_revision=approval.revision
        )
        changed = harness.operations.research_wait(
            command=harness.command,
            kinds=["approval"],
            cursor=snapshot["cursor"] if "cursor" in snapshot else None,
            max_wait_s=0,
        )
        assert any(event["kind"] == "approval" for event in changed["events"])
    finally:
        harness.close()


def test_resume_without_a_saga_is_a_typed_not_ready(tmp_path: Path) -> None:
    harness = Harness(tmp_path / "run-root")
    try:
        with pytest.raises(ResearchOperationNotReadyError) as excinfo:
            harness.operations.research_resume(
                command=harness.command,
                proposal_ref=PROPOSAL_ID,
                plan_digest="sha256:" + "a" * 64,
            )
        assert excinfo.value.rule == "saga_not_found"
    finally:
        harness.close()


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    run_root = tmp_path / "run-root"
    run_root.mkdir()
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    monkeypatch.delenv("ACP_IDENTITY_MODE", raising=False)
    from fastapi.testclient import TestClient

    from acp.api.server import create_app

    with TestClient(create_app()) as client:
        yield client


def test_http_routes_are_the_tool_transport(api_client: Any) -> None:
    capabilities = api_client.get(
        "/api/capabilities", params={"research_extension": "acp.research.v1@1.0.0"}
    )
    assert capabilities.status_code == 200
    payload = capabilities.json()
    assert payload["extensions"][0]["negotiated"] is True
    assert payload["research_tools"]["visible"] is True
    assert set(payload["tools"]) == set(mcp.TOOL_ALLOWLIST) | mcp.RESEARCH_TOOL_SET

    created_project = api_client.post("/api/v1/projects", json={"name": "M2-01c P2 tools"})
    assert created_project.status_code == 201, created_project.text
    default_project = str(created_project.json()["project_id"])

    proposal = build_proposal()
    proposal["project_id"] = default_project
    created = api_client.post(
        "/api/v2/research/proposals",
        json={
            "project_id": default_project,
            "session_seed": SEED,
            "proposal": proposal,
            "plan": PLAN,
            "idempotency_key": "idem-http-create",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["document"]["proposal_id"] == PROPOSAL_ID
    approval_ref = body["approval"]["approval_ref"]

    def decision(page: Any) -> dict[str, Any]:
        assert page.status_code == 200, page.text
        return page.json()

    approval = decision(api_client.get(f"/api/v2/research/approvals/{approval_ref}"))
    approved = api_client.post(
        f"/api/v2/research/approvals/{approval_ref}/approve",
        json={"expected_revision": approval["revision"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    snapshot = api_client.get(
        "/api/v2/research/snapshot",
        params={
            "refs": f"approval:{approval_ref},proposal:{PROPOSAL_ID}",
            "project_id": default_project,
        },
    )
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["snapshot_revision"].startswith("sha256:")

    waited = api_client.post(
        "/api/v2/research/wait",
        json={"kinds": ["approval"], "max_wait_s": 0, "project_id": default_project},
    )
    assert waited.status_code == 200, waited.text
    assert "cursor" in waited.json()

    unbounded = api_client.post(
        "/api/v2/research/wait",
        json={"kinds": ["approval"], "max_wait_s": 600, "project_id": default_project},
    )
    assert unbounded.status_code == 400
    assert unbounded.json()["detail"]["params"]["rule"] == "bound_exceeded"

    resumed = api_client.post(
        "/api/v2/research/resume",
        json={
            "project_id": default_project,
            "proposal_ref": PROPOSAL_ID,
            "plan_digest": body["digests"]["plan_digest"],
            "diagnostic_only": True,
        },
    )
    assert resumed.status_code == 409, resumed.text
    assert resumed.json()["detail"]["params"]["rule"] == "saga_not_found"

    saga = api_client.get("/api/v2/research/sagas/saga-does-not-exist")
    assert saga.status_code == 404
