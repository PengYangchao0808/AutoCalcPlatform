"""B04-3 (todo 24) — authorized reusable event stream + snapshot reset.

Acceptance clauses:

1. **reuse** — exactly one stream per ``(principal, scope)``; a second task in
   the same scope joins the existing stream (``is`` identity, ``active_count``
   stays 1) and events from both owners flow on one poll;
2. **revocation is immediate** — a live stream closes at the next authorized
   check and its next poll returns an explicit ``permission_changed`` reset; a
   new connection for a denied subscription is refused and leaves no stream;
3. **snapshot reset** — expired cursor, permission change, subscription change
   and service restart each yield ``reset=True`` with a typed reason and a
   full-reread ``snapshot()`` (never a silent empty success);
4. **cross-scope reuse denied** — a cursor from another principal/scope raises
   instead of being silently reinterpreted;
5. **coverage matrix** — the four categories (log / task progress / task
   summary / data availability) carry sample sequences and leave the polling
   degradation decision to A;
6. **old polling compatibility** — the legacy per-job SSE endpoints are
   unchanged and still serve events.

Run with: pytest tests/test_services_events_authz.py -q
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.services_events_bootstrap import (
    JobEventLogPort,
    open_persistent_event_service_for_path,
)
from acp_services.contracts.events import EventScope, OwnerType
from acp_services.runtime.events import (
    RESET_CURSOR_EXPIRED,
    RESET_PERMISSION_CHANGED,
    RESET_SERVICE_RESTART,
    RESET_SUBSCRIPTION_CHANGED,
    AuthorizedEventStream,
    AuthorizedStreamRegistry,
    EventCategory,
    StreamAuthorization,
    StreamAuthorizationError,
    StreamCursor,
    StreamScopeMismatchError,
    classify_event,
    coverage_matrix,
)

PRINCIPAL = "principal-local"
TASK = EventScope.TASK
PROJECT = EventScope.PROJECT


class _Authorizer:
    """Subscription authorizer double with a flippable revocation switch."""

    def __init__(self, *, allowed: bool = True, principal_id: str = PRINCIPAL) -> None:
        self.allowed = allowed
        self.principal_id = principal_id

    def authorize_stream(self, *, principal_id: str, scope: EventScope, job_id: str = ""):
        return StreamAuthorization(
            allowed=self.allowed,
            principal_id=self.principal_id,
            scope=scope,
            reason_code="granted" if self.allowed else "revoked",
        )


class _Logs:
    """Per-owner durable logs backed by real ``JobEventLog`` JSONL files."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def port(self, job_id: str) -> JobEventLogPort:
        path = self._root / job_id / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return JobEventLogPort(JobEventLog(path))

    def write(self, job_id: str, event_type: str, *, seq_key: str | None = None) -> None:
        service = open_persistent_event_service_for_path(
            self._root / job_id / "events.jsonl",
            owner_type=OwnerType.JOB,
            owner_id=job_id,
            scope=TASK,
        )
        service.append(event_type, dedup_key=seq_key)


def _stream(
    logs: _Logs,
    *,
    principal_id: str = PRINCIPAL,
    scope: EventScope = TASK,
    epoch: str = "epoch-1",
    retention: int | None = None,
    job_ids: tuple[str, ...] = (),
) -> AuthorizedEventStream:
    stream = AuthorizedEventStream(
        principal_id=principal_id,
        scope=scope,
        service_epoch=epoch,
        log_for=lambda job_id: logs.port(job_id),
        retention=retention,
    )
    if job_ids:
        stream.subscribe(job_ids)
    return stream


def _cursor(job_id: str, after_seq: int = 0, *, scope: EventScope = TASK, epoch: str = "epoch-1"):
    return StreamCursor(
        principal_id=PRINCIPAL,
        scope=scope,
        job_id=job_id,
        after_seq=after_seq,
        service_epoch=epoch,
    )


# ---------------------------------------------------------------------------
# (1) reuse — one stream per (principal, scope), many tasks
# ---------------------------------------------------------------------------


def test_registry_reuses_one_stream_across_tasks(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    registry = AuthorizedStreamRegistry()

    first = registry.open(
        principal_id=PRINCIPAL,
        scope=TASK,
        service_epoch="epoch-1",
        log_for=logs.port,
        job_ids=("job-a",),
    )
    second = registry.open(
        principal_id=PRINCIPAL,
        scope=TASK,
        service_epoch="epoch-1",
        log_for=logs.port,
        job_ids=("job-b",),
    )

    assert second is first
    assert registry.active_count == 1
    assert first.task_ids == ("job-a", "job-b")


def test_one_poll_carries_events_from_both_owners(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    logs.write("job-a", "job.started", seq_key="a1")
    logs.write("job-b", "progress", seq_key="b1")
    logs.write("job-a", "job.completed", seq_key="a2")

    stream = _stream(logs, job_ids=("job-a", "job-b"))
    batch = stream.poll([_cursor("job-a"), _cursor("job-b")])

    assert {event.owner_id for event in batch.events} == {"job-a", "job-b"}
    assert [event.event_type for event in batch.events].count("job.completed") == 1
    assert batch.cursors == {"job-a": 2, "job-b": 1}


def test_distinct_scopes_get_distinct_streams(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    registry = AuthorizedStreamRegistry()
    task_stream = registry.open(
        principal_id=PRINCIPAL,
        scope=TASK,
        service_epoch="epoch-1",
        log_for=logs.port,
        job_ids=("job-a",),
    )
    project_stream = registry.open(
        principal_id=PRINCIPAL,
        scope=PROJECT,
        service_epoch="epoch-1",
        log_for=logs.port,
        job_ids=("job-a",),
    )

    assert project_stream is not task_stream
    assert registry.active_count == 2


# ---------------------------------------------------------------------------
# (2) subscription authorization + immediate revocation
# ---------------------------------------------------------------------------


def test_new_connection_refused_when_subscription_denied(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    registry = AuthorizedStreamRegistry()

    with pytest.raises(StreamAuthorizationError):
        registry.open(
            principal_id=PRINCIPAL,
            scope=TASK,
            service_epoch="epoch-1",
            log_for=logs.port,
            job_ids=("job-a",),
            authorizer=_Authorizer(allowed=False),
        )

    assert registry.active_count == 0


def test_live_revocation_closes_stream_immediately(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    logs.write("job-a", "progress", seq_key="a1")
    authorizer = _Authorizer()
    stream = _stream(logs, job_ids=("job-a",))
    assert stream.check_authorization(authorizer) is True

    authorizer.allowed = False

    assert stream.check_authorization(authorizer) is False
    assert stream.closed is True
    assert stream.closed_reason == RESET_PERMISSION_CHANGED

    batch = stream.poll([_cursor("job-a")])
    assert batch.reset is True
    assert batch.reset_reason == RESET_PERMISSION_CHANGED
    assert batch.events == ()


def test_registry_open_refuses_after_live_revocation(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    registry = AuthorizedStreamRegistry()
    authorizer = _Authorizer()
    registry.open(
        principal_id=PRINCIPAL,
        scope=TASK,
        service_epoch="epoch-1",
        log_for=logs.port,
        job_ids=("job-a",),
        authorizer=authorizer,
    )
    authorizer.allowed = False

    with pytest.raises(StreamAuthorizationError):
        registry.open(
            principal_id=PRINCIPAL,
            scope=TASK,
            service_epoch="epoch-1",
            log_for=logs.port,
            job_ids=("job-a",),
            authorizer=authorizer,
        )
    assert registry.get(PRINCIPAL, TASK) is None


# ---------------------------------------------------------------------------
# (3) snapshot reset — expired cursor / permission / subscription / restart
# ---------------------------------------------------------------------------


def test_expired_cursor_signals_snapshot_reset(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    for index in range(5):
        logs.write("job-a", "progress", seq_key=f"a{index}")
    stream = _stream(logs, retention=2, job_ids=("job-a",))

    batch = stream.poll([_cursor("job-a", 1)])

    assert batch.reset is True
    assert batch.reset_reason == RESET_CURSOR_EXPIRED
    assert batch.events == ()
    snapshot = stream.snapshot()
    assert [event.seq for event in snapshot.events] == [1, 2, 3, 4, 5]


def test_permission_change_reset_clears_cursor(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    logs.write("job-a", "progress", seq_key="a1")
    stream = _stream(logs, job_ids=("job-a",))

    stream.reset(RESET_PERMISSION_CHANGED)
    batch = stream.poll([_cursor("job-a")])

    assert batch.reset is True
    assert batch.reset_reason == RESET_PERMISSION_CHANGED
    assert batch.cursors == {}


def test_subscription_change_signals_reset(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    stream = _stream(logs, job_ids=("job-a",))
    stream.subscribe(["job-b"])

    stream.unsubscribe(["job-b"])
    batch = stream.poll([_cursor("job-a")])

    assert batch.reset is True
    assert batch.reset_reason == RESET_SUBSCRIPTION_CHANGED


def test_service_restart_signals_reset(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    logs.write("job-a", "job.completed", seq_key="a1")
    stream = _stream(logs, epoch="epoch-2", job_ids=("job-a",))

    batch = stream.poll([_cursor("job-a", epoch="epoch-1")])

    assert batch.reset is True
    assert batch.reset_reason == RESET_SERVICE_RESTART
    assert stream.closed is True


def test_registry_opens_fresh_stream_after_restart(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    registry = AuthorizedStreamRegistry()
    old = registry.open(
        principal_id=PRINCIPAL,
        scope=TASK,
        service_epoch="epoch-1",
        log_for=logs.port,
        job_ids=("job-a",),
    )
    new = registry.open(
        principal_id=PRINCIPAL,
        scope=TASK,
        service_epoch="epoch-2",
        log_for=logs.port,
        job_ids=("job-a",),
    )

    assert new is not old
    assert old.closed is True
    assert old.closed_reason == RESET_SERVICE_RESTART
    assert new.poll([_cursor("job-a", epoch="epoch-1")]).reset_reason == RESET_SERVICE_RESTART


# ---------------------------------------------------------------------------
# (4) cross-scope reuse denied
# ---------------------------------------------------------------------------


def test_cross_scope_cursor_reuse_is_denied(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    stream = _stream(logs, job_ids=("job-a",))

    with pytest.raises(StreamScopeMismatchError):
        stream.poll([_cursor("job-a", scope=PROJECT)])


def test_cross_principal_cursor_reuse_is_denied(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    stream = _stream(logs, job_ids=("job-a",))
    foreign = StreamCursor(
        principal_id="other-principal",
        scope=TASK,
        job_id="job-a",
        service_epoch="epoch-1",
    )

    with pytest.raises(StreamScopeMismatchError):
        stream.poll([foreign])


# ---------------------------------------------------------------------------
# (5) coverage matrix declaration
# ---------------------------------------------------------------------------


def test_coverage_matrix_declares_four_categories_with_samples() -> None:
    matrix = coverage_matrix()

    assert {row.category for row in matrix} == set(EventCategory)
    assert len(matrix) == 4
    for row in matrix:
        assert row.sample_sequence
        assert row.channels
        assert row.reset_on
        assert row.polling_degradation == "a_side_decision"


def test_event_classification_covers_all_four_categories() -> None:
    assert classify_event("log") is EventCategory.LOG
    assert classify_event("stage.started") is EventCategory.TASK_PROGRESS
    assert classify_event("job.completed") is EventCategory.TASK_SUMMARY
    assert classify_event("pending_fetch") is EventCategory.DATA_AVAILABILITY
    assert classify_event("some.unknown.fact") is EventCategory.TASK_SUMMARY


def test_bounded_poll_never_skips_a_fact(tmp_path: Path) -> None:
    logs = _Logs(tmp_path)
    for index in range(3):
        logs.write("job-a", "progress", seq_key=f"a{index}")
    stream = _stream(logs, job_ids=("job-a",))

    first = stream.poll([_cursor("job-a")], limit=1)
    assert [event.seq for event in first.events] == [1]
    assert first.cursors == {"job-a": 1}

    second = stream.poll([_cursor("job-a", first.cursors["job-a"])], limit=1)
    assert [event.seq for event in second.events] == [2]


# ---------------------------------------------------------------------------
# (6) HTTP surface + old polling compatibility
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as test_client:
        yield test_client


def _seed_terminal_job(client: TestClient, tmp_path: Path, job_id: str, events: int = 2) -> None:
    manager = client.app.state.job_manager
    work_dir = tmp_path / job_id
    runtime_dir = work_dir / "WORK" / "00_RUNTIME"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    manager.store.create(
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="fake", name=job_id),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            progress=1.0,
            exit_code=0,
        )
    )
    log = JobEventLog(runtime_dir / "events.jsonl")
    for index in range(events):
        log.append("progress", index=index)


def _sse_event_names(text: str) -> list[str]:
    return [line[7:] for line in text.splitlines() if line.startswith("event: ")]


def test_scope_stream_multiplexes_two_tasks_over_one_connection(
    client: TestClient, tmp_path: Path
) -> None:
    _seed_terminal_job(client, tmp_path, "scope-a")
    _seed_terminal_job(client, tmp_path, "scope-b")

    response = client.get("/api/events/stream?scope=task&job_ids=scope-a,scope-b")

    assert response.status_code == 200
    body = response.text
    assert "id: scope-a:" in body
    assert "id: scope-b:" in body
    assert _sse_event_names(body).count("done") == 2
    registry = client.app.state.authorized_event_streams
    assert registry.active_count == 1

    # Same principal+scope: the second request reuses the one stream.
    client.get("/api/events/stream?scope=task&job_ids=scope-a,scope-b")
    assert registry.active_count == 1


def test_scope_stream_denies_when_authorizer_denies(client: TestClient, tmp_path: Path) -> None:
    _seed_terminal_job(client, tmp_path, "scope-c")
    from acp_services.contracts.access import LocalSingleAccessPolicy
    from acp_services.workspace.entry_authz import EntryAuthorizer

    client.app.state.entry_authorizer = EntryAuthorizer(
        require_grant=True, policy=LocalSingleAccessPolicy()
    )

    response = client.get("/api/events/stream?scope=task&job_ids=scope-c")

    assert response.status_code == 403
    registry = getattr(client.app.state, "authorized_event_streams", None)
    assert registry is None or registry.active_count == 0


def test_old_polling_endpoints_still_work(client: TestClient, tmp_path: Path) -> None:
    _seed_terminal_job(client, tmp_path, "legacy-a")

    with client.stream("GET", "/api/v1/jobs/legacy-a/events") as response:
        assert response.status_code == 200
    with client.stream("GET", "/api/jobs/legacy-a/events?after_seq=0") as response:
        assert response.status_code == 200
        assert "event: progress" in "".join(response.iter_text())
