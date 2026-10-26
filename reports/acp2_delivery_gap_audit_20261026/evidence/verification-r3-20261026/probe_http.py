"""Real isolated ASGI Controller/identity/ledger; disable scientific dispatch only."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
import pytest
from fastapi.testclient import TestClient
from tests.test_acp2_runtime_multitenant import _provision, ALPHA, TENANT
from acp_services.entrypoints.controller_client import ControllerClient, AuthorizationError, InvalidParameterError
from acp_services.entrypoints.mcp import McpServer, McpBackends
from acp.services_mcp_bootstrap import _ControllerSubmit, _stdio_identity_mapper


class AsgiControllerClient(ControllerClient):
    def __init__(self, http, key):
        super().__init__("http://testserver", api_key=key)
        self.http = http
        self.calls = []

    def _request_json(self, method, path, payload=None, **kwargs):
        response = self.http.request(method, path, json=payload, headers={"X-API-Key": self._api_key})
        self.calls.append({"method": method, "path": path, "status": response.status_code})
        if response.status_code in (401, 403):
            raise AuthorizationError("credential denied by isolated Controller")
        if response.status_code >= 400:
            raise InvalidParameterError(str(response.json()))
        return response.json()


def tool(server, args):
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "acp_submit", "arguments": args}})


with tempfile.TemporaryDirectory(prefix="acp-r3-") as directory, pytest.MonkeyPatch.context() as patch:
    root = Path(directory)
    provisioned = _provision(root)
    patch.setenv("ACP_RUN_ROOT", directory)
    patch.setenv("ACP_IDENTITY_MODE", "api-key")
    patch.setenv("ACP_IDENTITY_DB", str(provisioned["db_path"]))
    patch.setenv("ACP_IDENTITY_TENANT", TENANT)
    import acp.scheduler.capabilities as capabilities
    import acp.scheduler.manager as manager_module
    patch.setattr(capabilities, "local_satisfies", lambda required: True)
    patch.setattr(manager_module, "local_satisfies", lambda required: True)
    from acp.api.server import create_app
    app = create_app(run_root=root, max_running=2, poll_interval=60)
    with TestClient(app) as http:
        manager = app.state.job_manager
        suppressed = []
        patch.setattr(manager, "_start_submission_thread", lambda *a, **k: suppressed.append("dispatch_suppressed"))
        alice_key = provisioned["keys"]["alice_write"]
        bob_key = provisioned["lifecycle"].issue_key(provisioned["accounts"]["bob"].account_id,
            scope="workspace", actions=["read", "write", "control"], project=ALPHA)
        alice = AsgiControllerClient(http, alice_key.key)
        bob = AsgiControllerClient(http, bob_key.key)
        payload = {"workflow": "singlepoint", "input": {"source": "C", "source_type": "smiles"},
            "project_id": ALPHA, "resources": {"cores": 1, "memory_mb": 512}, "idempotency_key": "r3-two-users"}
        principals = [alice.identity()["principal"]["principal_id"], bob.identity()["principal"]["principal_id"]]
        first = alice.submit(payload)
        second = bob.submit(payload)
        assert principals[0] != principals[1]
        assert first["job_id"] == second["job_id"], (first, second)
        assert second["idempotent_replay"] is True
        results = {"scope": "Real ASGI routes, credentials, JobManager and persistent ledger in temporary root; local capability probe forced true and _start_submission_thread suppressed; no sockets/QC/LSF",
            "two_principals_same_project": {"principals": principals, "first": first, "second": second}}

        remote = {**payload, "idempotency_key": "r3-remote-choice", "execution_mode": "remote", "target_node": "r3-nonexistent-node"}
        direct = http.post("/api/v1/jobs", json=remote, headers={"X-API-Key": alice_key.key})
        assert direct.status_code == 400, direct.text
        adapted = _ControllerSubmit(alice).submit(remote, idempotency_key="r3-remote-choice", workspace_id=ALPHA)
        stored = manager.store.get(adapted["reference"]["id"])
        assert stored.spec.target_node is None and stored.spec.execution_mode is None
        results["remote_target_http_vs_mcp"] = {"direct_http_status": direct.status_code,
            "direct_http_error": direct.json(), "mcp_adapter_result": adapted,
            "stored_spec": {"target_node": stored.spec.target_node, "execution_mode": stored.spec.execution_mode}}

        from acp_services.runtime.research_budget import parse_budget_policy
        ledger = app.state.admission.ledger
        budget_id = "rb_r3_receipt"
        ledger.define_research_budget(parse_budget_policy({"unit": "task_count", "count": 1},
            budget_id=budget_id, project_id=ALPHA, approval_ref="ap_r3_seeded"))
        reservation = ledger.reserve_research_budget(budget_id, {"task_ref": 1},
            reservation_id="bres_r3_receipt", task_ref="r3-running-task", admission_reservation_id="res_r3_seeded")
        assert reservation.admitted
        ledger.mark_research_budget_dispatched("res_r3_seeded")
        exhausted = ledger.reserve_research_budget(budget_id, {"task_ref": 1}, reservation_id="bres_r3_before")
        assert not exhausted.admitted
        fake_receipt = http.post(f"/api/v2/research/budgets/{budget_id}/settle",
            headers={"X-API-Key": alice_key.key}, json={"receipt_id": "arbitrary-client-receipt",
            "reservation_id": "bres_r3_receipt", "trusted_source": "worker-receipt", "usage": {"qc_invocation": 999}})
        assert fake_receipt.status_code == 200, fake_receipt.text
        after_fake = ledger.reserve_research_budget(budget_id, {"task_ref": 1}, reservation_id="bres_r3_after")
        # Important disconfirmation: task_ref reservations are counted even after settlement.
        assert not after_fake.admitted
        assert fake_receipt.json()["budget"]["receipt_counters"]["qc_invocation"] == 999
        results["client_asserted_trusted_receipt"] = {"setup": "Seeded approved budget and dispatched reservation via real ledger; ordinary member write credential on HTTP settlement",
            "before_new_reservation": exhausted.to_dict(), "settlement_http_status": fake_receipt.status_code,
            "settlement_body": fake_receipt.json(), "after_new_reservation": after_fake.to_dict()}

        bound_identity = _stdio_identity_mapper(alice, alice_key.key)
        adapter = _ControllerSubmit(alice)
        server = McpServer(backends=McpBackends(adapter, adapter, adapter, adapter, adapter, adapter, adapter), identity=bound_identity)
        replay_payload = {**payload, "idempotency_key": "r3-revoked-key"}
        before = tool(server, replay_payload)
        assert before["result"]["isError"] is False, before
        provisioned["key_manager"].revoke(alice_key.key_id)
        denied = http.get("/api/identity", headers={"X-API-Key": alice_key.key})
        assert denied.status_code == 401
        calls_before = len(alice.calls)
        cached = tool(server, replay_payload)
        assert cached["result"]["isError"] is False and len(alice.calls) == calls_before
        uncached = tool(server, {**replay_payload, "idempotency_key": "r3-revoked-new-key"})
        assert uncached["result"]["isError"] is True
        results["revoked_key_cached_submit"] = {"http_identity_status": denied.status_code,
            "cached_reply": cached, "uncached_reply": uncached,
            "cached_controller_calls": 0, "before_revoke": before}
        results["suppressed_dispatches"] = len(suppressed)
        Path(__file__).with_name("http.json").write_text(json.dumps(results, indent=2) + "\n")
print("4 real ASGI cases reproduced; no scientific dispatch")
