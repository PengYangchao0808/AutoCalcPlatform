"""Controlled-area cache for remote job structure-viewer files.

Fetches required manifest/geometry/frequency files on demand via
``RemoteResultFetcher`` and caches them under
``<run_root>/.remote_cache/<job_id>/<rel_path>`` (atomic tmp+``os.replace``,
per-path ``threading.Lock``, permissions inherit run_root).

The flat remote layout (``<remote_task_dir>/<rel_path>``) is always tried
first.  Only when the flat read raises ``FileNotFoundError`` does a read-only
one-level nested fallback probe ``<remote_task_dir>/<dir>/<rel_path>``
candidates (legacy remote jobs created before scheduler markers were
uploaded wrote results nested under ``<molecule>/``).  Discovery needs
``fetcher.list_files``; fetchers without it degrade to the old behavior.

Must NOT write inside task dirs.  Must NOT block the event loop on SFTP
(uses the existing pool).
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acp.scheduler.remote.fetcher import RemoteResultFetcher

logger = logging.getLogger(__name__)

__all__ = ["RemoteStructureCache", "RemotePushError"]


class RemotePushError(RuntimeError):
    """A cache-to-remote write-back could not be performed."""


_CACHE_DIR_NAME = ".remote_cache"

_CATALOG_FETCH_PATHS: dict[str, tuple[str, ...]] = {
    "Confsearch": ("RESULT/confsearch/confsearch_manifest.json",),
    "PESsearch": (
        "RESULT/pes_search/pes_recommendations.json",
        "RESULT/pes_search/pes_review.json",
        "RESULT/pes_search/pes_profile.json",
    ),
    "BatchOptimize": ("RESULT/result_manifest.json",),
    "optimize": ("RESULT/result_manifest.json", "input.xyz"),
    "xtb_optimize": ("RESULT/result_manifest.json", "input.xyz"),
    "singlepoint": ("RESULT/result_manifest.json", "input.xyz"),
    "frequency": ("RESULT/result_manifest.json", "input.xyz"),
    "nmr": ("RESULT/result_manifest.json",),
    "scan": ("RESULT/trajectories/scan_trajectory.json",),
    # IRC trajectories are both catalog metadata and geometry.  The viewer
    # must parse them to know how many frame entries to expose.
    "irc": (
        "RESULT/irc/irc_forward.xyz",
        "RESULT/irc/irc_reverse.xyz",
    ),
    "legacy": (
        "RESULT/result_manifest.json",
        "RESULT/result_summary.json",
    ),
}

_CATALOG_READY_PATHS: dict[str, tuple[str, ...]] = {
    key: paths for key, paths in _CATALOG_FETCH_PATHS.items()
}
# PES profile data alone cannot produce structure entries; at least one of
# recommendations/review must exist before the remote catalog is usable.
_CATALOG_READY_PATHS["PESsearch"] = (
    "RESULT/pes_search/pes_recommendations.json",
    "RESULT/pes_search/pes_review.json",
)
# A simple-workflow input is only an optional fallback.  A completed remote
# result is considered synchronized once its result manifest is available.
for _simple_workflow in ("optimize", "xtb_optimize", "singlepoint", "frequency"):
    _CATALOG_READY_PATHS[_simple_workflow] = ("RESULT/result_manifest.json",)


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
        # Nested-layout fallback (read-only compat for pre-marker remote jobs):
        # per-job memoized candidate prefix + per-job discovery locks.  SFTP
        # calls are never wrapped in ``_master_lock`` (no global serialize).
        self._nested_prefixes: dict[str, str] = {}
        self._discovery_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def cache_path(self, job_id: str, rel_path: str) -> Path:
        """Return the cache path for *job_id*/*rel_path*.

        Raises:
            ValueError: If *job_id* or *rel_path* escapes the cache root.
        """
        normalized_job = Path(job_id)
        normalized = Path(rel_path)
        if (
            normalized_job.is_absolute()
            or any(part == ".." for part in normalized_job.parts)
            or normalized.is_absolute()
            or any(part == ".." for part in normalized.parts)
        ):
            raise ValueError(f"Cache path {rel_path!r} escapes the cache directory")
        cache_root = self._cache_root.resolve()
        job_root = (cache_root / normalized_job).resolve()
        if os.path.commonpath([str(cache_root), str(job_root)]) != str(cache_root):
            raise ValueError(f"Cache path {rel_path!r} escapes the cache directory")
        target = (job_root / normalized).resolve()
        if os.path.commonpath([str(job_root), str(target)]) != str(job_root):
            raise ValueError(f"Cache path {rel_path!r} escapes the cache directory")
        return target

    def job_root(self, job_id: str) -> Path:
        """Return the controlled cache root used to project one remote job."""
        return self.cache_path(job_id, ".")

    def _get_path_lock(self, cache_key: str) -> threading.Lock:
        with self._master_lock:
            lock = self._path_locks.get(cache_key)
            if lock is None:
                lock = threading.Lock()
                self._path_locks[cache_key] = lock
            return lock

    def _get_discovery_lock(self, job_id: str) -> threading.Lock:
        """Return the per-job lock serializing nested-layout discovery."""
        with self._master_lock:
            lock = self._discovery_locks.get(job_id)
            if lock is None:
                lock = threading.Lock()
                self._discovery_locks[job_id] = lock
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
        *,
        force: bool = False,
        raise_errors: bool = False,
    ) -> Path | None:
        """Fetch *rel_path* from the remote node and cache it.

        Returns the local cached path on success, or ``None`` when no
        fetcher is configured or the remote file does not exist.

        Args:
            force: Re-read from the remote even when a cached copy exists
                (mutable-file refresh before a write-back).  A genuinely
                absent remote file drops the stale local copy.
            raise_errors: Re-raise transport failures instead of degrading to
                ``None``; write-back callers need to distinguish "remote file
                missing" from "cannot reach the remote node".

        Must NOT write inside the task work_dir.
        """
        job_id = record.id
        target = self.cache_path(job_id, rel_path)
        cache_key = f"{job_id}:{rel_path}"
        lock = self._get_path_lock(cache_key)

        with lock:
            if not force and target.is_file():
                return target

            if self._fetcher_factory is None:
                logger.debug("No fetcher factory; cannot fetch %s/%s", job_id, rel_path)
                if raise_errors:
                    raise RemotePushError("Remote fetching is not configured")
                return None

            fetcher = self._fetcher_factory(job_id)
            if fetcher is None:
                logger.debug("Fetcher unavailable for job %s; cannot fetch %s", job_id, rel_path)
                if raise_errors:
                    raise RemotePushError(f"Fetcher unavailable for job {job_id}")
                return None
            try:
                data = fetcher.read_file(record, rel_path)
            except FileNotFoundError:
                data = self._discover_nested(fetcher, record, job_id, rel_path)
                if data is None:
                    logger.debug("Remote file not found (flat or nested): %s/%s", job_id, rel_path)
                    if force:
                        self._drop_cached(target)
                    return None
            except Exception:
                logger.warning("Failed to fetch %s/%s", job_id, rel_path, exc_info=True)
                if raise_errors:
                    raise
                return None

            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
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

    @staticmethod
    def _drop_cached(target: Path) -> None:
        """Remove a stale cached copy after the remote confirmed absence."""
        try:
            target.unlink(missing_ok=True)
        except OSError:
            logger.debug("Could not drop stale cache file %s", target, exc_info=True)

    def push_paths(self, record: Any, rel_paths: Sequence[str]) -> list[str]:
        """Upload cached files back to the remote job directory.

        The relative layout is preserved (cache path == remote path), which
        keeps manually-reviewed PES artifacts on the compute node — the
        authoritative location that downstream jobs, remote fetches and the
        retention cleanup operate on.

        Returns:
            The relative paths actually uploaded (missing cache files are
            skipped).

        Raises:
            RemotePushError: Fetcher/write-back unavailable, or any upload
                failed (partial uploads are possible; callers should surface
                the error and allow a retry).
        """
        if self._fetcher_factory is None:
            raise RemotePushError("Remote fetching is not configured")
        fetcher = self._fetcher_factory(record.id)
        write_fn = getattr(fetcher, "write_file", None) if fetcher is not None else None
        if write_fn is None:
            raise RemotePushError("Remote fetcher does not support write-back")

        uploaded: list[str] = []
        for rel_path in rel_paths:
            target = self.cache_path(record.id, rel_path)
            if not target.is_file():
                logger.debug("Skipping write-back of uncached %s/%s", record.id, rel_path)
                continue
            write_fn(record, rel_path, target.read_bytes())
            uploaded.append(rel_path)
        logger.info("Wrote back %d file(s) for job %s", len(uploaded), record.id)
        return uploaded

    def fetch_matching(self, record: Any, dir_rel: str, prefix: str) -> list[str]:
        """Fetch files named ``<prefix>*`` from one remote directory.

        Best-effort helper for listing-style views (review backups).  Never
        raises; fetchers without ``list_files`` return an empty list.
        """
        if self._fetcher_factory is None:
            return []
        fetcher = self._fetcher_factory(record.id)
        list_fn = getattr(fetcher, "list_files", None) if fetcher is not None else None
        if list_fn is None:
            return []
        try:
            entries = list_fn(record, dir_rel)
        except Exception:
            logger.debug("list_files failed for %s/%s", record.id, dir_rel, exc_info=True)
            return []

        fetched: list[str] = []
        for entry in entries:
            name = str(getattr(entry, "name", "") or "")
            if getattr(entry, "is_dir", False) or not name:
                continue
            if posixpath.basename(name).startswith(prefix):
                if self.fetch(record, name) is not None:
                    fetched.append(name)
        return fetched

    def _discover_nested(
        self,
        fetcher: Any,
        record: Any,
        job_id: str,
        rel_path: str,
    ) -> bytes | None:
        """Read *rel_path* from a one-level nested remote layout.

        Legacy remote jobs (created before scheduler markers were uploaded to
        the remote dir) write results nested under
        ``<remote_task_dir>/<molecule>/`` instead of flat at the task root.
        Only called after the flat ``read_file`` raised ``FileNotFoundError``;
        a flat hit never reaches here, so it costs zero extra SFTP calls.

        Fetchers without ``list_files`` (e.g. minimal fakes) degrade to the
        pre-fallback behavior.  The per-job memo only avoids repeated
        ``list_files`` calls; every fetch still tries the flat path first,
        then the memoized prefix, so mixed layouts (nested ``RESULT/...`` +
        flat ``input.xyz``) both resolve.

        Returns the remote bytes, or ``None`` when no candidate provides the
        file.  Never raises.
        """
        list_fn = getattr(fetcher, "list_files", None)
        if list_fn is None:
            logger.debug(
                "Fetcher for job %s has no list_files; nested fallback skipped for %s",
                job_id,
                rel_path,
            )
            return None

        prefix = self._nested_prefixes.get(job_id)
        if prefix is not None:
            data = self._read_nested_candidate(fetcher, record, job_id, prefix, rel_path)
            if data is not None:
                return data

        with self._get_discovery_lock(job_id):
            try:
                entries = list_fn(record)
            except Exception:
                logger.debug(
                    "list_files failed for job %s; nested fallback unavailable",
                    job_id,
                    exc_info=True,
                )
                return None
            candidates = sorted(
                {
                    str(getattr(entry, "name", "") or "")
                    for entry in entries
                    if getattr(entry, "is_dir", False)
                }
            )
            for name in candidates:
                if not name or name in {".", ".."}:
                    continue
                data = self._read_nested_candidate(fetcher, record, job_id, name, rel_path)
                if data is None:
                    continue
                self._nested_prefixes[job_id] = name
                logger.info(
                    "Nested remote layout detected for job %s: using prefix %r",
                    job_id,
                    name,
                )
                return data
            logger.debug("No nested candidate provides %s for job %s", rel_path, job_id)
            return None

    @staticmethod
    def _read_nested_candidate(
        fetcher: Any,
        record: Any,
        job_id: str,
        prefix: str,
        rel_path: str,
    ) -> bytes | None:
        """Try reading ``<prefix>/<rel_path>`` remotely; ``None`` on failure.

        ``FileNotFoundError`` and any other read error move discovery on to
        the next candidate (debug-logged).  Never raises.
        """
        candidate = f"{prefix}/{rel_path}"
        try:
            return fetcher.read_file(record, candidate)
        except FileNotFoundError:
            return None
        except Exception:
            logger.debug(
                "Nested candidate read failed for %s/%s",
                job_id,
                candidate,
                exc_info=True,
            )
            return None

    def fetch_catalog(self, record: Any, workflow: str) -> Path | None:
        """Fetch the small files required to build a remote viewer catalog.

        Geometry remains lazy except for IRC, whose multi-frame XYZ files are
        themselves the catalog index.  Missing optional files are tolerated.
        Returns the cached job root when a usable catalog source is present.
        """
        paths = _CATALOG_FETCH_PATHS.get(workflow, _CATALOG_FETCH_PATHS["legacy"])
        for rel_path in paths:
            self.fetch(record, rel_path)
        self._fetch_catalog_products(record)

        root = self.job_root(record.id)
        return root if self.catalog_ready(root, workflow) else None

    def _fetch_catalog_products(self, record: Any) -> None:
        """Fetch small auxiliary products referenced by a result manifest."""
        manifest_path = self.get_cached(record.id, "RESULT/result_manifest.json")
        if manifest_path is None:
            return
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        products = payload.get("products") if isinstance(payload, dict) else None
        if not isinstance(products, list):
            return
        for product in products:
            if not isinstance(product, dict) or product.get("kind") != "frequency_modes":
                continue
            product_path = str(product.get("path") or "").replace("\\", "/")
            if not product_path:
                continue
            rel_path = (
                product_path if product_path.startswith("RESULT/") else f"RESULT/{product_path}"
            )
            self.fetch(record, rel_path)

    # ------------------------------------------------------------------
    # Eviction
    # ------------------------------------------------------------------

    def purge_job(self, job_id: str) -> None:
        """Remove the entire cache directory for *job_id*."""
        job_dir = self.job_root(job_id)
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
                logger.info(
                    "Swept expired cache: %s (age=%.1f days)",
                    job_dir.name,
                    (time.time() - mtime) / 86400,
                )

        return removed

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    def required_files_absent(self, work_dir: Path, workflow: str) -> bool:
        """True when the workflow's primary manifest file is missing on disk.

        Used by the catalog endpoint to decide ``pending_fetch``.
        """
        return not self.catalog_ready(work_dir, workflow)

    def catalog_ready(self, root: Path, workflow: str) -> bool:
        """Return whether *root* contains enough metadata to build a catalog."""
        paths = _CATALOG_READY_PATHS.get(workflow, _CATALOG_READY_PATHS["legacy"])
        return any((Path(root) / rel_path).is_file() for rel_path in paths)
