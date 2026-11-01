"""Round-8 三.6: bounded envelopes that never lose discovery.

Two payloads ate the agent's context:

* ``preflight.submission_analysis.requested`` echoed the whole submission
  (inline XYZ, ``items[].xyz``, the 40-key ``levels`` table);
* the workflow-scoped capability response returned the full contract (9–10 KB
  for BatchOptimize, most of it prose and the nested level table).

Pinned here:

* the preflight echo ≤ 2 KB, structural payloads replaced by
  ``{"omitted", "sha256", "atoms", "bytes"}``, and the **plan digest still
  covers the full payload** (the projection never feeds the digest);
* the workflow-scoped default is a CORE view (≤ 5 KB for BatchOptimize),
  ``contract_sections`` fetches any section on demand, unknown sections are a
  typed 422, ``contract_sections=full`` keeps the historical block, and the
  contract digest is stable across every view;
* ``tools``/``extensions`` no longer ride along (they are handshake facts) and
  the response says where to get them;
* the highest-value preflight field (``effective.orca_summary``) is untouched.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_XYZ = "3\nTAG: INT\nO 0 0 0\nH 1 0 0\nH 0 1 0"

_ECHO_LIMIT = 2048
#: The decision's target is the **contract block** (the thing that used to be
#: 9–10 KB).  The whole response adds the small public refs + navigation
#: (~1.1 KB); it is guarded here so the wrapper cannot silently regrow either.
_CORE_BLOCK_LIMIT = 5000
_CORE_RESPONSE_LIMIT = 5632  # 5.5 KiB


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as test_client:
        yield test_client


def _bytes(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _counts(client: TestClient) -> dict[str, int]:
    return dict(client.app.state.job_manager.store.counts())


# --------------------------------------------------------------------------- #
# preflight echo
# --------------------------------------------------------------------------- #


def _pes_payload(xyz: str) -> dict[str, Any]:
    return {
        "workflow": "PESsearch",
        "input": {
            "source": {"source_type": "xyz_text", "xyz_text": xyz},
            "coordinate": {
                "kind": "distance",
                "atoms": [0, 5],
                "start": "4.90",
                "end": "1.55",
            },
            "protocol": {
                "scan_driver": {"software": "orca"},
                "scan_optimizer": {"method": "GFN2-xTB"},
            },
        },
        "method": {},
    }


def test_preflight_echo_is_bounded_and_omits_the_structural_payload(
    client: TestClient,
) -> None:
    big_xyz = _XYZ + "\n".join("He 0 0 0" for _ in range(2000))
    before = _counts(client)
    response = client.post("/api/v1/preflight", json=_pes_payload(big_xyz))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["would_create_task"] is False
    assert _counts(client) == before  # zero side effects

    requested = body["submission_analysis"]["requested"]
    assert _bytes(requested) <= _ECHO_LIMIT
    omitted = requested["input"]["source"]["xyz_text"]
    assert omitted["omitted"] is True
    assert omitted["sha256"] == hashlib.sha256(big_xyz.encode()).hexdigest()
    assert omitted["atoms"] == 3
    assert omitted["bytes"] == len(big_xyz.encode())
    # scalars survive: the receipt still explains which field drove the analysis
    assert requested["input"]["source"]["source_type"] == "xyz_text"
    assert requested["input"]["coordinate"]["start"] == "4.90"
    assert requested["input"]["protocol"]["scan_optimizer"]["method"] == "GFN2-xTB"
    assert "omitted" not in requested["input"]["coordinate"]


def test_plan_digest_is_computed_from_the_full_payload(
    client: TestClient,
) -> None:
    from acp.api.v1_routes import _plan_digest

    payload = _pes_payload(_XYZ)
    response = client.post("/api/v1/preflight", json=payload)
    assert response.status_code == 200, response.text
    digest = response.json()["plan_digest"]
    # The authoritative digest covers the full submitted payload, not the echo.
    assert digest == _plan_digest(
        {
            "workflow": payload["workflow"],
            "input": payload["input"],
            "method": payload["method"],
            "resources": {},
        }
    )
    # Changing only the omitted geometry must still change the digest.
    moved = _pes_payload(_XYZ.replace("O 0 0 0", "O 0 0 0.001"))
    other = client.post("/api/v1/preflight", json=moved)
    assert other.status_code == 200, other.text
    assert other.json()["plan_digest"] != digest


def test_bulky_scalar_method_is_collapsed_and_flagged(client: TestClient) -> None:
    payload = {
        "workflow": "BatchOptimize",
        "input": {
            "source_type": "batch_structures",
            "items": [{"xyz": _XYZ}],
            "charge": 0,
            "multiplicity": 1,
        },
        "method": {
            "profile": "opt_freq",
            "batch_roles": {
                "int": {
                    "method": "r2SCAN-3c",
                    "basis": "def2-SVP",
                    "sp_method": "DLPNO-CCSD(T)" + "X" * 3000,
                }
            },
        },
    }
    response = client.post("/api/v1/preflight", json=payload)
    assert response.status_code == 200, response.text
    requested = response.json()["submission_analysis"]["requested"]
    assert _bytes(requested) <= _ECHO_LIMIT
    assert requested.get("truncated") is True


def test_orca_summary_is_not_trimmed(client: TestClient) -> None:
    """五 的保留项: the effective ORCA projection is the highest-value field."""
    payload = {
        "workflow": "BatchOptimize",
        "input": {
            "source_type": "batch_structures",
            "items": [{"xyz": _XYZ}],
            "charge": 0,
            "multiplicity": 1,
        },
        "method": {
            "profile": "opt_freq",
            "batch_roles": {"int": {"method": "r2SCAN-3c", "basis": "def2-SVP"}},
        },
    }
    response = client.post("/api/v1/preflight", json=payload)
    assert response.status_code == 200, response.text
    effective = response.json()["submission_analysis"]["effective"]
    assert effective["roles"]["int"]["orca_summary"]


# --------------------------------------------------------------------------- #
# workflow-scoped capabilities
# --------------------------------------------------------------------------- #


def test_workflow_scope_core_view_is_bounded(client: TestClient) -> None:
    response = client.get("/api/capabilities", params={"workflow": "BatchOptimize"})
    assert response.status_code == 200, response.text
    body = response.json()
    block = body["workflow_contract"]
    assert _bytes(block) <= _CORE_BLOCK_LIMIT, _bytes(block)
    assert len(response.content) <= _CORE_RESPONSE_LIMIT, len(response.content)
    # discovery is preserved: names/types/enums/constraints/gaps + navigation
    assert block["workflow"] == "BatchOptimize"
    assert block["gaps"] and block["constraints"]
    assert "levels" not in block["method"]
    assert "examples" not in block
    assert "batch_roles" in str(block["method"]["property_types"])
    assert block["sections_available"] == [
        "input",
        "method",
        "method.levels",
        "resources",
        "examples",
        "constraints",
        "gaps",
        "full",
    ]
    assert block["next"]["arguments"]["contract_sections"] == "method.levels"
    # handshake facts are no longer duplicated in a workflow read
    assert "tools" not in body and "extensions" not in body
    assert body["next"]["tool"] == "acp_capabilities"
    assert body["task_states"]["vocabulary"]


def test_contract_sections_fetch_one_section(client: TestClient) -> None:
    core = client.get("/api/capabilities", params={"workflow": "BatchOptimize"}).json()
    levels = client.get(
        "/api/capabilities",
        params={"workflow": "BatchOptimize", "contract_sections": "method.levels"},
    ).json()
    assert set(levels["workflow_contract"]["method"]) == {"levels"}
    assert levels["workflow_contract"]["method"]["levels"]
    assert _bytes(levels["workflow_contract"]) < _bytes(core["workflow_contract"])

    input_view = client.get(
        "/api/capabilities",
        params={"workflow": "BatchOptimize", "contract_sections": "input"},
    ).json()
    assert "properties" in input_view["workflow_contract"]["input"]
    assert input_view["workflow_contract"]["next"]["arguments"]["contract_sections"] == "full"
    # digest is the digest of the FULL block, identical in every view
    assert levels["contract_digest"] == core["contract_digest"]
    assert input_view["contract_digest"] == core["contract_digest"]
    assert levels["contract_sections"] == "method.levels"


def test_contract_sections_full_keeps_the_historical_block(client: TestClient) -> None:
    full = client.get(
        "/api/capabilities",
        params={"workflow": "BatchOptimize", "contract_sections": "full"},
    ).json()
    block = full["workflow_contract"]
    assert "levels" in block["method"]
    assert "examples" in block
    assert "properties" in block["method"]
    core = client.get("/api/capabilities", params={"workflow": "BatchOptimize"}).json()
    assert _bytes(block) > _bytes(core["workflow_contract"])
    assert full["contract_digest"] == core["contract_digest"]


def test_unknown_contract_section_is_typed(client: TestClient) -> None:
    response = client.get(
        "/api/capabilities", params={"workflow": "BatchOptimize", "contract_sections": "bogus"}
    )
    assert response.status_code == 422
    params = response.json()["detail"]["params"]
    assert params["rule"] == "invalid_enum"
    assert "method.levels" in params["allowed"]


def test_full_cannot_be_combined_with_a_section(client: TestClient) -> None:
    response = client.get(
        "/api/capabilities",
        params={"workflow": "BatchOptimize", "contract_sections": "full,input"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["params"]["rule"] == "mutually_exclusive"


def test_post_surface_accepts_contract_sections(client: TestClient) -> None:
    response = client.post(
        "/api/capabilities",
        json={"workflow": "Confsearch", "contract_sections": "constraints"},
    )
    assert response.status_code == 200, response.text
    block = response.json()["workflow_contract"]
    assert block["constraints"]
    assert "input" not in block


def test_mcp_capabilities_forwards_contract_sections() -> None:
    """Round-8 P1: the CORE view's `next.arguments` must work over MCP.

    The HTTP switch alone left an MCP-only agent stuck on the bounded view with
    no way to reach method.levels/examples.
    """

    class _Caps:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def capabilities(self, **kwargs):
            self.calls.append(dict(kwargs))
            return {"workflow_contract": {"workflow": "BatchOptimize", "method": {"levels": {}}}}

    import dataclasses

    from tests.test_services_mcp import _Fakes
    from acp_services.entrypoints import mcp as mcp_mod

    caps = _Caps()
    backends = dataclasses.replace(_Fakes().backends(), capabilities=caps)
    server = mcp_mod.McpServer(backends=backends)
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": mcp_mod.McpTool.CAPABILITIES,
                "arguments": {"workflow": "BatchOptimize", "contract_sections": "full"},
            },
        }
    )
    assert response is not None
    assert response["result"]["isError"] is False
    assert caps.calls[-1]["contract_sections"] == "full"

    bad = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": mcp_mod.McpTool.CAPABILITIES,
                "arguments": {"workflow": "BatchOptimize", "contract_sections": "nope"},
            },
        }
    )
    assert bad is not None
    error = bad["result"]["structuredContent"]["error"]
    assert error["code"] == mcp_mod.mo.ErrorCode.INPUT_INVALID
    assert error["params"]["rule"] == "invalid_enum"
