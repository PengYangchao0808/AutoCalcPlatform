"""Controlled-area cache for remote job structure-viewer files.

Fetches required manifest/geometry/frequency files on demand via
``RemoteResultFetcher`` and caches them under
``<run_root>/.remote_cache/<job_id>/<rel_path>`` (atomic tmp+``os.replace``,
per-path ``threading.Lock``, permissions inherit run_root).

Must NOT write inside task dirs.  Must NOT block the event loop on SFTP
(uses the existing pool).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acp.scheduler.remote.fetcher import RemoteResultFetcher

logger = logging.getLogger(__name__)

__all__ = ["RemoteStructureCache"]

_CACHE_DIR_NAME = ".remote_cache"

_PRIMARY_MANIFEST: dict[str, str] = {
    "Confsearch": "RESULT/confsearch/confsearch_manifest.json",
    "PESsearch": "RESULT/pes_search/pes_profile.json",
    "BatchOptimize": "RESULT/result_manifest.json",
    "optimize": "RESULT/result_manifest.json",
    "xtb-optimize": "RESULT/result_manifest.json",
    "singlepoint": "RESULT/result_manifest.json",
    "frequency": "RESULT/result_manifest.json",
    "scan": "RESULT/trajectories/scan_trajectory.json",
    "irc": "RESULT/irc/",
    "legacy": "RESULT/result_manifest.json",
}


class RemoteStructureCache:
    """On-demand cache for remote job structure-viewer files.

    Args:
        run_root: The ACP run root (e.g. ``/var/lib/acp/runs``).
        fetcher_factory: Optional callable ``(job_id) -> RemoteResultFetcher``.
            When ``None``, ``fetch()`` always returns ``None``.
    """

    def __init__(
        self,
        run_root: Path,
        fetcher_factory: Callable[[str], RemoteResultFetcher] | None = None,
    ) -> None:
        self._run_root = Path(run_root).resolve()
        self._cache_root = self._run_root / _CACHE_DIR_NAME
        self._fetcher_factory = fetcher_factory
        self._master_lock = threading.Lock()
        self._path_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def cache_path(self, job_id: str, rel_path: str) -> Path:
        """Return the cache path for *job_id*/*rel_path*.

        Raises:
            ValueError: If *rel_path* escapes the cache root (``..`` segments).
        """
        normalized = Path(rel_path)
        parts = normalized.parts
        if any(p == ".." for p in parts):
            raise ValueError(
                f"Cache path {rel_path!r} escapes the cache directory"
            )
        return (self._cache_root / job_id / rel_path).resolve()

    def _get_path_lock(self, cache_key: str) -> threading.Lock:
        with self._master_lock:
            lock = self._path_locks.get(cache_key)
            if lock is None:
                lock = threading.Lock()
                self._path_locks[cache_key] = lock
            return lock

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_cached(self, job_id: str, rel_path: str) -> Path | None:
        """Return the cached path if it exists, else ``None``."""
        target = self.cache_path(job_id, rel_path)
        return target if target.is_file() else None

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    def fetch(
        self,
        record: Any,
        rel_path: str,
    ) -> Path | None:
        """Fetch *rel_path* from the remote node and cache it.

        Returns the local cached path on success, or ``None`` when no
        fetcher is configured or the remote file does not exist.

        Must NOT write inside the task work_dir.
        """
        job_id = record.id
        target = self.cache_path(job_id, rel_path)
        cache_key = f"{job_id}:{rel_path}"
        lock = self._get_path_lock(cache_key)

        with lock:
            if target.is_file():
                return target

            if self._fetcher_factory is None:
                logger.debug("No fetcher factory; cannot fetch %s/%s", job_id, rel_path)
                return None

            fetcher = self._fetcher_factory(job_id)
            if fetcher is None:
                logger.debug("Fetcher unavailable for job %s; cannot fetch %s", job_id, rel_path)
                return None
            try:
                data = fetcher.read_file(record, rel_path)
            except FileNotFoundError:
                logger.debug("Remote file not found: %s/%s", job_id, rel_path)
                return None
            except Exception:
                logger.warning("Failed to fetch %s/%s", job_id, rel_path, exc_info=True)
                return None

            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(target.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.replace(tmp_path, str(target))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            # Inherit run_root permissions
            try:
                run_root_stat = os.stat(str(self._run_root))
                os.chmod(str(target), run_root_stat.st_mode & 0o777)
            except OSError:
                pass

            logger.info("Cached %s/%s -> %s", job_id, rel_path, target)
            return target

    # ------------------------------------------------------------------
    # Eviction
    # ------------------------------------------------------------------

    def purge_job(self, job_id: str) -> None:
        """Remove the entire cache directory for *job_id*."""
        job_dir = self._cache_root / job_id
        if job_dir.is_dir():
            shutil.rmtree(job_dir, ignore_errors=True)
            logger.info("Purged cache for job %s", job_id)

    def sweep_expired(self, ttl_days: int = 7) -> int:
        """Remove cache entries older than *ttl_days*.

        Returns the number of directories removed.
        """
        if not self._cache_root.is_dir():
            return 0

        cutoff = time.time() - ttl_days * 86400
        removed = 0

        for job_dir in self._cache_root.iterdir():
            if not job_dir.is_dir():
                continue
            try:
                mtime = job_dir.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                shutil.rmtree(job_dir, ignore_errors=True)
                removed += 1
                logger.info("Swept expired cache: %s (age=%.1f days)", job_dir.name, (time.time() - mtime) / 86400)

        return removed

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    def required_files_absent(self, work_dir: Path, workflow: str) -> bool:
        """True when the workflow's primary manifest file is missing on disk.

        Used by the catalog endpoint to decide ``pending_fetch``.
        """
        primary = _PRIMARY_MANIFEST.get(workflow, "RESULT/result_manifest.json")
        target = work_dir / primary
        if primary.endswith("/"):
            return not target.is_dir()
        return not target.is_file()
