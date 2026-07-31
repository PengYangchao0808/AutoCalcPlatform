"""
Remote Job Runner
=================

Core remote execution orchestrator.  Submits an ACP job to a remote
OpenLAVA (LSF) compute node over SSH/SFTP and monitors it to completion.

The full flow:

1. **Sync code** to the selected node (if ``auto_sync`` and code changed).
2. **Select node** — explicit ``target_node`` or least-loaded.
3. **Materialise + upload input** — write ``input.xyz`` locally, SFTP to
   ``inputs/``.
4. **Generate + upload LSF script** — BSUB preamble + ``acp.cli`` command.
5. **Submit** — SSH ``bsub < submit.lsf``, parse the LSF job ID.
6. **Monitor** (15 s poll):
   * ``bjobs`` status → update ``record`` + ``remote_execution`` stage.
   * SFTP tail ``stdout.log`` / ``stderr.log`` → ``JobEventLog`` events.
   * Periodic SFTP read ``state.json`` → fine-grained stage progress
     (``current_stage``, ``progress``, per-stage events).
   * Check ``cancel_event`` → ``bkill`` (return value checked).
   * Read ``.exit_code`` on termination.
7. **Finish** — set ``record.result`` with LSF metadata, build provenance.
   **No files are downloaded** — results stay on the remote node.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import posixpath
import re
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.provenance import build_provenance_for_job
from acp.scheduler.remote.cleanup import RemoteCleanup
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.monitor import STATUS_DONE, RemoteJobMonitor
from acp.scheduler.remote.script_gen import (
    build_lsf_script_spec,
    generate_lsf_script,
)
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool, SSHExecutionError
from acp.scheduler.remote.sync import CodeSyncer
from acp.scheduler.runner import materialize_job_input
from acp.scheduler.stage_tasks import StageTask, StageTaskObserver

logger = logging.getLogger(__name__)

__all__ = [
    "RemoteJobRunner",
    "RemoteNodeUnavailableError",
    "RemoteSubmissionError",
]

_LSF_JOB_ID_RE = re.compile(r"Job <(\d+)>")
# Maximum log lines emitted per poll cycle to avoid event explosion.
_MAX_LOG_LINES_PER_POLL = 1000
# Seconds to wait for .exit_code to appear after LSF reports terminal state.
_EXIT_CODE_GRACE = 30
# Extra buffer (seconds) added on top of the configured walltime before a
# monitor loop is force-timed-out.  Prevents an indefinite loop if the LSF
# daemon dies or the node goes offline (plan P1-3).
_MONITOR_TIMEOUT_BUFFER = 3600
# Fallback monitor timeout when walltime is unparseable (10 days).
_MONITOR_TIMEOUT_FALLBACK = 10 * 24 * 3600
# Read state.json on every poll cycle for real-time remote progress.
_STATE_READ_INTERVAL = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_timestamp_dt(value: object) -> float:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def _missing_exit_code_error(lsf_status: str, lsf_job_id: str) -> str:
    """Error text for when LSF is terminal but no ``.exit_code`` was written.

    This is the hallmark of a job killed by LSF (e.g. the walltime /
    ``RUNLIMIT`` limit sends a signal to the whole process group) before
    the trailing ``echo $? > .exit_code`` in the wrapper script can run.
    """
    return (
        f"Remote LSF job {lsf_job_id} reached terminal state '{lsf_status}' "
        f"without writing .exit_code \u2014 it was most likely killed by LSF "
        f"(e.g. walltime/RUNLIMIT limit or a process-group signal) before "
        f"the exit code could be recorded"
    )


class RemoteNodeUnavailableError(RuntimeError):
    """No suitable remote node is available for job dispatch."""


class RemoteSubmissionError(RuntimeError):
    """LSF job submission (``bsub``) failed or produced no job ID."""


class RemoteJobRunner:
    """Submit and monitor a single ACP job on a remote LSF node.

    This runner is a drop-in alternative to :class:`JobRunner.run` for
    remote execution.  It shares the same ``(record, event_log,
    cancel_event) -> int`` signature so the :class:`JobManager` can
    dispatch to either transparently.
    """

    def __init__(
        self,
        ssh_pool: SSHConnectionPool,
        remote_config: RemoteExecutionConfig,
        stager: FileStager | None = None,
        monitor: RemoteJobMonitor | None = None,
        code_syncer: CodeSyncer | None = None,
        cleanup: RemoteCleanup | None = None,
        stage_task_observer: StageTaskObserver | None = None,
        poll_interval: int | None = None,
    ) -> None:
        self._ssh = ssh_pool
        self._config = remote_config
        self._stager = stager or FileStager(ssh_pool)
        self._monitor = monitor or RemoteJobMonitor(ssh_pool, self._stager)
        self._syncer = code_syncer or CodeSyncer(ssh_pool)
        self._cleanup = cleanup
        self._observer = stage_task_observer
        self._poll_interval = (
            poll_interval if poll_interval is not None else remote_config.poll_interval
        )
        # Per-job cache of the ``remote_execution`` stage task id so we avoid
        # a full ``list_by_job`` scan on every 15 s poll cycle (plan P2-8).
        self._remote_stage_task_ids: dict[str, str] = {}
        # Per-job state for poller-driven (non-blocking) execution.
        self._job_states: dict[str, dict[str, object]] = {}

    # ------------------------------------------------------------------ #
    # Non-blocking poller-driven API
    # ------------------------------------------------------------------ #

    def submit_remote(
        self,
        record: JobRecord,
        event_log: JobEventLog,
    ) -> str:
        """Prepare and submit the LSF job, return the LSF job ID immediately.

        Executes steps 1-5 (node selection, housekeeping, binary probe,
        code sync, upload, LSF script, ``bsub``).  Does **not** enter
        the monitor loop — that is driven by :meth:`poll_remote`.
        """
        spec = record.spec
        work_dir = Path(record.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        event_log.append("job.started", job_id=record.id, workflow=spec.workflow, mode="remote")

        node = self.select_node(spec)
        event_log.append("remote.node_selected", job_id=record.id, node=node.name, host=node.host)

        self._pre_submit_housekeeping(node, event_log, record.id)
        self._probe_required_binaries(node, spec, event_log, record.id)

        if self._config.auto_sync:
            self._sync_code_if_needed(node, event_log, record.id)

        remote_job_dir = posixpath.join(node.remote_work_dir, record.id)

        try:
            lsf_job_id, cli_cmd = self._prepare_and_submit(
                record, spec, node, remote_job_dir, event_log, work_dir
            )
        except Exception:
            self._cleanup_remote_dir(node, remote_job_dir, event_log, record.id)
            raise

        self._set_remote_stage_state(record.id, "running", started=True)

        stdout_offset = 0
        stderr_offset = 0
        self._tail_and_emit(
            node, remote_job_dir, "stdout.log", stdout_offset, event_log, record.id, "stdout"
        )
        self._tail_and_emit(
            node, remote_job_dir, "stderr.log", stderr_offset, event_log, record.id, "stderr"
        )

        self._job_states[record.id] = {
            "node": node,
            "remote_job_dir": remote_job_dir,
            "lsf_job_id": lsf_job_id,
            "stdout_offset": stdout_offset,
            "stderr_offset": stderr_offset,
            "cli_cmd": cli_cmd,
            "poll_cycle": 0,
            "seen_stages": set(),
        }

        # Persist recovery metadata immediately so the poller can
        # reconnect after a server restart, even before the first poll.
        result = dict(record.result or {})
        result["lsf_job_id"] = lsf_job_id
        result["node"] = node.name
        result["remote_dir"] = remote_job_dir
        result["command_line"] = " ".join(cli_cmd)
        record.result = result

        return lsf_job_id

    def poll_remote(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> tuple[bool, int | None]:
        """Single non-blocking check of remote job status.

        Checks ``.exit_code`` first (authoritative), then ``bjobs``
        (LSF state).  Tails logs and periodically reads ``state.json``
        for fine-grained stage progress.

        Returns ``(is_terminal, exit_code)``.
        """
        state = self._job_states.get(record.id)
        if state is None:
            return (True, record.exit_code if record.exit_code is not None else 1)

        node: RemoteNode = state["node"]  # type: ignore[assignment]
        remote_job_dir: str = state["remote_job_dir"]  # type: ignore[assignment]
        lsf_job_id: str = state["lsf_job_id"]  # type: ignore[assignment]
        stdout_offset: int = state["stdout_offset"]  # type: ignore[assignment]
        stderr_offset: int = state["stderr_offset"]  # type: ignore[assignment]
        poll_cycle: int = state.get("poll_cycle", 0)  # type: ignore[assignment]
        seen_stages: set[str] = state.get("seen_stages", set())  # type: ignore[assignment]

        if cancel_event.is_set():
            if not state.get("cancel_sent"):
                ok = self._monitor.cancel_job(node, lsf_job_id)
                event_log.append(
                    "remote.cancel_sent",
                    job_id=record.id,
                    lsf_job_id=lsf_job_id,
                    bkill_ok=ok,
                )
                state["cancel_sent"] = True
            exit_code = self._wait_exit_code(node, remote_job_dir, timeout=_EXIT_CODE_GRACE)
            self._cleanup_job_state(record.id)
            return (True, exit_code if exit_code is not None else 130)

        exit_code = self._monitor.get_exit_code(node, remote_job_dir)
        if exit_code is not None:
            return self._finalize_remote(
                record, event_log, state, node, remote_job_dir, lsf_job_id,
                exit_code, seen_stages,
            )

        # --- Periodic state.json read (every _STATE_READ_INTERVAL cycles) ---
        poll_cycle += 1
        state["poll_cycle"] = poll_cycle
        if poll_cycle % _STATE_READ_INTERVAL == 0:
            try:
                self._observe_remote_state(record, event_log, node, remote_job_dir, seen_stages)
                state["seen_stages"] = seen_stages
            except Exception:
                logger.debug("state.json read failed for %s", record.id, exc_info=True)

        try:
            status = self._monitor.get_lsf_status(node, lsf_job_id)
        except Exception as exc:
            logger.warning("bjobs poll failed for %s: %s", record.id, exc)
            status = ""

        if status:
            event_log.append(
                "remote.lsf_status",
                job_id=record.id,
                lsf_job_id=lsf_job_id,
                status=status,
            )
            self._mirror_lsf_stage(record.id, status)

            if status == "running" and record.status == JobStatus.PENDING:
                record.status = JobStatus.RUNNING

            if RemoteJobMonitor.is_terminal(status):
                exit_code = self._wait_exit_code(node, remote_job_dir, timeout=_EXIT_CODE_GRACE)
                if exit_code is None:
                    # LSF reports a terminal state but the wrapper script
                    # never wrote ``.exit_code`` \u2014 this happens when LSF
                    # kills the whole process group (e.g. the walltime /
                    # RUNLIMIT limit) before the trailing
                    # ``echo $? > .exit_code`` can run.  Synthesise an exit
                    # code so the job finalises instead of polling forever
                    # and leaving the record stuck in "running".
                    exit_code = 0 if status == STATUS_DONE else 1
                    if exit_code != 0:
                        record.error = record.error or _missing_exit_code_error(
                            status, lsf_job_id
                        )
                        event_log.append(
                            "remote.no_exit_code",
                            job_id=record.id,
                            lsf_job_id=lsf_job_id,
                            lsf_status=status,
                            message=record.error,
                        )
                        logger.warning(
                            "Remote job %s: LSF terminal '%s' with no .exit_code; "
                            "finalising as failed (likely killed by LSF)",
                            record.id, status,
                        )
                return self._finalize_remote(
                    record, event_log, state, node, remote_job_dir, lsf_job_id,
                    exit_code, seen_stages,
                )

        stdout_offset = self._tail_and_emit(
            node, remote_job_dir, "stdout.log", stdout_offset, event_log, record.id, "stdout"
        )
        stderr_offset = self._tail_and_emit(
            node, remote_job_dir, "stderr.log", stderr_offset, event_log, record.id, "stderr"
        )
        state["stdout_offset"] = stdout_offset
        state["stderr_offset"] = stderr_offset

        return (False, None)

    def cancel_remote(
        self, job_id: str, record: JobRecord | None = None,
    ) -> bool:
        """Send ``bkill`` to cancel a remote job (best-effort).

        When ``_job_states`` is empty (e.g. after a server restart) and
        *record* is provided with persisted ``result.lsf_job_id`` /
        ``result.node`` / ``result.remote_dir``, the in-memory state is
        recovered first so the bkill can reach the LSF job.

        Returns ``True`` if the cancellation signal was delivered,
        ``False`` if the job state was not found or bkill failed.
        """
        state = self._job_states.get(job_id)
        if state is None and record is not None:
            if self.recover_job_state(record):
                state = self._job_states.get(job_id)
        if state is None:
            return False
        node: RemoteNode = state["node"]  # type: ignore[assignment]
        lsf_job_id: str = state["lsf_job_id"]  # type: ignore[assignment]
        ok = self._monitor.cancel_job(node, lsf_job_id)
        if ok:
            state["cancel_sent"] = True
        return ok

    def _cleanup_job_state(self, job_id: str) -> None:
        self._job_states.pop(job_id, None)

    def recover_job_state(self, record: JobRecord) -> bool:
        """Rebuild in-memory ``_job_states`` after a server restart.

        Uses ``record.remote_job_id`` (or ``record.result["lsf_job_id"]``
        as fallback), ``record.result["node"]``, and
        ``record.result["remote_dir"]`` to reconstruct the polling state
        so the background poller can reconnect to the remote LSF job.

        Returns True on success, False if required metadata is missing.
        """
        lsf_job_id = record.remote_job_id
        if not lsf_job_id:
            result = record.result or {}
            lsf_job_id = result.get("lsf_job_id")
        if not lsf_job_id:
            return False
        result = record.result or {}
        node_name = result.get("node")
        remote_dir = result.get("remote_dir")
        if not node_name or not remote_dir:
            return False
        node = self._config.get_node(str(node_name))
        if node is None:
            return False
        cli_cmd_raw = result.get("command_line", "")
        self._job_states[record.id] = {
            "node": node,
            "remote_job_dir": str(remote_dir),
            "lsf_job_id": lsf_job_id,
            "stdout_offset": 0,
            "stderr_offset": 0,
            "cli_cmd": str(cli_cmd_raw).split() if cli_cmd_raw else [],
            "poll_cycle": 0,
            "seen_stages": set(),
        }
        return True

    def _finalize_remote(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        state: dict[str, object],
        node: RemoteNode,
        remote_job_dir: str,
        lsf_job_id: str,
        exit_code: int,
        seen_stages: set[str],
    ) -> tuple[bool, int]:
        """Apply the terminal *exit_code* to *record* and tear down poll state.

        Shared by the ``.exit_code`` and LSF-terminal branches of
        :meth:`poll_remote` so both follow one finalisation path: flush the
        remote logs, read the final ``state.json``, persist result metadata +
        provenance, mark stage tasks, emit a terminal event, and drop the
        in-memory poll state.  Returns ``(True, exit_code)``.
        """
        stdout_offset: int = state["stdout_offset"]  # type: ignore[assignment]
        stderr_offset: int = state["stderr_offset"]  # type: ignore[assignment]
        # Final log flush so captured stdout/stderr reflects the exit.
        state["stdout_offset"] = self._tail_and_emit(
            node, remote_job_dir, "stdout.log", stdout_offset, event_log, record.id, "stdout"
        )
        state["stderr_offset"] = self._tail_and_emit(
            node, remote_job_dir, "stderr.log", stderr_offset, event_log, record.id, "stderr"
        )
        try:
            self._observe_remote_state(record, event_log, node, remote_job_dir, seen_stages)
        except Exception:
            logger.debug("Final state.json read failed for %s", record.id, exc_info=True)

        record.exit_code = exit_code
        record.progress = 1.0 if exit_code == 0 else record.progress
        result = dict(record.result or {})
        result["lsf_job_id"] = lsf_job_id
        result["node"] = node.name
        result["host"] = node.host
        result["remote_dir"] = remote_job_dir
        result["exit_code"] = exit_code
        result["command_line"] = " ".join(state["cli_cmd"])  # type: ignore[arg-type]
        record.result = result
        self._build_provenance(record, state["cli_cmd"])  # type: ignore[arg-type]
        final_status = "completed" if exit_code == 0 else "failed"
        self._set_remote_stage_state(record.id, final_status, exit_code=exit_code)
        self._finalize_stages(record.id, final_status)
        event_log.append(
            "job.completed" if exit_code == 0 else "job.failed",
            job_id=record.id,
            exit_code=exit_code,
        )
        self._cleanup_job_state(record.id)
        return (True, exit_code)

    # ------------------------------------------------------------------ #
    # Remote state observation (state.json + .stage_* files)
    # ------------------------------------------------------------------ #

    def _observe_remote_state(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        node: RemoteNode,
        remote_job_dir: str,
        seen: set[str],
    ) -> None:
        """Read remote ``state.json`` and mirror progress/stages to *record*.

        Mirrors the logic in :meth:`JobRunner._observe_state` but reads
        the state file over SFTP instead of the local filesystem.
        """
        data = self._monitor.find_remote_state_json(node, remote_job_dir)
        if data is None:
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

        pending_events: list[tuple[float, str, str, dict[str, object]]] = []
        for name, info in stages.items():
            if not isinstance(info, dict):
                continue
            status = info.get("status")
            if status == "running" and f"running:{name}" not in seen:
                seen.add(f"running:{name}")
                ts = _safe_timestamp_dt(info.get("started_at"))
                pending_events.append((ts, "stage.started", name, {"stage": name}))
            elif status == "completed" and f"done:{name}" not in seen:
                seen.add(f"done:{name}")
                ts = _safe_timestamp_dt(info.get("completed_at"))
                pending_events.append((ts, "stage.completed", name, {"stage": name}))
            elif status == "failed" and f"failed:{name}" not in seen:
                seen.add(f"failed:{name}")
                ts = _safe_timestamp_dt(info.get("completed_at"))
                pending_events.append(
                    (ts, "stage.failed", name, {"stage": name, "error": str(info.get("error", ""))})
                )

        for _ts, event_type, _name, payload in sorted(pending_events, key=lambda x: x[0]):
            event_log.append(event_type, job_id=record.id, **payload)

    # ------------------------------------------------------------------ #
    # Legacy blocking API (kept for backward compatibility)
    # ------------------------------------------------------------------ #

    def select_node(self, spec: JobSpec) -> RemoteNode:
        """Pick the execution node: ``target_node`` if specified, else least-loaded."""
        target = getattr(spec, "target_node", None)
        if target:
            node = self._find_node_by_name(target)
            if node is None:
                raise RemoteNodeUnavailableError(f"Node {target!r} not found in configuration")
            if not node.enabled:
                raise RemoteNodeUnavailableError(f"Node {target!r} is disabled")
            return node
        return self._select_least_loaded()

    def run(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> int:
        """Execute the job remotely and return the process exit code (0 = success)."""
        spec = record.spec
        work_dir = Path(record.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        event_log.append("job.started", job_id=record.id, workflow=spec.workflow, mode="remote")

        try:
            return self._run_remote(record, event_log, cancel_event)
        except RemoteNodeUnavailableError as exc:
            logger.error("No remote node for job %s: %s", record.id, exc)
            record.error = str(exc)
            event_log.append("job.failed", job_id=record.id, error=str(exc))
            self._finalize_stages(record.id, "failed")
            return 1
        except RemoteSubmissionError as exc:
            logger.error("Submission failed for job %s: %s", record.id, exc)
            record.error = str(exc)
            event_log.append("job.failed", job_id=record.id, error=str(exc))
            self._finalize_stages(record.id, "failed")
            return 1
        except Exception as exc:
            logger.exception("Remote job %s crashed", record.id)
            record.error = str(exc)
            event_log.append("job.failed", job_id=record.id, error=str(exc))
            self._finalize_stages(record.id, "failed")
            return 1

    # ------------------------------------------------------------------ #
    # Core flow
    # ------------------------------------------------------------------ #

    def _run_remote(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> int:
        spec = record.spec
        work_dir = Path(record.work_dir)

        # 1. Node selection
        node = self.select_node(spec)
        event_log.append("remote.node_selected", job_id=record.id, node=node.name, host=node.host)

        # 1b. Pre-submit housekeeping: disk-pressure check + retention
        # cleanup.  When the node is too full this raises
        # RemoteNodeUnavailableError (caught by run()) so the job fails
        # fast with a clear reason rather than choking on ENOSPC mid-run.
        self._pre_submit_housekeeping(node, event_log, record.id)

        # 1c. Binary probe: verify workflow-required executables (censo,
        # crest, orca, xtb, ...) resolve on the node before burning an LSF
        # slot. Missing `censo` fails fast with configuration guidance;
        # other binaries only warn (they may be provided by the LSF job
        # environment, e.g. module load).
        self._probe_required_binaries(node, spec, event_log, record.id)

        # 2. Code sync (if enabled and needed)
        if self._config.auto_sync:
            self._sync_code_if_needed(node, event_log, record.id)

        remote_job_dir = posixpath.join(node.remote_work_dir, record.id)

        # Steps 3–5: prepare remote dir, upload input + script, submit.
        # If anything fails before bsub succeeds, clean up the remote
        # directory so we don't leak stale inputs (plan P2-1).
        try:
            lsf_job_id, cli_cmd = self._prepare_and_submit(
                record, spec, node, remote_job_dir, event_log, work_dir
            )
        except Exception:
            self._cleanup_remote_dir(node, remote_job_dir, event_log, record.id)
            raise

        self._set_remote_stage_state(record.id, "running", started=True)

        # 6. Monitor loop
        exit_code = self._monitor_loop(
            record, event_log, cancel_event, node, lsf_job_id, remote_job_dir
        )

        # 7. Finish — set result metadata + provenance (no file download)
        result = dict(record.result or {})
        result["lsf_job_id"] = lsf_job_id
        result["node"] = node.name
        result["host"] = node.host
        result["remote_dir"] = remote_job_dir
        result["command_line"] = " ".join(cli_cmd)
        result["exit_code"] = exit_code
        record.result = result
        record.exit_code = exit_code
        record.progress = 1.0 if exit_code == 0 else record.progress

        cancelled = bool(cancel_event.is_set())
        if cancelled:
            self._set_remote_stage_state(record.id, "cancelled", exit_code=exit_code)
            event_log.append("job.cancelled", job_id=record.id)
            final_status = "cancelled"
        elif exit_code == 0:
            self._set_remote_stage_state(record.id, "completed", exit_code=exit_code)
            event_log.append("job.completed", job_id=record.id, exit_code=exit_code)
            final_status = "completed"
        else:
            self._set_remote_stage_state(record.id, "failed", exit_code=exit_code)
            event_log.append("job.failed", job_id=record.id, exit_code=exit_code)
            final_status = "failed"

        self._build_provenance(record, cli_cmd)
        self._finalize_stages(record.id, final_status)
        return exit_code

    # ------------------------------------------------------------------ #
    # Prepare + submit
    # ------------------------------------------------------------------ #

    def _prepare_and_submit(
        self,
        record: JobRecord,
        spec: JobSpec,
        node: RemoteNode,
        remote_job_dir: str,
        event_log: JobEventLog,
        work_dir: Path,
    ) -> tuple[str, list[str]]:
        """Prepare remote inputs, generate the LSF script, and bsub.

        Returns ``(lsf_job_id, cli_command)``.  Raises on any failure —
        the caller is responsible for cleanup.
        """
        # 3. Prepare remote directory + upload input
        self._stager.make_remote_dir(node, remote_job_dir)

        inputs_dir = work_dir / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)
        run_root = work_dir.parent.parent
        materialized = materialize_job_input(spec.input, inputs_dir, run_root)

        remote_input_name = materialized.name if materialized else "input.xyz"
        if materialized and materialized.is_file():
            remote_inputs_dir = posixpath.join(remote_job_dir, "inputs")
            self._stager.make_remote_dir(node, remote_inputs_dir)
            self._stager.upload_file(
                node, materialized, posixpath.join(remote_inputs_dir, remote_input_name)
            )
            event_log.append("remote.input_uploaded", job_id=record.id, node=node.name)
        else:
            raise RemoteSubmissionError(f"Failed to materialise input for job {record.id}")

        # 4. Generate + upload LSF script
        lsf_spec, cli_cmd = build_lsf_script_spec(
            spec,
            record.id,
            node,
            queue=self._config.queue,
            walltime=self._config.walltime,
            extra_flags=self._config.extra_flags,
            input_path=posixpath.join("inputs", remote_input_name),
        )
        script_text = generate_lsf_script(lsf_spec)
        script_remote_path = posixpath.join(remote_job_dir, "submit.lsf")
        self._stager.upload_text(node, script_text, script_remote_path)

        record.current_stage = "remote_execution"
        record.progress = 0.0
        event_log.append(
            "process.starting",
            job_id=record.id,
            cmd=" ".join(cli_cmd),
            node=node.name,
            remote_dir=remote_job_dir,
        )

        # Initialise the remote_execution stage task.
        self._init_remote_stage(record.id)

        # 5. Submit via bsub
        lsf_job_id = self._submit_lsf(node, script_remote_path, remote_job_dir)
        event_log.append(
            "remote.submitted",
            job_id=record.id,
            lsf_job_id=lsf_job_id,
            node=node.name,
            remote_dir=remote_job_dir,
        )
        return lsf_job_id, cli_cmd

    def _cleanup_remote_dir(
        self, node: RemoteNode, remote_job_dir: str, event_log: JobEventLog, job_id: str
    ) -> None:
        """Best-effort removal of a partially-prepared remote job directory."""
        try:
            self._stager.remove_remote_dir(node, remote_job_dir)
            event_log.append(
                "remote.cleanup", job_id=job_id, node=node.name, remote_dir=remote_job_dir
            )
        except Exception:
            logger.debug("Remote cleanup failed for %s", remote_job_dir, exc_info=True)

    # ------------------------------------------------------------------ #
    # Monitoring
    # ------------------------------------------------------------------ #

    def _monitor_loop(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
        node: RemoteNode,
        lsf_job_id: str,
        remote_job_dir: str,
    ) -> int:
        """Poll LSF status and tail logs until the job terminates.

        Returns the integer exit code (``130`` for cancellation without
        ``.exit_code``, ``1`` for unknown failure).
        """
        stdout_offset = 0
        stderr_offset = 0
        exit_code: int | None = None
        last_lsf_status = ""
        poll_cycle = 0
        seen_stages: set[str] = set()

        # Absolute deadline — if LSF never reports a terminal state (daemon
        # crash, node offline) we force-fail rather than block the worker
        # thread forever (plan P1-3).
        walltime_s = self._config.walltime_seconds
        max_seconds = (
            walltime_s + _MONITOR_TIMEOUT_BUFFER if walltime_s > 0 else _MONITOR_TIMEOUT_FALLBACK
        )
        deadline = time.monotonic() + max_seconds
        timed_out = False

        while True:
            # --- Hard timeout ---
            if time.monotonic() >= deadline:
                timed_out = True
                logger.error(
                    "Remote job %s monitor timed out after %ds (walltime=%ds); attempting bkill",
                    record.id,
                    max_seconds,
                    walltime_s,
                )
                self._monitor.cancel_job(node, lsf_job_id)
                break

            # --- Cancellation (check first, every cycle) ---
            if cancel_event.is_set():
                ok = self._monitor.cancel_job(node, lsf_job_id)
                event_log.append(
                    "remote.cancel_sent",
                    job_id=record.id,
                    lsf_job_id=lsf_job_id,
                    bkill_ok=ok,
                )
                # Grace period for .exit_code to appear.
                exit_code = self._wait_exit_code(node, remote_job_dir, timeout=_EXIT_CODE_GRACE)
                break

            # --- Definitive terminal signal: .exit_code file ---
            exit_code = self._monitor.get_exit_code(node, remote_job_dir)
            if exit_code is not None:
                try:
                    self._observe_remote_state(record, event_log, node, remote_job_dir, seen_stages)
                except Exception:
                    logger.debug("Final state.json read failed for %s", record.id, exc_info=True)
                break

            # --- Periodic state.json read ---
            poll_cycle += 1
            if poll_cycle % _STATE_READ_INTERVAL == 0:
                try:
                    self._observe_remote_state(record, event_log, node, remote_job_dir, seen_stages)
                except Exception:
                    logger.debug("state.json read failed for %s", record.id, exc_info=True)

            # --- LSF status poll ---
            try:
                status = self._monitor.get_lsf_status(node, lsf_job_id)
            except Exception as exc:
                logger.warning("bjobs poll failed for %s: %s", record.id, exc)
                status = ""

            if status != last_lsf_status:
                last_lsf_status = status
                event_log.append(
                    "remote.lsf_status",
                    job_id=record.id,
                    lsf_job_id=lsf_job_id,
                    status=status,
                )
                self._mirror_lsf_stage(record.id, status)

            # --- Log tailing ---
            stdout_offset = self._tail_and_emit(
                node, remote_job_dir, "stdout.log", stdout_offset, event_log, record.id, "stdout"
            )
            stderr_offset = self._tail_and_emit(
                node, remote_job_dir, "stderr.log", stderr_offset, event_log, record.id, "stderr"
            )

            # --- LSF reports terminal but no .exit_code yet ---
            if RemoteJobMonitor.is_terminal(status):
                exit_code = self._wait_exit_code(node, remote_job_dir, timeout=_EXIT_CODE_GRACE)
                break

            time.sleep(self._poll_interval)

        # Final log flush.
        self._tail_and_emit(
            node, remote_job_dir, "stdout.log", stdout_offset, event_log, record.id, "stdout"
        )
        self._tail_and_emit(
            node, remote_job_dir, "stderr.log", stderr_offset, event_log, record.id, "stderr"
        )

        if exit_code is None:
            if cancel_event.is_set():
                return 130
            if timed_out:
                record.error = record.error or (
                    f"Remote job monitor timed out after {max_seconds}s"
                )
                return 1
            # LSF went terminal without writing .exit_code (e.g. killed by
            # a walltime/RUNLIMIT process-group signal).  DONE is treated
            # as success; any other terminal state is a failure.
            if last_lsf_status == STATUS_DONE:
                return 0
            record.error = record.error or _missing_exit_code_error(
                last_lsf_status or "unknown", lsf_job_id
            )
            return 1
        return exit_code

    def _wait_exit_code(
        self, node: RemoteNode, remote_job_dir: str, timeout: float = _EXIT_CODE_GRACE
    ) -> int | None:
        """Poll for ``.exit_code`` for up to *timeout* seconds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ec = self._monitor.get_exit_code(node, remote_job_dir)
            if ec is not None:
                return ec
            time.sleep(1.0)
        return None

    def _tail_and_emit(
        self,
        node: RemoteNode,
        remote_job_dir: str,
        filename: str,
        offset: int,
        event_log: JobEventLog,
        job_id: str,
        stream: str,
    ) -> int:
        """Tail a remote log file and emit new lines as ``log`` events."""
        if filename == "stdout.log":
            text, new_offset = self._monitor.tail_stdout(node, remote_job_dir, offset)
        else:
            text, new_offset = self._monitor.tail_stderr(node, remote_job_dir, offset)
        if not text:
            return new_offset
        lines = text.splitlines()
        if len(lines) > _MAX_LOG_LINES_PER_POLL:
            lines = lines[-_MAX_LOG_LINES_PER_POLL:]
        for line in lines:
            if line.strip():
                event_log.append("log", job_id=job_id, stream=stream, line=line)
        return new_offset

    # ------------------------------------------------------------------ #
    # Code sync
    # ------------------------------------------------------------------ #

    def _sync_code_if_needed(self, node: RemoteNode, event_log: JobEventLog, job_id: str) -> None:
        try:
            if not self._syncer.check_sync_needed(node):
                logger.debug("Code already in sync with %s", node.name)
                return
            event_log.append("remote.sync_start", job_id=job_id, node=node.name)
            result = self._syncer.sync_code(node)
            event_log.append(
                "remote.sync_done",
                job_id=job_id,
                node=node.name,
                uploaded=result.uploaded,
                total=result.total,
                errors=result.errors,
            )
            if not result.ok:
                logger.warning("Code sync to %s had errors: %s", node.name, result.errors)
        except Exception as exc:
            logger.error("Code sync to %s failed: %s", node.name, exc)
            event_log.append("remote.sync_failed", job_id=job_id, node=node.name, error=str(exc))
            raise

    # ------------------------------------------------------------------ #
    # Pre-submit housekeeping (Phase 5)
    # ------------------------------------------------------------------ #

    def _pre_submit_housekeeping(
        self, node: RemoteNode, event_log: JobEventLog, job_id: str
    ) -> None:
        """Run disk-pressure housekeeping before submitting to *node*.

        Delegates to :class:`RemoteCleanup` when one is configured.  If the
        node's disk usage exceeds the skip threshold (even after a retention
        sweep) this raises :class:`RemoteNodeUnavailableError` so the job
        fails fast rather than running out of disk mid-computation.

        Housekeeping is fail-open for transient errors: a disk-query or
        sweep failure is logged but does **not** block submission, since
        blocking on a flaky SSH probe is worse than proceeding.  Only the
        explicit ``should_skip`` decision blocks submission.
        """
        if self._cleanup is None:
            return

        try:
            decision = self._cleanup.pre_submit_housekeeping(node)
        except Exception as exc:
            logger.warning("Housekeeping crashed on %s: %s", node.name, exc)
            event_log.append(
                "remote.housekeeping_error",
                job_id=job_id,
                node=node.name,
                error=str(exc),
            )
            return

        removed = len(decision.cleanup.removed_dirs) if decision.cleanup else 0
        errors = len(decision.cleanup.errors) if decision.cleanup else 0
        event_log.append(
            "remote.housekeeping",
            job_id=job_id,
            node=node.name,
            disk_before=decision.disk_usage_before,
            disk_after=decision.disk_usage_after,
            should_skip=decision.should_skip,
            removed_dirs=removed,
            cleanup_errors=errors,
            reason=decision.reason,
        )

        if decision.should_skip:
            raise RemoteNodeUnavailableError(f"Node {node.name!r} skipped: {decision.reason}")

    # ------------------------------------------------------------------ #
    # Pre-submit binary probe (P5, acceptance gate 10)
    # ------------------------------------------------------------------ #

    _BINARY_PROBE_SCRIPT = (
        "import json, os, shutil, subprocess, sys\n"
        "names = sys.argv[1:]\n"
        "cfg = {}\n"
        "try:\n"
        "    import yaml\n"
        "    cfg_path = None\n"
        "    for cand in ('~/.cccp.yaml', '~/.conformer_search.yaml'):\n"
        "        p = os.path.expanduser(cand)\n"
        "        if os.path.isfile(p):\n"
        "            cfg_path = p\n"
        "            break\n"
        "    if cfg_path:\n"
        "        with open(cfg_path) as fh:\n"
        "            cfg = yaml.safe_load(fh) or {}\n"
        "except Exception:\n"
        "    cfg = {}\n"
        "exes = cfg.get('executables') or {}\n"
        "report = {}\n"
        "for name in names:\n"
        "    configured = ((exes.get(name) or {}).get('path')) or name\n"
        "    resolved = shutil.which(configured)\n"
        "    if resolved is None and os.path.isfile(configured) "
        "and os.access(configured, os.X_OK):\n"
        "        resolved = configured\n"
        "    version = None\n"
        "    if resolved and name in ('censo', 'xtb'):\n"
        "        flag = '-v' if name == 'censo' else '--version'\n"
        "        try:\n"
        "            proc = subprocess.run([resolved, flag], "
        "capture_output=True, text=True, timeout=20)\n"
        "            txt = (proc.stdout or proc.stderr).strip()\n"
        "            lines = [l.strip() for l in txt.splitlines() if l.strip()]\n"
        "            ver = next((l for l in lines if any(ch.isdigit() for ch in l)), None)\n"
        "            version = ver[:120] if ver else (lines[0][:120] if lines else None)\n"
        "        except Exception:\n"
        "            version = None\n"
        "    report[name] = {'configured': configured, "
        "'resolved': resolved, 'version': version}\n"
        "print(json.dumps(report))\n"
    )

    def _probe_required_binaries(
        self, node: RemoteNode, spec: JobSpec, event_log: JobEventLog, job_id: str
    ) -> None:
        """Probe workflow-required binaries on *node* before submission.

        Resolves each binary via the node-local ``~/.cccp.yaml``
        (``executables.<name>.path``; falls back to ``~/.conformer_search.yaml``)
        falling back to a PATH lookup in a
        login shell. A missing ``censo`` raises
        :class:`RemoteNodeUnavailableError` with configuration guidance
        (acceptance gate 10); other missing binaries only log a warning —
        they may be provided by the LSF job environment. SSH/transport
        failures are fail-open, consistent with housekeeping.
        """
        try:
            from acp.workflows.registry import get_workflow_entry

            entry = get_workflow_entry(spec.workflow)
            binaries = list(entry.requires_binaries) if entry else []
        except Exception:
            binaries = []
        if not binaries:
            return

        import json as _json
        import shlex as _shlex

        py = node.python_executable or "python3"
        script_arg = _shlex.quote(self._BINARY_PROBE_SCRIPT)
        args = " ".join(_shlex.quote(b) for b in binaries)
        command = "bash -lc " + _shlex.quote(f"{py} -c {script_arg} {args}")

        try:
            code, out, err = self._ssh.execute(node, command, timeout=90)
            report = _json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
        except Exception as exc:
            logger.warning("Binary probe crashed on %s (fail-open): %s", node.name, exc)
            event_log.append(
                "remote.binary_probe_error", job_id=job_id, node=node.name, error=str(exc)
            )
            return

        if not isinstance(report, dict) or not report:
            logger.warning(
                "Binary probe on %s returned no report (exit=%s, stderr=%s) — fail-open",
                node.name,
                code,
                (err or "")[-200:],
            )
            return

        missing = [name for name, info in report.items() if not info.get("resolved")]
        versions = {
            name: info.get("version") for name, info in report.items() if info.get("version")
        }
        event_log.append(
            "remote.binary_probe",
            job_id=job_id,
            node=node.name,
            report=report,
            missing=missing,
        )
        if versions:
            logger.info("Node %s binary versions: %s", node.name, versions)

        if "censo" in missing:
            configured = report.get("censo", {}).get("configured", "censo")
            raise RemoteNodeUnavailableError(
                f"Node {node.name!r} is missing the CENSO binary "
                f"(configured path: {configured!r}). Install it on the node "
                f"(Python >= 3.12: `pip install censo`; otherwise create a "
                f"dedicated venv) and set `executables.censo.path` in the "
                f"node-side ~/.cccp.yaml, e.g.\n"
                f"  executables:\n"
                f"    censo:\n"
                f"      path: /home/<user>/censo-venv/bin/censo"
            )

        for name in missing:
            logger.warning(
                "Node %s: required binary %r not resolved from login shell "
                "PATH or ~/.cccp.yaml — assuming the LSF job "
                "environment provides it",
                node.name,
                name,
            )

    # ------------------------------------------------------------------ #
    # LSF submission
    # ------------------------------------------------------------------ #

    def _submit_lsf(self, node: RemoteNode, script_remote_path: str, remote_job_dir: str) -> str:
        """Run ``bsub < submit.lsf`` on *node* and return the parsed LSF job ID."""
        cmd = f'cd "{remote_job_dir}" && bsub < "{script_remote_path}"'
        try:
            code, out, err = self._ssh.execute(node, cmd, timeout=60)
        except SSHExecutionError as exc:
            raise RemoteSubmissionError(f"bsub SSH execution failed on {node.name}: {exc}") from exc
        if code != 0:
            raise RemoteSubmissionError(
                f"bsub failed on {node.name} (exit={code}): {err.strip() or out.strip()}"
            )
        match = _LSF_JOB_ID_RE.search(out)
        if not match:
            raise RemoteSubmissionError(
                f"Could not parse LSF job ID from bsub output on {node.name}: {out!r}"
            )
        lsf_job_id = match.group(1)
        logger.info(
            "Submitted LSF job <%s> on %s, remote_dir=%s",
            lsf_job_id,
            node.name,
            remote_job_dir,
        )
        return lsf_job_id

    # ------------------------------------------------------------------ #
    # Node selection
    # ------------------------------------------------------------------ #

    def _find_node_by_name(self, name: str) -> RemoteNode | None:
        return self._config.get_node(name)

    def _select_least_loaded(self) -> RemoteNode:
        """Return the enabled node with the fewest running LSF jobs."""
        enabled = self._config.enabled_nodes
        if not enabled:
            raise RemoteNodeUnavailableError("No enabled remote nodes configured")

        best: RemoteNode | None = None
        best_count: int | None = None
        for node in enabled:
            try:
                count = self._monitor.get_running_job_count(node)
            except Exception:
                logger.debug("Failed querying job count on %s, skipping", node.name)
                count = node.max_concurrent_jobs
            if count >= node.max_concurrent_jobs:
                continue
            if best_count is None or count < best_count:
                best = node
                best_count = count

        if best is None:
            raise RemoteNodeUnavailableError("All remote nodes are at capacity")
        return best

    # ------------------------------------------------------------------ #
    # Stage task management
    # ------------------------------------------------------------------ #

    def _init_remote_stage(self, job_id: str) -> None:
        """Create a single ``remote_execution`` stage task (if observer present).

        The task is created in ``pending`` state with ``started_at=None``;
        it is filled in when LSF transitions to RUN (plan P2-7).
        """
        if self._observer is None:
            return
        existing = {t.stage_name for t in self._observer.store.list_by_job(job_id)}
        if "remote_execution" in existing:
            return
        task = StageTask(
            task_id=str(uuid.uuid4()),
            job_id=job_id,
            stage_name="remote_execution",
            task_type="remote",
            state="pending",
            started_at=None,
            updated_at=_utc_now_iso(),
        )
        self._observer.store.create(task)
        # Cache the task id so _set_remote_stage_state can update it
        # directly instead of scanning all stage tasks each poll (plan P2-8).
        self._remote_stage_task_ids[job_id] = task.task_id

    def _set_remote_stage_state(
        self, job_id: str, state: str, exit_code: int | None = None, started: bool = False
    ) -> None:
        if self._observer is None:
            return
        task_id = self._remote_stage_task_ids.get(job_id)
        task: StageTask | None = None
        if task_id is not None:
            task = self._observer.store.get(task_id)
        if task is None or task.stage_name != "remote_execution":
            # Fallback: scan by job (cache miss or stale entry).
            for candidate in self._observer.store.list_by_job(job_id):
                if candidate.stage_name == "remote_execution":
                    task = candidate
                    self._remote_stage_task_ids[job_id] = candidate.task_id
                    break
        if task is None:
            return

        task.state = state
        if started and task.started_at is None:
            task.started_at = _utc_now_iso()
        if state in ("completed", "failed", "cancelled"):
            task.completed_at = task.completed_at or _utc_now_iso()
        if exit_code is not None:
            task.exit_status = exit_code
        task.updated_at = _utc_now_iso()
        self._observer.store.update(task)

    def _mirror_lsf_stage(self, job_id: str, lsf_status: str) -> None:
        """Map the current LSF status to the remote_execution stage state."""
        if lsf_status == "running":
            self._set_remote_stage_state(job_id, "running", started=True)
        elif lsf_status == "pending":
            self._set_remote_stage_state(job_id, "pending")

    def _finalize_stages(self, job_id: str, final_status: str) -> None:
        """Mark all remaining non-terminal stage tasks with *final_status*."""
        if self._observer is None:
            return
        self._observer.finalize_job(job_id, final_status)

    # ------------------------------------------------------------------ #
    # Provenance
    # ------------------------------------------------------------------ #

    def _build_provenance(self, record: JobRecord, cli_cmd: list[str]) -> None:
        """Build provenance from CLI metadata + LSF info (no artifact capture)."""
        if record.completed_at is None:
            record.completed_at = _utc_now_iso()
        result = dict(record.result or {})
        if not result.get("command_line"):
            result["command_line"] = " ".join(cli_cmd)
        spec = record.spec
        if not result.get("backend_name") and spec.method.get("backend"):
            result["backend_name"] = str(spec.method["backend"])
        if not result.get("method"):
            method = spec.method.get("protocol")
            if method is not None:
                result["method"] = str(method)
        record.result = result

        try:
            provenance = build_provenance_for_job(spec, record)
            # Override hostname — the computation ran on the remote node,
            # not on this server.  build_provenance_for_job uses
            # socket.gethostname() which is the local ACP server.
            remote_host = result.get("host") or result.get("node")
            if remote_host:
                provenance.hostname = str(remote_host)
            record.result = dict(record.result or {})
            record.result["provenance"] = asdict(provenance)
        except Exception:
            logger.debug("Provenance build failed for %s", record.id, exc_info=True)
