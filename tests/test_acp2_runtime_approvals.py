"""B16 — persistent proposal approvals bound to frozen scientific plans.

Covers the frozen B03 canonical vectors (12/12), the proposal/plan projection
rules, the approval lifecycle (draft/pending/approved/rejected/expired/revoked)
with optimistic revisions, restart persistence, the submit authorization matrix
(unapproved/pending/expired/revoked/changed-plan/other-user) and a real HTTP
submit proving that a refused approval creates no job row and no work directory.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from acp_services.runtime.approvals import (
    ApprovalConflictError,
    ApprovalDeniedError,
    ApprovalService,
    ApprovalState,
)
from acp_services.runtime.canonical_digest import (
    CANONICAL_DIGEST_VERSION,
    CanonicalDigestError,
    canonical_digest,
    canonical_json,
    frozen_plan_definition,
    plan_digest,
    proposal_digest,
)

CONTRACTS = (
    Path(__file__).resolve().parents[1] / "docs/reference/next-change/acp2-contracts/runtime"
)
VECTORS = json.loads((CONTRACTS / "canonical-digest-vectors.v1.json").read_text(encoding="utf-8"))

PLAN = frozen_plan_definition(
    workflow="singlepoint",
    input={"xyz_text": "1\nH\nH 0 0 0.74\n"},
    method={"method": "STO-3G", "charge": 0, "multiplicity": 1},
    resources={"cores": 1, "memory_mb": 512},
)
PLAN_DIGEST = plan_digest(PLAN).digest
PROPOSAL = {
    "proposal_id": "prop-test",
    "project_id": "project-test",
    "title": "Approval test",
    "question": "Does it dispatch?",
    "capability_ids": ["singlepoint.run"],
    "recipe_ref": {
        "schema_id": "acp2.science.recipe.v1",
        "version": "1.0.0",
        "digest": "sha256:" + "1" * 64,
    },
    "plan_digest": PLAN_DIGEST,
    "budget_request": {"value": "60", "unit": "second"},
}
PROPOSAL_DIGEST = proposal_digest(PROPOSAL).digest
BUDGET = {"unit": "task_count", "count": 1, "timeout_s": 60}


def _service(tmp_path: Path) -> ApprovalService:
    return ApprovalService(tmp_path / "jobs.db")


def _approved(service: ApprovalService, *, ref: str = "prop-test", **kwargs: Any):
    approval, _ = service.request(
        proposal_ref=ref,
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project-test",
        requester_principal="alice",
        plan_definition=PLAN,
        budget=BUDGET,
        **kwargs,
    )
    approved, _ = service.approve(
        approval.approval_id, approver_principal="bob", expected_revision=approval.revision
    )
    return approved


# ── canonical vectors ───────────────────────────────────────────────────────


def test_canonical_vectors_reproduce_byte_for_byte() -> None:
    assert len(VECTORS["vectors"]) == 12
    for vector in VECTORS["vectors"]:
        if vector["kind"] == "proposal_digest":
            projection = proposal_digest(vector["input"])
            assert projection.digest == vector["digest"], vector["id"]
            assert projection.canonical_json == vector["canonical_json"], vector["id"]
            continue
        try:
            digest = canonical_digest(
                vector["input"],
                set_fields=tuple(vector.get("set_fields") or ()),
                drop_fields=tuple(vector.get("drop_fields") or ()),
            )
            text = canonical_json(
                vector["input"],
                set_fields=tuple(vector.get("set_fields") or ()),
                drop_fields=tuple(vector.get("drop_fields") or ()),
            )
        except CanonicalDigestError as exc:
            expected = vector["expected_error"]["params"]["rule"]
            assert exc.rule == expected, vector["id"]
            assert "digest" not in vector
            continue
        assert "expected_error" not in vector, vector["id"]
        assert digest == vector["digest"], vector["id"]
        assert text == vector["canonical_json"], vector["id"]


def test_helper_version_is_recorded() -> None:
    assert CANONICAL_DIGEST_VERSION == "1.0.0"
    projection = plan_digest(PLAN)
    assert projection.meta()["version"] == CANONICAL_DIGEST_VERSION
    assert projection.meta()["spec_id"] == "acp2.canonical-digest.v1"


def test_proposal_projection_excludes_volatile_and_appended_refs() -> None:
    baseline = proposal_digest(PROPOSAL).digest
    decorated = dict(PROPOSAL)
    decorated.update(
        approval_ref="ap-1",
        status="approved",
        updated_at="2026-10-22T10:00:00Z",
        revision=7,
        etag="abc",
        display_name="presentation-only",
        task_refs=[{"task_id": "t-1", "attempt": 1}],
        evidence_refs=[{"evidence_id": "ev-1"}],
    )
    assert proposal_digest(decorated).digest == baseline
    with pytest.raises(CanonicalDigestError) as excinfo:
        proposal_digest({**PROPOSAL, "surprise_science": {"temperature": 300}})
    assert excinfo.value.rule == "unknown_science_field"


def test_plan_digest_ignores_display_name_and_changes_with_science() -> None:
    base = plan_digest(PLAN).digest
    assert plan_digest({**PLAN, "display_name": "renamed", "name": "x"}).digest == base
    assert plan_digest({**PLAN, "method": {"method": "B3LYP"}}).digest != base
    assert plan_digest({**PLAN, "input": {"xyz_text": "different"}}).digest != base
    assert (
        plan_digest(
            {**PLAN, "recipe_ref": {"schema_id": "acp2.science.recipe.v1", "version": "1.0.0"}}
        ).digest
        != base
    )


# ── lifecycle ───────────────────────────────────────────────────────────────


def test_draft_request_approve_repeat_and_query(tmp_path: Path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(
        proposal_ref="prop-test",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project-test",
        requester_principal="alice",
        plan_definition=PLAN,
        budget=BUDGET,
    )
    assert draft.state == ApprovalState.DRAFT.value
    assert service.authorize_submission  # draft is not approvable
    with pytest.raises(ApprovalConflictError):
        service.approve(draft.approval_id, approver_principal="bob", expected_revision=1)

    pending, replayed = service.request(
        proposal_ref="prop-test",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project-test",
        requester_principal="alice",
        plan_definition=PLAN,
        budget=BUDGET,
    )
    assert replayed is False
    assert pending.approval_id == draft.approval_id
    assert pending.state == ApprovalState.PENDING.value
    assert pending.revision == 2

    again, replayed = service.request(
        proposal_ref="prop-test",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project-test",
        requester_principal="alice",
        plan_definition=PLAN,
        budget=BUDGET,
    )
    assert replayed is True and again.approval_id == pending.approval_id

    approved, replayed = service.approve(
        pending.approval_id, approver_principal="bob", expected_revision=pending.revision
    )
    assert replayed is False and approved.state == "approved" and approved.revision == 3
    repeat, replayed = service.approve(
        pending.approval_id, approver_principal="bob", expected_revision=pending.revision
    )
    assert replayed is True and repeat.revision == 3

    rows = service.query(project_id="project-test", approval_ref=pending.approval_id)
    assert [row.approval_id for row in rows] == [pending.approval_id]


def test_revision_conflict_is_reported(tmp_path: Path) -> None:
    service = _service(tmp_path)
    approval, _ = service.request(
        proposal_ref="prop-test",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project-test",
        requester_principal="alice",
        plan_definition=PLAN,
        budget=BUDGET,
    )
    service.approve(
        approval.approval_id, approver_principal="bob", expected_revision=approval.revision
    )
    with pytest.raises(ApprovalConflictError) as excinfo:
        service.revoke(
            approval.approval_id,
            actor_principal="bob",
            expected_revision=approval.revision,  # stale
        )
    assert excinfo.value.rule == "revision_conflict"
    revoked, _ = service.revoke(approval.approval_id, actor_principal="bob", expected_revision=2)
    assert revoked.state == "revoked" and revoked.revision == 3


def test_reject_and_expiry(tmp_path: Path) -> None:
    service = _service(tmp_path)
    approval, _ = service.request(
        proposal_ref="prop-reject",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project-test",
        requester_principal="alice",
        plan_definition=PLAN,
        budget=BUDGET,
    )
    rejected, _ = service.reject(
        approval.approval_id, approver_principal="bob", expected_revision=approval.revision
    )
    assert rejected.state == "rejected"
    with pytest.raises(ApprovalConflictError):
        service.approve(
            approval.approval_id, approver_principal="bob", expected_revision=rejected.revision
        )

    expires_at = (
        (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    )
    expired, _ = service.request(
        proposal_ref="prop-expired",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project-test",
        requester_principal="alice",
        plan_definition=PLAN,
        budget=BUDGET,
        expires_at=expires_at,
    )
    with pytest.raises(ApprovalDeniedError) as excinfo:
        service.authorize_submission(
            plan_digest=PLAN_DIGEST,
            proposal_ref="prop-expired",
            project_id="project-test",
            requester_principal="alice",
            proposal_digest=PROPOSAL_DIGEST,
            budget=BUDGET,
        )
    assert excinfo.value.rule == "approval_expired"
    rows = service.query(project_id="project-test", proposal_ref="prop-expired")
    assert rows[0].state == "expired"


# ── submit authorization matrix ─────────────────────────────────────────────


def _authorize(service: ApprovalService, **overrides: Any):
    values = {
        "plan_digest": PLAN_DIGEST,
        "proposal_ref": "prop-test",
        "project_id": "project-test",
        "requester_principal": "alice",
        "proposal_digest": PROPOSAL_DIGEST,
        "budget": BUDGET,
    }
    values.update(overrides)
    return service.authorize_submission(**values)


def test_authorization_matrix(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(ApprovalDeniedError) as missing:
        _authorize(service)
    assert missing.value.rule == "approval_required"

    pending, _ = service.request(
        proposal_ref="prop-test",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project-test",
        requester_principal="alice",
        plan_definition=PLAN,
        budget=BUDGET,
    )
    with pytest.raises(ApprovalDeniedError) as still_pending:
        _authorize(service)
    assert still_pending.value.rule == "approval_pending"

    approved, _ = service.approve(
        pending.approval_id, approver_principal="bob", expected_revision=pending.revision
    )
    assert _authorize(service).approval_id == approved.approval_id

    with pytest.raises(ApprovalDeniedError) as other_user:
        _authorize(service, requester_principal="carol")
    assert other_user.value.rule == "approval_principal_mismatch"

    with pytest.raises(ApprovalDeniedError) as other_project:
        _authorize(service, project_id="project-other")
    assert other_project.value.rule == "project_scope_mismatch"

    with pytest.raises(ApprovalConflictError) as changed_plan:
        _authorize(service, plan_digest="sha256:" + "2" * 64)
    assert changed_plan.value.rule == "approved_plan_digest_mismatch"

    with pytest.raises(ApprovalConflictError) as changed_proposal:
        _authorize(service, proposal_digest="sha256:" + "3" * 64)
    assert changed_proposal.value.rule == "approved_proposal_digest_mismatch"

    with pytest.raises(ApprovalConflictError) as changed_budget:
        _authorize(service, budget={"unit": "task_count", "count": 2, "timeout_s": 60})
    assert changed_budget.value.rule == "approved_budget_mismatch"

    service.revoke(approved.approval_id, actor_principal="bob", expected_revision=approved.revision)
    with pytest.raises(ApprovalDeniedError) as revoked:
        _authorize(service)
    assert revoked.value.rule == "approval_revoked"


def test_restart_reuses_the_same_approval_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    first = ApprovalService(db_path)
    approval = _approved(first)
    task_ref = {"task_id": "task-1", "attempt": 1}
    first.bind_task_ref(approval.approval_id, task_ref, actor_principal="alice")

    second = ApprovalService(db_path)  # a real process restart opens the same DB
    reloaded = second.get(approval.approval_id)
    assert reloaded is not None
    assert reloaded.state == "approved"
    assert reloaded.revision == approval.revision
    assert reloaded.task_ref == task_ref
    repeat, replayed = second.approve(
        approval.approval_id, approver_principal="bob", expected_revision=1
    )
    assert replayed is True and repeat.revision == approval.revision
    assert _authorize(second).approval_id == approval.approval_id


def test_events_use_the_single_outbox_and_never_write_state(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    service = ApprovalService(db_path)
    approval = _approved(service)
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT event_id, owner_type, scope, event_type, revision FROM job_outbox "
            "WHERE owner_type='project' ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()
    assert rows
    assert {row[3] for row in rows} >= {"approval.requested", "approval.approved"}
    assert all(row[1] == "project" and row[2] == "project" for row in rows)
    event_ids = [row[0] for row in rows]
    assert len(event_ids) == len(set(event_ids))
    # Re-reading the state after the events proves events are not an authority.
    assert service.get(approval.approval_id).state == "approved"


def test_no_new_jobstatus_or_waiting_review() -> None:
    from acp.scheduler.jobs import JobStatus

    values = {status.value for status in JobStatus}
    # JobStatus already has an unrelated execution PENDING; approval-only states
    # must never be job statuses, and WAITING_REVIEW is not reused either.
    assert values.isdisjoint({"draft", "approved", "rejected", "expired", "revoked"})
    # WAITING_REVIEW remains a review-only job status; approval code never uses
    # it and never changes resume()'s semantics.
    import ast

    import acp_services.runtime.approvals as approvals_module

    tree = ast.parse(Path(approvals_module.__file__).read_text(encoding="utf-8"))
    referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "JobStatus" not in referenced
    assert "resume" not in referenced


# ── real HTTP submit: no dispatch without the record ────────────────────────


@pytest.fixture()
def live_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import acp.scheduler.capabilities as capabilities_module
    import acp.scheduler.manager as manager_module

    monkeypatch.setattr(capabilities_module, "local_satisfies", lambda required: True)
    monkeypatch.setattr(manager_module, "local_satisfies", lambda required: True)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from fastapi.testclient import TestClient

    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=2, poll_interval=60)
    with TestClient(app) as client:
        manager = app.state.job_manager
        # Record dispatch intent (job row/dir) without starting QC.
        monkeypatch.setattr(manager, "_start_submission_thread", lambda *args, **kwargs: None)
        yield client, app


def _job_count(app: Any) -> int:
    conn = sqlite3.connect(str(app.state.job_manager.store.db_path))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
    finally:
        conn.close()


def _submit(
    client: Any,
    project_id: str,
    *,
    plan: dict[str, Any],
    plan_digest_value: str,
    proposal_ref: str,
    proposal_digest_value: str | None,
    budget: dict[str, Any] | None,
    name: str = "b16-submit",
) -> Any:
    body = {
        "workflow": plan["workflow"],
        "name": name,
        "input": plan["input"],
        "method": plan["method"],
        "resources": plan["resources"],
        "project_id": project_id,
        "plan_digest": plan_digest_value,
        "proposal_ref": proposal_ref,
    }
    if proposal_digest_value is not None:
        body["proposal_digest"] = proposal_digest_value
    if budget is not None:
        body["budget"] = budget
    return client.post("/api/v1/jobs", json=body)


def test_http_unapproved_submit_creates_no_job_or_workdir(live_app) -> None:
    client, app = live_app
    project = app.state.job_manager.default_project_id
    before = _job_count(app)
    root = Path(app.state.job_manager.run_root)
    dirs_before = {entry.name for entry in root.iterdir() if entry.is_dir()}
    response = _submit(
        client,
        project,
        plan=PLAN,
        plan_digest_value=PLAN_DIGEST,
        proposal_ref="prop-http-unapproved",
        proposal_digest_value=PROPOSAL_DIGEST,
        budget=BUDGET,
    )
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["params"]["rule"] == "approval_required"
    assert _job_count(app) == before
    dirs_after = {entry.name for entry in root.iterdir() if entry.is_dir()}
    assert dirs_after == dirs_before


def test_http_approved_submit_dispatches_and_other_principal_cannot(live_app) -> None:
    client, app = live_app
    project = app.state.job_manager.default_project_id
    proposal = {**PROPOSAL, "project_id": project}
    prop_digest = proposal_digest(proposal).digest
    requested = client.post(
        "/api/v2/research/approvals",
        json={
            "proposal_ref": "prop-http",
            "proposal": proposal,
            "plan": PLAN,
            "plan_digest": PLAN_DIGEST,
            "project_id": project,
            "budget": BUDGET,
        },
    )
    assert requested.status_code == 201, requested.text
    approval_ref = requested.json()["approval_ref"]
    approved = client.post(
        f"/api/v2/research/approvals/{approval_ref}/approve",
        json={"expected_revision": 1},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    before = _job_count(app)
    accepted = _submit(
        client,
        project,
        plan=PLAN,
        plan_digest_value=PLAN_DIGEST,
        proposal_ref="prop-http",
        proposal_digest_value=prop_digest,
        budget=BUDGET,
        name="b16-approved-display",
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["approval_ref"] == approval_ref
    assert _job_count(app) == before + 1

    # A different verified principal cannot reuse the record.
    first = ApprovalService(app.state.job_manager.store.db_path)
    with pytest.raises(ApprovalDeniedError) as excinfo:
        first.authorize_submission(
            plan_digest=PLAN_DIGEST,
            proposal_ref="prop-http",
            project_id=project,
            requester_principal="someone-else",
            proposal_digest=prop_digest,
            budget=BUDGET,
        )
    assert excinfo.value.rule == "approval_principal_mismatch"
