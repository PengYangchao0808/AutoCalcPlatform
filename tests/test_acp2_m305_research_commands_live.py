"""M3-05b — real Controller integration for the Workbench research commands.

This is the HTTP layer of the two-layer M3-05 test plan (the other layer is the
zero-dependency Node module suite ``frontend/js/module_tests/``):

* a real FastAPI Controller (``acp.api.server.create_app``) is served by a real
  uvicorn socket on 127.0.0.1;
* the REAL client modules (``frontend/js/client/research-commands.js`` and
  ``frontend/js/workbench/features/research-approval.js``) run in Node and are
  given a REAL HTTP transport built on Node's ``fetch``;
* the proposal the approval signs is written to the real
  ``ResearchResourceStore`` so the platform can verify the science binding on
  submit (no fixture-only shortcut);
* request → query → stale-revision 409 → approve → submit (real job_id/attempt/
  TaskRef) → TaskRef bound to the approval → revoke → second client reads the
  same record, all over HTTP.

Skipped when Node (>= 18) is unavailable; nothing here starts QC, LSF or a
model, and the submitted ``fake`` workflow job is cancelled at teardown.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_NODE_DRIVER = _REPO_ROOT / "tests/fixtures/acp2/m305_research_commands_live.js"
_COMMANDS_MODULE = "frontend/js/client/research-commands.js"
_APPROVAL_MODULE = "frontend/js/workbench/features/research-approval.js"
_FIXTURES = _REPO_ROOT / "docs/reference/next-change/acp2-contracts/experience/fixtures"
_PROPOSAL_FIXTURE = _FIXTURES / "proposal.valid.json"

_BUDGET = {"unit": "task_count", "count": 1, "timeout_s": 600}
_PROJECT = "proj-a"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _set_environment(monkeypatch: pytest.MonkeyPatch, run_root: Path) -> None:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")
    monkeypatch.setenv("ACP_POLL_INTERVAL", "1")
    monkeypatch.delenv("ACP_SERVER_PROJECT", raising=False)
    monkeypatch.delenv("ACP_SUBMIT_POLICY_PROFILE", raising=False)


def _node_binary() -> str | None:
    override = os.environ.get("ACP2_NODE")
    if override and Path(override).is_file():
        return override
    return shutil.which("node")


@pytest.fixture()
def live_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[dict[str, Any], None, None]:
    if _node_binary() is None:
        pytest.skip("node is not available for the M3-05 real-HTTP layer")
    httpx = pytest.importorskip("httpx")
    uvicorn = pytest.importorskip("uvicorn")

    run_root = tmp_path / "run-root"
    _set_environment(monkeypatch, run_root)
    # A fresh process-global app is required: create_app resolves run_root from
    # the environment on import-time side effects of acp.api.server.
    from acp.api.server import create_app

    app = create_app(run_root=run_root, max_running=1)
    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="m305-controller", daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(600):
        if server.started:
            break
        if not thread.is_alive():
            raise AssertionError("uvicorn exited before startup")
        time.sleep(0.05)
    else:  # pragma: no cover - startup failure
        raise AssertionError("uvicorn did not start within 60s")

    for _ in range(200):
        try:
            probe = httpx.get(base_url + "/api/v1/projects", timeout=2.0)
            if probe.status_code < 500:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    else:  # pragma: no cover - readiness failure
        raise AssertionError("the Controller never answered /api/v1/projects")

    manager = app.state.job_manager
    project = str(manager.default_project_id)
    jobs: list[str] = []
    try:
        yield {
            "app": app,
            "base_url": base_url,
            "project": project,
            "run_root": run_root,
            "db_path": str(manager.store.db_path),
            "jobs": jobs,
            "httpx": httpx,
        }
    finally:
        for job_id in jobs:
            try:
                httpx.post(base_url + f"/api/v1/jobs/{job_id}/cancel", timeout=5.0)
            except httpx.HTTPError:  # pragma: no cover - cleanup only
                pass
        server.should_exit = True
        thread.join(timeout=30.0)


def _seed_proposal(run_root: Path, project: str, document: dict[str, Any]) -> None:
    from acp_services.contracts.access import LOCAL_PRINCIPAL_ID
    from acp_services.data.research_resources import (
        PROPOSAL_SCHEMA,
        ResearchResourceRequest,
        ResearchResourceStore,
        research_resource_key,
    )

    store = ResearchResourceStore(run_root / "acp_research.db")
    try:
        store.create(
            ResearchResourceRequest(
                resource_id=research_resource_key(
                    project, "proposal", str(document["proposal_id"])
                ),
                schema=PROPOSAL_SCHEMA,
                document=document,
                principal_id=LOCAL_PRINCIPAL_ID,
                project_id=project,
            )
        )
    finally:
        store.close()


def _scenario(controller: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    from acp_services.runtime.canonical_digest import (
        frozen_plan_definition,
        plan_digest,
    )

    plan = frozen_plan_definition(
        workflow="fake",
        input={"source": "CCO"},
        method={"protocol": "ext"},
        resources={},
    )
    digest = plan_digest(plan).digest
    return {
        "repo_root": str(_REPO_ROOT),
        "base_url": controller["base_url"],
        "project_id": controller["project"],
        "proposal_ref": proposal["proposal_id"],
        "platform_proposal": {
            "proposal_id": proposal["proposal_id"],
            "project_id": controller["project"],
            "title": "M3-05 research commands",
            "question": "does the workbench approve and submit the frozen plan?",
            "capability_ids": ["singlepoint.run"],
            "recipe_ref": {
                "schema_id": "acp2.science.recipe.v1",
                "version": "1.0.0",
                "digest": "sha256:" + "1" * 64,
            },
            "plan_digest": digest,
            "budget_request": {"value": "60", "unit": "second"},
        },
        "science_projection": proposal,
        "plan": plan,
        "budget": _BUDGET,
        "submit_body": {
            "workflow": "fake",
            "name": "m305-live-submit",
            "input": {"source": "CCO"},
            "method": {"protocol": "ext"},
            "resources": {},
            "project_id": controller["project"],
        },
        "other_digest": "sha256:" + "f" * 64,
        "idempotency_key": "m305-live-submit-1",
        "expected_principal": None,
        "commands_module": _COMMANDS_MODULE,
        "approval_module": _APPROVAL_MODULE,
    }


def test_research_commands_round_trip_over_real_http(
    live_controller: dict[str, Any], tmp_path: Path
) -> None:
    from acp_services.contracts.access import LOCAL_PRINCIPAL_ID
    from acp_services.runtime.approvals import ApprovalService
    from acp_services.runtime.canonical_digest import (
        proposal_plan_definition_digest,
    )

    controller = live_controller
    proposal = json.loads(_PROPOSAL_FIXTURE.read_text(encoding="utf-8"))
    proposal["project_id"] = controller["project"]
    proposal["proposal_id"] = "prop-m305-live"
    _seed_proposal(controller["run_root"], controller["project"], proposal)

    # The authenticated local principal is the requester/approver (local-single
    # profile allows self-approval; the server policy remains the authority).
    scenario = _scenario(controller, proposal)
    scenario["expected_principal"] = LOCAL_PRINCIPAL_ID

    scenario_path = tmp_path / "m305-scenario.json"
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")

    node = _node_binary()
    assert node is not None
    completed = subprocess.run(
        [node, str(_NODE_DRIVER), str(scenario_path)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    raw = completed.stdout.strip()
    assert raw, f"node driver produced no output: {completed.stderr}"
    payload = json.loads(raw)
    assert payload.get("ok") is True, json.dumps(payload, indent=2)[-4000:]

    by_name = {step["name"]: step for step in payload["steps"]}
    for name in (
        "query.empty",
        "request",
        "approval.pending_view",
        "decide.approve",
        "approval.approved_view",
        "second_client.query",
        "stale_revision_409",
        "submit.changed_plan_refused",
        "submit",
        "approval.task_ref",
        "decide.revoke",
        "second_client.after_revoke",
    ):
        assert by_name.get(name, {}).get("ok") is True, f"step {name} failed: {payload}"

    assert by_name["stale_revision_409.result"]["value"] == {
        "code": "revision_conflict",
        "status": 409,
    }
    assert by_name["submit.changed_plan_refused"]["value"] == {
        "code": "approved_plan_digest_mismatch"
    }
    assert by_name["approval.pending_view"]["value"]["validity"] == "approval_not_granted"
    assert by_name["approval.approved_view"]["value"]["validity"] == "ok"
    assert by_name["approval.approved_view"]["value"]["binding"] == "full"
    assert by_name["approval.approved_view"]["value"]["approver"] == LOCAL_PRINCIPAL_ID

    submitted = payload["submitted"]
    assert submitted["job_id"], "a real job_id must be returned"
    assert submitted["attempt"] == 1
    assert submitted["task_ref"]["task_id"] == submitted["job_id"]
    controller["jobs"].append(submitted["job_id"])

    bound = by_name["approval.task_ref"]["value"]["task_ref"]
    assert bound["task_id"] == submitted["job_id"]
    assert payload["record"]["status_after_revoke"] == "revoked"
    assert payload["record"]["second_client"]["status"] == "approved"

    # The platform is the authority: a fresh service instance (another client)
    # reads the same record, its science binding and the bound TaskRef.
    service = ApprovalService(controller["db_path"])
    record = service.get(payload["record"]["approval_ref"])
    assert record is not None
    assert record.state == "revoked"
    assert record.approver_principal == LOCAL_PRINCIPAL_ID
    assert record.task_ref is not None
    assert record.task_ref["task_id"] == submitted["job_id"]
    assert record.binding_status == "full"
    expected_science = proposal_plan_definition_digest(proposal).digest
    assert record.science_digest == expected_science
    assert record.plan_digest == scenario["platform_proposal"]["plan_digest"]
