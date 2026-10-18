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
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from acp.results.frame_candidate_store import (
    FRAME_CANDIDATES_SCHEMA,
    load_authority,
    write_authority,
)
from acp.results.remote_structure_cache import RemoteStructureCache
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

logger = logging.getLogger(__name__)

__all__ = ["build_data_resource_port"]

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
