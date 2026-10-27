"""M1-06 — research writer + Candidate-Contracts thin slice.

Fast tests (no subprocess, no Controller) cover the B-side writer contract that
the slice depends on:

* a proposal create binds both digests and persists exactly one pending
  approval; reads return the stored document verbatim;
* a duplicate create is a typed conflict, not a second approval/fact;
* a CAS with a stale revision is a typed conflict carrying the current revision;
* a changed science/plan digest revokes the old approval and opens round + 1;
* a record create re-admits the M1-03 view: a tampered projection, an `accept`
  on partial evidence and a foreign research identity are all refused;
* the writer never writes an execution fact (the job store is untouched).

The slow test runs the real end-to-end acceptance script (Controller + SQLite +
MCP stdio child + RPH bridge + frontend contract probe) and asserts its summary
and negatives.  The script is independently runnable
(``PYTHONPATH=src python3.11 scripts/acceptance/acp2_thin_slice.py --out DIR``).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from acp_services.data.research_resources import ResearchResourceStore
from acp_services.runtime import worker
from acp_services.runtime.approvals import ApprovalService
from acp_services.runtime.evidence_projection import EvidenceView, project_evidence
from acp_services.runtime.research_session import ResearchIdentity
from acp_services.runtime.research_writer import (
    ResearchWriteConflictError,
    ResearchWriteDeniedError,
    ResearchWriteError,
    ResearchWriter,
)
from acp_services.workspace.command_identity import CommandIdentity

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "acp2" / "science" / "evidence_projection"
PROJECT = "proj-m1-06"
RELEASE_ID = "rel-m1-06"
RELEASE_DIGEST = "sha256:" + "c" * 64


def _identity(command: CommandIdentity, seed: str = "s") -> ResearchIdentity:
    return ResearchIdentity.allocate(command, session_seed=seed)


def _writer(tmp_path: Path) -> tuple[ResearchWriter, ApprovalService, CommandIdentity, Path]:
    run_root = tmp_path / "root"
    run_root.mkdir()
    store = ResearchResourceStore(run_root / "acp_research.db")
    approvals = ApprovalService(run_root / "acp_jobs.db")
    writer = ResearchWriter(resources=store, approvals=approvals)
    command = CommandIdentity.from_local(project_id=PROJECT)
    return writer, approvals, command, run_root


def _proposal(proposal_id: str = "prop-m1-06-001") -> dict[str, Any]:
    return {
        "proposal_id": proposal_id,
        "project_id": PROJECT,
        "question_ref": "q-m1-06",
        "question_revision": 0,
        "reaction_ref": "rxn-m1-06",
        "reaction_revision": 0,
        "hypothesis": "one stub dispatch yields one digest-bound report",
        "recipe_digest": "sha256:" + "a" * 64,
        "plan_definition_digest": "",
        "science": {
            "capability_steps": ["singlepoint"],
            "initial_inputs": [
                {
                    "input_id": "in-1",
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


def _plan() -> dict[str, Any]:
    return {
        "workflow": "fake",
        "input": {"source": "CCO", "source_type": "smiles"},
        "method": {},
        "resources": {"cores": 1},
    }


def _launch_budget() -> dict[str, Any]:
    return {"unit": "task_count", "count": 1, "timeout_s": 120}


def _task_result() -> dict[str, Any]:
    body = {
        "schema": worker.TASK_RESULT_SCHEMA,
        "task": "singlepoint",
        "status": "completed",
        "complete": True,
        "truth_table_row": "a",
        "qualification": worker.QUALIFICATION_COMPLETE,
        "partial_result": False,
        "normal_reuse": False,
        "publish": True,
        "publish_policy": "normal",
        "receipt_state": "completed",
        "job_status": "completed",
        "exit_code": 0,
        "required_facts": ["energy"],
        "missing_required_facts": [],
        "authority": "test",
        "result_digest": "sha256:" + "d" * 64,
        "written_at": "2026-10-27T00:00:00Z",
    }
    return {**body, "content_digest": worker.task_result_content_digest(body)}


def _receipt(task_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "receipt_version": 1,
        "job_id": "job-m1-06",
        "attempt": 1,
        "submission_id": "sub-m1-06",
        "state": "completed",
        "result_digest": "sha256:" + "d" * 64,
        "written_at": "2026-10-27T00:00:01Z",
        "release_id": RELEASE_ID,
        "release_digest": RELEASE_DIGEST,
        "evidence_digests": {"task_result_digest": task_result["content_digest"]},
    }


def _project(
    tmp_path: Path,
    *,
    qualification: str | None = None,
    identity: ResearchIdentity,
) -> EvidenceView:
    root = tmp_path / "projection"
    if not root.exists():
        shutil.copytree(FIXTURES, root)
    task_result = _task_result()
    if qualification is not None:
        body = {key: value for key, value in task_result.items() if key != "content_digest"}
        body["qualification"] = qualification
        body["complete"] = qualification == worker.QUALIFICATION_COMPLETE
        body["status"] = "partial"
        task_result = {**body, "content_digest": worker.task_result_content_digest(body)}
    receipt = _receipt(task_result)
    payload = (FIXTURES / "report.supported.positive.json").read_bytes()
    import hashlib

    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    return project_evidence(
        identity=identity,
        round_index=0,
        task_ref={
            "project_id": PROJECT,
            "task_id": "job-m1-06",
            "attempt": 1,
            "release": {"release_id": RELEASE_ID, "release_digest": RELEASE_DIGEST},
        },
        receipt=receipt,
        task_result=task_result,
        report=payload,
        report_digest=digest,
        read_root=root,
    )


def _record_payload(
    view: dict[str, Any], *, research: str = "", decision: str = "accept"
) -> dict[str, Any]:
    return {
        "project_id": PROJECT,
        "research_id": research,
        "round": 0,
        "decision": decision,
        "plan_definition_digest": view["evidence_refs"] and "sha256:" + "e" * 64,
        "task_refs": [
            {
                "project_id": PROJECT,
                "job_id": "job-m1-06",
                "attempt": 1,
                "release_id": RELEASE_ID,
            }
        ],
        "evidence_refs": [
            *[
                {
                    "evidence_id": ref["evidence_id"],
                    "kind": ref["kind"],
                    "sha256": ref["sha256"],
                    "relative_path": ref.get("relative_path"),
                }
                for ref in view["evidence_refs"]
            ],
            {
                "evidence_id": "report-m1-06",
                "kind": "evidence_report",
                "sha256": "sha256:" + "f" * 64,
                "relative_path": "RESULT/evidence_report.json",
            },
        ],
        "evidence_view": view,
        "created_at": "2026-10-27T00:00:00Z",
        "approval_ref": "",
    }


def test_create_proposal_binds_two_digests_and_one_approval(tmp_path: Path) -> None:
    writer, approvals, command, _root = _writer(tmp_path)
    result = writer.create_proposal(
        command=command,
        session_seed="slice-1",
        proposal=_proposal(),
        plan=_plan(),
        budget=_launch_budget(),
    )
    assert result.record.revision == 1
    assert result.approval.state == "pending"
    assert result.approval.binding_status == "full"
    assert result.approval.plan_digest == result.digests["plan_digest"]
    assert result.approval.science_digest == result.digests["science_digest"]
    assert result.approval.approval_id == result.record.document["approval_ref"]
    assert result.record.document["plan_definition_digest"] == result.digests["science_digest"]
    assert result.identity.research_id.startswith("res-")
    assert approvals.query(project_id=PROJECT, proposal_ref="prop-m1-06-001")

    stored = writer.read_proposal(
        principal_id=command.principal_id, project_id=PROJECT, proposal_id="prop-m1-06-001"
    )
    assert stored.digest == result.record.digest
    assert stored.document["project_id"] == PROJECT


def test_duplicate_create_is_a_typed_conflict_not_a_second_fact(tmp_path: Path) -> None:
    writer, approvals, command, _root = _writer(tmp_path)
    writer.create_proposal(
        command=command,
        session_seed="s",
        proposal=_proposal(),
        plan=_plan(),
        budget=_launch_budget(),
    )
    with pytest.raises(ResearchWriteConflictError) as excinfo:
        writer.create_proposal(
            command=command,
            session_seed="s",
            proposal=_proposal(),
            plan=_plan(),
            budget=_launch_budget(),
        )
    assert excinfo.value.rule == "revision_conflict"
    rows = approvals.query(project_id=PROJECT, proposal_ref="prop-m1-06-001")
    assert len(rows) == 1


def test_cas_requires_the_observed_revision(tmp_path: Path) -> None:
    writer, _approvals, command, _root = _writer(tmp_path)
    created = writer.create_proposal(
        command=command,
        session_seed="s",
        proposal=_proposal(),
        plan=_plan(),
        budget=_launch_budget(),
    )
    with pytest.raises(ResearchWriteConflictError) as excinfo:
        writer.update_proposal(
            command=command,
            session_seed="s",
            proposal_id="prop-m1-06-001",
            expected_revision=created.record.revision + 3,
            proposal=_proposal(),
            plan=_plan(),
            budget=_launch_budget(),
        )
    assert excinfo.value.rule == "revision_conflict"
    assert excinfo.value.current_revision == created.record.revision


def test_changed_science_revokes_the_old_approval_and_opens_a_new_round(tmp_path: Path) -> None:
    writer, approvals, command, _root = _writer(tmp_path)
    created = writer.create_proposal(
        command=command,
        session_seed="s",
        proposal=_proposal(),
        plan=_plan(),
        budget=_launch_budget(),
    )
    approved, _ = approvals.approve(
        created.approval.approval_id,
        approver_principal=command.principal_id,
        expected_revision=created.approval.revision,
    )
    assert approved.state == "approved"

    changed = _proposal()
    changed["science"] = {**changed["science"], "method": "wB97X-D4"}
    changed["round"] = 0
    candid = writer.update_proposal(
        command=command,
        session_seed="s",
        proposal_id="prop-m1-06-001",
        expected_revision=created.record.revision,
        proposal=changed,
        plan=_plan(),
        budget=_launch_budget(),
    )
    assert candid.round == 1
    assert candid.superseded_approval == created.approval.approval_id
    assert candid.approval.state == "pending"
    assert candid.approval.approval_id != created.approval.approval_id
    assert approvals.get(created.approval.approval_id).state == "revoked"
    assert candid.digests["science_digest"] != created.digests["science_digest"]
    assert candid.digests["plan_digest"] == created.digests["plan_digest"]


def test_changed_execution_plan_uses_the_m1_02_supersede_chain(tmp_path: Path) -> None:
    writer, approvals, command, _root = _writer(tmp_path)
    created = writer.create_proposal(
        command=command,
        session_seed="s",
        proposal=_proposal(),
        plan=_plan(),
        budget=_launch_budget(),
    )
    approvals.approve(
        created.approval.approval_id,
        approver_principal=command.principal_id,
        expected_revision=created.approval.revision,
    )
    changed_plan = {**_plan(), "method": {"levels": {"method": "wB97X-D4"}}}
    candid = writer.update_proposal(
        command=command,
        session_seed="s",
        proposal_id="prop-m1-06-001",
        expected_revision=created.record.revision,
        proposal=_proposal(),
        plan=changed_plan,
        budget=_launch_budget(),
    )
    assert candid.round == 1
    assert candid.superseded_approval == created.approval.approval_id
    assert candid.approval.state == "pending"
    assert candid.digests["plan_digest"] != created.digests["plan_digest"]
    assert candid.digests["science_digest"] == created.digests["science_digest"]
    assert approvals.get(created.approval.approval_id).state == "revoked"


def test_record_create_refuses_a_tampered_projection(tmp_path: Path) -> None:
    writer, _approvals, command, _root = _writer(tmp_path)
    view = _project(tmp_path, identity=_identity(command)).to_dict()
    tampered = dict(view)
    tampered["evidence_status"] = "partial"
    with pytest.raises(ResearchWriteConflictError) as excinfo:
        writer.create_record(
            command=command,
            session_seed="s",
            record=_record_payload(tampered),
            evidence_view=tampered,
        )
    assert excinfo.value.rule == "projection_digest_mismatch"


def test_record_create_refuses_accept_on_partial_evidence(tmp_path: Path) -> None:
    writer, _approvals, command, _root = _writer(tmp_path)
    partial = _project(
        tmp_path, qualification=worker.QUALIFICATION_PARTIAL, identity=_identity(command)
    ).to_dict()
    assert partial["evidence_status"] == "partial"
    with pytest.raises(ResearchWriteError) as excinfo:
        writer.create_record(
            command=command,
            session_seed="s",
            record=_record_payload(partial),
            evidence_view=partial,
        )
    assert excinfo.value.rule == "evidence_not_complete"


def test_record_create_refuses_a_foreign_research_identity(tmp_path: Path) -> None:
    writer, _approvals, command, _root = _writer(tmp_path)
    view = _project(tmp_path, identity=_identity(command)).to_dict()
    with pytest.raises(ResearchWriteDeniedError) as excinfo:
        writer.create_record(
            command=command,
            session_seed="s",
            record=_record_payload(view, research="res-foreign0000000000000000"),
            evidence_view=view,
        )
    assert excinfo.value.rule == "research_identity_mismatch"


def test_record_create_accepts_the_digest_bound_view(tmp_path: Path) -> None:
    writer, approvals, command, _root = _writer(tmp_path)
    created = writer.create_proposal(
        command=command,
        session_seed="s",
        proposal=_proposal(),
        plan=_plan(),
        budget=_launch_budget(),
    )
    approvals.approve(
        created.approval.approval_id,
        approver_principal=command.principal_id,
        expected_revision=created.approval.revision,
    )
    identity = _identity(command)
    view = _project(tmp_path, identity=identity).to_dict()
    payload = _record_payload(view, research=identity.research_id)
    payload["plan_definition_digest"] = created.digests["science_digest"]
    payload["approval_ref"] = created.approval.approval_id
    result = writer.create_record(
        command=command, session_seed="s", record=payload, evidence_view=view
    )
    assert result.record.revision == 1
    assert result.decision == "accept"
    assert result.projection_digest == view["projection_digest"]
    assert result.record.document["decision"] == "accept"
    assert result.record.document["research_id"] == created.identity.research_id


@pytest.mark.slow
def test_thin_slice_script_passes_end_to_end(tmp_path: Path) -> None:
    """Run the real slice (Controller + SQLite + MCP stdio + RPH bridge)."""
    import subprocess
    import sys

    out = tmp_path / "candidate-contracts"
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "acceptance" / "acp2_thin_slice.py"),
            "--out",
            str(out),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=900,
        env={
            **__import__("os").environ,
            "PYTHONPATH": str(REPO / "src"),
        },
    )
    summary_path = out / "summary.json"
    assert summary_path.is_file(), completed.stdout + completed.stderr[-2000:]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["status"] == "PASS", summary["reasons"]
    assert completed.returncode == 0
    assert summary["mcp"]["protocol_version"] == "2025-06-18"
    assert len(summary["mcp"]["tools"]) == 8
    negatives = json.loads((out / "negative-cases.json").read_text(encoding="utf-8"))
    assert {item["name"] for item in negatives} == {
        "negative.stale_revision",
        "negative.wrong_identity",
        "negative.fake_evidence",
        "negative.accept_on_partial_evidence",
        "negative.injection_plan_digest",
        "negative.approver_identity_from_payload",
        "negative.revoked_approval",
    }
    assert all(item["ok"] for item in negatives), negatives
    contracts = json.loads((out / "candidate-contracts.json").read_text(encoding="utf-8"))
    assert contracts["evidence_kind"] == "fixture"
    assert contracts["claims"]["real_science"] is False
    assert contracts["claims"]["real_controller"] is True
    go_no_go = json.loads((out / "go-no-go-inputs.json").read_text(encoding="utf-8"))
    assert go_no_go["decision"] == "NOT_TAKEN_HERE"
    assert go_no_go["quota"]["model_calls"] == 0
    assert go_no_go["plan_graph"]["start_critical_after_m1_06"]
