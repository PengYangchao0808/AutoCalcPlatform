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
    PENDING = "pending"
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
        active = (
            JobStatus.QUEUED, JobStatus.STARTING, JobStatus.PENDING,
            JobStatus.RUNNING, JobStatus.CANCELLING,
        )
        return self in active


def _derive_supported_workflows() -> tuple[str, ...]:
    """Derive the scheduler's supported workflow set from WORKFLOW_CATALOG.

    R14/D5: previously this was a hand-maintained tuple that drifted out
    of sync with ``acp.catalog.WORKFLOW_CATALOG``. It now derives from the
    catalog's ``status == "active"`` entries, plus the synthetic ``fake``
    workflow that exists only for scheduler tests (kept here, not in the
    public catalog — see test 3.12 which excludes ``fake`` from the
    equality assertion against the catalog's active set).

    Falls back to a static list when ``acp.catalog`` cannot be imported
    (e.g. during early bootstrap or standalone cccp use).
    """
    try:
        from acp.catalog import WORKFLOW_CATALOG
    except ImportError:
        return (
            "ensemble", "energy", "mechanism",
            "singlepoint", "optimize", "frequency", "optfreq", "optfreqsp",
            "fake",
        )
    active = tuple(w["id"] for w in WORKFLOW_CATALOG if w.get("status") == "active")
    # ``fake`` is a synthetic scheduler-only workflow (no catalog entry);
    # append it so test 3.12's set difference `SUPPORTED_WORKFLOWS - {"fake"}`
    # equals the catalog's active id set exactly.
    return active + ("fake",)


SUPPORTED_WORKFLOWS: tuple[str, ...] = _derive_supported_workflows()

_CENSO_PRESETS: tuple[str, ...] = ("censo-light", "censo-default", "censo-zero")


def censo_preset_from_method(method: dict[str, Any]) -> str | None:
    """Resolve the CENSO preset from a job's method dict.

    Priority: ``preset`` > ``profile_id`` > ``protocol``. Unknown values
    (e.g. the wizard's ``__custom__``) resolve to ``None`` so the CLI
    default applies.
    """
    raw = method.get("preset") or method.get("profile_id") or method.get("protocol")
    if not raw:
        return None
    value = str(raw).strip().lower()
    return value if value in _CENSO_PRESETS else None


def censo_solvent_from_method(method: dict[str, Any]) -> str | None:
    """Resolve the workflow-global solvent from a job's method dict.

    Priority: explicit ``method.solvent`` > per-level solvent fields from
    the wizard levels (``refinement_sp`` then ``dft_opt``) when the level's
    solvent_model is not ``none``.
    """
    solvent = method.get("solvent")
    if solvent:
        return str(solvent)
    levels = method.get("levels")
    if isinstance(levels, dict):
        for level_id in ("refinement_sp", "dft_opt"):
            level = levels.get(level_id)
            if not isinstance(level, dict):
                continue
            model = str(level.get("solvent_model") or "").strip().lower()
            value = str(level.get("solvent") or "").strip()
            if value and model not in ("", "none"):
                return value
    return None


def censo_ewin_from_method(method: dict[str, Any]) -> float | None:
    """Resolve the CREST energy window from a job's method dict.

    Priority: explicit ``method.ewin`` > the wizard's ``censo`` level
    ``ewin`` field. Non-numeric or non-positive values resolve to ``None``
    so the workflow/config default applies.
    """
    raw = method.get("ewin")
    if raw is None:
        levels = method.get("levels")
        if isinstance(levels, dict):
            censo_level = levels.get("censo")
            if isinstance(censo_level, dict):
                raw = censo_level.get("ewin")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


# ── xtbmd_censo_energy flag emission (E7: runner ⇄ script_gen parity) ────
# Single source of truth for the MD / batch-opt / ISOSTAT / conv-check /
# resume flag mapping. Both JobRunner._build_cmd and
# build_remote_cli_command emit through this function so the local and
# remote paths can never drift (DevDoc §10.2 — the "runner / script_gen
# 白名单" parity warning). Key set mirrors catalog.FIELD_DEFINITIONS for
# the xtbmd_censo_energy schema plus the energy-like top-level keys.

_XTBMD_SCALAR_FLAGS: dict[str, str] = {
    "md_temperature": "--md-temp",
    "md_time_ps": "--md-time",
    "md_dump_fs": "--md-dump",
    "md_step_fs": "--md-step",
    "md_hmass": "--md-hmass",
    "md_seed": "--md-seed",
    "md_seeds": "--md-seeds",
    "md_method": "--md-method",
    "md_timeout": "--md-timeout",
    "conv_novelty_max": "--conv-novelty-max",
    "conv_rmsd": "--conv-rmsd",
    "max_frames": "--max-frames",
    "opt_gfn": "--opt-gfn",
    "opt_level": "--opt-level",
    "opt_timeout": "--opt-timeout",
    "edis": "--edis",
    "gdis": "--gdis",
    "threshold": "--threshold",
}

# Boolean method keys → CLI opt-out flag (emitted when the value is False).
_XTBMD_BOOL_OPT_OUT_FLAGS: dict[str, str] = {
    "md_shake": "--md-no-shake",
    "md_nvt": "--no-md-nvt",
    "conv_check": "--no-conv-check",
}

# Boolean method keys → CLI opt-in flag (emitted when the value is True).
_XTBMD_BOOL_OPT_IN_FLAGS: dict[str, str] = {
    "keep_frames": "--keep-frames",
    "resume": "--resume",
    "rank1_only": "--rank1-only",
    "no_opt": "--no-opt",
}


def _as_bool(value: Any) -> bool | None:
    """Coerce a method-dict value to ``True`` / ``False`` (or ``None``).

    The frontend submits real JSON booleans, but API clients may send
    ``"true"`` / ``"false"`` strings (or ``1`` / ``0``); strict ``is
    True`` / ``is False`` checks would silently drop those.  Returns
    ``None`` for unrecognised values so the CLI default applies.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        norm = value.strip().lower()
        if norm in ("true", "1", "yes", "on"):
            return True
        if norm in ("false", "0", "no", "off"):
            return False
    return None


def xtbmd_method_flags(method: dict[str, Any]) -> list[str]:
    """Emit the xtbmd_censo_energy CLI flag group from a job's method dict.

    Scalar fields are forwarded whenever present and non-empty; booleans
    are emitted as explicit opt-in / opt-out flags so the CLI defaults
    (rank1_only=False, resume=False, md_nvt=True, conv_check=True, ...)
    never silently override an explicit user choice.  Boolean values are
    normalised via :func:`_as_bool` (tolerates ``"true"`` strings).

    ``ewin`` is intentionally not emitted here: it follows the shared
    :func:`censo_ewin_from_method` priority (``method.ewin`` then
    ``levels.censo.ewin``) used by the energy workflow.
    """
    flags: list[str] = []
    for key, flag in _XTBMD_SCALAR_FLAGS.items():
        value = method.get(key)
        if value is None or value == "":
            continue
        flags += [flag, str(value)]
    for key, flag in _XTBMD_BOOL_OPT_OUT_FLAGS.items():
        if _as_bool(method.get(key)) is False:
            flags.append(flag)
    for key, flag in _XTBMD_BOOL_OPT_IN_FLAGS.items():
        if _as_bool(method.get(key)) is True:
            flags.append(flag)
    return flags


@dataclass(frozen=True)
class JobSpec:
    """Immutable description of what a job should run.

    Attributes:
        workflow: One of :data:`SUPPORTED_WORKFLOWS`.
        name: Human-readable job label.
        input: Input payload (SMILES string, file path, or structured dict).
        method: Method/protocol settings. For conformer workflows this may
            contain ``protocol``, ``profile_id``, and an optional ``levels``
            dict with per-stage method/basis/solvent overrides.
        resources: Resource limits (nproc, mem, ...).
        output_dir: Explicit output directory override (else derived from run root).
        config_path: Optional path to a YAML config file.
        tags: Free-form tags for filtering.
        target_node: Name of a specific remote node to run on (``None`` = auto
            select the least-loaded node).  Only meaningful when the manager
            is configured for remote execution.
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
    target_node: str | None = None

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
    remote_job_id: str | None = None
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
            "remote_job_id": self.remote_job_id,
            "result": self.result,
        }


__all__ = [
    "JobStatus",
    "JobSpec",
    "JobRecord",
    "SUPPORTED_WORKFLOWS",
    "censo_preset_from_method",
    "censo_solvent_from_method",
    "censo_ewin_from_method",
    "xtbmd_method_flags",
]
