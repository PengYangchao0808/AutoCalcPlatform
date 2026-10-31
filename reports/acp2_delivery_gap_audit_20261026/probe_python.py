"""Read-only audit probes: real contracts/dispatch; no Controller or QC execution."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from acp_services.entrypoints.mcp import McpBackends, McpServer
from acp_services.entrypoints.application_params import (
    ApplicationParamError, normalize_analyze, normalize_prepare, normalize_read,
)
from acp_services.entrypoints.controller_client import ControllerClient
from acp_services.runtime.analysis_capabilities import DEFAULT_ANALYSIS_REGISTRY
from cccp.analysis.research.evidence import EvidenceReport


class NoEffects:
    def __getattr__(self, name):
        raise AssertionError(f"Unexpected backend access: {name}")


def server():
    port = NoEffects()
    return McpServer(backends=McpBackends(port, port, port, port, port, port, port))


def request(service, method, params=None):
    return service.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def main():
    if "--stdio" in sys.argv:
        from acp_services.entrypoints.mcp_stdio import JsonRpcLineTransport
        return JsonRpcLineTransport(server()).serve_forever()
    out = Path(__file__).parent / "evidence"
    service = server()
    contract = json.loads((ROOT / "docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json").read_text())
    required_tools = sorted({tool["name"] for row in contract["rows"] for tool in row["tools"]})
    required_methods = sorted({name for row in contract["rows"] for name in row.get("controller_client_methods", [])})
    init_plain = request(service, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    init_p2 = request(service, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {"extensions": [{"id": "acp.research.v1", "version": "1.0.0"}]}})
    specs = request(service, "tools/list")["result"]["tools"]
    names = [spec["name"] for spec in specs]
    fixture_path = ROOT / "tests/fixtures/acp2/science/contracts/evidence/supported_valid.json"
    fixture = json.loads(fixture_path.read_text())
    EvidenceReport.from_dict(fixture)
    normalizations = {}
    node_path = out / "node-probes.json"
    if node_path.exists():
        node = json.loads(node_path.read_text())
        for operation, normalizer in [("prepare", normalize_prepare), ("read", normalize_read), ("evidence", normalize_analyze)]:
            arguments = node["bridge_arguments"][operation]
            try:
                result = {"accepted": True, "normalized": normalizer(arguments)}
            except ApplicationParamError as exc:
                result = {"accepted": False, "message": str(exc), "params": exc.params}
            normalizations[operation] = {"arguments": arguments, **result}
    from acp.api.v1_routes import _is_research_launch, _enforce_approval
    budget_only = SimpleNamespace(proposal_ref=None, proposal_digest=None, plan_digest=None, budget={"unit": "task_count", "count": 1, "timeout_s": 60})
    sdk_paths = sorted(Path("/ACPServer/acp2-tools/deepseek-harness/node_modules/.pnpm").glob("@modelcontextprotocol+sdk*/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js"))
    files = ["src/acp_services/entrypoints/mcp.py", "src/acp_services/entrypoints/application_params.py", "rph/packages/bridge/src/index.ts", "rph/packages/dsh-bundle/skills/supervisor.ts", "rph/packages/dsh-bundle/skills/models.ts", "rph/packages/dsh-bundle/skills/planner.ts", "frontend/js/workbench/features/research-approval.js", "rph/packages/dsh-bundle/profiles/web.cordis.yml"]
    result = {
        "candidate": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "python": sys.version,
        "scope": "in-process real dispatch and normalization; NoEffects ports prevent Controller/QC access",
        "initialize_without_extension": init_plain,
        "initialize_requesting_p2": init_p2,
        "initialize_latest_sdk_version": request(service, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}}),
        "actual_tools": names,
        "p2_missing_tools": sorted(set(required_tools) - set(names)),
        "p2_missing_controller_methods": [name for name in required_methods if not hasattr(ControllerClient, name)],
        "p2_call_response": request(service, "tools/call", {"name": required_tools[0], "arguments": {}}),
        "tool_schemas": specs,
        "bridge_normalizations": normalizations,
        "analysis_registry_ids": DEFAULT_ANALYSIS_REGISTRY.ids(),
        "budget_only_approval_gate": {"is_research_launch": _is_research_launch(budget_only), "approval": _enforce_approval(None, budget_only)},
        "science_fixture": {"path": str(fixture_path.relative_to(ROOT)), "valid_for_cccp": True, "keys": sorted(fixture)},
        "sdk_client_paths": [str(p) for p in sdk_paths],
        "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in files},
    }
    (out / "python-probes.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"tools": len(names), "missing_p2_tools": len(result["p2_missing_tools"]), "bridge_normalizations": normalizations}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
