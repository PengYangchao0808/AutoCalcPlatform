"""Tests for the ProgressReporter (calculations/progress.py)."""

from __future__ import annotations

import json
from pathlib import Path

from acp.calculations.progress import ProgressReporter


def _read_state(work_dir: Path) -> dict:
    return json.loads((work_dir / "state.json").read_text(encoding="utf-8"))


class TestProgressReporterInit:
    def test_initialize_creates_state_json(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, job_name="test", stages=["a", "b"])
        reporter.initialize()
        data = _read_state(tmp_path)
        assert data["job_name"] == "test"
        assert data["status"] == "running"
        assert data["current_stage"] is None
        assert data["stage_total"] == 2
        assert data["progress_state"] == "indeterminate"
        assert set(data["stages"].keys()) == {"a", "b"}
        assert all(s["status"] == "pending" for s in data["stages"].values())

    def test_initialize_empty_stages(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path)
        reporter.initialize()
        data = _read_state(tmp_path)
        assert data["stage_total"] == 0
        assert data["stages"] == {}


class TestStageLifecycle:
    def test_start_stage(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["prepare", "scan"])
        reporter.initialize()
        reporter.start_stage("prepare")
        data = _read_state(tmp_path)
        assert data["current_stage"] == "prepare"
        assert data["stages"]["prepare"]["status"] == "running"
        assert "started_at" in data["stages"]["prepare"]
        assert data["stage_index"] == 1

    def test_complete_stage(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["prepare", "scan"])
        reporter.initialize()
        reporter.start_stage("prepare")
        reporter.complete_stage("prepare")
        data = _read_state(tmp_path)
        assert data["stages"]["prepare"]["status"] == "completed"
        assert "completed_at" in data["stages"]["prepare"]
        assert data["current_stage"] is None

    def test_fail_stage(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["prepare"])
        reporter.initialize()
        reporter.start_stage("prepare")
        reporter.fail_stage("prepare", "ORCA timeout")
        data = _read_state(tmp_path)
        assert data["stages"]["prepare"]["status"] == "failed"
        assert data["stages"]["prepare"]["error"] == "ORCA timeout"
        assert data["status"] == "failed"

    def test_auto_registers_unknown_stage(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["a"])
        reporter.initialize()
        reporter.start_stage("extra")
        data = _read_state(tmp_path)
        assert "extra" in data["stages"]
        assert data["stage_total"] == 2

    def test_stage_index_1_based(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["a", "b", "c"])
        reporter.initialize()
        reporter.start_stage("b")
        data = _read_state(tmp_path)
        assert data["stage_index"] == 2
        assert data["stage_total"] == 3


class TestSubStageProgress:
    def test_update_stage(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["scan"], min_interval=0.0)
        reporter.initialize()
        reporter.start_stage("scan")
        reporter.update_stage("scan", completed=17, total=40)
        data = _read_state(tmp_path)
        assert data["stages"]["scan"]["progress"] == 0.425
        assert data["stages"]["scan"]["detail"] == "17/40"
        assert data["stage_progress"] == 0.425
        assert data["stage_detail"] == "17/40"

    def test_update_stage_custom_detail(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["sp"], min_interval=0.0)
        reporter.initialize()
        reporter.start_stage("sp")
        reporter.update_stage("sp", completed=5, total=20, detail="5/20 single points")
        data = _read_state(tmp_path)
        assert data["stage_detail"] == "5/20 single points"

    def test_progress_state_determinate_when_sub_progress(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["scan"], min_interval=0.0)
        reporter.initialize()
        reporter.start_stage("scan")
        reporter.update_stage("scan", completed=1, total=10)
        data = _read_state(tmp_path)
        assert data["progress_state"] == "determinate"

    def test_progress_state_indeterminate_when_no_sub_progress(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["scan"], min_interval=0.0)
        reporter.initialize()
        reporter.start_stage("scan")
        data = _read_state(tmp_path)
        assert data["progress_state"] == "indeterminate"

    def test_update_stage_ignores_unknown_stage(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["a"], min_interval=0.0)
        reporter.initialize()
        reporter.update_stage("nonexistent", completed=1, total=10)  # should not raise


class TestOverallProgress:
    def test_overall_blends_completed_and_current(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["a", "b", "c", "d"], min_interval=0.0)
        reporter.initialize()
        reporter.start_stage("a")
        reporter.complete_stage("a")
        reporter.start_stage("b")
        reporter.complete_stage("b")
        reporter.start_stage("c")
        reporter.update_stage("c", completed=1, total=2)  # 50% through stage c
        data = _read_state(tmp_path)
        # 2 completed + 0.5 current = 2.5 / 4 = 0.625
        assert data["overall_progress"] == 0.625

    def test_overall_all_completed(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["a", "b"], min_interval=0.0)
        reporter.initialize()
        reporter.start_stage("a")
        reporter.complete_stage("a")
        reporter.start_stage("b")
        reporter.complete_stage("b")
        data = _read_state(tmp_path)
        assert data["overall_progress"] == 1.0

    def test_complete_marks_all_done(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["a", "b"], min_interval=0.0)
        reporter.initialize()
        reporter.start_stage("a")
        reporter.complete()  # should complete all stages
        data = _read_state(tmp_path)
        assert data["status"] == "completed"
        assert data["overall_progress"] == 1.0
        assert all(s["status"] == "completed" for s in data["stages"].values())

    def test_fail_marks_failed(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["a"], min_interval=0.0)
        reporter.initialize()
        reporter.start_stage("a")
        reporter.fail("fatal error")
        data = _read_state(tmp_path)
        assert data["status"] == "failed"


class TestThrottling:
    def test_throttled_writes_skip(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["a"], min_interval=60.0)
        reporter.initialize()
        reporter.start_stage("a")
        # This update should be throttled (within min_interval)
        reporter.update_stage("a", completed=1, total=10)
        # Force write to see the final state
        reporter.complete_stage("a")
        data = _read_state(tmp_path)
        assert data["stages"]["a"]["status"] == "completed"

    def test_force_writes_always(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["a"], min_interval=999.0)
        reporter.initialize()
        reporter.start_stage("a")
        reporter.complete_stage("a")
        data = _read_state(tmp_path)
        assert data["stages"]["a"]["status"] == "completed"


class TestAtomicWrite:
    def test_state_json_is_valid_json(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["a"], min_interval=0.0)
        reporter.initialize()
        reporter.start_stage("a")
        reporter.update_stage("a", completed=3, total=7)
        reporter.complete_stage("a")
        reporter.complete()
        # Should be parseable
        data = _read_state(tmp_path)
        assert isinstance(data, dict)
        assert "stages" in data
        assert "overall_progress" in data

    def test_no_tmp_file_left(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["a"])
        reporter.initialize()
        reporter.start_stage("a")
        reporter.complete()
        assert not (tmp_path / "state.tmp").exists()
        assert (tmp_path / "state.json").exists()


class TestSchemaCompat:
    """Verify state.json matches the schema _observe_state expects."""

    def test_has_current_stage_key(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["prepare"])
        reporter.initialize()
        reporter.start_stage("prepare")
        data = _read_state(tmp_path)
        assert "current_stage" in data
        assert "stages" in data
        assert isinstance(data["stages"], dict)

    def test_stages_have_status_key(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["a", "b"])
        reporter.initialize()
        data = _read_state(tmp_path)
        for stage_info in data["stages"].values():
            assert "status" in stage_info

    def test_overall_progress_key_present(self, tmp_path: Path) -> None:
        reporter = ProgressReporter(tmp_path, stages=["a"])
        reporter.initialize()
        data = _read_state(tmp_path)
        assert "overall_progress" in data
        assert isinstance(data["overall_progress"], float)
