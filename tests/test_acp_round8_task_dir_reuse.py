"""Round-8 三.1: task-directory reuse and failed-adoption cleanup invariants.

Two invariants keep the on-disk task identity honest:

* allocation never reuses a leaf that carries another job's ``task.json``;
* a failed adoption never deletes files this flow did not create — the
  directory is kept, logged, and recorded as ``task.dir_adoption_failed_leftover``
  (an event, never a new marker file).

The 008/022 root cause itself stays ``NOT_VERIFIED``; these tests pin the
invariants that make a silent leaf reuse impossible, not the historical cause.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from acp.scheduler.jobs import JobSpec
from acp.scheduler.manager import (
    _CLAIM_BINDING_NAME,
    _DIR_ADOPTION_FAILED_EVENT,
    JobManager,
    _foreign_task_dir_entries,
    _release_task_dir,
)
from acp.scheduler.runner import JobRunner
from acp_services.runtime.idempotency import IdempotencyContext, resolve_scope_id
from acp_services.store.store import JobStore

PROJECT_LEAF = "default_project"
FOREIGN_TASK_ID = "20261101_000000_999_fake"
CURRENT_JOB_ID = "20261102_000000_001_reuse"


class _Projects:
    """Project leaf resolver stub: the directory-reuse tests need no DB project."""

    def dir_leaf_for(self, project_id: str) -> str:
        return PROJECT_LEAF


def _spec() -> JobSpec:
    return JobSpec(workflow="fake", name="reuse", input={"source": "C"})


def _bare_manager(tmp_path: Path) -> JobManager:
    manager = JobManager.__new__(JobManager)
    manager.run_root = tmp_path / "run_root"
    manager.run_root.mkdir(parents=True, exist_ok=True)
    manager.default_project_id = "default"
    manager._projects = _Projects()
    return manager


def _real_manager(tmp_path: Path) -> JobManager:
    run_root = tmp_path / "run_root"
    run_root.mkdir()
    store = JobStore(run_root / "acp_jobs.db")
    return JobManager(
        run_root,
        store=store,
        runner=JobRunner(python_executable=sys.executable),
        poll_interval=5,
    )


def _context(manager: JobManager, key: str) -> IdempotencyContext:
    return IdempotencyContext(
        key=key,
        scope_id=resolve_scope_id("principal_local", manager.default_project_id),
        request_payload={"workflow": "fake", "name": "reuse", "input": {"source": "C"}},
        lease_seconds=60.0,
        wait_seconds=1.0,
    )


def _fail_finalize(monkeypatch: pytest.MonkeyPatch, manager: JobManager) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("finalize failed for the round-8 test")

    monkeypatch.setattr(manager.store, "finalize_idempotent_job", _boom)


def _write_foreign_owner(work_dir: Path, task_id: str = FOREIGN_TASK_ID) -> None:
    work_dir.mkdir(parents=True)
    (work_dir / "task.json").write_text(
        json.dumps({"task_id": task_id, "layout_version": 2}), encoding="utf-8"
    )


def test_existing_foreign_task_json_is_never_reused(tmp_path: Path) -> None:
    manager = _bare_manager(tmp_path)
    spec = _spec()
    project_root = manager.run_root.resolve() / PROJECT_LEAF
    foreign = project_root / spec.task_dir_name()
    _write_foreign_owner(foreign)
    (foreign / "state.json").write_text('{"owner": "job-008"}', encoding="utf-8")
    (foreign / "WORK").mkdir()

    allocated = manager._allocate_work_dir(spec, CURRENT_JOB_ID)

    assert allocated.name == f"{spec.task_dir_name()}__02"
    assert allocated.is_dir()
    # The other job's directory keeps every byte it had.
    assert json.loads((foreign / "task.json").read_text(encoding="utf-8"))["task_id"] == (
        FOREIGN_TASK_ID
    )
    assert (foreign / "state.json").read_text() == '{"owner": "job-008"}'
    assert (foreign / "WORK").is_dir()


def test_racing_owner_in_the_claimed_leaf_is_skipped_and_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``task.json`` written into the fresh leaf after ``mkdir`` is not adopted."""
    manager = _bare_manager(tmp_path)
    spec = _spec()
    project_root = manager.run_root.resolve() / PROJECT_LEAF
    project_root.mkdir(parents=True)
    contested = project_root / spec.task_dir_name()
    real_mkdir = Path.mkdir

    def racing_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        real_mkdir(self, *args, **kwargs)
        if self == contested and kwargs.get("exist_ok") is False:
            (self / "task.json").write_text(
                json.dumps({"task_id": "another-live-job"}), encoding="utf-8"
            )

    monkeypatch.setattr(Path, "mkdir", racing_mkdir)

    allocated = manager._allocate_work_dir(spec, CURRENT_JOB_ID)

    assert allocated.name == f"{spec.task_dir_name()}__02"
    # The contested leaf still belongs to its writer: nothing was deleted.
    assert contested.is_dir()
    assert json.loads((contested / "task.json").read_text(encoding="utf-8"))["task_id"] == (
        "another-live-job"
    )


def test_normal_allocation_still_uses_the_plain_leaf(tmp_path: Path) -> None:
    manager = _bare_manager(tmp_path)
    spec = _spec()

    allocated = manager._allocate_work_dir(spec, CURRENT_JOB_ID)

    assert allocated == manager.run_root.resolve() / PROJECT_LEAF / spec.task_dir_name()
    assert allocated.is_dir()


def test_release_task_dir_never_removes_foreign_content(tmp_path: Path) -> None:
    foreign = tmp_path / "task"
    foreign.mkdir()
    (foreign / _CLAIM_BINDING_NAME).write_text("{}", encoding="utf-8")
    (foreign / "state.json").write_text("evidence", encoding="utf-8")

    assert _release_task_dir(foreign, ignore=(_CLAIM_BINDING_NAME,)) is False
    assert sorted(entry.name for entry in foreign.iterdir()) == [
        _CLAIM_BINDING_NAME,
        "state.json",
    ]


def test_release_task_dir_removes_a_dir_holding_only_our_binding(tmp_path: Path) -> None:
    own = tmp_path / "own"
    own.mkdir()
    (own / _CLAIM_BINDING_NAME).write_text("{}", encoding="utf-8")

    assert _release_task_dir(own, ignore=(_CLAIM_BINDING_NAME,)) is True
    assert not own.exists()


def test_failed_adoption_keeps_leftovers_and_records_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _real_manager(tmp_path)
    try:
        final_dir = manager.run_root / "reuse_fake"
        final_dir.mkdir()
        (final_dir / "WORK").mkdir()
        (final_dir / "state.json").write_text('{"owner": "job-008"}', encoding="utf-8")
        manager._allocate_work_dir = lambda spec, job_id: final_dir  # type: ignore[method-assign]

        removed: list[Path] = []
        real_rmtree = shutil.rmtree

        def recording_rmtree(path: object, *args: object, **kwargs: object) -> None:
            removed.append(Path(str(path)))
            real_rmtree(str(path), *args, **kwargs)

        monkeypatch.setattr("acp.scheduler.manager.shutil.rmtree", recording_rmtree)
        _fail_finalize(monkeypatch, manager)

        with pytest.raises(RuntimeError, match="finalize failed"):
            manager.submit(_spec(), idempotency=_context(manager, "round8-leftover"))

        assert final_dir.is_dir()
        assert final_dir not in removed
        assert (final_dir / "state.json").read_text() == '{"owner": "job-008"}'
        events = [
            json.loads(line)
            for line in (final_dir / "WORK" / "00_RUNTIME" / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert [event["type"] for event in events] == [_DIR_ADOPTION_FAILED_EVENT]
        assert events[0]["job_id"].endswith("_reuse")
        assert "state.json" in events[0]["leftover"]
        assert events[0]["work_dir"] == str(final_dir)
    finally:
        manager.shutdown()


def test_failed_adoption_removes_only_the_empty_flow_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _real_manager(tmp_path)
    try:
        final_dir = manager.run_root / "empty_task"

        def _allocate(spec: JobSpec, job_id: str) -> Path:
            final_dir.mkdir(parents=True, exist_ok=False)
            return final_dir

        manager._allocate_work_dir = _allocate  # type: ignore[method-assign]
        _fail_finalize(monkeypatch, manager)

        with pytest.raises(RuntimeError, match="finalize failed"):
            manager.submit(_spec(), idempotency=_context(manager, "round8-empty"))

        assert not final_dir.exists()
        staging_root = manager.run_root / ".staging"
        assert list(staging_root.glob("*")) == []
    finally:
        manager.shutdown()


def test_foreign_task_dir_entries_reports_unreadable_paths(tmp_path: Path) -> None:
    assert _foreign_task_dir_entries(tmp_path / "missing") is None
    present = tmp_path / "present"
    present.mkdir()
    (present / "b").write_text("", encoding="utf-8")
    (present / "a").write_text("", encoding="utf-8")
    assert _foreign_task_dir_entries(present) == ["a", "b"]
    assert _foreign_task_dir_entries(present, ignore=("a",)) == ["b"]
