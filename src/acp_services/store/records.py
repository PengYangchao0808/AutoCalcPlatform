"""Job record models — the single implementation root (B05-7 / todo 77).

The scheduler job data models (``JobStatus`` enum, the ``ExecutionMode`` typed
preference and the ``JobSpec`` / ``JobRecord`` dataclasses) were moved in place
out of ``acp.scheduler.jobs`` so the migrated :mod:`acp_services.store.store`
can construct them without importing ``acp``. ``acp.scheduler.jobs`` re-exports
these same objects.

The open-workflow/CLI-flag *functions* of the legacy ``jobs`` module are NOT
here — they still live in ``acp.scheduler.jobs`` because they derive from the
catalog. Only the pure data models move, keeping this module free of ``acp``
imports (stdlib + ``acp_services`` only).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal

from acp_services.runtime.helpers import utc_now_iso as _utc_now_iso
from acp_services.store.layout import sanitize_task_dir_name

__all__ = [
    "ExecutionMode",
    "JobRecord",
    "JobSpec",
    "JobStatus",
]

ExecutionMode = Literal["local", "remote"]
"""Typed execution-mode preference — never a bare ``str``."""


class JobStatus(str, Enum):
    """Lifecycle states for a scheduler job."""

    QUEUED = "queued"
    STARTING = "starting"
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    WAITING_REVIEW = "waiting_review"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)

    @property
    def is_active(self) -> bool:
        active = (
            JobStatus.QUEUED,
            JobStatus.STARTING,
            JobStatus.PENDING,
            JobStatus.RUNNING,
            JobStatus.PAUSED,
            JobStatus.CANCELLING,
            JobStatus.WAITING_REVIEW,
        )
        return self in active


@dataclass(frozen=True)
class JobSpec:
    """Immutable description of what a job should run.

    Attributes:
        workflow: One of :data:`SUPPORTED_WORKFLOWS`.
        name: Canonical user-facing task label; for persisted jobs this equals
            the final physical task-directory leaf.
        input: Input payload (SMILES string, file path, or structured dict).
        method: Method/protocol settings. For conformer workflows this may
            contain ``protocol``, ``profile_id``, and an optional ``levels``
            dict with per-stage method/basis/solvent overrides.
        resources: Resource limits (nproc, mem, ...).
        output_dir: Explicit output directory override (else derived from run root).
        config_path: Optional path to a YAML config file.
        tags: Free-form tags for filtering.
        execution_mode: Resource-type preference (``"local"`` | ``"remote"``).
            ``None`` = follow the server default.  ``"remote"`` means
            "pick a suitable remote node"; use ``target_node`` to pin a
            specific instance (including ``"local"``).
        target_node: Name of a specific execution target to run on
            (``"local"`` or a configured remote node name).  Takes priority
            over ``execution_mode`` and the server default.
        node_tags: User-required node capability tags (AND semantics).
            Only statically declared tags can ever satisfy them
            (design D13); empty = no tag constraint.  Persisted in
            spec_json — deserialization tolerates the missing key
            (older rows predate the field).
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
    execution_mode: ExecutionMode | None = None
    target_node: str | None = None
    node_tags: list[str] = field(default_factory=list)
    molecule_name: str = ""
    task_name: str = ""
    remark: str = ""
    custom_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def task_dir_name(self) -> str:
        """Return the v2 task directory name ``<molecule>_<task>_<remark>``.

        Defaulting chain (design doc §4.3): the effective task component is
        ``task_name or workflow``; the effective molecule component is
        ``molecule_name or sanitized(name) or "mol"``.  The chain guarantees
        a well-formed name for any spec, so this never raises for callers
        that omit the optional v2 fields.
        """
        task = self.task_name or self.workflow
        if self.molecule_name:
            molecule = self.molecule_name
        else:
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.name)
            molecule = safe.strip("._") or "mol"
        return sanitize_task_dir_name(molecule, task, self.remark)

    @property
    def uses_v2_naming(self) -> bool:
        """Deprecated shim: naming is always v2 — kept for the remote runner."""
        return True


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
    remote_job_id: str | None = None
    group_id: str | None = None
    node_id: str | None = None
    host: str | None = None
    result: dict[str, Any] | None = None
    revision: int = 0
    attempt: int = 1

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
            "remote_job_id": self.remote_job_id,
            "group_id": self.group_id,
            "node_id": self.node_id,
            "host": self.host,
            "result": self.result,
            "revision": self.revision,
            "attempt": self.attempt,
        }
