"""
API v2 Schemas
==============

Pydantic models for the project-task ``/api/v2`` surface
(docs/ACP_Project_Task_Storage_Design_v2.md §12).  v2 "tasks" are the
existing scheduler jobs — the jobs table is the task index.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from acp.api.v1_schemas import normalize_node_tags

__all__ = [
    "V2FileEntry",
    "V2ProjectSummary",
    "V2TaskBatchItem",
    "V2TaskBatchRequest",
    "V2TaskBatchResponse",
    "V2TaskDetail",
    "V2TaskPatchRequest",
    "V2TaskRowModel",
    "V2TaskSummary",
    "V2TaskViewFacetsModel",
    "V2TaskViewGroupModel",
    "V2TaskViewResponse",
    "V2TreeResponse",
]


class V2ProjectSummary(BaseModel):
    """One project row with its task count (tasks = scheduler jobs)."""

    project_id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    n_tasks: int = 0
    created_at: str = ""
    updated_at: str = ""


class V2TaskSummary(BaseModel):
    """Task (= job) projection for list views."""

    task_id: str
    display_name: str
    molecule_name: str = ""
    task_name: str = ""
    remark: str = ""
    workflow: str
    task_dir_name: str
    status: str
    project_id: str | None = None
    created_at: str = ""
    updated_at: str = ""


class V2TaskDetail(V2TaskSummary):
    """Task detail = summary plus execution placement and progress."""

    node_id: str | None = None
    work_dir: str = ""
    input_hash: str | None = None
    current_stage: str | None = None
    error: str | None = None


class V2FileEntry(BaseModel):
    """One entry of a one-level area listing (path relative to the area base)."""

    path: str
    size: int = 0
    modified: float = 0.0
    is_dir: bool = False


class V2TreeResponse(BaseModel):
    """One-level listing of a task's ``RESULT/`` or ``WORK/`` area."""

    task_id: str
    area: str
    base: str
    entries: list[V2FileEntry] = Field(default_factory=list)


class V2TaskBatchItem(BaseModel):
    """One independent task in a §12 batch submission.

    The optional ``execution_mode`` / ``target_node`` / ``node_tags`` fields
    pass a node-selection preference through to the created job's spec
    (pure additive — omitted, they leave dispatch fully automatic).
    """

    molecule_name: str
    task_name: str
    remark: str = ""
    workflow: str
    input: dict[str, Any] = Field(default_factory=dict)
    method: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = None
    name: str = ""
    execution_mode: Literal["local", "remote"] | None = None
    target_node: str | None = None
    node_tags: list[str] = Field(default_factory=list)

    @field_validator("node_tags")
    @classmethod
    def _normalize_node_tags(cls, value: list[str]) -> list[str]:
        return normalize_node_tags(value)


class V2TaskBatchRequest(BaseModel):
    """Batch submission payload — every array element creates one task."""

    project_id: str | None = None
    tasks: list[V2TaskBatchItem] = Field(min_length=1)


class V2TaskBatchResponse(BaseModel):
    """Per-item batch outcome: successes and failures are both reported."""

    created: list[V2TaskSummary] = Field(default_factory=list)
    failed: list[dict[str, Any]] = Field(default_factory=list)


# ── Task-view models (T3) ────────────────────────────────────────────────


class V2TaskRowModel(BaseModel):
    """Flat task row for the grouped task view, aligned with
    ``task_views._row_to_task`` output plus optional active-row enrichment."""

    id: str
    status: str
    group_id: str | None = None
    project_id: str = ""
    project_name: str = ""
    created_at: str = ""
    updated_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    last_activity_at: str | None = None
    current_stage: str | None = None
    progress: float | None = None
    molecule_name: str = ""
    task_name: str = ""
    remark: str = ""
    display_name: str = ""
    task_dir_name: str = ""
    workflow: str = ""
    tags: list[str] = Field(default_factory=list)
    archived: bool = False
    batch_id: str | None = None
    spec: dict[str, Any] = Field(default_factory=dict)
    # Active-row enrichment fields (populated only for active-status rows)
    stage_index: int | None = None
    stage_total: int | None = None
    stage_detail: str | None = None
    progress_state: str | None = None
    live_status: dict[str, Any] | None = None
    display_method: str | None = None


class V2TaskViewGroupModel(BaseModel):
    """One group in the grouped task view response."""

    key: str
    display_name: str = ""
    unassigned: bool = False
    retired: bool = False
    count: int = 0
    truncated: bool = False
    min_created_at: str | None = None
    jobs: list[V2TaskRowModel] = Field(default_factory=list)


class V2TaskViewFacetsModel(BaseModel):
    """Faceted counts for each filter dimension."""

    statuses: dict[str, int] = Field(default_factory=dict)
    workflows: list[dict[str, Any]] = Field(default_factory=list)
    molecules: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[dict[str, Any]] = Field(default_factory=list)
    batches: list[dict[str, Any]] = Field(default_factory=list)


class V2TaskViewResponse(BaseModel):
    """Full task-view response with groups, facets, counts, and query echo."""

    groups: list[V2TaskViewGroupModel] = Field(default_factory=list)
    facets: V2TaskViewFacetsModel = Field(default_factory=V2TaskViewFacetsModel)
    total: int = 0
    truncated: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)


class V2TaskPatchRequest(BaseModel):
    """Request body for PATCH /tasks/{task_id} — user-editable display fields only."""

    molecule_name: str | None = None
    task_name: str | None = None
    remark: str | None = None
    tags: list[str] | None = None
