# pyright: reportAny=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""B04-2 (todo 23) — transactional outbox + idempotent event delivery.

Executable form of the review P1-7 fault matrix: a state change and its event
must commit in **one** owner transaction, delivery happens **after** that
commit, and a replay can never duplicate a fact or re-apply a state change.

Cases
-----
① CAS guard fails            -> state unchanged, no outbox event
② crash before the single   -> state and outbox roll back together (never an
  commit (after UPDATE /       event for an uncommitted state); a negative
  after outbox INSERT)         control shows the old "write after commit" branch
                               loses the fact (FAIL evidence)
③ crash after commit,        -> both exist, a fresh store delivers on restart
  before delivery
④ delivery success, crash    -> replayable, dedup by stable id, no duplicate
  before ack                    append and no state re-application
⑤ delivery retry             -> the legacy ``after_seq``/JSONL exit dedups the
                               same fact (never appended twice)
+ expired cursor             -> explicit snapshot reset, never a silent drop
+ permission revoked         -> the subscription is denied (revocation is live)

Real SQLite + real per-task JSONL in a tmp dir; crashes are injected through a
named fault point / the delivery port, never by rewriting product code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.store import JobStateConflictError, JobStore
from acp.services_events_bootstrap import (
    JobEventLogPort,
    JobStoreOutboxPort,
    open_outbox_delivery,
    open_persistent_event_service_for_path,
)
from acp_services.contracts.access import (
    AccessAction,
    AccessScope,
    DeploymentTier,
    LocalPrincipal,
    LocalSingleAccessPolicy,
    Principal,
    ReasonCode,
    ResourceGrant,
)
from acp_services.contracts.events import EventScope, OwnerType
from acp_services.runtime.outbox import (
    OutboxDeliveryError,
    OutboxDeliveryService,
    OutboxEventSpec,
    OutboxRecord,
)

_JOB = OwnerType.JOB
_SCOPE = EventScope.TASK


def _spec() -> JobSpec:
    return JobSpec(workflow="fake", name="t", input={"source": "CCO"})


def _record(root: Path, job_id: str, *, status: JobStatus = JobStatus.QUEUED) -> JobRecord:
    work_dir = root / "runs" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    return JobRecord(id=job_id, spec=_spec(), status=status, work_dir=str(work_dir))


def _db_path(root: Path) -> Path:
    return root / "acp_jobs.db"


def _event_path(root: Path, job_id: str) -> Path:
    return root / "runs" / job_id / "events.jsonl"


def _path_for(root: Path):
    def resolve(job_id: str) -> Path:
        return _event_path(root, job_id)

    return resolve


def _deliver(store: JobStore, root: Path, *, notifier=None) -> OutboxDeliveryService:
    return open_outbox_delivery(store, path_for=_path_for(root), notifier=notifier)


def _raising_hook(point: str):
    def hook(name: str) -> None:
        if name == point:
            raise RuntimeError(f"injected crash at {name}")

    return hook


def _spec_event(payload: dict) -> OutboxEventSpec:
    return OutboxEventSpec(event_type="state.changed", payload=payload)


# --------------------------------------------------------------------------- #
# ① CAS guard fails -> state unchanged, no outbox event
# --------------------------------------------------------------------------- #


def test_cas_guard_failure_writes_no_outbox_event(tmp_path: Path) -> None:
    store = JobStore(_db_path(tmp_path))
    store.create(_record(tmp_path, "j1"))
    before_revision = store.get("j1").revision  # type: ignore[union-attr]

    # Given: a stale expectation carrying an outbox event
    with pytest.raises(JobStateConflictError):
        store.transition(
            "j1",
            expected_status=JobStatus.QUEUED,
            expected_revision=99,
            status=JobStatus.RUNNING,
            outbox=_spec_event({"status": "running"}),
        )

    # Then: the row is untouched and no event was staged
    after = store.get("j1")
    assert after is not None
    assert after.status == JobStatus.QUEUED
    assert after.revision == before_revision
    assert store.has_pending_outbox() is False
    assert store.pending_outbox() == []
    assert JobEventLog(_event_path(tmp_path, "j1")).read_all() == []


# --------------------------------------------------------------------------- #
# ② crash before the single commit -> state and outbox roll back together
# --------------------------------------------------------------------------- #


def test_crash_after_update_rolls_back_state_and_outbox(tmp_path: Path) -> None:
    store = JobStore(_db_path(tmp_path))
    store.create(_record(tmp_path, "j2"))
    store._fault_point_hook = _raising_hook("transition.after_update")

    # When: the process "crashes" right after the UPDATE, before any commit
    with pytest.raises(RuntimeError, match="injected crash"):
        store.transition(
            "j2",
            expected_status=JobStatus.QUEUED,
            expected_revision=0,
            status=JobStatus.RUNNING,
            outbox=_spec_event({"status": "running"}),
        )

    # Then: state and outbox roll back together; no invented event survives
    after = store.get("j2")
    assert after is not None and after.status == JobStatus.QUEUED and after.revision == 0
    assert store.pending_outbox() == []
    assert JobEventLog(_event_path(tmp_path, "j2")).read_all() == []


def test_crash_after_outbox_insert_before_commit_rolls_back_both(tmp_path: Path) -> None:
    store = JobStore(_db_path(tmp_path))
    store.create(_record(tmp_path, "j2b"))
    # The outbox INSERT has run on the open transaction; the commit has not.
    store._fault_point_hook = _raising_hook("transition.before_commit")

    with pytest.raises(RuntimeError, match="injected crash"):
        store.transition(
            "j2b",
            expected_status=JobStatus.QUEUED,
            expected_revision=0,
            status=JobStatus.RUNNING,
            outbox=_spec_event({"status": "running"}),
        )

    after = store.get("j2b")
    assert after is not None and after.status == JobStatus.QUEUED and after.revision == 0
    assert store.pending_outbox() == [], "an uncommitted outbox row must not survive"


def test_control_legacy_write_after_commit_loses_the_fact(tmp_path: Path) -> None:
    """Negative control: the old 'commit state, then append event' order can lose
    the fact if the process dies between the two; the same-transaction outbox
    keeps the committed fact and its event together instead."""
    store = JobStore(_db_path(tmp_path))
    store.create(_record(tmp_path, "j2c"))
    log = JobEventLog(_event_path(tmp_path, "j2c"))

    # Legacy branch: state commits first, the event write is a later step.
    store.transition(
        "j2c",
        expected_status=JobStatus.QUEUED,
        expected_revision=0,
        status=JobStatus.RUNNING,
    )
    # <-- crash here, before the separate append
    assert store.get("j2c").status == JobStatus.RUNNING  # type: ignore[union-attr]
    assert log.read_all() == [], "committed fact lost its event (the outbox gap)"

    # Fixed branch (same transaction): the committed fact always has a pending
    # event row, so a restart can deliver it.
    store.create(_record(tmp_path, "j2d"))
    store.transition(
        "j2d",
        expected_status=JobStatus.QUEUED,
        expected_revision=0,
        status=JobStatus.RUNNING,
        outbox=_spec_event({"status": "running"}),
    )
    assert len(store.pending_outbox(job_id="j2d")) == 1


# --------------------------------------------------------------------------- #
# ③ crash after commit, before delivery -> restart delivers
# --------------------------------------------------------------------------- #


def test_crash_after_commit_before_delivery_restart_delivers(tmp_path: Path) -> None:
    db = _db_path(tmp_path)
    store = JobStore(db)
    store.create(_record(tmp_path, "j3"))

    stored = store.transition(
        "j3",
        expected_status=JobStatus.QUEUED,
        expected_revision=0,
        status=JobStatus.RUNNING,
        outbox=_spec_event({"status": "running"}),
    )
    log = JobEventLog(_event_path(tmp_path, "j3"))
    assert stored.status == JobStatus.RUNNING
    assert len(store.pending_outbox(job_id="j3")) == 1
    assert log.read_all() == [], "delivery has not run yet"

    # Restart: a fresh store over the same DB, then the delivery sweep.
    reopened = JobStore(db)
    result = _deliver(reopened, tmp_path).deliver_pending()

    assert result.delivered_count == 1
    assert result.failed == ()
    assert reopened.pending_outbox() == []
    records = log.read_all()
    assert [r["type"] for r in records] == ["state.changed"]
    assert records[0]["status"] == "running", "the event matches the committed fact"
    # Delivery never re-applied state.
    assert reopened.get("j3").revision == stored.revision  # type: ignore[union-attr]
    assert reopened.get("j3").status == JobStatus.RUNNING  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# ④ delivery success, crash before ack -> replayable + dedup, no state change
# --------------------------------------------------------------------------- #


class _UnackedOncePort:
    """Wrap the real outbox port and drop the first ack (crash before ack)."""

    def __init__(self, store: JobStore) -> None:
        self._inner = JobStoreOutboxPort(store)
        self.acked = 0

    def pending_outbox(self, *, job_id: str | None = None, limit: int | None = None):
        return self._inner.pending_outbox(job_id=job_id, limit=limit)

    def mark_outbox_delivered(self, event_id: str, *, seq: int, delivered_at: str) -> bool:
        if self.acked == 0:
            self.acked += 1
            return False  # process died before the ack landed
        return self._inner.mark_outbox_delivered(event_id, seq=seq, delivered_at=delivered_at)

    def mark_outbox_failed(self, event_id: str, *, error: str) -> None:
        self._inner.mark_outbox_failed(event_id, error=error)


def _service_with_port(store: JobStore, root: Path, port) -> OutboxDeliveryService:
    return OutboxDeliveryService(
        outbox=port,
        log_for=lambda job_id: JobEventLogPort(JobEventLog(_event_path(root, job_id))),
    )


def test_crash_before_ack_replays_without_duplicate_or_state_change(tmp_path: Path) -> None:
    store = JobStore(_db_path(tmp_path))
    store.create(_record(tmp_path, "j4"))
    stored = store.transition(
        "j4",
        expected_status=JobStatus.QUEUED,
        expected_revision=0,
        status=JobStatus.RUNNING,
        outbox=_spec_event({"status": "running"}),
    )
    log = JobEventLog(_event_path(tmp_path, "j4"))
    revision_after_commit = stored.revision

    # First delivery appends the fact but "crashes" before the ack.
    port = _UnackedOncePort(store)
    first = _service_with_port(store, tmp_path, port).deliver_pending()
    assert first.delivered_count == 1
    assert len(log.read_all()) == 1
    assert len(store.pending_outbox(job_id="j4")) == 1, "unacked row must stay pending"

    # Restart + retry: the same stable id dedups, the row is finally acked.
    second = _deliver(store, tmp_path).deliver_pending()
    assert second.delivered_count == 0, "the fact was already durable"
    assert len(second.duplicates) == 1, "the retry is recognised as a duplicate"
    assert second.pending == 0
    assert len(log.read_all()) == 1, "the same fact must never be appended twice"
    assert store.get("j4").revision == revision_after_commit  # type: ignore[union-attr]
    assert store.get("j4").status == JobStatus.RUNNING  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# ⑤ legacy after_seq / JSONL exit: a delivery retry dedups the same fact
# --------------------------------------------------------------------------- #


class _AlwaysPendingPort:
    """Report one row as pending regardless of its ack state (forced replay)."""

    def __init__(self, store: JobStore, event_id: str) -> None:
        self._inner = JobStoreOutboxPort(store)
        self._event_id = event_id

    def pending_outbox(self, *, job_id: str | None = None, limit: int | None = None):
        row = self._inner.pending_outbox(job_id=job_id, limit=None)
        if row:
            return row
        fetched = self._store_get()
        return [fetched] if fetched is not None else []

    def _store_get(self) -> OutboxRecord | None:
        return self._inner._store.get_outbox_event(self._event_id)  # noqa: SLF001

    def mark_outbox_delivered(self, event_id: str, *, seq: int, delivered_at: str) -> bool:
        return self._inner.mark_outbox_delivered(event_id, seq=seq, delivered_at=delivered_at)

    def mark_outbox_failed(self, event_id: str, *, error: str) -> None:
        self._inner.mark_outbox_failed(event_id, error=error)


def test_legacy_after_seq_exit_dedups_on_delivery_retry(tmp_path: Path) -> None:
    store = JobStore(_db_path(tmp_path))
    store.create(_record(tmp_path, "j5"))
    store.transition(
        "j5",
        expected_status=JobStatus.QUEUED,
        expected_revision=0,
        status=JobStatus.RUNNING,
        outbox=_spec_event({"status": "running"}),
    )
    log = JobEventLog(_event_path(tmp_path, "j5"))

    _deliver(store, tmp_path).deliver_pending()
    first_records = log.read_all()
    assert len(first_records) == 1
    # Legacy readers still see the ``type`` carrier + a stable idempotency key.
    assert first_records[0]["type"] == "state.changed"
    assert first_records[0]["idempotency_key"]

    # Force a replay of the *same* committed fact through the legacy exit.
    port = _AlwaysPendingPort(store, first_records[0].get("event_id", "j5:1:state.changed"))
    replay = _service_with_port(store, tmp_path, port)
    replay.deliver_pending()
    second_records = log.read_all()

    assert second_records == first_records, "delivery retry must not append again"


# --------------------------------------------------------------------------- #
# cursor alignment + old-version read path
# --------------------------------------------------------------------------- #


def test_snapshot_cursor_aligns_with_delivered_events(tmp_path: Path) -> None:
    store = JobStore(_db_path(tmp_path))
    store.create(_record(tmp_path, "j6"))

    snap_before = store.snapshot_with_cursor("j6")
    assert snap_before is not None and snap_before.cursor == 0 and snap_before.pending == 0

    stored = store.transition(
        "j6",
        expected_status=JobStatus.QUEUED,
        expected_revision=0,
        status=JobStatus.RUNNING,
        outbox=_spec_event({"status": "running"}),
    )
    snap_committed = store.snapshot_with_cursor("j6")
    assert snap_committed is not None
    assert snap_committed.record.revision == stored.revision
    assert snap_committed.pending == 1, "committed-not-delivered is visible to the subscriber"

    _deliver(store, tmp_path).deliver_pending()
    snap_delivered = store.snapshot_with_cursor("j6")
    assert snap_delivered is not None
    assert snap_delivered.cursor == 1
    assert snap_delivered.pending == 0

    # Replaying from the pre-change cursor yields exactly the new event once.
    service = open_persistent_event_service_for_path(
        _event_path(tmp_path, "j6"), owner_type=_JOB, owner_id="j6", scope=_SCOPE
    )
    replay = service.replay(snap_before.cursor)
    assert [e.event_type for e in replay.events] == ["state.changed"]
    assert replay.events[0].seq == snap_delivered.cursor


def test_expired_cursor_signals_snapshot_reset(tmp_path: Path) -> None:
    store = JobStore(_db_path(tmp_path))
    for index in range(5):
        job_id = f"e{index}"
        store.create(_record(tmp_path, job_id))
        store.transition(
            job_id,
            expected_status=JobStatus.QUEUED,
            expected_revision=0,
            status=JobStatus.RUNNING,
            outbox=_spec_event({"status": "running", "i": index}),
        )
        _deliver(store, tmp_path).deliver_pending(job_id=job_id)
    # all five events live in one owner log (reuse the first job's file)
    combined = _event_path(tmp_path, "e0")
    combined.parent.mkdir(parents=True, exist_ok=True)
    with combined.open("w", encoding="utf-8") as handle:
        for index in range(5):
            handle.write((_event_path(tmp_path, f"e{index}")).read_text(encoding="utf-8"))

    service = open_persistent_event_service_for_path(
        combined, owner_type=_JOB, owner_id="e0", scope=_SCOPE, retention=2
    )
    replay = service.replay(1)
    assert replay.reset is True
    assert replay.reset_reason == "cursor_expired"
    assert replay.events == ()


# --------------------------------------------------------------------------- #
# permission revoked -> the subscription is denied
# --------------------------------------------------------------------------- #


def test_permission_revoked_denies_subscription() -> None:
    principal: Principal = LocalPrincipal.resolve()
    scope = AccessScope(
        scope_id="scope-job-events",
        tenant_id=principal.tenant_id,
        action=AccessAction.READ,
        resource_kinds=("job",),
        project_ids=("proj-1",),
    )
    grant = ResourceGrant(
        grant_id="grant-1",
        principal_id=principal.principal_id,
        scope_id=scope.scope_id,
        resource_id="proj-1",
        actions=(AccessAction.READ,),
        granted_at="2026-10-18T00:00:00+00:00",
    )
    policy = LocalSingleAccessPolicy(grants=[grant], scopes=[scope])

    allowed = policy.authorize(principal, scope, resource_id="proj-1", action=AccessAction.READ)
    assert allowed.effect == "allow"
    assert principal.deployment_tier == DeploymentTier.LOCAL_SINGLE

    policy.revoke("grant-1")
    denied = policy.authorize(principal, scope, resource_id="proj-1", action=AccessAction.READ)
    assert denied.effect == "deny"
    assert denied.reason_code == ReasonCode.REVOKED


# --------------------------------------------------------------------------- #
# happy path: every event corresponds to a committed fact
# --------------------------------------------------------------------------- #


def test_happy_path_events_match_committed_facts(tmp_path: Path) -> None:
    store = JobStore(_db_path(tmp_path))
    store.create(_record(tmp_path, "h1"))
    seen: list[str] = []

    def notifier(envelope) -> None:  # noqa: ANN001 - test spy
        seen.append(envelope.event_type)

    store.transition(
        "h1",
        expected_status=JobStatus.QUEUED,
        expected_revision=0,
        status=JobStatus.RUNNING,
        outbox=_spec_event({"status": "running"}),
    )
    result = _deliver(store, tmp_path, notifier=notifier).deliver_pending()

    assert result.delivered_count == 1
    assert seen == ["state.changed"], "the SSE notify runs after the durable append"
    record = store.get("h1")
    assert record is not None and record.status == JobStatus.RUNNING
    events = JobEventLog(_event_path(tmp_path, "h1")).read_all()
    assert [(e["type"], e["status"]) for e in events] == [("state.changed", "running")]


def test_delivery_port_failures_are_retryable(tmp_path: Path) -> None:
    """A delivery error keeps the row pending (no silent drop)."""
    store = JobStore(_db_path(tmp_path))
    store.create(_record(tmp_path, "r1"))
    store.transition(
        "r1",
        expected_status=JobStatus.QUEUED,
        expected_revision=0,
        status=JobStatus.RUNNING,
        outbox=_spec_event({"status": "running"}),
    )

    class _FailPort:
        def __init__(self) -> None:
            self._inner = JobStoreOutboxPort(store)

        def pending_outbox(self, *, job_id: str | None = None, limit: int | None = None):
            return self._inner.pending_outbox(job_id=job_id, limit=limit)

        def mark_outbox_delivered(self, event_id: str, *, seq: int, delivered_at: str) -> bool:
            return self._inner.mark_outbox_delivered(event_id, seq=seq, delivered_at=delivered_at)

        def mark_outbox_failed(self, event_id: str, *, error: str) -> None:
            self._inner.mark_outbox_failed(event_id, error=error)

    def _boom(_job_id: str):
        raise OutboxDeliveryError("durable log unavailable")

    service = OutboxDeliveryService(outbox=_FailPort(), log_for=_boom)
    result = service.deliver_pending()

    assert result.delivered_count == 0
    assert result.failed and result.failed[0][0]
    row = store.get_outbox_event(store.pending_outbox()[0].event_id)
    assert row is not None and row.is_delivered is False
    assert row.delivery_attempts == 1
