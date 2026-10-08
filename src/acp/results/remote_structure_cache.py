"""Controlled-area cache for remote job structure-viewer files.

Fetches required manifest/geometry/frequency files on demand via
``RemoteResultFetcher`` and caches them under
``<run_root>/.remote_cache/<job_id>/<rel_path>`` (atomic tmp+``os.replace``,
per-path ``threading.Lock``, permissions inherit run_root).

Two fences keep the cache fresh (todo 15 / IS-5):

* **Attempt fence** — every fetched file gets a persisted sidecar
  ``<file>.attempt`` recording the job attempt it was fetched for, and the
  job-level fence file ``.remote_cache/<job_id>.fence`` records the highest
  attempt observed.  A read for a different attempt is a miss (refetch), a
  new cache instance over the same root sees the same persisted identity,
  and a fence bump (in-place rerun) drops entries that carry no attempt
  identity because they can no longer be proven fresh.
* **Purge generation fence** — ``.fence`` also carries a purge generation
  counter.  ``fetch`` captures the generation before touching the node and
  re-validates it under a per-job lock before ``os.replace``; ``purge_job``
  bumps it first, so a fetch that began before the purge discards its write
  while a fetch that begins after the purge proceeds normally.

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


def _record_attempt(record: Any) -> int | None:
    """Return the manager's 1-based attempt number for cache fencing.

    Returns ``None`` for attempt-less records (legacy fakes), which keeps the
    pre-fence behavior: their entries carry no attempt identity.
    """
    from acp.scheduler.job_edit import attempt_number

    try:
        return int(attempt_number(record))
    except AttributeError:
        attempt = getattr(record, "attempt", None)
        return attempt if isinstance(attempt, int) else None


_CACHE_DIR_NAME = ".remote_cache"
# Job-level fence file (swept-orphan cleanup matches this suffix): persists
# {"attempt": <int|null>, "generation": <int>} for one job id.
_FENCE_SUFFIX = ".fence"
# Per-file attempt sidecar written right next to the cached payload.
_ATTEMPT_SIDECAR_SUFFIX = ".attempt"

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
        # Purge generation / attempt fence: per-job lock serializing fence
        # file updates and the payload write's final generation check.
        self._generation_locks: dict[str, threading.Lock] = {}

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

    def _get_generation_lock(self, job_id: str) -> threading.Lock:
        """Return the per-job lock serializing fence updates and final writes."""
        with self._master_lock:
            lock = self._generation_locks.get(job_id)
            if lock is None:
                lock = threading.Lock()
                self._generation_locks[job_id] = lock
            return lock

    # ------------------------------------------------------------------
    # Attempt / purge-generation fence
    # ------------------------------------------------------------------

    def _fence_path(self, job_id: str) -> Path:
        """Return the job-level fence file (sibling of the job cache dir)."""
        job_root = self.cache_path(job_id, ".")
        return job_root.with_name(job_root.name + _FENCE_SUFFIX)

    def _read_fence(self, job_id: str) -> tuple[int | None, int]:
        """Return ``(attempt, generation)`` from the fence file.

        Missing or unreadable fence files decode as ``(None, 0)``.
        """
        try:
            payload = json.loads(self._fence_path(job_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, 0
        if not isinstance(payload, dict):
            return None, 0
        attempt = payload.get("attempt")
        generation = payload.get("generation")
        return (
            attempt if isinstance(attempt, int) else None,
            generation if isinstance(generation, int) else 0,
        )

    def _write_fence(self, job_id: str, attempt: int | None, generation: int) -> None:
        """Atomically persist the job fence (callers hold the generation lock)."""
        path = self._fence_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps({"attempt": attempt, "generation": generation}))
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _read_generation(self, job_id: str) -> int:
        """Return the persisted purge generation for *job_id* (0 when unset)."""
        return self._read_fence(job_id)[1]

    @staticmethod
    def _attempt_sidecar_path(target: Path) -> Path:
        return Path(str(target) + _ATTEMPT_SIDECAR_SUFFIX)

    def _read_attempt_sidecar(self, target: Path) -> tuple[bool, int | None]:
        """Return ``(present, attempt)`` for a cached file's sidecar."""
        try:
            payload = json.loads(self._attempt_sidecar_path(target).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False, None
        if not isinstance(payload, dict) or "attempt" not in payload:
            return False, None
        attempt = payload.get("attempt")
        return True, (attempt if isinstance(attempt, int) else None)

    def _write_attempt_sidecar(self, target: Path, attempt: int | None) -> None:
        """Persist the attempt a cached file was fetched for (atomic)."""
        sidecar = self._attempt_sidecar_path(target)
        fd, tmp_path = tempfile.mkstemp(dir=str(sidecar.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps({"attempt": attempt}))
            os.replace(tmp_path, str(sidecar))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _cache_entry_fresh(self, job_id: str, target: Path, attempt: int | None) -> bool:
        """True when *target* may satisfy a read for *attempt*."""
        if not target.is_file():
            return False
        present, sidecar_attempt = self._read_attempt_sidecar(target)
        if present:
            return sidecar_attempt == attempt
        # Entry predates attempt fencing (no identity of its own): serve it
        # only while the job-level fence cannot contradict the read.
        if attempt is None:
            return True
        stored_attempt, _ = self._read_fence(job_id)
        return stored_attempt is None or stored_attempt == attempt

    def _invalidate_unknown_attempt_entries(self, job_id: str) -> None:
        """Drop cache files without attempt sidecars after a fence bump.

        Such entries were written before attempt fencing existed (or seeded
        directly); once the job-level fence advances past them they can never
        be proven fresh for the new attempt, so they are removed and refetched
        on demand.  Sidecar'd entries stay — their own identity fences them.
        """
        job_dir = self.cache_path(job_id, ".")
        if not job_dir.is_dir():
            return
        removed = 0
        for entry in sorted(job_dir.rglob("*")):
            if not entry.is_file():
                continue
            if entry.name.endswith(_ATTEMPT_SIDECAR_SUFFIX):
                continue
            if self._attempt_sidecar_path(entry).is_file():
                continue
            try:
                entry.unlink()
            except OSError:
                logger.debug("Could not drop unfenced cache entry %s", entry, exc_info=True)
                continue
            removed += 1
        if removed:
            logger.info(
                "Dropped %d unfenced cache entr(ies) for job %s after attempt fence bump",
                removed,
                job_id,
            )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_cached(
        self,
        job_id: str,
        rel_path: str,
        *,
        attempt: int | None = None,
    ) -> Path | None:
        """Return the cached path if it exists, else ``None``.

        Args:
            attempt: When given, only serve an entry whose persisted attempt
                identity equals *attempt* — a cross-attempt entry is a miss.
                Without it the entry is validated against the job-level
                attempt fence (when one exists), so a new attempt's reads
                never receive another attempt's bytes.
        """
        target = self.cache_path(job_id, rel_path)
        if not target.is_file():
            return None
        present, sidecar_attempt = self._read_attempt_sidecar(target)
        if attempt is not None:
            return target if present and sidecar_attempt == attempt else None
        if not present:
            return target
        stored_attempt, _ = self._read_fence(job_id)
        if stored_attempt is None:
            return target
        return target if sidecar_attempt == stored_attempt else None

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
        attempt = _record_attempt(record)
        target = self.cache_path(job_id, rel_path)
        # Purge fence: capture the generation before any waiting/IO so a fetch
        # that began before purge_job can never repopulate the cache.
        captured_generation = self._read_generation(job_id)
        cache_key = f"{job_id}:{rel_path}"
        lock = self._get_path_lock(cache_key)

        with lock:
            if not force and self._cache_entry_fresh(job_id, target, attempt):
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

            # Establish/advance the attempt fence and re-validate the purge
            # generation before touching the node.
            with self._get_generation_lock(job_id):
                if self._read_generation(job_id) != captured_generation:
                    logger.debug(
                        "Cache for job %s purged before fetch of %s; discarding",
                        job_id,
                        rel_path,
                    )
                    return None
                if attempt is not None:
                    stored_attempt, generation = self._read_fence(job_id)
                    if stored_attempt is None:
                        self._write_fence(job_id, attempt=attempt, generation=generation)
                    elif attempt > stored_attempt:
                        # In-place rerun: sidecar'd entries stay fenced by
                        # their own identity; entries without attempt identity
                        # can never be proven fresh for the new attempt.
                        self._write_fence(job_id, attempt=attempt, generation=generation)
                        self._invalidate_unknown_attempt_entries(job_id)

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

            with self._get_generation_lock(job_id):
                if self._read_generation(job_id) != captured_generation:
                    logger.info(
                        "Discarding fetch of %s/%s: cache purged while fetching",
                        job_id,
                        rel_path,
                    )
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
                self._write_attempt_sidecar(target, attempt)

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
        """Remove a stale cached copy and its attempt sidecar after a confirmed remote absence."""
        for path in (target, Path(str(target) + _ATTEMPT_SIDECAR_SUFFIX)):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Could not drop stale cache file %s", path, exc_info=True)

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
        paths = tuple(dict.fromkeys((*paths, "RESULT/result_manifest.json", "RESULT/frame_candidates.json")))
        for rel_path in paths:
            self.fetch(record, rel_path)
        self._fetch_catalog_products(record)

        root = self.job_root(record.id)
        return root if self.catalog_ready(root, workflow) else None

    def fetch_reusable_geometries(self, record: Any, root: Path) -> None:
        """Fetch every formal geometry before indexing or destructive rerun."""
        from acp.results.manifest import load_result_manifest
        from acp.results.structure_policy import reusable_product
        from acp.confsearch.manifest import find_confsearch_manifest, read_manifest, resolve_manifest_geometry
        paths: set[str] = set()
        manifest = load_result_manifest(root)
        if manifest:
            for product in manifest.products:
                if reusable_product(product.to_dict()):
                    paths.add(product.path if product.path.startswith("RESULT/") else "RESULT/" + product.path)
        conformers = find_confsearch_manifest(root)
        if conformers:
            for row in read_manifest(conformers).get("conformers") or []:
                geometry = resolve_manifest_geometry(conformers, str(row.get("geometry") or ""))
                paths.add(geometry.resolve().relative_to(root.resolve()).as_posix())
        for rel in sorted(paths):
            if self.fetch(record, rel, raise_errors=True) is None:
                raise ValueError(f"远程结构尚未同步: {rel}")

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
        """Remove the entire cache directory for *job_id*.

        Bumps the job's persisted purge generation first: any fetch that began
        before this purge observes the change and discards its write, while a
        fetch that begins afterwards proceeds normally.
        """
        job_dir = self.job_root(job_id)
        with self._get_generation_lock(job_id):
            stored_attempt, generation = self._read_fence(job_id)
            self._write_fence(job_id, attempt=stored_attempt, generation=generation + 1)
            removed = job_dir.is_dir()
            if removed:
                shutil.rmtree(job_dir, ignore_errors=True)
        if removed:
            logger.info("Purged cache for job %s", job_id)

    def sweep_expired(self, ttl_days: int = 7) -> int:
        """Remove cache entries older than *ttl_days*.

        Also removes fence files whose job directory is gone and older than
        the TTL (purge keeps the generation fence alive for in-flight writes;
        only stale orphans are collected).  Returns the number of job
        directories removed.
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

        for fence in self._cache_root.glob(f"*{_FENCE_SUFFIX}"):
            if not fence.is_file():
                continue
            job_name = fence.name[: -len(_FENCE_SUFFIX)]
            if (self._cache_root / job_name).is_dir():
                continue
            try:
                if fence.stat().st_mtime >= cutoff:
                    continue
                fence.unlink()
            except OSError:
                logger.debug("Could not sweep orphan fence file %s", fence, exc_info=True)

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
