# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false
"""CAS state transitions, narrow progress updates, and jobs revision/attempt columns."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from acp.scheduler import migrations as migrations_module
from acp.scheduler.job_edit import attempt_number, compute_source_revision
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.migrations import migrate
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
