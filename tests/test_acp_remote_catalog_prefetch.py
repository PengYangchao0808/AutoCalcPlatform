"""Terminal-state remote catalog prefetch (backend-owned pending_fetch recovery).

Regression context (2026-09-28): the structure viewer's ``pending_fetch``
resolution depended on the browser issuing ``?fetch=1``.  A workbench tab
running pre-fix JS (or simply left open across job completion) never asked,
so a completed remote job stayed ``pending_fetch`` forever.  The manager now
prefetches the small catalog files by itself on the terminal transition and
on startup, so any frontend version observes ``availability=ready``.

Run with: pytest tests/test_acp_remote_catalog_prefetch.py -v
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.remote.runner import RemotePollObservation

MANIFEST = {
    "schema_version": "confsearch_v1",
    "workflow": "Confsearch",
    "conformers": [
        {
            "conf_id": "0001",
            "geometry": "conformers/0001.xyz",
            "energy_hartree": -100.0,
            "relative_energy_kcal": 0.0,
            "boltzmann_weight": 1.0,
            "rank": 1,
        }
    ],
}
MANIFEST_PATH = "RESULT/confsearch/confsearch_manifest.json"


class FakeFetcher:
    """Records requested paths; serves the confsearch manifest when asked."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def read_file(self, record: Any, filename: str) -> bytes:
        self.calls.append((str(record.id), filename))
        if self.fail:
            raise RuntimeError("transient SFTP failure")
        if filename == MANIFEST_PATH:
            return json.dumps(MANIFEST).encode("utf-8")
        raise FileNotFoundError(filename)


class TerminalRemoteRunner:
    def poll_remote(self, record: Any, event_log: Any, cancel_event: Any) -> RemotePollObservation:
        return RemotePollObservation(terminal=True, exit_code=0)

    def apply_terminal_side_effects(
        self, record: Any, event_log: Any, stage_events: Any = ()
    ) -> None:
        pass


def _seed_remote_job(
    mgr: JobManager,
    tmp_path: Path,
    job_id: str,
    *,
    status: JobStatus = JobStatus.RUNNING,
    workflow: str = "Confsearch",
    remote: bool = True,
) -> Path:
    work_dir = tmp_path / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    result = {"node": "node1", "remote_dir": f"/remote/{job_id}"} if remote else None
    record = JobRecord(
        id=job_id,
        spec=JobSpec(workflow=workflow, name=job_id),
        status=status,
        work_dir=str(work_dir),
        remote_job_id="42" if remote else None,
        result=result,
    )
    mgr.store.create(record)
    return work_dir


def _wait_cached(mgr: JobManager, job_id: str, timeout: float = 5.0) -> Path | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cached = mgr.structure_cache.get_cached(job_id, MANIFEST_PATH)
        if cached is not None:
            return cached
        time.sleep(0.02)
    return mgr.structure_cache.get_cached(job_id, MANIFEST_PATH)


@pytest.fixture()
def manager(tmp_path: Path):
    mgr = JobManager(run_root=tmp_path, max_running=1)
    mgr._poll_stop.set()  # tests drive _poll_job synchronously; no background races
    try:
        yield mgr
    finally:
        mgr.shutdown()


def test_terminal_transition_enqueues_and_prefetches(manager: JobManager, tmp_path: Path) -> None:
    """A remote job completing via _poll_job pulls its catalog into the cache."""
    fetcher = FakeFetcher()
    manager._remote_fetcher = fetcher  # type: ignore[assignment]
    manager.remote_runner = TerminalRemoteRunner()  # type: ignore[assignment]
    _seed_remote_job(manager, tmp_path, "rj")

    manager._poll_job("rj")

    assert manager.store.get("rj").status == JobStatus.COMPLETED
    cached = _wait_cached(manager, "rj")
    assert cached is not None
    assert json.loads(cached.read_text(encoding="utf-8"))["workflow"] == "Confsearch"


def test_prefetch_skipped_for_non_terminal_job(manager: JobManager, tmp_path: Path) -> None:
    """A running remote job is never prefetched (partial manifests must not cache)."""
    fetcher = FakeFetcher()
    manager._remote_fetcher = fetcher  # type: ignore[assignment]
    _seed_remote_job(manager, tmp_path, "running-job")

    manager._prefetch_remote_catalog("running-job")

    assert fetcher.calls == []
    assert manager.structure_cache.get_cached("running-job", MANIFEST_PATH) is None


def test_prefetch_skipped_for_local_job(manager: JobManager, tmp_path: Path) -> None:
    """Local jobs keep using their own work dir; no remote cache fetch."""
    fetcher = FakeFetcher()
    manager._remote_fetcher = fetcher  # type: ignore[assignment]
    _seed_remote_job(manager, tmp_path, "local-job", status=JobStatus.COMPLETED, remote=False)

    manager._prefetch_remote_catalog("local-job")

    assert fetcher.calls == []
    assert manager.structure_cache.get_cached("local-job", MANIFEST_PATH) is None


def test_prefetch_failure_does_not_kill_worker(manager: JobManager, tmp_path: Path) -> None:
    """A failing fetch is swallowed; a later job still gets prefetched."""
    failing = FakeFetcher(fail=True)
    manager._remote_fetcher = failing  # type: ignore[assignment]
    _seed_remote_job(manager, tmp_path, "bad-job", status=JobStatus.COMPLETED)

    manager._queue_catalog_prefetch("bad-job")
    deadline = time.monotonic() + 5.0
    while not failing.calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert failing.calls

    manager._remote_fetcher = FakeFetcher()  # type: ignore[assignment]
    _seed_remote_job(manager, tmp_path, "good-job", status=JobStatus.COMPLETED)
    manager._queue_catalog_prefetch("good-job")

    assert _wait_cached(manager, "good-job") is not None


def test_startup_sweep_prefetches_terminal_remote_jobs_only(
    manager: JobManager, tmp_path: Path
) -> None:
    """Startup sweep repairs completed remote jobs; local jobs are untouched."""
    fetcher = FakeFetcher()
    manager._remote_fetcher = fetcher  # type: ignore[assignment]
    _seed_remote_job(manager, tmp_path, "done-remote", status=JobStatus.COMPLETED)
    _seed_remote_job(manager, tmp_path, "failed-remote", status=JobStatus.FAILED)
    _seed_remote_job(manager, tmp_path, "done-local", status=JobStatus.COMPLETED, remote=False)
    _seed_remote_job(manager, tmp_path, "running-remote", status=JobStatus.RUNNING)

    manager._queue_startup_catalog_prefetch()

    assert _wait_cached(manager, "done-remote") is not None
    assert _wait_cached(manager, "failed-remote") is not None
    requested = {job_id for job_id, _ in fetcher.calls}
    assert "done-local" not in requested
    assert "running-remote" not in requested


def test_queue_is_noop_without_remote_fetcher(manager: JobManager) -> None:
    """No remote fetcher → no worker thread, no queue growth."""
    manager._queue_catalog_prefetch("anything")
    manager._queue_startup_catalog_prefetch()

    assert manager._catalog_prefetch_queue.empty()
    assert manager._catalog_prefetch_thread is None


def test_frontend_assets_revalidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Workbench JS is served with Cache-Control: no-cache so deploys land."""
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as client:
        response = client.get("/js/structure_viewer.js")

    assert response.status_code == 200
    assert "no-cache" in response.headers.get("cache-control", "")
