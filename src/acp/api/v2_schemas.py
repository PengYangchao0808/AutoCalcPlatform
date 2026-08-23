"""
API v2 Schemas
==============

Pydantic models for the project-task ``/api/v2`` surface
(docs/ACP_Project_Task_Storage_Design_v2.md §12).  v2 "tasks" are the
existing scheduler jobs — the jobs table is the task index.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "V2FileEntry",
    "V2ProjectSummary",
    "V2TaskBatchItem",
    "V2TaskBatchRequest",
    "V2TaskBatchResponse",
    "V2TaskDetail",
    "V2TaskSummary",
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
    """One independent task in a §12 batch submission."""

    molecule_name: str
    task_name: str
    remark: str = ""
    workflow: str
    input: dict[str, Any] = Field(default_factory=dict)
    method: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = None
    name: str = ""


class V2TaskBatchRequest(BaseModel):
    """Batch submission payload — every array element creates one task."""

    project_id: str | None = None
    tasks: list[V2TaskBatchItem] = Field(min_length=1)


class V2TaskBatchResponse(BaseModel):
    """Per-item batch outcome: successes and failures are both reported."""

    created: list[V2TaskSummary] = Field(default_factory=list)
    failed: list[dict[str, Any]] = Field(default_factory=list)
