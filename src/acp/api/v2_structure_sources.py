"""
API v2 Structure Sources
========================

Server-side query, metadata editing, and project tag management for
reusable structure sources.  Mounted under ``/api/v2`` by
:func:`acp.api.server.create_app`.

Endpoints follow the contract in ``docs/ACP_Task_Structure_Organization_Plan
§6.2``.  Static routes are registered before dynamic routes to avoid being
shadowed by path parameters.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from acp.scheduler.structure_source_indexer import StructureSourceIndexer
from acp.scheduler.structure_source_store import (
    RevisionConflictError,
    StructureSourceStore,
)
from acp_services.contracts.access import AccessAction

logger = logging.getLogger(__name__)

router = APIRouter()

_cache_lock = threading.Lock()
_singletons: dict[
    str, tuple[StructureSourceStore, StructureSourceIndexer, _SourceOrganizationPort]
] = {}


class _MetadataPatchBody(BaseModel):
    custom_name: str | None = None
    add_tags: list[str] | None = None
    remove_tags: list[str] | None = None
    expected_revision: int | None = None


class _BatchItem(BaseModel):
    source_uid: str
    add_tags: list[str] | None = None
    remove_tags: list[str] | None = None
    custom_name: str | None = None
    expected_revision: int | None = None


class _BatchBody(BaseModel):
    items: list[_BatchItem]


class _TagRenameBody(BaseModel):
    source: str
    target: str


class _TagRemoveBody(BaseModel):
    tag: str


class _CandidateStatusBody(BaseModel):
    status: str
    expected_revision: int
    actor: str = "user"


class _TrashPurgeBody(BaseModel):
    project_id: str | None = None
    source_uids: list[str] | None = None
    actor: str = "user"


class _AssessmentBody(BaseModel):
    conclusion: str
    reason_code: str
    scope: str = "project"
    note: str = ""
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    attachment_refs: list[str] = Field(default_factory=list)
    actor: str = "user"
    version_id: str | None = None


def _manager(request: Request) -> Any:
    manager = getattr(request.app.state, "job_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Job scheduler not initialized")
    return manager


def _require_structure_source_access(
    request: Request,
    *,
    action: str,
    project_id: str | None = None,
    resource_id: str = "",
) -> None:
    """Authorize one structure-source entry before any store/indexer work.

    Running before :func:`_get_stores` guarantees a denial creates no state (the
    singleton store/indexer is never initialized).  The 403 body shape matches
    the existing error contract.
    """
    from acp_services.workspace.entry_authz import EntryClass, default_entry_authorizer

    authorizer = getattr(request.app.state, "entry_authorizer", None) or default_entry_authorizer()
    decision = authorizer.authorize(
        EntryClass.STRUCTURE_SOURCES,
        project_id=project_id,
        action=action,
        resource_id=resource_id or (project_id or ""),
    )
    if not decision.allowed:
        raise HTTPException(status_code=decision.http_status, detail=decision.error_detail())


class _SourceOrganizationPort:
    """Typed entry for structure-source organization writes (todo 84).

    Discovery operations delegate to the todo-11 ``DataResourcePort``
    (``upsert_sources`` / ``list_sources`` / ``source_uid``); the organization
    operations (metadata, tags, candidate status/assessment, trash) delegate to
    the single ``StructureSourceStore`` implementation, so no second writer
    exists.  Every v2 structure-source **write** routes through this object.
    """

    def __init__(self, data_port: Any, source_store: StructureSourceStore) -> None:
        self._data = data_port
        self._store = source_store

    def upsert_sources(self, request: Any) -> int:
        return self._data.upsert_sources(request)

    def list_sources(self, job_id: str) -> Any:
        return self._data.list_sources(job_id)

    def source_uid(self, job_id: str, relative_path: str) -> str:
        return self._data.source_uid(job_id, relative_path)

    def batch_update_metadata(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        return self._store.batch_update_metadata(items)

    def trash_uids(self, project_id: str | None) -> list[str]:
        return self._store.trash_uids(project_id)

    def purge_candidates(
        self,
        source_uids: list[str],
        *,
        project_id: str | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        return self._store.purge_candidates(source_uids, project_id=project_id, actor=actor)

    def set_candidate_status(
        self,
        source_uid: str,
        status: str,
        *,
        reason: str = "",
        scope: str = "project",
        expected_revision: int,
        actor: str = "user",
    ) -> dict[str, Any]:
        return self._store.set_candidate_status(
            source_uid,
            status,
            reason=reason,
            scope=scope,
            expected_revision=expected_revision,
            actor=actor,
        )

    def add_assessment(
        self,
        source_uid: str,
        *,
        conclusion: str,
        reason_code: str,
        scope: str,
        note: str = "",
        evidence: list[dict[str, Any]] | None = None,
        attachment_refs: list[str] | None = None,
        actor: str = "user",
        version_id: str | None = None,
    ) -> dict[str, Any]:
        return self._store.add_assessment(
            source_uid,
            conclusion=conclusion,
            reason_code=reason_code,
            scope=scope,
            note=note,
            evidence=evidence,
            attachment_refs=attachment_refs,
            actor=actor,
            version_id=version_id,
        )

    def set_custom_name(
        self, source_uid: str, custom_name: str | None, expected_revision: int
    ) -> dict[str, Any]:
        return self._store.set_custom_name(source_uid, custom_name, expected_revision)

    def add_tags(self, source_uid: str, tags: list[str], expected_revision: int) -> dict[str, Any]:
        return self._store.add_tags(source_uid, tags, expected_revision)

    def remove_tags(
        self, source_uid: str, tags: list[str], expected_revision: int
    ) -> dict[str, Any]:
        return self._store.remove_tags(source_uid, tags, expected_revision)

    def rename_project_tag(self, project_id: str, source_key: str, target_display: str) -> int:
        return self._store.rename_project_tag(project_id, source_key, target_display)

    def remove_project_tag(self, project_id: str, tag_key: str) -> int:
        return self._store.remove_project_tag(project_id, tag_key)


def _ensure_ports(
    request: Request,
) -> tuple[StructureSourceStore, StructureSourceIndexer, _SourceOrganizationPort]:
    """Lazy-init the singleton store/indexer/port triple keyed by run_root."""
    run_root = getattr(request.app.state, "run_root", "")
    with _cache_lock:
        if run_root in _singletons:
            return _singletons[run_root]
        db_path = getattr(request.app.state, "db_path", "")
        source_store = StructureSourceStore(db_path)
        manager = _manager(request)
        from acp.services_data_bootstrap import build_data_resource_port

        data_port = build_data_resource_port(
            run_root,
            db_path=db_path,
            source_store=source_store,
            cache=getattr(manager, "structure_cache", None),
        )
        indexer = StructureSourceIndexer(
            store=manager.store,
            source_store=source_store,
            run_root=Path(run_root),
            structure_cache=getattr(manager, "structure_cache", None),
            data_port=data_port,
        )
        indexer.ensure_started()
        _singletons[run_root] = (
            source_store,
            indexer,
            _SourceOrganizationPort(data_port, source_store),
        )
        return _singletons[run_root]


def _get_stores(request: Request) -> tuple[StructureSourceStore, StructureSourceIndexer]:
    """Lazy-init singleton pair keyed by run_root."""
    source_store, indexer, _ = _ensure_ports(request)
    return source_store, indexer


def _source_organization_port(request: Request) -> _SourceOrganizationPort:
    """Return the typed structure-source write entry for this request."""
    _, _, port = _ensure_ports(request)
    return port


def _parse_tags_param(tags: str | None) -> list[str] | None:
    if not tags:
        return None
    return [t.strip() for t in tags.split(",") if t.strip()]


# ------------------------------------------------------------------ #
# Static routes (before dynamic)
# ------------------------------------------------------------------ #


@router.get("/structure-sources/facets")
def get_facets(
    request: Request,
    q: str | None = None,
    project_id: str | None = None,
    all_projects: bool = False,
    role: str | None = None,
    tags: str | None = None,
    tag_match: str = "any",
    workflow: str | None = None,
    source_kind: str | None = None,
    source_group: str | None = None,
    remote: bool | None = None,
    availability: str | None = None,
    assessment: str | None = None,
    usage_status: str | None = None,
    include_inactive: bool = False,
) -> dict[str, Any]:
    _require_structure_source_access(
        request,
        action=AccessAction.READ,
        project_id=(project_id if not all_projects else None),
    )
    source_store, indexer = _get_stores(request)
    parsed_tags = _parse_tags_param(tags)
    if source_kind and source_group:
        raise HTTPException(
            status_code=422,
            detail="source_kind and source_group are mutually exclusive",
        )
    try:
        counts = source_store.facet_counts(
            project_id=project_id if not all_projects else None,
            all_projects=all_projects,
            q=q,
            role=role,
            tags=parsed_tags,
            tag_match=tag_match,
            workflow=workflow,
            source_kind=source_kind,
            source_group=source_group,
            remote=remote,
            availability=availability,
            assessment=assessment,
            usage_status=usage_status,
            include_inactive=include_inactive,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    counts["indexing"] = indexer.coverage()
    return counts


@router.post("/structure-sources/batch-metadata")
def batch_metadata(request: Request, body: _BatchBody) -> dict[str, Any]:
    _require_structure_source_access(request, action=AccessAction.WRITE, resource_id="batch")
    port = _source_organization_port(request)
    items = []
    for item in body.items:
        row: dict[str, Any] = {"source_uid": item.source_uid}
        if item.custom_name is not None:
            row["custom_name"] = item.custom_name
        if item.add_tags is not None:
            row["add_tags"] = item.add_tags
        if item.remove_tags is not None:
            row["remove_tags"] = item.remove_tags
        row["expected_revision"] = item.expected_revision or 0
        items.append(row)
    result = port.batch_update_metadata(items)
    succeeded = result.get("succeeded", [])
    failed = [
        {"source_uid": f["source_uid"], "error": f.get("error", "")}
        for f in result.get("failed", [])
    ]
    conflicts = [
        {"source_uid": c["source_uid"], "current": c.get("projection", {})}
        for c in result.get("conflicts", [])
    ]
    return {"succeeded": succeeded, "failed": failed, "conflicts": conflicts}


@router.post("/structure-sources/trash/purge")
def purge_structure_source_trash(request: Request, body: _TrashPurgeBody) -> dict[str, Any]:
    """Purge selected or all project trash entries without deleting source jobs."""
    _require_structure_source_access(
        request, action=AccessAction.WRITE, project_id=body.project_id, resource_id="trash"
    )
    port = _source_organization_port(request)
    source_uids = port.trash_uids(body.project_id) if body.source_uids is None else body.source_uids
    try:
        return port.purge_candidates(
            source_uids,
            project_id=body.project_id,
            actor=body.actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ------------------------------------------------------------------ #
# Dynamic routes
# ------------------------------------------------------------------ #


@router.get("/structure-sources")
def list_structure_sources(
    request: Request,
    q: str | None = None,
    project_id: str | None = None,
    all_projects: bool = False,
    role: str | None = None,
    tags: str | None = None,
    tag_match: str = "any",
    workflow: str | None = None,
    source_kind: str | None = None,
    source_group: str | None = None,
    remote: bool | None = None,
    availability: str | None = None,
    assessment: str | None = None,
    usage_status: str | None = None,
    include_inactive: bool = False,
    sort: str = "produced_desc",
    group_by: str = "none",
    limit: int = Query(default=50, le=100),
    cursor: str | None = None,
    context_workflow: str | None = None,
) -> dict[str, Any]:
    _require_structure_source_access(
        request,
        action=AccessAction.READ,
        project_id=(project_id if not all_projects else None),
    )
    source_store, indexer = _get_stores(request)
    parsed_tags = _parse_tags_param(tags)
    if source_kind and source_group:
        raise HTTPException(
            status_code=422,
            detail="source_kind and source_group are mutually exclusive",
        )
    try:
        result = source_store.query_sources(
            project_id=project_id if not all_projects else None,
            all_projects=all_projects,
            q=q,
            role=role,
            tags=parsed_tags,
            tag_match=tag_match,
            workflow=workflow,
            source_kind=source_kind,
            source_group=source_group,
            remote=remote,
            availability=availability,
            assessment=assessment,
            usage_status=usage_status,
            include_inactive=include_inactive,
            sort=sort,
            group_by=group_by,
            limit=limit,
            cursor=cursor,
            context_workflow=context_workflow,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    result["indexing"] = indexer.coverage()
    return result


@router.get("/structure-sources/{source_uid}")
def get_structure_source(request: Request, source_uid: str) -> dict[str, Any]:
    _require_structure_source_access(request, action=AccessAction.READ, resource_id=source_uid)
    source_store, _ = _get_stores(request)
    projection = source_store.get(source_uid)
    if projection is None:
        raise HTTPException(status_code=404, detail=f"Source not found: {source_uid}")
    return projection


@router.get("/structure-sources/{source_uid}/candidate")
def get_candidate(request: Request, source_uid: str) -> dict[str, Any]:
    _require_structure_source_access(request, action=AccessAction.READ, resource_id=source_uid)
    source_store, _ = _get_stores(request)
    try:
        return source_store.get_candidate_detail(source_uid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/structure-sources/{source_uid}/candidate/status")
def patch_candidate_status(
    request: Request, source_uid: str, body: _CandidateStatusBody
) -> dict[str, Any]:
    _require_structure_source_access(request, action=AccessAction.WRITE, resource_id=source_uid)
    port = _source_organization_port(request)
    try:
        return port.set_candidate_status(
            source_uid,
            body.status,
            expected_revision=body.expected_revision,
            actor=body.actor,
        )
    except RevisionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "revision_conflict", "current": exc.projection},
        ) from exc
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.post("/structure-sources/{source_uid}/candidate/assessments")
def add_candidate_assessment(
    request: Request, source_uid: str, body: _AssessmentBody
) -> dict[str, Any]:
    _require_structure_source_access(request, action=AccessAction.WRITE, resource_id=source_uid)
    port = _source_organization_port(request)
    try:
        return port.add_assessment(
            source_uid,
            conclusion=body.conclusion,
            reason_code=body.reason_code,
            scope=body.scope,
            note=body.note,
            evidence=body.evidence,
            attachment_refs=body.attachment_refs,
            actor=body.actor,
            version_id=body.version_id,
        )
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.patch("/structure-sources/{source_uid}/metadata")
def patch_metadata(request: Request, source_uid: str, body: _MetadataPatchBody) -> dict[str, Any]:
    _require_structure_source_access(request, action=AccessAction.WRITE, resource_id=source_uid)
    source_store, _ = _get_stores(request)
    port = _source_organization_port(request)
    projection = source_store.get(source_uid)
    if projection is None:
        raise HTTPException(status_code=404, detail=f"Source not found: {source_uid}")

    has_any = (
        body.custom_name is not None or body.add_tags is not None or body.remove_tags is not None
    )
    if has_any and body.expected_revision is None:
        raise HTTPException(status_code=422, detail="expected_revision required for mutations")

    expected = body.expected_revision if body.expected_revision is not None else 0
    name_explicit = "custom_name" in body.model_fields_set

    try:
        if name_explicit:
            port.set_custom_name(source_uid, body.custom_name, expected)
            current_projection = source_store.get(source_uid)
            if current_projection:
                expected = current_projection.get("metadata_revision", 0) or 0

        if body.add_tags is not None:
            port.add_tags(source_uid, body.add_tags, expected)
            current_projection = source_store.get(source_uid)
            if current_projection:
                expected = current_projection.get("metadata_revision", 0) or 0

        if body.remove_tags is not None:
            port.remove_tags(source_uid, body.remove_tags, expected)
    except RevisionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "revision_conflict", "current": exc.projection},
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    updated = source_store.get(source_uid)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Source not found: {source_uid}")
    return updated


# ------------------------------------------------------------------ #
# Project tag management
# ------------------------------------------------------------------ #


@router.get("/projects/{project_id}/structure-tags")
def list_project_tags(request: Request, project_id: str) -> dict[str, Any]:
    _require_structure_source_access(request, action=AccessAction.READ, project_id=project_id)
    source_store, _ = _get_stores(request)
    tags = source_store.project_tag_counts(project_id)
    return {"tags": tags}


@router.post("/projects/{project_id}/structure-tags/rename")
def rename_project_tag(request: Request, project_id: str, body: _TagRenameBody) -> dict[str, Any]:
    _require_structure_source_access(request, action=AccessAction.WRITE, project_id=project_id)
    port = _source_organization_port(request)
    affected = port.rename_project_tag(project_id, body.source, body.target)
    return {"affected": affected}


@router.post("/projects/{project_id}/structure-tags/remove")
def remove_project_tag(request: Request, project_id: str, body: _TagRemoveBody) -> dict[str, Any]:
    _require_structure_source_access(request, action=AccessAction.WRITE, project_id=project_id)
    port = _source_organization_port(request)
    affected = port.remove_project_tag(project_id, body.tag)
    return {"affected": affected}
