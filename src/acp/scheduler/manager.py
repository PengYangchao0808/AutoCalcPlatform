# pyright: reportMissingImports=false, reportAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnannotatedClassAttribute=false, reportExplicitAny=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportRedeclaration=false, reportUnusedCallResult=false, reportUnnecessaryComparison=false, reportPrivateUsage=false, reportImplicitStringConcatenation=false, reportUnnecessaryIsInstance=false, reportUnreachable=false, reportUnusedParameter=false
"""
Scheduler Manager
=================

Owns job lifecycle: submission, queueing, background dispatch, cancellation,
and persistence. Jobs are submitted immediately (fire-and-forget) and a
background poller periodically queries cluster or subprocess status to update
job states. Batch submissions may additionally limit the number of jobs from
one persisted batch that can execute at once.
"""

from __future__ import annotations

import json
import logging
import random
import shutil
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from acp.calculations.contracts import JsonValue
from acp.mechanism.project import MechanismProjectStore
from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import (
    EXIT_WAITING_REVIEW,
    SUPPORTED_WORKFLOWS,
    JobRecord,
    JobSpec,
    JobStatus,
    write_mechanism_reaction_json,
)
from acp.scheduler.metrics import MetricsExtractor
from acp.scheduler.nodes import (
    ExecutionCapacityUnavailable,
    ExecutionTargetError,
    NodeRegistry,
    NodeSpec,
    validate_execution_request,
)
from acp.scheduler.projects import ProjectManager
from acp.scheduler.provenance import compute_input_hash
from acp.scheduler.runner import (
    JobRunner,
    find_workflow_state,
    populate_mechanism_study_result_metadata,
)
from acp.scheduler.stage_tasks import StageTaskObserver, StageTaskStore
from acp.scheduler.store import JobStore
from acp.scheduler.tasks import TaskIndex
from acp.storage.layout import TaskStorage, runtime_file

logger = logging.getLogger(__name__)

_CALCULATION_CHECKPOINT_PATH: Final = "WORK/00_RUNTIME/checkpoint.json"
_NO_CHECKPOINT_MESSAGE: Final = "该工作流不支持断点续算，请使用重算 (rerun)"

# Type-only import to avoid requiring paramiko when remote execution is off.
if TYPE_CHECKING:
    from acp.scheduler.local_cleanup import LocalCleanup, LocalCleanupReport, RetentionPolicy
    from acp.scheduler.remote.cleanup import RemoteCleanup
    from acp.scheduler.remote.config import RemoteExecutionConfig
    from acp.scheduler.remote.fetcher import RemoteResultFetcher
    from acp.scheduler.remote.monitor import RemoteJobMonitor
    from acp.scheduler.remote.node_manager import NodeManager
    from acp.scheduler.remote.ssh import SSHConnectionPool


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_review_payload(work_dir: Path) -> dict[str, Any] | None:
    payload_path = work_dir / "review_payload.json"
    if not payload_path.exists():
        return None
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _checkpoint_identity(payload_bytes: bytes) -> tuple[str, str] | None:
    """Return ``(workflow, plan_fingerprint)`` for a valid checkpoint payload."""
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    task_id = payload.get("task_id")
    workflow = payload.get("workflow")
    fingerprint = payload.get("plan_fingerprint")
    step_states = payload.get("step_states")
    items_state = payload.get("items_state")
    attempts = payload.get("attempts")
    if not isinstance(task_id, str) or not isinstance(workflow, str):
        return None
    if not isinstance(fingerprint, str) or not fingerprint:
        return None
    if not isinstance(step_states, list) or not isinstance(items_state, dict):
        return None
    if not isinstance(attempts, int) or isinstance(attempts, bool):
        return None
    return workflow, fingerprint


def _fingerprint_hint(payload: JsonValue) -> str | None:
    """Read an optional expected plan fingerprint from persisted metadata."""
    if not isinstance(payload, dict):
        return None
    for key in ("plan_fingerprint", "checkpoint_fingerprint"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("result", "metadata", "checkpoint"):
        nested = _fingerprint_hint(payload.get(key))
        if nested is not None:
            return nested
    return None


class JobManager:
    """Central job orchestrator backed by SQLite + background poller."""

    def __init__(
        self,
        run_root: Path | str,
        store: JobStore | None = None,
        runner: JobRunner | None = None,
        max_running: int = 1,
        poll_interval: int = 15,
        remote_config: RemoteExecutionConfig | None = None,
        local_retention_config: RetentionPolicy | None = None,
        local_cleanup_interval_hours: int = 6,
        local_max_jobs: int = 4,
    ):
        self.run_root = Path(run_root)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.store = store or JobStore(self.run_root / "acp_jobs.db")
        self.max_running = max_running  # retained for API compatibility (unused)
        self.poll_interval = max(5, int(poll_interval))

        self._stage_task_store = StageTaskStore(self.store.db_path)
        self._stage_task_observer = StageTaskObserver(self._stage_task_store)

        # v2 task index (design §9.1/§9.3): mirrors job metadata into the
        # ``tasks`` table at submit + on status transitions.  Best-effort
        # only — a broken index must never break job submission.
        self.tasks: TaskIndex | None = None
        try:
            self.tasks = TaskIndex(self.store.db_path)
        except Exception:
            logger.warning("Task index disabled (initialization failed)", exc_info=True)

        # Remote execution plumbing.  ``remote_runner`` is created whenever
        # remote *capability* exists (nodes configured) — independent of the
        # default execution mode (M1).  The default mode only influences
        # target resolution for jobs that don't pin one themselves.
        self._remote_config = remote_config
        self._runner_ssh_pool: SSHConnectionPool | None = None
        self._fetcher_ssh_pool: SSHConnectionPool | None = None
        self._remote_fetcher: RemoteResultFetcher | None = None
        self._remote_cleanup: RemoteCleanup | None = None
        self._remote_monitor: RemoteJobMonitor | None = None
        self._node_manager = None
        self.registry = NodeRegistry(
            local_max_jobs=local_max_jobs,
            remote_nodes=list(self._remote_config.nodes) if self._remote_config else [],
        )
        self.remote_runner = self._create_remote_runner() if self._remote_available() else None
        if self._node_manager is not None:
            self.registry.status_provider = self._node_manager.get_node_status

        # Local disk protection (Phase 5B).
        self._local_cleanup = self._create_local_cleanup(local_retention_config)

        self.runner = runner or JobRunner(stage_task_observer=self._stage_task_observer)
        self.runner.stage_task_observer = self._stage_task_observer
        if self.remote_runner is not None:
            self.runner.remote_runner = self.remote_runner  # type: ignore[assignment]
        if self._local_cleanup is not None:
            self.runner.local_cleanup = self._local_cleanup
        self._projects = ProjectManager(self.store, self.run_root)
        self.default_project_id = self._projects.ensure_default_project()
        self._mechanism_projects = MechanismProjectStore(self.store.db_path)

        # No ThreadPoolExecutor — all submitted jobs run concurrently via
        # the cluster/local system.  A background poller tracks status.
        self._cancel_events: dict[str, threading.Event] = {}
        self._submission_jobs: set[str] = set()
        self._poll_failures: dict[str, int] = {}
        self._lock = threading.RLock()
        self._counter = 0
        self._metrics_extractor = MetricsExtractor()

        # Background poller thread.
        self._poll_stop = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="acp-poller")

        # Background local-cleanup thread (Phase 5B step 5B.3).
        self._cleanup_thread: threading.Thread | None = None
        self._cleanup_stop_event: threading.Event | None = None
        self._cleanup_lock = threading.Lock()
        self._cleanup_interval_hours = max(1, int(local_cleanup_interval_hours))
        self._start_cleanup_thread()

        self._requeue_active_on_startup()
        self._dispatch_queued_jobs()
        self._poll_thread.start()

    def _is_remote_enabled(self) -> bool:
        return self._remote_config is not None and self._remote_config.is_remote

    def _remote_available(self) -> bool:
        """Remote *capability*: nodes are configured, regardless of default mode."""
        return self._remote_config is not None and bool(self._remote_config.enabled_nodes)

    @property
    def default_execution_mode(self) -> str:
        """Server default execution mode — only consulted during target resolution."""
        if self._remote_config is not None:
            return self._remote_config.execution_mode
        return "local"

    @staticmethod
    def _is_remote_job(record: JobRecord) -> bool:
        """Route lifecycle decisions from the job's own execution provenance.

        Covers all three provenance sources: ``remote_job_id``,
        ``result.lsf_job_id``, and ``result.execution_kind == "remote"``.
        The server default mode is never consulted for dispatched jobs.
        """
        if record.remote_job_id:
            return True
        result = record.result or {}
        return bool(result.get("lsf_job_id") or result.get("execution_kind") == "remote")

    def _create_remote_runner(self):
        """Instantiate the SSH pool + helpers + :class:`RemoteJobRunner`.

        Imports ``acp.scheduler.remote`` lazily so the paramiko dependency
        is only required when remote execution is actually enabled.
        """
        from acp.scheduler.remote import (
            CodeSyncer,
            FileStager,
            NodeManager,
            RemoteCleanup,
            RemoteJobMonitor,
            RemoteJobRunner,
            RemoteResultFetcher,
            SSHConnectionPool,
        )

        assert self._remote_config is not None
        # Runner pool: used by RemoteJobRunner, monitor, and code syncer.
        self._runner_ssh_pool = SSHConnectionPool()
        runner_stager = FileStager(self._runner_ssh_pool)
        monitor = RemoteJobMonitor(self._runner_ssh_pool, runner_stager)
        self._remote_monitor = monitor
        syncer = CodeSyncer(self._runner_ssh_pool)
        cleanup = RemoteCleanup(
            ssh_pool=self._runner_ssh_pool,
            stager=runner_stager,
            remote_config=self._remote_config,
            monitor=monitor,
        )
        self._remote_cleanup = cleanup
        self._node_manager: NodeManager = NodeManager(
            self._remote_config, self._runner_ssh_pool, monitor=monitor
        )
        # Fetcher pool: dedicated to on-demand file downloads so long
        # streaming transfers never block monitoring (P1-5, P1-9).
        self._fetcher_ssh_pool = SSHConnectionPool()
        fetcher_stager = FileStager(self._fetcher_ssh_pool)
        self._remote_fetcher = RemoteResultFetcher(
            ssh_pool=self._fetcher_ssh_pool,
            stager=fetcher_stager,
            remote_config=self._remote_config,
        )
        return RemoteJobRunner(
            ssh_pool=self._runner_ssh_pool,
            remote_config=self._remote_config,
            stager=runner_stager,
            monitor=monitor,
            code_syncer=syncer,
            cleanup=cleanup,
            stage_task_observer=self._stage_task_observer,
        )

    def _create_local_cleanup(self, policy: RetentionPolicy | None) -> LocalCleanup | None:
        """Instantiate the :class:`LocalCleanup` (Phase 5B) when enabled.

        Kept lazy-imported so the scheduler module imports cleanly even
        if a future change to ``local_cleanup.py`` has heavier deps.
        """
        if policy is None:
            return None
        from acp.scheduler.local_cleanup import LocalCleanup

        return LocalCleanup(
            run_root=self.run_root,
            store=self.store,
            policy=policy,
        )

    # ------------------------------------------------------------------ #
    # Background local-cleanup thread (Phase 5B step 5B.3)
    # ------------------------------------------------------------------ #

    def _start_cleanup_thread(self) -> None:
        if self._local_cleanup is None:
            return
        self._cleanup_stop_event = threading.Event()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            args=(self._cleanup_stop_event,),
            name="acp-local-cleanup",
            daemon=True,
        )
        self._cleanup_thread.start()
        logger.info(
            "Started local cleanup background thread (interval=%dh)",
            self._cleanup_interval_hours,
        )

    def _cleanup_loop(self, stop_event: threading.Event) -> None:
        interval = self._cleanup_interval_hours * 3600
        # First run: random delay in [0, interval) to avoid multiple
        # instances stampeding together right after a coordinated restart.
        first_delay = random.uniform(0, interval)
        if stop_event.wait(first_delay):
            return
        while not stop_event.wait(interval):
            self._run_background_cleanup()

    def _run_background_cleanup(self) -> LocalCleanupReport | None:
        """Run one full_cleanup sweep, guarded against re-entrancy.

        Delegates to :meth:`trigger_local_cleanup` so the lock / sweep /
        audit-log sequence is shared between the background thread and
        the maintenance API.  When a manual sweep already holds the lock,
        this tick is simply skipped — the next interval will retry.
        """
        # trigger_local_cleanup acquires _cleanup_lock, runs full_cleanup,
        # releases the lock, and writes _write_cleanup_log — all in one
        # code path shared with the API endpoint.
        return self.trigger_local_cleanup(dry_run=False)

    def _write_cleanup_log(self, report: LocalCleanupReport) -> None:
        try:
            log_path = self.run_root / "cleanup.log"
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {"ts": _utc_now_iso(), **report.to_dict()},
                        default=str,
                    )
                    + "\n"
                )
        except OSError:
            logger.debug("Failed to append cleanup.log", exc_info=True)

    def submit(self, spec: JobSpec, group_id: str | None = None) -> JobRecord:
        """Validate, persist, and enqueue a new job. Returns immediately.

        Job submission (including SSH sync, upload, bsub for remote mode)
        runs on a background daemon thread; an immediate first poll follows
        submission, then the periodic poller takes over.

        Args:
            spec: The job specification to submit.
            group_id: Queue-grouping key. Defaults to the new job's own id
                (self-rooted); pass an ancestor's id to link a clone (e.g.
                a ``rerun_job``) into the same group.
        """
        if spec.workflow not in SUPPORTED_WORKFLOWS:
            raise ValueError(
                f"Unsupported workflow: {spec.workflow}. Supported: {SUPPORTED_WORKFLOWS}"
            )

        spec = replace(
            spec,
            project_id=spec.project_id or self.default_project_id,
            input_hash=spec.input_hash or compute_input_hash(spec),
        )

        with self._lock:
            self._counter += 1
            seq = self._counter
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        # ``name`` used to be a UI-only label containing a batch timestamp
        # (for example ``INT_P__energy__mt5g72i5__2``). Keep that legacy value
        # only for the opaque job id; the persisted task name is canonicalised
        # to the final physical directory name below.
        raw_name = spec.name or spec.workflow
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in raw_name)[:40]
        job_id = f"{ts}_{seq:03d}_{safe_name}"

        work_dir = self._allocate_work_dir(spec, job_id)
        # The directory allocator is the single source of truth for the task
        # label. This also captures a ``__02``/``__03`` collision suffix so
        # queue, task index, task.json and job.json identify the same task.
        spec = replace(spec, name=work_dir.name)
        # v2 scaffold from creation so runtime files (events.jsonl) land in
        # WORK/00_RUNTIME immediately; old dirs without WORK/ stay legacy.
        try:
            TaskStorage(work_dir).ensure_layout()
        except OSError:
            logger.warning("v2 scaffold creation failed for %s", work_dir)

        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[job_id] = cancel_event

        record = JobRecord(
            id=job_id,
            spec=spec,
            status=JobStatus.QUEUED,
            work_dir=str(work_dir),
            project_id=spec.project_id,
            input_hash=spec.input_hash,
            group_id=group_id or job_id,
        )
        self.store.create(record)
        if self.tasks is not None:
            try:
                self.tasks.sync_from_job(record)
            except Exception:
                logger.warning("Task index sync failed for job %s", job_id, exc_info=True)
        self._stage_task_observer.initialize_job_stages(job_id, spec)
        self._write_job_json(record)
        self._event_log(record).append("job.created", job_id=job_id, workflow=spec.workflow)

        # Fire-and-forget: submit on a daemon thread, poll immediately after.
        self._start_submission_thread(job_id, f"acp-submit-{job_id}")

        return record

    def list_jobs(self, status: str | None = None, limit: int = 200) -> list[JobRecord]:
        return self.store.list(status=status, limit=limit)

    def get(self, job_id: str) -> JobRecord | None:
        return self.store.get(job_id)

    def move_job(self, job_id: str, project_id: str) -> JobRecord | None:
        """Move a non-active job to another project, including its work directory."""
        record = self.store.get(job_id)
        if record is None:
            return None
        if record.status.is_active:
            raise ValueError(f"Cannot move active job {job_id}: status={record.status.value}")
        target_project = self._projects.get_project(project_id)
        if target_project is None:
            raise ValueError(f"Project not found: {project_id}")
        if record.project_id == project_id:
            return record

        old_work_dir = Path(record.work_dir)
        new_work_dir = self._resolve_work_dir(replace(record.spec, project_id=project_id), job_id)

        if old_work_dir.exists():
            if new_work_dir.exists():
                raise ValueError(f"Target work directory already exists: {new_work_dir}")
            new_work_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_work_dir), str(new_work_dir))

        self.store.update_project_id_and_work_dir(job_id, project_id, str(new_work_dir))
        updated = self.store.get(job_id)
        if updated is not None:
            self._write_job_json(updated)
        return updated

    def clone_job(self, job_id: str, project_id: str) -> JobRecord | None:
        """Create a duplicate of a job in another (or the same) project."""
        record = self.store.get(job_id)
        if record is None:
            return None
        target_project = self._projects.get_project(project_id)
        if target_project is None:
            raise ValueError(f"Project not found: {project_id}")

        new_spec = replace(
            record.spec,
            project_id=project_id,
            output_dir=None,
        )
        return self.submit(new_spec)

    def rerun_job(self, job_id: str, project_id: str | None = None) -> JobRecord | None:
        """Re-run a terminal job in place.

        Rerun is deliberately different from :meth:`clone_job`: it keeps the
        original database row, task directory, and task identity, then
        queues a new execution attempt against that same task.  A failed or
        cancelled subprocess cannot retain its OS PID, so the scheduler may
        start a new child process, but it must never allocate a new
        ``job_id``/``work_dir`` pair.

        ``project_id`` is retained as a backwards-compatible request
        parameter.  It may only repeat the task's current project; moving or
        copying a task remains the responsibility of ``move``/``clone``.
        """
        record = self.store.get(job_id)
        if record is None:
            return None

        with self._lock:
            # Re-read under the manager lock so two rapid clicks cannot both
            # act on the same stale terminal snapshot.
            record = self.store.get(job_id)
            if record is None:
                return None
            current_project = record.project_id or record.spec.project_id
            if project_id is not None and project_id != current_project:
                raise ValueError("原地重跑不能切换项目，请使用复制到项目")
            if not record.status.is_terminal:
                raise ValueError(f"rerun requires a terminal status; got {record.status.value}")
            if self._has_live_process(job_id):
                raise ValueError(f"job {job_id} still has a live process tracked by the runner")
            if job_id in self._submission_jobs:
                raise ValueError(f"job {job_id} is already being submitted")

            attempts = int((record.result or {}).get("attempts") or 1) + 1
            old_status = record.status.value
            self._archive_attempt(record, attempts)

            result = dict(record.result or {})
            history = result.get("attempt_history")
            if not isinstance(history, list):
                history = []
            history.append(
                {
                    "attempt": attempts - 1,
                    "mode": "rerun",
                    "status": old_status,
                    "completed_at": record.completed_at,
                    "exit_code": record.exit_code,
                    "error": record.error,
                }
            )
            # Do not carry a previous workflow state, remote submission, or
            # result payload into a full rerun.  Keep only scheduler history.
            record.result = {
                "attempts": attempts,
                "attempt_history": history,
            }
            record.status = JobStatus.QUEUED
            record.started_at = None
            record.completed_at = None
            record.current_stage = None
            record.progress = None
            record.error = None
            record.pid = None
            record.exit_code = None
            record.remote_job_id = None
            record.touch()
            self._cancel_events[job_id] = threading.Event()
            self.store.update(record)

        self._stage_task_observer.reset_job(job_id)
        self._sync_task_status(record)
        self._write_job_json(record)
        self._event_log(record).append(
            "job.rerun",
            job_id=job_id,
            rerun_from=old_status,
            attempts=attempts,
            work_dir=record.work_dir,
        )
        self._start_submission_thread(job_id, f"acp-rerun-{job_id}")
        return record

    def _archive_attempt(self, record: JobRecord, attempt: int) -> None:
        """Move prior run material into a recoverable per-attempt archive.

        The task identity files and event log stay at the task root.  All
        workflow-generated content is moved as a unit so a full rerun starts
        with a clean ``WORK``/``RESULT`` tree without deleting diagnostic
        data.  The archive is intentionally inside the task directory and
        therefore remains available to the existing file browser.
        """
        work_dir = Path(record.work_dir)
        if not work_dir.is_dir():
            return
        archive_root = work_dir / "_attempts" / f"attempt_{attempt - 1:03d}"
        archive_root.mkdir(parents=True, exist_ok=True)
        stable = {
            "input.xyz",
            "input_source.json",
            "task.json",
            "job.json",
            "events.jsonl",
            "_attempts",
        }
        for child in list(work_dir.iterdir()):
            if child.name in stable:
                continue
            destination = archive_root / child.name
            try:
                shutil.move(str(child), str(destination))
            except OSError:
                logger.warning(
                    "Could not archive %s before rerun of %s",
                    child,
                    record.id,
                    exc_info=True,
                )

    def delete_job(self, job_id: str, delete_data: bool = False) -> bool:
        """Delete a job record and optionally its local and remote data.

        Active jobs must be cancelled before deletion. When ``delete_data`` is
        True the local work directory and remote directories (on all configured
        nodes) are removed before the database record is deleted. The DB delete
        cascades to stage_tasks / artifacts / mechanism_studies /
        decision_points via :meth:`JobStore.purge_cascade`.
        """
        record = self.store.get(job_id)
        if record is None:
            return False
        if record.status.is_active:
            raise ValueError(f"Job {job_id} is active; cancel it before deletion")

        self._emit_purged_event(record, delete_data=delete_data)
        if delete_data:
            self._delete_job_disk(record)
        self._purge_job_records(job_id)
        return True

    def purge_jobs(
        self,
        job_ids: list[str] | None = None,
        status: str | None = None,
        project_id: str | None = None,
        older_than_days: float | None = None,
        delete_data: bool = False,
        force_cancel: bool = False,
    ) -> list[dict[str, Any]]:
        """Batch-purge jobs with a per-job report (plan §4.1/§4.3).

        The target set is either explicit ``job_ids`` or a filter query
        (``status`` / ``project_id`` / ``older_than_days`` on
        ``completed_at``). Active jobs are skipped unless ``force_cancel``
        cancels them first (then waits up to 30 s for a terminal state).

        Returns:
            One dict per job: ``{job_id, ok, action, error}`` where
            *action* is ``"purged"`` | ``"skipped_active"`` |
            ``"cancel_failed"`` | ``"error"``.
        """
        if job_ids:
            targets = list(dict.fromkeys(job_ids))
        else:
            cutoff = None
            if older_than_days is not None:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
            targets = [
                record.id
                for record in self.store.list(
                    status=status,
                    project_id=project_id,
                    completed_before=cutoff,
                    limit=100000,
                )
            ]

        report: list[dict[str, Any]] = []
        for job_id in targets:
            record = self.store.get(job_id)
            if record is None:
                report.append(
                    {"job_id": job_id, "ok": False, "action": "error", "error": "job not found"}
                )
                continue
            if record.status.is_active:
                if not force_cancel:
                    report.append(
                        {
                            "job_id": job_id,
                            "ok": False,
                            "action": "skipped_active",
                            "error": f"job is active (status={record.status.value}); "
                            "use force_cancel to cancel it first",
                        }
                    )
                    continue
                try:
                    self.cancel(job_id)
                except Exception as exc:
                    report.append(
                        {
                            "job_id": job_id,
                            "ok": False,
                            "action": "cancel_failed",
                            "error": f"cancel raised: {exc}",
                        }
                    )
                    continue
                if not self._await_terminal(job_id, timeout=30.0):
                    report.append(
                        {
                            "job_id": job_id,
                            "ok": False,
                            "action": "cancel_failed",
                            "error": "still active 30s after cancel",
                        }
                    )
                    continue
                record = self.store.get(job_id)
                if record is None:
                    report.append({"job_id": job_id, "ok": True, "action": "purged", "error": None})
                    continue
            try:
                self._emit_purged_event(record, delete_data=delete_data)
                if delete_data:
                    self._delete_job_disk(record)
                self._purge_job_records(job_id)
                report.append({"job_id": job_id, "ok": True, "action": "purged", "error": None})
            except Exception as exc:
                logger.warning("Purge failed for job %s", job_id, exc_info=True)
                report.append({"job_id": job_id, "ok": False, "action": "error", "error": str(exc)})
        return report

    def _await_terminal(self, job_id: str, timeout: float = 30.0) -> bool:
        """Poll the store until *job_id* reaches a terminal state or timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.store.get(job_id)
            if record is None or record.status.is_terminal:
                return True
            time.sleep(1.0)
        record = self.store.get(job_id)
        return record is None or record.status.is_terminal

    def _purge_job_records(self, job_id: str) -> None:
        """Cascade-delete every DB row owned by *job_id* (jobs + children)."""
        self.store.purge_cascade(job_id)

    def _delete_job_disk(self, record: JobRecord) -> None:
        """Remove a job's remote directories and local work directory."""
        job_id = record.id
        if self._is_remote_enabled() and self._remote_cleanup is not None:
            try:
                self._remote_cleanup.delete_job_dirs(
                    job_id,
                    dir_names=[record.spec.task_dir_name()],
                )
            except Exception:
                logger.warning("Remote cleanup failed for job %s", job_id, exc_info=True)
        try:
            work_dir = Path(record.work_dir)
            if work_dir.exists():
                shutil.rmtree(work_dir)
        except Exception:
            logger.warning("Failed to remove work_dir for job %s", job_id, exc_info=True)

    def _emit_purged_event(self, record: JobRecord, delete_data: bool) -> None:
        """Append ``job.purged`` to the job's event log, when it still exists.

        The existence guard keeps :class:`JobEventLog` (whose constructor
        re-creates the parent directory) from resurrecting a work_dir that
        was already removed from disk.
        """
        events_path = runtime_file(record.work_dir, "events.jsonl")
        if not events_path.exists():
            return
        try:
            JobEventLog(events_path).append("job.purged", job_id=record.id, delete_data=delete_data)
        except OSError:
            logger.debug("Failed to append job.purged for %s", record.id, exc_info=True)

    @property
    def projects(self) -> ProjectManager:
        return self._projects

    @property
    def remote_fetcher(self) -> RemoteResultFetcher | None:
        """On-demand remote file/log fetcher (``None`` when remote is off)."""
        return self._remote_fetcher

    @property
    def remote_cleanup(self) -> RemoteCleanup | None:
        """Remote file-lifecycle manager (``None`` when remote is off)."""
        return self._remote_cleanup

    @property
    def remote_monitor(self) -> RemoteJobMonitor | None:
        """Remote LSF job monitor (``None`` when remote is off)."""
        return self._remote_monitor

    @property
    def node_manager(self) -> NodeManager | None:
        """Remote node status manager (``None`` when remote is off)."""
        return self._node_manager

    @property
    def local_cleanup(self) -> LocalCleanup | None:
        """Local disk-protection manager (Phase 5B, ``None`` when disabled)."""
        return self._local_cleanup

    def trigger_local_cleanup(self, dry_run: bool = False) -> LocalCleanupReport | None:
        """Manually trigger a local cleanup sweep (API / admin use).

        Reuses the background-thread lock so manual and automatic sweeps
        never overlap.  Writes the JSONL audit record to
        ``<run_root>/cleanup.log``.  Returns ``None`` when local cleanup
        is disabled or a sweep is already in progress.
        """
        if self._local_cleanup is None:
            return None
        if not self._cleanup_lock.acquire(blocking=False):
            return None
        try:
            report = self._local_cleanup.full_cleanup(dry_run=dry_run)
        except Exception:
            logger.warning("Manual local cleanup failed", exc_info=True)
            return None
        finally:
            self._cleanup_lock.release()
        self._write_cleanup_log(report)
        return report

    @property
    def stage_tasks(self) -> StageTaskStore:
        return self._stage_task_store

    def list_jobs_by_project(self, project_id: str, limit: int = 200) -> list[JobRecord]:
        return self.store.list_by_project(project_id, limit=limit)

    def delete_project(self, project_id: str, delete_data: bool = False) -> bool:
        """Delete a project and optionally all associated jobs and data.

        The default project cannot be deleted.  When ``delete_data`` is True,
        all jobs in the project are removed from the database, their local
        work directories are deleted, and remote directories are cleaned on
        every configured node.  Active jobs block deletion until they are
        cancelled or finish.
        """
        if project_id == self.default_project_id:
            raise ValueError("Default project cannot be deleted")
        project = self._projects.get_project(project_id)
        if project is None:
            return False

        records = self.store.list_by_project(project_id, limit=10000)
        if any(record.status.is_active for record in records):
            active = [r.id for r in records if r.status.is_active]
            raise ValueError(f"Cannot delete project with active jobs: {active}")

        if delete_data:
            job_ids = [record.id for record in records]
            if self._is_remote_enabled() and self._remote_cleanup is not None and job_ids:
                try:
                    self._remote_cleanup.delete_project_dirs(project_id, job_ids)
                except Exception:
                    logger.warning(
                        "Remote cleanup failed for project %s", project_id, exc_info=True
                    )
            for record in records:
                try:
                    work_dir = Path(record.work_dir)
                    if work_dir.exists():
                        shutil.rmtree(work_dir)
                except Exception:
                    logger.warning("Failed to remove work_dir for job %s", record.id, exc_info=True)
                self.store.delete(record.id)

        if delete_data:
            # ProjectManager.delete_project will also rmtree the project dir.
            self._projects.delete_project(project_id, delete_data=True)
        else:
            # Without data deletion, move all jobs to the default project first.
            for record in records:
                try:
                    self.move_job(record.id, self.default_project_id)
                except Exception:
                    logger.warning(
                        "Failed to move job %s to default project", record.id, exc_info=True
                    )
            self._projects.delete_project(project_id, delete_data=False)
        return True

    def counts(self) -> dict[str, int]:
        return self.store.counts()

    def cancel(self, job_id: str) -> JobRecord | None:
        record = self.store.get(job_id)
        if record is None:
            return None
        if record.status.is_terminal:
            return record

        if record.status == JobStatus.QUEUED:
            with self._lock:
                ev = self._cancel_events.pop(job_id, None)
            if ev:
                ev.set()
            record.status = JobStatus.CANCELLED
            record.completed_at = _utc_now_iso()
            record.touch()
            self.store.update(record)
            self._stage_task_observer.finalize_job(job_id, JobStatus.CANCELLED.value)
            self._write_job_json(record)
            self._advance_mechanism_project_for_job(record)
            self._event_log(record).append("job.cancelled", job_id=job_id)
            self._dispatch_queued_jobs()
            return record

        record.status = JobStatus.CANCELLING
        record.touch()
        self.store.update(record)
        with self._lock:
            ev = self._cancel_events.get(job_id)
        if ev:
            ev.set()

        if self._is_remote_job(record) and self.remote_runner is not None:
            if not record.remote_job_id and record.result and record.result.get("lsf_job_id"):
                record.remote_job_id = str(record.result["lsf_job_id"])
                record.touch()
                self.store.update(record)

            if record.remote_job_id:
                ok = self.remote_runner.cancel_remote(job_id, record)
                if not ok:
                    self._event_log(record).append(
                        "remote.cancel_failed", job_id=job_id, reason="bkill did not succeed"
                    )
            else:
                self.runner.cancel_local(job_id)
        else:
            self.runner.cancel_local(job_id)

        self._event_log(record).append("job.cancelling", job_id=job_id)
        return record

    def pause_for_review(self, job_id: str, payload: dict[str, Any]) -> JobRecord:
        """Pause a running job at a manual review gate.

        Args:
            job_id: Job identifier.
            payload: Review payload persisted into ``record.result``.

        Returns:
            Updated job record.

        Raises:
            KeyError: If the job does not exist.
            ValueError: If the current status is not ``RUNNING``.
        """
        record = self.store.get(job_id)
        if record is None:
            raise KeyError(f"Unknown job {job_id!r}")
        if record.status != JobStatus.RUNNING:
            raise ValueError(f"pause_for_review requires RUNNING status; got {record.status.value}")
        result = dict(record.result or {})
        result["review_payload"] = dict(payload)
        record.result = result
        record.status = JobStatus.WAITING_REVIEW
        record.touch()
        self.store.update(record)
        self._write_job_json(record)
        self._event_log(record).append(
            "job.waiting_review",
            job_id=job_id,
            payload=payload,
        )
        return record

    def resume(self, job_id: str, resolution: dict[str, Any] | None = None) -> JobRecord:
        """Resume a job previously paused for manual review.

        Args:
            job_id: Job identifier.
            resolution: Optional review resolution payload.

        Returns:
            Updated job record.

        Raises:
            KeyError: If the job does not exist.
            ValueError: If the current status is not ``WAITING_REVIEW``.
        """
        record = self.store.get(job_id)
        if record is None:
            raise KeyError(f"Unknown job {job_id!r}")
        with self._lock:
            if record.status != JobStatus.WAITING_REVIEW:
                raise ValueError(
                    f"resume requires WAITING_REVIEW status; got {record.status.value}"
                )
            result = dict(record.result or {})
            if resolution is not None:
                result["review_resolution"] = dict(resolution)
            record.result = result
            rerun_submission = bool(resolution and resolution.get("requeue"))
            record.status = JobStatus.STARTING if rerun_submission else JobStatus.RUNNING
            record.touch()
            self.store.update(record)
            self._write_job_json(record)
        self._event_log(record).append(
            "job.review_resumed",
            job_id=job_id,
            resolution=resolution,
            requeue=rerun_submission,
        )
        if rerun_submission:
            self._start_submission_thread(job_id, f"acp-resume-{job_id}")
        return record

    def pause_job(self, job_id: str) -> JobRecord:
        """Pause a running job: SIGSTOP the local process group, ``bstop`` remotely.

        The local subprocess stays alive (frozen, still tracked by the
        runner) so :meth:`unpause_job` can revive it in place; remote jobs
        rely on the LSF bstop/bresume pair instead.

        Args:
            job_id: Job identifier.

        Returns:
            Updated job record.

        Raises:
            KeyError: If the job does not exist.
            ValueError: If the current status is not ``RUNNING``.
            RuntimeError: If the remote pause capability is missing or failed.
        """
        record = self.store.get(job_id)
        if record is None:
            raise KeyError(f"Unknown job {job_id!r}")
        if record.status != JobStatus.RUNNING:
            raise ValueError(f"pause_job requires RUNNING status; got {record.status.value}")

        if self._is_remote_job(record):
            if self.remote_runner is None:
                raise RuntimeError("remote pause unsupported: remote runner is disabled")
            self._remote_bstop_bresume(record, "bstop_job", "pause")
            mode = "bstop"
        elif self.runner.pause_local(job_id):
            mode = "sigstop"
        else:
            # Process already gone (finished between check and signal) —
            # leave the record alone so the poller finalizes it normally.
            raise ValueError(
                f"job {job_id} has no live local process to pause (it may have just finished)"
            )

        record.status = JobStatus.PAUSED
        record.touch()
        self.store.update(record)
        self._write_job_json(record)
        self._event_log(record).append("job.paused", job_id=job_id, mode=mode)
        return record

    def unpause_job(self, job_id: str) -> JobRecord:
        """Resume a paused job: SIGCONT locally, ``bresume`` remotely.

        Args:
            job_id: Job identifier.

        Returns:
            Updated job record.

        Raises:
            KeyError: If the job does not exist.
            ValueError: If the current status is not ``PAUSED``.
            RuntimeError: If the remote unpause capability is missing or failed.
        """
        record = self.store.get(job_id)
        if record is None:
            raise KeyError(f"Unknown job {job_id!r}")
        if record.status != JobStatus.PAUSED:
            raise ValueError(f"unpause_job requires PAUSED status; got {record.status.value}")

        if self._is_remote_job(record):
            if self.remote_runner is None:
                raise RuntimeError("remote unpause unsupported: remote runner is disabled")
            self._remote_bstop_bresume(record, "bresume_job", "unpause")
            mode = "bresume"
        elif self.runner.resume_local(job_id):
            mode = "sigcont"
        else:
            # No tracked process (e.g. record survived a restart) — keep
            # PAUSED rather than flipping to RUNNING with nothing to poll.
            raise RuntimeError(f"cannot unpause local job {job_id}: process is no longer tracked")

        record.status = JobStatus.RUNNING
        record.touch()
        self.store.update(record)
        self._write_job_json(record)
        self._event_log(record).append("job.resumed", job_id=job_id, mode=mode)
        return record

    def continue_job(self, job_id: str) -> JobRecord:
        """Re-enter a FAILED/CANCELLED job from its checkpoint (plan §4.4).

        Workflow matrix: ``mechanism`` re-enters the same work_dir (the
        CLI auto-resumes from ``study.json`` phase fingerprints via the
        stable study_id in ``mechanism_config.json``); ``xtbmd_censo_energy``
        first persists ``method.resume=true`` into the spec so the rebuilt
        CLI command carries ``--resume``; calculation-plan workflows resume
        when their generic checkpoint is present. Other workflows without a
        checkpoint are rejected (the API maps the ``ValueError`` to 409 with
        a rerun hint).

        Args:
            job_id: Job identifier.

        Returns:
            Updated job record (status ``QUEUED``, re-dispatch started).

        Raises:
            KeyError: If the job does not exist.
            ValueError: If the status is not ``FAILED``/``CANCELLED``, a
                live process is still tracked, or the workflow cannot resume.
        """
        with self._lock:
            # Re-read under the manager lock for the same double-submit guard
            # used by in-place rerun.
            record = self.store.get(job_id)
            if record is None:
                raise KeyError(f"Unknown job {job_id!r}")
            if record.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
                message = "continue_job requires FAILED or CANCELLED status; got "
                message += record.status.value
                raise ValueError(message)
            if self._has_live_process(job_id):
                raise ValueError(
                    f"job {job_id} still has a live process tracked by the runner; "
                    "refusing to re-enter (possible zombie)"
                )
            if job_id in self._submission_jobs:
                raise ValueError(f"job {job_id} is already being submitted")
            workflow = record.spec.workflow
            if workflow == "mechanism":
                pass
            elif workflow == "xtbmd_censo_energy":
                record.spec = replace(record.spec, method={**record.spec.method, "resume": True})
            else:
                self._require_generic_checkpoint(record)

            old_status = record.status.value
            result = dict(record.result or {})
            result["attempts"] = int(result.get("attempts") or 1) + 1
            result["continued_from"] = old_status
            record.result = result
            record.status = JobStatus.QUEUED
            record.error = None
            record.started_at = None
            record.exit_code = None
            record.pid = None
            record.remote_job_id = None
            record.completed_at = None
            for key in ("lsf_job_id", "node", "remote_dir", "command_line"):
                result.pop(key, None)
            record.touch()
            self.store.update(record)
            self._cancel_events[job_id] = threading.Event()
        self._write_job_json(record)
        self._event_log(record).append(
            "job.continued",
            job_id=job_id,
            continued_from=old_status,
            attempts=result["attempts"],
            workflow=workflow,
        )
        self._start_submission_thread(job_id, f"acp-continue-{job_id}")
        return record

    def _require_generic_checkpoint(self, record: JobRecord) -> None:
        """Require a valid generic calculation checkpoint for *record*."""
        checkpoint = self._read_generic_checkpoint(record)
        if checkpoint is None:
            raise ValueError(_NO_CHECKPOINT_MESSAGE)

        checkpoint_workflow, actual_fingerprint = checkpoint
        if checkpoint_workflow != record.spec.workflow:
            raise ValueError(
                f"checkpoint workflow mismatch for job {record.id}: "
                f"expected {record.spec.workflow!r}, got {checkpoint_workflow!r}"
            )

        expected_fingerprint = self._expected_checkpoint_fingerprint(record)
        if expected_fingerprint is not None and actual_fingerprint != expected_fingerprint:
            raise ValueError(
                f"checkpoint fingerprint mismatch for job {record.id}: "
                f"expected {expected_fingerprint!r}, got {actual_fingerprint!r}"
            )

    def _read_generic_checkpoint(self, record: JobRecord) -> tuple[str, str] | None:
        """Read a local checkpoint or fetch a missing remote checkpoint on demand."""
        local_path = Path(record.work_dir) / _CALCULATION_CHECKPOINT_PATH
        payload: bytes | None = None
        if local_path.is_file():
            try:
                payload = local_path.read_bytes()
            except OSError:
                return None
        elif self._is_remote_job(record) and self._remote_fetcher is not None:
            try:
                payload = self._remote_fetcher.read_file(record, _CALCULATION_CHECKPOINT_PATH)
            except OSError:
                return None

        if payload is None:
            return None
        return _checkpoint_identity(payload)

    def _expected_checkpoint_fingerprint(self, record: JobRecord) -> str | None:
        """Find an optional expected fingerprint persisted with a scheduler job."""
        for payload in (record.result, record.spec.method):
            fingerprint = _fingerprint_hint(payload)
            if fingerprint is not None:
                return fingerprint

        work_dir = Path(record.work_dir)
        for metadata_name in ("job.json", "task.json"):
            try:
                payload = json.loads((work_dir / metadata_name).read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            fingerprint = _fingerprint_hint(payload)
            if fingerprint is not None:
                return fingerprint
        return None

    def _has_live_process(self, job_id: str) -> bool:
        """True when the runner still tracks a live (un-exited) subprocess."""
        proc = self.runner._processes.get(job_id)
        return proc is not None and proc.poll() is None

    def _remote_bstop_bresume(self, record: JobRecord, method_name: str, action: str) -> None:
        """Invoke the remote monitor's bstop/bresume contract for *record*.

        Follows the ``cancel_job(node, lsf_job_id)`` calling convention,
        resolving the node from execution provenance (``result["node"]``);
        single-argument monitor methods are also accepted.  Raises
        ``RuntimeError`` when the capability is missing (monitor method
        absent, no LSF job id) or the LSF command reports failure — the
        job status is left untouched in every failure mode.
        """
        method = getattr(self._remote_monitor, method_name, None)
        if not callable(method):
            raise RuntimeError(f"remote {action} unsupported: monitor has no {method_name}")
        lsf_id = record.remote_job_id or str((record.result or {}).get("lsf_job_id") or "")
        if not lsf_id:
            raise RuntimeError(f"remote {action} unsupported: no LSF job id")
        node = None
        node_name = (record.result or {}).get("node")
        if self._remote_config is not None and node_name:
            node = self._remote_config.get_node(str(node_name))
        ok = method(node, lsf_id) if node is not None else method(lsf_id)
        if not ok:
            raise RuntimeError(f"remote {action} failed: LSF command did not succeed")

    def event_log(self, job_id: str) -> JobEventLog | None:
        record = self.store.get(job_id)
        return self._event_log(record) if record else None

    def _allocate_work_dir(self, spec: JobSpec, job_id: str) -> Path:
        """Atomically reserve a canonical task directory for a new job.

        ``_dedupe_task_dir`` is intentionally a read-only resolver because it
        is also used by project moves. New submissions must additionally claim
        the selected leaf with ``exist_ok=False`` so concurrent batch submits
        cannot both persist the same task directory.
        """
        candidate = self._resolve_work_dir(spec, job_id)
        for _ in range(100_000):
            try:
                candidate.mkdir(parents=True, exist_ok=False)
                return candidate
            except FileExistsError:
                candidate = self._dedupe_task_dir(candidate)
        raise RuntimeError(f"Failed to atomically allocate task dir for {candidate}")

    def _resolve_work_dir(self, spec: JobSpec, job_id: str) -> Path:
        """Pick the job work dir, clamping any caller-supplied dir under run_root.

        v2 naming is unconditional: the project leaf is the project's
        frozen directory name (:meth:`ProjectManager.dir_leaf_for` — the
        DB ``run_root`` column is authoritative, so renamed or legacy
        UUID projects keep their original leaf), and the task leaf is
        ``<molecule>_<task>_<remark>`` (:meth:`JobSpec.task_dir_name`),
        filesystem-deduped with a short ``__NN`` suffix.  *job_id* stays
        the DB identity only — it never appears in the path.  An explicit
        ``output_dir`` is treated as a task-parent override only if it resolves
        inside ``run_root``; the canonical task leaf is still appended so an
        override cannot reintroduce a display/path mismatch.
        """
        project_root = self.run_root.resolve() / self._projects.dir_leaf_for(
            str(spec.project_id or self.default_project_id)
        )
        default = self._dedupe_task_dir(project_root / spec.task_dir_name())
        if not spec.output_dir:
            return default
        try:
            candidate = Path(spec.output_dir).resolve()
        except (OSError, ValueError):
            return default
        try:
            candidate.relative_to(self.run_root.resolve())
        except ValueError:
            logger.warning(
                "output_dir %s outside run_root; clamping to %s", spec.output_dir, default
            )
            return default
        # A caller may pass the canonical task directory itself for backwards
        # compatibility. In that case use its parent as the override root;
        # otherwise treat output_dir as the parent directory by contract.
        base_name = spec.task_dir_name()
        if candidate.name == base_name or (
            candidate.name.startswith(base_name + "__")
            and candidate.name[len(base_name) + 2 :].isdigit()
        ):
            candidate = candidate.parent
        return self._dedupe_task_dir(candidate / base_name)

    def _dedupe_task_dir(self, base: Path) -> Path:
        """Return *base*, or a ``__NN``-suffixed sibling when the dir already exists.

        v2 naming (§4.3): the physical task dir name is the display name and
        duplicates get a short ``__02`` / ``__03`` suffix.  The DB ``job_id``
        remains the uniqueness authority — this only disambiguates the on-disk
        directory.
        """
        if not base.exists():
            return base
        counter = 2
        while counter < 100000:
            candidate = base.parent / f"{base.name}__{counter:02d}"
            if not candidate.exists():
                return candidate
            counter += 1
        raise RuntimeError(f"Failed to allocate unique task dir for {base}")

    def work_dir_of(self, job_id: str) -> Path | None:
        record = self.store.get(job_id)
        return Path(record.work_dir) if record else None

    def shutdown(self) -> None:
        if self._cleanup_stop_event is not None and self._cleanup_thread is not None:
            self._cleanup_stop_event.set()
            self._cleanup_thread.join(timeout=10)
        self._poll_stop.set()
        if self._poll_thread.is_alive():
            self._poll_thread.join(timeout=10)
        with self._lock:
            for ev in self._cancel_events.values():
                ev.set()
            self._cancel_events.clear()
        for ssh_pool in (self._runner_ssh_pool, self._fetcher_ssh_pool):
            if ssh_pool is not None:
                try:
                    ssh_pool.close()
                except Exception:
                    logger.debug("Error closing SSH connection pool", exc_info=True)

    def _event_log(self, record: JobRecord) -> JobEventLog:
        return JobEventLog(runtime_file(record.work_dir, "events.jsonl"))

    def _write_job_json(self, record: JobRecord) -> None:
        path = Path(record.work_dir) / "job.json"
        path.write_text(
            json.dumps(record.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )

    def _sync_task_status(self, record: JobRecord) -> None:
        """Best-effort task-index refresh after a status transition."""
        if self.tasks is None:
            return
        try:
            self.tasks.update_status(record.id, record.status.value, record.current_stage)
        except Exception:
            logger.warning("Task index status sync failed for job %s", record.id, exc_info=True)

    def _start_submission_thread(self, job_id: str, thread_name: str) -> bool:
        """Start one submission worker unless that job is already dispatching."""
        with self._lock:
            if job_id in self._submission_jobs:
                return False
            self._submission_jobs.add(job_id)
            self._cancel_events.setdefault(job_id, threading.Event())
        try:
            threading.Thread(
                target=self._execute_submission,
                args=(job_id,),
                daemon=True,
                name=thread_name,
            ).start()
        except BaseException:
            with self._lock:
                self._submission_jobs.discard(job_id)
            raise
        return True

    # ------------------------------------------------------------------ #
    # Non-blocking submission + background poller
    # ------------------------------------------------------------------ #

    def _execute_submission(self, job_id: str) -> None:
        try:
            self._execute_submission_impl(job_id)
        finally:
            with self._lock:
                self._submission_jobs.discard(job_id)

    def _execute_submission_impl(self, job_id: str) -> None:
        """Background thread: submit job then immediately poll once.

        Temporary capacity shortfalls (local slots full, all remote nodes
        busy/unreachable) keep the job in ``STARTING`` and retry every
        60 s.  Permanent selection errors (:class:`ExecutionTargetError`)
        fall through to the generic branch and fail immediately.
        """
        try:
            from acp.scheduler.remote.runner import RemoteNodeUnavailableError

            retryable: tuple[type[Exception], ...] = (
                ExecutionCapacityUnavailable,
                RemoteNodeUnavailableError,
            )
        except ImportError:
            # paramiko not installed — the remote runner can never raise its
            # legacy error here; local execution must still work (P3).
            retryable = (ExecutionCapacityUnavailable,)

        retry_delay = 60

        while True:
            try:
                submitted = self._submit_job(job_id)
                if not submitted:
                    # A persisted batch slot is currently occupied. The job
                    # remains QUEUED and the poller will retry it after a
                    # terminal transition or server restart.
                    return
                break  # success
            except retryable as exc:
                record = self.store.get(job_id)
                if record is None:
                    return
                cancel_event = self._cancel_events.get(job_id)
                if cancel_event and cancel_event.is_set():
                    record.status = JobStatus.CANCELLED
                    record.completed_at = _utc_now_iso()
                    record.touch()
                    self.store.update(record)
                    self._sync_task_status(record)
                    self._write_job_json(record)
                    self._event_log(record).append(
                        "job.cancelled",
                        job_id=job_id,
                        reason="cancelled while waiting for execution capacity",
                    )
                    self._stage_task_observer.finalize_job(job_id, "cancelled")
                    self._advance_mechanism_project_for_job(record)
                    return
                if record.status.is_terminal:
                    return
                logger.info(
                    "No execution capacity for job %s (%s), retrying in %ds",
                    job_id,
                    exc,
                    retry_delay,
                )
                self._event_log(record).append(
                    "execution.waiting_for_capacity",
                    job_id=job_id,
                    retry_after=retry_delay,
                    message=str(exc),
                )
                time.sleep(retry_delay)
            except Exception as exc:
                logger.exception("Submission failed for job %s", job_id)
                record = self.store.get(job_id)
                if record and not record.status.is_terminal:
                    record.status = JobStatus.FAILED
                    record.error = f"Submission error: {exc}"
                    record.completed_at = _utc_now_iso()
                    record.touch()
                    self.store.update(record)
                    self._sync_task_status(record)
                    self._write_job_json(record)
                    self._event_log(record).append("job.failed", job_id=job_id, error=str(exc))
                    self._stage_task_observer.finalize_job(job_id, "failed")
                    self._advance_mechanism_project_for_job(record)
                return

        # Only poll if not already terminal (fake workflow finishes in _submit_job)
        record = self.store.get(job_id)
        if record and not record.status.is_terminal:
            self._poll_job(job_id)

    def _batch_slot_available(self, record: JobRecord) -> bool:
        """Return whether *record* may claim its persisted batch slot.

        ``parallelism`` is intentionally a per-batch limit, not a replacement
        for the node-level ``local.max_jobs``/remote capacity limits. Jobs are
        ordered by creation time and an earlier queued member is never passed
        by a later member of the same batch.
        """
        resources = record.spec.resources
        batch_id = resources.get("batch_id") if isinstance(resources, dict) else None
        if not batch_id:
            return True
        try:
            limit = max(1, int(resources.get("parallelism", 1)))
        except (TypeError, ValueError):
            limit = 1

        records = self.store.list(limit=10000)
        members = [
            candidate
            for candidate in records
            if isinstance(candidate.spec.resources, dict)
            and candidate.spec.resources.get("batch_id") == batch_id
        ]
        members.sort(key=lambda candidate: (candidate.created_at or "", candidate.id))
        try:
            position = next(
                index for index, candidate in enumerate(members) if candidate.id == record.id
            )
        except StopIteration:
            return True

        active_statuses = {
            JobStatus.STARTING,
            JobStatus.PENDING,
            JobStatus.RUNNING,
            JobStatus.PAUSED,
            JobStatus.CANCELLING,
            JobStatus.WAITING_REVIEW,
        }
        active_count = sum(
            candidate.id != record.id and candidate.status in active_statuses
            for candidate in members
        )
        if active_count >= limit:
            return False
        return not any(candidate.status == JobStatus.QUEUED for candidate in members[:position])

    def _dispatch_queued_jobs(self) -> None:
        """Re-dispatch durable queued jobs in FIFO order."""
        records = self.store.list(status=JobStatus.QUEUED.value, limit=10000)
        records.sort(key=lambda record: (record.created_at or "", record.id))
        for record in records:
            if self._poll_stop.is_set():
                return
            self._start_submission_thread(record.id, f"acp-queue-{record.id}")

    def _submit_job(self, job_id: str) -> bool:
        """Start the job (local subprocess or remote LSF). Non-blocking.

        For the ``fake`` workflow this runs in-process to completion —
        status is set to COMPLETED directly, avoiding any race with the
        background poll loop.
        """
        record = self.store.get(job_id)
        if record is None:
            logger.error("Job %s vanished before submit", job_id)
            return True
        if (
            record.status in (JobStatus.CANCELLED, JobStatus.CANCELLING)
            or record.status.is_terminal
        ):
            logger.info(
                "Job %s already terminal (%s), skipping submission",
                job_id,
                record.status.value,
            )
            return True

        # Claim the batch slot and transition to STARTING atomically. A
        # rejected member remains QUEUED and is retried by the dispatcher.
        with self._lock:
            if not self._batch_slot_available(record):
                return False
            record.status = JobStatus.STARTING
            record.started_at = _utc_now_iso()
            record.touch()
            self.store.update(record)
            self._sync_task_status(record)
            self._write_job_json(record)

        cancel_event = self._cancel_events.get(job_id, threading.Event())
        event_log = self._event_log(record)
        self._materialize_mechanism_reaction_if_present(record)

        # ------------------------------------------------------------------
        # Fake workflow: run in-process to completion, mark COMPLETED now.
        # ------------------------------------------------------------------
        if record.spec.workflow == "fake":
            self.runner.submit(record, event_log, cancel_event)
            record.status = JobStatus.COMPLETED
            record.exit_code = record.exit_code if record.exit_code is not None else 0
            record.progress = 1.0
            record.completed_at = _utc_now_iso()
            record.touch()
            self.store.update(record)
            self._sync_task_status(record)
            self._write_job_json(record)
            event_log.append("job.completed", job_id=job_id, exit_code=record.exit_code)
            self._stage_task_observer.finalize_job(job_id, "completed")
            self._advance_mechanism_project_for_job(record)
            with self._lock:
                self._cancel_events.pop(job_id, None)
            self._dispatch_queued_jobs()
            return True

        # ------------------------------------------------------------------
        # Real workflows: STARTING → resolve target → submit (fire-and-forget).
        # Remote jobs go to PENDING (waiting for cluster resources);
        # local jobs go to RUNNING (start immediately).
        # ------------------------------------------------------------------
        target = self._resolve_execution_target(record)
        self._record_execution_target(record, target)

        if target.kind == "local":
            # Admission gate + dispatch under the lock so concurrent
            # submission threads cannot oversubscribe local slots (M5).
            with self._lock:
                self._admit_local(record)
                self.runner.submit(record, event_log, cancel_event)
                record.status = JobStatus.RUNNING
                record.touch()
                self.store.update(record)
                self._sync_task_status(record)
                self._write_job_json(record)
            return True

        if self.remote_runner is None:
            raise ExecutionTargetError(
                "Remote execution target resolved but no remote runner is "
                "available (no enabled remote nodes configured)"
            )
        self._ensure_remote_capacity(target)
        lsf_job_id = self.remote_runner.submit_remote(record, event_log, target_node=target.name)
        record.remote_job_id = lsf_job_id
        record.status = JobStatus.PENDING

        record.touch()
        self.store.update(record)
        self._sync_task_status(record)
        self._write_job_json(record)
        return True

    def _materialize_mechanism_reaction_if_present(self, record: JobRecord) -> None:
        if record.spec.workflow != "mechanism":
            return
        study_id = record.spec.method.get("study_id")
        if not study_id:
            return
        study_row = self.store.get_mechanism_study(str(study_id))
        if study_row is None:
            return
        reaction_json_raw = study_row.get("reaction_json")
        if not isinstance(reaction_json_raw, str) or not reaction_json_raw.strip():
            return
        reaction_payload = json.loads(reaction_json_raw)
        if not isinstance(reaction_payload, dict):
            return
        write_mechanism_reaction_json(Path(record.work_dir), str(study_id), reaction_payload)

    # ------------------------------------------------------------------ #
    # Execution target resolution (single decision point — P4)
    # ------------------------------------------------------------------ #

    def _resolve_execution_target(self, record: JobRecord) -> NodeSpec:
        """Resolve the execution target: target_node > execution_mode > default.

        This is the only place the server default mode is consulted.
        """
        spec = record.spec
        validate_execution_request(spec)
        if spec.target_node:
            return self.registry.require(spec.target_node)
        mode = spec.execution_mode or self.default_execution_mode
        if mode == "local":
            return self.registry.local
        return self.registry.select_remote()

    def _record_execution_target(self, record: JobRecord, target: NodeSpec) -> None:
        """Persist execution provenance so poll/cancel/recovery never need
        the server default mode again for this job."""
        result = dict(record.result or {})
        result["execution_target"] = target.name
        result["execution_kind"] = target.kind
        record.result = result
        record.touch()
        self.store.update(record)
        self._event_log(record).append(
            "execution.target_resolved",
            job_id=record.id,
            target=target.name,
            kind=target.kind,
        )

    def count_local_running_jobs(self, exclude_id: str | None = None) -> int:
        """Local jobs holding a slot (STARTING or RUNNING, not remote)."""
        count = 0
        for status in (JobStatus.STARTING.value, JobStatus.RUNNING.value):
            for rec in self.store.list(status=status, limit=10000):
                if exclude_id is not None and rec.id == exclude_id:
                    continue
                if not self._is_remote_job(rec):
                    count += 1
        return count

    def _admit_local(self, record: JobRecord) -> None:
        """Local admission gate — raises when all local slots are taken."""
        limit = self.registry.local.max_jobs
        running = self.count_local_running_jobs(exclude_id=record.id)
        if running >= limit:
            raise ExecutionCapacityUnavailable(
                f"Local execution at capacity ({running}/{limit}); waiting for a slot"
            )

    def _ensure_remote_capacity(self, target: NodeSpec) -> None:
        """Capacity check for an explicitly pinned remote node.

        Auto-selected nodes are already capacity-filtered by
        ``NodeRegistry.select_remote``; this covers the explicit
        ``target_node`` path.  Offline/full targets are temporary
        conditions — the caller retries rather than failing the job.
        """
        running = self.registry.remote_running_jobs(target.name)
        if running is None:
            raise ExecutionCapacityUnavailable(
                f"target node '{target.name}' is offline or unreachable"
            )
        if running >= target.max_jobs:
            raise ExecutionCapacityUnavailable(
                f"target node '{target.name}' is at capacity ({running}/{target.max_jobs})"
            )

    def _poll_job(self, job_id: str) -> None:
        """Single non-blocking check of one job's status."""
        record = self.store.get(job_id)
        if record is None or record.status.is_terminal:
            return

        cancel_event = self._cancel_events.get(job_id, threading.Event())
        event_log = self._event_log(record)
        is_remote = self._is_remote_job(record) and self.remote_runner is not None

        if is_remote:
            try:
                is_terminal, exit_code = self.remote_runner.poll_remote(  # type: ignore[union-attr]
                    record, event_log, cancel_event
                )
                self._poll_failures.pop(job_id, None)
            except Exception as exc:
                # Transport-layer failure (SSH/bjobs unreachable).  This is
                # NOT a job failure: keep the status, do not cancel, do not
                # resubmit — only the LSF scheduler may judge the job (M3).
                failures = self._poll_failures.get(job_id, 0) + 1
                self._poll_failures[job_id] = failures
                logger.warning(
                    "Remote poll unreachable for job %s (failure %d): %s",
                    job_id,
                    failures,
                    exc,
                )
                event_log.append(
                    "remote.poll_unreachable",
                    job_id=job_id,
                    failures=failures,
                    error=str(exc),
                )
                return
        else:
            try:
                is_terminal, exit_code = self.runner.poll(record)
            except Exception as exc:
                logger.exception("Local poll failed for job %s", job_id)
                record.status = JobStatus.FAILED
                record.error = f"Local poll error: {exc}"
                record.completed_at = _utc_now_iso()
                record.touch()
                self.store.update(record)
                self._sync_task_status(record)
                self._write_job_json(record)
                event_log.append("job.failed", job_id=job_id, error=str(exc))
                self._stage_task_observer.finalize_job(job_id, "failed")
                self._advance_mechanism_project_for_job(record)
                self._dispatch_queued_jobs()
                return
            self._metrics_extractor.extract(record.id, Path(record.work_dir))

        if not is_terminal:
            record.touch()
            self.store.update(record)
            self._sync_task_status(record)
            return

        # A mechanism study paused at a review gate: translate the dedicated
        # exit code into WAITING_REVIEW instead of COMPLETED/FAILED.
        if exit_code == EXIT_WAITING_REVIEW:
            record.exit_code = exit_code
            record.status = JobStatus.WAITING_REVIEW
            payload = _load_review_payload(Path(record.work_dir))
            result = dict(record.result or {})
            if payload is not None:
                result["review_payload"] = payload
            record.result = populate_mechanism_study_result_metadata(record, result)
            record.touch()
            self.store.update(record)
            self._sync_task_status(record)
            self._write_job_json(record)
            event_log.append(
                "job.waiting_review",
                job_id=job_id,
                payload=payload,
            )
            with self._lock:
                self._cancel_events.pop(job_id, None)
            return

        record.exit_code = exit_code
        record.completed_at = _utc_now_iso()

        if cancel_event.is_set() and exit_code and exit_code != 0:
            record.status = JobStatus.CANCELLED
        elif exit_code == 0:
            record.status = JobStatus.COMPLETED
            record.progress = 1.0
            record.result = self._collect_result(record)
            if not is_remote:
                self.runner._capture_artifacts(record, Path(record.work_dir))
                self.runner._store_provenance(
                    record,
                    command_line=record.result.get("command_line", ""),
                )
        else:
            record.status = JobStatus.FAILED
            record.error = record.error or f"workflow exited with code {exit_code}"

        record.touch()
        self.store.update(record)
        self._sync_task_status(record)
        self._write_job_json(record)
        self._advance_mechanism_project_for_job(record)
        with self._lock:
            self._cancel_events.pop(job_id, None)
        self._dispatch_queued_jobs()

    def _poll_loop(self) -> None:
        """Background daemon: periodically poll all RUNNING jobs."""
        logger.info(
            "Poll loop started (interval=%ds, remote=%s)",
            self.poll_interval,
            self._is_remote_enabled(),
        )
        while not self._poll_stop.wait(self.poll_interval):
            try:
                # WAITING_REVIEW and PAUSED jobs are intentionally excluded
                # here: WAITING_REVIEW is held for manual review, and a
                # PAUSED job is frozen (SIGSTOP / bstop) with nothing for
                # polling to observe — unpause flips it back to RUNNING and
                # polling resumes (the retained _processes entry means no
                # bounce).
                for status_val in (
                    JobStatus.RUNNING.value,
                    JobStatus.PENDING.value,
                    JobStatus.CANCELLING.value,
                ):
                    records = self.store.list(status=status_val, limit=10000)
                    for record in records:
                        if self._poll_stop.is_set():
                            break
                        try:
                            self._poll_job(record.id)
                        except Exception:
                            logger.exception("Poll error for job %s", record.id)
                # Queued jobs are durable scheduler state. This pass also
                # recovers jobs whose submission worker disappeared because
                # the service restarted while they were waiting for a batch
                # slot.
                self._dispatch_queued_jobs()
            except Exception:
                logger.exception("Poll loop iteration failed")
        logger.info("Poll loop stopped")

    def _collect_result(self, record: JobRecord) -> dict[str, Any]:
        state_path = find_workflow_state(Path(record.work_dir))
        result: dict[str, Any] = dict(record.result or {})
        if state_path is not None and state_path.exists():
            try:
                result["state"] = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return populate_mechanism_study_result_metadata(record, result)

    def _requeue_active_on_startup(self) -> None:
        # Mark interrupted jobs FAILED so their work_dir is retained for
        # triage.  The ``[RESTART_FAILED]`` prefix lets LocalCleanup
        # apply a shorter retention window (risk 5 mitigation, Phase 5B)
        # since these dirs hold no useful partial results.
        #
        # Remote jobs with a valid remote_job_id can be recovered — the background
        # poller will re-check bjobs + .exit_code.
        restart_marker = "[RESTART_FAILED] interrupted by server restart"
        resume_hint = " — 可尝试续算 (try continue)"
        # CANCELLING jobs that were interrupted mid-cancellation should stay
        # CANCELLED — the user's cancel intent must survive a restart.
        for record in self.store.list(status=JobStatus.CANCELLING.value):
            record.status = JobStatus.CANCELLED
            record.error = restart_marker
            record.completed_at = _utc_now_iso()
            record.touch()
            self.store.update(record)
            self._stage_task_observer.finalize_job(record.id, JobStatus.CANCELLED.value)
            self._advance_mechanism_project_for_job(record)
            logger.info("Marked CANCELLING job %s as CANCELLED after restart", record.id)

        # PAUSED jobs: local ones died with the server (their frozen process
        # groups live in the service's cgroup — nothing to kill), so fail
        # them with the resumable hint.  Remote ones keep their bstop state
        # on the LSF side: recover the polling state and KEEP them PAUSED
        # until an explicit unpause (bresume) flips them back to RUNNING.
        paused_marker = (
            "[RESTART_FAILED] paused job frozen at restart — 可续算 (resumable via continue)"
        )
        for record in self.store.list(status=JobStatus.PAUSED.value):
            if self._try_recover_remote_job(record):
                logger.info(
                    "Recovered remote paused job %s (lsf=%s), kept PAUSED",
                    record.id,
                    record.remote_job_id,
                )
                continue
            record.status = JobStatus.FAILED
            record.error = paused_marker
            record.completed_at = _utc_now_iso()
            record.touch()
            self.store.update(record)
            self._stage_task_observer.finalize_job(record.id, JobStatus.FAILED.value)
            self._advance_mechanism_project_for_job(record)
            logger.info("Marked paused job %s as FAILED after restart", record.id)

        # WAITING_REVIEW jobs are intentionally excluded here: a server restart
        # must preserve their paused review state rather than marking them failed.
        for status in (JobStatus.RUNNING, JobStatus.STARTING, JobStatus.PENDING):
            for record in self.store.list(status=status.value):
                if self._try_recover_remote_job(record):
                    logger.info(
                        "Recovered remote job %s (lsf=%s) on restart, poller will resume",
                        record.id,
                        record.remote_job_id,
                    )
                    continue
                # Restart race guard: the workflow may have finished exactly
                # as the server went down — probe disk before failing (Q12).
                if self._disk_shows_completed(Path(record.work_dir)):
                    record.status = JobStatus.COMPLETED
                    record.exit_code = 0
                    record.progress = 1.0
                    record.completed_at = _utc_now_iso()
                    record.result = self._collect_result(record)
                    record.touch()
                    self.store.update(record)
                    self._write_job_json(record)
                    self._stage_task_observer.finalize_job(record.id, JobStatus.COMPLETED.value)
                    self._advance_mechanism_project_for_job(record)
                    logger.info("Marked interrupted job %s as COMPLETED (disk probe)", record.id)
                    continue
                record.status = JobStatus.FAILED
                record.error = restart_marker + resume_hint
                record.completed_at = _utc_now_iso()
                record.touch()
                self.store.update(record)
                self._stage_task_observer.finalize_job(record.id, JobStatus.FAILED.value)
                self._advance_mechanism_project_for_job(record)
                logger.info("Marked interrupted job %s as FAILED", record.id)

    def _disk_shows_completed(self, work_dir: Path) -> bool:
        """Probe disk for a job that finished exactly as the server died.

        True when the workflow ``state.json`` parses and shows every stage
        completed or skipped, and the ``.exit_code`` marker file holds
        ``0`` (the wrapper-script completion sentinel).
        """
        exit_code_path = work_dir / ".exit_code"
        try:
            if not exit_code_path.is_file():
                return False
            if exit_code_path.read_text(encoding="utf-8").strip() != "0":
                return False
        except OSError:
            return False
        state_path = find_workflow_state(work_dir)
        if state_path is None:
            return False
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(data, dict):
            return False
        stages = data.get("stages")
        if not isinstance(stages, dict) or not stages:
            return False
        return all(
            isinstance(info, dict) and info.get("status") in ("completed", "skipped")
            for info in stages.values()
        )

    def _try_recover_remote_job(self, record: JobRecord) -> bool:
        """Attempt to rebuild in-memory state for a remote job after restart.

        Returns True if recovery succeeds (job left in RUNNING for the
        background poller to pick up), False otherwise.
        """
        if not self._is_remote_job(record) or self.remote_runner is None:
            return False
        if not record.remote_job_id:
            return False
        return self.remote_runner.recover_job_state(record)

    # ── Mechanism project hook ───────────────────────────────────────────

    def _advance_mechanism_project_for_job(self, record: JobRecord) -> None:
        """Advance the mechanism project state machine when a stage job finishes.

        Called at every terminal-state transition (local poll, remote poll,
        cancel, fake completion, restart recovery). Skips silently if the
        job has no ``mechanism_project_id`` in its spec.
        """
        mech_project_id = getattr(record.spec, "mechanism_project_id", None)
        if not mech_project_id:
            return
        workflow = record.spec.workflow
        status_val = record.status.value
        try:
            self._mechanism_projects.advance_for_job(
                project_id=mech_project_id,
                job_id=record.id,
                workflow=workflow,
                job_status=status_val,
            )
        except Exception:
            logger.warning(
                "Failed to advance mechanism project %s for job %s",
                mech_project_id,
                record.id,
                exc_info=True,
            )


__all__ = ["JobManager"]
