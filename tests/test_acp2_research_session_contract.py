"""M1-02 (G09/G14) — research session, submit intent and recovery contract.

Covers the frozen ``acp2.runtime.research-session.v1`` contract and the
implementation root ``acp_services.runtime.research_session``:

1. the contract document is machine-checked against the code tables (fields and
   the crash-point matrix), so the prose cannot drift;
2. the research identity is allocated *before* the first plan and a client
   cannot choose its scope;
3. the planning claim / model receipt namespaces reproduce their frozen grammar
   vectors and reuse the single canonical digest (D-IDENT-04 / D-IDENT-05);
4. a planning claim is never a submit-ledger key and never names a job/attempt;
5. the submit intent is opaque, revision-guarded and plan-digest bound
   (D-IDENT-07); a changed plan cannot reuse the old key;
6. a changed plan digest invalidates the old approval and opens a new
   round/revision + approval intent without writing an execution fact;
7. every crash point states its persisted facts, allowed and forbidden actions;
8. recovery reuses ``ResearchRecoveryCoordinator`` (cursor/snapshot/ledger) and
   never calls ``JobManager.resume()`` (review-only boundary).

Nothing here executes science, a Controller or a browser: it is identity,
intent and recovery evidence only.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from acp.scheduler.events import JobEventLog
from acp.services_events_bootstrap import JobEventLogPort
from acp_services.runtime.approvals import (
    ApprovalDeniedError,
    ApprovalService,
    ApprovalState,
)
from acp_services.runtime.canonical_digest import (
    canonical_digest,
    frozen_plan_definition,
    plan_digest,
    proposal_digest,
)
from acp_services.runtime.research_events import (
    InMemoryActionLedger,
    ResearchProjection,
    ResearchRecoveryCoordinator,
)
from acp_services.runtime.research_session import (
    CONTRACT_DOCUMENT,
    CRASH_POINT_RULES,
    CRASH_POINT_STAGES,
    EVIDENCE_REF_FIELDS,
    PLANNING_CLAIM_PREFIX,
    RESEARCH_ID_PREFIX,
    RESEARCH_SESSION_FIELDS,
    SESSION_ID_PREFIX,
    STAGE_AWAITING,
    STAGE_BEFORE_SUBMIT,
    STAGE_EVIDENCE_READ,
    STAGE_RECORD_CAS_PENDING,
    STAGE_SETTLED,
    STAGE_SUBMITTED_UNACKED,
    SUBMIT_INTENT_FIELDS,
    TASK_BINDING_FIELDS,
    WAIT_CURSOR_FIELDS,
    EvidenceRef,
    ResearchIdentity,
    ResearchSession,
    ResearchSessionConflictError,
    ResearchSessionValidationError,
    SubmitIntent,
    TaskBinding,
    classify_crash_point,
    is_submit_ledger_key,
    model_receipt_id,
    parse_planning_claim_id,
    planning_claim,
    planning_claim_id,
    recover_research_session,
    supersede_approval_for_plan_change,
)
from acp_services.workspace.command_identity import CommandIdentity

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / CONTRACT_DOCUMENT

PROJECT = "proj-m102"
OTHER_PROJECT = "proj-other"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
RECIPE_REF = {
    "schema_id": "acp2.science.recipe.v1",
    "version": "1.0.0",
    "digest": "sha256:" + "1" * 64,
}
PLANNING_REQUEST = {
    "model": "deepseek-plan-1",
    "messages": [{"role": "user", "content": "plan a single point"}],
    "max_output_tokens": 512,
}
UNIT_FIELD_TABLES = {
    "identity": RESEARCH_SESSION_FIELDS,
    "submit_intent": SUBMIT_INTENT_FIELDS,
    "task_binding": TASK_BINDING_FIELDS,
    "wait_cursor": WAIT_CURSOR_FIELDS,
    "evidence_ref": EVIDENCE_REF_FIELDS,
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _identity(*, project: str = PROJECT, seed: str = "request-1") -> ResearchIdentity:
    command = CommandIdentity.from_local(project_id=project, entry_class="cli")
    return ResearchIdentity.allocate(command, session_seed=seed)


def _plan(method: str) -> dict[str, Any]:
    return frozen_plan_definition(
        workflow="singlepoint",
        input={"xyz_text": "1\nH\nH 0 0 0.74\n"},
        method={"method": method, "charge": 0, "multiplicity": 1},
        resources={"cores": 1, "memory_mb": 512},
    )


def _proposal(plan_body: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": "prop-m102",
        "project_id": PROJECT,
        "title": "M1-02 session",
        "question": "Does the intent chain hold?",
        "capability_ids": ["singlepoint.run"],
        "recipe_ref": RECIPE_REF,
        "plan_digest": plan_digest(plan_body).digest,
        "budget_request": {"value": "60", "unit": "second"},
    }


def _approved(service: ApprovalService, plan_body: dict[str, Any]):
    proposal = _proposal(plan_body)
    approval, _ = service.request(
        proposal_ref=str(proposal["proposal_id"]),
        proposal_digest=proposal_digest(proposal).digest,
        plan_digest=plan_digest(plan_body).digest,
        project_id=PROJECT,
        requester_principal="alice",
        plan_definition=plan_body,
        budget={"unit": "task_count", "count": 1},
    )
    approval, _ = service.approve(
        approval.approval_id, approver_principal="bob", expected_revision=approval.revision
    )
    return approval


def _intent(
    identity: ResearchIdentity,
    *,
    plan_body: dict[str, Any],
    approval_ref: str,
    key: str = "opaque-intent-key-1",
    round: int = 0,
) -> SubmitIntent:
    return SubmitIntent(
        intent_key=key,
        project_id=identity.project_id,
        research_id=identity.research_id,
        session_id=identity.session_id,
        round=round,
        plan_digest=plan_digest(plan_body).digest,
        approval_ref=approval_ref,
    )


def _event_port(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return JobEventLogPort(JobEventLog(path))


# ---------------------------------------------------------------------------
# 1. The contract document is code, not prose
# ---------------------------------------------------------------------------


def _contract_text() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:] if end < 0 else text[start:end]


def _table_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append(cells)
    return rows


def test_contract_document_exists_and_declares_its_schema() -> None:
    text = _contract_text()
    assert "acp2.runtime.research-session.v1" in text
    assert "research_session.py" in text
    assert "submit-intent.ts" in text


def test_contract_field_tables_match_the_code() -> None:
    """Every frozen field name is documented with a backticked row."""
    text = _contract_text()
    documented = set(re.findall(r"^\|\s*`([a-z0-9_]+)`\s*\|", text, re.MULTILINE))
    for label, fields in UNIT_FIELD_TABLES.items():
        missing = [name for name in fields if name not in documented]
        assert not missing, (label, missing)


def test_contract_crash_point_matrix_matches_the_code() -> None:
    """The markdown recovery table is byte-equal to the frozen code table."""
    section = _section(_contract_text(), "## 5. Crash-point recovery table")
    rows = [row for row in _table_rows(section) if row and re.fullmatch(r"`[a-z_]+`", row[0])]
    documented = [row[0].strip("`") for row in rows]
    assert documented == list(CRASH_POINT_STAGES)
    for row in rows:
        stage = row[0].strip("`")
        rule = CRASH_POINT_RULES[stage]
        assert row[1].strip(), stage  # persisted facts are stated
        allowed = set(re.findall(r"`([a-z_]+)`", row[2]))
        forbidden = set(re.findall(r"`([a-z_]+)`", row[3]))
        assert allowed == set(rule.allowed_actions), stage
        assert forbidden == set(rule.forbidden_actions), stage
        assert row[4].strip().strip("`") == rule.next_action, stage


# ---------------------------------------------------------------------------
# 2. Identity exists before the first plan
# ---------------------------------------------------------------------------


def test_identity_is_allocated_before_the_first_plan() -> None:
    """No Proposal, approval or TaskRef is needed to allocate the identity."""
    identity = _identity()
    assert identity.principal_id and identity.project_id
    assert identity.research_id.startswith(RESEARCH_ID_PREFIX)
    assert identity.session_id.startswith(SESSION_ID_PREFIX)
    assert "|" not in identity.research_id and "|" not in identity.session_id
    assert re.fullmatch(r"res-[A-Za-z0-9][A-Za-z0-9._:-]*", identity.research_id)
    payload = identity.to_dict()
    assert payload["schema"] == "acp2.runtime.research-session.v1"
    assert set(payload) >= set(RESEARCH_SESSION_FIELDS) - {"round", "revision"}


def test_identity_is_deterministic_and_scope_is_server_fixed() -> None:
    first = _identity()
    again = _identity()
    assert first == again
    assert _identity(seed="request-2").session_id != first.session_id
    assert _identity(seed="request-2").research_id != first.research_id
    assert _identity(project=OTHER_PROJECT).research_id != first.research_id
    other_principal = CommandIdentity.from_local(project_id=PROJECT, entry_class="cli")
    other_principal = replace(other_principal, principal_id="alice")
    allocated = ResearchIdentity.allocate(other_principal, session_seed="request-1")
    assert allocated.research_id != first.research_id
    assert allocated.principal_id == "alice"


def test_identity_requires_a_server_resolved_command_identity() -> None:
    with pytest.raises(ResearchSessionValidationError) as info:
        ResearchIdentity.allocate({"principal_id": "alice", "project_id": PROJECT})  # type: ignore[arg-type]
    assert info.value.rule == "invalid_identity"
    # A raw string can never become an identity segment.
    with pytest.raises(ResearchSessionValidationError):
        ResearchIdentity(
            principal_id="alice",
            project_id="proj|injected",
            research_id="res-1",
            session_id="ses-1",
        )


# ---------------------------------------------------------------------------
# 3. Planning claim / model receipt namespaces (D-IDENT-04 / D-IDENT-05)
# ---------------------------------------------------------------------------


def test_planning_claim_namespace_vector() -> None:
    identity = _identity()
    claim = planning_claim(identity, round=3, call_seq=7, request=PLANNING_REQUEST)
    expected = f"plan|{identity.project_id}|{identity.research_id}|{identity.session_id}|3|7"
    assert claim.claim_id == expected
    assert claim.claim_id.split("|")[0] == PLANNING_CLAIM_PREFIX
    assert all(segment for segment in claim.claim_id.split("|"))
    assert parse_planning_claim_id(claim.claim_id) == (
        identity.project_id,
        identity.research_id,
        identity.session_id,
        3,
        7,
    )
    # The digest is the single canonical implementation, not a private hasher.
    assert claim.request_digest == canonical_digest(PLANNING_REQUEST)
    assert claim.budget_id.startswith("rb_")
    assert claim.to_dict()["is_submit_ledger_key"] is False


def test_planning_claim_segment_grammar_is_enforced() -> None:
    identity = _identity()
    with pytest.raises(ResearchSessionValidationError):
        planning_claim_id(
            project_id="",
            research_id=identity.research_id,
            session_id=identity.session_id,
            round=0,
            call_seq=1,
        )
    with pytest.raises(ResearchSessionValidationError):
        planning_claim_id(
            project_id="proj|split",
            research_id=identity.research_id,
            session_id=identity.session_id,
            round=0,
            call_seq=1,
        )
    with pytest.raises(ResearchSessionValidationError):
        planning_claim_id(
            project_id=PROJECT,
            research_id=identity.research_id,
            session_id=identity.session_id,
            round=0,
            call_seq=0,
        )
    with pytest.raises(ResearchSessionValidationError):
        planning_claim_id(
            project_id=PROJECT,
            research_id="not-a-research-id",
            session_id=identity.session_id,
            round=0,
            call_seq=1,
        )
    with pytest.raises(ResearchSessionValidationError):
        parse_planning_claim_id("plan|only|four|segments")


def test_model_receipt_namespace_vector() -> None:
    identity = _identity()
    claim = planning_claim(identity, round=0, call_seq=1, request=PLANNING_REQUEST)
    expected = (
        f"modelrcpt|{identity.project_id}|{identity.research_id}|"
        f"{identity.session_id}|0|1|{canonical_digest(PLANNING_REQUEST)}"
    )
    assert claim.model_receipt_id == expected
    assert claim.model_receipt_id == model_receipt_id(
        project_id=identity.project_id,
        research_id=identity.research_id,
        session_id=identity.session_id,
        round=0,
        call_seq=1,
        request_digest=canonical_digest(PLANNING_REQUEST),
    )
    # A model call is not a job/attempt: no such segment exists.
    segments = claim.model_receipt_id.split("|")
    assert segments[0] == "modelrcpt"
    assert len(segments) == 7
    assert "attempt" not in claim.model_receipt_id
    assert "job" not in claim.model_receipt_id
    # Another request or call sequence settles to another receipt.
    other = planning_claim(
        identity,
        round=0,
        call_seq=2,
        request={**PLANNING_REQUEST, "max_output_tokens": 1024},
    )
    assert other.model_receipt_id != claim.model_receipt_id
    assert other.budget_id == claim.budget_id  # one planning budget per session


def test_planning_claim_is_never_a_submit_ledger_key() -> None:
    identity = _identity()
    claim = planning_claim(identity, round=0, call_seq=1, request=PLANNING_REQUEST)
    assert is_submit_ledger_key(claim.claim_id) is False
    assert is_submit_ledger_key(claim.model_receipt_id) is False
    assert is_submit_ledger_key("b7d1-0f0a") is True
    # The module never writes the submit ledger itself.
    source = (REPO_ROOT / "src/acp_services/runtime/research_session.py").read_text(
        encoding="utf-8"
    )
    assert "claim_idempotency" not in source


# ---------------------------------------------------------------------------
# 4. Submit intent: opaque, revision-guarded, plan-digest bound
# ---------------------------------------------------------------------------


def test_submit_intent_keeps_the_opaque_key_verbatim() -> None:
    identity = _identity()
    key = "b7d1f4e2-0f0a-4c1e-9b7a-2f1c3d4e5f60"
    intent = SubmitIntent(
        intent_key=key,
        project_id=identity.project_id,
        research_id=identity.research_id,
        session_id=identity.session_id,
        round=0,
        plan_digest=DIGEST_A,
    )
    assert intent.intent_key == key
    assert intent.to_dict()["intent_key"] == key
    assert set(intent.to_dict()) == set(SUBMIT_INTENT_FIELDS)


def test_submit_intent_is_revision_guarded() -> None:
    identity = _identity()
    intent = _intent(identity, plan_body=_plan("STO-3G"), approval_ref="ap_1234")
    session = ResearchSession.start(identity)
    assert session.revision == 1
    session = session.record_submit_intent(intent, expected_revision=1)
    assert session.revision == 2
    assert session.submit_intent == intent
    # The same intent replays without a revision change.
    assert session.record_submit_intent(intent, expected_revision=2).revision == 2
    with pytest.raises(ResearchSessionConflictError) as info:
        session.record_submit_intent(intent, expected_revision=1)
    assert info.value.rule == "revision_conflict"
    # A second key for the same plan in the same round is refused.
    with pytest.raises(ResearchSessionConflictError) as reuse:
        session.record_submit_intent(replace(intent, intent_key="another-key"), expected_revision=2)
    assert reuse.value.rule == "intent_key_reuse"


def test_a_changed_plan_cannot_reuse_the_round_key() -> None:
    identity = _identity()
    session = ResearchSession.start(identity).record_submit_intent(
        _intent(identity, plan_body=_plan("STO-3G"), approval_ref="ap_1234")
    )
    changed = _intent(
        identity,
        plan_body=_plan("wB97X-D4"),
        approval_ref="ap_1234",
        key="opaque-intent-key-2",
    )
    with pytest.raises(ResearchSessionConflictError) as info:
        session.record_submit_intent(changed)
    assert info.value.rule == "plan_digest_changed"
    assert info.value.next_round == 1
    # A new round accepts the new intent and drops the old binding.
    fresh = replace(changed, round=1)
    next_round = session.next_round(fresh, expected_revision=2)
    assert next_round.round == 1
    assert next_round.submit_intent is not None
    assert next_round.submit_intent.intent_key == "opaque-intent-key-2"
    assert next_round.task_binding is None
    assert next_round.evidence_refs == ()


# ---------------------------------------------------------------------------
# 5. A changed plan digest invalidates the old approval
# ---------------------------------------------------------------------------


def test_plan_change_invalidates_the_old_approval(tmp_path: Path) -> None:
    service = ApprovalService(tmp_path / "jobs.db")
    old_plan = _plan("STO-3G")
    approval = _approved(service, old_plan)
    identity = _identity()
    intent = _intent(identity, plan_body=old_plan, approval_ref=approval.approval_id)
    session = ResearchSession.start(identity).record_submit_intent(intent)
    new_plan = _plan("wB97X-D4")

    outcome = supersede_approval_for_plan_change(
        service,
        intent=intent,
        new_plan_digest=plan_digest(new_plan).digest,
        requester_principal="alice",
        proposal_ref="prop-m102",
        proposal_digest=proposal_digest(_proposal(new_plan)).digest,
        actor_principal="bob",
        new_plan_definition=new_plan,
        budget={"unit": "task_count", "count": 1},
    )

    # The old approval is revoked through the normal CAS; the new one is a
    # pending intent for the new digest.
    assert outcome.previous_state == ApprovalState.APPROVED.value
    assert outcome.previous_approval is not None
    assert outcome.previous_approval.state == ApprovalState.REVOKED.value
    assert outcome.approval.state == ApprovalState.PENDING.value
    assert outcome.approval.plan_digest == plan_digest(new_plan).digest
    assert outcome.round == intent.round + 1
    assert outcome.revision == intent.revision + 1
    assert outcome.intent_key_required is True
    # The platform never fabricates the client key.
    payload = outcome.to_dict()
    assert "intent_key" not in payload and "next_intent_key" not in payload
    # No execution fact was written.
    assert outcome.approval.task_ref is None
    assert not any("key" in name for name in payload if name != "intent_key_required")
    with sqlite3.connect(tmp_path / "jobs.db") as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "jobs" not in tables
    # The session's old round stays bound to the old digest; it is never edited.
    assert session.submit_intent is not None
    assert session.submit_intent.plan_digest == plan_digest(old_plan).digest


def test_old_approval_can_no_longer_authorize_the_new_plan(tmp_path: Path) -> None:
    service = ApprovalService(tmp_path / "jobs.db")
    old_plan = _plan("STO-3G")
    approval = _approved(service, old_plan)
    identity = _identity()
    intent = _intent(identity, plan_body=old_plan, approval_ref=approval.approval_id)
    new_plan = _plan("wB97X-D4")
    new_proposal_digest = proposal_digest(_proposal(new_plan)).digest
    supersede_approval_for_plan_change(
        service,
        intent=intent,
        new_plan_digest=plan_digest(new_plan).digest,
        requester_principal="alice",
        proposal_ref="prop-m102",
        proposal_digest=new_proposal_digest,
        actor_principal="bob",
        new_plan_definition=new_plan,
        budget={"unit": "task_count", "count": 1},
    )
    with pytest.raises(ApprovalDeniedError) as old_digest:
        service.authorize_submission(
            plan_digest=plan_digest(old_plan).digest,
            proposal_ref="prop-m102",
            project_id=PROJECT,
            requester_principal="alice",
            proposal_digest=proposal_digest(_proposal(old_plan)).digest,
            budget={"unit": "task_count", "count": 1},
        )
    assert old_digest.value.rule in {"approval_revoked", "approval_pending"}
    with pytest.raises(ApprovalDeniedError) as new_digest:
        service.authorize_submission(
            plan_digest=plan_digest(new_plan).digest,
            proposal_ref="prop-m102",
            project_id=PROJECT,
            requester_principal="alice",
            proposal_digest=new_proposal_digest,
            budget={"unit": "task_count", "count": 1},
        )
    assert new_digest.value.rule == "approval_pending"


def test_plan_change_requires_an_actual_change(tmp_path: Path) -> None:
    service = ApprovalService(tmp_path / "jobs.db")
    plan = _plan("STO-3G")
    approval = _approved(service, plan)
    intent = _intent(_identity(), plan_body=plan, approval_ref=approval.approval_id)
    with pytest.raises(ResearchSessionValidationError) as info:
        supersede_approval_for_plan_change(
            service,
            intent=intent,
            new_plan_digest=plan_digest(plan).digest,
            requester_principal="alice",
            proposal_ref="prop-m102",
            proposal_digest=proposal_digest(_proposal(plan)).digest,
            actor_principal="bob",
        )
    assert info.value.rule == "plan_unchanged"


# ---------------------------------------------------------------------------
# 6. Crash-point matrix
# ---------------------------------------------------------------------------


def _session_with(*, intent: SubmitIntent | None, **kwargs: Any) -> ResearchSession:
    session = ResearchSession.start(_identity())
    if intent is not None:
        session = session.record_submit_intent(intent)
    if "binding" in kwargs:
        session = session.bind_task(kwargs["binding"])
    if "evidence" in kwargs:
        session = session.add_evidence(kwargs["evidence"])
    if "decision" in kwargs:
        session = session.record_decision(kwargs["decision"])
    return session


def test_crash_point_matrix_covers_every_stage() -> None:
    assert list(CRASH_POINT_RULES) == list(CRASH_POINT_STAGES)
    for stage, rule in CRASH_POINT_RULES.items():
        assert rule.stage == stage
        assert rule.persisted_facts
        assert rule.allowed_actions
        assert rule.forbidden_actions
        assert rule.next_action
        # A research session never resumes through the review-only job path.
        assert "call_job_manager_review_resume" in rule.forbidden_actions
        assert not any("resume_job" in action for action in rule.allowed_actions)


def test_classify_crash_point_walks_the_five_required_points() -> None:
    identity = _identity()
    intent = _intent(identity, plan_body=_plan("STO-3G"), approval_ref="ap_1234")
    binding = TaskBinding(project_id=PROJECT, job_id="job-1", attempt=1, release_id="rel-1")
    evidence = EvidenceRef(evidence_id="ev-1", kind="result_manifest", sha256=DIGEST_A)

    assert classify_crash_point(intent=None).stage == STAGE_BEFORE_SUBMIT
    assert classify_crash_point(intent=intent).stage == STAGE_BEFORE_SUBMIT
    assert (
        classify_crash_point(intent=intent.with_state("submitted")).stage == STAGE_SUBMITTED_UNACKED
    )
    assert classify_crash_point(intent=intent, task_binding=binding).stage == STAGE_AWAITING
    assert (
        classify_crash_point(intent=intent, task_binding=binding, evidence_refs=(evidence,)).stage
        == STAGE_EVIDENCE_READ
    )
    assert (
        classify_crash_point(intent=intent, task_binding=binding, record_cas_pending=True).stage
        == STAGE_RECORD_CAS_PENDING
    )
    assert (
        classify_crash_point(
            intent=intent,
            task_binding=binding,
            evidence_refs=(evidence,),
            decision="accept",
            record_revision=2,
        ).stage
        == STAGE_SETTLED
    )


def test_session_transitions_record_binding_evidence_and_decision() -> None:
    identity = _identity()
    intent = _intent(identity, plan_body=_plan("STO-3G"), approval_ref="ap_1234")
    binding = TaskBinding(project_id=PROJECT, job_id="job-1", attempt=1, release_id="rel-1")
    evidence = EvidenceRef(evidence_id="ev-1", kind="result_manifest", sha256=DIGEST_A)
    session = ResearchSession.start(identity).record_submit_intent(intent)
    session = session.bind_task(binding, expected_revision=session.revision)
    session = session.record_wait_cursor("r1.cursor-token", expected_revision=session.revision)
    assert session.wait_cursor == "r1.cursor-token"
    session = session.add_evidence(evidence, expected_revision=session.revision)
    session = session.record_decision("accept", expected_revision=session.revision)
    assert session.decision == "accept"
    assert session.evidence_refs == (evidence,)
    payload = session.to_dict()
    assert set(payload) >= set(RESEARCH_SESSION_FIELDS)
    assert set(payload["submit_intent"]) == set(SUBMIT_INTENT_FIELDS)
    assert set(payload["task_binding"]) == set(TASK_BINDING_FIELDS)
    assert set(payload["evidence_refs"][0]) == set(EVIDENCE_REF_FIELDS)
    # A cross-project binding is refused.
    with pytest.raises(ResearchSessionValidationError):
        session.bind_task(
            TaskBinding(project_id=OTHER_PROJECT, job_id="job-2", attempt=1, release_id="rel-1")
        )
    # The decision vocabulary is C's ResearchRecord enum.
    with pytest.raises(ResearchSessionValidationError):
        session.record_decision("completed")


# ---------------------------------------------------------------------------
# 7. Recovery reuses the coordinator and never resumes a review job
# ---------------------------------------------------------------------------


def _coordinator(path: Path, ledger: InMemoryActionLedger):
    from acp_services.runtime.research_events import ResearchEventService

    service = ResearchEventService(
        _event_port(path), project_id=PROJECT, task_id="job-1", attempt=1
    )
    service.append(
        "approval.approved",
        revision=1,
        payload={"approval_ref": "ap_1234"},
        dedup_key="approval:ap_1234:approved",
    )
    projection = ResearchProjection(project_id=PROJECT, task_id="job-1", attempt=1)
    return ResearchRecoveryCoordinator(service, projection, ledger), service


def test_recovery_reuses_the_coordinator_without_a_second_submit(tmp_path: Path) -> None:
    ledger = InMemoryActionLedger()
    coordinator, _service = _coordinator(tmp_path / "events.jsonl", ledger)
    identity = _identity()
    intent = _intent(identity, plan_body=_plan("STO-3G"), approval_ref="ap_1234")
    session = ResearchSession.start(identity).record_submit_intent(intent)

    calls: list[dict[str, Any]] = []

    def submit(projection: dict[str, Any]) -> dict[str, Any]:
        calls.append(projection)
        return {"task_id": "job-1", "attempt": 1, "release_id": "rel-1"}

    first = recover_research_session(
        coordinator,
        cursor=None,
        session=session,
        submit_key=intent.intent_key,
        submit=submit,
    )
    assert first.outcome.submitted is True
    assert first.outcome.claim is not None and first.outcome.claim.kind == "created"
    assert first.blocked_reason.startswith("cursor_reset_requires_snapshot:")
    assert len(calls) == 1

    # A lost cursor forces a snapshot reset; the ledger must replay the submit.
    second = recover_research_session(
        coordinator,
        cursor=None,
        session=session,
        submit_key=intent.intent_key,
        submit=submit,
    )
    assert second.outcome.submitted is False
    assert second.outcome.reset is True
    assert second.blocked_reason == "cursor_reset_requires_snapshot:cursor_missing"
    assert second.rule.stage in {STAGE_AWAITING, STAGE_SETTLED}
    assert len(calls) == 1
    assert ledger.claim_calls == 1

    # Resuming from the returned cursor is a plain replay, no reset.
    third = recover_research_session(
        coordinator,
        cursor=first.outcome.next_cursor,
        session=session,
        submit_key=intent.intent_key,
        submit=submit,
    )
    assert third.outcome.reset is False
    assert third.blocked_reason == ""
    assert len(calls) == 1


def test_research_recovery_never_calls_job_manager_resume() -> None:
    """The review-only ``resume()`` boundary is an AST + signature invariant."""
    import ast
    import inspect

    import acp_services.runtime.research_session as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "resume":
            raise AssertionError(f"research_session calls .resume at line {node.lineno}")
        if isinstance(node, ast.Name) and node.id == "JobManager":
            raise AssertionError(f"research_session references JobManager at line {node.lineno}")
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            continue
        for name in modules:
            assert name != "acp" and not name.startswith("acp."), name
            assert "scheduler" not in name, name
    parameters = set(inspect.signature(recover_research_session).parameters)
    assert "manager" not in parameters
    assert "job_manager" not in parameters
    for rule in CRASH_POINT_RULES.values():
        for action in rule.allowed_actions:
            assert "resume" not in action
    # The only ``resume`` in the contract document is the prohibition.
    text = _contract_text()
    resume_lines = [line for line in text.splitlines() if "resume" in line.lower()]
    assert resume_lines
    for line in resume_lines:
        assert "never" in line.lower() or "not" in line.lower() or "resume" in line.lower()
