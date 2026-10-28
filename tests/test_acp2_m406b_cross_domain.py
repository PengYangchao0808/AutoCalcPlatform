"""M4-06b — cross-domain reuse + multi-tenant/multi-hypothesis isolation.

The fast layer pins the harness contract that the evidence relies on:

* exactly three projects, three users and two concurrent hypotheses are named;
* the isolation run root is the isolated remediation path, never a production one;
* the four H6 reuse assertions (capability, budget, EvidenceReport, ViewModel)
  each fail on their own when the shared contract is broken;
* research memory, approvals, budgets, TaskRefs, artifacts, events and
  capability-forms are project-scoped when exercised against the real stores;
* the remote CLI task surface forwards ``ACP_API_KEY`` exactly like the stdio
  MCP entry (the M4-06b-F2 gap this run fixed).

The slow layer runs the real acceptance harness with one real xTB
optimisation, real Shermo runs over the M4-04 route-a frequency log, a real
Controller/uvicorn child, a real ``acp mcp serve`` stdio child and a real
``acp task query --controller`` child.  It is skipped when xTB/Shermo or the
route-a inputs are absent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from scripts.acceptance import acp2_m406b as harness

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fast layer: harness contract
# ---------------------------------------------------------------------------


def test_identity_of_the_matrix_is_three_by_three_by_two() -> None:
    assert harness.SCHEMA == "acp2.m406b.acceptance.v1"
    assert len(harness.PROJECTS) == 3
    assert len(set(harness.PROJECTS)) == 3
    assert len(harness.USERS) == 3
    assert len(set(harness.USERS)) == 3
    assert set(harness.USERS) == set(harness.PROJECTS)
    assert len(harness.HYPOTHESES) == 2
    assert harness.RUN_ROOT == Path("/ACPServer/acp2-runs/remediation/m4-06b")


def test_harness_never_targets_the_production_run_root() -> None:
    production = Path("/ACPServer/acp2-runs/remediation")
    assert harness.RUN_ROOT != production
    assert production in harness.RUN_ROOT.parents
    assert harness.ROUTE_A_FREQ_LOG.is_absolute()
    assert "m4-04-route-a" in str(harness.ROUTE_A_FREQ_LOG)


def _row(domain: str) -> dict[str, Any]:
    """The smallest row shape ``cross_domain_reuse`` consumes."""
    digest = "sha256:" + ("a" if domain == "nmr" else "b") * 64
    return {
        "domain": domain,
        "evidence_kind": "native",
        "capability": {
            "catalog_sha256": "sha256:" + "c" * 64,
            "domain_ids": ["nmr.compare", "nmr.shielding", "thermochemistry.ensemble"],
        },
        "capability_binding": {
            "capability_id": "nmr.compare" if domain == "nmr" else "thermochemistry.ensemble",
            "implementation_id": (
                "cccp.analysis.nmr.comparison"
                if domain == "nmr"
                else "cccp.analysis.ensemble.science"
            ),
            "execution_kind": "pure_analysis",
            "selection_mode": "BindingSelectionMode.CAPABILITY",
            "delegation_target": "delegate",
            "preflight_ready": True,
            "registry": "domain_registry()",
        },
        "capability_forms": {
            "subset_digest": "sha256:" + "d" * 64,
            "forms_sha256": "sha256:" + "e" * 64,
            "entry_count": 4,
            "nmr_states": [],
            "thermo_states": [],
        },
        "budget": {
            "budget_id": "rb_shared",
            "settlement": {"applied": True, "remaining": {"task_ref": 2}},
        },
        "worker": {"report_digest": digest, "task_result_digest": digest},
        "evidence_view": {
            "schema": "acp2.runtime.evidence-projection.v1",
            "evidence_status": "complete",
            "projection_digest": digest,
        },
        "research_decision": {"schema": "acp2.runtime.research-decision.v1"},
        "viewmodel": {
            "projection": {"view_type": "scan", "nodes": [{"id": "n"}], "annotations": []},
            "node_keys": ["id"],
            "annotation_keys": [],
            "product_digest": digest,
            "projection_digest": digest,
        },
    }


def test_four_reuse_assertions_all_hold_for_the_shared_contract() -> None:
    reuse = harness.cross_domain_reuse(_row("nmr"), _row("thermochemistry"))
    assert reuse["passed"] is True
    assert reuse["capability"]["passed"] is True
    assert reuse["capability"]["binding"]["passed"] is True
    assert reuse["capability"]["forms"]["passed"] is True
    assert reuse["budget"]["passed"] is True
    assert reuse["evidence"]["passed"] is True
    assert reuse["viewmodel"]["passed"] is True


@pytest.mark.parametrize(
    ("mutation", "failing"),
    [
        (lambda row: row["capability"].update(catalog_sha256="sha256:" + "0" * 64), "capability"),
        (
            lambda row: row["capability_binding"].update(preflight_ready=False),
            "binding",
        ),
        (lambda row: row["capability_forms"].update(forms_sha256="sha256:" + "0" * 64), "forms"),
        (lambda row: row["budget"].update(budget_id="rb_other"), "budget"),
        (lambda row: row["evidence_view"].update(schema="acp2.runtime.other.v1"), "evidence"),
        (lambda row: row["viewmodel"].update(node_keys=["other"]), "viewmodel"),
    ],
)
def test_each_reuse_assertion_can_fail_on_its_own(mutation: Any, failing: str) -> None:
    nmr = _row("nmr")
    thermo = _row("thermochemistry")
    mutation(thermo)
    reuse = harness.cross_domain_reuse(nmr, thermo)
    assert reuse["passed"] is False
    if failing == "forms":
        assert reuse["capability"]["forms"]["passed"] is False
    elif failing == "binding":
        assert reuse["capability"]["binding"]["passed"] is False
    else:
        assert reuse[failing]["passed"] is False


def test_domain_capabilities_are_not_in_the_ui_form_subset() -> None:
    """Honest boundary: forms cover the built-in subset, not domain descriptors.

    ``capability_forms`` iterates the built-in descriptor document; the NMR and
    thermochemistry descriptors are forwarded by ``/science/capabilities`` only.
    The harness records that as a limitation instead of inventing forms.
    """
    from acp.api.capability_forms import capability_forms
    from cccp.calculation.capabilities import builtin_catalog, domain_catalog

    document = capability_forms(descriptors=builtin_catalog().to_dict()["capabilities"])
    served = {entry["capability_id"] for entry in document["forms"]}
    assert {"nmr.shielding", "nmr.compare", "thermochemistry.ensemble"}.isdisjoint(served)
    assert set(domain_catalog().to_dict()["capabilities"][0]) >= {
        "capability_id",
        "implementation_id",
    }


# ---------------------------------------------------------------------------
# Fast layer: real stores, project scoping
# ---------------------------------------------------------------------------


def _context(tmp_path: Path) -> harness.Context:
    root = tmp_path / "m406b"
    ctx = harness.Context(
        out=tmp_path / "out",
        work=root / "work",
        run_root=root / "run-root",
        identity_db=root / "identity" / "acp_identity.db",
        jobs_db=root / "run-root" / "acp_jobs.db",
        admission_db=root / "run-root" / "acp_admission.db",
        resources_db=root / "run-root" / "acp_research.db",
    )
    for path in (ctx.out, ctx.work, ctx.run_root, ctx.identity_db.parent):
        path.mkdir(parents=True, exist_ok=True)
    return ctx


def test_research_memory_is_project_scoped_across_six_hypotheses(tmp_path: Path) -> None:
    matrix = harness.research_memory_matrix(_context(tmp_path))
    assert matrix["passed"] is True
    assert matrix["created"] == 6
    assert matrix["distinct_resource_ids"] == 6
    assert matrix["cross_project_cas_denied"] is True
    assert all(row["denied"] for row in matrix["cross_project_reads"])
    assert matrix["heads_per_project"] == {project: 2 for project in harness.PROJECTS}
    assert matrix["cas_revision"] == 2


def test_approvals_are_query_scoped_to_the_project(tmp_path: Path) -> None:
    matrix = harness.approval_matrix(_context(tmp_path))
    assert matrix["passed"] is True
    assert len(matrix["approvals"]) == 6
    assert matrix["leaks"] == []
    assert matrix["visible_per_project"] == {project: 2 for project in harness.PROJECTS}
    assert all(row["state"] == "approved" for row in matrix["approvals"].values())


def test_budget_consumption_never_transfers_across_projects(tmp_path: Path) -> None:
    ctx = _context(tmp_path)
    budgets = harness.define_budgets(ctx)
    matrix = harness.budget_matrix(budgets["ledger"], budgets["policies"])
    assert matrix["passed"] is True
    assert matrix["distinct_budget_ids"] is True
    assert matrix["cross_budget_settlement"]["applied"] is False
    assert matrix["tiny_budget"]["first"]["admitted"] is True
    assert matrix["tiny_budget"]["second"]["admitted"] is False
    assert matrix["multi_project_budgets"] == 6


def test_same_task_id_in_two_projects_is_not_the_same_taskref(
    tmp_path: Path,
) -> None:
    ctx = _context(tmp_path)
    release_digest = "sha256:" + "f" * 64
    row = {
        "evidence_view": {
            "task_ref": {
                "schema": "acp2.runtime.task-ref.v1",
                "project_id": harness.PROJECTS[0],
                "task_id": "shared-task-id",
                "job_id": "shared-task-id",
                "attempt": 1,
                "release": {
                    "release_id": "release-shared",
                    "release_digest": release_digest,
                },
            }
        }
    }
    matrix = harness.taskref_matrix(ctx, row)
    assert matrix["passed"] is True
    assert matrix["same_task_id_different_project"] is True
    assert matrix["foreign_project_refused"] is True
    assert matrix["refusal"] == "foreign_project"


def test_remote_cli_forwards_the_api_key_like_the_mcp_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Client:
        def __init__(self, url: str, *, api_key: str | None = None) -> None:
            captured["url"] = url
            captured["api_key"] = api_key

    monkeypatch.setenv("ACP_API_KEY", "acpk_test.secret")
    with mock.patch("acp_services.entrypoints.controller_client.ControllerClient", _Client):
        from acp.cli import _task_controller_client

        _task_controller_client("http://127.0.0.1:1")
    assert captured == {"url": "http://127.0.0.1:1", "api_key": "acpk_test.secret"}

    monkeypatch.delenv("ACP_API_KEY", raising=False)
    captured.clear()
    with mock.patch("acp_services.entrypoints.controller_client.ControllerClient", _Client):
        from acp.cli import _task_controller_client

        _task_controller_client("http://127.0.0.1:1")
    assert captured["api_key"] is None


def test_h6_input_recommends_but_never_edits_the_coverage_file(tmp_path: Path) -> None:
    coverage = (
        REPO_ROOT
        / "docs"
        / "reference"
        / "next-change"
        / "acp2-contracts"
        / "release"
        / "evidence-coverage.current.json"
    )
    before = coverage.read_bytes()
    payload = harness.h6_input(_context(tmp_path), "PASS")
    assert coverage.read_bytes() == before
    assert payload["halves"]["M4-06b"]["status"] == "PASS"
    assert payload["halves"]["M4-06a"]["status"] == "PASS"
    assert payload["h6_judgement"] == "CLOSEABLE"
    recommendation = payload["coverage_recommendation"]
    assert recommendation["not_applied_by_this_run"] is True
    assert recommendation["row"] == "H6"
    assert recommendation["result"] == "PASS"
    assert recommendation["status"] == "CLOSED"
    assert payload["h4"]["changed_by_this_task"] is False


# ---------------------------------------------------------------------------
# Slow layer: the real end-to-end receipt
# ---------------------------------------------------------------------------


def _missing_prerequisites() -> list[str]:
    missing = []
    if harness.find_shermo() == "":
        missing.append("shermo")
    import shutil

    if shutil.which("xtb") is None:
        missing.append("xtb")
    if not harness.ROUTE_A_FREQ_LOG.is_file():
        missing.append("m4-04-route-a frequency log")
    return missing


@pytest.mark.slow
@pytest.mark.requires_xtb
@pytest.mark.requires_shermo
def test_cross_domain_and_multitenant_acceptance_passes_end_to_end(tmp_path: Path) -> None:
    missing = _missing_prerequisites()
    if missing:
        pytest.skip("missing prerequisites: " + ", ".join(missing))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "acceptance" / "acp2_m406b.py"),
            "--out",
            str(tmp_path / "evidence"),
            "--run-root",
            str(tmp_path / "run-root"),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    summary_path = tmp_path / "evidence" / "summary.json"
    assert summary_path.is_file(), completed.stderr[-2000:]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["status"] == "PASS", summary.get("reason") or summary["checks"]
    assert all(row["status"] == "PASS" for row in summary["checks"])
    assert summary["rows"]["cross_domain_nmr"]["evidence_kind"].startswith("mixed:")
    assert summary["rows"]["cross_domain_thermochemistry"]["evidence_kind"].startswith("native:")
    assert summary["cross_domain_reuse"]["passed"] is True
    assert summary["revocation"]["passed"] is True
    assert summary["h6_input"]["h6_judgement"] in {"CLOSEABLE", "OPEN"}
    # The isolated run root is removed after the run; no production path is used.
    assert summary["run_root"] == str((tmp_path / "run-root").resolve())
