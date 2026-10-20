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
from dataclasses import dataclass
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
from acp_services.data.archive import (
    ArchiveManifest,
    ArchivePackageClass,
    ArchiveSourcePort,
    ExecutionState,
    LocalArchiveSource,
    SnapshotFence,
    build_archive_manifest,
)
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
    ResourceKind,
    SourceUpsert,
    TransferDirection,
    TransferDownload,
    TransferReport,
    TransferResult,
    TransferUpload,
)
from acp_services.data.import_ import (
    DEFAULT_IMPORT_QUOTA,
    ArchiveImportService,
    ImportQuota,
    ImportRecordStore,
    LocalImportBundle,
)
from acp_services.data.operations import (
    PrepareExecutionResult,
    PrepareLease,
    PrepareRequest,
    default_resource_key,
)
from acp_services.data.read_root import (
    DEFAULT_MAX_READ_BYTES,
    AvailabilityRead,
    CacheIndexFacts,
    OperationFacts,
    ReadOnlyResourceReader,
    ReadRoot,
    ReadRootResolver,
)
from acp_services.data.resource_read import CacheOnlyResourceReader
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
    RejectionCode,
    ResourceClass,
    SourceFailureKind,
    SourceRead,
    TransferPipeline,
    TransferPrepareExecutor,
    TransferPublicationStore,
    TransferRequest,
    TransferSourceError,
)

logger = logging.getLogger(__name__)

# Boundary adapters are best-effort, but a programming error must surface
# rather than be downgraded to "transport failed, retry later".
_PROGRAMMING_ERRORS: tuple[type[BaseException], ...] = (TypeError, AttributeError, NameError)

__all__ = [
    "build_data_resource_port",
    "build_read_only_reader",
    "build_cache_only_resource_reader",
    "build_storage_allocation_port",
    "build_prepare_executor",
    "build_local_prepare_identity_provider",
    "PrepareIdentity",
    "build_transfer_cache_port",
    "build_transfer_source_port",
    "build_transfer_authorization_port",
    "build_transfer_pipeline",
    "build_transfer_executor",
    "build_archive_source",
    "build_task_archive_manifest",
    "build_archive_import_service",
    "build_local_import_bundle",
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


@dataclass(frozen=True)
class PrepareIdentity:
    """The authoritative current facts a prepare lease must still match.

    Supplied by an explicit provider (production: the scheduler manager's
    current record; local-single: :func:`build_local_prepare_identity_provider`).
    There is deliberately no "``None`` means allow" default: an executor built
    without a provider refuses to assemble.
    """

    job_id: str
    attempt: int
    workflow: str
    resource_digest: str
    resource_generation: int = 0
    binding_id: str = ""
    authorization_version: int = 0
    permission_scope: str = "local-single"
    authorized: bool = True


def _lease_workflow(lease: PrepareLease) -> str:
    """Return the workflow frozen into the lease payload (never re-read)."""
    try:
        return str(lease.payload.get("workflow") or "")
    except AttributeError:  # pragma: no cover - payload is always a mapping
        return ""


def _identity_mismatch(lease: PrepareLease, expected: PrepareIdentity | None) -> str | None:
    """Return a stable refusal token when *lease* is not the current identity."""
    if expected is None:
        return "identity_unavailable"
    if not expected.authorized:
        return "authorization_revoked"
    if str(expected.job_id) != str(lease.job_id):
        return "identity_mismatch:job_id"
    if int(expected.attempt) != int(lease.attempt):
        return "stale_attempt"
    lease_workflow = _lease_workflow(lease)
    if lease_workflow and expected.workflow and lease_workflow != expected.workflow:
        return "identity_mismatch:workflow"
    if str(expected.resource_digest) != str(lease.resource_digest):
        return "identity_mismatch:content"
    if int(expected.resource_generation) != int(lease.resource_generation):
        return "identity_mismatch:generation"
    if str(expected.binding_id) != str(lease.binding_id):
        return "identity_mismatch:binding"
    if int(expected.authorization_version) != int(lease.authorization_version):
        return "identity_mismatch:authorization"
    if str(expected.permission_scope) != str(lease.permission_scope):
        return "identity_mismatch:scope"
    return None


class _LeaseIdentityGuard:
    """Re-read the authoritative identity for one lease and validate it.

    The provider is consulted at every check point (pre-download, download
    re-authorization, publish re-authorization and the cache-locked publish
    boundary), so a rerun, rebind, revocation or purge observed at any of them
    refuses the lease.  Resource generation (identity fact) and cache purge
    generation (fence fact) stay separate: the former is compared to the
    lease, the latter is the cache's own publish fence.
    """

    def __init__(
        self,
        lease: PrepareLease,
        provider: Callable[[PrepareLease], PrepareIdentity | None],
        cache: RemoteStructureCache,
    ) -> None:
        self._lease = lease
        self._provider = provider
        self._cache = cache
        self.last_reason = ""

    def check(self) -> str | None:
        try:
            expected = self._provider(self._lease)
        except Exception:
            logger.warning(
                "prepare identity provider failed for %s", self._lease.job_id, exc_info=True
            )
            expected = None
        reason = _identity_mismatch(self._lease, expected)
        if reason is None:
            stored_attempt, _generation = self._cache._read_fence(self._lease.job_id)
            if stored_attempt is not None and int(stored_attempt) > int(self._lease.attempt):
                reason = "stale_attempt:cache_fence"
        self.last_reason = reason or ""
        return reason


class _LeaseAuthorizationPort:
    """Transfer authorization port backed by the live lease identity guard."""

    def __init__(self, guard: _LeaseIdentityGuard) -> None:
        self._guard = guard

    def authorize(
        self,
        *,
        job_id: str,
        attempt: int,
        relative_path: str,
        action: str,
        authorization_version: int,
    ) -> bool:
        lease = self._guard._lease  # noqa: SLF001 - same module, one owner
        if str(job_id) != str(lease.job_id) or int(attempt) != int(lease.attempt):
            return False
        if int(authorization_version) != int(lease.authorization_version):
            return False
        return self._guard.check() is None


def _catalog_fetch_paths(workflow: str) -> tuple[str, ...]:
    """Return the bounded set of files a viewer catalog needs for *workflow*."""
    from acp.results.remote_structure_cache import _CATALOG_FETCH_PATHS

    paths = _CATALOG_FETCH_PATHS.get(workflow, _CATALOG_FETCH_PATHS["legacy"])
    return tuple(
        dict.fromkeys((*paths, "RESULT/result_manifest.json", "RESULT/frame_candidates.json"))
    )


def _catalog_ready_paths(workflow: str) -> tuple[str, ...]:
    from acp.results.remote_structure_cache import _CATALOG_READY_PATHS

    return tuple(_CATALOG_READY_PATHS.get(workflow, _CATALOG_READY_PATHS["legacy"]))


def _catalog_ready_for_attempt(
    cache: RemoteStructureCache,
    root: Path,
    job_id: str,
    workflow: str,
    attempt: int,
) -> bool:
    """True only when the catalog body provably belongs to *attempt*.

    A present required file whose attempt sidecar names another attempt (or,
    for a legacy sidecar-less entry, when the job fence contradicts the read)
    can never satisfy this attempt.  This is the cache-hit/publish-commit
    identity gate; it never returns "ready" for bytes of a stale attempt.
    """
    if not cache.catalog_ready(root, workflow):
        return False
    stored_attempt, _generation = cache._read_fence(job_id)
    if stored_attempt is not None and int(stored_attempt) > int(attempt):
        return False
    for rel_path in _catalog_ready_paths(workflow):
        target = Path(root) / rel_path
        if not target.is_file():
            continue
        present, sidecar_attempt = cache._read_attempt_sidecar(target)
        if present:
            if sidecar_attempt != int(attempt):
                return False
        elif stored_attempt is not None and int(stored_attempt) != int(attempt):
            return False
    return True


class _PrepareExecutorAdapter:
    """Execute a prepare operation from the *whole* lease identity.

    B02-3 (plan todo 13) + D06 (plan todo 15).  The Services worker owns the
    persistent operation state machine; this adapter (legacy side) is the
    injected mechanism.  The lease is passed through untouched — it is never
    reduced to a job id and "the latest job" is never re-read as authority:

    * an explicit :class:`PrepareIdentity` provider is mandatory (no
      ``None``-means-allow); a missing provider refuses assembly/execution;
    * the identity is validated before any cache hit or download and again
      under the cache's generation lock at the publish/commit boundary;
    * every catalog file is published through the unified verified transfer
      pipeline, whose cache adapter re-checks the identity after the cache's
      own fence/generation checks and immediately before ``os.replace``;
    * stale/foreign leases return ``prepared=False`` with a typed detail and
      ``retryable=False`` — never ``already_ready``.

    The executor never decides state: it only reports whether the catalog is
    prepared and whether a retry could help.
    """

    def __init__(
        self,
        *,
        job_lookup: Callable[[str], Any],
        cache_provider: Callable[[], RemoteStructureCache],
        identity_provider: Callable[[PrepareLease], PrepareIdentity | None],
        is_remote: Callable[[Any], bool] | None = None,
        before_publish: Callable[[], None] | None = None,
    ) -> None:
        if not callable(identity_provider):
            raise ValueError(
                "build_prepare_executor requires an explicit identity_provider "
                "(no None-means-allow fallback)"
            )
        self._job_lookup = job_lookup
        self._cache_provider = cache_provider
        self._identity_provider = identity_provider
        self._is_remote = is_remote
        self._before_publish = before_publish
        self._publications: TransferPublicationStore | None = None

    # -- compatibility entry point (legacy direct prefetch) -------------

    def prefetch(self, job_id: str) -> PrepareExecutionResult:
        """Legacy direct prefetch: build the lease the operation would carry.

        The manager still calls this on a terminal transition as a fast path;
        it validates the same identity as the durable worker by synthesising
        the lease (with the current attempt) rather than bypassing the guard.
        """
        record = self._job_lookup(job_id)
        if record is None:
            return PrepareExecutionResult(prepared=False, detail="job_missing", retryable=False)
        workflow = str(getattr(getattr(record, "spec", None), "workflow", "") or "")
        attempt = int(getattr(record, "attempt", 0) or 0)
        lease = PrepareLease(
            operation_id=f"prefetch:{job_id}",
            job_id=job_id,
            attempt=attempt,
            resource_id=f"catalog:{workflow}",
            resource_kind=ResourceKind.CACHE.value,
            resource_digest=f"{job_id}\x1f{attempt}\x1f{workflow}",
            resource_key=default_resource_key(job_id, attempt, f"catalog:{workflow}"),
            permission_scope="local-single",
            owner_generation=0,
            lease_token="",
            lease_expires_at=None,
            authorization_version=0,
            binding_id="",
            resource_generation=0,
            attempt_count=0,
            payload={"workflow": workflow, "job_id": job_id, "attempt": attempt},
        )
        return self.execute(lease)

    # -- PrepareExecutorPort -------------------------------------------

    def execute(self, lease: PrepareLease) -> PrepareExecutionResult:
        """Prepare the catalog for exactly ``lease`` (full identity)."""
        cache = self._cache_provider()
        guard = _LeaseIdentityGuard(lease, self._identity_provider, cache)

        reason = guard.check()
        if reason is not None:
            return self._refuse(reason)

        record = self._job_lookup(lease.job_id)
        if record is None:
            return PrepareExecutionResult(prepared=False, detail="job_missing", retryable=False)
        status = getattr(record, "status", None)
        if status is None or not getattr(status, "is_terminal", False):
            return PrepareExecutionResult(prepared=False, detail="not_terminal", retryable=False)
        if self._is_remote is not None and not self._is_remote(record):
            return PrepareExecutionResult(prepared=False, detail="not_remote", retryable=False)
        if int(getattr(record, "attempt", 0) or 0) != int(lease.attempt):
            return self._refuse("stale_attempt")
        workflow = _lease_workflow(lease) or str(
            getattr(getattr(record, "spec", None), "workflow", "") or ""
        )
        record_workflow = str(getattr(getattr(record, "spec", None), "workflow", "") or "")
        if workflow and record_workflow and workflow != record_workflow:
            return self._refuse("identity_mismatch:workflow")

        # A cache-hit is accepted only for this attempt's own body, and only
        # after a final identity + fence check under the cache generation lock
        # (the same lock the cache/purge/publish paths take).
        cache_root = cache.job_root(lease.job_id)
        work_dir = Path(str(record.work_dir))
        with cache._get_generation_lock(lease.job_id):
            hit_reason = guard.check()
            if hit_reason is not None:
                return self._refuse(hit_reason)
            for root in (work_dir, cache_root):
                if _catalog_ready_for_attempt(
                    cache, root, lease.job_id, workflow, int(lease.attempt)
                ):
                    return PrepareExecutionResult(
                        prepared=True,
                        availability=Availability.READY,
                        detail="already_ready",
                        retryable=False,
                    )

        # Miss: publish every required catalog file through the unified
        # verified transfer path (single publisher: cache fence/generation +
        # atomic os.replace).  Missing optional files are tolerated exactly as
        # before; identity/permission/stale rejections abort immediately.
        pipeline = self._build_pipeline(cache, guard)
        fetched = 0
        for rel_path in _catalog_fetch_paths(workflow):
            result = pipeline.prepare(
                TransferRequest(
                    job_id=lease.job_id,
                    attempt=int(lease.attempt),
                    relative_path=rel_path,
                    resource_class=ResourceClass.MUTABLE,
                    resource_generation=int(lease.resource_generation),
                    authorization_version=int(lease.authorization_version),
                    binding_id=str(lease.binding_id),
                    payload={"workflow": workflow},
                )
            )
            if result.accepted:
                fetched += 1
                continue
            if result.rejection is RejectionCode.SOURCE_NOT_FOUND:
                continue
            return self._pipeline_refusal(result)

        with cache._get_generation_lock(lease.job_id):
            commit_reason = guard.check()
            if commit_reason is not None:
                return self._refuse(commit_reason)
            ready = _catalog_ready_for_attempt(
                cache, cache_root, lease.job_id, workflow, int(lease.attempt)
            )
        if not ready:
            return PrepareExecutionResult(
                prepared=False,
                availability=Availability.PREPARING,
                detail="not_ready",
                retryable=True,
            )
        return PrepareExecutionResult(
            prepared=True,
            availability=Availability.READY,
            detail="fetched" if fetched else "already_ready",
            retryable=False,
        )

    # -- internals ------------------------------------------------------

    def _build_pipeline(
        self, cache: RemoteStructureCache, guard: _LeaseIdentityGuard
    ) -> TransferPipeline:
        if self._publications is None:
            self._publications = TransferPublicationStore(cache._run_root / "acp_jobs.db")
        return TransferPipeline(
            cache=_TransferCacheAdapter(
                cache,
                publish_guard=guard.check,
                before_publish=self._before_publish,
            ),
            source=_TransferSourceAdapter(cache),
            authorization=_LeaseAuthorizationPort(guard),
            publications=self._publications,
        )

    @staticmethod
    def _refuse(reason: str) -> PrepareExecutionResult:
        return PrepareExecutionResult(
            prepared=False,
            availability=Availability.ABSENT,
            detail=reason,
            retryable=False,
        )

    @staticmethod
    def _pipeline_refusal(result: Any) -> PrepareExecutionResult:
        retryable = bool(result.retryable)
        if result.rejection is RejectionCode.LATE_TRANSFER:
            # A purge generation moved: a fresh attempt can legitimately retry.
            # An identity/attempt fence moved: the lease can never become valid.
            retryable = "generation_changed" in str(result.detail)
        return PrepareExecutionResult(
            prepared=False,
            availability=Availability.UNAVAILABLE,
            detail=f"publish_rejected:{result.rejection.value}",
            retryable=retryable,
        )


def build_local_prepare_identity_provider(
    job_lookup: Callable[[str], Any],
) -> Callable[[PrepareLease], PrepareIdentity | None]:
    """Explicit local-single identity/authorization policy.

    The local deployment has no separate resource-generation/authorization
    registry, so the policy states that fact explicitly (generation 0, no
    binding, authorization version 0, always authorized for local-single).
    Only the attempt/workflow/content are re-derived from the authoritative
    current record; a lease from an earlier attempt therefore mismatches.
    """

    def provider(lease: PrepareLease) -> PrepareIdentity | None:
        record = job_lookup(lease.job_id)
        if record is None:
            return None
        workflow = str(getattr(getattr(record, "spec", None), "workflow", "") or "")
        attempt = int(getattr(record, "attempt", 0) or 0)
        if attempt < 1:
            return None
        return PrepareIdentity(
            job_id=str(record.id),
            attempt=attempt,
            workflow=workflow,
            resource_digest=f"{record.id}\x1f{attempt}\x1f{workflow}",
            resource_generation=0,
            binding_id="",
            authorization_version=0,
            permission_scope="local-single",
            authorized=True,
        )

    return provider


def build_prepare_executor(
    *,
    job_lookup: Callable[[str], Any],
    identity_provider: Callable[[PrepareLease], PrepareIdentity | None] | None = None,
    cache_provider: Callable[[], RemoteStructureCache] | None = None,
    cache: RemoteStructureCache | None = None,
    is_remote: Callable[[Any], bool] | None = None,
    before_publish: Callable[[], None] | None = None,
) -> _PrepareExecutorAdapter:
    """Build the injected prepare executor from the real remote cache path.

    Args:
        job_lookup: ``job_id -> JobRecord | None`` (the scheduler store).
        identity_provider: **Required** authoritative identity/authorization
            provider for the current record.  There is no ``None``-means-allow
            fallback: omitting it raises ``ValueError`` (assembly refusal).
        cache_provider: Lazy accessor for the shared ``RemoteStructureCache``
            singleton (the fetcher factory inside it resolves the current
            fetcher, so the adapter stays valid across fetcher swaps).
        cache: Pre-built cache (alternative to ``cache_provider``).
        is_remote: Optional predicate distinguishing remote jobs; when omitted
            every terminal job is eligible.
        before_publish: Optional test/instrumentation barrier invoked inside
            the cache lock after the cache's fence checks and before the final
            identity check + ``os.replace``.

    Returns:
        An object satisfying :class:`acp_services.data.operations.PrepareExecutorPort`.

    Raises:
        ValueError: When no cache source or no identity provider is supplied.
    """
    if identity_provider is None or not callable(identity_provider):
        raise ValueError(
            "build_prepare_executor requires an explicit identity_provider "
            "(no None-means-allow fallback)"
        )
    if cache_provider is None:
        if cache is None:
            raise ValueError("build_prepare_executor requires cache or cache_provider")
        resolved = cache

        def cache_provider() -> RemoteStructureCache:  # type: ignore[misc]
            return resolved

    return _PrepareExecutorAdapter(
        job_lookup=job_lookup,
        cache_provider=cache_provider,
        identity_provider=identity_provider,
        is_remote=is_remote,
        before_publish=before_publish,
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

    def __init__(
        self,
        cache: RemoteStructureCache,
        *,
        publish_guard: Callable[[], str | None] | None = None,
        before_publish: Callable[[], None] | None = None,
    ) -> None:
        self._cache = cache
        self._publish_guard = publish_guard
        self._before_publish = before_publish

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
            # D06: optional barrier representing any concurrent event (rerun /
            # rebind / revoke / evict).  It runs *before* every remaining check
            # so that the authoritative identity check immediately preceding
            # ``os.replace`` observes the event; a moved fence/generation or a
            # changed identity discards the write instead of overwriting the
            # current bytes.
            if self._before_publish is not None:
                self._before_publish()
            if cache._read_generation(job_id) != expected_generation:
                return PublishOutcome(published=False, discarded=True, reason="generation_changed")
            stored_attempt, generation = cache._read_fence(job_id)
            if stored_attempt is not None and int(attempt) < int(stored_attempt):
                return PublishOutcome(published=False, discarded=True, reason="attempt_advanced")
            if self._publish_guard is not None:
                reason = self._publish_guard()
                if reason:
                    return PublishOutcome(published=False, discarded=True, reason=reason)
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
        except _PROGRAMMING_ERRORS:
            raise
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
            # Preserve the cache's read-only nested-layout compatibility: a
            # pre-marker remote job stores results under a discovered prefix.
            # The same fetcher/mechanism is used (no second downloader).
            if byte_range is None:
                nested = self._cache._discover_nested(fetcher, record, job_id, relative_path)
                if nested is not None:
                    data = bytes(nested)
                    return SourceRead(data=data, size_bytes=len(data), revision=0, generation=0)
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
        except _PROGRAMMING_ERRORS:
            raise
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


# --------------------------------------------------------------------------- #
# B02-5 (todo 15) — strictly read-only read-root + availability adapters
# --------------------------------------------------------------------------- #


class _ReadRootAdapter:
    """Resolve a job's readable root from local metadata only (no network).

    Local jobs read their ``work_dir``; remote jobs read the controlled
    ``RemoteStructureCache`` root.  Remote *catalog readiness* is answered from
    the local cache (a stat), never by fetching — that is the whole point of the
    read-only surface: a miss is reported, not repaired.
    """

    def __init__(self, *, job_lookup: Callable[[str], Any], cache: RemoteStructureCache) -> None:
        self._job_lookup = job_lookup
        self._cache = cache

    def resolve(self, job_id: str) -> ReadRoot:
        record = self._job_lookup(job_id)
        if record is None:
            return ReadRoot(job_id=job_id, path="", found=False)
        result = getattr(record, "result", None) or {}
        is_remote = bool(result.get("node") and result.get("remote_dir"))
        workflow = str(getattr(getattr(record, "spec", None), "workflow", "") or "")
        if is_remote:
            root = self._cache.job_root(record.id)
            ready = self._cache.catalog_ready(root, workflow)
            return ReadRoot(
                job_id=job_id,
                path=str(root),
                is_remote=True,
                catalog_ready=ready,
                found=True,
            )
        work_dir = Path(str(record.work_dir))
        return ReadRoot(
            job_id=job_id,
            path=str(work_dir),
            is_remote=False,
            catalog_ready=work_dir.is_dir(),
            found=True,
        )


class _ReadCacheIndexAdapter:
    """Expose the prepared cache index read-only (stat + bounded range read).

    ``entry`` / ``catalog`` are local stat/fence reads (``get_cached`` /
    ``catalog_ready`` / ``_read_fence``); ``read_bytes`` opens the cached file
    and reads at most *limit* bytes at *offset* so a large file is never loaded
    whole into memory.  No method can fetch from a node.
    """

    def __init__(self, cache: RemoteStructureCache) -> None:
        self._cache = cache

    def _generation(self, job_id: str) -> int:
        return int(self._cache._read_fence(job_id)[1])

    def entry(
        self, job_id: str, relative_path: str, *, attempt: int | None = None
    ) -> CacheIndexFacts:
        try:
            path = self._cache.get_cached(job_id, relative_path, attempt=attempt)
        except ValueError:
            return CacheIndexFacts(present=False)
        if path is None:
            return CacheIndexFacts(present=False)
        size: int | None = None
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        return CacheIndexFacts(
            present=True,
            cached_path=str(path),
            total_size=size,
            generation=self._generation(job_id),
        )

    def catalog(self, job_id: str, root: str, workflow: str) -> CacheIndexFacts:
        ready = self._cache.catalog_ready(Path(root), workflow)
        return CacheIndexFacts(present=ready, generation=self._generation(job_id))

    def read_bytes(self, path: str, offset: int, limit: int) -> bytes:
        with open(path, "rb") as handle:  # noqa: PTH123 - bounded binary range read
            handle.seek(int(offset))
            return handle.read(int(limit))


def _expected_catalog_prepare_request(
    record: Any, request: AvailabilityRead
) -> PrepareRequest | None:
    """Build the frozen expected identity for a catalog read from real facts."""
    job_id = str(getattr(record, "id", "") or "")
    if request.relative_path or not job_id:
        return None
    workflow = request.workflow or str(getattr(getattr(record, "spec", None), "workflow", "") or "")
    attempt = int(request.attempt or getattr(record, "attempt", 1) or 1)
    if attempt < 1:
        return None
    return PrepareRequest(
        job_id=job_id,
        attempt=attempt,
        resource_id=request.resource_id,
        resource_kind=ResourceKind.CACHE,
        resource_digest=f"{job_id}\x1f{attempt}\x1f{workflow}",
        permission_scope=request.permission_scope,
        resource_generation=int(request.expected_revision or 0),
        payload={"workflow": workflow, "job_id": job_id, "attempt": attempt},
    )


class _OperationAvailabilityAdapter:
    """Read the committed availability of the current operation (read-only).

    Delegates to the real :class:`PrepareOperationService`
    ``find_by_identity`` + ``availability`` — both pure reads.  When an
    expected-identity provider is supplied, the row is located by that frozen
    identity (never by "latest row"); a logical key with no matching identity
    reports a non-ready mismatch instead of falling through to an old cache
    body.  There is no enqueue/lease/cancel path here, so a read surface built
    over this adapter structurally cannot create an operation.
    """

    def __init__(
        self,
        service: Any | None,
        *,
        expected_provider: Callable[[AvailabilityRead], PrepareRequest | None] | None = None,
    ) -> None:
        self._service = service
        self._expected_provider = expected_provider

    def facts(self, request: AvailabilityRead) -> OperationFacts:
        if self._service is None:
            return OperationFacts(exists=False)
        attempt = int(request.attempt or 0)
        resource_key = default_resource_key(request.job_id, attempt, request.resource_id)
        expected: PrepareRequest | None = None
        if self._expected_provider is not None:
            try:
                expected = self._expected_provider(request)
            except (TypeError, ValueError, AttributeError):
                expected = None
        if expected is not None:
            record = self._service.find_by_identity(expected)
            if record is None:
                logical = self._service.find(
                    job_id=request.job_id,
                    resource_id=request.resource_id,
                    attempt=int(expected.attempt),
                    permission_scope=request.permission_scope,
                    resource_key=expected.key,
                )
                if logical is None:
                    return OperationFacts(exists=False)
                generation = int(expected.resource_generation)
                return OperationFacts(
                    exists=True,
                    availability=Availability.ABSENT,
                    freshness=Freshness.STALE,
                    basis_revision=generation,
                    expected_revision=generation,
                    detail="identity_mismatch",
                )
        else:
            record = self._service.find(
                job_id=request.job_id,
                resource_id=request.resource_id,
                attempt=attempt,
                permission_scope=request.permission_scope,
                resource_key=resource_key,
            )
        if record is None:
            return OperationFacts(exists=False)
        availability = self._service.availability(record.operation_id)
        detail = f"operation:{record.state.value}"
        if expected is not None and record.content_identity != expected.content_identity:
            detail += ":superseded"
        return OperationFacts(
            exists=True,
            availability=availability.availability,
            freshness=availability.freshness,
            basis_revision=availability.basis_revision,
            expected_revision=availability.expected_revision,
            detail=detail,
        )


class _ReadOnlyReaderAdapter(ReadOnlyResourceReader):
    """Marker subclass documenting the legacy-side composition (read-only)."""


def build_read_only_reader(
    *,
    job_lookup: Callable[[str], Any],
    cache: RemoteStructureCache,
    operations: Any | None = None,
    clock: Callable[[], str] | None = None,
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES,
    expected_provider: Callable[[AvailabilityRead], PrepareRequest | None] | None = None,
) -> ReadOnlyResourceReader:
    """Compose the strictly read-only reader from the real local mechanisms.

    Args:
        job_lookup: ``job_id -> JobRecord | None`` (the scheduler store).
        cache: The shared :class:`RemoteStructureCache` (read-only index access).
        operations: Optional :class:`PrepareOperationService` for the operation
            projection; only ``find_by_identity`` + ``availability`` are ever
            called, so a read through this reader cannot enqueue or lease.
        clock: Diagnostic timestamp source (tests pin it).
        max_read_bytes: Hard cap for every bounded range read.
        expected_provider: Optional authoritative expected-identity factory
            (``AvailabilityRead -> PrepareRequest``).  Production attempts to
            carry the full runtime identity (task 15/18) inject it here; the
            default derives the catalog identity from the job record.

    Returns:
        A :class:`ReadOnlyResourceReader` that never fetches over SSH/SFTP,
        never enqueues an operation and never parses or writes a file.
    """
    resolver = ReadRootResolver(_ReadRootAdapter(job_lookup=job_lookup, cache=cache))

    def _expected(request: AvailabilityRead) -> PrepareRequest | None:
        if expected_provider is not None:
            return expected_provider(request)
        record = job_lookup(request.job_id)
        if record is None:
            return None
        return _expected_catalog_prepare_request(record, request)

    return _ReadOnlyReaderAdapter(
        resolver=resolver,
        cache_index=_ReadCacheIndexAdapter(cache),
        operations=_OperationAvailabilityAdapter(operations, expected_provider=_expected),
        clock=clock,
        max_read_bytes=max_read_bytes,
    )


# --------------------------------------------------------------------------- #
# B02-6 (todo 16) — per-resource cache-only read switch composition
# --------------------------------------------------------------------------- #


def build_cache_only_resource_reader(
    *,
    job_lookup: Callable[[str], Any],
    cache: RemoteStructureCache,
    operations: Any | None = None,
    clock: Callable[[], str] | None = None,
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES,
    enabled: frozenset[str] | None = None,
) -> CacheOnlyResourceReader:
    """Compose the per-resource cache-only reader over the read-only surface.

    The returned reader maps the five switch resources (catalog / manifest /
    frame_candidates / frequency_modes / geometry) onto the todo-15 read-only
    surface with a per-resource adapter, and serves them from the prepared
    cache index only.  It never fetches, never enqueues and never writes; a
    cold cache is reported ``not_prepared`` so the caller can use the retained
    legacy GET fallback.

    Args:
        job_lookup: ``job_id -> JobRecord | None`` (the scheduler store).
        cache: The shared :class:`RemoteStructureCache` (read-only index).
        operations: Optional :class:`PrepareOperationService` operation
            projection; only ``find`` + ``availability`` are ever called.
        clock: Diagnostic timestamp source (tests pin it).
        max_read_bytes: Hard cap for every bounded range read.
        enabled: Enabled resource kinds; defaults to every resource.

    Returns:
        A :class:`~acp_services.data.resource_read.CacheOnlyResourceReader`.
    """
    reader = build_read_only_reader(
        job_lookup=job_lookup,
        cache=cache,
        operations=operations,
        clock=clock,
        max_read_bytes=max_read_bytes,
    )
    return CacheOnlyResourceReader(reader, enabled=enabled)


# --------------------------------------------------------------------------- #
# B08-3 (todo 44) — versioned archive export composition hooks
# --------------------------------------------------------------------------- #


def build_archive_source(
    task_root: str | Path,
    *,
    task_id: str = "",
    generation: int = 0,
    revision: int = 0,
    execution_state: ExecutionState | str = ExecutionState.COMMITTED,
    cache: RemoteStructureCache | None = None,
    job_id: str | None = None,
    attempt: int | None = None,
) -> ArchiveSourcePort:
    """Expose a task tree as an :class:`ArchiveSourcePort` for archive export.

    The pure archive builder lives in :mod:`acp_services.data.archive`; this
    legacy-side hook supplies the *pinnable* position.  When a shared
    :class:`RemoteStructureCache` and ``job_id`` are given the generation is the
    cache's committed fence generation and the revision is the attempt, so the
    export is pinned to one inspected storage position rather than to whatever
    the filesystem happens to contain mid-run.

    Args:
        task_root: Local task tree the export reads.
        task_id: Stable identity recorded in the manifest (defaults to ``job_id``).
        generation: Explicit fence generation when no cache is supplied.
        revision: Explicit fence revision when no cache is supplied.
        execution_state: ``committed`` (terminal) or ``in_flight``.
        cache: Optional shared structure cache used to read the committed fence.
        job_id: Job whose cache fence pins the generation.
        attempt: Attempt number (the revision) for the pinned fence.

    Returns:
        A :class:`LocalArchiveSource` satisfying :class:`ArchiveSourcePort`.
    """
    resolved_generation = int(generation)
    resolved_revision = int(revision)
    if cache is not None and job_id:
        _stored_attempt, fence_generation = cache._read_fence(job_id)
        resolved_generation = int(fence_generation)
        resolved_revision = int(attempt) if attempt is not None else resolved_revision
    return LocalArchiveSource(
        task_root,
        task_id=task_id or (job_id or ""),
        generation=resolved_generation,
        revision=resolved_revision,
        execution_state=execution_state,
    )


def build_task_archive_manifest(
    task_root: str | Path,
    *,
    package_class: ArchivePackageClass | str,
    source_kwargs: Mapping[str, Any] | None = None,
    expected_fence: SnapshotFence | None = None,
    max_attempts: int = 3,
    created_at: str | None = None,
) -> ArchiveManifest:
    """Compose `build_archive_source` + `build_archive_manifest` for a task tree.

    Thin, additive convenience for callers (import/CLI surfaces) that hold a
    task root and want the versioned manifest without wiring the source by hand.
    All snapshot/credential/package-class rules remain the pure builder's.
    """
    source = build_archive_source(task_root, **(source_kwargs or {}))
    return build_archive_manifest(
        source,
        package_class=package_class,
        expected_fence=expected_fence,
        max_attempts=max_attempts,
        created_at=created_at,
    )


# --------------------------------------------------------------------------- #
# B08-4 (todo 45) — archive import composition hooks
# --------------------------------------------------------------------------- #


def build_archive_import_service(
    import_root: str | Path,
    *,
    db_path: str | Path | None = None,
    quota: ImportQuota = DEFAULT_IMPORT_QUOTA,
    store: ImportRecordStore | None = None,
    clock: Any | None = None,
) -> ArchiveImportService:
    """Build the quota-limited archive importer from the real staging store.

    The pure import policy lives in :mod:`acp_services.data.import_`; this
    legacy-side hook only wires the persistence authority.  The record store
    defaults to ``<import_root>/acp_jobs.db`` so staging/import rows survive the
    same backup/restore as the rest of the platform.

    Args:
        import_root: Controlled root the published package bytes live under.
        db_path: SQLite path for the staging/import authority.
        quota: Staging limits (defaults to the module default).
        store: Pre-built :class:`ImportRecordStore` (created when omitted).
        clock: Optional injectable wall clock (diagnostic timestamps).

    Returns:
        An :class:`ArchiveImportService` that never executes, never unpickles and
        never adopts external execution authority.
    """
    resolved_root = Path(import_root)
    resolved_db = Path(db_path) if db_path is not None else resolved_root / "acp_jobs.db"
    record_store = store or ImportRecordStore(resolved_db)
    return ArchiveImportService(
        record_store,
        import_root=resolved_root,
        quota=quota,
        clock=clock,
    )


def build_local_import_bundle(package_root: str | Path) -> LocalImportBundle:
    """Wrap a local package directory as the injected import bundle port."""
    return LocalImportBundle(package_root)
