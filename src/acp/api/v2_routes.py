"""
API v2 Routes
=============

Project-task surface for the v2 storage design
(docs/ACP_Project_Task_Storage_Design_v2.md §12).  v2 "tasks" are the
existing scheduler jobs — the tasks table (TaskIndex) is the server-side
task index while jobs remains the execution record.  Mounted under
``/api/v2`` by :func:`acp.api.server.create_app`.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from acp.api.v2_schemas import (
    V2BatchOpItemResult,
    V2BatchOpsRequest,
    V2BatchOpsResult,
    V2FileEntry,
    V2LineageNode,
    V2LineageResponse,
    V2MoleculeGroupInfo,
    V2MoleculeGroupSuggestion,
    V2MoleculeMergeRequest,
    V2ProjectSummary,
    V2TagDeleteRequest,
    V2TagInfo,
    V2TagMergeRequest,
    V2TagOpResult,
    V2TagRenameRequest,
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


# ── Tag registry endpoints (T7) ───────────────────────────────────────


def _project_or_404(request: Request, project_id: str) -> None:
    manager = _manager(request)
    if manager.projects.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")


def _aggregate_tags_python(tasks_index, project_id: str) -> list[V2TagInfo]:
    """Python-side tag aggregation fallback when JSON1 is unavailable."""
    from collections import Counter

    rows = tasks_index._query(
        "SELECT tags FROM tasks WHERE project_id=?",
        (project_id,),
    )
    counter: Counter[str] = Counter()
    for row in rows:
        raw = row["tags"]
        try:
            tags = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(tags, list):
            for t in tags:
                if isinstance(t, str):
                    counter[t] += 1
    return [V2TagInfo(tag=t, count=c) for t, c in counter.most_common()]


def _aggregate_tags_sql(tasks_index, project_id: str) -> list[V2TagInfo] | None:
    """SQL-side tag aggregation using JSON1 ``json_each``. Returns None if JSON1 unavailable."""
    from acp.scheduler.task_views import _JSON1_OK

    if not _JSON1_OK:
        return None
    try:
        rows = tasks_index._query(
            "SELECT je.value AS tag, COUNT(*) AS cnt "
            "FROM tasks, json_each(tasks.tags) je "
            "WHERE project_id=? "
            "GROUP BY je.value "
            "ORDER BY cnt DESC",
            (project_id,),
        )
        return [V2TagInfo(tag=r["tag"], count=r["cnt"]) for r in rows]
    except Exception:
        return None


@router.get("/projects/{project_id}/tags", response_model=list[V2TagInfo])
def list_project_tags(project_id: str, request: Request) -> list[V2TagInfo]:
    """Aggregate tag counts for all tasks in a project."""
    _project_or_404(request, project_id)
    manager = _manager(request)
    result = _aggregate_tags_sql(manager.tasks, project_id)
    if result is None:
        result = _aggregate_tags_python(manager.tasks, project_id)
    return result


@router.post("/projects/{project_id}/tags/rename", response_model=V2TagOpResult)
def rename_tag(project_id: str, body: V2TagRenameRequest, request: Request) -> V2TagOpResult:
    """Rename a tag across all tasks in a project (exact-match replacement)."""
    _project_or_404(request, project_id)
    source = body.source.strip()
    target = body.target.strip()
    if not source:
        raise HTTPException(status_code=422, detail="source must be non-empty")
    if source == target:
        return V2TagOpResult(updated=0)

    def _rename_transform(tags: list[str]) -> list[str]:
        return [target if t == source else t for t in tags]

    manager = _manager(request)
    count = manager.tasks.rewrite_tags(project_id, _rename_transform)
    return V2TagOpResult(updated=count)


@router.post("/projects/{project_id}/tags/merge", response_model=V2TagOpResult)
def merge_tags(project_id: str, body: V2TagMergeRequest, request: Request) -> V2TagOpResult:
    """Merge multiple source tags into a single target tag."""
    _project_or_404(request, project_id)
    target = body.target.strip()
    sources = [s.strip() for s in body.sources if s.strip()]
    if not sources:
        raise HTTPException(
            status_code=422,
            detail="sources must contain at least one non-empty tag",
        )
    if target in sources:
        raise HTTPException(status_code=422, detail="sources must not contain the target tag")

    source_set = set(sources)

    def _merge_transform(tags: list[str]) -> list[str]:
        result: list[str] = []
        merged = False
        for t in tags:
            if t in source_set:
                if not merged:
                    result.append(target)
                    merged = True
            else:
                result.append(t)
        return result

    manager = _manager(request)
    count = manager.tasks.rewrite_tags(project_id, _merge_transform)
    return V2TagOpResult(updated=count)


@router.post("/projects/{project_id}/tags/delete", response_model=V2TagOpResult)
def delete_tag(project_id: str, body: V2TagDeleteRequest, request: Request) -> V2TagOpResult:
    """Remove a tag from all tasks in a project (never deletes tasks)."""
    _project_or_404(request, project_id)
    tag = body.tag.strip()
    if not tag:
        raise HTTPException(status_code=422, detail="tag must be non-empty")

    def _delete_transform(tags: list[str]) -> list[str]:
        return [t for t in tags if t != tag]

    manager = _manager(request)
    count = manager.tasks.rewrite_tags(project_id, _delete_transform)
    return V2TagOpResult(updated=count)


# ── Batch operations endpoint (T7) ───────────────────────────────────


_BATCH_OP_RE = re.compile(r"^(add_tags|remove_tags|archive|unarchive|set_molecule_name)$")


@router.post("/tasks/batch-ops", response_model=V2BatchOpsResult)
def batch_ops(body: V2BatchOpsRequest, request: Request) -> V2BatchOpsResult:
    """Execute a batch operation across multiple tasks.

    Per-task errors are captured in results (never 500 on one bad id).
    Archive rejects active tasks — the whole request returns 400 with
    offending ids listed.  No partial execution on archive validation failure.
    """
    if not _BATCH_OP_RE.match(body.op):
        raise HTTPException(status_code=422, detail=f"Unsupported op: {body.op}")

    manager = _manager(request)

    if body.op == "archive":
        _validate_archive_targets(manager, body.task_ids)

    results: list[V2BatchOpItemResult] = []
    updated_count = 0
    for task_id in body.task_ids:
        try:
            changed = _execute_batch_op(manager, task_id, body.op, body.payload)
            results.append(V2BatchOpItemResult(task_id=task_id, ok=True))
            if changed:
                updated_count += 1
        except Exception as exc:  # noqa: BLE001 — per-task isolation
            results.append(V2BatchOpItemResult(task_id=task_id, ok=False, error=str(exc)))

    return V2BatchOpsResult(results=results, updated=updated_count)


def _validate_archive_targets(manager: JobManager, task_ids: list[str]) -> None:
    """Reject if any task_id refers to a non-terminal (active) task.

    Raises 400 with offending ids listed.  NO partial execution.
    """
    terminal_statuses = {s.value for s in JobStatus if s.is_terminal}
    offending: list[str] = []
    for tid in task_ids:
        row = manager.tasks.get(tid)
        if row is not None and row.get("status") not in terminal_statuses:
            offending.append(tid)
    if offending:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot archive active tasks: {offending}",
        )


def _execute_batch_op(
    manager: JobManager,
    task_id: str,
    op: str,
    payload: dict[str, Any],
) -> bool:
    """Execute one batch op on a single task. Returns True when state changed.

    Raises for unknown task_id so caller captures it as ok=False.
    """
    existing = manager.tasks.get(task_id)
    if existing is None:
        raise ValueError(f"Task not found: {task_id}")

    if op == "add_tags":
        return _batch_add_tags(manager, task_id, existing, payload)
    if op == "remove_tags":
        return _batch_remove_tags(manager, task_id, existing, payload)
    if op == "archive":
        return _batch_set_archived(manager, task_id, True)
    if op == "unarchive":
        return _batch_set_archived(manager, task_id, False)
    if op == "set_molecule_name":
        return _batch_set_molecule_name(manager, task_id, payload)
    raise ValueError(f"Unsupported op: {op}")


def _validate_batch_tags(payload: dict[str, Any]) -> list[str]:
    """Validate and clean tags from payload. Raises ValueError on invalid input."""
    raw_tags = payload.get("tags", [])
    if not isinstance(raw_tags, list):
        raise ValueError("payload.tags must be a list")
    cleaned: list[str] = []
    for raw in raw_tags:
        tag = str(raw).strip()
        if not tag:
            raise ValueError("tags must not contain empty strings")
        if len(tag) > 32:
            raise ValueError("each tag must be ≤ 32 characters")
        cleaned.append(tag)
    if len(cleaned) > 20:
        raise ValueError("tags must contain at most 20 items")
    return cleaned


def _batch_add_tags(
    manager: JobManager,
    task_id: str,
    existing: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    new_tags = _validate_batch_tags(payload)
    try:
        current = json.loads(existing.get("tags", "[]"))
    except (json.JSONDecodeError, TypeError):
        current = []
    if not isinstance(current, list):
        current = []
    merged = list(current)
    changed = False
    for t in new_tags:
        if t not in merged:
            merged.append(t)
            changed = True
    if changed:
        manager.tasks.update_display_fields(task_id, tags=merged)
    return changed


def _batch_remove_tags(
    manager: JobManager,
    task_id: str,
    existing: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    remove_set = set(_validate_batch_tags(payload))
    try:
        current = json.loads(existing.get("tags", "[]"))
    except (json.JSONDecodeError, TypeError):
        current = []
    if not isinstance(current, list):
        current = []
    new_tags = [t for t in current if t not in remove_set]
    if new_tags != current:
        manager.tasks.update_display_fields(task_id, tags=new_tags)
        return True
    return False


def _batch_set_archived(
    manager: JobManager,
    task_id: str,
    archived: bool,
) -> bool:
    existing = manager.tasks.get(task_id)
    if existing is None:
        return False
    current_archived = bool(existing.get("archived", 0))
    if current_archived == archived:
        return False
    with manager.tasks._lock:
        conn = manager.tasks._connect()
        try:
            from acp.scheduler.tasks import _utc_now_iso

            conn.execute(
                "UPDATE tasks SET archived=?, updated_at=? WHERE task_id=?",
                (1 if archived else 0, _utc_now_iso(), task_id),
            )
            conn.commit()
        finally:
            if manager.tasks._shared_conn is None:
                conn.close()
    return True


def _batch_set_molecule_name(
    manager: JobManager,
    task_id: str,
    payload: dict[str, Any],
) -> bool:
    name = str(payload.get("molecule_name", "")).strip()
    if not name:
        raise ValueError("molecule_name must be non-empty")
    if len(name) > 200:
        raise ValueError("molecule_name must be ≤ 200 characters")
    existing = manager.tasks.get(task_id)
    if existing is None:
        return False
    if existing.get("molecule_name") == name:
        return False
    manager.tasks.update_display_fields(task_id, molecule_name=name)
    return True


# ── Molecule group / alias endpoints (T8) ────────────────────────────────


@router.get("/projects/{project_id}/molecule-groups", response_model=list[V2MoleculeGroupInfo])
def list_molecule_groups(project_id: str, request: Request) -> list[V2MoleculeGroupInfo]:
    """List molecule groups with alias lists and task counts for a project."""
    _project_or_404(request, project_id)
    manager = _manager(request)

    alias_rows = manager.tasks._query(
        "SELECT alias_key, group_key FROM molecule_aliases WHERE project_id=?",
        (project_id,),
    )
    alias_map: dict[str, str] = {r["alias_key"]: r["group_key"] for r in alias_rows}

    group_rows = manager.tasks._query(
        "SELECT group_key, display_name FROM molecule_groups WHERE project_id=?",
        (project_id,),
    )
    group_display: dict[str, str] = {r["group_key"]: r["display_name"] for r in group_rows}

    count_rows = manager.tasks._query(
        "SELECT molecule_key, COUNT(*) AS cnt FROM tasks "
        "WHERE project_id=? GROUP BY molecule_key",
        (project_id,),
    )
    key_counts: dict[str, int] = {r["molecule_key"]: r["cnt"] for r in count_rows}

    all_keys = set(key_counts) | set(group_display)
    result: list[V2MoleculeGroupInfo] = []
    for gk in sorted(all_keys):
        aliases = sorted(ak for ak, g in alias_map.items() if g == gk)
        result.append(
            V2MoleculeGroupInfo(
                group_key=gk,
                display_name=group_display.get(gk, gk),
                aliases=aliases,
                task_count=key_counts.get(gk, 0),
            )
        )
    return result


@router.post("/projects/{project_id}/molecule-groups/merge", response_model=V2TagOpResult)
def merge_molecule_groups(
    project_id: str, body: V2MoleculeMergeRequest, request: Request
) -> V2TagOpResult:
    """Merge alias keys into target_key, rewriting tasks.molecule_key.

    The target_key must be among existing molecule_key values in the project
    OR equal to one of the alias_keys (which becomes the canonical key).
    """
    _project_or_404(request, project_id)
    manager = _manager(request)

    target_key = body.target_key.strip()
    alias_keys = [k.strip() for k in body.alias_keys if k.strip()]
    if not alias_keys:
        raise HTTPException(
            status_code=422,
            detail="alias_keys must contain at least one non-empty key",
        )
    if not target_key:
        raise HTTPException(status_code=422, detail="target_key must be non-empty")

    existing_keys = {
        r["molecule_key"]
        for r in manager.tasks._query(
            "SELECT DISTINCT molecule_key FROM tasks WHERE project_id=?",
            (project_id,),
        )
    }
    if target_key not in existing_keys and target_key not in alias_keys:
        raise HTTPException(
            status_code=400,
            detail=f"target_key '{target_key}' does not match any existing molecule key or alias",
        )

    from acp.scheduler.molecule_groups import apply_group_merge

    updated = apply_group_merge(manager.tasks, project_id, alias_keys, target_key)
    return V2TagOpResult(updated=updated)


@router.get(
    "/projects/{project_id}/molecule-groups/suggestions",
    response_model=list[V2MoleculeGroupSuggestion],
)
def get_molecule_group_suggestions(
    project_id: str, request: Request
) -> list[V2MoleculeGroupSuggestion]:
    """Return merge suggestions based on casefold / separator-normalized similarity.

    Read-only — never modifies data.
    """
    _project_or_404(request, project_id)
    manager = _manager(request)

    rows = [
        dict(r)
        for r in manager.tasks._query(
            "SELECT molecule_key, molecule_name FROM tasks WHERE project_id=?",
            (project_id,),
        )
    ]

    from acp.scheduler.molecule_groups import suggest_group_merges

    raw = suggest_group_merges(rows)
    return [V2MoleculeGroupSuggestion(**s) for s in raw]


@router.delete(
    "/projects/{project_id}/molecule-groups/alias/{alias_key}",
    response_model=V2TagOpResult,
)
def delete_molecule_alias(
    project_id: str, alias_key: str, request: Request
) -> V2TagOpResult:
    """Remove an alias mapping.  Affected tasks' molecule_key falls back
    to their own molecule_group_key(molecule_name).
    """
    _project_or_404(request, project_id)
    manager = _manager(request)

    rows = manager.tasks._query(
        "SELECT group_key FROM molecule_aliases WHERE project_id=? AND alias_key=?",
        (project_id, alias_key),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Alias not found: {alias_key}")

    manager.tasks._run(
        "DELETE FROM molecule_aliases WHERE project_id=? AND alias_key=?",
        (project_id, alias_key),
    )

    from acp.scheduler.naming import molecule_group_key
    from acp.scheduler.tasks import _utc_now_iso

    affected_rows = manager.tasks._query(
        "SELECT task_id, molecule_name FROM tasks WHERE project_id=?",
        (project_id,),
    )
    updated = 0
    for row in affected_rows:
        computed_key = molecule_group_key(row["molecule_name"])
        if computed_key == alias_key:
            current = manager.tasks.get(row["task_id"])
            if current and current.get("molecule_key") != computed_key:
                manager.tasks._run(
                    "UPDATE tasks SET molecule_key=?, updated_at=? WHERE task_id=?",
                    (computed_key, _utc_now_iso(), row["task_id"]),
                )
                updated += 1

    return V2TagOpResult(updated=updated)


# ── Task lineage endpoint (T12) ────────────────────────────────────────

_MAX_LINEAGE_DEPTH = 10


def _extract_upstream_refs(spec_input: dict[str, Any]) -> list[tuple[str, str]]:
    """Extract (task_id, relation) pairs from spec.input source references.

    Covers all shapes recognized by v1_routes._source_job_id_from_input
    plus asset_id for structure_asset sources.
    """
    refs: list[tuple[str, str]] = []

    source = spec_input.get("source")
    if isinstance(source, dict):
        for key in ("source_job_id", "asset_id"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                refs.append((value.strip(), f"input.source.{key}"))

    top_sjid = spec_input.get("source_job_id")
    if isinstance(top_sjid, str) and top_sjid.strip():
        refs.append((top_sjid.strip(), "input.source_job_id"))

    from_dict = spec_input.get("from")
    if isinstance(from_dict, dict):
        from_sjid = from_dict.get("source_job_id")
        if isinstance(from_sjid, str) and from_sjid.strip():
            refs.append((from_sjid.strip(), "input.from.source_job_id"))

    return refs


def _build_lineage_node(
    record: JobRecord,
    relation: str,
    depth: int,
) -> V2LineageNode:
    spec = record.spec
    return V2LineageNode(
        task_id=record.id,
        workflow=spec.workflow,
        status=record.status.value,
        molecule_name=spec.molecule_name,
        task_name=spec.task_name,
        remark=spec.remark,
        relation=relation,
        depth=depth,
    )


def _resolve_upstream(
    manager: JobManager,
    task_id: str,
) -> list[V2LineageNode]:
    """Recursively resolve upstream lineage via spec.input source references."""
    upstream: list[V2LineageNode] = []
    visited: set[str] = {task_id}
    queue: list[tuple[str, int]] = [(task_id, 0)]

    while queue:
        current_id, depth = queue.pop(0)

        try:
            record = manager.store.get(current_id)
        except (json.JSONDecodeError, sqlite3.Error, ValueError, TypeError):
            logger.warning("lineage: skipping unreadable job %s", current_id)
            continue
        if record is None:
            logger.warning("lineage: task %s not found, skipping", current_id)
            continue

        spec_input = record.spec.input
        if not isinstance(spec_input, dict):
            continue

        for ref_id, relation in _extract_upstream_refs(spec_input):
            if ref_id in visited:
                continue
            visited.add(ref_id)
            try:
                ref_record = manager.store.get(ref_id)
            except (json.JSONDecodeError, sqlite3.Error, ValueError, TypeError):
                logger.warning("lineage: skipping unreadable upstream job %s", ref_id)
                continue
            if ref_record is None:
                logger.warning(
                    "lineage: upstream task %s (from %s) not found, skipping",
                    ref_id, current_id,
                )
                continue
            next_depth = depth + 1
            upstream.append(_build_lineage_node(ref_record, relation, next_depth))
            if next_depth < _MAX_LINEAGE_DEPTH:
                queue.append((ref_id, next_depth))

    return upstream


def _resolve_downstream(
    manager: JobManager,
    task_id: str,
) -> list[V2LineageNode]:
    """Find downstream tasks whose spec_json references this task_id."""
    store = manager.store
    downstream: list[V2LineageNode] = []

    with store._lock, store._connect() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE spec_json LIKE ?",
            (f"%{task_id}%",),
        ).fetchall()

    for row in rows:
        candidate_id = row["id"] if hasattr(row, "keys") else row[0]
        if candidate_id == task_id:
            continue
        try:
            record = manager.store.get(candidate_id)
        except (json.JSONDecodeError, sqlite3.Error, ValueError, TypeError):
            logger.warning("lineage: skipping unreadable downstream job %s", candidate_id)
            continue
        if record is None:
            continue
        spec_input = record.spec.input
        if not isinstance(spec_input, dict):
            continue
        for ref_id, relation in _extract_upstream_refs(spec_input):
            if ref_id == task_id:
                downstream.append(
                    _build_lineage_node(record, relation, 0)
                )
                break

    return downstream


@router.get("/tasks/{task_id}/lineage", response_model=V2LineageResponse)
def get_task_lineage(task_id: str, request: Request) -> V2LineageResponse:
    """Return upstream/downstream lineage for a task (read-only, T12).

    Upstream: recursively resolve spec.input source references (depth ≤ 10,
    cycle-guarded). Downstream: reverse-scan all jobs for references to this
    task_id.  All reads only — never writes data.
    """
    manager = _manager(request)
    try:
        record = manager.store.get(task_id)
    except (json.JSONDecodeError, sqlite3.Error, ValueError, TypeError):
        logger.warning("lineage: queried task %s has unreadable record, returning empty", task_id)
        return V2LineageResponse(task_id=task_id, upstream=[], downstream=[])
    if record is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    upstream = _resolve_upstream(manager, task_id)
    downstream = _resolve_downstream(manager, task_id)

    return V2LineageResponse(
        task_id=task_id,
        upstream=upstream,
        downstream=downstream,
    )
