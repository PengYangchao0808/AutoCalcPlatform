# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false
"""CAS state transitions, narrow progress updates, and jobs revision/attempt columns."""

from __future__ import annotations

import ast
import json
import sqlite3
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import acp.scheduler.manager as manager_module
from acp.scheduler import migrations as migrations_module
from acp.scheduler.job_edit import attempt_number, compute_source_revision
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.migrations import migrate
from acp.scheduler.remote.runner import RemoteJobRunner
from acp.scheduler.store import JobStateConflictError, JobStore


def _spec(name: str = "t") -> JobSpec:
    return JobSpec(workflow="fake", name=name, input={"source": "CCO"})


def _record(job_id: str, **overrides: object) -> JobRecord:
    kwargs: dict[str, object] = {
        "spec": _spec(),
        "status": JobStatus.QUEUED,
        "work_dir": f"/tmp/{job_id}",
    }
    kwargs.update(overrides)
    return JobRecord(id=job_id, **kwargs)  # pyright: ignore[reportArgumentType]


def _raw_row(db: Path, job_id: str) -> dict[str, object]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row is not None, f"row {job_id} missing"
        return {key: row[key] for key in row.keys()}
    finally:
        conn.close()


def _insert_legacy_row(
    db: Path, job_id: str, *, result_json: str | None, spec: dict[str, object] | None = None
) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "INSERT INTO jobs (id, workflow, name, status, work_dir, spec_json, "
            "created_at, updated_at, result_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                "fake",
                "legacy",
                "completed",
                f"/tmp/{job_id}",
                json.dumps(spec if spec is not None else {"workflow": "fake", "name": "legacy"}),
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                result_json,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_transition_bumps_revision_monotonically(tmp_path: Path) -> None:
    db = tmp_path / "cas.db"
    store = JobStore(db)
    store.create(_record("cas-1"))
    assert store.get("cas-1") is not None
    assert store.get("cas-1").revision == 0  # pyright: ignore[reportAttributeAccessIssue]

    first = store.transition(
        "cas-1", expected_status=JobStatus.QUEUED, expected_revision=0, status=JobStatus.RUNNING
    )
    assert first.revision == 1
    assert first.status == JobStatus.RUNNING

    second = store.transition(
        "cas-1",
        expected_status=JobStatus.RUNNING,
        expected_revision=1,
        status=JobStatus.PAUSED,
        progress=0.5,
    )
    assert second.revision == 2
    got = store.get("cas-1")
    assert got is not None
    assert got.revision == 2
    assert got.status == JobStatus.PAUSED
    assert got.progress == 0.5


def test_stale_transition_conflict_leaves_row_unchanged(tmp_path: Path) -> None:
    db = tmp_path / "stale.db"
    store = JobStore(db)
    store.create(_record("stale-1"))
    store.transition(
        "stale-1", expected_status=JobStatus.QUEUED, expected_revision=0, status=JobStatus.RUNNING
    )
    before = _raw_row(db, "stale-1")

    with pytest.raises(JobStateConflictError) as excinfo:
        store.transition(
            "stale-1",
            expected_status=JobStatus.RUNNING,
            expected_revision=0,
            status=JobStatus.CANCELLED,
        )
    err = excinfo.value
    assert err.job_id == "stale-1"
    assert err.expected["revision"] == 0
    assert err.actual is not None
    assert err.actual["revision"] == 1
    assert err.actual["status"] == "running"

    after = _raw_row(db, "stale-1")
    assert after == before, "conflicting transition must not modify the row"
    assert after["revision"] == 1, "revision must not bump on conflict"


def test_transition_rejects_spec_and_unknown_fields(tmp_path: Path) -> None:
    db = tmp_path / "spec.db"
    store = JobStore(db)
    store.create(_record("s1"))
    before_spec = _raw_row(db, "s1")["spec_json"]

    with pytest.raises(ValueError):
        store.transition("s1", expected_status=None, expected_revision=0, spec=_spec(name="hacked"))
    with pytest.raises(ValueError):
        store.transition("s1", expected_status=None, expected_revision=0, spec_json="{}")
    with pytest.raises(ValueError):
        store.transition("s1", expected_status=None, expected_revision=0, attempt=99)

    after = _raw_row(db, "s1")
    assert after["spec_json"] == before_spec, "transition must never write spec"
    assert after["revision"] == 0
    assert after["attempt"] == 1


def test_update_progress_narrow_preserves_state(tmp_path: Path) -> None:
    db = tmp_path / "prog.db"
    store = JobStore(db)
    store.create(
        _record(
            "p1",
            status=JobStatus.RUNNING,
            progress=0.1,
            result={"state": {"stage": 1}},
        )
    )
    before = _raw_row(db, "p1")

    out = store.update_progress("p1", expected_revision=0, progress=0.42, current_stage="opt")
    after = _raw_row(db, "p1")
    assert after["status"] == before["status"], "progress update must not touch status"
    assert after["spec_json"] == before["spec_json"], "progress update must not touch spec"
    assert after["result_json"] == before["result_json"], "result must stay byte-identical"
    assert out.progress == 0.42
    assert out.current_stage == "opt"
    assert out.status == JobStatus.RUNNING
    assert out.revision == 1

    out2 = store.update_progress("p1", expected_revision=1, result={"state": {"stage": 2}})
    after2 = _raw_row(db, "p1")
    assert after2["result_json"] != before["result_json"], "explicit result must be written"
    assert after2["status"] == before["status"]
    assert after2["spec_json"] == before["spec_json"]
    assert out2.revision == 2

    with pytest.raises(JobStateConflictError):
        store.update_progress("p1", expected_revision=1, progress=0.99)
    assert _raw_row(db, "p1")["revision"] == 2, "conflict must not bump revision"


def test_concurrent_transition_single_winner(tmp_path: Path) -> None:
    db = tmp_path / "race.db"
    store_a = JobStore(db)
    store_b = JobStore(db)
    store_a.create(_record("race-1"))
    barrier = threading.Barrier(2, timeout=10)
    outcome: dict[str, object] = {}

    def run(store: JobStore, key: str, status: JobStatus) -> None:
        barrier.wait()
        try:
            outcome[key] = store.transition(
                "race-1", expected_status=JobStatus.QUEUED, expected_revision=0, status=status
            )
        except JobStateConflictError as exc:
            outcome[key] = exc

    t1 = threading.Thread(target=run, args=(store_a, "a", JobStatus.RUNNING))
    t2 = threading.Thread(target=run, args=(store_b, "b", JobStatus.PAUSED))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    assert not t1.is_alive() and not t2.is_alive()
    assert len(outcome) == 2

    winners = [value for value in outcome.values() if isinstance(value, JobRecord)]
    conflicts = [value for value in outcome.values() if isinstance(value, JobStateConflictError)]
    assert len(winners) == 1, "exactly one transition must win the CAS"
    assert len(conflicts) == 1, "the loser must raise JobStateConflictError"

    final = store_a.get("race-1")
    assert final is not None
    assert final.revision == 1
    assert final.status == winners[0].status, "DB must equal the winner's write"
    assert _raw_row(db, "race-1")["status"] == final.status.value


def test_requeue_with_spec_attempt_and_source_revision(tmp_path: Path) -> None:
    db = tmp_path / "rq.db"
    store = JobStore(db)
    store.create(
        _record(
            "rq-1",
            status=JobStatus.FAILED,
            error="boom",
            pid=42,
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:01:00+00:00",
            current_stage="sp",
            progress=0.9,
            exit_code=2,
            remote_job_id="123",
            result={"attempts": 1, "remote": {"node": "n1"}},
        )
    )
    before_revision = compute_source_revision(store.get("rq-1"))  # pyright: ignore[reportArgumentType]
    new_spec = _spec(name="t2")

    out = store.requeue_with_spec(
        "rq-1",
        new_spec=new_spec,
        expected_revision=0,
        expected_attempt=1,
        expected_status=JobStatus.FAILED,
    )
    assert out.status == JobStatus.QUEUED
    assert out.attempt == 2, "attempt must increment 1-based"
    assert out.revision == 1
    assert out.spec.name == "t2"
    assert (
        out.started_at,
        out.completed_at,
        out.current_stage,
        out.progress,
        out.error,
        out.pid,
        out.exit_code,
        out.remote_job_id,
    ) == (None, None, None, None, None, None, None, None), "runtime fields must clear"
    assert out.result == {"attempts": 1, "remote": {"node": "n1"}}, "result_json stays unless passed"
    assert json.loads(str(_raw_row(db, "rq-1")["spec_json"])) == new_spec.to_dict()

    reread = store.get("rq-1")
    assert reread is not None
    assert attempt_number(reread) == 2
    assert attempt_number(reread) == attempt_number(out)
    assert compute_source_revision(reread) == compute_source_revision(out)
    assert compute_source_revision(reread) != before_revision

    with pytest.raises(JobStateConflictError):
        store.requeue_with_spec(
            "rq-1",
            new_spec=new_spec,
            expected_revision=1,
            expected_attempt=1,
            expected_status=JobStatus.QUEUED,
        )
    with pytest.raises(ValueError):
        store.requeue_with_spec(
            "rq-1",
            new_spec=new_spec,
            expected_revision=1,
            expected_attempt=2,
            expected_status=JobStatus.QUEUED,
            status=JobStatus.RUNNING,
        )
    with pytest.raises(TypeError):
        store.requeue_with_spec(
            "rq-1",
            new_spec={"workflow": "fake"},  # pyright: ignore[reportArgumentType]
            expected_revision=1,
            expected_attempt=2,
            expected_status=JobStatus.QUEUED,
        )
    final = store.get("rq-1")
    assert final is not None
    assert final.attempt == 2
    assert final.status == JobStatus.QUEUED


def test_fresh_db_has_revision_attempt_columns(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    JobStore(db)
    conn = sqlite3.connect(str(db))
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    finally:
        conn.close()
    assert "revision" in columns, "fresh jobs table must carry revision"
    assert "attempt" in columns, "fresh jobs table must carry attempt"


def test_legacy_insert_without_new_columns_reads_default(tmp_path: Path) -> None:
    db = tmp_path / "legacy_default.db"
    store = JobStore(db)
    _insert_legacy_row(db, "legacy-1", result_json=None)
    rec = store.get("legacy-1")
    assert rec is not None
    assert rec.attempt == 1, "legacy INSERT without attempt must read 1"
    assert rec.revision == 0
    assert attempt_number(rec) == 1


def test_migration_backfills_legacy_attempts(tmp_path: Path) -> None:
    db = tmp_path / "backfill.db"
    pre020 = [m for m in migrations_module._MIGRATIONS if m["id"] != "020"]
    with patch.object(migrations_module, "_MIGRATIONS", pre020):
        JobStore(db)

    _insert_legacy_row(db, "legacy-3", result_json=json.dumps({"attempts": 3}))
    _insert_legacy_row(db, "legacy-bad", result_json="{not json")
    _insert_legacy_row(db, "legacy-none", result_json=json.dumps({}))

    migrate(db)
    store = JobStore(db)
    assert store.get("legacy-3") is not None
    assert store.get("legacy-3").attempt == 3, "backfill must read result attempts=3"
    assert _raw_row(db, "legacy-bad")["attempt"] == 1, "invalid JSON must fall back to 1"
    assert store.get("legacy-none") is not None
    assert store.get("legacy-none").attempt == 1, "missing attempts must fall back to 1"


def test_attempt_number_column_authoritative() -> None:
    rec = _record("an-1")
    assert rec.attempt == 1
    assert attempt_number(rec) == 1
    rec.result = {"attempts": 3}
    assert attempt_number(rec) == 3, "column at default defers to legacy result"
    rec.attempt = 2
    assert attempt_number(rec) == 2, "column beyond default is authoritative"
    rec2 = _record("an-2")
    rec2.result = {"attempts": 0}
    assert attempt_number(rec2) == 1


# ====================================================================== #
# Todo 2: poll split — CAS state transitions, narrow progress writes,
# terminal-persist-first + idempotent side effects, reconcile loop.
# ====================================================================== #


def _make_manager(tmp_path: Path) -> JobManager:
    return JobManager(run_root=tmp_path, poll_interval=30)


def _seed_running_job(
    mgr: JobManager, tmp_path: Path, job_id: str, **overrides: object
) -> JobRecord:
    work_dir = tmp_path / "runs" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, object] = {
        "spec": _spec(),
        "status": JobStatus.RUNNING,
        "work_dir": str(work_dir),
    }
    kwargs.update(overrides)
    record = JobRecord(id=job_id, **kwargs)  # pyright: ignore[reportArgumentType]
    mgr.store.create(record)
    return record


def _remote_runner(lsf_status: str) -> RemoteJobRunner:
    """Real RemoteJobRunner with a mocked monitor (see _poll_remote_runner)."""
    monitor = MagicMock()
    monitor.get_exit_code.return_value = None
    monitor.get_lsf_status.return_value = lsf_status
    monitor.find_remote_state_json.return_value = None
    monitor.tail_stdout.return_value = ("", 0)
    monitor.tail_stderr.return_value = ("", 0)
    runner = RemoteJobRunner(
        ssh_pool=MagicMock(),
        remote_config=MagicMock(),
        stager=MagicMock(),
        monitor=monitor,
        code_syncer=MagicMock(),
        poll_interval=0,
    )
    return runner


def _seed_job_state(runner: RemoteJobRunner, job_id: str) -> None:
    runner._job_states[job_id] = {
        "node": MagicMock(),
        "remote_job_dir": f"/scratch/acp/{job_id}",
        "lsf_job_id": "777",
        "stdout_offset": 0,
        "stderr_offset": 0,
        "poll_cycle": 0,
        "seen_stages": set(),
    }


def _event_types(mgr: JobManager, job_id: str) -> list[str]:
    log = mgr.event_log(job_id)
    return [] if log is None else [e["type"] for e in log.read_all()]


def test_stale_poll_cannot_override_pause(tmp_path: Path) -> None:
    """Probe lifecycle (fixed): a poll that reads RUNNING but pauses
    mid-flight must drop its stale observation — DB stays PAUSED and the
    drop is audited with ``job.poll_dropped_stale``."""
    mgr = _make_manager(tmp_path)
    try:
        record = _seed_running_job(mgr, tmp_path, "race")
        mgr.runner.pause_local = lambda job_id: True  # type: ignore[method-assign]
        seen: list[str] = []

        def poll(stale_record: JobRecord) -> tuple[bool, int | None]:
            mgr.pause_job(record.id)
            seen.append(mgr.store.get(record.id).status.value)  # type: ignore[union-attr]
            stale_record.progress = 0.55
            stale_record.current_stage = "sampling"
            return (False, None)

        mgr.runner.poll = poll  # type: ignore[method-assign]
        mgr._poll_job(record.id)

        final = mgr.store.get(record.id)
        assert final is not None
        assert seen == [JobStatus.PAUSED.value], "pause must win during the poll"
        assert final.status == JobStatus.PAUSED, "stale poll must not resurrect RUNNING"
        assert final.progress != 0.55, "stale progress observation must be dropped"
        assert "job.poll_dropped_stale" in _event_types(mgr, record.id)
        assert "job.paused" in _event_types(mgr, record.id)
    finally:
        mgr.shutdown()


def test_terminal_not_resurrected(tmp_path: Path) -> None:
    """Concurrent terminal poll vs pause: the first writer wins — a terminal
    observation loaded before a pause must not persist COMPLETED nor run
    terminal side effects."""
    mgr = _make_manager(tmp_path)
    try:
        record = _seed_running_job(mgr, tmp_path, "term-race")
        mgr.runner.pause_local = lambda job_id: True  # type: ignore[method-assign]

        def poll(stale_record: JobRecord) -> tuple[bool, int | None]:
            mgr.pause_job(record.id)
            stale_record.exit_code = 0
            return (True, 0)

        mgr.runner.poll = poll  # type: ignore[method-assign]
        mgr._poll_job(record.id)

        final = mgr.store.get(record.id)
        assert final is not None
        assert final.status == JobStatus.PAUSED, "terminal observation must lose to pause"
        assert final.completed_at is None, "no terminal timestamps on the losing writer"
        assert not (final.result or {}).get("terminal_side_effects_done"), (
            "side effects must not run for a dropped terminal observation"
        )
        assert "job.poll_dropped_stale" in _event_types(mgr, record.id)
    finally:
        mgr.shutdown()


def test_progress_update_preserves_status_spec_result(tmp_path: Path) -> None:
    """A non-terminal poll persists through the narrow progress API: status,
    spec_json and result_json stay byte-identical, revision bumps."""
    mgr = _make_manager(tmp_path)
    try:
        record = _seed_running_job(
            mgr, tmp_path, "prog-keep", progress=0.1, result={"state": {"stage": 1}}
        )
        before = _raw_row(tmp_path / "acp_jobs.db", record.id)

        def poll(stale_record: JobRecord) -> tuple[bool, int | None]:
            stale_record.progress = 0.42
            stale_record.current_stage = "opt"
            return (False, None)

        mgr.runner.poll = poll  # type: ignore[method-assign]
        mgr._poll_job(record.id)

        after = _raw_row(tmp_path / "acp_jobs.db", record.id)
        assert after["status"] == before["status"] == JobStatus.RUNNING.value
        assert after["spec_json"] == before["spec_json"], "progress writes never touch spec"
        assert after["result_json"] == before["result_json"], "result must stay byte-identical"
        assert after["progress"] == 0.42
        assert after["current_stage"] == "opt"
        assert after["revision"] != before["revision"], "narrow write must bump revision"
    finally:
        mgr.shutdown()


def test_pending_transitions_to_running(tmp_path: Path) -> None:
    """LSF ``status=running`` observed by a real poll_remote drives the legal
    PENDING→RUNNING state transition through store.transition."""
    mgr = _make_manager(tmp_path)
    try:
        record = _seed_running_job(
            mgr,
            tmp_path,
            "pend-run",
            status=JobStatus.PENDING,
            remote_job_id="777",
            result={"node": "n1", "remote_dir": "/scratch/acp/pend-run"},
        )
        runner = _remote_runner("running")
        _seed_job_state(runner, record.id)
        mgr.remote_runner = runner  # type: ignore[assignment]

        mgr._poll_job(record.id)

        final = mgr.store.get(record.id)
        assert final is not None
        assert final.status == JobStatus.RUNNING, "PENDING→RUNNING must persist"
        assert final.revision >= 1
        assert record.status == JobStatus.PENDING, "poll must not mutate the loaded record"
        assert "remote.lsf_status" in _event_types(mgr, record.id)
    finally:
        mgr.shutdown()


def test_terminal_side_effects_retry(tmp_path: Path) -> None:
    """Terminal side effects are retried by the reconcile pass until the
    ``terminal_side_effects_done`` marker persists; the terminal event's
    stable idempotency key prevents duplicates across retries."""
    mgr = _make_manager(tmp_path)
    try:
        record = _seed_running_job(
            mgr,
            tmp_path,
            "term-retry",
            status=JobStatus.COMPLETED,
            exit_code=0,
            progress=1.0,
            completed_at="2026-01-01T00:00:00+00:00",
            remote_job_id="555",
            result={"node": "n1", "terminal_side_effects_done": False},
        )
        runner = _remote_runner("done")
        mgr.remote_runner = runner  # type: ignore[assignment]

        real_sync = mgr._sync_task_status
        calls = {"n": 0}

        def flaky_sync(rec: JobRecord) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("sync backend down")
            real_sync(rec)

        mgr._sync_task_status = flaky_sync  # type: ignore[method-assign]

        # First reconcile: side effects start, terminal event emitted, then a
        # side effect fails — marker must NOT be set (retryable).
        mgr._reconcile_once()
        marker = (mgr.store.get(record.id).result or {}).get("terminal_side_effects_done")  # type: ignore[union-attr]
        assert marker is False, "failed side effects must leave the job retryable"
        terminal_events = [e for e in _event_types(mgr, record.id) if e == "job.completed"]
        assert len(terminal_events) == 1

        # Second reconcile: side effects complete, marker persists.
        mgr._reconcile_once()
        final = mgr.store.get(record.id)
        assert final is not None
        assert (final.result or {}).get("terminal_side_effects_done") is True

        # Third reconcile: marker set → category no longer matches.
        mgr._reconcile_once()
        terminal_events = [e for e in _event_types(mgr, record.id) if e == "job.completed"]
        assert len(terminal_events) == 1, "idempotency key must prevent duplicate events"
    finally:
        mgr.shutdown()


def test_starting_unconfirmed_picked_by_reconcile(tmp_path: Path) -> None:
    """STARTING + submit_state=unconfirmed is owned by the reconcile loop —
    the regular poll scan never touches (misjudges) it, and reconcile
    converges it to PENDING once the submission is recoverable."""
    mgr = _make_manager(tmp_path)
    try:
        record = _seed_running_job(
            mgr,
            tmp_path,
            "start-unconf",
            status=JobStatus.STARTING,
            remote_job_id="999",
            result={
                "execution_kind": "remote",
                "node": "n1",
                "remote_dir": "/scratch/acp/start-unconf",
                "remote": {"submit_state": "unconfirmed", "node": "n1"},
            },
        )

        # Regular poll must not touch a STARTING job (not in the scan set).
        mgr._poll_job(record.id)
        assert mgr.store.get(record.id).status == JobStatus.STARTING  # type: ignore[union-attr]

        class _Recoverable:
            def __init__(self) -> None:
                self.recover_calls = 0

            def recover_job_state(self, rec: JobRecord) -> bool:
                self.recover_calls += 1
                return True

            def apply_terminal_side_effects(
                self, rec: JobRecord, event_log, stage_events=()
            ) -> None:
                raise AssertionError("not used")

        fake = _Recoverable()
        mgr.remote_runner = fake  # type: ignore[assignment]

        mgr._reconcile_once()
        final = mgr.store.get(record.id)
        assert final is not None
        assert final.status == JobStatus.PENDING, "reconcile must converge STARTING→PENDING"
        assert fake.recover_calls == 1

        # Category no longer matches — reconcile does not pick it again.
        mgr._reconcile_once()
        assert fake.recover_calls == 1
        assert mgr.store.get(record.id).status == JobStatus.PENDING  # type: ignore[union-attr]
    finally:
        mgr.shutdown()


# ====================================================================== #
# Todo 3: every manager control write is CAS-conditional — permanent guard
# against reintroducing whole-row store.update() calls in manager.py.
# ====================================================================== #


def _whole_row_update_lines(source: str) -> list[int]:
    """1-based line numbers of ``<x>.store.update(...)`` whole-row writes.

    Narrow conditional APIs (``update_progress`` / ``transition`` /
    ``requeue_with_spec`` / ``update_work_dir_and_name``) are distinct
    attribute names and are never flagged.
    """
    tree = ast.parse(source)
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "update":
            continue
        value = node.func.value
        if isinstance(value, ast.Attribute) and value.attr == "store":
            hits.append(node.lineno)
    return sorted(hits)


def test_manager_has_no_whole_row_update(tmp_path: Path) -> None:
    """manager.py must contain zero ``self.store.update(...)`` call sites.

    All control writes go through the conditional store APIs; this guard is
    the permanent tripwire (plan todo 3 / Metis M7) — implemented in this
    plan's own test module rather than the shared ``check_grep_gates.py``.
    """
    manager_path = Path(manager_module.__file__)
    source = manager_path.read_text(encoding="utf-8")
    hits = _whole_row_update_lines(source)
    assert hits == [], (
        "manager.py must not perform whole-row store.update() writes — route "
        "them through transition()/update_progress()/requeue_with_spec() "
        f"(whole-row calls found at lines: {hits})"
    )

    # Negative self-check: scan an injected temporary copy (manager.py itself
    # is never modified) and require the scanner to flag the whole-row call.
    injected_path = tmp_path / "manager_injected.py"
    injected_path.write_text(
        source
        + "\n\n"
        + "def _injected_whole_row_write(manager, record):\n"
        + "    manager.store.update(record)\n",
        encoding="utf-8",
    )
    injected_hits = _whole_row_update_lines(
        injected_path.read_text(encoding="utf-8")
    )
    assert injected_hits, "scanner must flag an injected whole-row store.update() call"


# ====================================================================== #
# Todo 3: barrier/sequenced interleavings + attempt isolation + contract B
# (storage identity + receipt archiving).
# ====================================================================== #


def test_interleave_poll_vs_pause_barrier(tmp_path: Path) -> None:
    """Barrier race [poll vs pause]: whichever writer lands first, the job
    ends PAUSED — a stale poll observation can never win the pause."""
    mgr = _make_manager(tmp_path)
    try:
        record = _seed_running_job(mgr, tmp_path, "barrier-poll-pause")
        mgr.runner.pause_local = lambda job_id: True  # type: ignore[method-assign]
        barrier = threading.Barrier(2, timeout=10)
        seen: dict[str, object] = {}

        def poll(stale_record: JobRecord) -> tuple[bool, int | None]:
            barrier.wait()
            stale_record.progress = 0.55
            stale_record.current_stage = "late"
            return (False, None)

        mgr.runner.poll = poll  # type: ignore[method-assign]

        def do_poll() -> None:
            mgr._poll_job(record.id)

        def do_pause() -> None:
            barrier.wait()
            seen["pause"] = mgr.pause_job(record.id).status

        t_poll = threading.Thread(target=do_poll)
        t_pause = threading.Thread(target=do_pause)
        t_poll.start()
        t_pause.start()
        t_poll.join(timeout=30)
        t_pause.join(timeout=30)
        assert not t_poll.is_alive() and not t_pause.is_alive()

        final = mgr.store.get(record.id)
        assert final is not None
        assert seen["pause"] == JobStatus.PAUSED, "pause must report PAUSED"
        assert final.status == JobStatus.PAUSED, "pause must win regardless of interleaving"
        assert final.revision >= 1
        assert "job.paused" in _event_types(mgr, record.id)
    finally:
        mgr.shutdown()


def test_interleave_cancel_vs_unpause_barrier(tmp_path: Path) -> None:
    """Barrier race [cancel vs unpause]: cancel always ends CANCELLING —
    an unpause racing a cancel can never leave the job RUNNING."""
    mgr = _make_manager(tmp_path)
    try:
        record = _seed_running_job(mgr, tmp_path, "barrier-cancel-unpause", status=JobStatus.PAUSED)
        barrier = threading.Barrier(2, timeout=10)

        def _resume(job_id: str) -> bool:
            barrier.wait()
            return True

        mgr.runner.resume_local = _resume  # type: ignore[method-assign]
        mgr.runner.cancel_local = lambda job_id: True  # type: ignore[method-assign]
        seen: dict[str, object] = {}

        def do_unpause() -> None:
            try:
                seen["unpause"] = mgr.unpause_job(record.id).status
            except ValueError as exc:
                seen["unpause"] = f"rejected: {exc}"

        def do_cancel() -> None:
            barrier.wait()
            result = mgr.cancel(record.id)
            seen["cancel"] = None if result is None else result.status

        t_unpause = threading.Thread(target=do_unpause)
        t_cancel = threading.Thread(target=do_cancel)
        t_unpause.start()
        t_cancel.start()
        t_unpause.join(timeout=30)
        t_cancel.join(timeout=30)
        assert not t_unpause.is_alive() and not t_cancel.is_alive()

        final = mgr.store.get(record.id)
        assert final is not None
        assert final.status == JobStatus.CANCELLING, "cancel must beat the racing unpause"
        assert seen["cancel"] == JobStatus.CANCELLING
        assert "job.cancelling" in _event_types(mgr, record.id)
    finally:
        mgr.shutdown()


def test_interleave_terminal_vs_old_poll(tmp_path: Path) -> None:
    """Sequenced [terminal vs old poll]: a terminal state committed while an
    older observation is in flight is preserved; the stale write is dropped."""
    mgr = _make_manager(tmp_path)
    try:
        record = _seed_running_job(mgr, tmp_path, "barrier-terminal-poll")
        terminal_committed = threading.Event()

        def poll(stale_record: JobRecord) -> tuple[bool, int | None]:
            committed = mgr.store.transition(
                record.id,
                expected_status=JobStatus.RUNNING,
                expected_revision=stale_record.revision,
                expected_attempt=stale_record.attempt,
                status=JobStatus.COMPLETED,
                exit_code=0,
                progress=1.0,
                completed_at="2026-01-01T00:00:00+00:00",
            )
            terminal_committed.set()
            assert committed.status == JobStatus.COMPLETED
            stale_record.exit_code = 0
            return (True, 0)

        mgr.runner.poll = poll  # type: ignore[method-assign]
        mgr._poll_job(record.id)
        assert terminal_committed.is_set()

        final = mgr.store.get(record.id)
        assert final is not None
        assert final.status == JobStatus.COMPLETED, "terminal state must survive the old poll"
        assert final.completed_at == "2026-01-01T00:00:00+00:00"
        assert final.exit_code == 0
        assert not (final.result or {}).get("terminal_side_effects_done"), (
            "the dropped stale write must not run terminal side effects"
        )
        assert "job.poll_dropped_stale" in _event_types(mgr, record.id)
    finally:
        mgr.shutdown()


def test_interleave_old_attempt_continue_vs_new_attempt_terminal(tmp_path: Path) -> None:
    """[old attempt continue vs new attempt terminal]: replaying attempt=1
    writes against an attempt=2 terminal row conflicts and drops — the new
    attempt's row stays byte-identical."""
    db = tmp_path / "acp_jobs.db"
    mgr = _make_manager(tmp_path)
    try:
        mgr.store.create(_record("iso-1", status=JobStatus.FAILED, error="boom"))
        old = mgr.store.get("iso-1")
        assert old is not None and old.attempt == 1

        updated = mgr.store.requeue_with_spec(
            "iso-1",
            new_spec=old.spec,
            expected_revision=old.revision,
            expected_attempt=1,
            expected_status=JobStatus.FAILED,
        )
        mgr.store.transition(
            "iso-1",
            expected_status=JobStatus.QUEUED,
            expected_revision=updated.revision,
            expected_attempt=2,
            status=JobStatus.CANCELLED,
            completed_at="2026-01-02T00:00:00+00:00",
        )
        before = _raw_row(db, "iso-1")
        assert before["attempt"] == 2

        with pytest.raises(JobStateConflictError):
            mgr._requeue_record_cas(
                "iso-1",
                new_spec=old.spec,
                expected=old,
                expected_status=(JobStatus.FAILED, JobStatus.CANCELLED),
                result={"continued_from": "failed"},
            )
        with pytest.raises(JobStateConflictError):
            mgr._cas_write(
                old,
                expected_status=JobStatus.FAILED,
                status=JobStatus.RUNNING,
            )
    finally:
        mgr.shutdown()

    after = _raw_row(db, "iso-1")
    assert after == before, "an old-attempt replay must never modify the new attempt's row"


def test_requeue_preserves_storage_identity_and_archives_receipts(tmp_path: Path) -> None:
    """Contract B: rerun keeps the persisted storage identity and archives the
    closed attempt's checkpoint/step output/RESULT/run receipts under
    ``WORK/00_RUNTIME/attempts/<n>/`` (recoverable, never deleted)."""
    mgr = _make_manager(tmp_path)
    try:
        work_dir = tmp_path / "runs" / "ident"
        _seed_job_for_transitions(
            mgr,
            work_dir,
            "ident",
            status=JobStatus.FAILED,
            result={
                "attempts": 1,
                "remote": {"node": "n1", "submit_state": "done", "lsf_job_id": "7"},
                "remote_dir": "/scratch/acp/ident",
            },
        )
        checkpoint = {"task_id": "ident", "workflow": "fake", "plan_fingerprint": "abc"}
        runtime = work_dir / "WORK" / "00_RUNTIME"
        runtime.mkdir(parents=True)
        (runtime / "checkpoint.json").write_text(json.dumps(checkpoint), encoding="utf-8")
        search = work_dir / "WORK" / "02_SEARCH"
        search.mkdir(parents=True)
        (search / "step.out").write_text("old-step", encoding="utf-8")
        result_dir = work_dir / "RESULT"
        result_dir.mkdir(parents=True)
        (result_dir / "result.json").write_text("{}", encoding="utf-8")
        (work_dir / ".exit_code").write_text("1", encoding="utf-8")
        (work_dir / "state.json").write_text("{}", encoding="utf-8")
        submissions: list[str] = []
        mgr._execute_submission = lambda job_id: submissions.append(job_id)  # type: ignore[method-assign]

        rerun = mgr.rerun_job("ident")
        assert rerun is not None
        assert rerun.attempt == 2
        assert rerun.result is not None
        assert rerun.result["remote"] == {"node": "n1"}, (
            "storage identity survives; submission fields are cleared"
        )
        assert rerun.result["remote_dir"] == "/scratch/acp/ident"
        assert "attempts" not in rerun.result, "jobs.attempt is the single counter"

        archive = work_dir / "WORK" / "00_RUNTIME" / "attempts" / "1"
        archived_checkpoint = archive / "WORK" / "00_RUNTIME" / "checkpoint.json"
        assert json.loads(archived_checkpoint.read_text(encoding="utf-8")) == checkpoint
        archived_step = archive / "WORK" / "02_SEARCH" / "step.out"
        assert archived_step.read_text(encoding="utf-8") == "old-step"
        assert (archive / "RESULT" / "result.json").is_file()
        assert (archive / ".exit_code").read_text(encoding="utf-8") == "1"
        assert (archive / "state.json").is_file()
        assert not (work_dir / ".exit_code").exists(), "receipts live only in the archive"
        assert submissions == ["ident"]
    finally:
        mgr.shutdown()


def test_manager_control_writes_carry_attempt_in_events(tmp_path: Path) -> None:
    """Migrated control events expose ``attempt`` (audit trail requirement)."""
    mgr = _make_manager(tmp_path)
    try:
        record = _seed_running_job(mgr, tmp_path, "evt-attempt", status=JobStatus.RUNNING)
        mgr.runner.pause_local = lambda job_id: True  # type: ignore[method-assign]
        mgr.runner.resume_local = lambda job_id: True  # type: ignore[method-assign]
        mgr.pause_job(record.id)
        mgr.unpause_job(record.id)
        events = {e["type"]: e for e in mgr.event_log(record.id).read_all()}  # type: ignore[union-attr]
        assert events["job.paused"]["attempt"] == 1
        assert events["job.resumed"]["attempt"] == 1
    finally:
        mgr.shutdown()


def _seed_job_for_transitions(
    mgr: JobManager, work_dir: Path, job_id: str, **overrides: object
) -> JobRecord:
    work_dir.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, object] = {
        "spec": _spec(),
        "status": JobStatus.FAILED,
        "work_dir": str(work_dir),
    }
    kwargs.update(overrides)
    record = JobRecord(id=job_id, **kwargs)  # pyright: ignore[reportArgumentType]
    mgr.store.create(record)
    return record
