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
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import SUPPORTED_WORKFLOWS, JobRecord, JobSpec, JobStatus
from acp.scheduler.projects import ProjectManager
from acp.scheduler.provenance import compute_input_hash
from acp.scheduler.runner import JobRunner
from acp.scheduler.store import JobStore
from acp.scheduler.stage_tasks import StageTaskObserver, StageTaskStore

logger = logging.getLogger(__name__)


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
    ):
        self.run_root = Path(run_root)
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.store = store or JobStore(self.run_root / "acp_jobs.db")
        self.max_running = max_running

        self._stage_task_store = StageTaskStore(self.store.db_path)
        self._stage_task_observer = StageTaskObserver(self._stage_task_store)
        self.runner = runner or JobRunner(stage_task_observer=self._stage_task_observer)
        self.runner.stage_task_observer = self._stage_task_observer
        self._projects = ProjectManager(self.store, self.run_root)
        self.default_project_id = self._projects.ensure_default_project()

        self._executor = ThreadPoolExecutor(max_workers=max(1, max_running))
        self._cancel_events: dict[str, threading.Event] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._lock = threading.Lock()
        self._counter = 0

        self._requeue_active_on_startup()

    def submit(self, spec: JobSpec) -> JobRecord:
        """Validate, persist, and enqueue a new job."""
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

    @property
    def projects(self) -> ProjectManager:
        return self._projects

    @property
    def stage_tasks(self) -> StageTaskStore:
        return self._stage_task_store

    def list_jobs_by_project(self, project_id: str, limit: int = 200) -> list[JobRecord]:
        return self.store.list_by_project(project_id, limit=limit)

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
        with self._lock:
            for ev in self._cancel_events.values():
                ev.set()
        time.sleep(0.2)
        self._executor.shutdown(wait=False, cancel_futures=True)

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
        for status in (JobStatus.RUNNING, JobStatus.STARTING, JobStatus.CANCELLING):
            for record in self.store.list(status=status.value):
                record.status = JobStatus.FAILED
                record.error = "interrupted by server restart"
                record.completed_at = _utc_now_iso()
                record.touch()
                self.store.update(record)
                self._stage_task_observer.finalize_job(record.id, JobStatus.FAILED.value)
                logger.info("Marked interrupted job %s as FAILED", record.id)

__all__ = ["JobManager"]
