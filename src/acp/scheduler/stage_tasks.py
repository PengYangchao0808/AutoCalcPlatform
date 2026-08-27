"""Stage task planning, persistence, and file-based observation."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from acp.scheduler.jobs import JobSpec, censo_preset_from_method
from acp.scheduler.migrations import migrate

_TERMINAL_TASK_STATES = {"completed", "failed", "cancelled", "skipped"}
_EVENT_PRIORITY = {"started": 0, "completed": 1, "failed": 2}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StageTask:
    task_id: str
    job_id: str
    stage_name: str
    task_type: str | None = None
    state: str = "pending"
    exit_status: int | None = None
    retry_count: int = 0
    pid: int | None = None
    stderr_summary: str | None = None
    status_detail: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str = field(default_factory=_utc_now_iso)
    result: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None


@dataclass
class StagePlan:
    stage_name: str
    task_type: str | None = None


class StagePlanProvider(Protocol):
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]: ...


_STAGE_PLAN_PROVIDERS: dict[str, StagePlanProvider] = {}


def register_plan_provider(workflow: str, provider: StagePlanProvider) -> None:
    _STAGE_PLAN_PROVIDERS[workflow.strip().lower()] = provider


def get_stage_plan(spec: JobSpec) -> list[StagePlan]:
    provider = _STAGE_PLAN_PROVIDERS.get(spec.workflow.strip().lower())
    if provider is None:
        return []
    return provider.initial_plan(spec)


class _FakeStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        return [StagePlan("init"), StagePlan("compute"), StagePlan("finalize")]


class _MechanismStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        # Study-mode vocabulary mirrors StudyOrchestrator phases: S0/S1/S2/S3/SR/S4.
        # The observer still watches stage-task marker files; mechanism-study
        # progress is primarily event-file based (events.jsonl), so these names
        # are an initial UI plan rather than file-emitted phase markers.
        return [
            StagePlan("S0"),
            StagePlan("S1"),
            StagePlan("S2"),
            StagePlan("S3"),
            StagePlan("SR"),
            StagePlan("S4"),
        ]


class _EnsembleStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        preset = censo_preset_from_method(spec.method) or "censo-light"
        if preset == "censo-zero":
            return [
                StagePlan("embed_smiles"),
                StagePlan("crest_search"),
                StagePlan("ensemble_export"),
            ]
        if preset == "censo-default":
            return [
                StagePlan("embed_smiles"),
                StagePlan("crest_search"),
                StagePlan("censo_prescreening"),
                StagePlan("censo_screening"),
                StagePlan("censo_optimization"),
                StagePlan("ensemble_export"),
            ]
        # censo-light (default)
        return [
            StagePlan("embed_smiles"),
            StagePlan("crest_search"),
            StagePlan("censo_prescreening"),
            StagePlan("censo_screening"),
            StagePlan("ensemble_export"),
        ]


class _EnergyStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        preset = censo_preset_from_method(spec.method) or "censo-light"
        no_opt = spec.method.get("no_opt", False)
        if preset == "censo-zero":
            if no_opt:
                return [
                    StagePlan("embed_smiles"),
                    StagePlan("crest_search"),
                    StagePlan("censo_refinement"),
                    StagePlan("final_format"),
                ]
            return [
                StagePlan("embed_smiles"),
                StagePlan("crest_search"),
                StagePlan("dft_optimize"),
                StagePlan("frequency"),
                StagePlan("single_point"),
                StagePlan("shermo_thermo"),
                StagePlan("final_format"),
            ]
        if preset == "censo-default":
            return [
                StagePlan("embed_smiles"),
                StagePlan("crest_search"),
                StagePlan("censo_prescreening"),
                StagePlan("censo_screening"),
                StagePlan("censo_optimization"),
                StagePlan("censo_refinement"),
                StagePlan("frequency"),
                StagePlan("shermo_thermo"),
                StagePlan("final_format"),
            ]
        # censo-light (default)
        if no_opt:
            return [
                StagePlan("embed_smiles"),
                StagePlan("crest_search"),
                StagePlan("censo_prescreening"),
                StagePlan("censo_screening"),
                StagePlan("censo_refinement"),
                StagePlan("final_format"),
            ]
        return [
            StagePlan("embed_smiles"),
            StagePlan("crest_search"),
            StagePlan("censo_prescreening"),
            StagePlan("censo_screening"),
            StagePlan("dft_optimize"),
            StagePlan("frequency"),
            StagePlan("single_point"),
            StagePlan("shermo_thermo"),
            StagePlan("final_format"),
        ]


class _NmrStagePlanProvider:
    """Stage plan for the NMR + DP4/DP5 workflow (DevDoc §5).

    Stages 2–3 (CREST/CENSO + GIAO NMR) run per-candidate on the compute
    node; stages 4–8 are lightweight local analysis. The plan below
    mirrors the per-candidate conformer stages (censo-light) followed by
    the GIAO + analysis tail.
    """

    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        preset = censo_preset_from_method(spec.method) or "censo-light"
        if preset == "censo-zero":
            return [
                StagePlan("embed_smiles"),
                StagePlan("crest_search"),
                StagePlan("ensemble_export"),
                StagePlan("giao_nmr"),
                StagePlan("boltzmann_average"),
                StagePlan("dp4_dp5_probability"),
                StagePlan("nmr_report"),
            ]
        return [
            StagePlan("embed_smiles"),
            StagePlan("crest_search"),
            StagePlan("censo_prescreening"),
            StagePlan("censo_screening"),
            StagePlan("ensemble_export"),
            StagePlan("giao_nmr"),
            StagePlan("boltzmann_average"),
            StagePlan("dp4_dp5_probability"),
            StagePlan("nmr_report"),
        ]


class _XtbmdCensoEnergyStagePlanProvider:
    """Stage plan for the xtbmd_censo_energy workflow (DevDoc §8.2).

    Stage names follow the workflow's ``state.initialize`` stage_names:
    embed → xtbmd → batch_opt → isostat → energy_filter → censo →
    dft_handoff → finalize → conformer_energy.  The fine-DFT stages are
    dropped under ``--no-opt`` (except ``censo-default``: the workflow
    forces ``opt_enabled=True`` for that preset regardless of no_opt —
    DevDoc §8.4 / xtbmd_censo_energy.py); the ``censo-zero`` passthrough
    skips the per-conformer CENSO funnel.
    """

    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        preset = censo_preset_from_method(spec.method) or "censo-light"
        no_opt = spec.method.get("no_opt", False)
        plan = [
            StagePlan("embed"),
            StagePlan("xtbmd"),
            StagePlan("batch_opt"),
            StagePlan("isostat"),
            StagePlan("energy_filter"),
        ]
        if preset != "censo-zero":
            plan.append(StagePlan("censo"))
        if not no_opt or preset == "censo-default":
            plan.append(StagePlan("dft_handoff"))
        plan.append(StagePlan("finalize"))
        plan.append(StagePlan("conformer_energy"))
        return plan


class _ConfsearchStagePlanProvider:
    """Confsearch stage plan (plan §12.3): prepare → sampling → energy →
    dedup → refinement → finalize. The refinement stage is policy-dependent
    (``screen`` protocols stop after dedup)."""

    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        policy = str(spec.method.get("refinement_policy") or "screen")
        protocol = str(spec.method.get("protocol") or "censo-crest")
        plan = [
            StagePlan("prepare"),
            StagePlan("sampling"),
            StagePlan("energy"),
            StagePlan("dedup"),
        ]
        if policy != "screen" and protocol not in ("xtb-crest", "xtb-md"):
            plan.append(StagePlan("refinement"))
        plan.append(StagePlan("finalize"))
        return plan


class _PesSearchStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        if str(spec.method.get("mode") or "") == "bond_length_scan":
            from acp.mechanism.bond_scan import BOND_SCAN_STAGES

            return [StagePlan(name) for name in BOND_SCAN_STAGES]
        return [
            StagePlan("prepare"),
            StagePlan("path_search"),
            StagePlan("candidate_extract"),
            StagePlan("finalize"),
        ]


class _LowConfirmStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        plan = [
            StagePlan("prepare"),
            StagePlan("optimize"),
            StagePlan("frequency"),
        ]
        if not spec.method.get("no_irc"):
            plan.append(StagePlan("irc"))
        plan.append(StagePlan("finalize"))
        return plan


class _HighConfirmStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        plan = [
            StagePlan("prepare"),
            StagePlan("optimize"),
            StagePlan("frequency"),
            StagePlan("single_point"),
            StagePlan("thermo"),
        ]
        if spec.method.get("irc"):
            plan.append(StagePlan("irc"))
        plan.append(StagePlan("finalize"))
        return plan


_BATCHOPTIMIZE_STAGES: dict[str, tuple[str, ...]] = {
    "opt_only": ("prepare", "optimize", "finalize"),
    "opt_freq": ("prepare", "optimize", "frequency", "finalize"),
    "opt_freq_sp": ("prepare", "optimize", "frequency", "single_point", "finalize"),
    "opt_freq_sp_thermo": (
        "prepare",
        "optimize",
        "frequency",
        "single_point",
        "thermochemistry",
        "finalize",
    ),
}


class _BatchOptimizeStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        profile = str(spec.method.get("profile") or spec.method.get("profile_id") or "opt_freq")
        try:
            stages = _BATCHOPTIMIZE_STAGES[profile.strip().lower()]
        except KeyError as exc:
            raise ValueError(f"unknown BatchOptimize profile: {profile!r}") from exc
        return [StagePlan(stage_name) for stage_name in stages]


class StageTaskStore:
    """Thread-safe SQLite persistence for stage-level task rows."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        migrate(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, task: StageTask) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO stage_tasks (
                    task_id, job_id, stage_name, task_type, state, exit_status, retry_count,
                    pid, stderr_summary, status_detail, started_at, completed_at, updated_at,
                    result_json, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _task_to_row(task),
            )
            conn.commit()

    def get(self, task_id: str) -> StageTask | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM stage_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
        return _row_to_task(row) if row is not None else None

    def list_by_job(self, job_id: str) -> list[StageTask]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM stage_tasks WHERE job_id=? ORDER BY rowid ASC",
                (job_id,),
            ).fetchall()
        return [_row_to_task(row) for row in rows]

    def update(self, task: StageTask) -> None:
        task.updated_at = task.updated_at or _utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE stage_tasks
                SET job_id=?, stage_name=?, task_type=?, state=?, exit_status=?, retry_count=?,
                    pid=?, stderr_summary=?, status_detail=?, started_at=?, completed_at=?,
                    updated_at=?, result_json=?, provenance_json=?
                WHERE task_id=?
                """,
                (
                    task.job_id,
                    task.stage_name,
                    task.task_type,
                    task.state,
                    task.exit_status,
                    task.retry_count,
                    task.pid,
                    task.stderr_summary,
                    task.status_detail,
                    task.started_at,
                    task.completed_at,
                    task.updated_at,
                    json.dumps(task.result) if task.result is not None else None,
                    json.dumps(task.provenance) if task.provenance is not None else None,
                    task.task_id,
                ),
            )
            conn.commit()

    def list_pending_by_job(self, job_id: str) -> list[StageTask]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM stage_tasks WHERE job_id=? AND state='pending' ORDER BY rowid ASC",
                (job_id,),
            ).fetchall()
        return [_row_to_task(row) for row in rows]


class StageTaskObserver:
    """Mirror stage lifecycle marker files into persistent stage task rows."""

    def __init__(self, store: StageTaskStore, poll_interval: float = 1.0):
        self.store = store
        self.poll_interval = poll_interval

    def initialize_job_stages(self, job_id: str, spec: JobSpec) -> list[StageTask]:
        planned = get_stage_plan(spec)
        if not planned:
            return self.store.list_by_job(job_id)

        existing = {task.stage_name: task for task in self.store.list_by_job(job_id)}
        for stage in planned:
            if stage.stage_name in existing:
                continue
            task = StageTask(
                task_id=str(uuid.uuid4()),
                job_id=job_id,
                stage_name=stage.stage_name,
                task_type=stage.task_type,
                updated_at=_utc_now_iso(),
            )
            self.store.create(task)
            existing[stage.stage_name] = task
        return self.store.list_by_job(job_id)

    def reset_job(self, job_id: str) -> None:
        """Reset mirrored stage rows for a full in-place rerun.

        The job identity and stage task IDs remain stable, while execution
        state from the previous attempt is cleared so the UI does not show a
        finished stage from an earlier full rerun as the current result.
        Checkpoint continuation intentionally does not call this method.
        """
        now = _utc_now_iso()
        for task in self.store.list_by_job(job_id):
            task.state = "pending"
            task.exit_status = None
            task.retry_count = 0
            task.pid = None
            task.stderr_summary = None
            task.status_detail = None
            task.started_at = None
            task.completed_at = None
            task.updated_at = now
            task.result = None
            task.provenance = None
            self.store.update(task)

    def poll_and_mirror(self, job_id: str, work_dir: Path) -> list[StageTask]:
        stage_root = work_dir / "stage_tasks"
        if not stage_root.exists():
            return []

        tasks_by_id = {task.task_id: task for task in self.store.list_by_job(job_id)}
        updated: dict[str, StageTask] = {}
        for path in sorted(stage_root.rglob("*.json"), key=_stage_event_sort_key):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue

            task_id = payload.get("task_id")
            stage_name = payload.get("stage")
            if not isinstance(task_id, str) or not task_id:
                continue

            task = tasks_by_id.get(task_id)
            if task is None:
                if not isinstance(stage_name, str) or not stage_name:
                    continue
                task = StageTask(
                    task_id=task_id,
                    job_id=job_id,
                    stage_name=stage_name,
                    updated_at=_utc_now_iso(),
                )
                self.store.create(task)
                tasks_by_id[task_id] = task

            if self._apply_payload(task, payload):
                self.store.update(task)
                updated[task.task_id] = task
        return list(updated.values())

    def finalize_job(self, job_id: str, final_status: str) -> None:
        now = _utc_now_iso()
        for task in self.store.list_by_job(job_id):
            if task.state in _TERMINAL_TASK_STATES:
                continue
            if final_status == "cancelled":
                task.state = "cancelled"
            elif final_status == "completed":
                task.state = "skipped"
            else:
                task.state = "failed"
            task.completed_at = task.completed_at or now
            task.updated_at = now
            self.store.update(task)

    def _apply_payload(self, task: StageTask, payload: dict[str, Any]) -> bool:
        before = json.dumps(_task_snapshot(task), sort_keys=True)
        status = payload.get("status")
        now = _utc_now_iso()

        stage_name = payload.get("stage")
        if isinstance(stage_name, str) and stage_name:
            task.stage_name = stage_name
        if isinstance(payload.get("pid"), int):
            task.pid = payload["pid"]
        if isinstance(payload.get("retry_count"), int):
            task.retry_count = payload["retry_count"]
        if isinstance(payload.get("stderr_summary"), str):
            task.stderr_summary = payload["stderr_summary"]
        if isinstance(payload.get("status_detail"), str) and payload["status_detail"]:
            task.status_detail = payload["status_detail"]
        if isinstance(payload.get("result"), dict):
            task.result = dict(payload["result"])
        if isinstance(payload.get("provenance"), dict):
            task.provenance = dict(payload["provenance"])

        if isinstance(status, str):
            if status == "running":
                if task.state not in _TERMINAL_TASK_STATES:
                    task.state = "running"
                if isinstance(payload.get("started_at"), str):
                    task.started_at = payload["started_at"]
                else:
                    task.started_at = task.started_at or now
            elif status in {"completed", "failed", "cancelled", "skipped"}:
                if task.state not in _TERMINAL_TASK_STATES:
                    task.state = status
                if isinstance(payload.get("started_at"), str) and task.started_at is None:
                    task.started_at = payload["started_at"]
                if isinstance(payload.get("completed_at"), str):
                    task.completed_at = payload["completed_at"]
                else:
                    task.completed_at = task.completed_at or now
                exit_code = payload.get("exit_code")
                if isinstance(exit_code, int):
                    task.exit_status = exit_code
                if status == "failed" and isinstance(payload.get("error"), str):
                    task.stderr_summary = payload["error"]

        task.updated_at = str(payload.get("completed_at") or payload.get("started_at") or now)
        after = json.dumps(_task_snapshot(task), sort_keys=True)
        return before != after


def _task_to_row(task: StageTask) -> tuple[Any, ...]:
    return (
        task.task_id,
        task.job_id,
        task.stage_name,
        task.task_type,
        task.state,
        task.exit_status,
        task.retry_count,
        task.pid,
        task.stderr_summary,
        task.status_detail,
        task.started_at,
        task.completed_at,
        task.updated_at,
        json.dumps(task.result) if task.result is not None else None,
        json.dumps(task.provenance) if task.provenance is not None else None,
    )


def _row_to_task(row: sqlite3.Row) -> StageTask:
    return StageTask(
        task_id=row["task_id"],
        job_id=row["job_id"],
        stage_name=row["stage_name"],
        task_type=row["task_type"],
        state=row["state"],
        exit_status=row["exit_status"],
        retry_count=row["retry_count"],
        pid=row["pid"],
        stderr_summary=row["stderr_summary"],
        status_detail=row["status_detail"] if "status_detail" in row.keys() else None,
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        updated_at=row["updated_at"],
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        provenance=json.loads(row["provenance_json"]) if row["provenance_json"] else None,
    )


def _stage_event_sort_key(path: Path) -> tuple[str, int, str]:
    parts = path.name.split(".")
    marker = parts[-2] if len(parts) >= 3 else path.name
    return (str(path.parent), _EVENT_PRIORITY.get(marker, 99), path.name)


def _task_snapshot(task: StageTask) -> dict[str, Any]:
    return {
        "stage_name": task.stage_name,
        "state": task.state,
        "exit_status": task.exit_status,
        "retry_count": task.retry_count,
        "pid": task.pid,
        "stderr_summary": task.stderr_summary,
        "status_detail": task.status_detail,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "updated_at": task.updated_at,
        "result": task.result,
        "provenance": task.provenance,
    }


register_plan_provider("fake", _FakeStagePlanProvider())
register_plan_provider("confsearch", _ConfsearchStagePlanProvider())
register_plan_provider("pessearch", _PesSearchStagePlanProvider())
register_plan_provider("lowconfirm", _LowConfirmStagePlanProvider())
register_plan_provider("highconfirm", _HighConfirmStagePlanProvider())
register_plan_provider("mechanism", _MechanismStagePlanProvider())
register_plan_provider("ensemble", _EnsembleStagePlanProvider())
register_plan_provider("energy", _EnergyStagePlanProvider())
register_plan_provider("nmr", _NmrStagePlanProvider())
register_plan_provider("xtbmd_censo_energy", _XtbmdCensoEnergyStagePlanProvider())
register_plan_provider("batchoptimize", _BatchOptimizeStagePlanProvider())


# Simple workflow providers
class _SinglepointStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        return [StagePlan(stage_name="single_point")]


class _OptimizeStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        return [StagePlan(stage_name="optimize")]


class _FrequencyStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        return [StagePlan(stage_name="frequency")]


class _ScanStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        return [StagePlan(stage_name="scan")]


class _OptfreqStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        return [StagePlan(stage_name="opt_freq")]


class _OptfreqspStagePlanProvider:
    def initial_plan(self, spec: JobSpec) -> list[StagePlan]:
        return [
            StagePlan(stage_name="opt_freq"),
            StagePlan(stage_name="single_point"),
            StagePlan(stage_name="shermo"),
        ]


register_plan_provider("singlepoint", _SinglepointStagePlanProvider())
register_plan_provider("optimize", _OptimizeStagePlanProvider())
register_plan_provider("frequency", _FrequencyStagePlanProvider())
register_plan_provider("scan", _ScanStagePlanProvider())
register_plan_provider("optfreq", _OptfreqStagePlanProvider())
register_plan_provider("optfreqsp", _OptfreqspStagePlanProvider())


__all__ = [
    "StagePlan",
    "StagePlanProvider",
    "StageTask",
    "StageTaskObserver",
    "StageTaskStore",
    "get_stage_plan",
    "register_plan_provider",
]
