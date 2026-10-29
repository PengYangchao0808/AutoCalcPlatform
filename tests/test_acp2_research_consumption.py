"""F05 public consumer surface: fake model transport, real CAS, no QC dispatch."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from acp import mcp_extension
from acp_services.entrypoints import mcp
from acp_services.entrypoints.controller_client import ControllerClient
from acp_services.entrypoints.mcp_stdio import JsonRpcLineTransport
from acp_services.entrypoints.research_consumption import (
    CONSUMPTION_ACTIONS,
    CONSUMPTION_ROUTES,
    CONSUMPTION_SCHEMAS,
    CONSUMPTION_TOOL_NAMES,
    ControllerConsumptionPort,
)
from acp_services.runtime.research_consumption import (
    checkpoint_client_state,
    consumer_budget_usage,
    validate_client_attempt,
)
from acp_services.runtime.research_service import ResearchOperationConflictError
from tests.test_acp2_mcp_research_handshake import _server
from tests.test_acp2_p2_tools import (
    PLAN,
    PROJECT,
    PROPOSAL_ID,
    SEED,
    Harness,
    _call,
    _negotiate,
    _tools_list,
    build_proposal,
)


class RecordingConsumer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, dict(arguments)))
        return {"observed": tool}


def server_with_consumer(consumer: RecordingConsumer, **kwargs: Any) -> mcp.McpServer:
    return mcp.McpServer(
        backends=replace(_server()._backends, consumption=consumer),
        extension=mcp_extension.RESEARCH_EXTENSION_SPEC,
        **kwargs,
    )


def test_consumer_surface_is_additive_and_hidden_before_negotiation() -> None:
    consumer = RecordingConsumer()
    server = server_with_consumer(consumer)
    assert len(mcp.TOOL_ALLOWLIST) == 8
    assert len(mcp.RESEARCH_TOOL_NAMES) == 10
    assert len(CONSUMPTION_TOOL_NAMES) == 11
    assert set(_tools_list(server)) == mcp.TOOL_ALLOWLIST
    tool = "acp_research_model_statement_v1"
    assert _call(server, tool, {})["isError"] is True
    assert consumer.calls == []
    _negotiate(server)
    assert set(_tools_list(server)) == (
        mcp.TOOL_ALLOWLIST | mcp.RESEARCH_TOOL_SET | set(CONSUMPTION_TOOL_NAMES)
    )
    assert _call(server, tool, {})["isError"] is False
    assert consumer.calls == [(tool, {})]
    assert _call(server, tool, {"api_key": "must-not-forward"})["isError"] is True
    assert len(consumer.calls) == 1


def test_consumer_writes_keep_the_server_action_ceiling() -> None:
    consumer = RecordingConsumer()
    server = server_with_consumer(consumer, identity=mcp.IdentityMapper(bound_actions=("read",)))
    _negotiate(server)
    result = _call(
        server,
        "acp_research_model_plan_v1",
        {
            "messages": [{"role": "user", "content": "candidate"}],
            "model": "fake",
            "session_seed": "test",
        },
    )
    assert result["isError"] is True
    assert consumer.calls == []
    assert CONSUMPTION_ACTIONS["acp_research_model_plan_v1"] == "write"


def test_stdio_uses_the_same_negotiated_consumer_dispatch() -> None:
    consumer = RecordingConsumer()
    server = server_with_consumer(consumer)
    frames = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": mcp.MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "fake-F05", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "acp_capabilities",
                "arguments": {
                    "research_extension": {
                        "id": mcp_extension.RESEARCH_EXTENSION_ID,
                        "version": "1.0.0",
                    }
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "acp_research_model_statement_v1",
                "arguments": {},
            },
        },
    ]
    stdin = io.BytesIO(b"\n".join(json.dumps(frame).encode() for frame in frames) + b"\n")
    stdout = io.BytesIO()
    assert JsonRpcLineTransport(server, stdin=stdin, stdout=stdout).serve_forever() == 0
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[-1]["result"]["isError"] is False
    assert consumer.calls == [("acp_research_model_statement_v1", {})]


def test_consumption_transport_has_only_literal_escaped_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ControllerClient("http://127.0.0.1:18765")
    observed: list[tuple[Any, ...]] = []

    def request(method: str, path: str, payload: Any = None) -> dict[str, Any]:
        observed.append((method, path, payload))
        return {"ok": True}

    monkeypatch.setattr(client, "_request_json", request)
    port = ControllerConsumptionPort(client)
    port.call(
        "acp_research_record_read_v1", {"research_id": "id/with?separator", "project_id": "p"}
    )
    assert observed[-1] == (
        "GET",
        "/api/v2/research/records/id%2Fwith%3Fseparator?project_id=p",
        None,
    )
    port.call("acp_research_model_plan_v1", {"session_seed": "s", "messages": []})
    assert observed[-1] == ("POST", "/api/v2/model/plan", {"session_seed": "s", "messages": []})


def test_consumer_contract_matches_registry() -> None:
    contract = json.loads(
        (
            Path(__file__).parents[1]
            / "docs/reference/next-change/acp2-contracts/runtime/research-consumption.v1.json"
        ).read_text()
    )
    assert contract["extension"] == mcp_extension.RESEARCH_EXTENSION_ID
    assert set(contract["operations"]) == set(CONSUMPTION_TOOL_NAMES)
    for tool, route in CONSUMPTION_ROUTES.items():
        assert contract["operations"][tool]["http"] == list(route)
        assert contract["operations"][tool]["inputSchema"] == CONSUMPTION_SCHEMAS[tool]


def test_checkpoint_uses_real_proposal_cas_and_refuses_stale_or_foreign_attempts(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    try:
        created = harness.operations.proposal_create(
            command=harness.command,
            proposal=build_proposal(),
            plan=PLAN,
            session_seed=SEED,
        )
        task = {"project_id": PROJECT, "job_id": "job-F05", "attempt": 1, "release_id": "rel-F05"}
        current = SimpleNamespace(id="job-F05", project_id=PROJECT, attempt=1)
        args = dict(
            operations=harness.operations,
            command=harness.command,
            proposal_ref=PROPOSAL_ID,
            proposal=created["document"],
            current_task=current,
            entry={"kind": "task_ref", "value": task},
            expected_revision=1,
            plan=PLAN,
            session_seed=SEED,
        )
        saved = checkpoint_client_state(**args)
        assert saved["revision"] == 2
        assert saved["document"]["rph_state"]["task_ref"] == task
        with pytest.raises(ResearchOperationConflictError):
            checkpoint_client_state(**args)
        for altered in (
            dict(task, attempt=0),
            dict(task, attempt=True),
            dict(task, project_id="other"),
        ):
            with pytest.raises(ResearchOperationConflictError, match="current project attempt"):
                validate_client_attempt(altered, current, PROJECT)
    finally:
        harness.close()


def test_budget_projection_marks_unmeasured_counters_explicitly() -> None:
    model = {
        "model_requests": 2,
        "receipt_rows": [
            {"receipt_id": "fake-1", "usage": {"input_tokens": 13, "output_tokens": 7}}
        ],
    }
    projection = SimpleNamespace(consumed={"task_ref": 1, "recipe_step": 1, "qc_invocation": 1})
    result = consumer_budget_usage(projection, model)
    assert result["used"]["model_requests"] == 2
    assert result["used"]["task_refs"] == 1
    assert result["used"]["input_tokens"] == 13
    assert result["unmeasured"] == ["wall_seconds", "queue_seconds"]


def test_model_readiness_and_usage_application_make_zero_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.api import v2_model_gateway, v2_research
    from acp_services.workspace.command_identity import CommandIdentity
    from tests.test_acp2_model_gateway import RecordingTransport, _gateway

    transport = RecordingTransport()
    gateway = _gateway(tmp_path, transport)
    app = FastAPI()
    app.include_router(v2_model_gateway.router, prefix="/api/v2")
    app.include_router(v2_research.router, prefix="/api/v2")
    app.state.model_gateway = gateway
    app.state.admission = SimpleNamespace(ledger=gateway._ledger)
    command = CommandIdentity.from_local(project_id="p")
    monkeypatch.setattr(v2_model_gateway, "_command_identity", lambda *a, **k: command)
    monkeypatch.setattr(v2_model_gateway, "_require_entry_access", lambda *a, **k: None)
    monkeypatch.setattr(v2_research, "_command_identity", lambda *a, **k: command)
    monkeypatch.setattr(v2_research, "_require_entry_access", lambda *a, **k: None)
    monkeypatch.setattr(v2_research, "_research_project_scope", lambda *a, **k: "p")
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    request = Request({"type": "http", "app": app})
    result = v2_research.research_readiness(request, project_id="p")
    facts = {fact["id"]: fact for fact in result["facts"]}
    assert facts["model"]["status"] == "ok"
    assert all(fact["status"] in {"ok", "warn", "fail"} and fact["hint"] for fact in facts.values())
    usage = v2_research.research_consumer_usage(
        request, project_id="p", proposal_ref="prop-F05", session_seed="s"
    )
    assert usage["used"]["model_requests"] == 0
    assert transport.calls == []
    # Route registration, not a claim of successful HTTP delivery in this test.
    paths = set(app.openapi()["paths"])
    assert {"/api/v2/model/plan", "/api/v2/research/readiness", "/api/v2/research/usage"} <= paths


def test_prerecord_evidence_route_reuses_the_single_projection_and_guards_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    from acp.api import v2_research

    app = FastAPI()
    request = Request({"type": "http", "app": app})
    current = SimpleNamespace(id="job-F05", attempt=1, project_id="p")
    monkeypatch.setattr(
        v2_research, "_manager", lambda *a: SimpleNamespace(get=lambda job: current)
    )
    monkeypatch.setattr(v2_research, "_research_project_scope", lambda *a: "p")
    monkeypatch.setattr(v2_research, "_require_entry_access", lambda *a, **k: None)
    observed: list[dict[str, Any]] = []

    def project(record: dict[str, Any], req: Any, **kwargs: Any) -> JSONResponse:
        observed.append(record)
        return JSONResponse({"same_projection": True})

    monkeypatch.setattr(v2_research, "_project_record_evidence", project)
    task = {
        "project_id": "p",
        "task_id": "job-F05",
        "attempt": 1,
        "release": {"release_id": "rel-F05", "release_digest": "sha256:" + "c" * 64},
    }
    report = {
        "kind": "evidence_report",
        "sha256": "sha256:" + "d" * 64,
        "relative_path": "REPORT/evidence_report.json",
    }
    body = v2_research.EvidenceReadBody(
        project_id="p",
        session_seed="s",
        research_id="res-F05",
        round=0,
        task_ref=task,
        report_ref=report,
    )
    assert v2_research.read_attempt_evidence(body, request).status_code == 200
    assert observed[0]["task_refs"][0]["release_id"] == "rel-F05"
    assert observed[0]["evidence_refs"] == [report]
    current.attempt = 2
    with pytest.raises(HTTPException) as fenced:
        v2_research.read_attempt_evidence(body, request)
    assert fenced.value.status_code == 409
    assert len(observed) == 1


def test_local_evidence_reader_refuses_remote_work_dir_before_file_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from acp.api import v2_research

    request = Request({"type": "http", "app": FastAPI()})
    record = SimpleNamespace(id="remote-F05", work_dir="/must/not/read")
    manager = SimpleNamespace(get=lambda job: record, _is_remote_job=lambda job: True)
    monkeypatch.setattr(v2_research, "_manager", lambda *a: manager)
    with pytest.raises(HTTPException) as unavailable:
        v2_research._local_work_dir(request, record.id)
    assert unavailable.value.status_code == 503
    assert unavailable.value.detail["params"]["rule"] == "remote_evidence_not_available"
