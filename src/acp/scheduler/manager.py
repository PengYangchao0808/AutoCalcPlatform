"""
Scheduler Manager
=================

Owns job lifecycle: submission, queueing, concurrency limits, background
dispatch, cancellation, and persistence. Runs each job on a bounded thread
pool; the :class:`~acp.scheduler.runner.JobRunner` performs the actual work.
"""

from __future__ import annotations

import json
import logging
import random
import shutil
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from acp.catalog import convert_method_levels_to_protocol_levels
from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import SUPPORTED_WORKFLOWS, JobRecord, JobSpec, JobStatus
from acp.scheduler.projects import ProjectManager
from acp.scheduler.provenance import compute_input_hash
from acp.scheduler.runner import JobRunner
from acp.scheduler.stage_tasks import StageTaskObserver, StageTaskStore
from acp.scheduler.store import JobStore
from conformer_search.config import load_config
from conformer_search.core.protocols import validate_protocol_methods

logger = logging.getLogger(__name__)

# Type-only import to avoid requiring paramiko when remote execution is off.
if TYPE_CHECKING:
    from acp.scheduler.local_cleanup import LocalCleanup, LocalCleanupReport, RetentionPolicy
    from acp.scheduler.remote.cleanup import RemoteCleanup
    from acp.scheduler.remote.config import RemoteExecutionConfig
    from acp.scheduler.remote.fetcher import RemoteResultFetcher
    from acp.scheduler.remote.node_manager import NodeManager
    from acp.scheduler.remote.ssh import SSHConnectionPool


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    """Central job orchestrator backed by SQLite + a thread pool."""

    def __init__(
        self,
        run_root: Path | str,
        store: JobStore | None = None,
        runner: JobRunner | None = None,
        max_running: int = 1,
        remote_config: RemoteExecutionConfig | None = None,
        local_retention_config: RetentionPolicy | None = None,
        local_cleanup_interval_hours: int = 6,
    ):
        self.run_root = Path(run_root)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.store = store or JobStore(self.run_root / "acp_jobs.db")
        self.max_running = max_running

        self._stage_task_store = StageTaskStore(self.store.db_path)
        self._stage_task_observer = StageTaskObserver(self._stage_task_store)

        # Remote execution plumbing (created lazily only when configured).
        self._remote_config = remote_config
        self._runner_ssh_pool: SSHConnectionPool | None = None
        self._fetcher_ssh_pool: SSHConnectionPool | None = None
        self._remote_fetcher: RemoteResultFetcher | None = None
        self._remote_cleanup: RemoteCleanup | None = None
        self._node_manager: NodeManager | None = None
        self.remote_runner = self._create_remote_runner() if self._is_remote_enabled() else None

        # Local disk protection (Phase 5B).  Built eagerly so the
        # property is available immediately for API endpoints even when
        # no job has run yet.
        self._local_cleanup = self._create_local_cleanup(local_retention_config)

        self.runner = runner or JobRunner(stage_task_observer=self._stage_task_observer)
        self.runner.stage_task_observer = self._stage_task_observer
        if self.remote_runner is not None:
            self.runner.remote_runner = self.remote_runner
        if self._local_cleanup is not None:
            self.runner.local_cleanup = self._local_cleanup
        self._projects = ProjectManager(self.store, self.run_root)
        self.default_project_id = self._projects.ensure_default_project()

        self._executor = ThreadPoolExecutor(max_workers=max(1, max_running))
        self._cancel_events: dict[str, threading.Event] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._lock = threading.RLock()
        self._counter = 0

        # Background local-cleanup thread (Phase 5B step 5B.3).  Only
        # started when local cleanup is enabled.
        self._cleanup_thread: threading.Thread | None = None
        self._cleanup_stop_event: threading.Event | None = None
        self._cleanup_lock = threading.Lock()
        self._cleanup_interval_hours = max(1, int(local_cleanup_interval_hours))
        self._start_cleanup_thread()

        self._requeue_active_on_startup()

    def _is_remote_enabled(self) -> bool:
        return self._remote_config is not None and self._remote_config.is_remote

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

    def submit(self, spec: JobSpec) -> JobRecord:
        """Validate, persist, and enqueue a new job."""
        if spec.workflow not in SUPPORTED_WORKFLOWS:
            raise ValueError(
                f"Unsupported workflow: {spec.workflow}. Supported: {SUPPORTED_WORKFLOWS}"
            )

        # Validate that conformer jobs use the canonical methods for the
        # requested protocol unless explicit levels are supplied.
        if spec.workflow == "conformer":
            protocol = spec.method.get("protocol") or "ext"
            levels = spec.method.get("levels")
            if levels:
                levels = convert_method_levels_to_protocol_levels(levels)
            cfg = load_config(config_path=Path(spec.config_path) if spec.config_path else None)
            is_valid, errors = validate_protocol_methods(cfg, protocol, levels)
            if not is_valid:
                raise ValueError(f"Protocol validation failed for {protocol}: {'; '.join(errors)}")

        spec = replace(
            spec,
            project_id=spec.project_id or self.default_project_id,
            input_hash=spec.input_hash or compute_input_hash(spec),
        )

        with self._lock:
            self._counter += 1
            seq = self._counter
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        raw_name = spec.name or spec.workflow
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in raw_name)[:40]
        job_id = f"{ts}_{seq:03d}_{safe_name}"

        work_dir = self._resolve_work_dir(spec, job_id)
        work_dir.mkdir(parents=True, exist_ok=True)

        record = JobRecord(
            id=job_id,
            spec=spec,
            status=JobStatus.QUEUED,
            work_dir=str(work_dir),
            project_id=spec.project_id,
            input_hash=spec.input_hash,
        )
        self.store.create(record)
        self._stage_task_observer.initialize_job_stages(job_id, spec)
        self._write_job_json(record)
        self._event_log(record).append("job.created", job_id=job_id, workflow=spec.workflow)

        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[job_id] = cancel_event
            self._futures[job_id] = self._executor.submit(self._run_job, job_id, cancel_event)
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

        new_name = record.spec.name + "_copy" if record.spec.name else "copy"
        new_spec = replace(
            record.spec,
            project_id=project_id,
            name=new_name,
            output_dir=None,
        )
        return self.submit(new_spec)

    def delete_job(self, job_id: str, delete_data: bool = False) -> bool:
        """Delete a job record and optionally its local and remote data.

        Active jobs must be cancelled before deletion. When ``delete_data`` is
        True the local work directory and remote directories (on all configured
        nodes) are removed before the database record is deleted.
        """
        record = self.store.get(job_id)
        if record is None:
            return False
        if record.status.is_active:
            raise ValueError(f"Job {job_id} is active; cancel it before deletion")

        if delete_data:
            if self._is_remote_enabled() and self._remote_cleanup is not None:
                try:
                    self._remote_cleanup.delete_job_dirs(job_id)
                except Exception:
                    logger.warning("Remote cleanup failed for job %s", job_id, exc_info=True)
            try:
                work_dir = Path(record.work_dir)
                if work_dir.exists():
                    shutil.rmtree(work_dir)
            except Exception:
                logger.warning("Failed to remove work_dir for job %s", job_id, exc_info=True)

        self.store.delete(job_id)
        return True

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
                fut = self._futures.get(job_id)
            if fut is not None and fut.cancel():
                record.status = JobStatus.CANCELLED
                record.completed_at = _utc_now_iso()
                record.touch()
                self.store.update(record)
                self._stage_task_observer.finalize_job(job_id, JobStatus.CANCELLED.value)
                self._write_job_json(record)
                self._event_log(record).append("job.cancelled", job_id=job_id)
                return record

        record.status = JobStatus.CANCELLING
        record.touch()
        self.store.update(record)
        with self._lock:
            ev = self._cancel_events.get(job_id)
        if ev:
            ev.set()
        self._event_log(record).append("job.cancelling", job_id=job_id)
        return record

    def event_log(self, job_id: str) -> JobEventLog | None:
        record = self.store.get(job_id)
        return self._event_log(record) if record else None

    def _resolve_work_dir(self, spec: JobSpec, job_id: str) -> Path:
        """Pick the job work dir, clamping any caller-supplied dir under run_root.

        An explicit ``output_dir`` is honored only if it resolves inside
        ``run_root``; otherwise we fall back to ``run_root/job_id`` so the file
        endpoints can never enumerate or serve paths outside the run root.
        """
        project_root = self.run_root.resolve() / str(spec.project_id or self.default_project_id)
        default = project_root / job_id
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
        return candidate

    def work_dir_of(self, job_id: str) -> Path | None:
        record = self.store.get(job_id)
        return Path(record.work_dir) if record else None

    def shutdown(self) -> None:
        # Stop the local-cleanup background thread first (fast).
        if self._cleanup_stop_event is not None and self._cleanup_thread is not None:
            self._cleanup_stop_event.set()
            self._cleanup_thread.join(timeout=10)
        with self._lock:
            for ev in self._cancel_events.values():
                ev.set()
        time.sleep(0.2)
        self._executor.shutdown(wait=False, cancel_futures=True)
        for ssh_pool in (self._runner_ssh_pool, self._fetcher_ssh_pool):
            if ssh_pool is not None:
                try:
                    ssh_pool.close()
                except Exception:
                    logger.debug("Error closing SSH connection pool", exc_info=True)

    def _event_log(self, record: JobRecord) -> JobEventLog:
        return JobEventLog(Path(record.work_dir) / "events.jsonl")

    def _write_job_json(self, record: JobRecord) -> None:
        path = Path(record.work_dir) / "job.json"
        path.write_text(
            json.dumps(record.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )

    def _run_job(self, job_id: str, cancel_event: threading.Event) -> None:
        record = self.store.get(job_id)
        if record is None:
            logger.error("Job %s vanished before run", job_id)
            return

        record.status = JobStatus.STARTING
        record.started_at = _utc_now_iso()
        record.touch()
        self.store.update(record)
        self._write_job_json(record)

        record.status = JobStatus.RUNNING
        record.touch()
        self.store.update(record)

        event_log = self._event_log(record)
        try:
            exit_code = self.runner.run(record, event_log, cancel_event)
        except Exception as exc:
            logger.exception("Runner raised for job %s", job_id)
            record.status = JobStatus.FAILED
            record.error = str(exc)
            record.completed_at = _utc_now_iso()
            record.touch()
            self.store.update(record)
            self._write_job_json(record)
            return

        record.exit_code = exit_code
        record.completed_at = _utc_now_iso()
        if cancel_event.is_set() and exit_code != 0:
            record.status = JobStatus.CANCELLED
        elif exit_code == 0:
            record.status = JobStatus.COMPLETED
            record.progress = 1.0
            record.result = self._collect_result(record)
        else:
            record.status = JobStatus.FAILED
            record.error = record.error or f"workflow exited with code {exit_code}"
        record.touch()
        self.store.update(record)
        self._write_job_json(record)

    def _collect_result(self, record: JobRecord) -> dict[str, Any]:
        from acp.scheduler.runner import find_workflow_state

        state_path = find_workflow_state(Path(record.work_dir))
        result: dict[str, Any] = dict(record.result or {})
        if state_path is not None and state_path.exists():
            try:
                result["state"] = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return result

    def _requeue_active_on_startup(self) -> None:
        # Mark interrupted jobs FAILED so their work_dir is retained for
        # triage.  The ``[RESTART_FAILED]`` prefix lets LocalCleanup
        # apply a shorter retention window (risk 5 mitigation, Phase 5B)
        # since these dirs hold no useful partial results.
        restart_marker = "[RESTART_FAILED] interrupted by server restart"
        for status in (JobStatus.RUNNING, JobStatus.STARTING, JobStatus.CANCELLING):
            for record in self.store.list(status=status.value):
                record.status = JobStatus.FAILED
                record.error = restart_marker
                record.completed_at = _utc_now_iso()
                record.touch()
                self.store.update(record)
                self._stage_task_observer.finalize_job(record.id, JobStatus.FAILED.value)
                logger.info("Marked interrupted job %s as FAILED", record.id)


__all__ = ["JobManager"]
