"""M1-04b: one SubmitPolicy/classify_submit decision point.

The pre-M1-04 Controller had two research-launch field sets
(``_is_research_launch`` vs ``_enforce_approval``).  These tests freeze the
single matrix:

* ``local-single`` / ``manual-legacy`` legacy submits are explicitly allowed and
  recorded (never denied by accident);
* an agent-scoped credential can never downgrade: omit-all, budget-only and
  proposal-less submits are denials with a typed rule;
* a research submit needs a proposal reference, the execution plan digest and a
  server approval that binds **both** digests;
* the policy profile comes from the server (deployment profile / credential
  scope), never from a payload field, and no ``PrincipalKind.AGENT`` is added.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acp_services.runtime.submit_policy import (
    SUBMIT_CLASS_AGENT,
    SUBMIT_CLASS_LOCAL_SINGLE,
    SUBMIT_CLASS_MANUAL_LEGACY,
    SUBMIT_CLASS_RESEARCH,
    SUBMIT_MATRIX,
    SUBMIT_POLICY_ENV,
    SubmitProbe,
    classify_submit,
    resolve_policy_profile,
)
from acp_services.workspace.command_identity import (
    CommandIdentity,
    EntryClass,
    PermissionScope,
    SubjectKind,
)

_PLAN = "sha256:" + "b" * 64
_SCIENCE = "sha256:" + "c" * 64


def _local() -> CommandIdentity:
    return CommandIdentity(
        principal_id="principal_local",
        subject_kind=SubjectKind.OPERATOR,
        entry_class=EntryClass.HTTP,
        permission_scope=PermissionScope.LOCAL_SINGLE,
    )


def _keyed() -> CommandIdentity:
    return CommandIdentity(
        principal_id="principal_user",
        key_id="key_alpha",
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


class _Approval:
    def __init__(self, science_digest: str = "") -> None:
        self.science_digest = science_digest
        self.approval_id = "ap_test"


# ---------------------------------------------------------------------------
# profile resolution (server side only)
# ---------------------------------------------------------------------------


def test_local_operator_profile_is_local_single() -> None:
    profile = resolve_policy_profile(_local(), environ={})
    assert profile.name == "local-single"
    assert profile.source == "local-operator"
    assert profile.agent is False


def test_keyed_credential_is_never_local_single() -> None:
    profile = resolve_policy_profile(_keyed(), environ={})
    assert profile.name == "manual"
    assert profile.keyed is True
    # Even an explicit local-single deployment profile must not widen a key.
    widened = resolve_policy_profile(_keyed(), environ={SUBMIT_POLICY_ENV: "local-single"})
    assert widened.name == "manual"
    assert widened.keyed is True


def test_agent_profile_comes_from_credential_scope_or_deployment() -> None:
    assert resolve_policy_profile(_agent(), environ={}).name == "agent"
    assert resolve_policy_profile(_agent(), environ={}).source == "credential-scope"
    # A deployment-wide agent profile applies to every non-operator entry.
    assert resolve_policy_profile(_local(), environ={SUBMIT_POLICY_ENV: "agent"}).name == "agent"
    assert resolve_policy_profile(_local(), environ={SUBMIT_POLICY_ENV: "strict"}).strict


def test_payload_cannot_select_the_profile() -> None:
    # The probe carries request fields only; the profile signature has no payload
    # parameter, so a wire field can never become a policy input.
    import inspect

    parameters = inspect.signature(resolve_policy_profile).parameters
    assert "identity" in parameters
    assert not any(name in parameters for name in ("payload", "body", "profile_hint"))


# ---------------------------------------------------------------------------
# legacy submits are allowed and recorded
# ---------------------------------------------------------------------------


def test_local_single_legacy_submit_is_allowed_and_classified() -> None:
    decision = classify_submit(SubmitProbe(workflow="optimize"), _local(), environ={})
    assert decision.allowed is True
    assert decision.submit_class == SUBMIT_CLASS_LOCAL_SINGLE
    assert decision.approval_required is False
    assert decision.to_dict()["submit_class"] == SUBMIT_CLASS_LOCAL_SINGLE


def test_keyed_legacy_submit_is_manual_legacy() -> None:
    decision = classify_submit(SubmitProbe(workflow="optimize"), _keyed(), environ={})
    assert decision.allowed is True
    assert decision.submit_class == SUBMIT_CLASS_MANUAL_LEGACY


def test_strict_profile_refuses_a_legacy_submit() -> None:
    identity = _keyed()
    decision = classify_submit(
        SubmitProbe(workflow="optimize"), identity, environ={SUBMIT_POLICY_ENV: "strict"}
    )
    assert decision.allowed is False
    assert decision.rule == "submit_requires_research_fields"
    assert decision.http_status == 403


# ---------------------------------------------------------------------------
# agent credentials never downgrade
# ---------------------------------------------------------------------------


def test_agent_omit_all_is_denied_not_downgraded() -> None:
    decision = classify_submit(SubmitProbe(workflow="optimize"), _agent(), environ={})
    assert decision.allowed is False
    assert decision.submit_class == SUBMIT_CLASS_AGENT
    assert decision.approval_required is True
    assert decision.rule == "agent_proposal_required"
    assert decision.http_status == 403


def test_agent_budget_only_is_denied() -> None:
    probe = SubmitProbe(workflow="optimize", has_budget=True)
    decision = classify_submit(probe, _agent(), environ={})
    assert decision.allowed is False
    assert decision.rule == "agent_budget_only_cannot_downgrade"
    assert decision.detail()["code"] == "access_denied"


def test_agent_without_proposal_is_denied() -> None:
    probe = SubmitProbe(workflow="optimize", has_plan_digest=True)
    decision = classify_submit(probe, _agent(), environ={})
    assert decision.allowed is False
    assert decision.rule == "agent_proposal_required"


def test_agent_complete_research_still_needs_an_approval() -> None:
    probe = SubmitProbe(
        workflow="optimize", has_proposal_ref=True, has_plan_digest=True, has_budget=True
    )
    denied = classify_submit(probe, _agent(), environ={})
    assert denied.allowed is False
    assert denied.rule == "approval_required"
    allowed = classify_submit(probe, _agent(), environ={}, approval=_Approval())
    assert allowed.allowed is True
    assert allowed.submit_class == SUBMIT_CLASS_RESEARCH


# ---------------------------------------------------------------------------
# research submits
# ---------------------------------------------------------------------------


def test_budget_only_submit_is_not_silently_dropped() -> None:
    probe = SubmitProbe(workflow="optimize", has_budget=True)
    decision = classify_submit(probe, _local(), environ={})
    assert decision.allowed is False
    assert decision.rule == "approval_required"
    assert decision.research is True
    assert "silently ignored" in decision.message


def test_plan_without_proposal_requires_an_approval() -> None:
    probe = SubmitProbe(workflow="optimize", has_plan_digest=True)
    decision = classify_submit(probe, _local(), environ={})
    assert decision.allowed is False
    assert decision.rule == "approval_required"
    assert decision.http_status == 403


def test_proposal_without_plan_is_a_plan_digest_error() -> None:
    probe = SubmitProbe(workflow="optimize", has_proposal_ref=True)
    decision = classify_submit(probe, _local(), environ={})
    assert decision.allowed is False
    assert decision.rule == "plan_digest_required"
    assert decision.http_status == 400


def test_complete_research_requires_a_bound_approval() -> None:
    probe = SubmitProbe(
        workflow="optimize", has_proposal_ref=True, has_plan_digest=True, has_budget=True
    )
    assert classify_submit(probe, _local(), environ={}).rule == "approval_required"
    approved = classify_submit(probe, _local(), environ={}, approval=_Approval())
    assert approved.allowed is True
    assert approved.research is True


def test_science_digest_mismatch_and_unverifiable_are_denied() -> None:
    probe = SubmitProbe(workflow="optimize", has_proposal_ref=True, has_plan_digest=True)
    approval = _Approval(science_digest=_SCIENCE)
    mismatch = classify_submit(
        probe,
        _local(),
        environ={},
        approval=approval,
        science_digest="sha256:" + "d" * 64,
        approval_science_digest=approval.science_digest,
    )
    assert mismatch.allowed is False
    assert mismatch.rule == "approval_binding_mismatch"
    unverifiable = classify_submit(
        probe,
        _local(),
        environ={},
        approval=approval,
        science_digest="",
        approval_science_digest=approval.science_digest,
    )
    assert unverifiable.allowed is False
    assert unverifiable.rule == "science_binding_unverifiable"
    matching = classify_submit(
        probe,
        _local(),
        environ={},
        approval=approval,
        science_digest=approval.science_digest,
        approval_science_digest=approval.science_digest,
    )
    assert matching.allowed is True


def test_matrix_rows_are_complete_and_unique() -> None:
    assert len(SUBMIT_MATRIX) >= 7
    rules = {(row["profile"], row["submit_class"], row["approval"]) for row in SUBMIT_MATRIX}
    assert ("agent", SUBMIT_CLASS_AGENT, "denied") in rules
    assert ("local-single", SUBMIT_CLASS_LOCAL_SINGLE, "not_required") in rules
    assert ("manual", SUBMIT_CLASS_MANUAL_LEGACY, "not_required") in rules
    assert ("any", SUBMIT_CLASS_RESEARCH, "required") in rules


# ---------------------------------------------------------------------------
# HTTP entry: the single decision point is what the route runs
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path):
    from fastapi.testclient import TestClient

    from acp.api.server import create_app

    app = create_app(run_root=tmp_path / "run-root", max_running=1)
    with TestClient(app) as test_client:
        yield test_client


def test_http_legacy_submit_records_its_submit_class(client) -> None:
    response = client.post(
        "/api/v1/jobs",
        json={"workflow": "fake", "name": "legacy-submit-class", "input": {"source": "C"}},
    )
    assert response.status_code == 201, response.text
    assert response.json()["submit_class"] == SUBMIT_CLASS_LOCAL_SINGLE


def test_http_budget_only_submit_is_refused_before_dispatch(client) -> None:
    before = len(client.get("/api/v1/jobs?limit=1000").json()["jobs"])
    response = client.post(
        "/api/v1/jobs",
        json={
            "workflow": "fake",
            "name": "budget-only",
            "input": {"source": "C"},
            "budget": {"unit": "task_count", "count": 1, "timeout_s": 60},
        },
    )
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "access_denied"
    assert detail["params"]["rule"] == "approval_required"
    assert len(client.get("/api/v1/jobs?limit=1000").json()["jobs"]) == before


def test_http_research_submit_without_approval_is_refused(client) -> None:
    from acp_services.runtime.canonical_digest import (
        frozen_plan_definition,
        plan_digest,
    )

    workflow_input = {"source": "C"}
    digest = plan_digest(frozen_plan_definition(workflow="fake", input=workflow_input)).digest
    response = client.post(
        "/api/v1/jobs",
        json={
            "workflow": "fake",
            "name": "research-no-approval",
            "input": workflow_input,
            "proposal_ref": "prop-none",
            "plan_digest": digest,
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["params"]["rule"] == "approval_required"


def test_http_budget_object_with_unknown_key_is_not_dropped(client) -> None:
    response = client.post(
        "/api/v1/jobs",
        json={
            "workflow": "fake",
            "name": "budget-unknown",
            "input": {"source": "C"},
            "budget": {"unit": "task_count", "count": 1, "timeout_s": 60, "wall_seconds": 900},
        },
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["params"]["rule"] == "unknown_field"
    assert detail["params"]["field"] == "budget.wall_seconds"


def test_http_science_binding_is_verified_when_the_approval_carries_it(client) -> None:
    """An approval with a science digest is only usable with a verifiable proposal."""
    from acp_services.runtime.approvals import ApprovalService
    from acp_services.runtime.canonical_digest import (
        frozen_plan_definition,
        plan_digest,
    )

    workflow_input = {"source": "C"}
    digest = plan_digest(frozen_plan_definition(workflow="fake", input=workflow_input)).digest
    manager = client.app.state.job_manager
    project = str(manager.default_project_id)
    approvals = ApprovalService(manager.store.db_path)
    pending, _ = approvals.request(
        proposal_ref="prop-science-binding",
        proposal_digest="sha256:" + "a" * 64,
        plan_digest=digest,
        project_id=project,
        requester_principal="principal_local",
        science_digest="sha256:" + "c" * 64,
    )
    approvals.approve(
        pending.approval_id,
        approver_principal="principal_local",
        expected_revision=pending.revision,
    )
    # No proposal document exists for this reference: the science binding cannot
    # be verified, so the submit is refused instead of being trusted.
    response = client.post(
        "/api/v1/jobs",
        json={
            "workflow": "fake",
            "name": "science-unverifiable",
            "input": workflow_input,
            "proposal_ref": "prop-science-binding",
            "plan_digest": digest,
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["params"]["rule"] == "science_binding_unverifiable"


def test_http_science_binding_accepts_a_matching_stored_proposal(client) -> None:
    from acp_services.data.research_resources import (
        PROPOSAL_SCHEMA,
        ResearchResourceRequest,
        ResearchResourceStore,
        research_resource_key,
    )
    from acp_services.runtime.approvals import ApprovalService
    from acp_services.runtime.canonical_digest import (
        frozen_plan_definition,
        plan_digest,
        proposal_plan_definition_digest,
    )

    workflow_input = {"source": "C"}
    digest = plan_digest(frozen_plan_definition(workflow="fake", input=workflow_input)).digest
    manager = client.app.state.job_manager
    project = str(manager.default_project_id)
    proposal = {
        "proposal_id": "prop-science-stored",
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
            "charge": -1,
            "multiplicity": 2,
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
    science_digest = proposal_plan_definition_digest(proposal).digest
    store: ResearchResourceStore = client.app.state.research_resources
    store.create(
        ResearchResourceRequest(
            resource_id=research_resource_key(project, "proposal", "prop-science-stored"),
            schema=PROPOSAL_SCHEMA,
            document=proposal,
            principal_id="principal_local",
            project_id=project,
        )
    )
    approvals = ApprovalService(manager.store.db_path)
    pending, _ = approvals.request(
        proposal_ref="prop-science-stored",
        proposal_digest="sha256:" + "a" * 64,
        plan_digest=digest,
        project_id=project,
        requester_principal="principal_local",
        science_digest=science_digest,
    )
    approvals.approve(
        pending.approval_id,
        approver_principal="principal_local",
        expected_revision=pending.revision,
    )
    response = client.post(
        "/api/v1/jobs",
        json={
            "workflow": "fake",
            "name": "science-bound",
            "input": workflow_input,
            "proposal_ref": "prop-science-stored",
            "plan_digest": digest,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["submit_class"] == SUBMIT_CLASS_RESEARCH


def test_http_research_submit_clarifies_incomplete_stored_science(client) -> None:
    """A stored proposal without charge/multiplicity can never be submitted."""
    from acp_services.data.research_resources import (
        PROPOSAL_SCHEMA,
        ResearchResourceRequest,
        research_resource_key,
    )
    from acp_services.runtime.approvals import ApprovalService
    from acp_services.runtime.canonical_digest import (
        frozen_plan_definition,
        plan_digest,
    )

    workflow_input = {"source": "C"}
    digest = plan_digest(frozen_plan_definition(workflow="fake", input=workflow_input)).digest
    manager = client.app.state.job_manager
    project = str(manager.default_project_id)
    proposal = {
        "proposal_id": "prop-incomplete-science",
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
    client.app.state.research_resources.create(
        ResearchResourceRequest(
            resource_id=research_resource_key(project, "proposal", "prop-incomplete-science"),
            schema=PROPOSAL_SCHEMA,
            document=proposal,
            principal_id="principal_local",
            project_id=project,
        )
    )
    approvals = ApprovalService(manager.store.db_path)
    pending, _ = approvals.request(
        proposal_ref="prop-incomplete-science",
        proposal_digest="sha256:" + "a" * 64,
        plan_digest=digest,
        project_id=project,
        requester_principal="principal_local",
        science_digest="sha256:" + "c" * 64,
    )
    approvals.approve(
        pending.approval_id,
        approver_principal="principal_local",
        expected_revision=pending.revision,
    )
    response = client.post(
        "/api/v1/jobs",
        json={
            "workflow": "fake",
            "name": "incomplete-science",
            "input": workflow_input,
            "proposal_ref": "prop-incomplete-science",
            "plan_digest": digest,
        },
    )
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["params"]["rule"] == "clarification_required"
    assert detail["params"]["fields"] == ["charge", "multiplicity"]
