"""B19 — cross-research-session event, cursor and recovery contract.

Covers the frozen ``acp2.runtime.events-recovery.v1`` contract and the
fixture-level recovery that B19a hands to C without a live DSH client:

1. contract/field/digest freeze and manifest registration (validator VALID);
2. persistent replay from an opaque cursor and the current snapshot;
3. duplicate event ids are no-ops;
4. out-of-order/stale revisions cannot overwrite a newer terminal projection;
5. cursor expiry and a lost cursor reset to the snapshot and never produce a
   second submit (idempotent action ledger);
6. an authorization revocation stops delivery mid-stream;
7. a chat/DSH session is never the project authority;
8. C ``ResearchRecord`` refs are reference-only and never double-written;
9. RPH exit after task completion releases the LLM wait without cancelling or
   re-dispatching the task.

The real DSH termination/handshake row is intentionally absent here: this host
has no delivered DSH, so B19b records it as ``NOT_VERIFIED(c_dsh_absent)``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from acp.scheduler.events import JobEventLog
from acp.services_events_bootstrap import JobEventLogPort
from acp_services.contracts.events import EventScope, OwnerType
from acp_services.runtime.events import (
    RESET_CURSOR_EXPIRED,
    RESET_PERMISSION_CHANGED,
    AuthorizedStreamRegistry,
    StreamAuthorization,
    StreamAuthorizationError,
    StreamCursor,
)
from acp_services.runtime.research_events import (
    ALL_EVENT_TYPES,
    APPROVAL_EVENT_TYPES,
    B_EVENT_TYPES,
    BUDGET_EVENT_TYPES,
    C_REFERENCE_EVENT_TYPES,
    DEFAULT_CURSOR_MAX_AGE_S,
    PAYLOAD_VERSION,
    RESEARCH_EVENT_SCHEMA,
    TASK_EVENT_TYPES,
    InMemoryActionLedger,
    ResearchEvent,
    ResearchEventService,
    ResearchEventValidationError,
    ResearchProjection,
    ResearchRecoveryCoordinator,
    ResearchSessionRef,
    SessionAuthorityError,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    REPO_ROOT / "docs/reference/next-change/acp2-contracts/runtime/events-recovery.v1.json"
)
HANDOFF_PATH = REPO_ROOT / "docs/reference/next-change/acp2-contracts/runtime/B19a-to-C.json"
MANIFEST_PATH = REPO_ROOT / "docs/reference/next-change/acp2-contracts/manifest.json"

PROJECT = "project_b19_research"
TASK = "task_b19_research"
PRINCIPAL = "principal_local"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _log(path: Path) -> JobEventLogPort:
    path.parent.mkdir(parents=True, exist_ok=True)
    return JobEventLogPort(JobEventLog(path))


def _service(
    path: Path,
    *,
    project_id: str = PROJECT,
    task_id: str = TASK,
    attempt: int = 1,
    cursor_max_age_s: int = DEFAULT_CURSOR_MAX_AGE_S,
    clock=None,
    retention: int | None = None,
) -> ResearchEventService:
    return ResearchEventService(
        _log(path),
        project_id=project_id,
        task_id=task_id,
        attempt=attempt,
        cursor_max_age_s=cursor_max_age_s,
        clock=clock,
        retention=retention,
    )


def _projection(*, task_id: str = TASK) -> ResearchProjection:
    return ResearchProjection(project_id=PROJECT, task_id=task_id, attempt=1)


def _canonical_contract_digest(document: dict) -> str:
    body = json.loads(json.dumps(document))
    body["digest"] = ""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _Authorizer:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed

    def authorize_stream(self, *, principal_id: str, scope: EventScope, job_id: str = ""):
        return StreamAuthorization(
            allowed=self.allowed,
            principal_id=principal_id,
            scope=scope,
            reason_code="granted" if self.allowed else "revoked",
        )


# ---------------------------------------------------------------------------
# (1) contract freeze
# ---------------------------------------------------------------------------


def test_contract_freezes_event_cursor_and_recovery_fields() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert contract["schema"] == RESEARCH_EVENT_SCHEMA
    assert contract["contract_version"] == "1.0.0"
    assert contract["status"] == "delivered"

    event = contract["event"]
    assert event["payload_version"] == PAYLOAD_VERSION
    assert set(event["identity_fields"]) == {
        "event_id",
        "seq",
        "cursor",
        "event_type",
        "task_id",
        "attempt",
        "project_id",
        "revision",
        "payload_version",
        "owner",
        "terminal",
        "emitted_at",
        "payload",
        "dedup_key",
    }
    assert event["owner_components"]["B"] == list(B_EVENT_TYPES)
    assert event["owner_components"]["C"] == list(C_REFERENCE_EVENT_TYPES)
    assert set(event["event_types"]) == set(ALL_EVENT_TYPES)
    assert set(contract["event_types"]["task"]) == set(TASK_EVENT_TYPES)
    assert set(contract["event_types"]["approval"]) == set(APPROVAL_EVENT_TYPES)
    assert set(contract["event_types"]["budget"]) == set(BUDGET_EVENT_TYPES)
    assert set(contract["event_types"]["c_reference"]) == set(C_REFERENCE_EVENT_TYPES)

    cursor = contract["cursor"]
    assert cursor["wire"] == "opaque"
    assert cursor["max_age_s"] == DEFAULT_CURSOR_MAX_AGE_S
    assert cursor["snapshot"]["required_on_reset"] is True
    assert RESET_CURSOR_EXPIRED in cursor["reset_reasons"]
    assert "cursor_missing" in cursor["reset_reasons"]
    assert "cursor_invalid" in cursor["reset_reasons"]

    rules = contract["rules"]
    for rule in (
        "replay",
        "out_of_order",
        "duplicate",
        "idempotent_consumer",
        "terminal_projection",
        "revocation",
        "rph_exit_after_task_completion",
        "lost_cursor",
        "session_is_not_authority",
    ):
        assert rule in rules, rule
        assert rules[rule], rule


def test_contract_digest_is_reproducible() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["digest"] == _canonical_contract_digest(contract)


def test_contract_is_registered_in_the_manifest_and_validator_is_valid() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["contracts"] if item["id"] == "events-recovery")
    assert entry["status"] == "delivered"
    assert entry["delivered_by"] == "B19"
    assert entry["owner"] == "B"
    assert entry["sha256"] == hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/acceptance/validate_acp2_contracts.py"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "VALID:" in completed.stdout


def test_b19a_handoff_is_delivered_and_matches_the_contract() -> None:
    handoff = json.loads(HANDOFF_PATH.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert handoff["schema"] == "acp2.runtime.b19a-handoff.v1"
    assert handoff["from"] == "B19a"
    assert handoff["to"] == "C"
    assert handoff["status"] == "delivered"
    assert handoff["contract_schema"] == RESEARCH_EVENT_SCHEMA
    assert handoff["contract_digest"] == contract["digest"]
    contract_sha = "sha256:" + hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()
    assert handoff["contract_sha256"] == contract_sha
    assert handoff["cursor"]["max_age_s"] == DEFAULT_CURSOR_MAX_AGE_S
    assert handoff["does_not_require_live_dsh"] is True
    assert handoff["business_state_ownership"]["B"] == "task/approval/budget"
    assert (
        handoff["business_state_ownership"]["C"]
        == "ResearchRecord question/proposal/round/task references"
    )


# ---------------------------------------------------------------------------
# (2) persistent replay + snapshot
# ---------------------------------------------------------------------------


def test_replay_from_cursor_survives_a_process_restart(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = _service(path)
    started, _ = first.append("task.started", revision=1, dedup_key="e1")
    cursor = first.current_cursor()
    progress, _ = first.append("task.progress", revision=2, payload={"progress": 0.5})
    completed, _ = first.append("task.completed", revision=3, dedup_key="e3")

    # A new service instance is a new process for the consumer: the cursor is
    # the only state it carries.
    second = _service(path)
    replay = second.replay(cursor)

    assert replay.reset is False
    assert [event.event_id for event in replay.events] == [progress.event_id, completed.event_id]
    assert replay.snapshot_required is False
    snapshot = second.snapshot()
    assert [event.event_id for event in snapshot] == [
        started.event_id,
        progress.event_id,
        completed.event_id,
    ]

    projection = _projection()
    projection.rebuild_from_snapshot(snapshot)
    assert projection.status == "completed"
    assert projection.revision == 3


def test_cursor_is_opaque_and_bound_to_the_sequence_tail(tmp_path: Path) -> None:
    service = _service(tmp_path / "events.jsonl")
    service.append("task.started", revision=1)
    service.append("task.progress", revision=2)
    cursor = service.current_cursor()

    assert cursor.startswith("r1.")
    decoded = base64.urlsafe_b64decode(cursor.split(".", 1)[1] + "===").decode("ascii")
    assert decoded.startswith("2:")
    invalid = service.replay("not-a-cursor")
    assert invalid.reset is True
    assert invalid.reset_reason == "cursor_invalid"
    assert invalid.snapshot_required is True


# ---------------------------------------------------------------------------
# (3) duplicate / (4) out-of-order / terminal protection
# ---------------------------------------------------------------------------


def test_duplicate_event_id_is_a_noop(tmp_path: Path) -> None:
    service = _service(tmp_path / "events.jsonl")
    first, duplicate = service.append("task.started", revision=1, dedup_key="same")
    again, duplicate_again = service.append("task.started", revision=1, dedup_key="same")

    assert duplicate is False
    assert duplicate_again is True
    assert again.event_id == first.event_id
    assert len(service.snapshot()) == 1

    projection = _projection()
    projection.rebuild_from_snapshot(service.snapshot())
    result = projection.apply(again)
    assert result.outcome == "duplicate"
    assert projection.status == "running"
    assert projection.revision == 1


def test_out_of_order_and_newer_non_terminal_cannot_revive_terminal(tmp_path: Path) -> None:
    service = _service(tmp_path / "events.jsonl")
    started, _ = service.append("task.started", revision=1, dedup_key="start")
    completed, _ = service.append("task.completed", revision=2, dedup_key="done")

    projection = _projection()
    assert projection.apply(started).applied
    assert projection.apply(completed).applied
    assert projection.status == "completed"
    assert projection.task_terminal is True

    stale = replace(started, event_id="stale-start", revision=1, cursor="r1.stale")
    assert projection.apply(stale).outcome == "stale"

    newer_non_terminal = replace(started, event_id="newer-start", revision=9, cursor="r1.newer")
    result = projection.apply(newer_non_terminal)
    assert result.outcome == "terminal_protected"
    assert projection.status == "completed"
    assert projection.revision == 2


def test_same_revision_additive_fact_is_accepted_after_approval() -> None:
    projection = _projection()
    requested = ResearchEvent(
        event_id="a1",
        seq=1,
        cursor="r1.a1",
        event_type="approval.requested",
        task_id=TASK,
        attempt=1,
        project_id=PROJECT,
        revision=1,
        payload_version=PAYLOAD_VERSION,
        owner="B",
        terminal=False,
        emitted_at="2026-10-23T00:00:00+00:00",
        payload={"approval_ref": "ap_1"},
    )
    approved = ResearchEvent(
        event_id="a2",
        seq=2,
        cursor="r1.a2",
        event_type="approval.approved",
        task_id=TASK,
        attempt=1,
        project_id=PROJECT,
        revision=2,
        payload_version=PAYLOAD_VERSION,
        owner="B",
        terminal=True,
        emitted_at="2026-10-23T00:00:01+00:00",
        payload={"approval_ref": "ap_1"},
    )
    bound = ResearchEvent(
        event_id="a3",
        seq=3,
        cursor="r1.a3",
        event_type="approval.task_bound",
        task_id=TASK,
        attempt=1,
        project_id=PROJECT,
        revision=2,
        payload_version=PAYLOAD_VERSION,
        owner="B",
        terminal=False,
        emitted_at="2026-10-23T00:00:02+00:00",
        payload={"approval_ref": "ap_1", "task_ref": {"task_id": TASK}},
    )

    assert projection.apply(requested).applied
    assert projection.apply(approved).applied
    assert projection.apply(bound).outcome == "applied_additive"
    assert projection.submitted is True
    assert projection.task_ref == {"task_id": TASK}
    assert projection.approval_state == "approved"


# ---------------------------------------------------------------------------
# (5) cursor expiry / lost cursor -> reset, never a duplicate submit
# ---------------------------------------------------------------------------


def test_cursor_expiry_resets_to_the_snapshot(tmp_path: Path) -> None:
    clock = [1_000.0]
    service = _service(
        tmp_path / "events.jsonl",
        cursor_max_age_s=60,
        clock=lambda: clock[0],
    )
    service.append("task.started", revision=1)
    cursor = service.current_cursor()
    clock[0] += 61

    replay = service.replay(cursor, now=clock[0])
    assert replay.reset is True
    assert replay.reset_reason == RESET_CURSOR_EXPIRED
    assert replay.snapshot_required is True
    assert len(replay.events) == 1
    assert replay.cursor != cursor


def test_expired_cursor_does_not_cause_a_second_submit(tmp_path: Path) -> None:
    clock = [2_000.0]
    service = _service(
        tmp_path / "events.jsonl",
        cursor_max_age_s=60,
        clock=lambda: clock[0],
    )
    service.append("task.started", revision=1, dedup_key="start")
    cursor = service.current_cursor()
    service.append("approval.approved", revision=2, payload={"approval_ref": "ap_b19"})
    clock[0] += 120

    ledger = InMemoryActionLedger()
    projection = _projection()
    coordinator = ResearchRecoveryCoordinator(service, projection, ledger)
    submitted: list[dict] = []

    def _submit(projection_body: dict) -> dict:
        submitted.append(projection_body)
        return {"task_id": TASK, "attempt": 1}

    first = coordinator.recover(
        cursor,
        submit_key="b19-submit-1",
        request_digest="sha256:payload",
        submit=_submit,
    )
    assert first.reset is True
    assert first.reset_reason == RESET_CURSOR_EXPIRED
    assert first.submitted is True
    assert len(submitted) == 1

    second = coordinator.recover(
        cursor,
        submit_key="b19-submit-1",
        request_digest="sha256:payload",
        submit=_submit,
    )
    assert second.reset is True
    assert second.submitted is False
    assert len(submitted) == 1
    assert second.projection.submitted is True
    assert second.action.kind in {"observe", "no_action"}


def test_lost_cursor_rebuilds_state_and_dispatches_once(tmp_path: Path) -> None:
    service = _service(tmp_path / "events.jsonl")
    service.append("approval.approved", revision=1, payload={"approval_ref": "ap_lost"})
    ledger = InMemoryActionLedger()
    projection = _projection()
    coordinator = ResearchRecoveryCoordinator(service, projection, ledger)
    calls: list[str] = []

    for _ in range(2):
        outcome = coordinator.recover(
            None,
            submit_key="lost-cursor-submit",
            request_digest="sha256:same",
            submit=lambda body: calls.append(body["task_id"]) or {"task_id": TASK},
        )
        assert outcome.reset is True
        assert outcome.reset_reason == "cursor_missing"

    assert calls == [TASK]
    assert ledger.claim_calls == 1
    assert ledger.complete_calls == 1


# ---------------------------------------------------------------------------
# (6) revocation stops delivery mid-stream
# ---------------------------------------------------------------------------


def test_revocation_stops_delivery_mid_stream(tmp_path: Path) -> None:
    path = tmp_path / TASK / "events.jsonl"
    service = ResearchEventService(
        _log(path),
        project_id=PROJECT,
        task_id=TASK,
        attempt=1,
    )
    service.append("task.started", revision=1, dedup_key="r1")
    authorizer = _Authorizer()
    registry = AuthorizedStreamRegistry(owner_type=OwnerType.TASK)
    stream = registry.open(
        principal_id=PRINCIPAL,
        scope=EventScope.PROJECT,
        service_epoch="epoch-b19",
        log_for=lambda job_id: _log(tmp_path / job_id / "events.jsonl"),
        job_ids=(TASK,),
        authorizer=authorizer,
    )
    cursor = StreamCursor(
        principal_id=PRINCIPAL,
        scope=EventScope.PROJECT,
        job_id=TASK,
        after_seq=0,
        service_epoch="epoch-b19",
    )
    first = stream.poll([cursor])
    assert len(first.events) == 1

    # Mid-stream authorization revocation closes the live stream.
    authorizer.allowed = False
    assert stream.check_authorization(authorizer) is False
    assert stream.closed is True
    after_revocation = stream.poll([cursor])
    assert after_revocation.reset is True
    assert after_revocation.reset_reason == RESET_PERMISSION_CHANGED
    assert after_revocation.events == ()

    # New connections are refused and leave no live stream behind.
    registry.close(PRINCIPAL, EventScope.PROJECT)
    refused = AuthorizedStreamRegistry(owner_type=OwnerType.TASK)
    with pytest.raises(StreamAuthorizationError):
        refused.open(
            principal_id=PRINCIPAL,
            scope=EventScope.PROJECT,
            service_epoch="epoch-b19",
            log_for=lambda job_id: _log(tmp_path / job_id / "events.jsonl"),
            job_ids=(TASK,),
            authorizer=authorizer,
        )
    assert refused.active_count == 0


# ---------------------------------------------------------------------------
# (7) chat session is not project authority
# ---------------------------------------------------------------------------


def test_chat_session_cannot_claim_project_authority(tmp_path: Path) -> None:
    service = _service(tmp_path / "events.jsonl")
    service.append("task.started", revision=1)
    projection = _projection()
    projection.rebuild_from_snapshot(service.snapshot())
    coordinator = ResearchRecoveryCoordinator(service, projection, InMemoryActionLedger())

    with pytest.raises(SessionAuthorityError):
        coordinator.detach_session(
            ResearchSessionRef(session_id="dsh-1", claimed_project_id="project_other")
        )

    detached = coordinator.detach_session(ResearchSessionRef(session_id="dsh-1"))
    assert detached.blocks_on_llm is False
    assert detached.continues is True
    assert projection.project_id == PROJECT
    assert projection.status == "running"


def test_event_project_scope_mismatch_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path / "events.jsonl")
    with pytest.raises(ResearchEventValidationError):
        service.append("task.started", revision=1, project_id="project_other")

    foreign = ResearchEvent(
        event_id="foreign",
        seq=1,
        cursor="r1.foreign",
        event_type="task.completed",
        task_id=TASK,
        attempt=1,
        project_id="project_other",
        revision=1,
        payload_version=PAYLOAD_VERSION,
        owner="B",
        terminal=True,
        emitted_at="2026-10-23T00:00:00+00:00",
    )
    result = _projection().apply(foreign)
    assert result.outcome == "project_scope_violation"


# ---------------------------------------------------------------------------
# (8) B/C business-state separation
# ---------------------------------------------------------------------------


def test_c_research_record_refs_are_reference_only(tmp_path: Path) -> None:
    # C's record is owned by C; B never receives a write to it.
    c_record = {
        "question": {"id": "q1", "text": "does the reaction proceed?"},
        "proposal": {"id": "p1", "revision": 4},
        "round": 2,
    }
    before = json.loads(json.dumps(c_record))

    service = _service(tmp_path / "events.jsonl")
    event, duplicate = service.append(
        "research.proposal.referenced",
        revision=1,
        owner_component="C",
        dedup_key="c-proposal-ref",
        payload={
            "resource_ref": "research/proposal/p1",
            "resource_revision": 4,
            "resource_digest": "sha256:" + "a" * 64,
        },
    )
    assert duplicate is False
    assert c_record == before  # B did not write the record

    projection = _projection()
    result = projection.apply(event)
    assert result.outcome == "applied"
    assert projection.status == "unknown"
    assert projection.revision == 0
    assert projection.c_refs["research.proposal.referenced"]["resource_revision"] == 4

    # C may not publish B business state; B may not publish C reference facts.
    with pytest.raises(ResearchEventValidationError):
        service.append("task.completed", revision=2, owner_component="C")
    with pytest.raises(ResearchEventValidationError):
        service.append("research.question.referenced", revision=2, owner_component="B")


# ---------------------------------------------------------------------------
# (9) RPH exit after task completion
# ---------------------------------------------------------------------------


def test_rph_exit_after_completion_does_not_wait_cancel_or_redispatch(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "events.jsonl")
    service.append("approval.approved", revision=1, payload={"approval_ref": "ap_rph"})
    service.append("task.completed", revision=2, dedup_key="rph-completed")
    projection = _projection()
    ledger = InMemoryActionLedger()
    coordinator = ResearchRecoveryCoordinator(service, projection, ledger)
    calls: list[str] = []

    outcome = coordinator.recover(
        None,
        submit_key="rph-submit",
        request_digest="sha256:rph",
        submit=lambda body: calls.append(body["task_id"]) or {"task_id": TASK},
    )
    assert outcome.action.kind == "no_action"
    assert calls == []

    detached = coordinator.detach_session(
        ResearchSessionRef(session_id="dsh-rph"), reason="rph_exit"
    )
    assert detached.continues is False
    assert detached.blocks_on_llm is False
    assert detached.reason == "task_terminal"
    assert projection.status == "completed"
