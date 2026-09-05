"""
Scheduler Runner
================

Maps a :class:`JobSpec` to a concrete execution. Real workflows run as
``python -m acp.cli run <workflow>`` subprocesses for
crash isolation and clean PID-based cancellation. The ``fake`` workflow runs
in-process so the workbench is demoable without QC binaries.

Stage progress is observed by polling the workflow's ``state.json`` and emitting
``stage.*`` events; stdout/stderr are captured to log files.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from acp.chem.embedding import smiles_to_xyz, xyz_to_multiframe_demo
from acp.scheduler.artifacts import ArtifactRegistry, capture_stage_artifacts
from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import (
    EXIT_WAITING_REVIEW,
    SCAN_CONFIG_FILENAME,
    JobRecord,
    JobSpec,
    batchoptimize_method_flags,
    censo_ewin_from_method,
    censo_preset_from_method,
    censo_solvent_from_method,
    confsearch_method_flags,
    input_chemistry_flags,
    nmr_method_flags,
    scan_method_flags,
    xtbmd_method_flags,
)
from acp.scheduler.processctl import find_task_processes, pid_is_alive, process_references
from acp.scheduler.provenance import Provenance, build_provenance_for_job
from acp.scheduler.stage_tasks import StageTaskObserver, StageTaskStore
from acp.storage.layout import TaskStorage, runtime_file
from acp.storage.record import TaskRecord

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 1.0

_GFN_DISPLAY_TO_INT: dict[str, int] = {
    "GFN0-xTB": 0,
    "GFN1-xTB": 1,
    "GFN2-xTB": 2,
    "0": 0,
    "1": 1,
    "2": 2,
}


class JobRunnerRemoteProtocol(Protocol):
    """Structural type shared by :class:`~acp.scheduler.remote.runner.RemoteJobRunner`.

    Avoids importing ``acp.scheduler.remote`` (which requires paramiko) at
    module load time when remote execution is not configured.
    """

    def run(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> int: ...

    def submit_remote(
        self,
        record: JobRecord,
        event_log: JobEventLog,
    ) -> str: ...

    def poll_remote(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> tuple[bool, int | None]: ...

    def cancel_remote(self, job_id: str) -> None: ...

    def recover_job_state(self, record: JobRecord) -> bool: ...


class LocalCleanupProtocol(Protocol):
    """Structural type for :class:`~acp.scheduler.local_cleanup.LocalCleanup`.

    Avoids importing the concrete class at module load time.  Only
    :meth:`pre_submit_housekeeping` is invoked from the runner.
    """

    def pre_submit_housekeeping(self) -> Any: ...


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_timestamp(value: object) -> float:
    """Convert a state.json timestamp to a sortable float.  Unknown / missing
    timestamps map to 0.0 so they sort earliest (best-effort fallback)."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def find_workflow_state(work_dir: Path) -> Path | None:
    """Locate the workflow's ``state.json``, preferring the shallowest match.

    Real workflows nest state.json one level under the output dir
    (``<output>/<mol_id>/state.json``); NMR additionally writes an inner
    conformer state at ``<output>/conformer/<id>/state.json``. Shallowest-first
    selection picks the correct (outer) state in both cases. The ``fake``
    workflow writes directly at the root.
    """
    root_state = work_dir / "state.json"
    if root_state.exists():
        return root_state
    candidates = list(work_dir.rglob("state.json"))
    if not candidates:
        return None

    def _key(path: Path) -> tuple[int, float]:
        try:
            depth = len(path.relative_to(work_dir).parts)
            mtime = path.stat().st_mtime
        except OSError:
            return (99, 0.0)
        return (depth, -mtime)

    return min(candidates, key=_key)


def _opt_text(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return None


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("Failed to read JSON object from %s", path, exc_info=True)
        return None
    if not isinstance(payload, dict):
        logger.debug("Ignoring non-object JSON payload at %s", path)
        return None
    return payload


def _materialized_input_name(source: str, stem: str = "input") -> str:
    """Name for a materialized input file."""
    suffix = Path(source).suffix.lower()
    if suffix in (".com", ".inp"):
        return f"{stem}{suffix}"
    return f"{stem}.xyz"


def _extract_input_source(inp: dict[str, Any]) -> str:
    source = (
        inp.get("input_artifact")
        or inp.get("source")
        or inp.get("input")
        or inp.get("smiles")
        or ""
    )
    return str(source)


def _write_log_header(
    record: JobRecord,
    work_dir: Path,
    cmd: list[str],
    out: Any,
    err: Any,
) -> None:
    """Write the v2 provenance header to ``stdout.log`` and ``stderr.log``.

    The timestamped job id no longer appears in any path (v2 task dirs are
    named ``<molecule>_<task>_<remark>``); instead it is embedded into the
    log content itself, ahead of any subprocess output.
    """
    header_lines = [
        f"# job_id: {record.id}",
        f"# workflow: {record.spec.workflow}",
        f"# task_dir_name: {work_dir.name}",
        f"# work_dir: {work_dir}",
        f"# launched_at: {datetime.now(timezone.utc).isoformat()}",
        f"# command: {' '.join(cmd)}",
        "",
    ]
    header = "\n".join(header_lines)
    out.write(header)
    out.flush()
    err.write(header)
    err.flush()


def _copy_structure_asset(source: str, inputs_dir: Path, run_root: Path, stem: str) -> Path:
    candidate = (run_root / source).resolve()
    try:
        candidate.relative_to(run_root.resolve())
    except ValueError as exc:
        raise ValueError(f"Asset path escapes run_root: {source}") from exc
    if not candidate.is_file():
        raise ValueError(f"Asset file not found: {source}")
    dest = inputs_dir / _materialized_input_name(str(candidate), stem)
    shutil.copy2(candidate, dest)
    return dest


def _materialize_single_input(
    inp: dict[str, Any],
    inputs_dir: Path,
    run_root: Path,
    *,
    stem: str = "input",
) -> Path | None:
    source_type = str(inp.get("source_type", ""))
    source = _extract_input_source(inp)

    if not source:
        return None

    inputs_dir.mkdir(parents=True, exist_ok=True)
    dest = inputs_dir / f"{stem}.xyz"

    if source_type == "xyz_text":
        dest.write_text(source, encoding="utf-8")
        return dest

    if source_type == "structure_asset":
        return _copy_structure_asset(source, inputs_dir, run_root, stem)

    if (
        source.endswith(".xyz")
        or source.endswith(".gjf")
        or source.endswith(".sdf")
        or source.endswith(".com")
        or source.endswith(".inp")
    ):
        candidate = Path(source)
        if candidate.is_file():
            dest = inputs_dir / _materialized_input_name(str(candidate), stem)
            shutil.copy2(candidate, dest)
            return dest

    if source_type == "smiles" or _looks_like_smiles(source):
        xyz = smiles_to_xyz(source)
        dest.write_text(xyz, encoding="utf-8")
        return dest

    try:
        xyz = smiles_to_xyz(source)
        dest.write_text(xyz, encoding="utf-8")
        return dest
    except (ImportError, RuntimeError, ValueError):
        return None


def _materialize_batch_structures(inp: dict[str, Any], inputs_dir: Path) -> Path | None:
    """Expand a Workbench ``batch_structures`` payload into an items file.

    The v2 frontend bundles per-structure entries into
    ``{source_type: "batch_structures", items: [...]}``; the BatchOptimize
    CLI only reads files, so the payload is serialised to
    ``inputs/batch_items.json`` (schema ``batch_structures_v1``) before the
    subprocess starts.  ``source_id`` references must already have been
    inlined by the API layer (they need store/remote-fetcher access).
    """
    items = inp.get("items")
    if not isinstance(items, list):
        return None
    entries: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        xyz = str(item.get("xyz") or "")
        if not xyz.strip():
            continue
        entry: dict[str, Any] = {"xyz": xyz}
        for key in ("name", "tag", "role", "candidate_id", "charge", "multiplicity", "include"):
            if item.get(key) is not None:
                entry[key] = item[key]
        entries.append(entry)
    if not entries:
        return None
    payload = {"schema_version": "batch_structures_v1", "items": entries}
    inputs_dir.mkdir(parents=True, exist_ok=True)
    dest = inputs_dir / "batch_items.json"
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dest


def materialize_job_input(
    inp: dict[str, Any],
    inputs_dir: Path,
    run_root: Path,
    materialized_roles: dict[str, Path] | None = None,
) -> Path | None:
    if isinstance(inp.get("scan_request"), dict):
        return None

    if str(inp.get("source_type") or "") == "batch_structures":
        materialized = _materialize_batch_structures(inp, inputs_dir)
        if materialized is not None:
            return materialized

    return _materialize_single_input(inp, inputs_dir, run_root)


def _looks_like_smiles(s: str) -> bool:
    stripped = s.strip()
    if not stripped or "\n" in stripped or len(stripped) > 200:
        return False
    if stripped[0].isdigit():
        return False
    return True


class JobRunner:
    """Execute jobs as subprocesses (or in-process for the ``fake`` workflow)."""

    def __init__(
        self,
        python_executable: str | None = None,
        stage_task_observer: StageTaskObserver | None = None,
        remote_runner: JobRunnerRemoteProtocol | None = None,
        local_cleanup: LocalCleanupProtocol | None = None,
    ):
        self.python = python_executable or sys.executable
        self.stage_task_observer = stage_task_observer
        # When set, eligible jobs are dispatched to a remote compute node
        # instead of a local subprocess.  Populated by ``JobManager`` when
        # ``remote_config.is_remote`` is True.
        self.remote_runner = remote_runner
        # Local disk-protection manager (Phase 5B).  When set, the local
        # branch runs pre-submit housekeeping before materializing input.
        # Populated by ``JobManager`` when ``local_retention.enabled``.
        self.local_cleanup = local_cleanup
        self._proc_lock = threading.Lock()
        self._processes: dict[str, subprocess.Popen[Any]] = {}
        self._seen_stages: dict[str, set[str]] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._event_logs: dict[str, JobEventLog] = {}
        # Per-task execution locks (WORK/00_RUNTIME/run.lock): at most one
        # live execution per task directory, across service restarts.
        self._run_locks: dict[str, Path] = {}

    # ------------------------------------------------------------------ #
    # New non-blocking API (poller-driven)
    # ------------------------------------------------------------------ #

    def submit(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> None:
        """Start job subprocess, return immediately. Non-blocking.

        For the ``fake`` workflow, runs in-process to completion
        (still non-blocking from the API caller's perspective since
        this runs on a daemon submission thread).
        """
        work_dir = Path(record.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        with self._proc_lock:
            self._cancel_events[record.id] = cancel_event
            self._event_logs[record.id] = event_log

        if record.spec.workflow == "fake":
            TaskStorage(work_dir).result_dir().mkdir(parents=True, exist_ok=True)
            self._run_fake(record, event_log, cancel_event)
            record.exit_code = 0
            record.completed_at = datetime.now(timezone.utc).isoformat()
            return

        # v2 task-storage layout (§3/§5): WORK/00_RUNTIME + RESULT/ scaffold,
        # input.xyz + task.json at the task root.  Scheduler zone-A files
        # (stdout/stderr/events/job.json) intentionally stay at the root for
        # backward compatibility with logs.py / events.py / v1_routes readers.
        storage = TaskStorage(work_dir)
        storage.ensure_layout()
        self._guard_single_execution(record)
        self._acquire_run_lock(record)
        try:
            self._prepare_and_launch(record, work_dir, storage, event_log)
        except BaseException:
            self._release_run_lock(record.id)
            raise

    def _prepare_and_launch(
        self,
        record: JobRecord,
        work_dir: Path,
        storage: TaskStorage,
        event_log: JobEventLog,
    ) -> None:
        """Materialize input, build the command, and spawn the subprocess."""
        observer = self._observer_for_record(record)
        observer.initialize_job_stages(record.id, record.spec)
        event_log.append("job.started", job_id=record.id, workflow=record.spec.workflow)

        skip = self._pre_submit_housekeeping_local(record, event_log)
        if skip:
            raise RuntimeError(f"Local disk full, submission blocked for job {record.id}")

        materialized_roles: dict[str, Path] = {}
        inputs_dir = work_dir
        materialized = materialize_job_input(
            record.spec.input,
            inputs_dir,
            work_dir.parent.parent,
            materialized_roles,
        )
        if materialized is not None and materialized != work_dir / "input.xyz":
            try:
                storage.write_input_xyz(materialized.read_text(encoding="utf-8"))
            except OSError:
                logger.warning("Could not copy primary input.xyz for job %s", record.id)
        storage.write_task_json(
            TaskRecord(
                task_id=record.id,
                project_id=record.project_id or "",
                molecule_name=record.spec.molecule_name,
                task_name=record.spec.task_name,
                remark=record.spec.remark,
                display_name=record.spec.name,
                workflow=record.spec.workflow,
                task_dir_name=work_dir.name,
                status=record.status.value,
                node_path=record.work_dir,
                input_hash=record.input_hash,
                current_stage=record.current_stage,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )

        effective_input_path = (
            str(materialized) if materialized else _extract_input_source(record.spec.input)
        )
        cmd = self._build_cmd(
            record.spec,
            work_dir,
            effective_input_path,
            materialized_roles,
        )
        stdout_path = runtime_file(work_dir, "stdout.log")
        stderr_path = runtime_file(work_dir, "stderr.log")

        event_log.append("process.starting", job_id=record.id, cmd=" ".join(cmd))

        with (
            stdout_path.open("w", encoding="utf-8") as out,
            stderr_path.open("w", encoding="utf-8") as err,
        ):
            _write_log_header(record, work_dir, cmd, out, err)
            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                cmd,
                stdout=out,
                stderr=err,
                cwd=str(work_dir),
                env=env,
                start_new_session=True,
            )
            record.pid = proc.pid

        self._write_run_lock(self._run_locks[record.id], record, task_pid=proc.pid)
        with self._proc_lock:
            self._processes[record.id] = proc
            self._seen_stages[record.id] = set()
        observer.initialize_job_stages(record.id, record.spec)

    def poll(self, record: JobRecord) -> tuple[bool, int | None]:
        """Single non-blocking check of job status.

        Returns ``(is_terminal, exit_code)``.  *is_terminal* is True when
        the job has finished (or vanished).
        """
        with self._proc_lock:
            proc = self._processes.get(record.id)

        if proc is None:
            # Untracked RUNNING job: when its task process is still alive the
            # job is owned by a peer server (or was deliberately skipped by
            # restart recovery) — keep waiting instead of failing it.  The
            # 2026-09-05 incident showed a second server instance must not
            # re-fail a job just because it is absent from ``_processes``.
            if self._foreign_task_alive(record):
                return (False, None)
            event_log = self._event_logs.get(record.id)
            exit_code = record.exit_code if record.exit_code is not None else 1
            if event_log:
                event_log.append(
                    "job.failed" if exit_code != 0 else "job.completed",
                    job_id=record.id,
                    exit_code=exit_code,
                )
            self._release_run_lock(record.id)
            with self._proc_lock:
                self._seen_stages.pop(record.id, None)
                self._cancel_events.pop(record.id, None)
                self._event_logs.pop(record.id, None)
            return (True, exit_code)

        with self._proc_lock:
            cancel_event = self._cancel_events.get(record.id)

        if cancel_event and cancel_event.is_set():
            self._terminate(proc)
            event_log = self._event_logs.get(record.id)
            if event_log:
                event_log.append("job.cancelled", job_id=record.id, exit_code=130)
            self._release_run_lock(record.id)
            with self._proc_lock:
                self._processes.pop(record.id, None)
                self._seen_stages.pop(record.id, None)
                self._cancel_events.pop(record.id, None)
                self._event_logs.pop(record.id, None)
            return (True, 130)

        if record.pid is None:
            record.pid = proc.pid

        ret = proc.poll()
        if ret is not None:
            record.exit_code = ret
            event_log = self._event_logs.get(record.id)
            with self._proc_lock:
                seen = self._seen_stages.setdefault(record.id, set())
            if event_log:
                state_path = find_workflow_state(Path(record.work_dir))
                self._observe_state(record, event_log, state_path, seen)
            observer = self._observer_for_record(record)
            observer.poll_and_mirror(record.id, Path(record.work_dir))
            with self._proc_lock:
                ce = self._cancel_events.get(record.id)
            if ce and ce.is_set() and ret != 0:
                event_log.append(
                    "job.cancelled", job_id=record.id, exit_code=ret
                ) if event_log else None
            elif ret == 0:
                event_log.append(
                    "job.completed", job_id=record.id, exit_code=ret
                ) if event_log else None
            elif ret == EXIT_WAITING_REVIEW:
                event_log.append(
                    "job.waiting_review", job_id=record.id, exit_code=ret
                ) if event_log else None
            else:
                event_log.append(
                    "job.failed", job_id=record.id, exit_code=ret
                ) if event_log else None
            self._release_run_lock(record.id)
            with self._proc_lock:
                self._processes.pop(record.id, None)
                self._seen_stages.pop(record.id, None)
                self._cancel_events.pop(record.id, None)
                self._event_logs.pop(record.id, None)
            return (True, ret)

        event_log = self._event_logs.get(record.id)
        if event_log:
            with self._proc_lock:
                seen = self._seen_stages.setdefault(record.id, set())
            state_path = find_workflow_state(Path(record.work_dir))
            self._observe_state(record, event_log, state_path, seen)
        observer = self._observer_for_record(record)
        observer.poll_and_mirror(record.id, Path(record.work_dir))
        return (False, None)

    def cancel_local(self, job_id: str) -> None:
        """Terminate a locally-running subprocess."""
        self._release_run_lock(job_id)
        with self._proc_lock:
            proc = self._processes.pop(job_id, None)
            self._seen_stages.pop(job_id, None)
            self._cancel_events.pop(job_id, None)
            self._event_logs.pop(job_id, None)
        if proc and proc.poll() is None:
            self._terminate(proc)

    def pause_local(self, job_id: str) -> bool:
        """Freeze a locally-running job's whole process group (SIGSTOP).

        The ``_processes`` entry is **retained** so the poller re-adopts the
        job once :meth:`resume_local` revives it — no state is popped and
        ``record.exit_code`` stays untouched while paused.

        Returns:
            ``True`` when the stop signal was delivered; ``False`` when the
            job is not tracked or its process already exited (let the poller
            finalize it instead of wedging it in PAUSED).
        """
        with self._proc_lock:
            proc = self._processes.get(job_id)
        if proc is None or proc.poll() is not None:
            return False
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGSTOP)
        except ProcessLookupError:
            return False
        except OSError:
            logger.warning("Failed to SIGSTOP process group for job %s", job_id, exc_info=True)
            return False
        return True

    def resume_local(self, job_id: str) -> bool:
        """Revive a paused local job's process group (SIGCONT).

        Returns:
            ``True`` when the job is still tracked — either revived, or it
            exited while paused (nothing to signal; the poller finalizes it
            as soon as the record is RUNNING again).  ``False`` means the
            job is not tracked locally at all (e.g. after a server restart).
        """
        with self._proc_lock:
            proc = self._processes.get(job_id)
        if proc is None:
            return False
        if proc.poll() is not None:
            return True
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGCONT)
        except ProcessLookupError:
            return True
        except OSError:
            logger.warning("Failed to SIGCONT process group for job %s", job_id, exc_info=True)
            return False
        return True

    # ------------------------------------------------------------------ #
    # Per-task single-execution guard (run.lock)
    # ------------------------------------------------------------------ #

    def _live_task_pids(self, record: JobRecord) -> list[int]:
        """Live, non-zombie processes still bound to this task directory.

        Combines the runner-tracked subprocess (same-service case) with a
        ``/proc`` scan so orphans from a previous service instance are found.
        """
        work_dir = Path(record.work_dir)
        with self._proc_lock:
            proc = self._processes.get(record.id)
        pids = set(find_task_processes(work_dir))
        if proc is not None and proc.poll() is None and pid_is_alive(proc.pid):
            pids.add(proc.pid)
        return sorted(pids)

    def _foreign_task_alive(self, record: JobRecord) -> bool:
        """True when an untracked task process is still computing in work_dir.

        Probes the ``run.lock`` task pid first (cheap) and falls back to a
        ``/proc`` scan; the PID-recycling guard requires the pid to actually
        reference the task directory.  Only invoked on the rare
        untracked-RUNNING poll path, so the scan cost is bounded.
        """
        work_dir = Path(record.work_dir)
        payload: dict[str, object] = {}
        try:
            payload = json.loads(runtime_file(work_dir, "run.lock").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        task_pid = payload.get("task_pid") if isinstance(payload, dict) else None
        if isinstance(task_pid, int) and task_pid > 0:
            if pid_is_alive(task_pid) and process_references(task_pid, work_dir):
                return True
        return bool(find_task_processes(work_dir))

    def _guard_single_execution(self, record: JobRecord) -> None:
        """Refuse to start when a live process already occupies the task."""
        live = self._live_task_pids(record)
        if live:
            raise RuntimeError(
                f"task {record.id} still has live process(es) {live} in "
                f"{record.work_dir}; refusing to start a second execution"
            )

    def _write_run_lock(self, lock_path: Path, record: JobRecord, task_pid: int | None) -> None:
        payload = {
            "job_id": record.id,
            "owner_pid": os.getpid(),
            "task_pid": task_pid,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _acquire_run_lock(self, record: JobRecord) -> None:
        """Atomically claim ``WORK/00_RUNTIME/run.lock`` for this execution.

        A pre-existing lock is stale (and stolen) when its owner service is
        gone, or when it belongs to this very service with no live task
        process left behind by a crashed submission thread.  A lock held by
        a live foreign owner is a hard conflict.
        """
        lock_path = runtime_file(Path(record.work_dir), "run.lock")
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            self._resolve_existing_run_lock(record, lock_path)
        self._write_run_lock(lock_path, record, task_pid=None)
        self._run_locks[record.id] = lock_path

    def _resolve_existing_run_lock(self, record: JobRecord, lock_path: Path) -> None:
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        owner_pid = payload.get("owner_pid") if isinstance(payload, dict) else None
        owner_pid = int(owner_pid) if isinstance(owner_pid, int) else 0
        if pid_is_alive(owner_pid):
            if owner_pid == os.getpid() and not self._live_task_pids(record):
                logger.warning(
                    "Stolen stale run.lock for job %s (crashed submission thread)",
                    record.id,
                )
            else:
                raise RuntimeError(
                    f"run.lock for task {record.id} is held by live process {owner_pid}; "
                    "refusing to start a duplicate execution"
                )
            return
        logger.info("Stolen run.lock of dead service (pid=%s) for job %s", owner_pid, record.id)
        lock_path.unlink(missing_ok=True)

    def _release_run_lock(self, job_id: str) -> None:
        lock_path = self._run_locks.pop(job_id, None)
        if lock_path is None:
            return
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("Failed to remove run.lock for job %s", job_id, exc_info=True)

    # ------------------------------------------------------------------ #
    # Legacy blocking API (kept for backward compat)
    # ------------------------------------------------------------------ #

    def _should_run_remote(self, spec: JobSpec) -> bool:
        """True when this job should be dispatched to a remote compute node.

        Requires ``remote_runner`` to be configured (set by
        :class:`~acp.scheduler.manager.JobManager` when
        ``remote_config.is_remote``) **and** the workflow to be one that
        has a remote CLI mapping (``fake`` is always local).
        """
        if self.remote_runner is None:
            return False
        return spec.workflow != "fake"

    def run(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> int:
        """Run the job. Returns process exit code (0 = success)."""
        work_dir = Path(record.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        # Remote dispatch — the fake workflow always stays local so the
        # workbench remains demoable without QC binaries or SSH access.
        if self._should_run_remote(record.spec):
            assert self.remote_runner is not None
            return self.remote_runner.run(record, event_log, cancel_event)

        observer = self._observer_for_record(record)
        observer.initialize_job_stages(record.id, record.spec)
        event_log.append("job.started", job_id=record.id, workflow=record.spec.workflow)

        try:
            if record.spec.workflow == "fake":
                exit_code = self._run_fake(record, event_log, cancel_event)
            else:
                exit_code = self._run_subprocess(record, event_log, cancel_event)
        except Exception as exc:
            logger.exception("Job %s crashed", record.id)
            record.error = str(exc)
            event_log.append("job.failed", job_id=record.id, error=str(exc))
            observer.finalize_job(record.id, "failed")
            return 1

        if cancel_event.is_set():
            event_log.append("job.cancelled", job_id=record.id)
        elif exit_code == 0:
            event_log.append("job.completed", job_id=record.id, exit_code=exit_code)
        else:
            event_log.append("job.failed", job_id=record.id, exit_code=exit_code)

        final_status = "failed"
        if cancel_event.is_set() and exit_code != 0:
            final_status = "cancelled"
        elif exit_code == 0:
            final_status = "completed"
        observer.finalize_job(record.id, final_status)
        return exit_code

    def _run_subprocess(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> int:
        work_dir = Path(record.work_dir)
        inputs_dir = work_dir / "inputs"
        inputs_dir.mkdir(exist_ok=True)
        run_root = work_dir.parent.parent

        # Phase 5B: local disk-pressure housekeeping before materializing
        # input.  When the run_root filesystem is critically full the job
        # is rejected gracefully (exit_code=1) instead of running out of
        # disk mid-computation.  Local mode does NOT raise — there is no
        # "switch node" option, so we just fail the job.
        skip = self._pre_submit_housekeeping_local(record, event_log)
        if skip:
            return 1

        materialized = None
        materialized_roles: dict[str, Path] = {}
        try:
            materialized = materialize_job_input(
                record.spec.input,
                inputs_dir,
                run_root,
                materialized_roles,
            )
        except Exception as exc:
            event_log.append("job.failed", job_id=record.id, error=str(exc))
            return 1

        effective_input_path = (
            str(materialized) if materialized else _extract_input_source(record.spec.input)
        )
        cmd = self._build_cmd(
            record.spec,
            work_dir,
            effective_input_path,
            materialized_roles,
        )
        stdout_path = runtime_file(work_dir, "stdout.log")
        stderr_path = runtime_file(work_dir, "stderr.log")

        event_log.append("process.starting", job_id=record.id, cmd=" ".join(cmd))

        with (
            stdout_path.open("w", encoding="utf-8") as out,
            stderr_path.open("w", encoding="utf-8") as err,
        ):
            _write_log_header(record, work_dir, cmd, out, err)
            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                cmd,
                stdout=out,
                stderr=err,
                cwd=str(work_dir),
                env=env,
                start_new_session=True,
            )
            record.pid = proc.pid

        self._observer_for_record(record).initialize_job_stages(record.id, record.spec)
        exit_code = self._monitor(record, proc, event_log, cancel_event)
        if exit_code == 0:
            self._capture_artifacts(record, work_dir)
            self._capture_ensemble_thermo(record, work_dir)
            self._store_provenance(record, command_line=" ".join(cmd))
        return exit_code

    def _monitor(
        self,
        record: JobRecord,
        proc: subprocess.Popen[Any],
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> int:
        seen_stages: set[str] = set()
        observer = self._observer_for_record(record)

        while True:
            state_path = find_workflow_state(Path(record.work_dir))
            self._observe_state(record, event_log, state_path, seen_stages)
            observer.poll_and_mirror(record.id, Path(record.work_dir))
            if cancel_event.is_set():
                self._terminate(proc)
                break
            ret = proc.poll()
            if ret is not None:
                record.exit_code = ret
                state_path = find_workflow_state(Path(record.work_dir))
                self._observe_state(record, event_log, state_path, seen_stages)
                observer.poll_and_mirror(record.id, Path(record.work_dir))
                break
            time.sleep(_POLL_INTERVAL)

        if proc.poll() is None:
            proc.wait(timeout=10)
        return proc.returncode if proc.returncode is not None else 1

    def _observe_state(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        state_path: Path | None,
        seen: set[str],
    ) -> None:
        if state_path is None or not state_path.exists():
            return
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        if data.get("status") == "failed":
            return

        record.current_stage = data.get("current_stage")
        stages = data.get("stages") if isinstance(data.get("stages"), dict) else {}
        total = max(len(stages), 1)
        done = sum(
            1
            for stage in stages.values()
            if isinstance(stage, dict) and stage.get("status") in ("completed", "skipped")
        )
        record.progress = round(done / total, 3)

        # Extended schema: ProgressReporter provides blended overall progress
        overall = data.get("overall_progress")
        if isinstance(overall, (int, float)):
            record.progress = round(float(overall), 3)

        # Mirror the running stage onto its stage_tasks row so the job-detail
        # stepper can show live phase detail ("status_detail").
        if record.current_stage:
            try:
                observer = self._observer_for_record(record)
                for task in observer.store.list_by_job(record.id):
                    if (
                        task.stage_name == record.current_stage
                        and task.status_detail != record.current_stage
                    ):
                        task.status_detail = record.current_stage
                        observer.store.update(task)
            except Exception:
                logger.debug("status_detail mirror failed for job %s", record.id, exc_info=True)

        pending_events: list[tuple[float, str, str, dict[str, object]]] = []
        for name, info in stages.items():
            if not isinstance(info, dict):
                continue
            status = info.get("status")
            if status == "running" and f"running:{name}" not in seen:
                seen.add(f"running:{name}")
                ts = _safe_timestamp(info.get("started_at"))
                pending_events.append((ts, "stage.started", name, {"stage": name}))
            elif status == "completed" and f"done:{name}" not in seen:
                seen.add(f"done:{name}")
                ts = _safe_timestamp(info.get("completed_at"))
                pending_events.append((ts, "stage.completed", name, {"stage": name}))
            elif status == "failed" and f"failed:{name}" not in seen:
                seen.add(f"failed:{name}")
                ts = _safe_timestamp(info.get("completed_at"))
                pending_events.append(
                    (ts, "stage.failed", name, {"stage": name, "error": str(info.get("error", ""))})
                )

        for _ts, event_type, _name, payload in sorted(pending_events, key=lambda x: x[0]):
            event_log.append(event_type, job_id=record.id, **payload)

    def _terminate(self, proc: subprocess.Popen[Any]) -> None:
        if proc.poll() is not None:
            return
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return
        try:
            # Defensive SIGCONT: a SIGSTOP-frozen group cannot act on
            # SIGTERM (handlers never run while stopped) — revive it first
            # so the terminate handshake and SIGKILL escalation both work.
            try:
                os.killpg(pgid, signal.SIGCONT)
            except ProcessLookupError:
                pass
            os.killpg(pgid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(pgid, signal.SIGKILL)
                proc.wait(timeout=5)
        except OSError:
            logger.warning("Failed to terminate subprocess", exc_info=True)

    def _pre_submit_housekeeping_local(
        self,
        record: JobRecord,
        event_log: JobEventLog,
    ) -> bool:
        """Run local disk-pressure housekeeping before a local submission.

        Delegates to the injected :class:`LocalCleanup` (Phase 5B) when
        one is configured.  Fail-open for transient errors: a stat or
        sweep failure is logged but does **not** block submission.  Only
        the explicit ``should_skip`` decision blocks it.

        Unlike the remote path, the local branch **returns** ``True`` to
        signal "skip submission" rather than raising — there is no other
        node to fall back to, so the job is simply marked FAILED with a
        disk-full error message.

        Returns:
            ``True`` when submission must be skipped (disk full).
        """
        if self.local_cleanup is None:
            return False

        try:
            decision = self.local_cleanup.pre_submit_housekeeping()
        except Exception as exc:
            # Defensive: housekeeping must never crash the job.  Log and
            # proceed (fail-open).
            logger.warning("Local housekeeping crashed: %s", exc)
            event_log.append(
                "local.housekeeping_error",
                job_id=record.id,
                error=str(exc),
            )
            return False

        removed = len(decision.cleanup.work_dirs_removed) if decision.cleanup else 0
        errors = len(decision.cleanup.errors) if decision.cleanup else 0
        event_log.append(
            "local.housekeeping",
            job_id=record.id,
            disk_before=decision.disk_usage_before,
            disk_after=decision.disk_usage_after,
            should_skip=decision.should_skip,
            removed_dirs=removed,
            cleanup_errors=errors,
            reason=decision.reason,
        )

        if decision.should_skip:
            record.error = (
                f"Local disk full ({decision.disk_usage_after}%), "
                f"submission blocked: {decision.reason}"
            )
            logger.warning("Job %s blocked by disk pressure: %s", record.id, decision.reason)
            return True

        return False

    def _build_cmd(
        self,
        spec: JobSpec,
        work_dir: Path,
        input_path: str = "",
        materialized_roles: Mapping[str, Path | str] | None = None,
    ) -> list[str]:
        wf = spec.workflow
        if wf not in (
            "Confsearch",
            "PESsearch",
            "BatchOptimize",
            "ensemble",
            "energy",
            "nmr",
            "xtbmd_censo_energy",
            "singlepoint",
            "optimize",
            "frequency",
            "scan",
            "irc",
            "xtb_optimize",
        ):
            raise ValueError(f"No subprocess mapping for workflow: {wf}")

        cmd: list[str] = [self.python, "-m", "acp.cli", "run", wf]
        inp = spec.input
        method = spec.method
        res = spec.resources
        cli_work_dir = work_dir.as_posix()

        source = input_path or _extract_input_source(inp)
        if wf == "PESsearch":
            return self._build_pessearch_cmd(spec, work_dir, source)
        if wf == "nmr":
            return self._build_nmr_cmd(spec, work_dir)

        if wf == "BatchOptimize":
            artifact = inp.get("from_artifact")
            items_file = inp.get("items_file")
            if artifact:
                cmd += ["--from-artifact", str(artifact), "--output", cli_work_dir]
            elif items_file:
                cmd += ["--items-file", str(items_file), "--output", cli_work_dir]
            elif input_path and Path(input_path).is_file():
                # The Workbench creates one scheduler job per structure.  Its
                # materialized input.xyz is therefore a valid one-item XYZ
                # batch for the BatchOptimize CLI.  Keep this task flat, just
                # like the ordinary single-structure workflows.
                # Prefer the scheduler's canonical root input.xyz even when
                # the uploaded source had another suffix (e.g. .gjf).
                staged_input = work_dir / "input.xyz"
                batch_input = staged_input if staged_input.is_file() else Path(input_path)
                cmd += ["--items-file", str(batch_input), "--output", cli_work_dir]
            else:
                raise ValueError(
                    "BatchOptimize job requires a batch artifact, items file, or "
                    "materialized structure"
                )
            # Scheduler tasks are one-structure-per-task (layout spec §1a):
            # every dispatch shape must produce the flat WORK/03_OPT…
            # layout, never the nested WORK/03_OPT/batch/item_001 tree.
            cmd += ["--layout-mode", "single_flat"]
            cmd += batchoptimize_method_flags(method, inp)
            if spec.config_path:
                cmd += ["--config", str(spec.config_path)]
            if res.get("nproc") is not None:
                cmd += ["--nproc", str(res["nproc"])]
            if res.get("mem"):
                cmd += ["--mem", str(res["mem"])]
            cmd += input_chemistry_flags(inp)
            return cmd

        if not source:
            raise ValueError(f"{wf} job requires a valid input structure")

        if wf == "Confsearch":
            cmd += ["--input", str(source), "--output", cli_work_dir]
            if spec.name:
                cmd += ["--name", spec.name]
            cmd += confsearch_method_flags(method)
            solvent = censo_solvent_from_method(method)
            if solvent:
                cmd += ["--solvent", solvent]
            ewin = censo_ewin_from_method(method)
            if ewin is not None:
                cmd += ["--ewin", str(ewin)]
        elif wf in {"ensemble", "energy"}:
            cmd += ["--input", str(source), "--output", cli_work_dir]
            preset = censo_preset_from_method(method)
            if preset:
                cmd += ["--preset", preset]
            if spec.name:
                cmd += ["--name", spec.name]
            if wf == "energy" and method.get("no_opt"):
                cmd += ["--no-opt"]
            if wf == "energy" and method.get("rank1_only"):
                cmd += ["--rank1-only"]
            if wf == "energy" and method.get("rank1_only") is False:
                # CLI defaults to rank1-only; an explicit opt-out must be
                # forwarded so the full-ensemble path is restored.
                cmd += ["--full-ensemble"]
            if wf == "energy" and method.get("threshold") is not None:
                cmd += ["--threshold", str(method["threshold"])]
            if wf == "energy" and method.get("levels"):
                cmd += ["--levels", json.dumps(method["levels"])]
            if wf == "ensemble" and method.get("keep_all"):
                cmd += ["--keep-all"]
            solvent = censo_solvent_from_method(method)
            if solvent:
                cmd += ["--solvent", solvent]
            ewin = censo_ewin_from_method(method)
            if ewin is not None:
                cmd += ["--ewin", str(ewin)]
        elif wf == "xtbmd_censo_energy":
            cmd += ["--input", str(source), "--output", cli_work_dir]
            preset = censo_preset_from_method(method)
            if preset:
                cmd += ["--preset", preset]
            if spec.name:
                cmd += ["--name", spec.name]
            if method.get("levels"):
                cmd += ["--levels", json.dumps(method["levels"])]
            # MD / batch-opt / ISOSTAT / conv / resume control group —
            # single shared flag builder keeps this branch in parity with
            # the remote script_gen path (E7, DevDoc §10.2).
            cmd += xtbmd_method_flags(method)
            solvent = censo_solvent_from_method(method)
            if solvent:
                cmd += ["--solvent", solvent]
            ewin = censo_ewin_from_method(method)
            if ewin is not None:
                # GFN1 energy window for this workflow (CLI --ewin);
                # the same flag name, different object vs. energy.
                cmd += ["--ewin", str(ewin)]
        elif wf in ("singlepoint", "optimize", "frequency", "scan"):
            cmd += ["--input", str(source), "--output", cli_work_dir]
            if spec.name:
                cmd += ["--name", spec.name]
            levels = method.get("levels", {})
            if levels:
                from acp.catalog import method_levels_to_cli_flags

                cmd += method_levels_to_cli_flags(levels)
            if wf == "scan":
                cmd += scan_method_flags(method, inp)
        elif wf == "irc":
            cmd += ["--input", str(source), "--output", cli_work_dir]
            if spec.name:
                cmd += ["--name", spec.name]
            input_role = inp.get("input_role")
            if input_role:
                cmd += ["--input-role", str(input_role)]
            raw_directions = inp.get("directions")
            if isinstance(raw_directions, (list, tuple)):
                direction_names = {str(direction).strip().lower() for direction in raw_directions}
                if direction_names == {"forward"}:
                    cmd += ["--direction", "forward"]
                elif direction_names == {"reverse"}:
                    cmd += ["--direction", "reverse"]
                elif direction_names in ({"forward", "reverse"}, {"both"}):
                    cmd += ["--direction", "both"]
                elif direction_names:
                    raise ValueError("irc directions must be forward, reverse, or both")
            levels = method.get("levels")
            irc_level = levels.get("irc", {}) if isinstance(levels, Mapping) else {}
            if not isinstance(irc_level, Mapping):
                irc_level = {}
            irc_method = method.get("method") or method.get("functional") or irc_level.get("method")
            if irc_method:
                cmd += ["--method", str(irc_method)]
            irc_basis = method.get("basis") or irc_level.get("basis")
            if irc_basis:
                cmd += ["--basis", str(irc_basis)]
            maxpoints = method.get("maxpoints") or irc_level.get("maxpoints")
            if maxpoints is not None:
                cmd += ["--maxpoints", str(maxpoints)]
            irc_step = method.get("step") or irc_level.get("step")
            if irc_step is not None:
                cmd += ["--step", str(irc_step)]
        elif wf == "xtb_optimize":
            cmd += ["--input", str(source), "--output", cli_work_dir]
            if spec.name:
                cmd += ["--name", spec.name]
            xtb_level = (method.get("levels") or {}).get("xtb_opt", {})
            gfn_val = xtb_level.get("gfn")
            if gfn_val is not None:
                gfn_int = _GFN_DISPLAY_TO_INT.get(str(gfn_val), gfn_val)
                cmd += ["--gfn", str(gfn_int)]
            if xtb_level.get("opt_level"):
                cmd += ["--opt-level", str(xtb_level["opt_level"])]
            if xtb_level.get("max_steps") is not None:
                cmd += ["--max-steps", str(xtb_level["max_steps"])]
            if xtb_level.get("solvent"):
                cmd += ["--solvent", str(xtb_level["solvent"])]
            sm = xtb_level.get("solvent_model")
            if sm and str(sm).lower() not in ("", "none"):
                cmd += ["--solvent-model", str(sm)]

        if spec.config_path and wf != "mech-chain":
            cmd += ["--config", str(spec.config_path)]
        if res.get("nproc") is not None:
            cmd += ["--nproc", str(res["nproc"])]
        if res.get("mem"):
            cmd += ["--mem", str(res["mem"])]
        cmd += input_chemistry_flags(inp)
        return cmd

    def _build_nmr_cmd(self, spec: JobSpec, work_dir: Path) -> list[str]:
        """Build the ``acp run nmr`` argv (multi-candidate + spectrum).

        Payload convention (DevDoc §11.1):
        ``spec.input = {"candidates": [{source_type, source, charge, ...}],
                        "experiment": {"content": "<§6.2 text>"}}``.
        For backwards compatibility a single-candidate ``spec.input`` is
        also accepted (treated as a one-element candidate list).
        """
        cmd: list[str] = [self.python, "-m", "acp.cli", "run", "nmr"]
        cmd += ["--output", work_dir.as_posix()]

        inp = spec.input
        method = spec.method
        res = spec.resources

        inputs_dir = work_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)

        candidates = inp.get("candidates") if isinstance(inp.get("candidates"), list) else None
        if candidates is None:
            # legacy single-candidate payload
            candidates = [inp]

        enumerate_mode = bool(inp.get("enumerate"))
        for idx, cand in enumerate(candidates):
            if not isinstance(cand, dict):
                continue
            cand_source = cand.get("source") or cand.get("smiles") or cand.get("input")
            if not cand_source:
                continue
            if enumerate_mode and _looks_like_smiles(str(cand_source)):
                # Enumerate needs bond information (stereochemistry is
                # topological): pass SMILES through verbatim instead of
                # materializing to XYZ (which has no bond table).
                cmd += ["--input", str(cand_source)]
                continue
            materialized = materialize_job_input(cand, inputs_dir, work_dir)
            if materialized is not None:
                cmd += ["--input", str(materialized)]
            else:
                # fall back to the raw source (SMILES strings survive)
                cmd += ["--input", str(cand_source)]

        experiment = inp.get("experiment") or method.get("experiment")
        exp_mode = (experiment or {}).get("mode", "assigned")
        if exp_mode == "bruker":
            # P3: Bruker raw-data zip uploaded via /uploads?parse=false.
            bruker_dir = self._materialize_bruker_asset(experiment, inputs_dir, work_dir)
            if bruker_dir is None:
                raise ValueError(
                    "nmr bruker job requires experiment.spectrum_asset_id (+ filename)"
                )
            cmd += ["--bruker", str(bruker_dir)]
            refs = experiment.get("references") if isinstance(experiment, dict) else None
            if isinstance(refs, dict):
                for key, value in refs.items():
                    cmd += ["--bruker-ref", f"{key}={value}"]
        else:
            spectrum_path = self._materialize_experiment(experiment, inputs_dir)
            if spectrum_path is None:
                raise ValueError("nmr job requires an 'experiment' payload (spectrum text)")
            cmd += ["--spectrum", str(spectrum_path)]

        # P2: diastereomer enumeration (single-candidate payload only).
        # The backend expands the one candidate into its full diastereomer
        # set; enantiomer pairs collapse (DP4 cannot distinguish them).
        if enumerate_mode:
            cmd += ["--enumerate"]
            stereocenters = inp.get("stereocenters")
            if isinstance(stereocenters, str) and stereocenters.strip():
                cmd += ["--stereocenters", stereocenters.strip()]
            elif isinstance(stereocenters, list) and stereocenters:
                cmd += ["--stereocenters", ",".join(str(s) for s in stereocenters)]

        if spec.name:
            cmd += ["--name", spec.name]
        preset = censo_preset_from_method(method)
        if preset:
            cmd += ["--preset", preset]
        cmd += nmr_method_flags(method)
        solvent = censo_solvent_from_method(method)
        if solvent:
            cmd += ["--solvent", solvent]
        ewin = censo_ewin_from_method(method)
        if ewin is not None:
            cmd += ["--ewin", str(ewin)]

        if spec.config_path:
            cmd += ["--config", str(spec.config_path)]
        if res.get("nproc") is not None:
            cmd += ["--nproc", str(res["nproc"])]
        if res.get("mem"):
            cmd += ["--mem", str(res["mem"])]
        return cmd

    def _build_pessearch_cmd(self, spec: JobSpec, work_dir: Path, source: str) -> list[str]:
        """Build the PESsearch argv for local execution."""
        inp = spec.input
        method = spec.method
        res = spec.resources
        bond_scan_mode = str(method.get("mode") or "") == "bond_length_scan"

        cmd: list[str] = [self.python, "-m", "acp.cli", "run", "PESsearch"]
        if bond_scan_mode:
            cmd += ["--mode", "bond_length_scan"]
            scan_request = dict(inp.get("scan_request") or method.get("scan_request") or {})
            work_dir.mkdir(parents=True, exist_ok=True)
            scan_config_path = work_dir / SCAN_CONFIG_FILENAME
            scan_config_path.write_text(
                json.dumps(scan_request, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            cmd += [
                "--scan-config",
                scan_config_path.as_posix(),
                "--output",
                work_dir.as_posix(),
            ]
        else:
            from_manifest = inp.get("from")
            if from_manifest:
                cmd += ["--from", str(from_manifest)]
            cmd += ["--output", work_dir.as_posix()]
            if method.get("strategy"):
                cmd += ["--strategy", str(method["strategy"])]
            select = method.get("select")
            if isinstance(select, (list, tuple)) and select:
                cmd += ["--select", ",".join(str(item) for item in select)]
            elif isinstance(select, str) and select.strip():
                cmd += ["--select", select.strip()]
            plan = inp.get("coordinate_plan") or method.get("coordinate_plan")
            if plan is not None:
                cmd += ["--plan", json.dumps(plan)]
            product = inp.get("product")
            if product:
                cmd += ["--product", str(product)]
            ts_guess = inp.get("ts_guess")
            if ts_guess:
                cmd += ["--ts-guess", str(ts_guess)]

        if spec.config_path:
            cmd += ["--config", str(spec.config_path)]
        if res.get("nproc") is not None:
            cmd += ["--nproc", str(res["nproc"])]
        if res.get("mem"):
            cmd += ["--mem", str(res["mem"])]
        cmd += input_chemistry_flags(inp)
        return cmd

    @staticmethod
    def _materialize_experiment(
        experiment: dict[str, Any] | None,
        inputs_dir: Path,
    ) -> Path | None:
        """Write the §6.2 spectrum text to ``inputs/experiment.txt``."""
        if experiment is None:
            return None
        content = experiment.get("content")
        if not content:
            return None
        inputs_dir.mkdir(parents=True, exist_ok=True)
        out = inputs_dir / "experiment.txt"
        out.write_text(str(content), encoding="utf-8")
        return out

    @staticmethod
    def _materialize_bruker_asset(
        experiment: dict[str, Any] | None,
        inputs_dir: Path,
        work_dir: Path,
    ) -> Path | None:
        """Resolve + extract an uploaded Bruker zip into ``inputs/bruker``.

        The frontend uploads the zip via ``/uploads?parse=false`` and
        submits ``experiment = {mode: "bruker", spectrum_asset_id,
        filename, project_id}``. The asset lives at
        ``<run_root>/<project_id>/uploads/<upload_id>/original/<filename>``
        (``run_root`` derives from the job work dir, mirroring
        ``_run_subprocess``). Non-zip assets (an already-extracted
        directory tarball layout) are rejected with a clear error.
        """
        if not experiment:
            return None
        upload_id = experiment.get("spectrum_asset_id")
        filename = experiment.get("filename")
        if not upload_id or not filename:
            return None
        if Path(str(filename)).name != str(filename):
            raise ValueError(f"Unsafe bruker asset filename: {filename!r}")
        project_id = str(experiment.get("project_id") or "uncategorized")
        if project_id in ("", ".", "..") or "/" in project_id or "\\" in project_id:
            raise ValueError(f"Unsafe bruker asset project_id: {project_id!r}")

        run_root = work_dir.parent.parent
        asset = (
            run_root / project_id / "uploads" / str(upload_id) / "original" / str(filename)
        ).resolve()
        try:
            asset.relative_to(run_root.resolve())
        except ValueError:
            raise ValueError(f"Bruker asset path escapes run_root: {asset}")
        if not asset.is_file():
            raise ValueError(f"Bruker asset not found: {asset}")
        if asset.suffix.lower() != ".zip":
            raise ValueError(f"Bruker asset must be a .zip archive: {asset.name}")

        import zipfile

        dest = inputs_dir / "bruker"
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(asset) as zf:
            for member in zf.namelist():
                target = (dest / member).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise ValueError(f"Unsafe path in bruker zip: {member!r}")
            zf.extractall(dest)
        return dest

    def _run_fake(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> int:
        from acp.core.state import WorkflowState

        work_dir = Path(record.work_dir)
        state = WorkflowState(work_dir=work_dir, job_name=record.spec.name or record.id)
        state.initialize(input_source="fake", stage_names=["init", "compute", "finalize"])
        observer = self._observer_for_record(record)
        tasks = observer.initialize_job_stages(record.id, record.spec)
        tasks_by_stage = {task.stage_name: task for task in tasks}
        stages = ["init", "compute", "finalize"]

        try:
            for name in stages:
                if cancel_event.is_set():
                    return 130

                task = tasks_by_stage.get(name)
                if task is None:
                    tasks = observer.initialize_job_stages(record.id, record.spec)
                    tasks_by_stage = {item.stage_name: item for item in tasks}
                    task = tasks_by_stage.get(name)
                if task is None:
                    raise ValueError(f"Missing stage task row for fake stage '{name}'")

                started_at = _utc_now_iso()
                self._write_stage_task_event(
                    work_dir,
                    name,
                    task.task_id,
                    "started",
                    {
                        "task_id": task.task_id,
                        "stage": name,
                        "status": "running",
                        "pid": os.getpid(),
                        "started_at": started_at,
                    },
                )
                observer.poll_and_mirror(record.id, work_dir)
                state.set_stage(name)
                event_log.append("stage.started", job_id=record.id, stage=name)
                event_log.append(
                    "log", job_id=record.id, stream="stdout", line=f"[fake] running {name}"
                )
                time.sleep(0.8)
                if cancel_event.is_set():
                    return 130

                state.complete_stage(name, result={"fake": True})
                self._write_stage_task_event(
                    work_dir,
                    name,
                    task.task_id,
                    "completed",
                    {
                        "task_id": task.task_id,
                        "stage": name,
                        "status": "completed",
                        "pid": os.getpid(),
                        "started_at": started_at,
                        "completed_at": _utc_now_iso(),
                        "exit_code": 0,
                        "result": {"fake": True},
                    },
                )
                observer.poll_and_mirror(record.id, work_dir)
                event_log.append("stage.completed", job_id=record.id, stage=name)

            source_type = record.spec.input.get("source_type", "")
            source = (
                record.spec.input.get("source")
                or record.spec.input.get("input")
                or record.spec.input.get("smiles")
            )
            if not source:
                raise ValueError("fake workflow requires input.source")

            if source_type == "xyz_text":
                xyz = str(source)
                if not xyz.strip().startswith(str(xyz.count("\n"))):
                    pass
            elif source_type == "structure_asset":
                run_root = Path(record.work_dir).parent.parent
                asset_path = (run_root / str(source)).resolve()
                try:
                    asset_path.relative_to(run_root.resolve())
                    xyz = asset_path.read_text(encoding="utf-8")
                except (ValueError, OSError) as exc:
                    raise ValueError(f"Cannot read structure asset: {source}") from exc
            else:
                xyz = smiles_to_xyz(str(source))

            (TaskStorage(work_dir).result_dir() / "input_preview.xyz").write_text(
                xyz,
                encoding="utf-8",
            )

            demo_frames = bool(record.spec.input.get("demo_frames"))
            if demo_frames:
                demo_xyz = xyz_to_multiframe_demo(xyz, frames=3)
                (TaskStorage(work_dir).result_dir() / "demo_frames.xyz").write_text(
                    demo_xyz,
                    encoding="utf-8",
                )

            method_levels = record.spec.method.get("levels") if record.spec.method else None
            if method_levels:
                from acp.catalog import method_levels_to_workflow_config

                schema_id = record.spec.method.get("schema_id", "confsearch")
                wf_config = method_levels_to_workflow_config(
                    method_levels, schema_id, record.spec.workflow
                )
                (TaskStorage(work_dir).result_dir() / "method_config.json").write_text(
                    json.dumps(wf_config, indent=2),
                    encoding="utf-8",
                )

            self._capture_artifacts(record, work_dir)
            self._store_provenance(record)
            state.mark_completed()
            return 0
        except Exception as exc:
            current = str(state.state.get("current_stage") or "unknown")
            error_message = str(exc)
            state.fail_stage(current, error=error_message)
            task = tasks_by_stage.get(current)
            if task is not None:
                self._write_stage_task_event(
                    work_dir,
                    current,
                    task.task_id,
                    "failed",
                    {
                        "task_id": task.task_id,
                        "stage": current,
                        "status": "failed",
                        "pid": os.getpid(),
                        "completed_at": _utc_now_iso(),
                        "exit_code": 1,
                        "error": error_message,
                    },
                )
                observer.poll_and_mirror(record.id, work_dir)
            raise

    def _observer_for_record(self, record: JobRecord) -> StageTaskObserver:
        if self.stage_task_observer is None:
            fallback_db = Path(record.work_dir) / ".acp_stage_tasks.db"
            self.stage_task_observer = StageTaskObserver(StageTaskStore(fallback_db))
        return self.stage_task_observer

    def _capture_artifacts(self, record: JobRecord, work_dir: Path) -> None:
        """Register output files as artifacts after job completion."""
        observer = self.stage_task_observer
        store = getattr(observer, "store", None) if observer is not None else None
        db_path = getattr(store, "db_path", None)
        results_dir = TaskStorage(work_dir).result_dir()
        if db_path is None or not results_dir.exists():
            return

        registry = ArtifactRegistry(db_path)
        artifacts = capture_stage_artifacts(
            registry=registry,
            job_id=record.id,
            task_id=None,
            work_dir=work_dir,
            stage_dir=results_dir,
        )
        if not artifacts:
            return

        result = dict(record.result or {})
        result["artifacts"] = [asdict(artifact) for artifact in artifacts]
        record.result = result

    def _capture_ensemble_thermo(self, record: JobRecord, work_dir: Path) -> None:
        """Merge the energy workflow's ensemble_thermo.json into the job result.

        Makes ``total_gibbs_kcal_mol`` (and friends) available to the
        workbench info panel without re-parsing the workflow output files.
        The canonical ``RESULT/energies/ensemble_thermo.json`` is preferred;
        historical ``finalDFT/ensemble_thermo.json`` is accepted as a
        read-only fallback (single-mol jobs nest one level under ``work_dir``).
        """
        canonical = sorted(
            work_dir.rglob("RESULT/energies/ensemble_thermo.json"),
            key=lambda p: len(p.parts),
        )
        legacy = sorted(
            work_dir.rglob("finalDFT/ensemble_thermo.json"),
            key=lambda p: len(p.parts),
        )
        candidates = canonical or legacy
        if not candidates:
            return
        try:
            data = json.loads(candidates[0].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        result = dict(record.result or {})
        result["ensemble_thermo"] = data
        record.result = result

    def _store_provenance(self, record: JobRecord, command_line: str = "") -> None:
        if record.completed_at is None:
            record.completed_at = _utc_now_iso()
        result = dict(record.result or {})
        if command_line:
            result["command_line"] = command_line
        if record.spec.workflow == "fake":
            result.setdefault("backend_name", "fake")
            result.setdefault("method", "demo")
        elif "backend_name" not in result and record.spec.method.get("backend") is not None:
            result["backend_name"] = str(record.spec.method["backend"])
        if "method" not in result:
            method = record.spec.method.get("protocol")
            if method is not None:
                result["method"] = str(method)
        record.result = result

        provenance = build_provenance_for_job(record.spec, record)
        record.result = dict(record.result or {})
        record.result["provenance"] = asdict(provenance)
        self._persist_provenance_to_stage_tasks(record, provenance)

    def _persist_provenance_to_stage_tasks(self, record: JobRecord, provenance: Provenance) -> None:
        observer = self.stage_task_observer
        store = getattr(observer, "store", None) if observer is not None else None
        if store is None:
            return
        tasks = store.list_by_job(record.id)
        if not tasks:
            return
        task = tasks[-1]
        task.provenance = asdict(provenance)
        task.updated_at = _utc_now_iso()
        store.update(task)

    def _write_stage_task_event(
        self,
        work_dir: Path,
        stage_name: str,
        task_id: str,
        marker: str,
        payload: dict[str, Any],
    ) -> None:
        stage_dir = work_dir / "stage_tasks" / stage_name
        stage_dir.mkdir(parents=True, exist_ok=True)
        path = stage_dir / f"{task_id}.{marker}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


__all__ = [
    "JobRunner",
    "JobRunnerRemoteProtocol",
    "find_workflow_state",
    "materialize_job_input",
]
