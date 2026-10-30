"""P2: workflow-scoped contract discovery on ``/api/capabilities``."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.api.submission_contract import validate_workflow_submission


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=2)) as test_client:
        yield test_client


def test_workflow_selector_returns_complete_contract(client: TestClient) -> None:
    payload = client.get("/api/capabilities", params={"workflow": "Confsearch"}).json()
    block = payload["workflow_contract"]
    assert block["workflow"] == "Confsearch"
    assert block["coverage"] == "partial"
    assert block["method"]["enums"]["refinement_policy"] == [
        "all",
        "cumulative-99",
        "rank1",
        "screen",
    ]
    assert "confsearch" in block["method"]["levels"]
    for example in block["examples"]:
        validate_workflow_submission(
            example["workflow"],
            input=example["input"],
            method=example["method"],
            resources=example["resources"],
        )
    assert block["status_vocabulary"]["envelope"]["ok"]
    assert block["gaps"], "partial coverage must name its gaps"


def test_workflow_selector_is_case_insensitive(client: TestClient) -> None:
    payload = client.get("/api/capabilities", params={"workflow": "confsearch"}).json()
    assert payload["workflow_contract"]["workflow"] == "Confsearch"


def test_unknown_workflow_reports_allowed_and_suggestion(client: TestClient) -> None:
    response = client.get("/api/capabilities", params={"workflow": "Confserch"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["params"]["field"] == "workflow"
    assert detail["params"]["rule"] == "unknown_workflow"
    assert "Confsearch" in detail["params"]["allowed"]
    assert detail["params"]["suggestion"] == ["Confsearch"]
    upper = client.get("/api/capabilities", params={"workflow": "CONFSERCH"})
    assert upper.status_code == 422
    assert upper.json()["detail"]["params"]["suggestion"] == ["Confsearch"]


def test_post_body_and_query_are_equivalent(client: TestClient) -> None:
    query = client.get("/api/capabilities", params={"workflow": "BatchOptimize"}).json()
    body = client.post("/api/capabilities", json={"workflow": "BatchOptimize"}).json()
    assert query["workflow_contract"] == body["workflow_contract"]
    for example in body["workflow_contract"]["examples"]:
        validate_workflow_submission(
            example["workflow"],
            input=example["input"],
            method=example["method"],
            resources=example["resources"],
        )


def test_no_selector_keeps_the_frozen_shape(client: TestClient) -> None:
    payload = client.get("/api/capabilities").json()
    assert "workflow_contract" not in payload
    assert "status_vocabulary" not in payload
    assert payload["limits"]["enforcement"] in {"admission", "not-verified"}


def test_uncovered_workflow_is_marked_not_provided(client: TestClient) -> None:
    # PESsearch/scan gained staged partial coverage (2026-10-30 first-batch
    # contracts); irc is still outside the published set.
    payload = client.get("/api/capabilities", params={"workflow": "irc"}).json()
    block = payload["workflow_contract"]
    assert block["coverage"] == "not_provided"
    assert "not published" in block["reason"]


def test_capabilities_publish_task_instruction_routing(client: TestClient) -> None:
    """Goal -> chain routing: task_instructions index in one read (round 4)."""
    payload = client.get("/api/capabilities").json()
    tasks = payload.get("task_instructions")
    assert isinstance(tasks, list) and len(tasks) == 5
    by_id = {entry["guide_id"]: entry for entry in tasks}
    ts = by_id["task.find_transition_state"]
    assert ts["coverage"] == "first_batch"
    assert ts["detail"] == {
        "tool": "acp_get_guidance",
        "arguments": {"guide_id": "task.find_transition_state"},
    }
    assert "多参考" in by_id["task.multireference_analysis"]["title"]


def test_capabilities_publish_a_workflow_contract_index(client: TestClient) -> None:
    """Round 3+: one read answers which workflows publish a contract, and how
    to fetch the detail or what to do when there is none (no source reading)."""
    payload = client.get("/api/capabilities").json()
    index = payload.get("workflow_contracts")
    assert isinstance(index, dict) and len(index) == 15
    pes = index["PESsearch"]
    assert pes["coverage"] == "partial"
    assert pes["detail"] == {"tool": "acp_capabilities", "arguments": {"workflow": "PESsearch"}}
    assert pes["guidance"] == "workflow.PESsearch"
    # frozen-payload entries are now contracted
    assert index["XtbPathSearch"]["coverage"] == "partial"
    assert index["OrcaGradient"]["coverage"] == "partial"
    assert index["XtbConstrainedOpt"]["coverage"] == "partial"
    # uncontracted workflows carry an actionable fallback instead of silence
    irc = index["irc"]
    assert irc["coverage"] == "not_provided"
    assert "uncontracted in MCP" in irc["fallback"]
    assert irc["detail"]["arguments"] == {"workflow": "irc"}
