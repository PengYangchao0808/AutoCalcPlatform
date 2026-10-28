"""Real MCP dispatcher and Controller adapter with recording ports; no jobs launched."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
from acp_services.entrypoints.mcp import McpServer, McpBackends
from acp.services_mcp_bootstrap import _ControllerSubmit


class Ports:
    def __init__(self):
        self.submits = []
        self.prepares = []

    def submit(self, spec, **kwargs):
        self.submits.append(dict(spec))
        return {"job_id": "r3-job", "task_ref": {"project_id": "r3-project", "job_id": "r3-job", "attempt": 1, "release_id": "r3-release"}}

    def prepare(self, spec, **kwargs):
        self.prepares.append(dict(spec))
        return {"operation_id": "op_r3", "operation_ref": {"operation_id": "op_r3"}, "availability": "pending"}


def session():
    ports = Ports()
    return McpServer(backends=McpBackends(ports, ports, ports, ports, ports, ports, ports)), ports


def call(server, name, args):
    reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    assert "result" in reply, reply
    assert reply["result"]["isError"] is False, reply
    return reply["result"]["structuredContent"]


submit_args = {"workflow": "singlepoint", "input": {"source": "C"}, "idempotency_key": "r3-shared-key"}
prepare_args = {"purpose": "artifact", "job_id": "job-a", "resource": "result_manifest", "idempotency_key": "r3-shared-key"}
results = {"scope": "Real MCP dispatcher and real Controller normalization; backend recording ports replace all effects"}
s, p = session()
prep = call(s, "acp_prepare_artifact", prepare_args)
sub = call(s, "acp_submit", submit_args)
assert len(p.submits) == 0
assert sub["data"]["reference"]["kind"] == "operation"
results["prepare_then_submit"] = {"prepare": prep, "submit": sub, "submit_calls": p.submits}
s, p = session()
first = call(s, "acp_prepare_artifact", prepare_args)
second = call(s, "acp_prepare_artifact", {**prepare_args, "job_id": "job-b", "resource": "different_resource", "attempt": 9})
assert len(p.prepares) == 1
results["prepare_changed_identity"] = {"first": first, "second": second, "prepare_calls": p.prepares}
s, p = session()
first = call(s, "acp_submit", submit_args)
second = call(s, "acp_submit", submit_args)
assert "task_ref" in first["data"] and "task_ref" not in second["data"]
assert len(p.submits) == 1
results["submit_replay_loses_identity"] = {"first": first, "replay": second, "submit_count": len(p.submits)}
s, p = session()
call(s, "acp_prepare_artifact", prepare_args)
call(s, "acp_submit", {**submit_args, "idempotency_key": "r3-different-key"})
assert len(p.submits) == 1
results["control_different_keys"] = {"submit_count": len(p.submits)}
p = Ports()
payload = {**submit_args, "execution_mode": "remote", "target_node": "lsf-r3", "node_tags": ["remote"], "name": "required-name", "config_path": "r3-config.yaml", "molecule_name": "r3-molecule", "task_name": "r3-task", "remark": "required-remark"}
adapter = _ControllerSubmit(p)
adapter.submit(payload, idempotency_key="r3-route-key", workspace_id="r3-project")
dropped = sorted(set(payload) - set(p.submits[0]))
assert {"execution_mode", "target_node", "name", "config_path"}.issubset(dropped)
results["controller_adapter_drops_fields"] = {"input": payload, "controller_received": p.submits[0], "dropped": dropped}
Path(__file__).with_name("mcp.json").write_text(json.dumps(results, indent=2) + "\n")
print("5 MCP/adapter cases reproduced, including distinct-key negative control")
