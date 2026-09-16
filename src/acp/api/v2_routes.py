"""
API v2 Routes
=============

Project-task surface for the v2 storage design
(docs/ACP_Project_Task_Storage_Design_v2.md §12).  v2 "tasks" are the
existing scheduler jobs — the jobs table is the task index.  Mounted under
``/api/v2`` by :func:`acp.api.server.create_app`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from acp.api.v2_schemas import (
    V2FileEntry,
    V2ProjectSummary,
    V2TaskBatchItem,
    V2TaskBatchRequest,
    V2TaskBatchResponse,
    V2TaskDetail,
    V2TaskPatchRequest,
    V2TaskRowModel,
    V2TaskSummary,
    V2TaskViewFacetsModel,
    V2TaskViewGroupModel,
    V2TaskViewResponse,
    V2TreeResponse,
)
from acp.scheduler.capabilities import NoCapableNodeError
from acp.scheduler.files import resolve_safe
from acp.scheduler.jobs import SUPPORTED_WORKFLOWS, JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.nodes import (
    ExecutionTargetError,
    validate_execution_request,
    validate_submission_target,
)
from acp.storage.backend import (
    LocalStorageBackend,
    StorageError,
    StorageNotFoundError,
    TaskStorageBackend,
)
from acp.storage.layout import TaskLayout
from acp.storage.manifest import ResultManifest

logger = logging.getLogger(__name__)

router = APIRouter()

#: Upper bound used when counting tasks per project (JobStore has no
#: per-project COUNT helper; len(list_by_project(...)) is the fallback).
_COUNT_LIMIT = 1_000_000


def _manager(request: Request) -> JobManager:
    manager = getattr(request.app.state, "job_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Job scheduler not initialized")
    return manager


def _task_summary(record: JobRecord) -> V2TaskSummary:
    spec = record.spec
    return V2TaskSummary(
        task_id=record.id,
        # The physical task directory is the canonical user-facing name.
        # Using it here also normalises historical records whose old
        # ``spec.name`` still contains the pre-v2 random batch suffix.
        display_name=Path(record.work_dir).name if record.work_dir else spec.task_dir_name(),
        molecule_name=spec.molecule_name,
        task_name=spec.task_name,
        remark=spec.remark,
        workflow=spec.workflow,
        task_dir_name=Path(record.work_dir).name,
        status=record.status.value,
        project_id=record.project_id or spec.project_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _task_detail(record: JobRecord) -> V2TaskDetail:
    spec = record.spec
    result = record.result if isinstance(record.result, dict) else {}
    return V2TaskDetail(
        **_task_summary(record).model_dump(),
        node_id=result.get("node") or spec.target_node,
        work_dir=record.work_dir,
        input_hash=record.input_hash or spec.input_hash,
        current_stage=record.current_stage,
        error=record.error,
    )


def _task_or_404(request: Request, task_id: str) -> JobRecord:
    record = _manager(request).store.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return record


_ACTIVE_STATUSES_FOR_ENRICHMENT: frozenset[str] = frozenset({
    s.value for s in JobStatus if s.is_active
})
_ENRICHMENT_CAP = 200


def _enrich_active_rows(
    request: Request,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Enrich active-row dicts with live stage/progress fields from state.json."""
    from acp.api.v1_routes import _enrich_job_snapshot, _record_to_v1_model

    manager = _manager(request)
    enriched_count = 0
    for row in rows:
        if row.get("status") not in _ACTIVE_STATUSES_FOR_ENRICHMENT:
            continue
        if enriched_count >= _ENRICHMENT_CAP:
            logger.warning(
                "task-view enrichment cap (%d) reached; skipping remaining active rows",
                _ENRICHMENT_CAP,
            )
            break
        record = manager.store.get(row["id"])
        if record is None:
            continue
        job_model = _record_to_v1_model(record)
        enriched = _enrich_job_snapshot(record, job_model, include_event=False)
        for field in (
            "stage_index", "stage_total", "stage_detail",
            "progress_state", "display_method",
        ):
            val = getattr(enriched, field, None)
            if val is not None:
                row[field] = val
        live = getattr(enriched, "live_status", None)
        if live is not None:
            if hasattr(live, "model_dump"):
                row["live_status"] = live.model_dump()
            elif isinstance(live, dict):
                row["live_status"] = live
        enriched_count += 1
    return rows


def _storage_for(record: JobRecord) -> TaskStorageBackend:
    """Storage backend serving a task's files (§9: server stores no copies).

    Local tasks read the task dir directly; remote tasks read the local
    mirror populated by the on-demand fetcher (SFTP wiring lands with the
    node-agent phase). All v2 file endpoints go through this single swap
    point so the backend can change without touching routes.
    """
    return LocalStorageBackend(Path(record.work_dir))


@router.get("/projects", response_model=list[V2ProjectSummary])
def list_projects(request: Request) -> list[V2ProjectSummary]:
    """List projects with their task counts (§12)."""
    manager = _manager(request)
    summaries: list[V2ProjectSummary] = []
    for project in manager.projects.list_projects():
        project_id = str(project.get("project_id", ""))
        n_tasks = len(manager.store.list_by_project(project_id, limit=_COUNT_LIMIT))
        summaries.append(
            V2ProjectSummary(
                project_id=project_id,
                name=str(project.get("name", "")),
                description=str(project.get("description", "")),
                tags=[str(tag) for tag in project.get("tags", [])],
                n_tasks=n_tasks,
                created_at=str(project.get("created_at", "")),
                updated_at=str(project.get("updated_at", "")),
            )
        )
    return summaries


@router.get("/projects/{project_id}/tasks", response_model=list[V2TaskSummary])
def list_project_tasks(
    project_id: str,
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[V2TaskSummary]:
    """List a project's tasks (= jobs), newest first (§12)."""
    manager = _manager(request)
    if manager.projects.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    records = manager.store.list_by_project(project_id, limit=limit)
    return [_task_summary(record) for record in records]


@router.get("/task-view", response_model=V2TaskViewResponse)
def get_task_view(
    request: Request,
    project_id: str | None = Query(default=None),
    group_by: str = Query(
        default="molecule",
        pattern=r"^(molecule|remark|workflow|batch|tag|none)$",
    ),
    sort: str = Query(
        default="created_desc",
        pattern=r"^(created_desc|created_asc|completed_desc|activity_desc|name_asc|name_desc)$",
    ),
    status: str | None = Query(default=None),
    workflow: str | None = Query(default=None),
    molecule: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    batch: str | None = Query(default=None),
    remark: str | None = Query(default=None),
    q: str | None = Query(default=None),
    archived: str = Query(default="exclude", pattern=r"^(exclude|include|only)$"),
    running_first: bool = False,
    group_limit: int = Query(default=200, ge=1, le=1000),
) -> V2TaskViewResponse:
    """Grouped task view with filters, facets, and counts (T3)."""
    from acp.scheduler.task_views import (
        ArchivedFilter,
        GroupBy,
        TaskSort,
        TaskViewQuery,
        query_project_tasks,
    )

    manager = _manager(request)
    if project_id is not None and manager.projects.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    def _parse_csv(val: str | None) -> tuple[str, ...]:
        if not val:
            return ()
        return tuple(v.strip() for v in val.split(",") if v.strip())

    q = TaskViewQuery(
        project_id=project_id,
        group_by=GroupBy(group_by),
        sort=TaskSort(sort),
        statuses=_parse_csv(status),
        workflows=_parse_csv(workflow),
        molecule_keys=_parse_csv(molecule),
        tags=_parse_csv(tag),
        batch_ids=_parse_csv(batch),
        remarks=_parse_csv(remark),
        search=q or "",
        archived=ArchivedFilter(archived),
        running_first=running_first,
        group_limit=group_limit,
    )
    result = query_project_tasks(manager.tasks, q)

    # Enrich active rows with live stage/progress fields
    for group in result.get("groups", []):
        jobs = group.get("jobs", [])
        if jobs:
            _enrich_active_rows(request, jobs)

    facets_raw = result.get("facets", {})
    facets = V2TaskViewFacetsModel(
        statuses=facets_raw.get("statuses", {}),
        workflows=facets_raw.get("workflows", []),
        molecules=facets_raw.get("molecules", []),
        tags=facets_raw.get("tags", []),
        batches=facets_raw.get("batches", []),
    )

    groups = [
        V2TaskViewGroupModel(
            key=g["key"],
            display_name=g.get("display_name", g["key"]),
            unassigned=g.get("unassigned", False),
            retired=g.get("retired", False),
            count=g["count"],
            truncated=g.get("truncated", False),
            min_created_at=g.get("min_created_at"),
            jobs=[V2TaskRowModel(**j) for j in g.get("jobs", [])],
        )
        for g in result.get("groups", [])
    ]

    return V2TaskViewResponse(
        groups=groups,
        facets=facets,
        total=result.get("total", 0),
        truncated=result.get("truncated", False),
        counts=result.get("counts", {}),
        query=result.get("query", {}),
    )


@router.get("/tasks/{task_id}", response_model=V2TaskDetail)
def get_task(task_id: str, request: Request) -> V2TaskDetail:
    """Fetch one task's detail projection (§12)."""
    return _task_detail(_task_or_404(request, task_id))


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, body: V2TaskPatchRequest, request: Request) -> dict[str, Any]:
    """Update user-editable display fields on a task row (T3).

    Only touches the tasks index — never modifies jobs/spec_json/work_dir.
    """
    manager = _manager(request)
    existing = manager.tasks.get(task_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    if body.molecule_name is not None and len(body.molecule_name) > 200:
        raise HTTPException(status_code=422, detail="molecule_name must be ≤ 200 characters")
    if body.task_name is not None and len(body.task_name) > 200:
        raise HTTPException(status_code=422, detail="task_name must be ≤ 200 characters")
    if body.remark is not None and len(body.remark) > 200:
        raise HTTPException(status_code=422, detail="remark must be ≤ 200 characters")

    if body.tags is not None:
        if len(body.tags) > 20:
            raise HTTPException(status_code=422, detail="tags must contain at most 20 items")
        cleaned_tags: list[str] = []
        for raw_tag in body.tags:
            tag = raw_tag.strip()
            if not tag:
                raise HTTPException(status_code=422, detail="tags must not contain empty strings")
            if len(tag) > 32:
                raise HTTPException(status_code=422, detail="each tag must be ≤ 32 characters")
            cleaned_tags.append(tag)
        tags_to_write = cleaned_tags
    else:
        tags_to_write = None

    manager.tasks.update_display_fields(
        task_id,
        molecule_name=body.molecule_name,
        task_name=body.task_name,
        remark=body.remark,
        tags=tags_to_write,
    )

    updated = manager.tasks.get(task_id)
    assert updated is not None
    return updated


@router.get("/tasks/{task_id}/tree", response_model=V2TreeResponse)
def get_task_tree(
    task_id: str,
    request: Request,
    area: str = Query(default="result", pattern="^(result|work)$"),
) -> V2TreeResponse:
    """One-level listing of the task's ``RESULT/`` or ``WORK/`` area (§12).

    A missing area base yields an empty entry list, not an error.
    """
    record = _task_or_404(request, task_id)
    area_name = TaskLayout.RESULT_DIR_NAME if area == "result" else TaskLayout.WORK_DIR_NAME
    base = Path(record.work_dir) / area_name
    storage = _storage_for(record)
    try:
        listing = storage.list_dir(area_name)
    except StorageNotFoundError:
        listing = []
    except StorageError as exc:
        raise HTTPException(status_code=500, detail=f"storage error: {exc}") from exc
    entries = [
        V2FileEntry(path=e.name, size=e.size, modified=e.mtime, is_dir=e.is_dir) for e in listing
    ]
    return V2TreeResponse(task_id=record.id, area=area, base=str(base), entries=entries)


@router.get("/tasks/{task_id}/files/{file_path:path}")
def download_task_file(task_id: str, file_path: str, request: Request) -> FileResponse:
    """Download a file from the task directory (traversal-guarded, §12)."""
    work_dir = _manager(request).work_dir_of(task_id)
    if work_dir is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    resolved = resolve_safe(work_dir, file_path)
    if resolved is None:
        raise HTTPException(status_code=404, detail="File not found or outside work directory")
    return FileResponse(str(resolved), filename=resolved.name)


@router.get("/tasks/{task_id}/results")
def get_task_results(task_id: str, request: Request) -> dict[str, Any]:
    """Return the task's ``RESULT/result_manifest.json`` verbatim (§8 shape)."""
    record = _task_or_404(request, task_id)
    result_dir = Path(record.work_dir) / TaskLayout.RESULT_DIR_NAME
    try:
        manifest = ResultManifest.read(result_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="no result manifest") from exc
    return {**manifest.to_dict(), "task_id": record.id}


def _manifest_product_file(
    record: JobRecord,
    product_id: str,
    kinds: frozenset[str],
) -> Path:
    """Resolve ``RESULT/<product.path>`` for a manifest product of *kinds*."""
    result_dir = Path(record.work_dir) / TaskLayout.RESULT_DIR_NAME
    try:
        manifest = ResultManifest.read(result_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="no result manifest") from exc
    for product in manifest.products:
        if product.id == product_id and product.kind.value in kinds:
            resolved = resolve_safe(
                Path(record.work_dir),
                f"{TaskLayout.RESULT_DIR_NAME}/{product.path}",
            )
            if resolved is None:
                raise HTTPException(
                    status_code=404,
                    detail="File not found or outside work directory",
                )
            return resolved
    raise HTTPException(
        status_code=404,
        detail=f"Product not found: {product_id}",
    )


@router.get("/tasks/{task_id}/structures/{structure_id}")
def download_task_structure(structure_id: str, task_id: str, request: Request) -> FileResponse:
    """Serve a ``kind == "structure"`` product file from ``RESULT/`` (§12)."""
    record = _task_or_404(request, task_id)
    resolved = _manifest_product_file(record, structure_id, frozenset({"structure"}))
    return FileResponse(str(resolved), filename=resolved.name)


@router.get("/tasks/{task_id}/frequencies/{frequency_id}")
def download_task_frequencies(frequency_id: str, task_id: str, request: Request) -> FileResponse:
    """Serve a frequency product's raw file from ``RESULT/`` (§12).

    Until the Phase 5 parsers land this serves the raw product file for
    kinds ``frequency_modes`` and ``file``.
    """
    record = _task_or_404(request, task_id)
    resolved = _manifest_product_file(record, frequency_id, frozenset({"frequency_modes", "file"}))
    return FileResponse(str(resolved), filename=resolved.name)


@router.post("/tasks/batch", response_model=V2TaskBatchResponse, status_code=201)
def create_task_batch(req: V2TaskBatchRequest, request: Request) -> V2TaskBatchResponse:
    """Create one independent task per array element (§12 batch submission).

    Per-item failures are collected into ``failed`` instead of aborting
    the batch.  Each request gets a shared ``batch_id`` injected into
    item resources (when absent) so the task view can group them.
    """
    manager = _manager(request)
    req_batch_id = "batch_" + uuid4().hex[:12]
    created: list[V2TaskSummary] = []
    failed: list[dict[str, Any]] = []
    for item in req.tasks:
        outcome = _submit_batch_item(manager, item, req.project_id, req_batch_id)
        if isinstance(outcome, V2TaskSummary):
            created.append(outcome)
        else:
            failed.append(
                {
                    "molecule_name": item.molecule_name,
                    "task_name": item.task_name,
                    "error": outcome,
                }
            )
    return V2TaskBatchResponse(created=created, failed=failed)


def _execution_target_error_message(exc: Exception) -> str:
    """Serialize a target-validation error into a per-item ``failed`` string.

    The batch contract is per-item (never an HTTP 400/500 for one bad
    element), so the structured v1 400 body (``_target_validation_detail``)
    is flattened into the string form ``failed[]`` entries carry: the stable
    machine-readable ``code`` first, then the English reason and any
    ``missing_software``/``missing_tags`` payload.
    """
    code = getattr(exc, "code", None) or "execution_target_error"
    parts = [f"{code}: {exc}"]
    for field in ("missing_software", "missing_tags"):
        values = sorted(getattr(exc, field, ()) or ())
        if values:
            parts.append(f"{field}={values}")
    return "; ".join(parts)


def _submit_batch_item(
    manager: JobManager,
    item: V2TaskBatchItem,
    request_project_id: str | None,
    req_batch_id: str | None = None,
) -> V2TaskSummary | str:
    """Submit one batch item; returns the summary or an error message.

    When *req_batch_id* is given and the item's resources lack a
    ``batch_id``, it is injected so the task view can group batch items.
    """
    if item.workflow not in SUPPORTED_WORKFLOWS:
        return f"Unsupported workflow '{item.workflow}'. Supported: {list(SUPPORTED_WORKFLOWS)}"
    resources = dict(item.resources) if item.resources else {}
    if req_batch_id and "batch_id" not in resources:
        resources["batch_id"] = req_batch_id
    spec = JobSpec(
        workflow=item.workflow,
        name=item.name or f"{item.molecule_name}_{item.task_name}",
        input=item.input,
        method=item.method,
        resources=resources,
        project_id=item.project_id or request_project_id,
        molecule_name=item.molecule_name,
        task_name=item.task_name,
        remark=item.remark,
        execution_mode=item.execution_mode,
        target_node=item.target_node,
        node_tags=item.node_tags,
    )
    try:
        validate_execution_request(spec)
    except ExecutionTargetError as exc:
        logger.warning("batch item %s/%s rejected: %s", item.molecule_name, item.task_name, exc)
        return _execution_target_error_message(exc)
    try:
        validate_submission_target(spec, registry=manager.registry)
    except (ExecutionTargetError, NoCapableNodeError) as exc:
        logger.warning("batch item %s/%s rejected: %s", item.molecule_name, item.task_name, exc)
        return _execution_target_error_message(exc)
    try:
        record = manager.submit(spec)
    except Exception as exc:  # noqa: BLE001 — one bad item must not abort the batch
        logger.warning("batch item %s/%s failed: %s", item.molecule_name, item.task_name, exc)
        return str(exc)
    return _task_summary(record)
