"""
Phase 5B tests — LocalCleanup (local run_root disk protection).

Mirrors the Phase 5 remote-cleanup test coverage but against the local
filesystem (no SSH/SFTP).  Uses a tmp_path run_root + a real SQLite
JobStore so the DB-driven status mapping is exercised end-to-end.

Run with: PYTHONPATH=src python3 tests/test_local_cleanup.py
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.local_cleanup import (
    DEFAULT_MAX_DIRS_PER_SWEEP,
    DISK_CLEANUP_THRESHOLD,
    DISK_SKIP_THRESHOLD,
    LocalCleanup,
    LocalCleanupReport,
    RetentionPolicy,
    _format_bytes,
    _is_safe_run_root,
)
from acp.scheduler.runner import JobRunner
from acp.scheduler.store import JobStore

# ====================================================================== #
# Fixtures
# ====================================================================== #


@pytest.fixture
def run_root(tmp_path: Path) -> Path:
    root = tmp_path / "acp_runs"
    root.mkdir()
    return root


@pytest.fixture
def store(run_root: Path) -> JobStore:
    return JobStore(run_root / "acp_jobs.db")


def _make_record(
    job_id: str,
    project_id: str = "uncategorized",
    status: JobStatus = JobStatus.COMPLETED,
    completed_at: str | None = None,
    error: str | None = None,
) -> JobRecord:
    return JobRecord(
        id=job_id,
        spec=JobSpec(workflow="fake", name=job_id, project_id=project_id),
        status=status,
        work_dir="",
        project_id=project_id,
        completed_at=completed_at,
        error=error,
    )


def _make_job_dir(
    run_root: Path, project_id: str, job_id: str, size_bytes: int = 1024, age_days: float = 0.0
) -> Path:
    """Create run_root/<project>/<job_id>/ with one payload file of given size."""
    job_dir = run_root / project_id / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    payload = job_dir / "payload.bin"
    payload.write_bytes(b"x" * size_bytes)
    if age_days > 0:
        ts = time.time() - age_days * 86400
        os.utime(job_dir, (ts, ts))
    return job_dir


def _set_record_work_dir_and_store(
    store: JobStore, run_root: Path, record: JobRecord, project_id: str, job_id: str
) -> JobRecord:
    job_dir = run_root / project_id / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    record.work_dir = str(job_dir)
    store.create(record)
    return record


# ====================================================================== #
# _is_safe_run_root
# ====================================================================== #


def test_safe_run_root_accepts_persistent_volume():
    assert _is_safe_run_root("/var/lib/acp/runs") is True
    assert _is_safe_run_root("/scratch/acp_jobs") is True


def test_safe_run_root_rejects_root_and_shallow():
    assert _is_safe_run_root("/") is False
    assert _is_safe_run_root("/scratch") is False  # only 1 component
    assert _is_safe_run_root("") is False
    assert _is_safe_run_root(None) is False  # type: ignore[arg-type]


def test_safe_run_root_rejects_relative():
    assert _is_safe_run_root("foo/bar") is False
    assert _is_safe_run_root("./acp_runs") is False


def test_safe_run_root_rejects_dangerous_literal_roots():
    for bad in ("/tmp", "/home", "/var", "/usr", "/root", "/etc", "/dev", "/proc"):
        assert _is_safe_run_root(bad) is False, bad


def test_safe_run_root_accepts_under_tmp_with_depth():
    # pytest tmpdir lives under /tmp — must be accepted.
    assert _is_safe_run_root("/tmp/acp_runs") is True
    assert _is_safe_run_root("/tmp/pytest-xyz/run_root") is True


# ====================================================================== #
# _format_bytes
# ====================================================================== #


def test_format_bytes():
    assert _format_bytes(0) == "0 B"
    assert _format_bytes(512) == "512 B"
    assert _format_bytes(2048) == "2.0 KiB"
    assert _format_bytes(1048576) == "1.0 MiB"
    assert _format_bytes(1610612736) == "1.5 GiB"


# ====================================================================== #
# Constructor validation
# ====================================================================== #


def test_constructor_rejects_bad_thresholds(run_root: Path, store: JobStore):
    with pytest.raises(ValueError, match="must not exceed"):
        LocalCleanup(run_root, store, cleanup_threshold=95, skip_threshold=90)


def test_constructor_accepts_equal_default_thresholds(run_root: Path, store: JobStore):
    cleanup = LocalCleanup(run_root, store)
    assert cleanup._cleanup_threshold == DISK_CLEANUP_THRESHOLD
    assert cleanup._skip_threshold == DISK_SKIP_THRESHOLD
    assert cleanup._max_dirs_per_sweep == DEFAULT_MAX_DIRS_PER_SWEEP


# ====================================================================== #
# check_disk_usage / disk_usage_detail
# ====================================================================== #


def test_check_disk_usage_returns_percent(run_root: Path, store: JobStore):
    cleanup = LocalCleanup(run_root, store)
    pct = cleanup.check_disk_usage()
    assert 0 <= pct <= 100


def test_disk_usage_detail_structure(run_root: Path, store: JobStore):
    cleanup = LocalCleanup(run_root, store)
    detail = cleanup.disk_usage_detail()
    assert {"total_bytes", "used_bytes", "free_bytes", "percent_used", "job_count"} <= set(detail)
    assert detail["total_bytes"] > 0
    assert 0 <= detail["percent_used"] <= 100


def test_check_disk_usage_missing_run_root(tmp_path: Path, store: JobStore):
    cleanup = LocalCleanup(tmp_path / "does_not_exist", store)
    # shutil.disk_usage tolerates a missing path on the same filesystem;
    # the result is still a valid percent (0 only on OSError).
    pct = cleanup.check_disk_usage()
    assert 0 <= pct <= 100


# ====================================================================== #
# cleanup_old_work_dirs — status-based retention
# ====================================================================== #


def test_cleanup_removes_old_completed_keeps_recent(run_root: Path, store: JobStore):
    old = _make_record("job_old", status=JobStatus.COMPLETED)
    _set_record_work_dir_and_store(store, run_root, old, "uncategorized", "job_old")
    _make_job_dir(run_root, "uncategorized", "job_old", size_bytes=500, age_days=31)

    fresh = _make_record("job_fresh", status=JobStatus.COMPLETED)
    _set_record_work_dir_and_store(store, run_root, fresh, "uncategorized", "job_fresh")
    _make_job_dir(run_root, "uncategorized", "job_fresh", size_bytes=500, age_days=1)

    cleanup = LocalCleanup(run_root, store, policy=RetentionPolicy(completed_days=30))
    report = cleanup.cleanup_old_work_dirs()

    assert len(report.work_dirs_removed) == 1
    assert report.freed_bytes_est >= 500
    assert not (run_root / "uncategorized" / "job_old").exists()
    assert (run_root / "uncategorized" / "job_fresh").exists()
    assert report.ok


def test_cleanup_failed_uses_longer_retention(run_root: Path, store: JobStore):
    failed = _make_record("job_failed", status=JobStatus.FAILED)
    _set_record_work_dir_and_store(store, run_root, failed, "uncategorized", "job_failed")
    _make_job_dir(run_root, "uncategorized", "job_failed", size_bytes=100, age_days=45)

    completed = _make_record("job_done", status=JobStatus.COMPLETED)
    _set_record_work_dir_and_store(store, run_root, completed, "uncategorized", "job_done")
    _make_job_dir(run_root, "uncategorized", "job_done", size_bytes=100, age_days=45)

    cleanup = LocalCleanup(
        run_root, store, policy=RetentionPolicy(completed_days=30, failed_days=90)
    )
    report = cleanup.cleanup_old_work_dirs()

    # 45 days: completed (30d) expired → removed; failed (90d) still fresh → kept.
    assert len(report.work_dirs_removed) == 1
    assert (run_root / "uncategorized" / "job_failed").exists()
    assert not (run_root / "uncategorized" / "job_done").exists()


def test_cleanup_cancelled_uses_cancelled_window(run_root: Path, store: JobStore):
    cancelled = _make_record("job_can", status=JobStatus.CANCELLED)
    _set_record_work_dir_and_store(store, run_root, cancelled, "uncategorized", "job_can")
    _make_job_dir(run_root, "uncategorized", "job_can", size_bytes=100, age_days=20)

    cleanup = LocalCleanup(
        run_root,
        store,
        policy=RetentionPolicy(completed_days=30, cancelled_days=10),
    )
    report = cleanup.cleanup_old_work_dirs()
    assert len(report.work_dirs_removed) == 1


def test_cleanup_orphan_dir_falls_back_to_completed(run_root: Path, store: JobStore):
    # Directory exists but no DB record → treated as orphan, completed window.
    _make_job_dir(run_root, "uncategorized", "orphan_job", size_bytes=200, age_days=35)
    cleanup = LocalCleanup(run_root, store, policy=RetentionPolicy(completed_days=30))
    report = cleanup.cleanup_old_work_dirs()
    assert len(report.work_dirs_removed) == 1


def test_cleanup_restart_failed_uses_short_window(run_root: Path, store: JobStore):
    # FAILED job whose error carries the [RESTART_FAILED] marker → 7-day window.
    restart = _make_record(
        "job_restart",
        status=JobStatus.FAILED,
        error="[RESTART_FAILED] interrupted by server restart",
    )
    _set_record_work_dir_and_store(store, run_root, restart, "uncategorized", "job_restart")
    _make_job_dir(run_root, "uncategorized", "job_restart", size_bytes=100, age_days=10)

    cleanup = LocalCleanup(run_root, store, policy=RetentionPolicy(failed_days=90))
    report = cleanup.cleanup_old_work_dirs()
    # 10 days > 7-day restart window → removed despite failed_days=90.
    assert len(report.work_dirs_removed) == 1


def test_cleanup_dry_run_no_mutation(run_root: Path, store: JobStore):
    old = _make_record("job_old", status=JobStatus.COMPLETED)
    _set_record_work_dir_and_store(store, run_root, old, "uncategorized", "job_old")
    _make_job_dir(run_root, "uncategorized", "job_old", size_bytes=300, age_days=31)

    cleanup = LocalCleanup(run_root, store, policy=RetentionPolicy(completed_days=30))
    report = cleanup.cleanup_old_work_dirs(dry_run=True)

    assert report.dry_run is True
    assert len(report.work_dirs_removed) == 1
    assert report.freed_bytes_est >= 300
    # Nothing actually deleted.
    assert (run_root / "uncategorized" / "job_old").exists()


def test_cleanup_respects_max_dirs_cap(run_root: Path, store: JobStore):
    for i in range(5):
        rec = _make_record(f"job_{i}", status=JobStatus.COMPLETED)
        _set_record_work_dir_and_store(store, run_root, rec, "uncategorized", f"job_{i}")
        _make_job_dir(run_root, "uncategorized", f"job_{i}", size_bytes=50, age_days=31)

    cleanup = LocalCleanup(run_root, store, policy=RetentionPolicy(completed_days=30))
    report = cleanup.cleanup_old_work_dirs(max_dirs_per_sweep=2)
    assert report.capped is True
    assert len(report.work_dirs_removed) == 2


def test_cleanup_max_dirs_zero_unlimited(run_root: Path, store: JobStore):
    for i in range(3):
        rec = _make_record(f"job_{i}", status=JobStatus.COMPLETED)
        _set_record_work_dir_and_store(store, run_root, rec, "uncategorized", f"job_{i}")
        _make_job_dir(run_root, "uncategorized", f"job_{i}", size_bytes=50, age_days=31)

    cleanup = LocalCleanup(run_root, store, policy=RetentionPolicy(completed_days=30))
    report = cleanup.cleanup_old_work_dirs(max_dirs_per_sweep=0)
    assert report.capped is False
    assert len(report.work_dirs_removed) == 3


def test_cleanup_unsafe_run_root_rejected(tmp_path: Path, store: JobStore):
    cleanup = LocalCleanup(tmp_path / "acp_runs", store)
    cleanup.run_root = Path("/")  # force unsafe
    report = cleanup.cleanup_old_work_dirs()
    assert any("unsafe" in e for e in report.errors)
    assert report.work_dirs_removed == []


def test_cleanup_never_deletes_project_dir(run_root: Path, store: JobStore):
    # Project dir that happens to look old.
    project = run_root / "uncategorized"
    project.mkdir(parents=True, exist_ok=True)
    ts = time.time() - 400 * 86400
    os.utime(project, (ts, ts))
    cleanup = LocalCleanup(run_root, store, policy=RetentionPolicy(completed_days=30))
    report = cleanup.cleanup_old_work_dirs()
    # No job dirs → nothing removed, project dir untouched.
    assert report.work_dirs_removed == []
    assert project.exists()


def test_cleanup_missing_run_root_is_noop(tmp_path: Path, store: JobStore):
    cleanup = LocalCleanup(tmp_path / "never_created", store)
    report = cleanup.cleanup_old_work_dirs()
    assert report.work_dirs_removed == []
    assert report.ok


def test_cleanup_report_to_dict(run_root: Path, store: JobStore):
    old = _make_record("job_old", status=JobStatus.COMPLETED)
    _set_record_work_dir_and_store(store, run_root, old, "uncategorized", "job_old")
    _make_job_dir(run_root, "uncategorized", "job_old", size_bytes=100, age_days=31)
    cleanup = LocalCleanup(run_root, store, policy=RetentionPolicy(completed_days=30))
    report = cleanup.cleanup_old_work_dirs()
    d = report.to_dict()
    assert d["work_dirs_removed_count"] == 1
    assert "freed_human" in d
    assert d["ok"] is True


# ====================================================================== #
# cleanup_old_db_records
# ====================================================================== #


def test_db_cleanup_deletes_old_rows(run_root: Path, store: JobStore):
    # Insert a row with an old completed_at by writing directly to SQLite
    # (JobRecord sets completed_at to now via _utc_now_iso otherwise).
    old_iso = "2020-01-01T00:00:00+00:00"
    rec = _make_record("job_db_old", status=JobStatus.COMPLETED, completed_at=old_iso)
    rec.work_dir = str(run_root / "uncategorized" / "job_db_old")
    store.create(rec)

    cleanup = LocalCleanup(run_root, store, policy=RetentionPolicy(db_record_days=365))
    report = cleanup.cleanup_old_db_records()
    assert report.db_records_removed == 1
    assert store.get("job_db_old") is None


def test_db_cleanup_keeps_recent_rows(run_root: Path, store: JobStore):
    rec = _make_record(
        "job_recent", status=JobStatus.COMPLETED, completed_at="2099-01-01T00:00:00+00:00"
    )
    rec.work_dir = str(run_root / "uncategorized" / "job_recent")
    store.create(rec)

    cleanup = LocalCleanup(run_root, store, policy=RetentionPolicy(db_record_days=365))
    report = cleanup.cleanup_old_db_records()
    assert report.db_records_removed == 0
    assert store.get("job_recent") is not None


def test_db_cleanup_dry_run(run_root: Path, store: JobStore):
    old_iso = "2020-01-01T00:00:00+00:00"
    rec = _make_record("job_db_old", status=JobStatus.COMPLETED, completed_at=old_iso)
    rec.work_dir = str(run_root / "uncategorized" / "job_db_old")
    store.create(rec)

    cleanup = LocalCleanup(run_root, store, policy=RetentionPolicy(db_record_days=365))
    report = cleanup.cleanup_old_db_records(dry_run=True)
    assert report.db_records_removed == 1
    assert report.dry_run is True
    # Not actually deleted.
    assert store.get("job_db_old") is not None


def test_db_cleanup_vacuum(run_root: Path, store: JobStore):
    old_iso = "2020-01-01T00:00:00+00:00"
    rec = _make_record("job_vac", status=JobStatus.COMPLETED, completed_at=old_iso)
    rec.work_dir = str(run_root / "uncategorized" / "job_vac")
    store.create(rec)

    cleanup = LocalCleanup(
        run_root,
        store,
        policy=RetentionPolicy(db_record_days=365, vacuum_after_db_cleanup=True),
    )
    report = cleanup.cleanup_old_db_records()
    assert report.db_records_removed == 1
    assert report.db_vacuumed is True


# ====================================================================== #
# full_cleanup
# ====================================================================== #


def test_full_cleanup_runs_both(run_root: Path, store: JobStore):
    _make_job_dir(run_root, "uncategorized", "old_dir", size_bytes=100, age_days=31)
    old_iso = "2020-01-01T00:00:00+00:00"
    rec = _make_record("old_dir", status=JobStatus.COMPLETED, completed_at=old_iso)
    rec.work_dir = str(run_root / "uncategorized" / "old_dir")
    store.create(rec)

    cleanup = LocalCleanup(
        run_root, store, policy=RetentionPolicy(completed_days=30, db_record_days=365)
    )
    report = cleanup.full_cleanup()
    assert len(report.work_dirs_removed) == 1
    assert report.db_records_removed == 1
    assert report.disk_usage_before >= 0
    assert report.disk_usage_after >= 0


def test_full_cleanup_dry_run(run_root: Path, store: JobStore):
    _make_job_dir(run_root, "uncategorized", "old_dir", size_bytes=100, age_days=31)
    old_iso = "2020-01-01T00:00:00+00:00"
    rec = _make_record("old_dir", status=JobStatus.COMPLETED, completed_at=old_iso)
    rec.work_dir = str(run_root / "uncategorized" / "old_dir")
    store.create(rec)

    cleanup = LocalCleanup(run_root, store)
    report = cleanup.full_cleanup(dry_run=True)
    assert report.dry_run is True
    # Nothing deleted.
    assert (run_root / "uncategorized" / "old_dir").exists()
    assert store.get("old_dir") is not None


# ====================================================================== #
# pre_submit_housekeeping
# ====================================================================== #


def test_housekeeping_low_disk_noop(run_root: Path, store: JobStore):
    cleanup = LocalCleanup(run_root, store)
    decision = cleanup.pre_submit_housekeeping()
    assert decision.should_skip is False
    assert decision.cleanup is None
    assert "no cleanup needed" in decision.reason


def test_housekeeping_high_disk_triggers_cleanup(monkeypatch, run_root: Path, store: JobStore):
    cleanup = LocalCleanup(run_root, store, policy=RetentionPolicy(completed_days=30))
    calls = {"cleanup": 0}

    def fake_sweep(dry_run=False, max_dirs_per_sweep=None):
        calls["cleanup"] += 1
        return LocalCleanupReport()

    monkeypatch.setattr(cleanup, "check_disk_usage", lambda: 92)
    monkeypatch.setattr(cleanup, "cleanup_old_work_dirs", fake_sweep)
    decision = cleanup.pre_submit_housekeeping()
    assert decision.should_skip is False
    assert calls["cleanup"] == 1
    assert decision.cleanup is not None


def test_housekeeping_skip_threshold_rejects(monkeypatch, run_root: Path, store: JobStore):
    cleanup = LocalCleanup(run_root, store)
    monkeypatch.setattr(cleanup, "check_disk_usage", lambda: 96)
    monkeypatch.setattr(cleanup, "cleanup_old_work_dirs", lambda **kw: LocalCleanupReport())
    decision = cleanup.pre_submit_housekeeping()
    assert decision.should_skip is True
    assert "skip threshold" in decision.reason


def test_housekeeping_high_disk_no_cleanup_possible(monkeypatch, run_root: Path, store: JobStore):
    cleanup = LocalCleanup(run_root, store)
    # before=92 triggers cleanup, but after stays 96 → still rejected.
    monkeypatch.setattr(cleanup, "check_disk_usage", lambda: 96)
    monkeypatch.setattr(cleanup, "cleanup_old_work_dirs", lambda **kw: LocalCleanupReport())
    decision = cleanup.pre_submit_housekeeping()
    assert decision.should_skip is True


def test_housekeeping_disk_query_fail_open(monkeypatch, run_root: Path, store: JobStore):
    cleanup = LocalCleanup(run_root, store)

    def boom():
        raise OSError("stat boom")

    monkeypatch.setattr(cleanup, "check_disk_usage", boom)
    decision = cleanup.pre_submit_housekeeping()
    # OSError → 0 → below cleanup_threshold → proceed (fail-open).
    assert decision.should_skip is False
    assert decision.disk_usage_before == 0


def test_housekeeping_after_probe_failure_conservative(
    monkeypatch, run_root: Path, store: JobStore
):
    cleanup = LocalCleanup(run_root, store)
    # before=96 triggers cleanup; after-probe fails (0) → conservative
    # fallback keeps 96 → should_skip.
    seq = iter([96, 0])

    def fake():
        return next(seq)

    monkeypatch.setattr(cleanup, "check_disk_usage", fake)
    monkeypatch.setattr(cleanup, "cleanup_old_work_dirs", lambda **kw: LocalCleanupReport())
    decision = cleanup.pre_submit_housekeeping()
    assert decision.should_skip is True
    assert decision.disk_usage_after == 96  # fell back to before


def test_housekeeping_cleanup_crash_recorded(monkeypatch, run_root: Path, store: JobStore):
    cleanup = LocalCleanup(run_root, store)

    def boom(**kw):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(cleanup, "check_disk_usage", lambda: 92)
    monkeypatch.setattr(cleanup, "cleanup_old_work_dirs", boom)
    decision = cleanup.pre_submit_housekeeping()
    # Crash captured, not raised; cleanup is non-None with error recorded.
    assert decision.cleanup is not None
    assert any("sweep exploded" in e for e in decision.cleanup.errors)


def test_housekeeping_decision_to_dict(run_root: Path, store: JobStore):
    cleanup = LocalCleanup(run_root, store)
    decision = cleanup.pre_submit_housekeeping()
    d = decision.to_dict()
    assert "should_skip" in d and "reason" in d


# ====================================================================== #
# JobRunner integration (local branch)
# ====================================================================== #


def _runner_record(run_root: Path) -> tuple[JobRunner, JobRecord, JobEventLog, Path]:
    job_dir = run_root / "uncategorized" / "job_x"
    job_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id="job_x",
        spec=JobSpec(workflow="fake", name="job_x", project_id="uncategorized"),
        status=JobStatus.RUNNING,
        work_dir=str(job_dir),
        project_id="uncategorized",
    )
    log = JobEventLog(job_dir / "events.jsonl")
    return JobRunner(), record, log, job_dir


def test_runner_local_cleanup_none_is_noop(run_root: Path):
    runner, record, log, _ = _runner_record(run_root)
    runner.local_cleanup = None
    skip = runner._pre_submit_housekeeping_local(record, log)
    assert skip is False


def test_runner_normal_path_emits_event(monkeypatch, run_root: Path):
    from acp.scheduler.local_cleanup import LocalHousekeepingDecision

    runner, record, log, job_dir = _runner_record(run_root)
    fake = type(
        "Fake",
        (),
        {
            "pre_submit_housekeeping": lambda self: LocalHousekeepingDecision(
                should_skip=False,
                disk_usage_before=40,
                disk_usage_after=40,
                cleanup=None,
                reason="ok",
            )
        },
    )()
    runner.local_cleanup = fake  # type: ignore[assignment]
    skip = runner._pre_submit_housekeeping_local(record, log)
    assert skip is False
    text = (job_dir / "events.jsonl").read_text()
    assert "local.housekeeping" in text


def test_runner_skip_returns_true_and_sets_error(monkeypatch, run_root: Path):
    from acp.scheduler.local_cleanup import LocalHousekeepingDecision

    runner, record, log, _ = _runner_record(run_root)
    fake = type(
        "Fake",
        (),
        {
            "pre_submit_housekeeping": lambda self: LocalHousekeepingDecision(
                should_skip=True,
                disk_usage_before=96,
                disk_usage_after=96,
                cleanup=None,
                reason="disk usage 96% exceeds skip threshold 95%",
            )
        },
    )()
    runner.local_cleanup = fake  # type: ignore[assignment]
    skip = runner._pre_submit_housekeeping_local(record, log)
    assert skip is True
    assert "disk full" in (record.error or "").lower()
    assert "96" in (record.error or "")


def test_runner_housekeeping_crash_fail_open(run_root: Path):
    runner, record, log, job_dir = _runner_record(run_root)
    fake = type(
        "Fake",
        (),
        {"pre_submit_housekeeping": lambda self: (_ for _ in ()).throw(RuntimeError("boom"))},
    )()
    runner.local_cleanup = fake  # type: ignore[assignment]
    skip = runner._pre_submit_housekeeping_local(record, log)
    assert skip is False  # fail-open
    text = (job_dir / "events.jsonl").read_text()
    assert "local.housekeeping_error" in text


# ====================================================================== #
# JobManager wiring (background thread + cleanup.log)
# ====================================================================== #


def test_manager_local_cleanup_enabled(run_root: Path):
    from acp.scheduler.manager import JobManager

    mgr = JobManager(run_root=run_root, local_retention_config=RetentionPolicy(completed_days=30))
    try:
        assert mgr.local_cleanup is not None
        assert mgr.runner.local_cleanup is mgr.local_cleanup
        assert mgr._cleanup_thread is not None
        assert mgr._cleanup_thread.is_alive()
    finally:
        mgr.shutdown()


def test_manager_local_cleanup_disabled(run_root: Path):
    from acp.scheduler.manager import JobManager

    mgr = JobManager(run_root=run_root, local_retention_config=None)
    try:
        assert mgr.local_cleanup is None
        assert mgr.runner.local_cleanup is None
        assert mgr._cleanup_thread is None
    finally:
        mgr.shutdown()


def test_manager_shutdown_stops_cleanup_thread(run_root: Path):
    from acp.scheduler.manager import JobManager

    mgr = JobManager(
        run_root=run_root,
        local_retention_config=RetentionPolicy(completed_days=30),
        local_cleanup_interval_hours=1,
    )
    thread = mgr._cleanup_thread
    assert thread is not None
    mgr.shutdown()
    assert not thread.is_alive()


def test_manager_trigger_local_cleanup_writes_log(run_root: Path):
    from acp.scheduler.manager import JobManager

    mgr = JobManager(run_root=run_root, local_retention_config=RetentionPolicy(completed_days=30))
    try:
        report = mgr.trigger_local_cleanup()
        assert report is not None
        log_path = run_root / "cleanup.log"
        assert log_path.exists()
        line = log_path.read_text().strip().splitlines()[-1]
        assert '"work_dirs_removed_count"' in line or '"work_dirs_removed": []' in line
    finally:
        mgr.shutdown()


def test_manager_restart_failed_marker_applied(run_root: Path):
    """A RUNNING job at startup is re-marked FAILED with the marker."""
    from acp.scheduler.manager import JobManager

    # Seed a store with an active job.
    store = JobStore(run_root / "acp_jobs.db")
    active = JobRecord(
        id="active_job",
        spec=JobSpec(workflow="fake", name="active_job"),
        status=JobStatus.RUNNING,
        work_dir=str(run_root / "uncategorized" / "active_job"),
    )
    store.create(active)

    mgr = JobManager(run_root=run_root, store=store, local_retention_config=None)
    try:
        rec = mgr.get("active_job")
        assert rec is not None
        assert rec.status == JobStatus.FAILED
        assert "[RESTART_FAILED]" in (rec.error or "")
    finally:
        mgr.shutdown()


# ====================================================================== #
# API endpoints (require fastapi/httpx)
# ====================================================================== #

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")


def _build_test_app(run_root: Path):
    from fastapi import FastAPI

    from acp.api.v1_routes import router as v1_router
    from acp.scheduler.local_cleanup import RetentionPolicy
    from acp.scheduler.manager import JobManager

    app = FastAPI()
    app.include_router(v1_router, prefix="/api/v1")
    mgr = JobManager(run_root=run_root, local_retention_config=RetentionPolicy(completed_days=30))

    @app.on_event("startup")
    async def _startup():
        app.state.job_manager = mgr
        app.state.db_path = str(mgr.store.db_path)
        app.state.run_root = str(run_root)

    @app.on_event("shutdown")
    async def _shutdown():
        mgr.shutdown()

    return app, mgr


def test_api_disk_usage(run_root: Path):
    from fastapi.testclient import TestClient

    app, mgr = _build_test_app(run_root)
    with TestClient(app) as client:
        resp = client.get("/api/v1/maintenance/disk-usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cleanup_enabled"] is True
        assert 0 <= data["percent_used"] <= 100
        assert data["run_root"] == str(run_root)


def test_api_cleanup_dry_run(run_root: Path):
    from fastapi.testclient import TestClient

    app, mgr = _build_test_app(run_root)
    with TestClient(app) as client:
        resp = client.post("/api/v1/maintenance/cleanup?dry_run=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is True
        assert "freed_human" in data


def test_api_cleanup_scope_work_dirs(run_root: Path):
    from fastapi.testclient import TestClient

    app, mgr = _build_test_app(run_root)
    with TestClient(app) as client:
        resp = client.post("/api/v1/maintenance/cleanup?scope=work_dirs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_records_removed"] == 0


def test_api_cleanup_bad_scope(run_root: Path):
    from fastapi.testclient import TestClient

    app, mgr = _build_test_app(run_root)
    with TestClient(app) as client:
        resp = client.post("/api/v1/maintenance/cleanup?scope=bogus")
        assert resp.status_code == 400


def test_api_cleanup_disabled_returns_503(tmp_path: Path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from acp.api.v1_routes import router as v1_router
    from acp.scheduler.manager import JobManager

    rr = tmp_path / "runs"
    app = FastAPI()
    app.include_router(v1_router, prefix="/api/v1")
    mgr = JobManager(run_root=rr, local_retention_config=None)

    @app.on_event("startup")
    async def _startup():
        app.state.job_manager = mgr
        app.state.db_path = str(mgr.store.db_path)
        app.state.run_root = str(rr)

    @app.on_event("shutdown")
    async def _shutdown():
        mgr.shutdown()

    with TestClient(app) as client:
        resp = client.post("/api/v1/maintenance/cleanup")
        assert resp.status_code == 503


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
