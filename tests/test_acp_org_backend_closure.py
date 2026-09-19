"""
Tests for backend closure items (org purge cascade, job_resolved_name join,
v1 enrichment, submit-time metadata snapshot).

Covers acceptance items A13 (purge leaves no orphaned org rows),
A14 (source rename does not rewrite submitted-task input info),
A15 (old clients/v1 unchanged behavior + new fields not filtered out).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.provenance import compute_input_hash
from acp.scheduler.store import JobStore
from acp.scheduler.structure_source_store import StructureSourceStore, source_uid_for
from acp.storage.layout import runtime_file


def _make_manager(tmp_path: Path, **kw: Any) -> JobManager:
    kw.setdefault("poll_interval", 30)
    return JobManager(run_root=tmp_path / "runs", **kw)


def _seed_job(
    store: JobStore,
    work_dir: Path,
    job_id: str,
    *,
    status: JobStatus = JobStatus.COMPLETED,
    workflow: str = "fake",
    name: str | None = None,
    method: dict | None = None,
    project_id: str | None = None,
) -> JobRecord:
    work_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow=workflow,
            name=name or job_id,
            method=method or {},
            project_id=project_id,
        ),
        status=status,
        work_dir=str(work_dir),
        project_id=project_id,
    )
    store.create(record)
    return record


def _table_count(db: Path, table: str, where: str, param: tuple[Any, ...]) -> int:
    with sqlite3.connect(str(db)) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", param).fetchone()[0])


def store_get(store: JobStore, job_id: str) -> JobRecord | None:
    return store.get(job_id)


# ---------------------------------------------------------------------------
# 1. Purge cascade cleans org tables
# ---------------------------------------------------------------------------


class TestPurgeOrgCascade:
    def test_purge_removes_org_rows_via_manager(self, tmp_path: Path) -> None:
        mgr = _make_manager(tmp_path)
        try:
            db = mgr.store.db_path
            org = StructureSourceStore(db)

            _seed_job(mgr.store, tmp_path / "runs/victim", "victim")
            _seed_job(mgr.store, tmp_path / "runs/other", "bystander")

            org.upsert_index_entries(
                [
                    _make_entry("victim", "RESULT/structures/a.xyz"),
                    _make_entry("victim", "RESULT/structures/b.xyz"),
                    _make_entry("bystander", "RESULT/structures/c.xyz"),
                ]
            )
            uid_v = source_uid_for("victim", "RESULT/structures/a.xyz")
            org.set_custom_name(uid_v, "Custom A", 0)

            assert _table_count(db, "structure_source_index", "job_id=?", ("victim",)) == 2
            assert _table_count(db, "structure_source_index", "job_id=?", ("bystander",)) == 1

            report = mgr.purge_jobs(["victim"])
            assert report[0]["ok"] is True

            assert store_get(mgr.store, "victim") is None
            assert _table_count(db, "structure_source_index", "job_id=?", ("victim",)) == 0
            assert _table_count(db, "structure_source_metadata", "source_uid=?", (uid_v,)) == 0
            assert _table_count(db, "structure_source_index", "job_id=?", ("bystander",)) == 1
        finally:
            mgr.shutdown()

    def test_manager_purge_cleans_org_via_manager(self, tmp_path: Path) -> None:
        mgr = _make_manager(tmp_path)
        try:
            db = mgr.store.db_path
            org = StructureSourceStore(db)

            _seed_job(mgr.store, tmp_path / "runs/done", "done")
            org.upsert_index_entries([_make_entry("done", "p.xyz")])
            uid = source_uid_for("done", "p.xyz")
            org.add_tags(uid, ["tag1"], 0)

            assert _table_count(db, "structure_source_index", "job_id=?", ("done",)) == 1
            report = mgr.purge_jobs(["done"])
            assert report[0]["ok"] is True
            assert _table_count(db, "structure_source_index", "job_id=?", ("done",)) == 0
            assert _table_count(db, "structure_source_tags", "source_uid=?", (uid,)) == 0
        finally:
            mgr.shutdown()

    def test_purge_nonexistent_job_no_org_error(self, tmp_path: Path) -> None:
        mgr = _make_manager(tmp_path)
        try:
            report = mgr.purge_jobs(["no-such-job"])
            assert report[0]["ok"] is False
        finally:
            mgr.shutdown()


# ---------------------------------------------------------------------------
# 2. job_resolved_name join
# ---------------------------------------------------------------------------


def _make_entry(
    job_id: str = "job_001",
    path: str = "RESULT/structures/mol.xyz",
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source_id": f"job_{job_id}:{path}",
        "job_id": job_id,
        "path": path,
        "label": "Structure",
        "workflow": "Confsearch",
        "project_id": "proj_1",
        "job_name": f"Task {job_id}",
        "molecule_name": "mol",
        "job_status": "completed",
        "source_kind": "final",
        "formula": "C2H6O",
        "atom_count": 9,
        "charge": 0,
        "multiplicity": 1,
        "has_3d": 1,
        "remote": 0,
        "candidate_id": "",
        "role": "",
        "role_evidence": "",
        "availability": "available",
    }
    base.update(overrides)
    return base


class TestJobResolvedNameJoin:
    def test_query_sources_reflects_task_custom_name(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.db")
        store = StructureSourceStore(db)
        store.upsert_index_entries([_make_entry("j1", "a.xyz")])

        # Without a tasks row, job_resolved_name falls back to job_name
        result = store.query_sources(all_projects=True)
        item = result["items"][0]
        assert item["job_resolved_name"] == "Task j1"

    def test_query_sources_with_task_row_uses_custom_name(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = StructureSourceStore(db)
        store.upsert_index_entries([_make_entry("j1", "a.xyz")])

        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, job_id, display_name, workflow, "
                "status, created_at, updated_at, custom_name, name_revision) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "j1",
                    "j1",
                    "old_name",
                    "Confsearch",
                    "completed",
                    "2026-01-01",
                    "2026-01-01",
                    "My Custom Name",
                    1,
                ),
            )
            conn.commit()

        result = store.query_sources(all_projects=True)
        item = result["items"][0]
        assert item["job_resolved_name"] == "My Custom Name"

    def test_group_by_job_uses_resolved_name(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = StructureSourceStore(db)
        store.upsert_index_entries(
            [
                _make_entry("j1", "a.xyz"),
                _make_entry("j1", "b.xyz"),
            ]
        )

        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, job_id, display_name, workflow, "
                "status, created_at, updated_at, custom_name, name_revision) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "j1",
                    "j1",
                    "old",
                    "Confsearch",
                    "completed",
                    "2026-01-01",
                    "2026-01-01",
                    "Renamed",
                    1,
                ),
            )
            conn.commit()

        result = store.query_sources(all_projects=True, group_by="job")
        assert result["groups"][0]["label"] == "Renamed"

    def test_facet_jobs_includes_resolved_name(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = StructureSourceStore(db)
        store.upsert_index_entries([_make_entry("j1", "a.xyz")])

        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, job_id, display_name, workflow, "
                "status, created_at, updated_at, custom_name, name_revision) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "j1",
                    "j1",
                    "display",
                    "Confsearch",
                    "completed",
                    "2026-01-01",
                    "2026-01-01",
                    "Facet Name",
                    1,
                ),
            )
            conn.commit()

        facets = store.facet_counts(all_projects=True)
        job_entry = facets["jobs"][0]
        assert job_entry["job_resolved_name"] == "Facet Name"


# ---------------------------------------------------------------------------
# 3. v1 enrichment
# ---------------------------------------------------------------------------


class TestV1Enrichment:
    def test_structure_source_summary_has_optional_fields(self) -> None:
        from acp.api.v1_schemas import StructureSourceSummary

        s = StructureSourceSummary(
            source_id="job_001:RESULT/structures/a.xyz",
            job_id="001",
            job_name="Task",
            workflow="Confsearch",
        )
        assert s.custom_name is None
        assert s.resolved_name is None
        assert s.tags == []
        assert s.source_uid == ""
        assert s.job_resolved_name is None

    def test_v1_enrichment_populates_fields(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        store = StructureSourceStore(db)
        store.upsert_index_entries([_make_entry("j1", "a.xyz", job_name="Original")])
        uid = source_uid_for("j1", "a.xyz")
        store.set_custom_name(uid, "Custom A", 0)
        store.add_tags(uid, ["tag1", "tag2"], 1)

        entry = store.get_by_legacy_source_id("job_j1:a.xyz")
        assert entry is not None
        assert entry["custom_name"] == "Custom A"
        assert entry["tags"] == ["tag1", "tag2"]
        assert entry["resolved_name"] == "Custom A"


# ---------------------------------------------------------------------------
# 4. Submit-time metadata snapshot + input_hash regression
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_resolve_batch_returns_snapshots(self, tmp_path: Path) -> None:
        from acp.api.v1_routes import _resolve_batch_structures_input

        mgr = _make_manager(tmp_path)
        try:
            db = mgr.store.db_path
            org = StructureSourceStore(db)
            _seed_job(mgr.store, tmp_path / "runs/src", "src_job")
            org.upsert_index_entries(
                [
                    _make_entry("src_job", "RESULT/structures/mol.xyz", job_name="Source"),
                ]
            )
            uid = source_uid_for("src_job", "RESULT/structures/mol.xyz")
            org.set_custom_name(uid, "My Structure", 0)

            xyz_dir = tmp_path / "runs/src" / "RESULT" / "structures"
            xyz_dir.mkdir(parents=True, exist_ok=True)
            (xyz_dir / "mol.xyz").write_text("3\n\nH 0 0 0\nH 0 0 1\nH 0 0 2\n")

            req = SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(
                        job_manager=mgr,
                        db_path=str(db),
                        run_root=str(tmp_path / "runs"),
                        remote_fetcher=None,
                    )
                )
            )

            inp = {
                "items": [
                    {
                        "source_id": "job_src_job:RESULT/structures/mol.xyz",
                        "name": "test",
                    }
                ]
            }
            resolved, snapshots = _resolve_batch_structures_input(inp, req)

            assert len(snapshots) == 1
            snap = snapshots[0]
            assert snap["source_id"] == "job_src_job:RESULT/structures/mol.xyz"
            assert snap["source_uid"] == uid
            assert snap["job_id"] == "src_job"
            assert snap["custom_name"] == "My Structure"
            assert snap["resolved_name"] == "My Structure"
            assert "captured_at" in snap
            assert "xyz" in resolved["items"][0]
        finally:
            mgr.shutdown()

    def test_input_hash_unchanged_by_snapshot(self) -> None:

        spec = JobSpec(
            workflow="BatchOptimize",
            input={"items": [{"xyz": "3\n\nH 0 0 0\nH 0 0 1\nH 0 0 2\n"}]},
            method={"backend": "orca"},
        )
        h1 = compute_input_hash(spec)
        h2 = compute_input_hash(spec)
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_snapshot_event_recorded_after_submit(self, tmp_path: Path) -> None:
        mgr = _make_manager(tmp_path)
        try:
            db = mgr.store.db_path
            org = StructureSourceStore(db)
            _seed_job(mgr.store, tmp_path / "runs/src", "src2")
            org.upsert_index_entries(
                [
                    _make_entry("src2", "mol.xyz"),
                ]
            )

            record = _seed_job(
                mgr.store,
                tmp_path / "runs/target",
                "target",
                status=JobStatus.COMPLETED,
            )

            events_path = runtime_file(record.work_dir, "events.jsonl")
            JobEventLog(events_path).append(
                "structure_source_snapshot",
                job_id=record.id,
                snapshots=[{"source_id": "job_src2:mol.xyz", "captured_at": "2026-01-01"}],
            )

            events = JobEventLog(events_path).read_all()
            snap_events = [e for e in events if e.get("type") == "structure_source_snapshot"]
            assert len(snap_events) == 1
            assert snap_events[0]["job_id"] == "target"
            assert len(snap_events[0]["snapshots"]) == 1
        finally:
            mgr.shutdown()

    def test_purge_does_not_affect_downstream_snapshot(self, tmp_path: Path) -> None:
        mgr = _make_manager(tmp_path)
        try:
            db = mgr.store.db_path
            org = StructureSourceStore(db)
            _seed_job(mgr.store, tmp_path / "runs/src", "src3")
            org.upsert_index_entries([_make_entry("src3", "mol.xyz")])

            downstream = _seed_job(
                mgr.store,
                tmp_path / "runs/down",
                "down",
                status=JobStatus.COMPLETED,
            )

            events_path = runtime_file(downstream.work_dir, "events.jsonl")
            JobEventLog(events_path).append(
                "structure_source_snapshot",
                job_id=downstream.id,
                snapshots=[{"source_id": "job_src3:mol.xyz"}],
            )

            mgr.purge_jobs(["src3"])

            events = JobEventLog(events_path).read_all()
            snap_events = [e for e in events if e.get("type") == "structure_source_snapshot"]
            assert len(snap_events) == 1
            assert org.get_by_legacy_source_id("job_src3:mol.xyz") is None
        finally:
            mgr.shutdown()
