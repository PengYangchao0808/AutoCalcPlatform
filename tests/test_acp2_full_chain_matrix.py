"""M4-01 — the no-QC full product chain and its low-cost failure matrix (G20).

The M4-01 exit condition is "every low-cost counterexample passes on one combined
candidate; the result only closes interface/persistence semantics, not real
science/model/browser".  Two layers pin that:

* the **fast structural layer** below reads the acceptance drivers as source and
  asserts the frozen case table, the round chain, the two explicit model
  branches and the absence of a hardcoded "not delivered" claim.  It runs in the
  default (non-slow) suite and needs no network, no Node and no Controller;
* the **end-to-end layer** (:func:`test_full_chain_end_to_end_matrix`, ``slow``)
  runs ``scripts/acceptance/acp2_full_chain.py`` for real: a real uvicorn
  Controller, real SQLite research/job stores, the real MCP server + RPH bridge,
  the real Supervisor state machine and the real Workbench modules in Node.  It
  is the producer whose ``summary.json``/``fault-matrix.json`` the structural
  layer consumes when the committed evidence is present.

Declared boundaries are asserted, not hidden: ``evidence_kind=fixture``, the
CCCP-dispatch stub and the explicit test model adapter are recorded in
``stub-boundaries.json``; no test here claims real science, a real model or a
real browser.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "acceptance" / "acp2_full_chain.py"
MCP_RUNNER = REPO_ROOT / "scripts" / "acceptance" / "acp2_full_chain_mcp.mjs"
WORKBENCH_DRIVER = REPO_ROOT / "scripts" / "acceptance" / "acp2_full_chain_workbench.mjs"
EVIDENCE = REPO_ROOT / "reports" / "acp2_remediation_20261026" / "m4-01"

#: The eleven required cross-entry failure cases (M4-01 plan text).
REQUIRED_CASES = (
    "fault.cross_tool_same_key",
    "fault.cross_user",
    "fault.revoked_approval",
    "fault.illegal_remote_target",
    "fault.forged_receipt",
    "fault.approval_bypass",
    "fault.budget_concurrency",
    "fault.crash_recovery",
    "fault.wrong_evidence_identity",
    "fault.cursor_expiry",
    "fault.publication_retry",
)

#: The R01-R09 low-cost combinations (R09 shares the R07 legacy row).
REQUIRED_R_CASES = (
    "fault.r01_incomplete_task_result",
    "fault.r02_qc_rescue_over_limit",
    "fault.r03_runtime_identity_mismatch",
    "fault.r04_lost_right_intermediate_write",
    "fault.r05_historical_attempt_usage",
    "fault.r06_planning_quota_without_taskref",
    "fault.r07_legacy_key_and_data_row",
    "fault.r08_malicious_artifact_and_receipt",
)


def _script_tree() -> ast.Module:
    return ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))


def _case_table() -> tuple[tuple[str, str, str], ...]:
    for node in ast.walk(_script_tree()):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "CASES":
                value = ast.literal_eval(node.value)
                return tuple(tuple(row) for row in value)
    raise AssertionError("the M4-01 case table is missing from acp2_full_chain.py")


def test_the_case_table_covers_every_required_counterexample() -> None:
    table = _case_table()
    names = [row[0] for row in table]
    assert len(names) == len(set(names)), "a case name is duplicated"
    missing = [name for name in (*REQUIRED_CASES, *REQUIRED_R_CASES) if name not in names]
    assert missing == [], f"missing M4-01 cases: {missing}"
    for name, expected, method in table:
        assert expected, f"{name} declares no expected rule"
        assert method.startswith("_fault_"), f"{name} has no fault implementation"
        assert f"def {method}(" in SCRIPT.read_text(encoding="utf-8"), (
            f"{name} names a missing method {method}"
        )


def test_the_chain_is_a_two_round_revise_chain_with_real_entries() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "def round_zero(" in source and "def round_one(" in source
    # round 0 decides `revise`; the CAS opens round 1 and supersedes the approval.
    assert '"decision": "revise"' in source
    assert '"decision": "accept"' in source
    assert "superseded_approval" in source
    # The chain reads the same facts back over HTTP and through the store history.
    assert "round0.readback" in source
    assert "record.history" in source
    # Real entries, not fixtures: the Controller is a uvicorn subprocess (the
    # reused M1-06 skeleton) and the MCP leg is the Node runner over the real
    # bridge.
    assert "uvicorn" in source
    assert "from acp2_thin_slice import" in source
    mcp = MCP_RUNNER.read_text(encoding="utf-8")
    for anchor in (
        "openMcpSession",
        "proposal.create",
        "approval.query",
        "approval.approve",
        "research.wait",
        "research.snapshot",
        "prepare",
        "read",
        "runSupervisor",
    ):
        assert anchor in mcp, f"the MCP leg lost {anchor}"


def test_the_model_has_two_real_branches_and_never_hardcodes_not_delivered() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    mcp = MCP_RUNNER.read_text(encoding="utf-8")
    assert '"test-fixture"' in source and '"model-gateway"' in source
    # The live branch is real code (product planner + gateway transport), not a
    # boolean claim; selecting it without a gateway is NOT_VERIFIED (exit 2).
    assert "runProductionSupervisor" in mcp
    assert "createModelPlanner" in mcp
    assert "gateway_url" in mcp
    assert "EXIT_PREREQ" in mcp
    lowered = (source + mcp).lower()
    for banned in ("delivered=false", "delivered = false", '"delivered": false'):
        assert banned not in lowered, f"a hardcoded non-delivery claim survived: {banned}"
    assert "evidence_kind" in source and "fixture" in source


def test_workbench_adapter_uses_the_real_frontend_modules_over_http() -> None:
    driver = WORKBENCH_DRIVER.read_text(encoding="utf-8")
    for module in (
        "frontend/js/client/research-query.js",
        "frontend/js/workbench/contracts/experience_contract.js",
        "frontend/js/workbench/features/research-evidence-view.js",
        "frontend/js/workbench/features/research-approval.js",
    ):
        assert module in driver or module in SCRIPT.read_text(encoding="utf-8")
    assert "ACPClient.research.fetchEvidence" in driver
    assert "ACPResearchEvidence.project" in driver
    assert "ACPResearchApproval.setCard" in driver
    assert "fetch(" in driver  # the real HTTP transport
    assert 'expectedRefusal("record.foreign_project"' in driver


def test_data_idempotency_is_measured_with_a_cold_client() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "ControllerClient(self.controller.base_url)" in source
    for anchor in (
        '"no_extra_job"',
        '"no_extra_operation_row"',
        '"no_write_amplification"',
        '"controller_calls"',
        '"p50"',
        '"p95"',
    ):
        assert anchor in source, f"the performance row lost {anchor}"


def test_the_declared_boundaries_are_written_not_hidden() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for anchor in (
        "cccp.dispatch",
        "model.adapter",
        "stub-boundaries.json",
        "NOT_VERIFIED",
        "real model / DSH provider",
    ):
        assert anchor in source
    assert 'EVIDENCE_KIND = "fixture"' in source


def test_the_mcp_leg_is_a_real_product_session_and_resumes_the_wait() -> None:
    """M4-01 found F7/F8 here; the runner must now prove them closed, not hide them."""
    mcp = MCP_RUNNER.read_text(encoding="utf-8")
    # F7: the session is opened in *product* mode, so the handshake is performed
    # before `tools/list` and the negotiated 8+10 surface is asserted for real.
    assert 'mode: "product"' in mcp
    assert 'mode: "acceptance"' not in mcp
    assert "tools_observed" in mcp
    # F8: the issued cursor is replayed through the envelope (never worked around).
    assert "cursor_roundtrip" in mcp
    assert 'id: "F7-session-tools-list-order"' in mcp
    assert 'id: "F8-wait-cursor-redacted"' in mcp
    assert 'status: "closed"' in mcp
    # Both findings are reported as closed *with evidence*, and an open defect is
    # still reported as open (the two lists never collapse into one).
    assert "closed_findings" in mcp
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"findings": self.findings' in source
    assert '"closed_findings": self.closed_findings' in source
    # The machine-envelope passthrough that closes F8 is the declared mechanism.
    machine_output = (
        REPO_ROOT / "src" / "acp_services" / "entrypoints" / "machine_output.py"
    ).read_text(encoding="utf-8")
    assert "PASSTHROUGH_FIELD_VALUES" in machine_output
    assert "is_opaque_wait_cursor" in machine_output


@pytest.mark.skipif(not EVIDENCE.is_dir(), reason="the committed M4-01 evidence is not present")
def test_committed_evidence_closes_the_matrix() -> None:
    summary = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))
    assert summary["schema"] == "acp2.m4-01.full-chain.v1"
    assert summary["status"] == "PASS", summary.get("reasons")
    assert summary["evidence_kind"] == "fixture"
    matrix = json.loads((EVIDENCE / "fault-matrix.json").read_text(encoding="utf-8"))
    assert matrix["total"] == matrix["passed"] == len(_case_table())
    for case in matrix["cases"]:
        assert case["ok"] is True, f"{case['name']}: {case['observed']} != {case['expected']}"
    performance = json.loads((EVIDENCE / "performance.json").read_text(encoding="utf-8"))
    data = performance["data"]
    assert data["same_operation_ref"] and data["no_extra_job"]
    assert data["no_extra_operation_row"] and data["no_write_amplification"]
    assert data["latency_ms"]["p95"] >= data["latency_ms"]["p50"] >= 0
    assert data["controller_calls"] == 6
    assert data["controller_prepare_calls"] == data["controller_read_calls"] == 3
    boundaries = json.loads((EVIDENCE / "stub-boundaries.json").read_text(encoding="utf-8"))
    assert boundaries["evidence_kind"] == "fixture"
    assert {item["id"] for item in boundaries["boundaries"]} == {"cccp.dispatch", "model.adapter"}
    # The chain summary names the two rounds and the two TaskRefs, not a fixture.
    chain = summary["chain"]
    assert chain["round0"]["record"]["decision"] == "revise"
    assert chain["round1"]["record"]["decision"] == "accept"
    assert chain["round0"]["task_ref"]["task_id"] != chain["round1"]["task_ref"]["task_id"]
    assert chain["round1"]["superseded_approval"] == chain["round0"]["approval_ref"]
    # F9: the superseding (revised-round) approval keeps the full scientific
    # binding; it is never downgraded to an execution-only approval.
    assert chain["round0"]["approval_binding_status"] == "full"
    assert chain["round1"]["approval_binding_status"] == "full"
    fixes = {item["id"] for item in summary.get("product_fixes", [])}
    assert "F9-supersede-approval-science-binding" in fixes


@pytest.mark.slow
def test_full_chain_end_to_end_matrix(tmp_path: Path) -> None:
    """Run the whole M4-01 chain + matrix against a real Controller (slow)."""
    out = tmp_path / "m4-01"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
        env={"PYTHONPATH": str(REPO_ROOT / "src"), **_environment()},
    )
    assert (out / "summary.json").is_file(), completed.stderr[-4000:]
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "PASS", summary.get("reasons") or completed.stdout[-4000:]
    matrix = json.loads((out / "fault-matrix.json").read_text(encoding="utf-8"))
    assert matrix["passed"] == matrix["total"] == len(_case_table())
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert "real science" not in json.dumps(summary.get("findings") or [])
    # F7/F8 (found by M4-01) are re-tested and reported closed with their evidence.
    closed = {item["id"]: item["status"] for item in summary.get("closed_findings") or []}
    assert closed.get("F7-session-tools-list-order") == "closed", closed
    assert closed.get("F8-wait-cursor-redacted") == "closed", closed
    assert summary.get("findings") == [], summary.get("findings")
    # The product session observed the real negotiated surface (8 P1 + 10 P2) on
    # both rounds; the cursor round-trip is part of the wait evidence.
    for round_key in ("round0", "round1"):
        tools = (summary["mcp"][round_key] or {}).get("tools") or []
        assert len(tools) == 18, (round_key, tools)


def _environment() -> dict[str, str]:
    import os

    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "HOME",
            "ACP2_TOOLS",
            "LANG",
            "LC_ALL",
            "TMPDIR",
        }
    }
    return env
