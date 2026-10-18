"""Legacy-side composition root for the typed :class:`DataResourcePort` seam.

B02-1 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 11).  The
Services-side port shape is frozen in :mod:`acp_services.data.contracts`; that
package must not import this application, so the **delegation adapters live
here**.  This module wraps the *existing* implementations and returns an object
that satisfies :class:`~acp_services.data.contracts.DataResourcePort`:

* inputs / sources  → :class:`acp.scheduler.structure_source_store.StructureSourceStore`
  (``upsert_index_entries`` / ``list_by_job`` / ``get`` + ``source_uid_for``)
* artifacts/manifest → :class:`acp.storage.manifest.ResultManifest` (v2 writer/reader,
  legacy-version compatible)
* candidates        → :mod:`acp.results.frame_candidate_store` authority helpers
  (``load_authority`` / ``write_authority`` — the authoritative
  ``RESULT/frame_candidates.json`` file is preserved byte-for-byte)
* transfers / cache → :class:`acp.results.remote_structure_cache.RemoteStructureCache`
  (``push_paths`` / ``fetch`` / ``get_cached`` / ``catalog_ready``)

**This is a seam, not a switch.**  Nothing in the legacy read/write paths is
rerouted here; the port simply exposes the same operations through typed
methods so later slices can consume them.  Behaviour, the authoritative
``frame_candidates.json`` file, ``source_uid_for`` stability, ``expected_revision``
conflict semantics and legacy manifest read compatibility are all unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from acp.results.frame_candidate_store import (
    FRAME_CANDIDATES_SCHEMA,
    load_authority,
    write_authority,
)
from acp.results.remote_structure_cache import RemoteStructureCache
from acp.scheduler.remote.paths import (
    compose_remote_dir,
    resolve_remote_dir,
    storage_relative_path,
)
from acp.scheduler.structure_source_store import StructureSourceStore, source_uid_for
from acp.storage.manifest import ResultManifest
from acp_services.data.contracts import (
    Availability,
    AvailabilityQuery,
    CacheEntry,
    CacheLookup,
    CandidateSnapshot,
    CandidateWrite,
    DataAvailability,
    DataResourcePort,
    DataRevisionConflictError,
    Freshness,
    InputRecord,
    InputRegistration,
    InputValidation,
    InputValidationRequest,
    ManifestPublication,
    ManifestPublish,
    ManifestRead,
    ManifestSnapshot,
    RemoteJobRef,
    SourceUpsert,
    TransferDirection,
    TransferDownload,
    TransferReport,
    TransferResult,
    TransferUpload,
)
from acp_services.data.operations import (
    PrepareExecutionResult,
    PrepareLease,
)
from acp_services.data.storage_binding import (
    MAPPING_VERSION_CURRENT,
    MAPPING_VERSION_LEGACY,
    PATH_SOURCE_FALLBACK,
    PATH_SOURCE_LEGACY_FLAT,
    LegacyResolution,
)
from acp_services.data.transfer import (
    FenceSnapshot,
    PublishOutcome,
    SourceFailureKind,
    SourceRead,
    TransferPipeline,
    TransferPrepareExecutor,
    TransferPublicationStore,
    TransferSourceError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "build_data_resource_port",
    "build_storage_allocation_port",
    "build_prepare_executor",
    "build_transfer_cache_port",
    "build_transfer_source_port",
    "build_transfer_authorization_port",
    "build_transfer_pipeline",
    "build_transfer_executor",
]

_MANIFEST_READ_ERRORS = (
    FileNotFoundError,
    OSError,
    json.JSONDecodeError,
    ValueError,
    TypeError,
    KeyError,
)


def _record(ref: RemoteJobRef) -> Any:
    """Adapt the neutral job reference to the record shape the cache expects."""
    return SimpleNamespace(id=ref.job_id, attempt=ref.attempt)


class _DataResourceAdapter:
    """Delegate :class:`DataResourcePort` onto the existing implementations."""

    def __init__(self, source_store: StructureSourceStore, cache: RemoteStructureCache) -> None:
        self._source_store = source_store
        self._cache = cache

    # --- inputs ---------------------------------------------------------
    def register_input(self, request: InputRegistration) -> InputRecord:
        entry: dict[str, Any] = {
            "job_id": request.job_id,
            "path": request.relative_path,
            "project_id": request.project_id,
            "label": request.label,
            "role": request.role,
            "source_kind": request.source_kind,
            "availability": request.availability,
            "remote": False,
        }
        if request.metadata:
            entry.update(dict(request.metadata))
        affected = self._source_store.upsert_index_entries([entry])
        return InputRecord(
            input_id=request.input_id,
            source_uid=source_uid_for(request.job_id, request.relative_path),
            job_id=request.job_id,
            relative_path=request.relative_path,
            registered=bool(affected),
        )

    def validate_input(self, request: InputValidationRequest) -> InputValidation:
        uid = source_uid_for(request.job_id, request.relative_path)
        row = self._source_store.get(uid)
        if row is None:
            return InputValidation(
                source_uid=uid, valid=False, exists=False, reason="not_registered"
            )
        availability = str(row.get("availability") or "")
        exists = availability not in ("", "missing")
        return InputValidation(
            source_uid=uid,
            valid=exists,
            exists=exists,
            availability=availability,
            reason=None if exists else "missing",
        )

    # --- artifacts / manifest ------------------------------------------
    def publish_manifest(self, request: ManifestPublish) -> ManifestPublication:
        root = Path(request.task_root)
        manifest = ResultManifest.from_dict(dict(request.manifest))
        path = manifest.write(root / "RESULT")
        revision = int(request.manifest.get("revision", 0) or 0)
        return ManifestPublication(
            path=str(path),
            version=manifest.version,
            product_count=len(manifest.products),
            revision=revision,
        )

    def read_manifest(self, request: ManifestRead) -> ManifestSnapshot:
        root = Path(request.task_root)
        try:
            manifest = ResultManifest.read(root / "RESULT")
        except _MANIFEST_READ_ERRORS:
            return ManifestSnapshot(found=False, version=0, manifest={})
        return ManifestSnapshot(found=True, version=manifest.version, manifest=manifest.to_dict())

    # --- sources -------------------------------------------------------
    def upsert_sources(self, request: SourceUpsert) -> int:
        return self._source_store.upsert_index_entries(
            [dict(entry) for entry in request.entries],
            discovery_version=request.discovery_version,
            replace_job_id=request.replace_job_id,
        )

    def list_sources(self, job_id: str) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._source_store.list_by_job(job_id))

    def source_uid(self, job_id: str, relative_path: str) -> str:
        return source_uid_for(job_id, relative_path)

    # --- candidates ----------------------------------------------------
    def save_candidates(self, request: CandidateWrite) -> CandidateSnapshot:
        root = Path(request.task_root)
        existing = load_authority(root)
        current_revision = int(existing.get("revision", 0)) if existing else 0
        if request.expected_revision is not None and int(request.expected_revision) != (
            current_revision
        ):
            raise DataRevisionConflictError(
                f"revision conflict: stored={current_revision}, "
                f"expected={request.expected_revision}"
            )
        payload: dict[str, Any] = dict(request.payload)
        payload["schema_version"] = FRAME_CANDIDATES_SCHEMA
        payload["revision"] = current_revision + 1
        payload.setdefault("candidates", [])
        write_authority(root, payload)
        return self.read_candidates(request.task_root)

    def read_candidates(self, task_root: str) -> CandidateSnapshot:
        payload = load_authority(Path(task_root))
        if payload is None:
            return CandidateSnapshot(found=False, schema_version=None, revision=0, candidates=())
        schema_version = str(payload.get("schema_version") or "") or None
        candidates = payload.get("candidates") or []
        return CandidateSnapshot(
            found=True,
            schema_version=schema_version,
            revision=int(payload.get("revision", 0) or 0),
            candidates=tuple(dict(candidate) for candidate in candidates),
        )

    def list_candidates(self, task_root: str) -> tuple[Mapping[str, Any], ...]:
        return self.read_candidates(task_root).candidates

    # --- transfers -----------------------------------------------------
    def upload_paths(self, request: TransferUpload) -> TransferReport:
        record = _record(request.job)
        uploaded = self._cache.push_paths(record, list(request.relative_paths))
        uploaded_set = set(uploaded)
        skipped = tuple(path for path in request.relative_paths if path not in uploaded_set)
        return TransferReport(
            direction=TransferDirection.UPLOAD,
            uploaded=tuple(uploaded),
            skipped=skipped,
        )

    def download_file(self, request: TransferDownload) -> TransferResult:
        record = _record(request.job)
        path = self._cache.fetch(
            record,
            request.relative_path,
            force=request.force,
            raise_errors=request.raise_errors,
        )
        return TransferResult(
            direction=TransferDirection.DOWNLOAD,
            relative_path=request.relative_path,
            cached_path=str(path) if path is not None else None,
            fetched=path is not None,
        )

    # --- cache ---------------------------------------------------------
    def cache_entry(self, request: CacheLookup) -> CacheEntry:
        record = _record(request.job)
        path = self._cache.get_cached(
            record.id,
            request.relative_path,
            attempt=request.job.attempt,
        )
        return CacheEntry(
            relative_path=request.relative_path,
            present=path is not None,
            cached_path=str(path) if path is not None else None,
        )

    def resource_availability(self, request: AvailabilityQuery) -> DataAvailability:
        if request.relative_path:
            entry = self.cache_entry(
                CacheLookup(
                    job=RemoteJobRef(job_id=request.job_id),
                    relative_path=request.relative_path,
                )
            )
            availability = Availability.READY if entry.present else Availability.ABSENT
        else:
            ready = self._cache.catalog_ready(Path(request.root), request.workflow)
            availability = Availability.READY if ready else Availability.ABSENT
        freshness = Freshness.CURRENT if availability is Availability.READY else Freshness.UNKNOWN
        return DataAvailability(
            resource_id=request.resource_id,
            availability=availability,
            freshness=freshness,
            basis_revision=request.basis_revision,
            expected_revision=request.expected_revision,
        )


def build_data_resource_port(
    run_root: str | Path,
    *,
    db_path: str | Path | None = None,
    source_store: StructureSourceStore | None = None,
    cache: RemoteStructureCache | None = None,
) -> DataResourcePort:
    """Build the typed Data port from the real legacy implementations.

    Args:
        run_root: Task data root; the cache is rooted here.
        db_path: SQLite path for the structure-source store; defaults to
            ``<run_root>/acp_jobs.db`` (the same DB the scheduler uses).
        source_store: Pre-built :class:`StructureSourceStore` (created when omitted).
        cache: Pre-built :class:`RemoteStructureCache` (created when omitted).

    Returns:
        A :class:`DataResourcePort`-satisfying object delegating to the real
        implementations (no re-implementation, no schema change).
    """
    run_root_path = Path(run_root)
    resolved_db = Path(db_path) if db_path is not None else run_root_path / "acp_jobs.db"
    store = source_store or StructureSourceStore(resolved_db)
    structure_cache = cache or RemoteStructureCache(run_root_path)
    return _DataResourceAdapter(source_store=store, cache=structure_cache)


# --------------------------------------------------------------------------- #
# B02-2 (todo 12) — storage-binding allocation adapter
# --------------------------------------------------------------------------- #


def _relative_from(remote_dir: str, base: str) -> str:
    """Recover the relative path of *remote_dir* under *base* (best effort)."""
    base = (base or "").rstrip("/")
    if base and remote_dir.startswith(base + "/"):
        return remote_dir[len(base) + 1 :].lstrip("/")
    return posixpath.basename(remote_dir)


class _StorageAllocationAdapter:
    """Delegate the allocation seam onto ``remote/paths.py`` + the exclusive claim.

    Wraps the *real* entry points — ``storage_relative_path`` (paths.py:46),
    ``compose_remote_dir`` (paths.py:59) and ``resolve_remote_dir`` (paths.py:85)
    plus :meth:`FileStager.claim_remote_job_dir` — so the Services allocator
    never re-implements directory derivation or ownership.  Human-readable
    directories, the single persisted path key, scheduler markers, the
    rerun/continue same-dir semantics and the legacy-adoption chain are all
    preserved because they *are* the wrapped implementation.
    """

    def __init__(
        self,
        *,
        node_lookup: Callable[[str], Any] | None = None,
        stager: Any | None = None,
    ) -> None:
        self._node_lookup = node_lookup
        self._stager = stager

    # --- endpoint resolution -------------------------------------------
    def _node(self, endpoint: str, *, run_root: str | None = None) -> Any:
        if not endpoint:
            # Local endpoint: the controlled root is the remote work dir base.
            return SimpleNamespace(name="", remote_work_dir=str(run_root or ""))
        if self._node_lookup is not None:
            return self._node_lookup(endpoint)
        from acp.scheduler.remote.config import RemoteExecutionConfig
        from cccp.config import load_config

        cfg = RemoteExecutionConfig.from_config_dict(load_config().get("cluster", {}) or {})
        for node in cfg.nodes:
            if node.name == endpoint:
                return node
        raise KeyError(f"unknown remote endpoint {endpoint!r}")

    def _stager_obj(self) -> Any:
        if self._stager is None:
            from acp.scheduler.remote.sftp import FileStager
            from acp.scheduler.remote.ssh import SSHConnectionPool

            self._stager = FileStager(SSHConnectionPool())
        return self._stager

    # --- StorageAllocationPort -----------------------------------------
    def storage_relative_path(self, *, work_dir: str, run_root: str) -> str:
        return storage_relative_path(SimpleNamespace(work_dir=work_dir), run_root)

    def compose_remote_dir(self, relative_path: str, *, endpoint: str) -> str:
        return compose_remote_dir(relative_path, self._node(endpoint))

    def resolve_legacy_dir(
        self,
        *,
        job_id: str,
        attempt: int,
        work_dir: str,
        run_root: str,
        endpoint: str,
        task_leaf: str = "",
        explicit: str | None = None,
        persisted_remote_dir: str | None = None,
        persisted_relative: str | None = None,
        probe: Callable[[str], bool] | None = None,
    ) -> LegacyResolution:
        result: dict[str, Any] = {}
        if persisted_remote_dir:
            result["remote_dir"] = persisted_remote_dir
        if persisted_relative:
            result["remote"] = {"schema": 1, "relative": persisted_relative, "attempt": attempt}
        spec = (
            SimpleNamespace(uses_v2_naming=True, task_dir_name=lambda: task_leaf)
            if task_leaf
            else None
        )
        record = SimpleNamespace(
            id=job_id, attempt=attempt, work_dir=work_dir, result=result, spec=spec
        )
        node = self._node(endpoint, run_root=run_root)
        remote_dir, fallback = resolve_remote_dir(
            record, node, explicit=explicit, probe=probe, run_root=run_root
        )
        meta = (record.result or {}).get("remote") or {}
        path_source = str(meta.get("path_source") or "")
        relative = str(meta.get("relative") or "") or _relative_from(
            remote_dir, getattr(node, "remote_work_dir", "")
        )
        legacy = path_source in (PATH_SOURCE_LEGACY_FLAT, PATH_SOURCE_FALLBACK)
        return LegacyResolution(
            remote_dir=remote_dir,
            relative_path=relative,
            path_source=path_source,
            fallback=fallback,
            mapping_version=MAPPING_VERSION_LEGACY if legacy else MAPPING_VERSION_CURRENT,
        )

    def claim_dir(
        self,
        *,
        remote_dir: str,
        job_id: str,
        attempt: int,
        work_dir: str,
        endpoint: str,
    ) -> str:
        node = self._node(endpoint)
        record = SimpleNamespace(
            id=job_id, attempt=attempt, work_dir=work_dir, result={}, spec=None
        )
        return self._stager_obj().claim_remote_job_dir(node, remote_dir, record)


def build_storage_allocation_port(
    *,
    node_lookup: Callable[[str], Any] | None = None,
    stager: Any | None = None,
) -> Any:
    """Build the injected storage-allocation port from the real legacy mechanism.

    Args:
        node_lookup: Optional ``endpoint -> node`` resolver (tests inject a fake).
            When omitted the real ``cluster`` config is loaded.
        stager: Optional pre-built :class:`FileStager` (created on first claim).

    Returns:
        An object satisfying :class:`acp_services.data.storage_binding.StorageAllocationPort`;
        it delegates to ``remote/paths.py`` and the exclusive claim verbatim.
    """
    return _StorageAllocationAdapter(node_lookup=node_lookup, stager=stager)


# --------------------------------------------------------------------------- #
# B02-3 (todo 13) — persistent prepare-operation executor adapter
# --------------------------------------------------------------------------- #


class _PrepareExecutorAdapter:
    """Execute a prepare operation by prefetching through the real cache path.

    B02-3 (plan todo 13).  The Services-side worker owns the persistent operation
    state machine; this adapter (legacy side) is the *mechanism* it injects — it
    delegates to :class:`acp.results.remote_structure_cache.RemoteStructureCache`
    (``catalog_ready`` / ``job_root`` / ``fetch_catalog``), the exact same path
    the scheduler's in-process prefetch used, so the ``pending_fetch → ready``
    flip and the on-disk cache layout are unchanged.

    The executor never decides state: it only reports whether the catalog is
    prepared and is retryable.  A non-terminal / local / missing job is a
    permanent (non-retryable) skip; a transport failure is a retryable one.
    """

    def __init__(
        self,
        *,
        job_lookup: Callable[[str], Any],
        cache_provider: Callable[[], RemoteStructureCache],
        is_remote: Callable[[Any], bool] | None = None,
    ) -> None:
        self._job_lookup = job_lookup
        self._cache_provider = cache_provider
        self._is_remote = is_remote

    def prefetch(self, job_id: str) -> PrepareExecutionResult:
        record = self._job_lookup(job_id)
        if record is None:
            return PrepareExecutionResult(prepared=False, detail="job_missing", retryable=False)
        status = getattr(record, "status", None)
        if status is None or not getattr(status, "is_terminal", False):
            return PrepareExecutionResult(prepared=False, detail="not_terminal", retryable=False)
        if self._is_remote is not None and not self._is_remote(record):
            return PrepareExecutionResult(prepared=False, detail="not_remote", retryable=False)
        cache = self._cache_provider()
        workflow = str(getattr(getattr(record, "spec", None), "workflow", "") or "")
        work_dir = Path(str(record.work_dir))
        if cache.catalog_ready(work_dir, workflow):
            return PrepareExecutionResult(
                prepared=True,
                availability=Availability.READY,
                detail="already_ready",
                retryable=False,
            )
        if cache.catalog_ready(cache.job_root(job_id), workflow):
            return PrepareExecutionResult(
                prepared=True,
                availability=Availability.READY,
                detail="already_ready",
                retryable=False,
            )
        if cache.fetch_catalog(record, workflow) is not None:
            return PrepareExecutionResult(
                prepared=True,
                availability=Availability.READY,
                detail="fetched",
                retryable=False,
            )
        return PrepareExecutionResult(
            prepared=False,
            availability=Availability.PREPARING,
            detail="not_ready",
            retryable=True,
        )

    def execute(self, lease: PrepareLease) -> PrepareExecutionResult:
        return self.prefetch(lease.job_id)


def build_prepare_executor(
    *,
    job_lookup: Callable[[str], Any],
    cache_provider: Callable[[], RemoteStructureCache] | None = None,
    cache: RemoteStructureCache | None = None,
    is_remote: Callable[[Any], bool] | None = None,
) -> _PrepareExecutorAdapter:
    """Build the injected prepare executor from the real remote cache path.

    Args:
        job_lookup: ``job_id -> JobRecord | None`` (the scheduler store).
        cache_provider: Lazy accessor for the shared ``RemoteStructureCache``
            singleton (the fetcher factory inside it resolves the current
            fetcher, so the adapter stays valid across fetcher swaps).
        cache: Pre-built cache (alternative to ``cache_provider``).
        is_remote: Optional predicate distinguishing remote jobs; when omitted
            every terminal job is eligible.

    Returns:
        An object satisfying :class:`acp_services.data.operations.PrepareExecutorPort`.
    """
    if cache_provider is None:
        if cache is None:
            raise ValueError("build_prepare_executor requires cache or cache_provider")
        resolved = cache

        def cache_provider() -> RemoteStructureCache:  # type: ignore[misc]
            return resolved

    return _PrepareExecutorAdapter(
        job_lookup=job_lookup,
        cache_provider=cache_provider,
        is_remote=is_remote,
    )


# --------------------------------------------------------------------------- #
# B02-4 (todo 14) — verified transfer pipeline adapters
# --------------------------------------------------------------------------- #


class _TransferCacheAdapter:
    """Delegate the transfer cache port onto :class:`RemoteStructureCache`.

    The Services pipeline owns verification policy; this adapter supplies the
    **mechanism** — and only the mechanism — by reusing the real cache:

    * ``cache_path`` — the traversal guard (``remote_structure_cache.py:158``).
    * ``_read_fence`` / ``_read_generation`` — the persisted attempt fence +
      purge generation (``:221``/``:255``).
    * ``_get_generation_lock`` / ``_write_fence`` /
      ``_invalidate_unknown_attempt_entries`` — the exact fence protocol the
      cache's own ``fetch`` uses, so a verified publish is indistinguishable
      from a normal cached fetch.
    * ``_write_attempt_sidecar`` + ``os.replace`` — the same atomic publish +
      sidecar the cache writes at ``:466-478``; the file is never left partial
      and no second publish implementation exists.

    ``push_paths`` is delegated verbatim (``remote_structure_cache.py:499``).
    """

    def __init__(self, cache: RemoteStructureCache) -> None:
        self._cache = cache

    def guarded_path(self, job_id: str, relative_path: str) -> Path:
        return self._cache.cache_path(job_id, relative_path)

    def fence(self, job_id: str) -> FenceSnapshot:
        attempt, generation = self._cache._read_fence(job_id)
        return FenceSnapshot(attempt=attempt, generation=generation)

    def publish(
        self,
        job_id: str,
        relative_path: str,
        tmp_path: str,
        *,
        attempt: int,
        expected_generation: int,
    ) -> PublishOutcome:
        cache = self._cache
        target = cache.cache_path(job_id, relative_path)  # traversal guard
        with cache._get_generation_lock(job_id):
            if cache._read_generation(job_id) != expected_generation:
                return PublishOutcome(published=False, discarded=True, reason="generation_changed")
            stored_attempt, generation = cache._read_fence(job_id)
            if stored_attempt is not None and int(attempt) < int(stored_attempt):
                return PublishOutcome(published=False, discarded=True, reason="attempt_advanced")
            if attempt is not None:
                if stored_attempt is None:
                    cache._write_fence(job_id, attempt=int(attempt), generation=generation)
                elif int(attempt) > int(stored_attempt):
                    cache._write_fence(job_id, attempt=int(attempt), generation=generation)
                    cache._invalidate_unknown_attempt_entries(job_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp_path, str(target))
            cache._write_attempt_sidecar(target, int(attempt))
            try:
                run_root_stat = os.stat(str(cache._run_root))
                os.chmod(str(target), run_root_stat.st_mode & 0o777)
            except OSError:
                pass
        logger.info("Published verified transfer %s/%s -> %s", job_id, relative_path, target)
        return PublishOutcome(published=True, cached_path=str(target))

    def exists(self, job_id: str, relative_path: str) -> bool:
        try:
            return self._cache.cache_path(job_id, relative_path).is_file()
        except ValueError:
            return False

    def list_files(self, job_id: str) -> tuple[str, ...]:
        root = self._cache.job_root(job_id)
        if not root.is_dir():
            return ()
        found: list[str] = []
        for entry in sorted(root.rglob("*")):
            if not entry.is_file():
                continue
            if entry.name.endswith(".attempt"):
                continue
            found.append(entry.relative_to(root).as_posix())
        return tuple(found)

    def push_paths(self, job_id: str, relative_paths: Any) -> list[str]:
        record = SimpleNamespace(id=job_id)
        return self._cache.push_paths(record, list(relative_paths))


class _TransferSourceAdapter:
    """Delegate the transfer source port onto the real remote fetcher.

    Classification of fetch failures is the SFTP taxonomy the pipeline needs:
    a missing file, a permission denial (revoked access) and everything else
    (transient transport) are distinct — an authorization revocation is never
    conflated with "file not there".
    """

    def __init__(self, cache: RemoteStructureCache) -> None:
        self._cache = cache

    def _fetcher(self, job_id: str) -> Any:
        factory = getattr(self._cache, "_fetcher_factory", None)
        if factory is None:
            return None
        try:
            return factory(job_id)
        except Exception:  # pragma: no cover - factory failure is a transport failure
            logger.warning("transfer fetcher factory failed for %s", job_id, exc_info=True)
            return None

    def read(
        self,
        job_id: str,
        attempt: int,
        relative_path: str,
        *,
        byte_range: Any = None,
    ) -> SourceRead:
        fetcher = self._fetcher(job_id)
        if fetcher is None:
            raise TransferSourceError(SourceFailureKind.TRANSIENT, "no remote fetcher configured")
        record = SimpleNamespace(id=job_id, attempt=int(attempt))
        try:
            if byte_range is not None:
                data = bytes(
                    fetcher.read_range(
                        record, relative_path, int(byte_range.offset), int(byte_range.limit)
                    )
                )
            else:
                data = bytes(fetcher.read_file(record, relative_path))
        except FileNotFoundError as exc:
            raise TransferSourceError(SourceFailureKind.NOT_FOUND, str(exc)) from exc
        except PermissionError as exc:
            raise TransferSourceError(SourceFailureKind.PERMISSION_DENIED, str(exc)) from exc
        except Exception as exc:
            raise self._classify(exc) from exc
        revision = 0
        if byte_range is None:
            info = self._file_info(fetcher, record, relative_path)
            if info is not None:
                revision = int(float(getattr(info, "mtime", 0.0)) * 1_000_000)
        return SourceRead(data=data, size_bytes=len(data), revision=revision, generation=0)

    @staticmethod
    def _file_info(fetcher: Any, record: Any, relative_path: str) -> Any:
        info_fn = getattr(fetcher, "file_info", None)
        if info_fn is None:
            return None
        try:
            return info_fn(record, relative_path)
        except Exception:
            return None

    @staticmethod
    def _classify(exc: BaseException) -> TransferSourceError:
        errno = getattr(exc, "errno", None)
        if errno in (1, 13, 17):  # EPERM / EACCES / EEXIST surfaced by paramiko
            return TransferSourceError(SourceFailureKind.PERMISSION_DENIED, str(exc))
        text = str(exc).lower()
        if "permission denied" in text or "access denied" in text:
            return TransferSourceError(SourceFailureKind.PERMISSION_DENIED, str(exc))
        if "not found" in text or "no such file" in text:
            return TransferSourceError(SourceFailureKind.NOT_FOUND, str(exc))
        return TransferSourceError(SourceFailureKind.TRANSIENT, str(exc))


class _TransferAuthorizationAdapter:
    """Delegate the re-checked authorization onto an injected decision callable.

    With no callable supplied every transfer is allowed (the local-single
    deployment boundary); a real policy supplies a callable that receives the
    transfer identity and re-evaluates the grant at both check points.
    """

    def __init__(self, decision: Callable[[Mapping[str, Any]], bool] | None = None) -> None:
        self._decision = decision

    def authorize(
        self,
        *,
        job_id: str,
        attempt: int,
        relative_path: str,
        action: str,
        authorization_version: int,
    ) -> bool:
        if self._decision is None:
            return True
        return bool(
            self._decision(
                {
                    "job_id": job_id,
                    "attempt": attempt,
                    "relative_path": relative_path,
                    "action": action,
                    "authorization_version": authorization_version,
                }
            )
        )


def build_transfer_cache_port(cache: RemoteStructureCache) -> _TransferCacheAdapter:
    """Wrap the real structure cache as the transfer cache port."""
    return _TransferCacheAdapter(cache)


def build_transfer_source_port(cache: RemoteStructureCache) -> _TransferSourceAdapter:
    """Wrap the real cache's fetcher as the transfer source port."""
    return _TransferSourceAdapter(cache)


def build_transfer_authorization_port(
    decision: Callable[[Mapping[str, Any]], bool] | None = None,
) -> _TransferAuthorizationAdapter:
    """Build the re-checked authorization port (allow-all when no policy given)."""
    return _TransferAuthorizationAdapter(decision)


def build_transfer_pipeline(
    *,
    cache: RemoteStructureCache,
    db_path: str | Path | None = None,
    publications: TransferPublicationStore | None = None,
    source: Any | None = None,
    authorization: Any | None = None,
    adoption: Any | None = None,
) -> TransferPipeline:
    """Compose the verified transfer pipeline from the real cache mechanism.

    Args:
        cache: The shared :class:`RemoteStructureCache` (traversal guard / fence /
            generation / atomic publish / ``push_paths``).
        db_path: SQLite path for the publication table; defaults to the cache's
            run root ``acp_jobs.db`` so it survives the same backup/restore.
        publications: Pre-built publication store (created when omitted).
        source: Optional source port override (defaults to the real fetcher).
        authorization: Optional authorization port override (defaults allow-all).
        adoption: Optional historical expected-value resolver.
    """
    store = publications or TransferPublicationStore(
        Path(db_path) if db_path is not None else cache._run_root / "acp_jobs.db"
    )
    return TransferPipeline(
        cache=_TransferCacheAdapter(cache),
        source=source or _TransferSourceAdapter(cache),
        authorization=authorization or _TransferAuthorizationAdapter(),
        publications=store,
        adoption=adoption,
    )


def build_transfer_executor(
    pipeline: TransferPipeline,
    request_factory: Callable[[PrepareLease], Any],
) -> TransferPrepareExecutor:
    """Wrap the pipeline as the todo-13 prepare-operation executor."""
    return TransferPrepareExecutor(pipeline, request_factory)
