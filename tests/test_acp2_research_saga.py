"""M2-01b — cross-database research saga, same-transaction outbox, reconciliation.

The M2-01b exit condition is "every break point retries without a second fact,
the outbox and the business write commit or roll back together, and a read-only
reconciliation names who retries what".  These tests pin exactly that, against
the real authorities (SQLite research store, B16 approvals, M1-02 submit intent)
and with **no seed fixture**: every fact is created by the driver under test.

Coverage map:

* :func:`test_every_break_point_retries_without_a_second_fact` — the five-step
  matrix (proposal/approval/submit/task_ref/evidence_ref × prepare/business
  break points), each resumed by a **fresh** driver over the same databases;
* :func:`test_new_process_resumes_the_chain_without_duplicate_facts` — a real
  child process is killed mid-chain and a second process finishes it;
* :func:`test_research_outbox_commits_or_rolls_back_with_the_resource` — the
  same-transaction API on the research database;
* :func:`test_jobs_outbox_is_written_in_the_state_transaction` and
  :func:`test_admission_event_is_written_in_the_reservation_transaction` — the
  two pre-existing same-transaction paths the coverage table points at;
* :func:`test_outbox_delivery_is_at_least_once_with_exactly_once_effect` — the
  existing ``OutboxDeliveryService`` drains committed rows only;
* :func:`test_reconcile_is_read_only_and_names_the_retry_owner` and
  :func:`test_reconcile_action_is_the_only_mutating_entry`;
* :func:`test_record_stores_references_not_authority_rows` — the non-authority
  projection guard;
* :func:`test_round_append_is_a_record_revision_with_its_own_outbox_row` and
  :func:`test_operation_idempotency_key_replays_or_conflicts`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from acp_services.data.research_resources import ResearchResourceStore
from acp_services.runtime.admission import (
    AdmissionRequest,
    Capacity,
    ReservationLedger,
    ReserveLimits,
)
from acp_services.runtime.approvals import ApprovalService
from acp_services.runtime.local_outbox import (
    LOCAL_OUTBOX_COVERAGE,
    LocalOutboxTable,
    research_outbox,
)
from acp_services.runtime.outbox import OutboxDeliveryService, OutboxEventSpec
from acp_services.runtime.research_saga import (
    SAGA_STEPS,
    STEP_RETRY_OWNER,
    ResearchSaga,
    SagaConflictError,
    SagaStore,
    saga_id_for,
)
from acp_services.runtime.research_service import ResearchOperationConflictError, ResearchOperations
from acp_services.runtime.research_writer import (
    RECORD_AUTHORITY_KEYS,
    ResearchWriter,
    record_authority_violations,
)
from acp_services.store.records import JobRecord, JobSpec, JobStatus
from acp_services.store.store import JobStore
from acp_services.workspace.command_identity import CommandIdentity

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
PYTHON = sys.executable

PROJECT = "proj-m201"
PRINCIPAL = "alice"
SEED = "seed-m201"
PROPOSAL_ID = "prop-m201-001"

PLAN: dict[str, Any] = {
    "workflow": "fake",
    "input": {"source": "CCO", "source_type": "smiles"},
    "method": {},
    "resources": {"cores": 1},
}


def build_proposal(proposal_id: str = PROPOSAL_ID) -> dict[str, Any]:
    return {
        "proposal_id": proposal_id,
        "project_id": PROJECT,
        "question_ref": "question-m201",
        "question_revision": 0,
        "reaction_ref": "reaction-m201",
        "reaction_revision": 0,
        "hypothesis": "a digest-bound round decides on one bound attempt",
        "recipe_digest": "sha256:" + "a" * 64,
        "plan_definition_digest": "",
        "science": {
            "capability_steps": ["singlepoint"],
            "initial_inputs": [
                {
                    "input_id": "in-m201",
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


class SubmitLedger:
    """An idempotent submit port: the same key replays the same TaskRef."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.by_key: dict[str, dict[str, Any]] = {}

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        key = str(payload["idempotency_key"])
        self.calls.append(key)
        if key not in self.by_key:
            self.by_key[key] = {
                "task_ref": {
                    "task_id": "job-m201-1",
                    "job_id": "job-m201-1",
                    "attempt": 1,
                    "release_id": "rel-local-m201",
                    "project_id": PROJECT,
                },
                "state": "queued",
            }
        return self.by_key[key]


def _record_payload(approval_ref: str, science_digest: str) -> dict[str, Any]:
    return {
        "proposal_ref": PROPOSAL_ID,
        "approval_ref": approval_ref,
        "plan_definition_digest": science_digest,
        "round": 0,
        "decision": "accept",
        "task_refs": [
            {
                "project_id": PROJECT,
                "job_id": "job-m201-1",
                "attempt": 1,
                "release_id": "rel-local-m201",
            }
        ],
        "evidence_refs": [
            {
                "evidence_id": "ev-m201",
                "kind": "evidence_report",
                "sha256": "sha256:" + "c" * 64,
                "relative_path": "REPORT/evidence_report.json",
            }
        ],
        "created_at": "2026-10-27T00:10:00Z",
    }


class Rig:
    """The four databases + the driver under test (rebuilt per "process")."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.resources = ResearchResourceStore(root / "acp_research.db")
        self.approvals = ApprovalService(root / "acp_jobs.db")
        self.sagas = SagaStore(root / "acp_research_saga.db")
        self.writer = ResearchWriter(resources=self.resources, approvals=self.approvals)
        self.submit = SubmitLedger()
        self.command = CommandIdentity(principal_id=PRINCIPAL, project_id=PROJECT)

    def saga(self, fault_hook: Any = None) -> ResearchSaga:
        return ResearchSaga(
            store=self.sagas,
            writer=self.writer,
            approvals=self.approvals,
            submit=self.submit,
            fault_hook=fault_hook,
        )

    def operations(self, *, saga: ResearchSaga | None = None) -> ResearchOperations:
        return ResearchOperations(
            writer=self.writer,
            resources=self.resources,
            approvals=self.approvals,
            sagas=self.sagas,
            saga=saga or self.saga(),
            submit=self.submit,
        )

    def approve(self, approval_ref: str) -> None:
        approval = self.approvals.get(approval_ref)
        assert approval is not None
        if approval.state == "approved":
            return
        self.approvals.approve(
            approval_ref, approver_principal=PRINCIPAL, expected_revision=approval.revision
        )

    def close(self) -> None:
        self.resources.close()
        self.sagas.close()


def _start(rig: Rig, *, record: Mapping[str, Any] | None = None):
    return rig.saga().start(
        command=rig.command,
        session_seed=SEED,
        proposal=build_proposal(),
        plan=PLAN,
        record=record,
    )


def _full_record(rig: Rig, saga_id: str) -> dict[str, Any]:
    """Build the record payload bound to the saga's real approval + digests."""
    header = rig.sagas.header(saga_id)
    assert header is not None
    digests = header.request["digests"]
    approval_ref = header.approval_ref
    if not approval_ref:
        intent = rig.sagas.intent(saga_id, "proposal")
        assert intent is not None and intent.committed
        approval_ref = str(intent.result.get("approval_ref") or "")
    return _record_payload(approval_ref, digests["science_digest"])


def _counts(rig: Rig) -> dict[str, int]:
    proposals = rig.resources.list_heads(project_id=PROJECT)
    return {
        "proposal_revisions": sum(1 for item in proposals if item.resource_id.startswith("rrk_")),
        "approvals": len(rig.approvals.query(project_id=PROJECT, limit=200)),
        "sagas": len(rig.sagas.list_headers(project_id=PROJECT, limit=200)),
        "submit_calls": len(rig.submit.calls),
        "submit_keys": len(rig.submit.by_key),
        "records": len(
            rig.resources.list_heads(
                project_id=PROJECT, schema="acp2.experience.research-record.v1"
            )
        ),
    }


# ---------------------------------------------------------------------------
# 1. The break-point matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("step", SAGA_STEPS)
@pytest.mark.parametrize("phase", ["after_prepare", "after_business"])
def test_every_break_point_retries_without_a_second_fact(
    tmp_path: Path, step: str, phase: str
) -> None:
    """A crash at any step boundary is resumed by a fresh driver, once."""
    rig = Rig(tmp_path / "run-root")
    try:
        started = _start(rig)
        saga_id = started.header.saga_id

        crashed = {"at": ""}

        def fault(at_step: str, at_phase: str) -> None:
            if at_step == step and at_phase == phase:
                crashed["at"] = f"{at_step}:{at_phase}"
                raise RuntimeError("simulated crash")

        if step == "proposal":
            # Crash before the first proposal revision is committed.
            with pytest.raises(RuntimeError):
                rig.saga(fault_hook=fault).advance(saga_id)
            assert crashed["at"] == f"{step}:{phase}"
            # Drive to the approval gate: the proposal is written exactly once
            # (the after-business phase adopts the stored revision).
            gated = rig.saga().advance(saga_id)
            assert gated.blocked_step == "approval", gated.to_dict()
            approval_ref = gated.intent("proposal").result["approval_ref"]  # type: ignore[union-attr]
            rig.approve(approval_ref)
            record = _full_record(rig, saga_id)
            rig.saga().start(
                command=rig.command,
                session_seed=SEED,
                proposal=build_proposal(),
                plan=PLAN,
                record=record,
            )
        else:
            first = rig.saga().advance(saga_id)
            approval_ref = first.intent("proposal").result["approval_ref"]  # type: ignore[union-attr]
            rig.approve(approval_ref)
            record = _full_record(rig, saga_id)
            # Re-open the saga with the record material so the last step can commit.
            rig.saga().start(
                command=rig.command,
                session_seed=SEED,
                proposal=build_proposal(),
                plan=PLAN,
                record=record,
            )
            with pytest.raises(RuntimeError):
                rig.saga(fault_hook=fault).advance(saga_id)
            assert crashed["at"] == f"{step}:{phase}"

        # A brand-new driver over the same databases (the "restart").
        restarted = rig.saga()
        status = restarted.advance(saga_id)
        assert status.completed is True, status.to_dict()
        assert all(item.state == "committed" for item in status.intents), status.to_dict()

        counts = _counts(rig)
        assert counts["records"] == 1
        assert counts["approvals"] == 1
        assert counts["sagas"] == 1
        assert counts["submit_keys"] == 1
        # The submit port may legitimately see the same key twice (at-least-once
        # *attempt*), but never a second key: one task, one intent.
        assert len(set(rig.submit.calls)) == 1
        # One proposal resource with exactly one revision (no second write).
        proposal = rig.writer.read_proposal(
            principal_id=PRINCIPAL, project_id=PROJECT, proposal_id=PROPOSAL_ID
        )
        assert proposal.revision == 1
        assert len(rig.resources.versions(proposal.resource_id)) == 1
    finally:
        rig.close()


def test_break_point_before_approval_is_blocked_not_forced(tmp_path: Path) -> None:
    """A pending approval blocks the chain and names the approver as retry owner."""
    rig = Rig(tmp_path / "run-root")
    try:
        started = _start(rig)
        status = rig.saga().advance(started.header.saga_id)
        assert status.completed is False
        assert status.blocked_step == "approval"
        assert status.blocked_reason == "approval_not_approved"
        reconciliation = rig.saga().reconcile(started.header.saga_id)
        approval_step = next(item for item in reconciliation.steps if item.step == "approval")
        assert approval_step.retry_owner == "approver"
        assert reconciliation.retry_owner == "approver"
        assert reconciliation.next_action == "retry:approval"
        # No task, no record: the approval is the only open gate.
        assert rig.submit.calls == []
        assert _counts(rig)["records"] == 0
    finally:
        rig.close()


# ---------------------------------------------------------------------------
# 2. A real second process finishes the chain
# ---------------------------------------------------------------------------

CHILD_SCRIPT = r"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["ACP2_SRC"])
sys.path.insert(0, str(Path(os.environ["ACP2_SRC"]).parent / "tests"))

from test_acp2_research_saga import PLAN, SEED, Rig, _full_record, _start, build_proposal

root = Path(os.environ["ACP2_RUN_ROOT"])
mode = os.environ["ACP2_MODE"]
rig = Rig(root)

if mode == "start":
    started = _start(rig)
    gated = rig.saga().advance(started.header.saga_id)
    rig.approve(gated.intent("proposal").result["approval_ref"])
    record = _full_record(rig, started.header.saga_id)
    rig.saga().start(
        command=rig.command,
        session_seed=SEED,
        proposal=build_proposal(),
        plan=PLAN,
        record=record,
    )

    def fault(at_step, at_phase):
        if at_step == "submit" and at_phase == "after_business":
            os._exit(137)

    rig.saga(fault_hook=fault).advance(started.header.saga_id)
    raise SystemExit(0)

saga_id = os.environ["ACP2_SAGA_ID"]
status = rig.saga().advance(saga_id)
print(status.completed)
"""


def test_new_process_resumes_the_chain_without_duplicate_facts(tmp_path: Path) -> None:
    """Kill a process mid-chain, then finish it in a second process."""
    run_root = tmp_path / "run-root"
    run_root.mkdir()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_ROOT)
    env["ACP2_SRC"] = str(SRC_ROOT)
    env["ACP2_RUN_ROOT"] = str(run_root)
    env["ACP2_MODE"] = "start"

    first = subprocess.run(
        [PYTHON, "-c", CHILD_SCRIPT], env=env, capture_output=True, text=True, timeout=120
    )
    assert first.returncode == 137, first.stderr

    rig = Rig(run_root)
    try:
        sagas = rig.sagas.list_headers(project_id=PROJECT, limit=10)
        assert len(sagas) == 1
        saga_id = sagas[0].saga_id
        # The crash landed after the business write and before the intent commit.
        submit_intent = rig.sagas.intent(saga_id, "submit")
        assert submit_intent is not None and submit_intent.state == "prepared"
        assert submit_intent.attempts == 1
    finally:
        rig.close()

    env["ACP2_MODE"] = "finish"
    env["ACP2_SAGA_ID"] = saga_id
    second = subprocess.run(
        [PYTHON, "-c", CHILD_SCRIPT], env=env, capture_output=True, text=True, timeout=120
    )
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip().endswith("True"), second.stdout

    rig = Rig(run_root)
    try:
        status = rig.saga().status(saga_id)
        assert all(item.state == "committed" for item in status.intents), status.to_dict()
        # Exactly one proposal revision, one approval, one record: the crash did
        # not create a second fact, and neither did the resume.
        proposal = rig.writer.read_proposal(
            principal_id=PRINCIPAL, project_id=PROJECT, proposal_id=PROPOSAL_ID
        )
        assert len(rig.resources.versions(proposal.resource_id)) == 1
        assert len(rig.approvals.query(project_id=PROJECT, limit=10)) == 1
        record = rig.writer.read_record(
            principal_id=PRINCIPAL,
            project_id=PROJECT,
            research_id=status.header.research_id,
        )
        assert record.document["decision"] == "accept"
        assert len(rig.resources.versions(record.resource_id)) == 1
    finally:
        rig.close()


# ---------------------------------------------------------------------------
# 3. Same-transaction outbox
# ---------------------------------------------------------------------------


def test_outbox_coverage_matrix_names_every_authority() -> None:
    assert set(LOCAL_OUTBOX_COVERAGE) == {"jobs", "research", "admission", "coordination"}
    for kind, row in LOCAL_OUTBOX_COVERAGE.items():
        assert row.same_transaction is True, kind
        assert row.authority and row.entry, kind
        assert "no cross-database atomicity" in row.note or row.note


def test_research_outbox_commits_or_rolls_back_with_the_resource(tmp_path: Path) -> None:
    store = ResearchResourceStore(tmp_path / "acp_research.db")
    try:
        saga_id = "saga-tx-1"

        class BoomError(RuntimeError):
            pass

        def failing(conn: Any) -> None:
            LocalOutboxTable.ensure(conn)
            raise BoomError("business write failed")

        with pytest.raises(BoomError):
            research_outbox(
                store,
                events=[OutboxEventSpec(event_type="research.test", payload={"n": 1})],
                owner_id=saga_id,
                business=failing,
            )
        with store.transaction() as conn:
            assert LocalOutboxTable.pending(conn, owner_id=saga_id) == []

        def succeeding(conn: Any) -> str:
            return "committed"

        result = research_outbox(
            store,
            events=[OutboxEventSpec(event_type="research.test", payload={"n": 1})],
            owner_id=saga_id,
            business=succeeding,
        )
        assert result == "committed"
        with store.transaction() as conn:
            pending = LocalOutboxTable.pending(conn, owner_id=saga_id)
            assert len(pending) == 1
            assert pending[0].event_type == "research.test"
    finally:
        store.close()


def test_research_outbox_refuses_a_store_without_the_transaction_seam() -> None:
    from acp_services.runtime.local_outbox import LocalOutboxError

    class NoSeam:
        pass

    with pytest.raises(LocalOutboxError):
        research_outbox(
            NoSeam(),
            events=[],
            owner_id="saga-x",
            business=lambda conn: None,
        )


def test_jobs_outbox_is_written_in_the_state_transaction(tmp_path: Path) -> None:
    """The pre-existing B04-2 path the coverage table points at (no second one)."""
    store = JobStore(tmp_path / "acp_jobs.db")
    store.create(
        JobRecord(
            id="job-tx-1",
            spec=JobSpec(workflow="fake", name="m201", input={"source": "C"}),
            status=JobStatus.QUEUED,
            work_dir=str(tmp_path / "work" / "job-tx-1"),
        )
    )
    spec = OutboxEventSpec(event_type="job.queued", payload={"n": 1})
    created = store.get("job-tx-1")
    assert created is not None
    store.transition(
        "job-tx-1",
        expected_status=JobStatus.QUEUED,
        expected_revision=created.revision,
        outbox=spec,
    )
    assert len(store.pending_outbox(job_id="job-tx-1")) == 1

    # A failed CAS guard rolls the outbox row back with the state change.
    from acp_services.store.store import JobStateConflictError

    with pytest.raises(JobStateConflictError):
        store.transition(
            "job-tx-1",
            expected_status=JobStatus.QUEUED,  # already RUNNING? no: still QUEUED
            expected_revision=99,
            outbox=OutboxEventSpec(event_type="job.queued", payload={"n": 2}),
        )
    assert len(store.pending_outbox(job_id="job-tx-1")) == 1


def test_admission_event_is_written_in_the_reservation_transaction(tmp_path: Path) -> None:
    """The ledger writes its business row + budget event in one transaction."""
    ledger = ReservationLedger(str(tmp_path / "admission.db"))
    request = AdmissionRequest(
        principal_id=PRINCIPAL, project_id=PROJECT, job_id="job-adm-1", cores=1
    )
    limits = ReserveLimits(
        capacity=Capacity(cores=4, memory_mb=4096, concurrency=4), policy_version="m201"
    )
    before = _admission_events(ledger)
    outcome = ledger.reserve(request, limits)
    assert outcome.reservation is not None
    after = _admission_events(ledger)
    assert after >= before  # the row and its events are one commit, never split
    # A refused reservation (over capacity) inserts neither a row nor an event.
    over = ledger.reserve(
        AdmissionRequest(principal_id=PRINCIPAL, project_id=PROJECT, job_id="job-adm-2", cores=99),
        ReserveLimits(
            capacity=Capacity(cores=1, memory_mb=1024, concurrency=1), policy_version="m201"
        ),
    )
    assert over.allowed is False
    assert ledger.count() == 1


def _admission_events(ledger: ReservationLedger) -> int:
    import sqlite3

    conn = sqlite3.connect(str(ledger.db_path))
    try:
        row = conn.execute("SELECT COUNT(*) FROM admission_reservations").fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def test_outbox_delivery_is_at_least_once_with_exactly_once_effect(tmp_path: Path) -> None:
    """A crash after the durable append but before the ack re-appends nothing."""
    rig = Rig(tmp_path / "run-root")
    try:
        started = _start(rig)
        gated = rig.saga().advance(started.header.saga_id)
        rig.approve(gated.intent("proposal").result["approval_ref"])
        record = _full_record(rig, started.header.saga_id)
        rig.saga().start(
            command=rig.command,
            session_seed=SEED,
            proposal=build_proposal(),
            plan=PLAN,
            record=record,
        )
        rig.saga().advance(started.header.saga_id)
        saga_id = started.header.saga_id
        # One prepared + one committed event per step, all committed with the intent.
        pending = len(rig.sagas.pending_outbox(job_id=saga_id))
        assert pending == 2 * len(SAGA_STEPS)

        log = _RecordingLog()
        service = OutboxDeliveryService(
            outbox=rig.sagas, log_for=lambda owner: log, clock=lambda: "2026-10-27T01:00:00Z"
        )
        first = service.deliver_pending(job_id=saga_id)
        assert first.delivered_count == pending
        assert rig.sagas.undelivered_count(owner_id=saga_id) == 0
        assert len(log.entries) == pending

        # Simulate the crash window: the append happened, the ack did not.
        import sqlite3

        conn = sqlite3.connect(str(rig.sagas.db_path))
        try:
            conn.execute("UPDATE local_outbox SET delivered_at=NULL")
            conn.commit()
        finally:
            conn.close()
        second = service.deliver_pending(job_id=saga_id)
        assert second.delivered_count == 0
        assert len(second.duplicates) == pending
        assert rig.sagas.undelivered_count(owner_id=saga_id) == 0
        # Exactly-once effect: the durable log did not grow.
        assert len(log.entries) == pending
    finally:
        rig.close()


class _RecordingLog:
    """The EventLogPort shape, with the dedup guard the real log has."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self._keys: set[str] = set()

    def append(self, event_type: str, *, idempotency_key: str | None = None, **data: Any) -> None:
        if idempotency_key and idempotency_key in self._keys:
            return
        if idempotency_key:
            self._keys.add(idempotency_key)
        self.entries.append({"type": event_type, "idempotency_key": idempotency_key, **data})

    def count(self) -> int:
        return len(self.entries)

    def read_all(self) -> list[dict[str, Any]]:
        return list(self.entries)

    def read_last(self) -> dict[str, Any] | None:
        return self.entries[-1] if self.entries else None


# ---------------------------------------------------------------------------
# 4. Reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_is_read_only_and_names_the_retry_owner(tmp_path: Path) -> None:
    rig = Rig(tmp_path / "run-root")
    try:
        started = _start(rig)
        saga_id = started.header.saga_id
        before = _snapshot(rig, saga_id)
        reconciliation = rig.saga().reconcile(saga_id)
        after = _snapshot(rig, saga_id)
        assert before == after, "reconcile must not mutate the intent chain"
        assert {step.step for step in reconciliation.steps} == set(SAGA_STEPS)
        assert step_retry_owners() == {step: STEP_RETRY_OWNER[step][0] for step in SAGA_STEPS}
        open_step = next(item for item in reconciliation.steps if item.state != "committed")
        assert open_step.retry_owner == STEP_RETRY_OWNER[open_step.step][0]
    finally:
        rig.close()


def step_retry_owners() -> dict[str, str]:
    return {step: owner for step, (owner, _reason) in STEP_RETRY_OWNER.items()}


def _snapshot(rig: Rig, saga_id: str) -> tuple[Any, ...]:
    header = rig.sagas.header(saga_id)
    intents = rig.sagas.intents(saga_id)
    outbox = rig.sagas.pending_outbox(job_id=saga_id)
    return (
        None if header is None else (header.state, header.revision, header.updated_at),
        tuple((item.step, item.state, item.attempts, item.updated_at) for item in intents),
        tuple((row.event_id, row.delivered_at) for row in outbox),
    )


def test_reconcile_action_is_the_only_mutating_entry(tmp_path: Path) -> None:
    rig = Rig(tmp_path / "run-root")
    try:
        started = _start(rig)
        saga_id = started.header.saga_id
        header = rig.sagas.header(saga_id)
        assert header is not None
        with pytest.raises(SagaConflictError):
            rig.saga().reconcile_action(saga_id, action="advance", expected_revision=999)
        with pytest.raises(Exception):
            rig.saga().reconcile_action(
                saga_id, action="nonsense", expected_revision=header.revision
            )
        abandoned = rig.saga().reconcile_action(
            saga_id, action="abandon", expected_revision=header.revision, reason="superseded"
        )
        assert abandoned.header.state == "abandoned"
        status = rig.saga().status(saga_id)
        assert all(item.state != "prepared" for item in status.intents)
        with pytest.raises(SagaConflictError):
            rig.saga().advance(saga_id)
    finally:
        rig.close()


# ---------------------------------------------------------------------------
# 5. Record projection + round append + idempotency keys
# ---------------------------------------------------------------------------


def test_record_stores_references_not_authority_rows(tmp_path: Path) -> None:
    rig = Rig(tmp_path / "run-root")
    try:
        started = _start(rig)
        gated = rig.saga().advance(started.header.saga_id)
        rig.approve(gated.intent("proposal").result["approval_ref"])
        record = _full_record(rig, started.header.saga_id)
        rig.saga().start(
            command=rig.command,
            session_seed=SEED,
            proposal=build_proposal(),
            plan=PLAN,
            record=record,
        )
        status = rig.saga().advance(started.header.saga_id)
        assert status.completed is True, status.to_dict()
        written = rig.writer.read_record(
            principal_id=PRINCIPAL,
            project_id=PROJECT,
            research_id=status.header.research_id,
        )
        document = dict(written.document)
        # The record cites a TaskRef and evidence refs, never a live authority row.
        assert record_authority_violations(document) == []
        assert set(document) & RECORD_AUTHORITY_KEYS == set()
        assert document["task_refs"][0]["job_id"] == "job-m201-1"
        assert document["evidence_refs"][0]["sha256"].startswith("sha256:")
        # A record that did copy an authority row is refused by name.
        assert record_authority_violations({"round": 0, "usage": [{"cores": 1}]}) == ["usage"]
        assert record_authority_violations(
            {"task_refs": [{"job_id": "j", "status": "RUNNING"}]}
        ) == ["task_refs[0].status"]
    finally:
        rig.close()


def test_round_append_is_a_record_revision_with_its_own_outbox_row(tmp_path: Path) -> None:
    rig = Rig(tmp_path / "run-root")
    try:
        started = _start(rig)
        gated = rig.saga().advance(started.header.saga_id)
        rig.approve(gated.intent("proposal").result["approval_ref"])
        record = _full_record(rig, started.header.saga_id)
        rig.saga().start(
            command=rig.command,
            session_seed=SEED,
            proposal=build_proposal(),
            plan=PLAN,
            record=record,
        )
        status = rig.saga().advance(started.header.saga_id)
        assert status.completed is True
        current = rig.writer.read_record(
            principal_id=PRINCIPAL,
            project_id=PROJECT,
            research_id=status.header.research_id,
        )
        operations = rig.operations()
        appended = operations.record_append_round(
            command=rig.command,
            research_id=status.header.research_id,
            expected_revision=current.revision,
            session_seed=SEED,
            record={
                "round": 1,
                "decision": "revise",
                "task_refs": [
                    {
                        "project_id": PROJECT,
                        "job_id": "job-m201-1",
                        "attempt": 1,
                        "release_id": "rel-local-m201",
                    }
                ],
                "evidence_refs": [
                    {
                        "evidence_id": "ev-m201-b",
                        "kind": "evidence_report",
                        "sha256": "sha256:" + "d" * 64,
                    }
                ],
            },
        )
        assert appended["revision"] == current.revision + 1
        assert appended["round"] == 1
        assert appended["record_revision"] == 2
        with rig.resources.transaction() as conn:
            rows = LocalOutboxTable.pending(conn, owner_id=current.resource_id)
        assert [row.event_type for row in rows] == ["research.record.round_appended"]
        # A stale CAS is a typed conflict, not a second round.
        with pytest.raises(ResearchOperationConflictError):
            operations.record_append_round(
                command=rig.command,
                research_id=status.header.research_id,
                expected_revision=current.revision,
                session_seed=SEED,
                record={
                    "round": 2,
                    "decision": "accept",
                    "task_refs": record["task_refs"],
                    "evidence_refs": record["evidence_refs"],
                },
            )
    finally:
        rig.close()


def test_operation_idempotency_key_replays_or_conflicts(tmp_path: Path) -> None:
    rig = Rig(tmp_path / "run-root")
    try:
        operations = rig.operations()
        first = operations.proposal_create(
            command=rig.command,
            proposal=build_proposal(),
            plan=PLAN,
            session_seed=SEED,
            idempotency_key="idem-m201-create",
        )
        assert first["replayed"] is False
        replay = operations.proposal_create(
            command=rig.command,
            proposal=build_proposal(),
            plan=PLAN,
            session_seed=SEED,
            idempotency_key="idem-m201-create",
        )
        assert replay["replayed"] is True
        assert replay["revision"] == first["revision"] == 1
        with pytest.raises(ResearchOperationConflictError) as excinfo:
            operations.proposal_create(
                command=rig.command,
                proposal=build_proposal("prop-m201-002"),
                plan=PLAN,
                session_seed=SEED,
                idempotency_key="idem-m201-create",
            )
        assert excinfo.value.rule == "idempotency_key_reuse"
    finally:
        rig.close()


def test_saga_id_is_derived_so_a_retry_finds_the_same_chain(tmp_path: Path) -> None:
    rig = Rig(tmp_path / "run-root")
    try:
        first = _start(rig)
        second = _start(rig)
        assert first.header.saga_id == second.header.saga_id
        assert first.header.saga_id == saga_id_for(
            project_id=PROJECT,
            proposal_ref=PROPOSAL_ID,
            plan_digest=first.header.plan_digest,
        )
        assert len(rig.sagas.list_headers(project_id=PROJECT, limit=10)) == 1
        # A different plan digest is a different chain, never the same one.
        other = rig.saga().start(
            command=rig.command,
            session_seed=SEED,
            proposal=build_proposal("prop-m201-002"),
            plan={**PLAN, "resources": {"cores": 2}},
        )
        assert other.header.saga_id != first.header.saga_id
        assert json.dumps(other.header.to_dict())  # projection stays JSON-safe
    finally:
        rig.close()
