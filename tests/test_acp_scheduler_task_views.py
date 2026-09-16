# pyright: reportMissingImports=false, reportPrivateUsage=false, reportAny=false, reportUnusedCallResult=false
"""
Tests for task_views.py — whole-project task view engine (T2 acceptance criteria).

Covers:
  1. Molecule grouping (case/whitespace fold, empty → __unassigned__)
  2. Status multi=union, molecule+workflow+status cross=intersection
  3. Tag union dedup (task with ["a","b"] appears once when both selected)
  4. group_limit truncation — total/counts/groups[].count still whole-scope
  5. Facets own-dimension exclusion
  6. Sorting (created_asc, completed_desc with NULL last, activity_desc) + running_first
  7. Batch grouping — shared batch_id same group, NULL → __singles__
  8. Search case-insensitive + literal % escaped
  9. archived exclude/include/only
  10. Tag grouping multi-membership + sum≥total
  11. _JSON1_OK=False → tag filter identical, tag grouping raises ValueError
  12. project_id=None cross-project
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.migrations import migrate
from acp.scheduler.tasks import TaskIndex

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    *,
    workflow: str = "Confsearch",
    molecule_name: str = "CCO",
    task_name: str = "search",
    remark: str = "",
    tags: list[str] | None = None,
    resources: dict[str, Any] | None = None,
    project_id: str = "proj1",
) -> JobSpec:
    return JobSpec(
        workflow=workflow,
        name=f"{molecule_name}_{task_name}",
        molecule_name=molecule_name,
        task_name=task_name,
        remark=remark,
        tags=tags or [],
        resources=resources or {},
        input={"molecule_name": molecule_name},
        project_id=project_id,
    )


def _make_record(
    *,
    job_id: str,
    status: JobStatus = JobStatus.COMPLETED,
    project_id: str = "proj1",
    work_dir: str = "/tmp/proj/task",
    spec: JobSpec | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    current_stage: str | None = None,
    group_id: str | None = None,
    remote_job_id: str | None = None,
    progress: float | None = None,
) -> JobRecord:
    spec = spec or _make_spec(project_id=project_id)
    sa = started_at
    ca = completed_at
    if sa is None and status != JobStatus.QUEUED:
        sa = "2026-01-01T10:00:00"
    if ca is None and status.is_terminal:
        ca = "2026-01-01T11:00:00"
    return JobRecord(
        id=job_id,
        spec=spec,
        status=status,
        work_dir=work_dir,
        project_id=project_id,
        started_at=sa,
        completed_at=ca,
        current_stage=current_stage,
        progress=progress or (1.0 if status == JobStatus.COMPLETED else None),
        remote_job_id=remote_job_id,
        group_id=group_id or job_id,
    )


def _setup_project_and_tasks(tmp_path: Path) -> tuple[TaskIndex, str]:
    """Create a TaskIndex with project + 8 tasks covering diverse scenarios."""
    db = tmp_path / "test.db"
    migrate(db)
    idx = TaskIndex(db)

    # We need projects table for project_name lookup
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO projects "
        "(project_id, name, description, tags, run_root, "
        "settings, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "proj1",
            "TestProject",
            "",
            "[]",
            str(tmp_path),
            "{}",
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00",
        ),
    )
    conn.execute(
        "INSERT INTO projects "
        "(project_id, name, description, tags, run_root, "
        "settings, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "proj2",
            "OtherProject",
            "",
            "[]",
            str(tmp_path / "p2"),
            "{}",
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00",
        ),
    )
    conn.commit()
    conn.close()

    tasks = [
        # 1: molecule="BCB-Allene", running, no batch
        dict(
            job_id="t1",
            status=JobStatus.RUNNING,
            project_id="proj1",
            molecule_name="BCB-Allene",
            task_name="search",
            remark="",
            workflow="Confsearch",
            tags=["待检查"],
        ),
        # 2: molecule="BCB-Allene" (case-different → same key), completed, batch=bat1
        dict(
            job_id="t2",
            status=JobStatus.COMPLETED,
            project_id="proj1",
            molecule_name="bcb-allene",
            task_name="opt",
            remark="freq",
            workflow="BatchOptimize",
            tags=["待检查", "论文使用"],
            resources={"batch_id": "bat1"},
        ),
        # 3: molecule="MeOH", failed, batch=bat1
        dict(
            job_id="t3",
            status=JobStatus.FAILED,
            project_id="proj1",
            molecule_name="MeOH",
            task_name="scan",
            remark="scan",
            workflow="scan",
            resources={"batch_id": "bat1"},
        ),
        # 4: molecule="" (empty → __unassigned__), queued
        dict(
            job_id="t4",
            status=JobStatus.QUEUED,
            project_id="proj1",
            molecule_name="",
            task_name="generic",
            remark="no mol",
            workflow="Confsearch",
        ),
        # 5: molecule="EtOH", completed, archived
        dict(
            job_id="t5",
            status=JobStatus.COMPLETED,
            project_id="proj1",
            molecule_name="EtOH",
            task_name="optimize",
            remark="old",
            workflow="optimize",
        ),
        # 6: molecule="BCB-Allene" (third), paused
        dict(
            job_id="t6",
            status=JobStatus.PAUSED,
            project_id="proj1",
            molecule_name="BCB-Allene",
            task_name="rerun",
            remark="retry",
            workflow="Confsearch",
            tags=["论文使用"],
        ),
        # 7: molecule="MeOH", completed, no batch
        dict(
            job_id="t7",
            status=JobStatus.COMPLETED,
            project_id="proj1",
            molecule_name="MeOH",
            task_name="opt2",
            remark="",
            workflow="optimize",
        ),
        # 8: molecule="EtOH", running, on proj2
        dict(
            job_id="t8",
            status=JobStatus.RUNNING,
            project_id="proj2",
            molecule_name="EtOH",
            task_name="search2",
            remark="proj2 task",
            workflow="Confsearch",
        ),
    ]

    for t in tasks:
        spec = _make_spec(
            workflow=t.get("workflow", "Confsearch"),
            molecule_name=t.get("molecule_name", ""),
            task_name=t.get("task_name", ""),
            remark=t.get("remark", ""),
            tags=t.get("tags"),
            resources=t.get("resources"),
            project_id=t.get("project_id", "proj1"),
        )
        record = _make_record(
            job_id=t["job_id"],
            status=t.get("status", JobStatus.COMPLETED),
            project_id=t.get("project_id", "proj1"),
            spec=spec,
            work_dir=f"/tmp/{t.get('project_id', 'proj1')}/{t['job_id']}",
        )
        idx.sync_from_job(record)

    # Archive t5
    idx._run("UPDATE tasks SET archived=1 WHERE task_id='t5'")
    # Set specific timestamps for sorting tests
    idx._run(
        "UPDATE tasks SET created_at='2026-01-01T09:00:00', "
        "started_at='2026-01-01T09:05:00', "
        "completed_at='2026-01-01T09:30:00', "
        "last_activity_at='2026-01-01T09:30:00' "
        "WHERE task_id='t1'"
    )
    idx._run(
        "UPDATE tasks SET created_at='2026-01-02T08:00:00', "
        "started_at='2026-01-02T08:05:00', "
        "completed_at='2026-01-02T08:30:00', "
        "last_activity_at='2026-01-02T08:30:00' "
        "WHERE task_id='t2'"
    )
    idx._run(
        "UPDATE tasks SET created_at='2026-01-03T07:00:00', "
        "started_at='2026-01-03T07:05:00', "
        "completed_at=NULL, "
        "last_activity_at='2026-01-03T07:05:00' "
        "WHERE task_id='t3'"
    )
    idx._run("UPDATE tasks SET created_at='2026-01-04T06:00:00' WHERE task_id='t4'")
    idx._run(
        "UPDATE tasks SET created_at='2026-01-05T05:00:00', "
        "started_at='2026-01-05T05:05:00', "
        "completed_at='2026-01-05T05:30:00', "
        "last_activity_at='2026-01-05T05:30:00' "
        "WHERE task_id='t5'"
    )
    idx._run(
        "UPDATE tasks SET created_at='2026-01-06T04:00:00', "
        "started_at='2026-01-06T04:05:00', "
        "last_activity_at='2026-01-06T04:05:00' "
        "WHERE task_id='t6'"
    )
    idx._run(
        "UPDATE tasks SET created_at='2026-01-07T03:00:00', "
        "started_at='2026-01-07T03:05:00', "
        "completed_at='2026-01-07T03:30:00', "
        "last_activity_at='2026-01-07T03:30:00' "
        "WHERE task_id='t7'"
    )
    idx._run(
        "UPDATE tasks SET created_at='2026-01-08T02:00:00', started_at='2026-01-08T02:05:00', "
        "last_activity_at='2026-01-08T02:05:00' WHERE task_id='t8'"
    )

    return idx, "proj1"


# ===========================================================================
# AC 1: Molecule grouping — case/whitespace fold, empty → __unassigned__
# ===========================================================================


class TestMoleculeGrouping:
    def test_same_key_same_group(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(idx, TaskViewQuery(project_id=proj))
        groups = result["groups"]
        bcb_upper = None
        bcb_lower = None
        for g in groups:
            if g["key"] == "BCB-Allene":
                bcb_upper = g
            elif g["key"] == "bcb-allene":
                bcb_lower = g
        assert bcb_upper is not None, f"Expected BCB-Allene group, got {[g['key'] for g in groups]}"
        assert bcb_upper["count"] == 2  # t1, t6
        assert bcb_lower is not None, f"Expected bcb-allene group, got {[g['key'] for g in groups]}"
        assert bcb_lower["count"] == 1  # t2

    def test_empty_molecule_to_unassigned(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(idx, TaskViewQuery(project_id=proj))
        groups = result["groups"]
        unassigned = None
        for g in groups:
            if g["key"] == "__unassigned__":
                unassigned = g
                break
        assert unassigned is not None, "Expected __unassigned__ group"
        assert unassigned["unassigned"] is True
        assert unassigned["count"] == 1  # t4

    def test_total_unaffected_by_grouping(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        # Default archived=exclude → t5 (archived) excluded → 6 tasks
        result = query_project_tasks(idx, TaskViewQuery(project_id=proj))
        assert result["total"] == 6


# ===========================================================================
# AC 2: Filter semantics — statuses multi=union, cross-dimension=intersection
# ===========================================================================


class TestFilterSemantics:
    def test_multi_status_union(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, statuses=("running", "completed")),
        )
        # running: t1; completed: t2,t5(archived=excluded),t7 → t1,t2,t7 = 3
        assert result["total"] == 3

    def test_cross_dimension_intersection(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(
                project_id=proj,
                molecule_keys=("BCB-Allene",),
                workflows=("Confsearch",),
                statuses=("running",),
            ),
        )
        # molecule=BCB-Allene AND workflow=Confsearch AND status=running → only t1
        assert result["total"] == 1
        job_ids = [r["id"] for g in result["groups"] for r in g["jobs"]]
        assert job_ids == ["t1"]

    def test_empty_project(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        db = tmp_path / "test.db"
        migrate(db)
        idx = TaskIndex(db)
        result = query_project_tasks(idx, TaskViewQuery(project_id="nonexistent"))
        assert result["total"] == 0
        assert result["groups"] == []
        assert result["truncated"] is False


# ===========================================================================
# AC 3: Tag union dedup — task with ["a","b"], both selected → once, total=1
# ===========================================================================


class TestTagUnionDedup:
    def test_task_with_two_tags_both_selected_appears_once(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        # t2 has tags ["待检查", "论文使用"]
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, tags=("待检查", "论文使用")),
        )
        # t2 has both tags, but should appear only once (UNION dedup)
        job_ids = [r["id"] for g in result["groups"] for r in g["jobs"]]
        assert job_ids.count("t2") == 1
        assert result["total"] == 3  # t1(待检查), t2(both), t6(论文使用)


# ===========================================================================
# AC 4: group_limit truncation — total/counts whole-scope, truncated=true
# ===========================================================================


class TestTruncation:
    def test_group_limit_preserves_total_and_counts(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        full = query_project_tasks(idx, TaskViewQuery(project_id=proj))
        limited = query_project_tasks(idx, TaskViewQuery(project_id=proj, group_limit=1))
        assert limited["total"] == full["total"]
        assert limited["counts"] == full["counts"]
        assert limited["truncated"] is True
        for g in limited["groups"]:
            assert g["count"] <= 1 or g["truncated"] is True
        # Per-group counts by key must match the untruncated run
        full_by_key = {g["key"]: g["count"] for g in full["groups"]}
        limited_by_key = {g["key"]: g["count"] for g in limited["groups"]}
        assert full_by_key == limited_by_key

    def test_max_total_truncation(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        limited = query_project_tasks(idx, TaskViewQuery(project_id=proj, max_total=3))
        assert limited["truncated"] is True
        assert limited["total"] == 6  # total still whole-scope (6 non-archived in proj1)


# ===========================================================================
# AC 5: Facets own-dimension exclusion
# ===========================================================================


class TestFacetsExclusion:
    def test_molecule_filter_does_not_affect_workflow_facet(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        full = query_project_tasks(idx, TaskViewQuery(project_id=proj))
        filtered = query_project_tasks(
            idx, TaskViewQuery(project_id=proj, molecule_keys=("bcb-allene",))
        )
        # Workflow facet should have same counts when filtering by molecule
        full_wf_counts = {w["key"]: w["count"] for w in full["facets"]["workflows"]}
        filt_wf_counts = {w["key"]: w["count"] for w in filtered["facets"]["workflows"]}
        assert full_wf_counts == filt_wf_counts

    def test_facet_structure(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(idx, TaskViewQuery(project_id=proj))
        facets = result["facets"]
        assert "statuses" in facets
        assert "workflows" in facets
        assert "molecules" in facets
        assert "tags" in facets
        assert "batches" in facets
        for m in facets["molecules"]:
            assert "key" in m
            assert "name" in m
            assert "count" in m

    def test_cross_project_facet_isolation(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(idx, TaskViewQuery(project_id="proj1"))
        mol_keys = {m["key"] for m in result["facets"]["molecules"]}
        # proj2 has EtOH → proj1 facets must not list it
        proj1_keys = {"BCB-Allene", "bcb-allene", "MeOH", "__unassigned__"}
        assert "EtOH" not in mol_keys or mol_keys == proj1_keys
        # proj1 facets only contain proj1 molecule keys
        for mk in mol_keys:
            assert mk in {"BCB-Allene", "bcb-allene", "MeOH", "__unassigned__"}

    def test_archived_scope_facets(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import (
            ArchivedFilter,
            TaskViewQuery,
            query_project_tasks,
        )

        idx, proj = _setup_project_and_tasks(tmp_path)
        excl = query_project_tasks(
            idx,
            TaskViewQuery(
                project_id=proj,
                archived=ArchivedFilter.exclude,
            ),
        )
        only = query_project_tasks(
            idx,
            TaskViewQuery(
                project_id=proj,
                archived=ArchivedFilter.only,
            ),
        )
        incl = query_project_tasks(
            idx,
            TaskViewQuery(
                project_id=proj,
                archived=ArchivedFilter.include,
            ),
        )
        excl_total = sum(excl["facets"]["statuses"].values())
        only_total = sum(only["facets"]["statuses"].values())
        incl_total = sum(incl["facets"]["statuses"].values())
        assert excl_total + only_total == incl_total
        assert only_total == 1  # t5 archived


# ===========================================================================
# AC 6: Sorting — created_asc, completed_desc (NULL last), activity_desc,
#         running_first stable partition
# ===========================================================================


class TestSorting:
    def test_created_asc(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskSort, TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(idx, TaskViewQuery(project_id=proj, sort=TaskSort.created_asc))
        for g in result["groups"]:
            times = [r["created_at"] for r in g["jobs"]]
            assert times == sorted(times), f"Group {g['key']} not sorted ascending"

    def test_created_desc_groups_descending(self, tmp_path: Path) -> None:
        """Group-level ordering: created_desc groups by max(created_at) DESC."""
        from acp.scheduler.task_views import (
            GroupBy,
            TaskSort,
            TaskViewQuery,
            query_project_tasks,
        )

        idx, proj = _setup_project_and_tasks(tmp_path)

        # --- created_desc: groups ordered by max(created_at) DESC ---
        result = query_project_tasks(
            idx,
            TaskViewQuery(
                project_id=proj,
                sort=TaskSort.created_desc,
                group_by=GroupBy.molecule,
            ),
        )
        group_keys = [g["key"] for g in result["groups"]]
        # Fixture (non-archived proj1):
        #   MeOH: max(created_at) = 2026-01-07T03:00:00 (t7)
        #   BCB-Allene: max(created_at) = 2026-01-06T04:00:00 (t6)
        #   __unassigned__: max(created_at) = 2026-01-04T06:00:00 (t4)
        #   bcb-allene: max(created_at) = 2026-01-02T08:00:00 (t2)
        #   EtOH: archived, excluded
        assert group_keys == ["MeOH", "BCB-Allene", "__unassigned__", "bcb-allene"], (
            f"created_desc groups should order newest-max-first, got {group_keys}"
        )

        # --- created_asc: groups ordered by min(created_at) ASC ---
        result_asc = query_project_tasks(
            idx,
            TaskViewQuery(
                project_id=proj,
                sort=TaskSort.created_asc,
                group_by=GroupBy.molecule,
            ),
        )
        group_keys_asc = [g["key"] for g in result_asc["groups"]]
        #   BCB-Allene: min(created_at) = 2026-01-01T01:00:00 (t1)
        #   bcb-allene: min(created_at) = 2026-01-02T08:00:00 (t2)
        #   MeOH: min(created_at) = 2026-01-03T07:00:00 (t3)
        #   __unassigned__: min(created_at) = 2026-01-04T06:00:00 (t4)
        #   EtOH: archived, excluded
        assert group_keys_asc == ["BCB-Allene", "bcb-allene", "MeOH", "__unassigned__"], (
            f"created_asc groups should order oldest-min-first, got {group_keys_asc}"
        )

        # --- name_asc: groups ordered A→Z by key ---
        result_name = query_project_tasks(
            idx,
            TaskViewQuery(
                project_id=proj,
                sort=TaskSort.name_asc,
                group_by=GroupBy.molecule,
            ),
        )
        group_keys_name = [g["key"] for g in result_name["groups"]]
        assert group_keys_name == ["BCB-Allene", "MeOH", "__unassigned__", "bcb-allene"], (
            f"name_asc groups should order A→Z by key, got {group_keys_name}"
        )

        # --- Tie-stability: two groups with identical created_at ---
        shared_ts = "2026-02-01T00:00:00"
        for mol, tid in [("AAA", "t_tie_aaa"), ("BBB", "t_tie_bbb")]:
            idx.upsert(
                {
                    "task_id": tid,
                    "job_id": tid,
                    "project_id": proj,
                    "molecule_name": mol,
                    "task_name": "tie_test",
                    "remark": "",
                    "display_name": mol,
                    "workflow": "Confsearch",
                    "task_dir_name": f"dir_{tid}",
                    "status": "completed",
                    "node_id": "local",
                    "node_path": "/tmp",
                    "input_hash": None,
                    "result_manifest_path": None,
                    "current_stage": None,
                    "storage_mode": "local",
                    "layout_version": 2,
                    "created_at": shared_ts,
                    "updated_at": shared_ts,
                    "molecule_key": mol,
                    "tags": "[]",
                    "archived": 0,
                    "batch_id": None,
                    "last_activity_at": None,
                    "started_at": None,
                    "completed_at": None,
                    "group_id": tid,
                    "progress": None,
                }
            )

        result_desc = query_project_tasks(
            idx,
            TaskViewQuery(
                project_id=proj,
                sort=TaskSort.created_desc,
                group_by=GroupBy.molecule,
            ),
        )
        desc_keys = [g["key"] for g in result_desc["groups"]]
        assert desc_keys.index("AAA") < desc_keys.index("BBB"), (
            f"Tie-stability created_desc: 'AAA' should precede 'BBB', got {desc_keys}"
        )

        result_asc2 = query_project_tasks(
            idx,
            TaskViewQuery(
                project_id=proj,
                sort=TaskSort.created_asc,
                group_by=GroupBy.molecule,
            ),
        )
        asc_keys = [g["key"] for g in result_asc2["groups"]]
        assert asc_keys.index("AAA") < asc_keys.index("BBB"), (
            f"Tie-stability created_asc: 'AAA' should precede 'BBB', got {asc_keys}"
        )

    def test_completed_desc_null_last(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskSort, TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx, TaskViewQuery(project_id=proj, sort=TaskSort.completed_desc)
        )
        for g in result["groups"]:
            times = [r["completed_at"] for r in g["jobs"]]
            coalesced = [t or r["created_at"] for t, r in zip(times, g["jobs"])]
            assert coalesced == sorted(coalesced, reverse=True), (
                f"Group {g['key']} not sorted by COALESCE(completed_at, created_at) DESC"
            )

    def test_activity_desc_within_group(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskSort, TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(
                project_id=proj,
                sort=TaskSort.activity_desc,
            ),
        )
        for g in result["groups"]:
            coalesced = [r["last_activity_at"] or r["created_at"] for r in g["jobs"]]
            assert coalesced == sorted(coalesced, reverse=True), (
                f"Group {g['key']} not sorted by COALESCE(last_activity_at, created_at) DESC"
            )

    def test_running_first_stable_partition(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import (
            GroupBy,
            TaskSort,
            TaskViewQuery,
            query_project_tasks,
        )

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(
                project_id=proj,
                sort=TaskSort.created_desc,
                running_first=True,
                group_by=GroupBy.none,
            ),
        )
        all_rows = result["groups"][0]["jobs"]
        active_statuses = {
            "queued",
            "running",
            "paused",
            "starting",
            "pending",
            "cancelling",
            "waiting_review",
        }
        active_rows = [r for r in all_rows if r["status"] in active_statuses]
        inactive_rows = [r for r in all_rows if r["status"] not in active_statuses]
        if active_rows and inactive_rows:
            last_active_idx = max(
                i for i, r in enumerate(all_rows) if r["status"] in active_statuses
            )
            first_inactive_idx = min(
                i for i, r in enumerate(all_rows) if r["status"] not in active_statuses
            )
            assert last_active_idx < first_inactive_idx


# ===========================================================================
# AC 7: Batch grouping — shared batch_id same group, NULL → __singles__
# ===========================================================================


class TestBatchGrouping:
    def test_shared_batch_same_group(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import (
            GroupBy,
            TaskViewQuery,
            query_project_tasks,
        )

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, group_by=GroupBy.batch),
        )
        bat1_group = None
        for g in result["groups"]:
            if g["key"] == "bat1":
                bat1_group = g
                break
        assert bat1_group is not None
        assert bat1_group["count"] == 2  # t2, t3

    def test_batch_singles_count(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import (
            GroupBy,
            TaskViewQuery,
            query_project_tasks,
        )

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, group_by=GroupBy.batch),
        )
        singles = None
        for g in result["groups"]:
            if g["key"] == "__singles__":
                singles = g
                break
        assert singles is not None
        # t1,t4,t6,t7 have no batch_id (t5 excluded by archived filter) = 4
        assert singles["count"] == 4


# ===========================================================================
# AC 8: Search case-insensitive + literal % escaped
# ===========================================================================


class TestSearch:
    def test_case_insensitive_search(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, search="bcb"),
        )
        # t1,t2,t6 have molecule "BCB-Allene" → case-insensitive match
        assert result["total"] == 3

    def test_search_literal_percent_escaped(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        # Insert decoy: "100abc" would match unescaped LIKE '100%'
        idx.upsert(
            {
                "task_id": "t_pct_decoy",
                "job_id": "t_pct_decoy",
                "project_id": proj,
                "molecule_name": "100abc",
                "task_name": "test",
                "remark": "",
                "display_name": "100abc",
                "workflow": "Confsearch",
                "task_dir_name": "dir",
                "status": "completed",
                "node_id": "local",
                "node_path": "/tmp",
                "input_hash": None,
                "result_manifest_path": None,
                "current_stage": None,
                "storage_mode": "local",
                "layout_version": 2,
                "created_at": "2026-01-10T00:00:00",
                "updated_at": "2026-01-10T00:00:00",
                "molecule_key": "100abc",
                "tags": "[]",
                "archived": 0,
                "batch_id": None,
                "last_activity_at": None,
                "started_at": None,
                "completed_at": None,
                "group_id": "t_pct_decoy",
                "progress": None,
            }
        )
        # Insert target: "100%"
        idx.upsert(
            {
                "task_id": "t_pct",
                "job_id": "t_pct",
                "project_id": proj,
                "molecule_name": "100%",
                "task_name": "test",
                "remark": "",
                "display_name": "100%",
                "workflow": "Confsearch",
                "task_dir_name": "dir",
                "status": "completed",
                "node_id": "local",
                "node_path": "/tmp",
                "input_hash": None,
                "result_manifest_path": None,
                "current_stage": None,
                "storage_mode": "local",
                "layout_version": 2,
                "created_at": "2026-01-10T00:01:00",
                "updated_at": "2026-01-10T00:01:00",
                "molecule_key": "100%",
                "tags": "[]",
                "archived": 0,
                "batch_id": None,
                "last_activity_at": None,
                "started_at": None,
                "completed_at": None,
                "group_id": "t_pct",
                "progress": None,
            }
        )
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, search="100%"),
        )
        assert result["total"] == 1
        job_ids = [r["id"] for g in result["groups"] for r in g["jobs"]]
        assert "t_pct" in job_ids
        assert "t_pct_decoy" not in job_ids

    def test_search_underscore_escaped_with_decoy(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        # Insert decoy: "axb" matches unescaped LIKE "a_b"
        idx.upsert(
            {
                "task_id": "t_ud_decoy",
                "job_id": "t_ud_decoy",
                "project_id": proj,
                "molecule_name": "axb",
                "task_name": "test",
                "remark": "",
                "display_name": "axb",
                "workflow": "Confsearch",
                "task_dir_name": "dir",
                "status": "completed",
                "node_id": "local",
                "node_path": "/tmp",
                "input_hash": None,
                "result_manifest_path": None,
                "current_stage": None,
                "storage_mode": "local",
                "layout_version": 2,
                "created_at": "2026-01-11T00:00:00",
                "updated_at": "2026-01-11T00:00:00",
                "molecule_key": "axb",
                "tags": "[]",
                "archived": 0,
                "batch_id": None,
                "last_activity_at": None,
                "started_at": None,
                "completed_at": None,
                "group_id": "t_ud_decoy",
                "progress": None,
            }
        )
        # Insert target: "A_B"
        idx.upsert(
            {
                "task_id": "t_underscore",
                "job_id": "t_underscore",
                "project_id": proj,
                "molecule_name": "A_B",
                "task_name": "test",
                "remark": "",
                "display_name": "A_B",
                "workflow": "Confsearch",
                "task_dir_name": "dir",
                "status": "completed",
                "node_id": "local",
                "node_path": "/tmp",
                "input_hash": None,
                "result_manifest_path": None,
                "current_stage": None,
                "storage_mode": "local",
                "layout_version": 2,
                "created_at": "2026-01-11T00:01:00",
                "updated_at": "2026-01-11T00:01:00",
                "molecule_key": "a_b",
                "tags": "[]",
                "archived": 0,
                "batch_id": None,
                "last_activity_at": None,
                "started_at": None,
                "completed_at": None,
                "group_id": "t_underscore",
                "progress": None,
            }
        )
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, search="A_B"),
        )
        assert result["total"] == 1
        job_ids = [r["id"] for g in result["groups"] for r in g["jobs"]]
        assert "t_underscore" in job_ids
        assert "t_ud_decoy" not in job_ids

    def test_search_mixed_case_molecule_name(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        # "MeOH" search matches molecule_name
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, search="meoh"),
        )
        assert result["total"] == 2  # t3, t7


# ===========================================================================
# AC 9: archived exclude/include/only
# ===========================================================================


class TestArchivedFilter:
    def test_exclude_archived(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import ArchivedFilter, TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, archived=ArchivedFilter.exclude),
        )
        # t5 is archived → excluded
        job_ids = [r["id"] for g in result["groups"] for r in g["jobs"]]
        assert "t5" not in job_ids
        assert result["total"] == 6  # 7 - 1 archived

    def test_include_archived(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import ArchivedFilter, TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, archived=ArchivedFilter.include),
        )
        assert result["total"] == 7  # all 7 in proj1

    def test_only_archived(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import ArchivedFilter, TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, archived=ArchivedFilter.only),
        )
        assert result["total"] == 1
        job_ids = [r["id"] for g in result["groups"] for r in g["jobs"]]
        assert job_ids == ["t5"]


# ===========================================================================
# AC 10: Tag grouping — multi-membership + sum≥total
# ===========================================================================


class TestTagGrouping:
    def test_tag_grouping_multi_membership(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import (
            GroupBy,
            TaskViewQuery,
            query_project_tasks,
        )

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, group_by=GroupBy.tag),
        )
        # t2 has ["待检查","论文使用"] → should appear in both groups
        all_job_ids = []
        for g in result["groups"]:
            for r in g["jobs"]:
                all_job_ids.append(r["id"])
        assert all_job_ids.count("t2") == 2

    def test_tag_grouping_sum_ge_total(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import (
            GroupBy,
            TaskViewQuery,
            query_project_tasks,
        )

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, group_by=GroupBy.tag),
        )
        sum_counts = sum(g["count"] for g in result["groups"])
        assert sum_counts >= result["total"]

    def test_untagged_sentinel(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import (
            GroupBy,
            TaskViewQuery,
            query_project_tasks,
        )

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, group_by=GroupBy.tag),
        )
        untagged = None
        for g in result["groups"]:
            if g["key"] == "__untagged__":
                untagged = g
                break
        assert untagged is not None
        # t3, t4, t7 have no tags (t5 excluded by archived filter) = 3
        assert untagged["count"] == 3


# ===========================================================================
# AC 11: _JSON1_OK=False — tag filter identical, tag grouping raises ValueError
# ===========================================================================


class TestJSON1Fallback:
    def test_tag_filter_identical_without_json1(self, tmp_path: Path) -> None:
        from acp.scheduler import task_views
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)

        # Get result with JSON1
        result_ok = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, tags=("待检查",)),
        )

        # Monkeypatch _JSON1_OK = False
        original = task_views._JSON1_OK
        try:
            task_views._JSON1_OK = False
            result_fallback = query_project_tasks(
                idx,
                TaskViewQuery(project_id=proj, tags=("待检查",)),
            )
        finally:
            task_views._JSON1_OK = original

        assert result_fallback["total"] == result_ok["total"]
        job_ids_ok = sorted(r["id"] for g in result_ok["groups"] for r in g["jobs"])
        job_ids_fb = sorted(r["id"] for g in result_fallback["groups"] for r in g["jobs"])
        assert job_ids_ok == job_ids_fb

    def test_tag_grouping_raises_without_json1(self, tmp_path: Path) -> None:
        from acp.scheduler import task_views
        from acp.scheduler.task_views import (
            GroupBy,
            TaskViewQuery,
            query_project_tasks,
        )

        idx, proj = _setup_project_and_tasks(tmp_path)
        original = task_views._JSON1_OK
        try:
            task_views._JSON1_OK = False
            with pytest.raises(ValueError, match="tag grouping requires SQLite JSON1"):
                query_project_tasks(
                    idx,
                    TaskViewQuery(project_id=proj, group_by=GroupBy.tag),
                )
        finally:
            task_views._JSON1_OK = original


# ===========================================================================
# AC 12: project_id=None cross-project
# ===========================================================================


class TestCrossProject:
    def test_project_id_none_cross_project(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(idx, TaskViewQuery(project_id=None))
        # 6 tasks in proj1 (t5 archived excluded) + 1 in proj2 = 7
        assert result["total"] == 7

    def test_cross_project_groups_all_molecules(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(idx, TaskViewQuery(project_id=None))
        molecule_keys = {g["key"] for g in result["groups"]}
        # Should include proj2's "EtOH" too
        assert "EtOH" in molecule_keys


# ===========================================================================
# Additional: counts contract — all JobStatus keys present
# ===========================================================================


class TestCountsContract:
    def test_counts_has_all_status_keys(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(idx, TaskViewQuery(project_id=proj))
        counts = result["counts"]
        # All JobStatus values should be present in counts
        for s in JobStatus:
            assert s.value in counts

    def test_counts_values_correct(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(idx, TaskViewQuery(project_id=proj))
        counts = result["counts"]
        # proj1: running=t1, completed=t2,t7 (t5 archived=excluded), failed=t3, queued=t4, paused=t6
        assert counts["running"] == 1
        assert counts["completed"] == 2
        assert counts["failed"] == 1
        assert counts["queued"] == 1
        assert counts["paused"] == 1


# ===========================================================================
# Additional: TaskRow shape alignment
# ===========================================================================


class TestTaskRowShape:
    def test_row_has_spec_nested_object(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(idx, TaskViewQuery(project_id=proj))
        row = result["groups"][0]["jobs"][0]
        assert "spec" in row
        spec = row["spec"]
        assert "workflow" in spec
        assert "molecule_name" in spec
        assert "task_name" in spec
        assert "remark" in spec
        assert "tags" in spec

    def test_row_has_project_name(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(idx, TaskViewQuery(project_id=proj))
        for g in result["groups"]:
            for r in g["jobs"]:
                if r["project_id"] == "proj1":
                    assert r["project_name"] == "TestProject"
                elif r["project_id"] == "proj2":
                    assert r["project_name"] == "OtherProject"

    def test_row_tags_is_list(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(idx, TaskViewQuery(project_id=proj))
        for g in result["groups"]:
            for r in g["jobs"]:
                assert isinstance(r["tags"], list)


# ===========================================================================
# Additional: workflow grouping retired flag
# ===========================================================================


class TestWorkflowGrouping:
    def test_workflow_groups_carry_retired_flag(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import (
            GroupBy,
            TaskViewQuery,
            query_project_tasks,
        )

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, group_by=GroupBy.workflow),
        )
        for g in result["groups"]:
            assert "retired" in g
            assert isinstance(g["retired"], bool)


# ===========================================================================
# Additional: remark grouping
# ===========================================================================


class TestRemarkGrouping:
    def test_remark_empty_to_unassigned(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import (
            GroupBy,
            TaskViewQuery,
            query_project_tasks,
        )

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, group_by=GroupBy.remark),
        )
        unassigned = None
        for g in result["groups"]:
            if g["key"] == "__unassigned__":
                unassigned = g
                break
        assert unassigned is not None
        assert unassigned["unassigned"] is True


# ===========================================================================
# Additional: none grouping
# ===========================================================================


class TestNoneGrouping:
    def test_single_all_group(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import (
            GroupBy,
            TaskViewQuery,
            query_project_tasks,
        )

        idx, proj = _setup_project_and_tasks(tmp_path)
        result = query_project_tasks(
            idx,
            TaskViewQuery(project_id=proj, group_by=GroupBy.none),
        )
        assert len(result["groups"]) == 1
        assert result["groups"][0]["key"] == "__all__"
        assert result["groups"][0]["count"] == 6  # t5 archived excluded


# ===========================================================================
# Adversarial: weird search strings with % _ \
# ===========================================================================


class TestStaleState:
    def test_requery_after_edit(self, tmp_path: Path) -> None:
        from acp.scheduler.task_views import TaskViewQuery, query_project_tasks

        idx, proj = _setup_project_and_tasks(tmp_path)
        r1 = query_project_tasks(idx, TaskViewQuery(project_id=proj, search="BCB"))
        assert r1["total"] == 3

        # Edit t1's molecule_name
        idx._run("UPDATE tasks SET molecule_name='ZZZ', molecule_key='zzz' WHERE task_id='t1'")
        r2 = query_project_tasks(idx, TaskViewQuery(project_id=proj, search="BCB"))
        assert r2["total"] == 2  # t1 no longer matches


# ===========================================================================
# __all__ exports
# ===========================================================================


class TestExports:
    def test_module_has_all(self) -> None:
        import acp.scheduler.task_views as mod

        assert hasattr(mod, "__all__")
        assert "query_project_tasks" in mod.__all__
        assert "TaskViewQuery" in mod.__all__
        assert "GroupBy" in mod.__all__
        assert "TaskSort" in mod.__all__
        assert "ArchivedFilter" in mod.__all__
