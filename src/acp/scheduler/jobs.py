"""
Scheduler Job Models
====================

Job-level data structures for the ACP task scheduler. These are deliberately
separate from the lower-level :class:`acp.core.workflow.WorkflowResult` status —
a ``Job`` wraps a workflow invocation with queueing, persistence, and lifecycle
metadata.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


class JobStatus(str, Enum):
    """Lifecycle states for a scheduler job."""

    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def is_active(self) -> bool:
        active = (JobStatus.QUEUED, JobStatus.STARTING, JobStatus.RUNNING, JobStatus.CANCELLING)
        return self in active


SUPPORTED_WORKFLOWS: tuple[str, ...] = ("conformer", "nmr", "benchmark", "mechanism", "fake")


@dataclass(frozen=True)
class JobSpec:
    """Immutable description of what a job should run.

    Attributes:
        workflow: One of :data:`SUPPORTED_WORKFLOWS`.
        name: Human-readable job label.
        input: Input payload (SMILES string, file path, or structured dict).
        method: Method/protocol settings (protocol, backend, references, ...).
        resources: Resource limits (nproc, mem, ...).
        output_dir: Explicit output directory override (else derived from run root).
        config_path: Optional path to a YAML config file.
        tags: Free-form tags for filtering.
    """

    workflow: str
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    method: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
    output_dir: str | None = None
    config_path: str | None = None
    tags: list[str] = field(default_factory=list)
    project_id: str | None = None
    input_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JobRecord:
    """Mutable, persistable record tracking one job's lifecycle."""

    id: str
    spec: JobSpec
    status: JobStatus = JobStatus.QUEUED
    work_dir: str = ""
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    current_stage: str | None = None
    progress: float | None = None
    error: str | None = None
    project_id: str | None = None
    input_hash: str | None = None
    pid: int | None = None
    exit_code: int | None = None
    result: dict[str, Any] | None = None

    def touch(self) -> None:
        self.updated_at = _utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "spec": self.spec.to_dict(),
            "status": self.status.value,
            "work_dir": self.work_dir,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_stage": self.current_stage,
            "progress": self.progress,
            "error": self.error,
            "project_id": self.project_id,
            "input_hash": self.input_hash,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "result": self.result,
        }


__all__ = ["JobStatus", "JobSpec", "JobRecord", "SUPPORTED_WORKFLOWS"]
