"""
API Schemas
===========

Pydantic models isolating API JSON shapes from internal dataclasses.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ServiceStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"


class QueueCounts(BaseModel):
    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0


class StatusResponse(BaseModel):
    service: str = "ACP Workbench"
    version: str = "1.0.0"
    status: ServiceStatus = ServiceStatus.OK
    host: str = ""
    port: int = 0
    wsl: bool = False
    python: str = ""
    run_root: str = ""
    uptime_seconds: float = 0.0
    queue: QueueCounts = Field(default_factory=QueueCounts)


class CapabilityInfo(BaseModel):
    name: str
    available: bool


class BackendInfo(BaseModel):
    name: str
    available: bool
    path: str = ""
    version: str = ""
    capabilities: list[CapabilityInfo] = Field(default_factory=list)


class BackendsResponse(BaseModel):
    backends: list[BackendInfo]


class WorkflowInfo(BaseModel):
    name: str
    label: str
    description: str = ""
    requires_binaries: list[str] = Field(default_factory=list)


class WorkflowsResponse(BaseModel):
    workflows: list[WorkflowInfo]


class ProtocolInfo(BaseModel):
    name: str
    description: str = ""


class ProtocolsResponse(BaseModel):
    protocols: list[ProtocolInfo]


class JobCreateRequest(BaseModel):
    workflow: str
    name: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    method: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)
    output_dir: str | None = None
    config_path: str | None = None
    tags: list[str] = Field(default_factory=list)
    project_id: str | None = None


class JobSpecModel(BaseModel):
    workflow: str
    name: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    method: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)
    output_dir: str | None = None
    config_path: str | None = None
    tags: list[str] = Field(default_factory=list)
    project_id: str | None = None


class JobRecordModel(BaseModel):
    id: str
    spec: JobSpecModel
    status: str
    work_dir: str = ""
    project_id: str | None = None
    input_hash: str | None = None
    created_at: str = ""
    updated_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    current_stage: str | None = None
    progress: float | None = None
    error: str | None = None
    pid: int | None = None
    exit_code: int | None = None
    result: dict[str, Any] | None = None


class JobListResponse(BaseModel):
    jobs: list[JobRecordModel]
    counts: QueueCounts


class JobCreatedResponse(BaseModel):
    job_id: str
    status: str
    workflow: str
    project_id: str | None = None


class FileEntry(BaseModel):
    path: str
    size: int
    modified: float


class FileManifestResponse(BaseModel):
    work_dir: str
    files: list[FileEntry]
    truncated: bool = False


class ErrorResponse(BaseModel):
    detail: str


__all__ = [
    "BackendInfo",
    "BackendsResponse",
    "CapabilityInfo",
    "ErrorResponse",
    "FileEntry",
    "FileManifestResponse",
    "JobCreateRequest",
    "JobCreatedResponse",
    "JobListResponse",
    "JobRecordModel",
    "JobSpecModel",
    "ProtocolInfo",
    "ProtocolsResponse",
    "QueueCounts",
    "ServiceStatus",
    "StatusResponse",
    "WorkflowInfo",
    "WorkflowsResponse",
]
