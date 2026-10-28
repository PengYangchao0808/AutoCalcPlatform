"""M2-03a — approval policy, authenticated approver and the double digest.

Exit condition (EXECUTION_PLAN M2-03a): the approver comes only from the
authenticated principal, an agent/model credential can never approve or decide
(enforced by server policy, not by UI discipline), requester/approver separation
is a *policy profile* answer, a browser/model payload naming an approver is
refused at the policy layer, and the approval additively persists the scientific
plan projection next to the two digests.

Coverage map:

* :func:`test_policy_matrix_is_frozen_and_profile_derived` — the frozen matrix;
* :func:`test_agent_credential_cannot_decide_even_with_project_control` — the
  server verdict for an agent credential (no HTTP needed);
* :func:`test_service_subject_never_signs_a_scientific_approval`;
* :func:`test_separation_of_duties_is_a_profile_answer`;
* :func:`test_payload_approver_fields_are_one_definition`
  + :func:`test_http_payload_approver_is_refused_with_the_policy_rule`;
* :func:`test_http_agent_credential_cannot_approve`;
* :func:`test_http_separated_approver_is_allowed_and_self_approval_refused`;
* :func:`test_approval_additively_stores_the_science_projection`
  + :func:`test_projection_that_does_not_reproduce_its_digest_is_refused`;
* :func:`test_http_approval_stores_the_projection_of_a_c_shaped_proposal`;
* :func:`test_expired_and_revoked_approvals_cannot_authorize`;
* :func:`test_cross_identity_approval_cannot_be_reused`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from acp_services.runtime.approval_policy import (
    APPROVAL_POLICY_MATRIX,
    APPROVER_PAYLOAD_FIELDS,
    authorize_decision,
    payload_approver_fields,
    resolve_approval_policy,
)
from acp_services.runtime.approvals import (
    ApprovalDeniedError,
    ApprovalService,
    ApprovalValidationError,
    science_projection_digest,
)
from acp_services.runtime.canonical_digest import (
    frozen_plan_definition,
    plan_digest,
    proposal_plan_definition_digest,
)
from acp_services.runtime.submit_policy import SUBMIT_POLICY_ENV
from acp_services.workspace.command_identity import (
    CommandIdentity,
    EntryClass,
    PermissionScope,
    SubjectKind,
)

PLAN_DIGEST = "sha256:" + "b" * 64
PROPOSAL_DIGEST = "sha256:" + "a" * 64


def _local() -> CommandIdentity:
    return CommandIdentity(
        principal_id="principal_local",
        subject_kind=SubjectKind.OPERATOR,
        entry_class=EntryClass.HTTP,
        permission_scope=PermissionScope.LOCAL_SINGLE,
    )


def _keyed(principal: str = "principal_user", key: str = "key_alpha") -> CommandIdentity:
    return CommandIdentity(
        principal_id=principal,
        key_id=key,
        actions=("read", "write"),
        subject_kind=SubjectKind.USER,
        entry_class=EntryClass.HTTP,
        permission_scope=PermissionScope.API_KEY,
    )


def _agent() -> CommandIdentity:
    return CommandIdentity(
        principal_id="principal_agent",
        key_id="key_agent",
        actions=("read", "write"),
        subject_kind=SubjectKind.SERVICE,
        entry_class=EntryClass.HTTP,
        permission_scope="agent",
    )


def _proposal(project: str = "project_alpha") -> dict[str, Any]:
    return {
        "proposal_id": "prop-m203",
        "project_id": project,
        "recipe_digest": "sha256:" + "1" * 64,
        "science": {
            "capability_steps": [
                {"step_index": 0, "capability": "geometry_optimization", "version": "1.0.0"}
            ],
            "initial_inputs": [
                {"input_id": "in-1", "sha256": "sha256:" + "2" * 64, "format": "xyz"}
            ],
            "method": "r2SCAN-3c",
            "mapping": "identity",
            "charge": 0,
            "multiplicity": 1,
            "allowed_substitutions": [],
        },
        "budget": {
            "model_requests": 1,
            "input_tokens": 1000,
            "output_tokens": 500,
            "task_refs": 1,
            "recipe_steps": 1,
            "qc_invocations": 1,
            "wall_seconds": 600,
            "queue_seconds": 60,
        },
    }


# ---------------------------------------------------------------------------
# policy resolution and the frozen matrix
# ---------------------------------------------------------------------------


def test_policy_matrix_is_frozen_and_profile_derived() -> None:
    rows = {row["profile"]: row for row in APPROVAL_POLICY_MATRIX}
    assert rows["agent"]["decide"] == "denied"
    assert rows["agent"]["rule"] == "agent_cannot_decide_approval"
    assert rows["manual"]["separation_of_duties"] is True
    assert rows["strict"]["separation_of_duties"] is True
    assert rows["local-single"]["separation_of_duties"] is False

    # The profile comes from the same resolver the submit path uses.
    assert resolve_approval_policy(_agent(), environ={}).name == "agent"
    assert (
        resolve_approval_policy(_keyed(), environ={}).name
        == resolve_approval_policy(_keyed(), environ={SUBMIT_POLICY_ENV: "manual"}).name
    )
    assert resolve_approval_policy(_local(), environ={}).name == "local-single"
    # A keyed credential is never widened into the local single path.
    widened = resolve_approval_policy(_keyed(), environ={SUBMIT_POLICY_ENV: "local-single"})
    assert widened.separation_of_duties is True


def test_agent_credential_cannot_decide_even_with_project_control() -> None:
    policy = resolve_approval_policy(_agent(), environ={})
    verdict = authorize_decision(
        policy,
        action="approve",
        approver_principal="principal_agent",
        requester_principal="principal_human",
    )
    assert verdict.allowed is False
    assert verdict.rule == "agent_cannot_decide_approval"
    assert verdict.http_status == 403
    assert verdict.detail()["code"] == "access_denied"
    # A deployment-wide agent profile refuses even an operator-shaped entry.
    deployed = resolve_approval_policy(_local(), environ={SUBMIT_POLICY_ENV: "agent"})
    assert (
        authorize_decision(
            deployed,
            action="reject",
            approver_principal="principal_local",
            requester_principal="principal_local",
        ).rule
        == "agent_cannot_decide_approval"
    )


def test_service_subject_never_signs_a_scientific_approval() -> None:
    service = CommandIdentity(
        principal_id="principal_worker",
        key_id="key_worker",
        actions=("read", "write"),
        subject_kind=SubjectKind.SERVICE,
        entry_class=EntryClass.HTTP,
        permission_scope=PermissionScope.API_KEY,
    )
    policy = resolve_approval_policy(service, environ={})
    assert policy.decisions_allowed is False
    verdict = authorize_decision(
        policy,
        action="approve",
        approver_principal="principal_worker",
        requester_principal="principal_human",
    )
    assert verdict.rule == "service_credential_cannot_decide_approval"


def test_separation_of_duties_is_a_profile_answer() -> None:
    # local-single: the single operator may self-approve.
    local = resolve_approval_policy(_local(), environ={})
    assert local.self_approval_allowed is True
    assert (
        authorize_decision(
            local,
            action="approve",
            approver_principal="principal_local",
            requester_principal="principal_local",
        ).allowed
        is True
    )
    # manual (keyed): requester and approver must differ.
    manual = resolve_approval_policy(_keyed(), environ={})
    same = authorize_decision(
        manual,
        action="approve",
        approver_principal="principal_user",
        requester_principal="principal_user",
    )
    assert same.allowed is False
    assert same.rule == "requester_approver_separation_required"
    other = authorize_decision(
        manual,
        action="approve",
        approver_principal="principal_reviewer",
        requester_principal="principal_user",
    )
    assert other.allowed is True


def test_payload_approver_fields_are_one_definition() -> None:
    assert "principal_id" in APPROVER_PAYLOAD_FIELDS
    assert payload_approver_fields({"approver": "alice", "role": "admin", "workflow": "x"}) == (
        "approver",
        "role",
    )
    assert payload_approver_fields({"approver": "", "roles": []}) == ()
    # The HTTP router imports the same tuple instead of a private copy.
    from acp.api import v2_approvals

    assert v2_approvals._FORBIDDEN_IDENTITY_FIELDS is APPROVER_PAYLOAD_FIELDS


# ---------------------------------------------------------------------------
# service-level: the additive science projection (double digest)
# ---------------------------------------------------------------------------


def test_approval_additively_stores_the_science_projection(tmp_path: Path) -> None:
    service = ApprovalService(tmp_path / "jobs.db")
    proposal = _proposal()
    digest = proposal_plan_definition_digest(proposal).digest
    approval, _ = service.request(
        proposal_ref="prop-m203",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project_alpha",
        requester_principal="principal_user",
        science_projection=proposal,
    )
    assert approval.science_digest == digest
    assert approval.science_projection
    # The projection is the C plan definition, not the whole Proposal document.
    assert set(approval.science_projection) == {"recipe_digest", "science", "budget"}
    assert "proposal_id" not in approval.science_projection
    assert approval.binding()["science_projection_digest"] == digest
    assert approval.to_dict()["science_projection"] == approval.science_projection

    # Durable: a second service instance reads the same projection.
    reloaded = ApprovalService(tmp_path / "jobs.db").get(approval.approval_id)
    assert reloaded is not None
    assert reloaded.science_projection == approval.science_projection
    assert reloaded.science_digest == digest
    assert reloaded.binding_status == "full"


def test_projection_that_does_not_reproduce_its_digest_is_refused(tmp_path: Path) -> None:
    service = ApprovalService(tmp_path / "jobs.db")
    with pytest.raises(ApprovalValidationError) as excinfo:
        service.request(
            proposal_ref="prop-mismatch",
            proposal_digest=PROPOSAL_DIGEST,
            plan_digest=PLAN_DIGEST,
            project_id="project_alpha",
            requester_principal="principal_user",
            science_digest="sha256:" + "c" * 64,
            science_projection=_proposal(),
        )
    assert excinfo.value.rule == "science_digest_mismatch"
    assert service.query(project_id="project_alpha") == []


# ---------------------------------------------------------------------------
# HTTP entry
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from acp.api.server import create_app

    monkeypatch.delenv(SUBMIT_POLICY_ENV, raising=False)
    app = create_app(run_root=tmp_path / "run-root", max_running=1)
    with TestClient(app) as test_client:
        yield test_client


def _http_plan() -> dict[str, Any]:
    """The frozen execution plan body (the route digests it verbatim)."""
    return frozen_plan_definition(workflow="fake", input={"source": "C"})


def _http_plan_digest() -> str:
    return plan_digest(frozen_plan_definition(workflow="fake", input={"source": "C"})).digest


def _semantic_proposal(project: str, proposal_ref: str) -> dict[str, Any]:
    """The platform Proposal document (B16 semantic projection fields)."""
    return {
        "proposal_id": proposal_ref,
        "project_id": project,
        "title": "M2-03a approval",
        "question": "Does the approval bind the science?",
        "capability_ids": ["singlepoint.run"],
        "recipe_ref": {
            "schema_id": "acp2.science.recipe.v1",
            "version": "1.0.0",
            "digest": "sha256:" + "1" * 64,
        },
        "plan_digest": _http_plan_digest(),
        "budget_request": {"value": "60", "unit": "second"},
    }


def _request_approval(client: Any, *, proposal_ref: str = "prop-http-m203", **extra: Any):
    manager = client.app.state.job_manager
    project = str(manager.default_project_id)
    body = {
        "proposal_ref": proposal_ref,
        "proposal": _semantic_proposal(project, proposal_ref),
        # The C plan definition the science digest signs (M2-03a additive body).
        "science_projection": _proposal(project),
        "plan": _http_plan(),
        "project_id": project,
        "budget": {"unit": "task_count", "count": 1, "timeout_s": 600},
        **extra,
    }
    return client.post("/api/v2/research/approvals", json=body)


def test_http_payload_approver_is_refused_with_the_policy_rule(client) -> None:
    response = client.post(
        "/api/v2/research/approvals",
        json={
            "proposal_ref": "prop-payload-approver",
            "proposal_digest": PROPOSAL_DIGEST,
            "plan_digest": PLAN_DIGEST,
            "approver": "principal_someone_else",
        },
    )
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["params"]["rule"] == "approver_identity_from_payload"
    assert detail["params"]["fields"] == ["approver"]


def test_http_approval_stores_the_projection_of_a_c_shaped_proposal(client) -> None:
    requested = _request_approval(client)
    assert requested.status_code == 201, requested.text
    body = requested.json()
    assert body["science_projection"]
    assert set(body["science_projection"]) == {"recipe_digest", "science", "budget"}
    project = str(client.app.state.job_manager.default_project_id)
    expected = proposal_plan_definition_digest(
        {**_proposal(project), "proposal_id": "prop-http-m203"}
    ).digest
    assert body["science_digest"] == expected


def test_http_agent_credential_cannot_approve(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """A model credential holds project control but still cannot decide."""
    requested = _request_approval(client, proposal_ref="prop-agent-decide")
    assert requested.status_code == 201, requested.text
    approval_ref = requested.json()["approval_ref"]
    revision = requested.json()["revision"]

    # Deployment-wide agent profile: every entry is an agent credential, so the
    # policy refuses the decision before the approval service is reached.
    monkeypatch.setenv(SUBMIT_POLICY_ENV, "agent")
    refused = client.post(
        f"/api/v2/research/approvals/{approval_ref}/approve",
        json={"expected_revision": revision},
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"]["params"]["rule"] == "agent_cannot_decide_approval"
    still_pending = client.get(f"/api/v2/research/approvals/{approval_ref}").json()
    assert still_pending["status"] == "pending"


def test_http_separated_approver_is_allowed_and_self_approval_refused(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The separation rule is server policy: it never depends on the UI."""
    from acp_services.runtime.approval_policy import authorize_decision

    requested = _request_approval(client, proposal_ref="prop-separation")
    assert requested.status_code == 201, requested.text
    record = requested.json()

    # Under the `manual` profile the requester may not sign its own approval.
    monkeypatch.setenv(SUBMIT_POLICY_ENV, "manual")
    self_decision = client.post(
        f"/api/v2/research/approvals/{record['approval_ref']}/approve",
        json={"expected_revision": record["revision"]},
    )
    assert self_decision.status_code == 403, self_decision.text
    assert (
        self_decision.json()["detail"]["params"]["rule"] == "requester_approver_separation_required"
    )

    # A different verified principal is allowed at the policy layer.  The HTTP
    # route's project-control gate still applies (the local Controller owner is
    # not a second principal in local-single mode), so the policy verdict is
    # asserted directly for the separated approver.
    policy = resolve_approval_policy(_keyed("principal_reviewer", "key_reviewer"), environ={})
    verdict = authorize_decision(
        policy,
        action="approve",
        approver_principal="principal_reviewer",
        requester_principal=str(record["requester_ref"]),
    )
    assert verdict.allowed is True
    assert verdict.rule == "decision_allowed"


def test_expired_and_revoked_approvals_cannot_authorize(tmp_path: Path) -> None:
    service = ApprovalService(tmp_path / "jobs.db")
    expired, _ = service.request(
        proposal_ref="prop-expired",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project_alpha",
        requester_principal="principal_user",
        expires_at="2000-01-01T00:00:00Z",
    )
    with pytest.raises(ApprovalDeniedError) as expired_error:
        service.authorize_submission(
            plan_digest=PLAN_DIGEST,
            proposal_ref="prop-expired",
            project_id="project_alpha",
            requester_principal="principal_user",
        )
    assert expired_error.value.rule == "approval_expired"

    revoked, _ = service.request(
        proposal_ref="prop-revoked",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project_alpha",
        requester_principal="principal_user",
    )
    approved, _ = service.approve(
        revoked.approval_id, approver_principal="principal_user", expected_revision=revoked.revision
    )
    service.revoke(
        approved.approval_id,
        actor_principal="principal_user",
        expected_revision=approved.revision,
    )
    with pytest.raises(ApprovalDeniedError) as revoked_error:
        service.authorize_submission(
            plan_digest=PLAN_DIGEST,
            proposal_ref="prop-revoked",
            project_id="project_alpha",
            requester_principal="principal_user",
        )
    assert revoked_error.value.rule == "approval_revoked"
    assert expired.approval_id != revoked.approval_id


def test_cross_identity_approval_cannot_be_reused(tmp_path: Path) -> None:
    service = ApprovalService(tmp_path / "jobs.db")
    pending, _ = service.request(
        proposal_ref="prop-cross",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project_alpha",
        requester_principal="principal_user",
    )
    service.approve(
        pending.approval_id,
        approver_principal="principal_user",
        expected_revision=pending.revision,
    )
    with pytest.raises(ApprovalDeniedError) as denied:
        service.authorize_submission(
            plan_digest=PLAN_DIGEST,
            proposal_ref="prop-cross",
            project_id="project_alpha",
            requester_principal="principal_other",
        )
    assert denied.value.rule == "approval_principal_mismatch"


def test_p2_decision_surface_cannot_bypass_the_policy(tmp_path: Path) -> None:
    """The second decision entry (P2 operations) refuses an agent credential."""
    from acp_services.data.research_resources import ResearchResourceStore
    from acp_services.runtime.research_service import (
        ResearchOperationDeniedError,
        ResearchOperations,
    )
    from acp_services.runtime.research_writer import ResearchWriter

    resources = ResearchResourceStore(tmp_path / "acp_research.db")
    approvals = ApprovalService(tmp_path / "acp_jobs.db")
    operations = ResearchOperations(
        writer=ResearchWriter(resources=resources, approvals=approvals),
        resources=resources,
        approvals=approvals,
    )
    pending, _ = approvals.request(
        proposal_ref="prop-p2-bypass",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project_alpha",
        requester_principal="principal_human",
    )
    agent_command = CommandIdentity(
        principal_id="principal_agent",
        key_id="key_agent",
        subject_kind=SubjectKind.SERVICE,
        permission_scope="agent",
        project_id="project_alpha",
    )
    with pytest.raises(ResearchOperationDeniedError) as excinfo:
        operations.approval_decide(
            command=agent_command,
            approval_ref=pending.approval_id,
            decision="approve",
            expected_revision=pending.revision,
        )
    assert excinfo.value.rule == "agent_cannot_decide_approval"
    assert excinfo.value.http_status == 403
    with pytest.raises(ResearchOperationDeniedError) as revoked:
        operations.approval_revoke(
            command=agent_command,
            approval_ref=pending.approval_id,
            expected_revision=pending.revision,
        )
    assert revoked.value.rule == "agent_cannot_decide_approval"
    still_pending = approvals.get(pending.approval_id)
    assert still_pending is not None and still_pending.state == "pending"


def test_plan_and_science_digest_are_bound_independently(tmp_path: Path) -> None:
    """Neither digest may stand in for the other (D06 + M2-03a)."""
    service = ApprovalService(tmp_path / "jobs.db")
    proposal = _proposal()
    science_digest = science_projection_digest(proposal)
    pending, _ = service.request(
        proposal_ref="prop-dual",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=PLAN_DIGEST,
        project_id="project_alpha",
        requester_principal="principal_user",
        science_projection=proposal,
        science_digest=science_digest,
    )
    approved, _ = service.approve(
        pending.approval_id,
        approver_principal="principal_user",
        expected_revision=pending.revision,
    )
    approval = service.authorize_submission(
        plan_digest=PLAN_DIGEST,
        proposal_ref="prop-dual",
        project_id="project_alpha",
        requester_principal="principal_user",
    )
    assert approval.binding()["plan_digest"] == PLAN_DIGEST
    assert approval.binding()["science_digest"] == science_digest
    other_plan, _ = service.request(
        proposal_ref="prop-dual-plan",
        proposal_digest=PROPOSAL_DIGEST,
        plan_digest=plan_digest(
            frozen_plan_definition(workflow="fake", input={"source": "N"})
        ).digest,
        project_id="project_alpha",
        requester_principal="principal_user",
        science_projection=proposal,
    )
    assert other_plan.science_digest == science_digest  # same science, different plan
    assert other_plan.plan_digest != approved.plan_digest
