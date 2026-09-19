"""Tests for acp.scheduler.structure_source_indexer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.store import JobStore
from acp.scheduler.structure_source_indexer import StructureSourceIndexer
from acp.scheduler.structure_source_store import StructureSourceStore

_XYZ_TS = """\
3
TAG: TS | candidate_id=ts_guess_001
C 0.000000 0.000000 0.000000
O 1.200000 0.000000 0.000000
H 0.000000 1.200000 0.000000
"""

_XYZ_INT = """\
3
TAG: INT | candidate_id=int_guess_001
C 0.000000 0.000000 0.000000
O 1.200000 0.000000 0.000000
H 0.000000 1.200000 0.000000
"""

_XYZ_PLAIN = """\
3
water
O 0.000000 0.000000 0.000000
H 0.950000 0.000000 0.000000
H -0.950000 0.000000 0.000000
"""


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_record(
    job_id: str,
    *,
    workflow: str = "Confsearch",
    status: JobStatus = JobStatus.COMPLETED,
    work_dir: Path | None = None,
    project_id: str | None = "uncategorized",
    completed_at: str = "2026-09-01T10:00:00+00:00",
    updated_at: str = "2026-09-01T10:00:00+00:00",
    remote_job_id: str | None = None,
    result: dict[str, Any] | None = None,
) -> JobRecord:
    return JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow=workflow,
            name=f"Task {job_id}",
            project_id=project_id,
        ),
        status=status,
        work_dir=str(work_dir) if work_dir else f"/tmp/{job_id}",
        created_at="2026-09-01T09:00:00+00:00",
        updated_at=updated_at,
        completed_at=completed_at,
        project_id=project_id,
        remote_job_id=remote_job_id,
        result=result,
    )


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test.db")


@pytest.fixture()
def job_store(db_path: str) -> JobStore:
    return JobStore(db_path)


@pytest.fixture()
def source_store(db_path: str) -> StructureSourceStore:
    return StructureSourceStore(db_path)


@pytest.fixture()
def indexer(
    job_store: JobStore, source_store: StructureSourceStore, tmp_path: Path
) -> StructureSourceIndexer:
    return StructureSourceIndexer(
        store=job_store,
        source_store=source_store,
        run_root=tmp_path,
    )


# ------------------------------------------------------------------ #
# Seed helper
# ------------------------------------------------------------------ #


def _seed_completed_job(
    job_store: JobStore,
    tmp_path: Path,
    job_id: str,
    *,
    xyz_text: str = _XYZ_PLAIN,
    project_id: str | None = "uncategorized",
    workflow: str = "Confsearch",
) -> Path:
    work_dir = tmp_path / job_id
    result_dir = work_dir / "RESULT"
    result_dir.mkdir(parents=True, exist_ok=True)
    _write(result_dir / "optimized.xyz", xyz_text)
    _write(
        result_dir / "result_manifest.json",
        json.dumps(
            {"products": [{"path": "optimized.xyz", "kind": "structure", "id": "global_min"}]}
        ),
    )
    record = _make_record(
        job_id,
        work_dir=work_dir,
        project_id=project_id,
        workflow=workflow,
    )
    job_store.create(record)
    return work_dir


# ------------------------------------------------------------------ #
# Tests
# ------------------------------------------------------------------ #


class TestRoleProjection:
    """Role preserved as TS/INT/'', legacy tag stays 'TS'|''."""

    def test_ts_role_preserved(
        self, job_store: JobStore, source_store: StructureSourceStore, tmp_path: Path
    ) -> None:
        _seed_completed_job(job_store, tmp_path, "job_ts", xyz_text=_XYZ_TS)
        indexer = StructureSourceIndexer(
            store=job_store, source_store=source_store, run_root=tmp_path
        )
        indexer.refresh_job("job_ts")
        rows = source_store.list_by_job("job_ts")
        assert len(rows) >= 1
        row = rows[0]
        assert row["role"] == "TS"
        assert row["role_evidence"] == "xyz_tag"

    def test_int_role_preserved(
        self, job_store: JobStore, source_store: StructureSourceStore, tmp_path: Path
    ) -> None:
        _seed_completed_job(job_store, tmp_path, "job_int", xyz_text=_XYZ_INT)
        indexer = StructureSourceIndexer(
            store=job_store, source_store=source_store, run_root=tmp_path
        )
        indexer.refresh_job("job_int")
        rows = source_store.list_by_job("job_int")
        assert len(rows) >= 1
        row = rows[0]
        assert row["role"] == "INT"
        assert row["role_evidence"] == "xyz_tag"

    def test_unlabeled_role(
        self, job_store: JobStore, source_store: StructureSourceStore, tmp_path: Path
    ) -> None:
        _seed_completed_job(job_store, tmp_path, "job_plain", xyz_text=_XYZ_PLAIN)
        indexer = StructureSourceIndexer(
            store=job_store, source_store=source_store, run_root=tmp_path
        )
        indexer.refresh_job("job_plain")
        rows = source_store.list_by_job("job_plain")
        assert len(rows) >= 1
        row = rows[0]
        assert row["role"] == ""
        assert row["role_evidence"] == ""


class TestIndexerBackfill:
    """Full backfill populates rows from seeded jobs."""

    def test_backfill_populates_rows(
        self, job_store: JobStore, source_store: StructureSourceStore, tmp_path: Path
    ) -> None:
        _seed_completed_job(job_store, tmp_path, "job_001")
        _seed_completed_job(job_store, tmp_path, "job_002")
        indexer = StructureSourceIndexer(
            store=job_store, source_store=source_store, run_root=tmp_path, page_size=1
        )
        indexer._full_backfill()
        cov = indexer.coverage()
        assert cov["indexed_jobs"] >= 2
        rows1 = source_store.list_by_job("job_001")
        assert len(rows1) >= 1
        rows2 = source_store.list_by_job("job_002")
        assert len(rows2) >= 1

    def test_empty_job_gets_indexed(
        self, job_store: JobStore, source_store: StructureSourceStore, tmp_path: Path
    ) -> None:
        work_dir = tmp_path / "empty_job"
        work_dir.mkdir(parents=True)
        record = _make_record("job_empty", work_dir=work_dir)
        job_store.create(record)
        indexer = StructureSourceIndexer(
            store=job_store, source_store=source_store, run_root=tmp_path
        )
        indexer._index_job(record)
        cov = indexer.coverage()
        assert cov["indexed_jobs"] >= 1
        rows = source_store.list_by_job("job_empty")
        assert len(rows) == 0


class TestIncrementalSweep:
    """Incremental sweep picks up newly completed jobs."""

    def test_sweep_picks_up_new_job(
        self, job_store: JobStore, source_store: StructureSourceStore, tmp_path: Path
    ) -> None:
        indexer = StructureSourceIndexer(
            store=job_store, source_store=source_store, run_root=tmp_path
        )
        indexer._full_backfill()
        assert indexer.coverage()["indexed_jobs"] == 0
        _seed_completed_job(job_store, tmp_path, "job_new")
        indexer._incremental_sweep()
        rows = source_store.list_by_job("job_new")
        assert len(rows) >= 1

    def test_refresh_job_direct(
        self, job_store: JobStore, source_store: StructureSourceStore, tmp_path: Path
    ) -> None:
        _seed_completed_job(job_store, tmp_path, "job_r")
        indexer = StructureSourceIndexer(
            store=job_store, source_store=source_store, run_root=tmp_path
        )
        count = indexer.refresh_job("job_r")
        assert count >= 1
        rows = source_store.list_by_job("job_r")
        assert len(rows) >= 1


class TestRemotePlaceholder:
    """Remote jobs get pending_sync placeholder rows."""

    def test_remote_placeholder(
        self, job_store: JobStore, source_store: StructureSourceStore, tmp_path: Path
    ) -> None:
        record = _make_record(
            "job_remote",
            work_dir=tmp_path / "remote",
            remote_job_id="lsf_123",
        )
        job_store.create(record)
        indexer = StructureSourceIndexer(
            store=job_store, source_store=source_store, run_root=tmp_path
        )
        indexer._index_job(record)
        rows = source_store.list_by_job("job_remote")
        assert len(rows) == 1
        row = rows[0]
        assert row["availability"] == "pending_sync"
        assert row["remote"] == 1


class TestEnsureStartedIdempotent:
    """ensure_started is idempotent."""

    def test_call_twice(self, indexer: StructureSourceIndexer) -> None:
        indexer.ensure_started()
        t1 = indexer._thread
        indexer.ensure_started()
        t2 = indexer._thread
        assert t1 is t2
        indexer.stop()
        if t1:
            t1.join(timeout=5)


class TestCoverage:
    """coverage() returns expected fields."""

    def test_coverage_fields(
        self, job_store: JobStore, source_store: StructureSourceStore, tmp_path: Path
    ) -> None:
        indexer = StructureSourceIndexer(
            store=job_store, source_store=source_store, run_root=tmp_path
        )
        cov = indexer.coverage()
        assert "indexed_jobs" in cov
        assert "last_indexed_at" in cov
        assert "indexing_state" in cov
        assert "pending_jobs" in cov
        assert "started_at" in cov


class TestPurgeNotify:
    """purge_notify cascades deletes."""

    def test_purge_removes_rows(
        self, job_store: JobStore, source_store: StructureSourceStore, tmp_path: Path
    ) -> None:
        _seed_completed_job(job_store, tmp_path, "job_purge")
        indexer = StructureSourceIndexer(
            store=job_store, source_store=source_store, run_root=tmp_path
        )
        indexer.refresh_job("job_purge")
        assert len(source_store.list_by_job("job_purge")) >= 1
        deleted = indexer.purge_notify("job_purge")
        assert deleted >= 1
        assert len(source_store.list_by_job("job_purge")) == 0
