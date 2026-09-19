"""
Structure Source Indexer
========================

Background indexer that discovers reusable structure sources from terminal
scheduler jobs and populates :class:`StructureSourceStore` for server-side
query, filtering, and metadata management.

Two modes:

- **Full backfill**: pages through *all* terminal jobs and runs discovery
  for each, building the index from scratch.
- **Incremental sweep**: periodically re-discovers jobs whose
  ``updated_at`` is newer than the last index snapshot, catching new
  completions, candidate saves, and result refreshes without touching
  ``manager.py``.

Design contract (ACP_Task_Structure_Organization_Plan §5.3):

- Backfill runs in a daemon thread; does not block the API server.
- Remote jobs get a ``pending_sync`` placeholder row (no live SSH probes).
- Failed discovery rows retain ``discovery_version=0`` for retry.
- ``purge_notify`` cascades deletes through ``StructureSourceStore``.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from acp.scheduler.structure_source_store import StructureSourceStore

if TYPE_CHECKING:
    from acp.scheduler.remote.fetcher import RemoteResultFetcher
    from acp.scheduler.store import JobStore

logger = logging.getLogger(__name__)

__all__ = ["StructureSourceIndexer"]

_REMOTE_PLACEHOLDER_REL = "__remote_pending__"
_SWEEP_PAGE_SIZE = 25


class StructureSourceIndexer:
    """Background indexer populating StructureSourceStore from terminal jobs.

    Args:
        store: Scheduler :class:`JobStore` for job queries.
        source_store: :class:`StructureSourceStore` for index writes.
        run_root: Scheduler run root (job work directories live under it).
        fetcher: Optional remote fetcher (not used during backfill).
        page_size: Number of jobs per discovery page.
        sweep_interval: Seconds between incremental sweep passes.
    """

    def __init__(
        self,
        store: JobStore,
        source_store: StructureSourceStore,
        run_root: Path,
        fetcher: RemoteResultFetcher | None = None,
        *,
        page_size: int = _SWEEP_PAGE_SIZE,
        sweep_interval: float = 60.0,
    ) -> None:
        self._store = store
        self._source_store = source_store
        self._run_root = Path(run_root)
        self._fetcher = fetcher
        self._page_size = page_size
        self._sweep_interval = sweep_interval

        self._start_lock = threading.Lock()
        self._started = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._indexing_state: str = "idle"
        self._started_at: str | None = None
        self._retry_queue: dict[str, int] = {}
        self._max_retries = 3

    def ensure_started(self) -> None:
        """Start the background indexer thread (idempotent)."""
        with self._start_lock:
            if self._started:
                return
            self._started = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True, name="structure-source-indexer"
            )
            self._thread.start()
            logger.info("Structure source indexer started")

    def stop(self) -> None:
        """Signal the background thread to stop."""
        self._stop_event.set()

    def _run_loop(self) -> None:
        """Main daemon loop: full backfill once, then incremental sweeps."""
        from datetime import datetime, timezone

        self._indexing_state = "running"
        self._started_at = datetime.now(timezone.utc).isoformat()
        try:
            self._full_backfill()
        except Exception:
            logger.exception("Full backfill failed unexpectedly")
        finally:
            self._indexing_state = "idle"

        while not self._stop_event.is_set():
            self._stop_event.wait(self._sweep_interval)
            if self._stop_event.is_set():
                break
            self._indexing_state = "running"
            try:
                self._incremental_sweep()
            except Exception:
                logger.exception("Incremental sweep failed")
            finally:
                self._indexing_state = "idle"

    # ------------------------------------------------------------------ #
    # Full backfill
    # ------------------------------------------------------------------ #

    def _full_backfill(self) -> None:
        """Page through all terminal jobs and run discovery."""

        offset = 0
        while not self._stop_event.is_set():
            records = self._store.list_terminal_jobs_paged(
                offset=offset,
                limit=self._page_size,
            )
            if not records:
                break
            for record in records:
                if self._stop_event.is_set():
                    break
                self._index_job(record)
            offset += len(records)

    # ------------------------------------------------------------------ #
    # Incremental sweep
    # ------------------------------------------------------------------ #

    def _incremental_sweep(self) -> None:
        """Re-discover jobs with updated_at newer than their last index."""

        offset = 0
        while not self._stop_event.is_set():
            records = self._store.list_terminal_jobs_paged(
                offset=offset,
                limit=self._page_size,
            )
            if not records:
                break
            for record in records:
                if self._stop_event.is_set():
                    break
                self._refresh_job_if_stale(record)
            offset += len(records)

    def _refresh_job_if_stale(self, record: Any) -> None:
        """Re-discover a job only if its updated_at is newer than indexed_at."""
        state = self._source_store.list_by_job(record.id)
        if not state:
            self._index_job(record)
            return
        indexed_at = self._get_indexed_at(record.id)
        if indexed_at and record.updated_at and record.updated_at > indexed_at:
            self._index_job(record)

    def _get_indexed_at(self, job_id: str) -> str | None:
        """Read indexed_at from structure_source_index_state for a job."""
        with self._source_store._lock, self._source_store._connect() as conn:
            row = conn.execute(
                "SELECT indexed_at FROM structure_source_index_state WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return row["indexed_at"] if row else None

    # ------------------------------------------------------------------ #
    # Per-job indexing
    # ------------------------------------------------------------------ #

    def _index_job(self, record: Any) -> None:
        """Run discovery for a single job and upsert results."""

        job_id = record.id
        is_remote = self._is_remote(record)

        if is_remote:
            self._index_remote_placeholder(record)
            self._source_store.mark_job_indexed(job_id, discovery_version=1)
            return

        try:
            from acp.scheduler.structure_sources import StructureSourceService

            service = StructureSourceService(self._store, self._run_root, self._fetcher)
            entries = service.list_recent(
                limit=50,
                project_id=record.project_id or record.spec.project_id,
                include_remote=False,
            )
            filtered = [e for e in entries if e.get("job_id") == job_id]
            if not filtered:
                self._source_store.mark_job_indexed(job_id, discovery_version=1)
                return
            index_rows = self._entries_to_index_rows(filtered, record)
            if index_rows:
                self._source_store.upsert_index_entries(index_rows, discovery_version=1)
            self._source_store.mark_job_indexed(job_id, discovery_version=1)
            self._retry_queue.pop(job_id, None)
        except Exception:
            version = self._retry_queue.get(job_id, 0) + 1
            self._retry_queue[job_id] = version
            if version >= self._max_retries:
                self._source_store.mark_job_indexed(job_id, discovery_version=0)
                self._retry_queue.pop(job_id, None)
                logger.warning(
                    "Job %s: discovery failed %d times, marking indexed (v0)", job_id, version
                )
            else:
                logger.warning("Job %s: discovery failed (attempt %d), will retry", job_id, version)

    def _index_remote_placeholder(self, record: Any) -> None:
        """Insert a single pending_sync placeholder row for a remote job."""
        job_id = record.id
        project_id = record.project_id or record.spec.project_id
        entry = {
            "job_id": job_id,
            "relative_path": _REMOTE_PLACEHOLDER_REL,
            "source_id": f"job_{job_id}:{_REMOTE_PLACEHOLDER_REL}",
            "project_id": project_id,
            "job_name": record.spec.name,
            "molecule_name": getattr(record.spec, "molecule_name", "") or "",
            "workflow": record.spec.workflow,
            "job_status": record.status.value,
            "source_kind": "final",
            "label": record.spec.name or job_id,
            "candidate_id": "",
            "role": "",
            "role_evidence": "",
            "formula": "",
            "atom_count": 0,
            "charge": 0,
            "multiplicity": 1,
            "has_3d": False,
            "remote": True,
            "availability": "pending_sync",
            "produced_at": record.completed_at or "",
        }
        self._source_store.upsert_index_entries([entry], discovery_version=1)

    @staticmethod
    def _is_remote(record: Any) -> bool:
        """True when the job ran on a remote node."""
        result = record.result or {}
        return bool(
            record.remote_job_id
            or result.get("lsf_job_id")
            or result.get("execution_kind") == "remote"
        )

    @staticmethod
    def _entries_to_index_rows(entries: list[dict[str, Any]], record: Any) -> list[dict[str, Any]]:
        """Convert StructureSourceService entries to index row dicts."""
        rows: list[dict[str, Any]] = []
        for entry in entries:
            source_id = entry.get("source_id", "")
            if not source_id:
                continue
            try:
                job_id_parsed, rel = _parse_source_id(source_id)
            except ValueError:
                continue
            needs_fetch = entry.get("needs_fetch", False)
            availability = "pending_fetch" if needs_fetch else "available"
            rows.append(
                {
                    "job_id": job_id_parsed,
                    "relative_path": rel,
                    "source_id": source_id,
                    "project_id": entry.get("project_id"),
                    "job_name": entry.get("job_name", ""),
                    "molecule_name": entry.get("molecule_name", ""),
                    "workflow": entry.get("workflow", ""),
                    "job_status": entry.get("job_status", ""),
                    "source_kind": entry.get("source_kind", ""),
                    "label": entry.get("label", ""),
                    "candidate_id": entry.get("candidate_id", ""),
                    "role": entry.get("role", ""),
                    "role_evidence": entry.get("role_evidence", ""),
                    "formula": entry.get("formula", ""),
                    "atom_count": entry.get("atom_count"),
                    "charge": entry.get("charge"),
                    "multiplicity": entry.get("multiplicity"),
                    "has_3d": int(bool(entry.get("has_3d"))),
                    "remote": int(bool(entry.get("remote"))),
                    "availability": availability,
                    "produced_at": entry.get("completed_at", ""),
                }
            )
        return rows

    # ------------------------------------------------------------------ #
    # On-demand refresh
    # ------------------------------------------------------------------ #

    def refresh_job(self, job_id: str) -> int:
        """On-demand re-index a single job.

        Returns the number of index rows upserted.
        """
        record = self._store.get(job_id)
        if record is None:
            return 0
        if not record.status.is_terminal:
            return 0
        self._source_store.delete_by_job(job_id)
        self._index_job(record)
        new_rows = self._source_store.list_by_job(job_id)
        return len(new_rows)

    # ------------------------------------------------------------------ #
    # Coverage
    # ------------------------------------------------------------------ #

    def coverage(self) -> dict[str, Any]:
        """Merge store.index_coverage() with live indexer state."""
        store_cov = self._source_store.index_coverage()
        terminal_count = self._store.count_terminal_jobs()
        indexed_count = store_cov.get("indexed_jobs", 0)
        pending = max(0, terminal_count - indexed_count)
        return {
            "indexed_jobs": indexed_count,
            "last_indexed_at": store_cov.get("last_indexed_at"),
            "indexing_state": self._indexing_state,
            "pending_jobs": pending,
            "started_at": self._started_at,
        }

    # ------------------------------------------------------------------ #
    # Purge
    # ------------------------------------------------------------------ #

    def purge_notify(self, job_id: str) -> int:
        """Remove index entries for a purged job.

        Called by Wave 4 manager purge cascade.
        """
        return self._source_store.delete_by_job(job_id)


def _parse_source_id(source_id: str) -> tuple[str, str]:
    """Split ``job_<id>:<rel>`` into ``(job_id, rel)``."""
    prefix, sep, rel = source_id.partition(":")
    if not sep or not prefix.startswith("job_") or not prefix[4:] or not rel:
        raise ValueError(f"Invalid source_id: {source_id}")
    return prefix[4:], rel
