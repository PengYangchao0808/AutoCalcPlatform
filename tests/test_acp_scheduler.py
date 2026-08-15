"""Tests for the scheduler core (store, manager, runner) without external binaries."""

# pyright: reportMissingImports=false, reportPrivateUsage=false, reportPossiblyUnboundVariable=false, reportOptionalMemberAccess=false, reportUnusedCallResult=false, reportAny=false

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import EXIT_WAITING_REVIEW, JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.migrations import migrate
from acp.scheduler.runner import materialize_job_input
from acp.scheduler.store import JobStore


def test_materialize_com_and_inp_preserve_suffix(tmp_path: Path) -> None:
    """M8: .com/.inp inputs must not fall into the SMILES conversion path;
    their format-significant suffix is preserved for the CLI parser."""
    run_root = tmp_path / "root"
    run_root.mkdir()
    inputs_dir = tmp_path / "inputs"
    com = run_root / "mol.com"
    com.write_text("! SP wB97X-D4 def2-TZVPP\n* xyz 0 1\nC 0 0 0\n*\n", encoding="utf-8")
    inp_file = run_root / "mol.inp"
    inp_file.write_text("! SP\n* xyz 0 1\nC 0 0 0\n*\n", encoding="utf-8")

    for src, expected in ((com, "input.com"), (inp_file, "input.inp")):
        dest = materialize_job_input(
            {"source_type": "file", "source": str(src)},
            inputs_dir,
            run_root,
        )
        assert dest is not None
        assert dest.name == expected
        assert dest.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")


def test_materialize_structure_asset_preserves_com_suffix(tmp_path: Path) -> None:
    run_root = tmp_path / "root"
    run_root.mkdir()
    asset = run_root / "mol.com"
    asset.write_text("! SP\n* xyz 0 1\nC 0 0 0\n*\n", encoding="utf-8")

    dest = materialize_job_input(
        {"source_type": "structure_asset", "source": "mol.com"}, tmp_path / "inputs", run_root
    )
    assert dest is not None
    assert dest.name == "input.com"


def test_store_roundtrip(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    from acp.scheduler.jobs import JobRecord

    record = JobRecord(
        id="job-1",
        spec=JobSpec(workflow="fake", name="t", input={"source": "CCO"}),
        status=JobStatus.QUEUED,
        work_dir=str(tmp_path / "job-1"),
    )
    store.create(record)
    fetched = store.get("job-1")
    assert fetched is not None
    assert fetched.spec.workflow == "fake"
    assert fetched.status == JobStatus.QUEUED

    fetched.status = JobStatus.RUNNING
    fetched.pid = 4242
    store.update(fetched)
    assert store.get("job-1").pid == 4242

    store.create(
        JobRecord(
            id="job-2",
            spec=JobSpec(workflow="fake"),
            status=JobStatus.COMPLETED,
            work_dir=str(tmp_path / "job-2"),
        )
    )
    counts = store.counts()
    assert counts["running"] == 1
    assert counts["completed"] == 1
    assert len(store.list()) == 2


def test_store_reload_preserves_history(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    JobStore(db).create(
        JobRecord(
            id="persisted",
            spec=JobSpec(workflow="fake", name="p"),
            status=JobStatus.COMPLETED,
            work_dir=str(tmp_path / "p"),
        )
    )
    reopened = JobStore(db)
    assert reopened.get("persisted") is not None
    assert reopened.counts()["completed"] == 1


def test_manager_runs_fake_job_to_completion(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=2)
    record = mgr.submit(JobSpec(workflow="fake", name="demo", input={"source": "CCO"}))
    assert record.status == JobStatus.QUEUED

    for _ in range(40):
        cur = mgr.get(record.id)
        assert cur is not None
        if cur.status.is_terminal:
            break
        time.sleep(0.5)

    assert cur.status == JobStatus.COMPLETED
    assert cur.exit_code == 0
    events = mgr.event_log(record.id).read_all()
    types = [e["type"] for e in events]
    assert "job.created" in types
    assert "stage.started" in types
    assert "job.completed" in types
    mgr.shutdown()


def test_manager_rejects_unknown_workflow(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        import pytest

        with pytest.raises(ValueError):
            mgr.submit(JobSpec(workflow="nonexistent"))
    finally:
        mgr.shutdown()


def test_manager_marks_interrupted_jobs_on_startup(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    store = JobStore(db)
    from acp.scheduler.jobs import JobRecord

    store.create(
        JobRecord(
            id="was-running",
            spec=JobSpec(workflow="fake"),
            status=JobStatus.RUNNING,
            work_dir=str(tmp_path / "r"),
        )
    )
    mgr = JobManager(run_root=tmp_path, store=store, max_running=1)
    interrupted = mgr.get("was-running")
    assert interrupted.status == JobStatus.FAILED
    assert "interrupted" in (interrupted.error or "")
    mgr.shutdown()


def test_event_log_append_read(tmp_path: Path) -> None:
    log = JobEventLog(tmp_path / "events.jsonl")
    log.append("job.started", job_id="x")
    log.append("log", line="hello")
    records = log.read_all()
    assert len(records) == 2
    assert records[0]["type"] == "job.started"
    assert records[1]["line"] == "hello"
    assert "timestamp" in records[0]


def test_find_state_file_prefers_shallowest(tmp_path: Path) -> None:
    """P1#1: nested workflows (NMR) write multiple state.json; shallowest wins."""
    import json

    from acp.scheduler.runner import find_workflow_state

    root = tmp_path / "work"
    (root / "jobA").mkdir(parents=True)
    (root / "jobA" / "state.json").write_text(json.dumps({"current_stage": "nmr"}))
    (root / "conformer" / "mol").mkdir(parents=True)
    (root / "conformer" / "mol" / "state.json").write_text(json.dumps({"current_stage": "crest"}))

    found = find_workflow_state(root)
    assert found is not None
    assert found.relative_to(root) == Path("jobA/state.json")

    single = tmp_path / "single"
    (single / "mol_CCO_1234").mkdir(parents=True)
    (single / "mol_CCO_1234" / "state.json").write_text("{}")
    found2 = find_workflow_state(single)
    assert found2 is not None
    assert found2.relative_to(single) == Path("mol_CCO_1234/state.json")

    assert find_workflow_state(tmp_path / "empty") is None


def test_queued_job_cancel_is_immediate(tmp_path: Path) -> None:
    """P1#3: cancelling a running job transitions to CANCELLING then terminal.

    With the poller-driven architecture, jobs no longer queue — all are
    submitted concurrently on daemon threads.  This test verifies the
    cancel flow for a running fake job.
    """
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        second = mgr.submit(JobSpec(workflow="fake", name="second", input={"source": "Y"}))
        time.sleep(0.3)

        cancelled = mgr.cancel(second.id)
        assert cancelled is not None
        cur = mgr.get(second.id)
        assert cur.status in (JobStatus.CANCELLING, JobStatus.CANCELLED, JobStatus.RUNNING)

        for _ in range(20):
            cur = mgr.get(second.id)
            if cur.status.is_terminal:
                break
            time.sleep(0.3)

        assert mgr.get(second.id).status.is_terminal
    finally:
        mgr.shutdown()


def test_resolve_safe_rejects_traversal(tmp_path: Path) -> None:
    """Sprint 0: resolve_safe() must reject path-traversal attempts."""
    from acp.scheduler.files import resolve_safe

    work = tmp_path / "work"
    work.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("password")

    (work / "ok.txt").write_text("data")
    ok = resolve_safe(work, "ok.txt")
    assert ok is not None and ok.name == "ok.txt"

    assert resolve_safe(work, "../secret.txt") is None
    assert resolve_safe(work, "../../etc/passwd") is None
    assert resolve_safe(work, "../../../secret.txt") is None

    assert resolve_safe(work, "missing.txt") is None


def test_output_dir_outside_run_root_is_clamped(tmp_path: Path) -> None:
    """P2#2: an output_dir escaping run_root must be ignored (clamped under run_root)."""
    mgr = JobManager(run_root=tmp_path, max_running=1)
    try:
        escape = tmp_path.parent / "evil_elsewhere"
        record = mgr.submit(
            JobSpec(workflow="fake", name="clamp", input={"source": "X"}, output_dir=str(escape))
        )
        work_dir = Path(record.work_dir).resolve()
        run_root = tmp_path.resolve()
        assert run_root in work_dir.parents or work_dir == run_root, (
            f"work_dir {work_dir} escaped run_root {run_root}"
        )
        assert "evil_elsewhere" not in record.work_dir
    finally:
        mgr.shutdown()


def test_waiting_review_status_flags() -> None:
    assert JobStatus.WAITING_REVIEW.is_active is True
    assert JobStatus.WAITING_REVIEW.is_terminal is False


def test_pause_for_review_and_resume_roundtrip(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        work_dir = tmp_path / "job-running"
        work_dir.mkdir()
        record = JobRecord(
            id="job-running",
            spec=JobSpec(workflow="fake", name="review"),
            status=JobStatus.RUNNING,
            work_dir=str(work_dir),
        )
        mgr.store.create(record)

        paused = mgr.pause_for_review(record.id, {"decision_id": "dec-1"})
        assert paused.status == JobStatus.WAITING_REVIEW
        assert paused.result is not None
        assert paused.result["review_payload"]["decision_id"] == "dec-1"
        assert mgr.event_log(record.id).read_all()[-1]["type"] == "job.waiting_review"

        resumed = mgr.resume(record.id, {"approved": True})
        assert resumed.status == JobStatus.RUNNING
        assert resumed.result is not None
        assert resumed.result["review_resolution"]["approved"] is True
        assert mgr.event_log(record.id).read_all()[-1]["type"] == "job.review_resumed"
    finally:
        mgr.shutdown()


def test_poll_loop_skips_waiting_review_jobs(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        work_dir = tmp_path / "job-waiting"
        work_dir.mkdir()
        record = JobRecord(
            id="job-waiting",
            spec=JobSpec(workflow="fake", name="waiting"),
            status=JobStatus.WAITING_REVIEW,
            work_dir=str(work_dir),
        )
        mgr.store.create(record)

        seen: list[str] = []

        def fake_poll(job_id: str) -> None:
            seen.append(job_id)

        mgr._poll_job = fake_poll  # type: ignore[method-assign]
        mgr._poll_stop.set()
        mgr._poll_loop()

        assert record.id not in seen
    finally:
        mgr.shutdown()


def test_move_delete_block_waiting_review_jobs(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        project = mgr.projects.create_project("Target")
        work_dir = tmp_path / "job-review-blocked"
        work_dir.mkdir()
        record = JobRecord(
            id="job-review-blocked",
            spec=JobSpec(workflow="fake", name="blocked", project_id=mgr.default_project_id),
            status=JobStatus.WAITING_REVIEW,
            work_dir=str(work_dir),
            project_id=mgr.default_project_id,
        )
        mgr.store.create(record)

        with pytest.raises(ValueError, match="active job"):
            mgr.move_job(record.id, str(project["project_id"]))
        with pytest.raises(ValueError, match="active"):
            mgr.delete_job(record.id)
    finally:
        mgr.shutdown()


def test_requeue_active_on_startup_preserves_waiting_review(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    store = JobStore(db)
    waiting_dir = tmp_path / "waiting"
    waiting_dir.mkdir()
    store.create(
        JobRecord(
            id="waiting-review",
            spec=JobSpec(workflow="fake", name="review"),
            status=JobStatus.WAITING_REVIEW,
            work_dir=str(waiting_dir),
        )
    )

    mgr = JobManager(run_root=tmp_path, store=store, poll_interval=30)
    try:
        waiting = mgr.get("waiting-review")
        assert waiting is not None
        assert waiting.status == JobStatus.WAITING_REVIEW
    finally:
        mgr.shutdown()


def test_poll_job_translates_waiting_review_exit_code(tmp_path: Path) -> None:
    mgr = JobManager(run_root=tmp_path, poll_interval=30)
    try:
        work_dir = tmp_path / "job-review-gate"
        work_dir.mkdir()
        (work_dir / "review_payload.json").write_text(
            json.dumps(
                {
                    "study_id": "study-1",
                    "status": "waiting",
                    "pending_decisions": ["decision-1"],
                }
            ),
            encoding="utf-8",
        )
        record = JobRecord(
            id="job-review-gate",
            spec=JobSpec(workflow="mechanism", name="review-gate"),
            status=JobStatus.RUNNING,
            work_dir=str(work_dir),
        )
        mgr.store.create(record)
        mgr.runner.poll = lambda _record: (True, EXIT_WAITING_REVIEW)  # type: ignore[method-assign]

        mgr._poll_job(record.id)

        updated = mgr.get(record.id)
        assert updated is not None
        assert updated.status == JobStatus.WAITING_REVIEW
        assert updated.exit_code == EXIT_WAITING_REVIEW
        assert updated.completed_at is None
        assert updated.result is not None
        assert updated.result["review_payload"]["study_id"] == "study-1"
        assert updated.result["review_payload"]["pending_decisions"] == ["decision-1"]
        assert mgr.event_log(record.id).read_all()[-1]["type"] == "job.waiting_review"
    finally:
        mgr.shutdown()


def test_migration_006_and_mechanism_store_helpers(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "scheduler.db"
    applied_first = migrate(db_path)
    applied_second = migrate(db_path)

    assert applied_first == 4
    assert applied_second == 0

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert "mechanism_studies" in tables
    assert "decision_points" in tables

    store = JobStore(db_path)
    store.upsert_mechanism_study(
        "study-1",
        job_id="job-1",
        study_json='{"study_id": "study-1"}',
        status="waiting",
        created_at="2026-08-12T00:00:00+00:00",
        updated_at="2026-08-12T01:00:00+00:00",
    )
    store.upsert_decision_point(
        "decision-1",
        study_id="study-1",
        status="waiting",
        payload='{"decision": 1}',
        resolution=None,
        created_at="2026-08-12T00:10:00+00:00",
        resolved_at=None,
    )

    study = store.get_mechanism_study("study-1")
    assert study is not None
    assert study["status"] == "waiting"
    assert store.list_mechanism_studies(limit=10)[0]["id"] == "study-1"

    decision = store.get_decision_point("decision-1")
    assert decision is not None
    assert decision["status"] == "waiting"
    assert store.list_decision_points("study-1", limit=10)[0]["id"] == "decision-1"
