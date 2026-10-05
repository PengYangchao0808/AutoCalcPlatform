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

import json
import logging
import posixpath
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict, cast

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import (
    SCAN_CONFIG_FILENAME,
    JobRecord,
    JobSpec,
    JobStatus,
    build_task_record,
)
from acp.scheduler.nodes import ExecutionTargetError
from acp.scheduler.provenance import build_provenance_for_job
from acp.scheduler.remote.cleanup import RemoteCleanup
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.monitor import STATUS_DONE, STATUS_PAUSED, RemoteJobMonitor
from acp.scheduler.remote.node_manager import detect_node_python
from acp.scheduler.remote.paths import resolve_remote_dir
from acp.scheduler.remote.script_gen import (
    build_lsf_script_spec,
    build_remote_scan_config_payload,
    generate_lsf_script,
)
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool, SSHExecutionError
from acp.scheduler.remote.sync import CodeSyncer
from acp.scheduler.runner import materialize_job_input
from acp.scheduler.stage_tasks import StageTask, StageTaskObserver
from acp.storage.layout import TaskStorage

logger = logging.getLogger(__name__)

__all__ = [
    "RemoteJobRunner",
    "RemoteNodeUnavailableError",
    "RemotePollObservation",
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


class _RemoteJobStateBase(TypedDict):
    node: RemoteNode
    remote_job_dir: str
    lsf_job_id: str
    stdout_offset: int
    stderr_offset: int
    cli_cmd: list[str]


class _RemoteJobState(_RemoteJobStateBase, total=False):
    # total=False subclass: typing.NotRequired is 3.11+, matrix includes 3.10.
    poll_cycle: int
    seen_stages: set[str]
    cancel_sent: bool


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


@dataclass(frozen=True)
class RemotePollObservation:
    """Structured result of one remote poll — the poll never mutates the record.

    ``observed_status`` carries a legal *state* observation (RUNNING/PAUSED)
    for the manager to persist through ``store.transition``; ``progress`` and
    ``current_stage`` are *progress* observations persisted through the
    narrow ``store.update_progress`` API.  On terminal polls ``final_state``
    carries ``{"result": ..., "error": ...}`` for the terminal transition and
    ``stage_events`` are stage events the manager emits only AFTER the
    terminal CAS succeeds (see :meth:`RemoteJobRunner.apply_terminal_side_effects`).
    """

    terminal: bool
    exit_code: int | None = None
    lsf_status: str | None = None
    observed_status: JobStatus | None = None
    progress: float | None = None
    current_stage: str | None = None
    final_state: dict[str, Any] | None = None
    stage_events: tuple[tuple[str, dict[str, Any]], ...] = ()


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
        self._job_states: dict[str, _RemoteJobState] = {}
        # Per-node cache of the probe-resolved (Python 3.10+) interpreter.
        # Keyed by node name; TTL 300 s so repeated submissions on the same
        # node don't re-probe every time.
        self._python_probe_cache: dict[str, tuple[float, str]] = {}

    # ------------------------------------------------------------------ #
    # Non-blocking poller-driven API
    # ------------------------------------------------------------------ #

    def submit_remote(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        target_node: str | None = None,
        *,
        remote_job_dir: str | None = None,
    ) -> str:
        """Prepare and submit the LSF job, return the LSF job ID immediately.

        Executes steps 1-5 (node selection, housekeeping, binary probe,
        code sync, upload, LSF script, ``bsub``).  Does **not** enter
        the monitor loop — that is driven by :meth:`poll_remote`.

        ``target_node`` is the already-resolved execution target from
        ``NodeRegistry`` (single-point selection, M4).  When omitted the
        legacy ``select_node(spec)`` path runs for backward compatibility.

        ``remote_job_dir`` is the manager's already-resolved storage
        identity dir; when omitted the record is resolved through
        :func:`~acp.scheduler.remote.paths.resolve_remote_dir` (persisted
        mapping → legacy dual-candidate probe → fallback).
        """
        spec = record.spec
        work_dir = Path(record.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        event_log.append("job.started", job_id=record.id, workflow=spec.workflow, mode="remote")

        if target_node is not None:
            node = self._find_node_by_name(target_node)
            if node is None or not node.enabled:
                raise ExecutionTargetError(f"target_node '{target_node}' not found or disabled")
        else:
            node = self.select_node(spec)
        event_log.append("remote.node_selected", job_id=record.id, node=node.name, host=node.host)

        self._pre_submit_housekeeping(node, event_log, record.id)
        self._probe_required_binaries(node, spec, event_log, record.id)

        if self._config.auto_sync:
            self._sync_code_if_needed(node, event_log, record.id)

        remote_job_dir = self._resolve_remote_dir_for(
            record, node, event_log, explicit=remote_job_dir
        )

        claim_state: dict[str, str] = {}
        try:
            lsf_job_id, cli_cmd = self._prepare_and_submit(
                record, spec, node, remote_job_dir, event_log, work_dir, claim_state=claim_state
            )
        except Exception:
            # Only a directory this submission *created* is deletable; a
            # `reused` dir (or an ownership conflict) is never cleaned.
            if claim_state.get("disposition") == "created":
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
    ) -> RemotePollObservation:
        """Single non-blocking check of remote job status.

        Checks ``.exit_code`` first (authoritative), then ``bjobs``
        (LSF state).  Tails logs and periodically reads ``state.json``
        for fine-grained stage progress.

        The observation is *returned* without mutating ``record``: state
        transitions (PENDING/PAUSED→RUNNING, →PAUSED) and progress are
        persisted by the caller through conditional store APIs, and terminal
        side effects run only after the terminal CAS succeeds (see
        :meth:`collect_final_state` / :meth:`apply_terminal_side_effects`).
        """
        state = self._job_states.get(record.id)
        if state is None:
            return RemotePollObservation(
                terminal=True,
                exit_code=record.exit_code if record.exit_code is not None else 1,
            )

        node = state["node"]
        remote_job_dir = state["remote_job_dir"]
        lsf_job_id = state["lsf_job_id"]
        stdout_offset = state["stdout_offset"]
        stderr_offset = state["stderr_offset"]
        poll_cycle = state.get("poll_cycle", 0)
        seen_stages = state.get("seen_stages", set())

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
            exit_code = self._wait_exit_code(
                node, remote_job_dir, timeout=_EXIT_CODE_GRACE, attempt=record.attempt
            )
            # Poll-state teardown happens in apply_terminal_side_effects,
            # only after the manager persists the terminal transition.
            return RemotePollObservation(
                terminal=True,
                exit_code=exit_code if exit_code is not None else 130,
            )

        exit_code = self._monitor.get_exit_code(node, remote_job_dir)
        if exit_code is not None and not self._receipt_is_current(
            node, remote_job_dir, record.attempt
        ):
            # Stale receipt from a previous attempt — not this run's result.
            exit_code = None
        if exit_code is not None:
            return self.collect_final_state(
                record,
                event_log,
                state,
                node,
                remote_job_dir,
                lsf_job_id,
                exit_code,
                seen_stages,
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

        observed_status: JobStatus | None = None
        if status:
            event_log.append(
                "remote.lsf_status",
                job_id=record.id,
                lsf_job_id=lsf_job_id,
                status=status,
            )
            self._mirror_lsf_stage(record.id, status)

            # State observations (legal transitions only — the manager CASes
            # them against the persisted status; a no-op change stays a
            # progress-only observation).
            if status == "running" and record.status in (
                JobStatus.PENDING,
                JobStatus.PAUSED,
            ):
                observed_status = JobStatus.RUNNING
            elif status == STATUS_PAUSED and record.status in (
                JobStatus.PENDING,
                JobStatus.RUNNING,
            ):
                observed_status = JobStatus.PAUSED

            if RemoteJobMonitor.is_terminal(status):
                exit_code = self._wait_exit_code(
                    node, remote_job_dir, timeout=_EXIT_CODE_GRACE, attempt=record.attempt
                )
                if exit_code is None:
                    # LSF reports a terminal state but the wrapper script
                    # never wrote ``.exit_code`` — this happens when LSF
                    # kills the whole process group (e.g. the walltime /
                    # RUNLIMIT limit) before the trailing
                    # ``echo $? > .exit_code`` can run.  Synthesise an exit
                    # code so the job finalises instead of polling forever
                    # and leaving the record stuck in "running".
                    exit_code = 0 if status == STATUS_DONE else 1
                    if exit_code != 0:
                        record.error = record.error or _missing_exit_code_error(status, lsf_job_id)
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
                            record.id,
                            status,
                        )
                return self.collect_final_state(
                    record,
                    event_log,
                    state,
                    node,
                    remote_job_dir,
                    lsf_job_id,
                    exit_code,
                    seen_stages,
                )

        stdout_offset = self._tail_and_emit(
            node, remote_job_dir, "stdout.log", stdout_offset, event_log, record.id, "stdout"
        )
        stderr_offset = self._tail_and_emit(
            node, remote_job_dir, "stderr.log", stderr_offset, event_log, record.id, "stderr"
        )
        state["stdout_offset"] = stdout_offset
        state["stderr_offset"] = stderr_offset

        return RemotePollObservation(
            terminal=False,
            lsf_status=status or None,
            observed_status=observed_status,
            progress=record.progress,
            current_stage=record.current_stage,
        )

    def cancel_remote(
        self,
        job_id: str,
        record: JobRecord | None = None,
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
        node = state["node"]
        lsf_job_id = state["lsf_job_id"]
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

    def collect_final_state(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        state: _RemoteJobState,
        node: RemoteNode,
        remote_job_dir: str,
        lsf_job_id: str,
        exit_code: int,
        seen_stages: set[str],
    ) -> RemotePollObservation:
        """Collect the terminal-state payload without applying side effects.

        Shared by the ``.exit_code`` and LSF-terminal branches of
        :meth:`poll_remote`: flushes the remote logs, reads the final
        ``state.json`` (stage events are collected, not emitted), and fills
        the in-memory result metadata + provenance.  The returned
        observation is what the manager persists through the terminal CAS;
        events, stage teardown and poll-state cleanup follow in
        :meth:`apply_terminal_side_effects` — only after that CAS succeeds.
        """
        stdout_offset = state["stdout_offset"]
        stderr_offset = state["stderr_offset"]
        # Final log flush so captured stdout/stderr reflects the exit.
        state["stdout_offset"] = self._tail_and_emit(
            node, remote_job_dir, "stdout.log", stdout_offset, event_log, record.id, "stdout"
        )
        state["stderr_offset"] = self._tail_and_emit(
            node, remote_job_dir, "stderr.log", stderr_offset, event_log, record.id, "stderr"
        )
        stage_events: tuple[tuple[str, dict[str, Any]], ...] = ()
        try:
            collected = self._observe_remote_state(
                record, event_log, node, remote_job_dir, seen_stages, emit=False
            )
            stage_events = tuple((etype, dict(payload)) for _ts, etype, _name, payload in collected)
            state["seen_stages"] = seen_stages
        except Exception:
            logger.debug("Final state.json read failed for %s", record.id, exc_info=True)

        record.exit_code = exit_code
        if exit_code == 0:
            record.progress = 1.0
        result = dict(record.result or {})
        result["lsf_job_id"] = lsf_job_id
        result["node"] = node.name
        result["host"] = node.host
        result["remote_dir"] = remote_job_dir
        result["exit_code"] = exit_code
        cli_cmd = state["cli_cmd"]
        result["command_line"] = " ".join(cli_cmd)
        record.result = result
        self._build_provenance(record, cli_cmd)
        return RemotePollObservation(
            terminal=True,
            exit_code=exit_code,
            progress=record.progress,
            current_stage=record.current_stage,
            final_state={"result": dict(record.result or {}), "error": record.error},
            stage_events=stage_events,
        )

    def apply_terminal_side_effects(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        stage_events: tuple[tuple[str, dict[str, Any]], ...] = (),
    ) -> None:
        """Emit terminal events and tear down stage/poll state — idempotent.

        Called by the manager only AFTER the terminal CAS succeeds, and safe
        to retry: every event carries a stable
        ``terminal:<job>:<attempt>:<event>`` idempotency key, so a crash
        between the event and the ``terminal_side_effects_done`` marker can
        never duplicate the event on the reconcile retry.
        """
        exit_code = record.exit_code
        key_base = f"terminal:{record.id}:{record.attempt}"
        for event_type, payload in stage_events:
            event_log.append(
                event_type,
                job_id=record.id,
                idempotency_key=f"{key_base}:{event_type}:{payload.get('stage', '')}",
                **payload,
            )
        if record.status == JobStatus.CANCELLED:
            self._cleanup_job_state(record.id)
            return
        success = exit_code == 0
        event_type = "job.completed" if success else "job.failed"
        event_log.append(
            event_type,
            job_id=record.id,
            exit_code=exit_code,
            idempotency_key=f"{key_base}:{event_type}",
        )
        final_status = "completed" if success else "failed"
        self._set_remote_stage_state(record.id, final_status, exit_code=exit_code)
        self._finalize_stages(record.id, final_status)
        self._cleanup_job_state(record.id)

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
        *,
        emit: bool = True,
    ) -> list[tuple[float, str, str, dict[str, object]]]:
        """Read remote ``state.json`` and mirror progress/stages to *record*.

        Mirrors the logic in :meth:`JobRunner._observe_state` but reads
        the state file over SFTP instead of the local filesystem.  With
        ``emit=False`` the stage events are only returned (terminal polls
        defer emission until after the manager's terminal CAS).
        """
        data = cast(
            dict[str, object] | None,
            self._monitor.find_remote_state_json(node, remote_job_dir),
        )
        if data is None:
            return []

        # Mirror the observed payload into the local work dir: the API's
        # state.json enrichment reads only local files, so without this the
        # live timeline/metrics never see remote progress.
        self._mirror_state_json(record, data)

        if data.get("status") == "failed":
            return []

        current_stage = data.get("current_stage")
        record.current_stage = str(current_stage) if isinstance(current_stage, str) else None
        raw_stages = data.get("stages")
        stages = cast(dict[str, object], raw_stages) if isinstance(raw_stages, dict) else {}
        total = max(len(stages), 1)
        done = sum(
            1
            for stage in stages.values()
            if isinstance(stage, dict) and stage.get("status") in ("completed", "skipped")
        )
        record.progress = round(done / total, 3)

        overall = data.get("overall_progress")
        if isinstance(overall, (int, float)):
            record.progress = round(float(overall), 3)

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

        if emit:
            for _ts, event_type, _name, payload in sorted(pending_events, key=lambda x: x[0]):
                event_log.append(event_type, job_id=record.id, **payload)
        return pending_events

    def _mirror_state_json(self, record: JobRecord, data: dict[str, object]) -> None:
        """Write the observed remote state payload to the local work dir."""
        work_dir = Path(record.work_dir)
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            state_path = work_dir / "state.json"
            tmp = state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(state_path)
        except OSError:
            logger.debug("state.json mirror failed for %s", record.id, exc_info=True)

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

        # Storage identity resolution — the SAME path as submit_remote
        # (no second directory-join implementation in this legacy entry).
        remote_job_dir = self._resolve_remote_dir_for(record, node, event_log)

        # Steps 3–5: claim the remote dir, archive the previous attempt's
        # receipts, upload input + script, submit.  Late failures only
        # clean up a directory this submission created.
        claim_state: dict[str, str] = {}
        try:
            lsf_job_id, cli_cmd = self._prepare_and_submit(
                record, spec, node, remote_job_dir, event_log, work_dir, claim_state=claim_state
            )
        except Exception:
            if claim_state.get("disposition") == "created":
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
        claim_state: dict[str, str] | None = None,
    ) -> tuple[str, list[str]]:
        """Claim the dir, archive old receipts, upload inputs, bsub.

        Returns ``(lsf_job_id, cli_command)``.  Raises on any failure —
        the caller cleans up only when ``claim_state["disposition"]`` is
        ``"created"`` (reused/conflicting dirs are never deleted).

        The exclusive claim runs BEFORE any file write, and the previous
        attempt's receipt archive runs BEFORE ``bsub`` — a failure there
        aborts the submission (no PENDING is published).
        """
        disposition = self._stager.claim_remote_job_dir(node, remote_job_dir, record)
        if claim_state is not None:
            claim_state["disposition"] = disposition

        self._archive_remote_attempt(node, remote_job_dir, record, event_log, disposition)

        inputs_dir = work_dir
        inputs_dir.mkdir(parents=True, exist_ok=True)
        run_root = work_dir.parent.parent
        materialized_roles: dict[str, Path] = {}
        materialized = materialize_job_input(spec.input, inputs_dir, run_root, materialized_roles)

        bond_scan_mode = spec.workflow == "PESsearch" and (
            str(spec.method.get("mode") or "") == "bond_length_scan"
        )
        is_batch_structures = str(spec.input.get("source_type") or "") == "batch_structures"
        remote_input_name = "batch_items.json" if is_batch_structures else "input.xyz"
        if bond_scan_mode:
            scan_payload = build_remote_scan_config_payload(spec) or {}
            scan_config_local = work_dir / SCAN_CONFIG_FILENAME
            scan_config_local.write_text(
                json.dumps(scan_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._stager.upload_file(
                node,
                scan_config_local,
                posixpath.join(remote_job_dir, SCAN_CONFIG_FILENAME),
            )
            event_log.append(
                "remote.input_uploaded",
                job_id=record.id,
                node=node.name,
                role="scan_config",
            )
        elif materialized and materialized.is_file():
            remote_path = posixpath.join(remote_job_dir, remote_input_name)
            self._stager.upload_file(node, materialized, remote_path)
            event_log.append(
                "remote.input_uploaded",
                job_id=record.id,
                node=node.name,
                role="input",
            )
        else:
            raise RemoteSubmissionError(f"Failed to materialise input for job {record.id}")

        # 3b. Scheduler-context markers (job.json + task.json): the node
        # side detects them by existence only (workflows/_helpers.py) and
        # otherwise nests products under <remote_job_dir>/<molecule>/,
        # where the flat result-fetch layer never finds them.
        self._upload_scheduler_markers(record, node, remote_job_dir, work_dir, event_log)

        # 4. Generate + upload LSF script
        py = self._resolve_node_python(node, job_id=record.id)
        lsf_spec, cli_cmd = build_lsf_script_spec(
            spec,
            record.id,
            node,
            # Per-node queue override; None keeps cluster queue byte-identically.
            queue=node.queue or self._config.queue,
            walltime=self._config.walltime,
            extra_flags=self._config.extra_flags,
            input_path=remote_input_name,
            remote_dir_name=spec.task_dir_name() if spec.uses_v2_naming else None,
            remote_job_dir=remote_job_dir,
            python_executable=py,
            pre_cmds=self._config.pre_cmds,
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

    def _upload_scheduler_markers(
        self,
        record: JobRecord,
        node: RemoteNode,
        remote_job_dir: str,
        work_dir: Path,
        event_log: JobEventLog,
    ) -> None:
        """Ensure + upload the scheduler-context markers ``job.json``/``task.json``.

        These Zone-A markers make the node side treat *remote_job_dir* as
        a scheduler task dir (``workflows/_helpers.py::is_scheduler_task_dir``)
        so workflow products land flat (``RESULT/...``, ``state.json``,
        ``WORK/00_RUNTIME/checkpoint.json``) instead of nested under
        ``<remote_job_dir>/<molecule>/``.  The uploaded ``job.json`` is a
        submission-time snapshot used only as an existence marker by the
        node side — it is never refreshed after submission.

        Missing local markers are generated first (the manager normally
        writes ``job.json``; the legacy ``run()`` path needs the defensive
        write); existing files are left untouched.  Upload failures
        propagate like input-upload failures so the submission fails
        visibly — never silently produce a nested-layout task.
        """
        task_json = work_dir / "task.json"
        if not task_json.is_file():
            TaskStorage(work_dir).write_task_json(build_task_record(record))
        job_json = work_dir / "job.json"
        if not job_json.is_file():
            job_json.write_text(
                json.dumps(record.to_dict(), indent=2, default=str), encoding="utf-8"
            )

        for marker in ("job.json", "task.json"):
            self._stager.upload_file(
                node, work_dir / marker, posixpath.join(remote_job_dir, marker)
            )
        event_log.append(
            "remote.markers_uploaded",
            job_id=record.id,
            node=node.name,
            files=["job.json", "task.json"],
        )

    def _resolve_remote_dir_for(
        self,
        record: JobRecord,
        node: RemoteNode,
        event_log: JobEventLog,
        *,
        explicit: str | None = None,
    ) -> str:
        """Resolve storage identity → remote dir and emit legacy-path events.

        Shared by ``submit_remote`` and the legacy ``run()``/``_run_remote``
        entry so there is exactly one directory-resolution implementation.
        """
        resolved, fallback = resolve_remote_dir(
            record,
            node,
            explicit=explicit,
            probe=self._owner_probe(node, record),
        )
        source = ((record.result or {}).get("remote") or {}).get("path_source")
        if fallback:
            event_log.append(
                "remote.path_legacy_fallback",
                job_id=record.id,
                node=node.name,
                remote_dir=resolved,
            )
        elif source == "legacy_flat":
            # Legacy in-flight job adopted at its old flat directory —
            # persist happens through the record.result write-back.
            event_log.append(
                "remote.path_legacy_flat",
                job_id=record.id,
                node=node.name,
                remote_dir=resolved,
            )
        return resolved

    def _owner_probe(self, node: RemoteNode, record: JobRecord):
        """Ownership predicate for the dual-candidate probe (job.json/task.json)."""

        def probe(candidate: str) -> bool:
            for marker in ("job.json", "task.json"):
                try:
                    raw = self._stager.read_remote_file(
                        node, posixpath.join(candidate, marker)
                    )
                except (OSError, SSHExecutionError):
                    continue
                if not raw:
                    continue
                try:
                    payload = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if not isinstance(payload, dict):
                    continue
                owner = payload.get("id") or payload.get("task_id")
                if owner == record.id:
                    return True
            return False

        return probe

    def _archive_remote_attempt(
        self,
        node: RemoteNode,
        remote_job_dir: str,
        record: JobRecord,
        event_log: JobEventLog,
        disposition: str,
    ) -> None:
        """Archive the previous attempt's receipts into ``attempts/<n>/``.

        Runs inside the submit thread BEFORE ``bsub``: a failure aborts the
        submission (no PENDING published before archiving completes) and the
        poller never observes the directory first.  ``checkpoint.json`` /
        ``step_result*.json`` / ``RESULT/`` are archived too unless this is
        a **continue** (``remote.resume``), which adopts them in place.
        """
        previous_attempt = record.attempt - 1
        if previous_attempt < 1 or disposition == "created":
            return
        meta = (record.result or {}).get("remote")
        adopt_results = isinstance(meta, dict) and bool(meta.get("resume"))
        archive_root = posixpath.join(
            remote_job_dir, "WORK", "00_RUNTIME", "attempts", str(previous_attempt)
        )
        moved: list[str] = []

        def _move(rel_src: str, rel_dst: str) -> None:
            src = posixpath.join(remote_job_dir, rel_src)
            if not self._stager.remote_exists(node, src):
                return
            self._stager.rename_remote(node, src, posixpath.join(archive_root, rel_dst))
            moved.append(rel_src)

        for name in (".exit_code", "state.json"):
            _move(name, name)
        try:
            entries = self._stager.list_remote_dir(node, remote_job_dir)
        except (FileNotFoundError, OSError):
            entries = []
        for entry in entries:
            if entry.name.startswith(".stage_"):
                _move(entry.name, entry.name)
        runtime_rel = posixpath.join("WORK", "00_RUNTIME")
        _move(posixpath.join(runtime_rel, "run.lock"), posixpath.join(runtime_rel, "run.lock"))
        if adopt_results:
            if moved:
                event_log.append(
                    "remote.attempts_archived",
                    job_id=record.id,
                    attempt=previous_attempt,
                    files=moved,
                    adopted_results=True,
                )
            return

        _runtime_checkpoint = posixpath.join(runtime_rel, "checkpoint.json")
        _move(_runtime_checkpoint, _runtime_checkpoint)
        _move("checkpoint.json", "checkpoint.json")
        _move("RESULT", "RESULT")
        try:
            runtime_entries = self._stager.list_remote_dir(
                node, posixpath.join(remote_job_dir, runtime_rel)
            )
        except (FileNotFoundError, OSError):
            runtime_entries = []
        for source_entries, rel_prefix in ((entries, ""), (runtime_entries, runtime_rel)):
            for entry in source_entries:
                if entry.name.startswith("step_result"):
                    rel = posixpath.join(rel_prefix, entry.name)
                    _move(rel, rel)
        if moved:
            event_log.append(
                "remote.attempts_archived",
                job_id=record.id,
                attempt=previous_attempt,
                files=moved,
                adopted_results=False,
            )

    def _receipt_is_current(self, node: RemoteNode, remote_job_dir: str, attempt: int) -> bool:
        """True unless the remote receipts demonstrably belong to an older attempt.

        ``state.json``/``job.json`` may declare the attempt they were
        written for; a declared-but-different attempt means the receipt
        set predates the current attempt and must not drive finalisation.
        Unknown/undeclared receipts stay current (legacy compatibility).
        """
        for marker in ("state.json", "job.json"):
            try:
                raw = self._stager.read_remote_file(
                    node, posixpath.join(remote_job_dir, marker)
                )
            except (OSError, SSHExecutionError):
                continue
            if not raw:
                continue
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(payload, dict):
                declared = payload.get("attempt")
                if isinstance(declared, int) and declared != attempt:
                    return False
        return True

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
                exit_code = self._wait_exit_code(
                    node, remote_job_dir, timeout=_EXIT_CODE_GRACE, attempt=record.attempt
                )
                break

            # --- Definitive terminal signal: .exit_code file ---
            exit_code = self._monitor.get_exit_code(node, remote_job_dir)
            if exit_code is not None and not self._receipt_is_current(
                node, remote_job_dir, record.attempt
            ):
                # Stale receipt from a previous attempt — keep polling.
                exit_code = None
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
                exit_code = self._wait_exit_code(
                    node, remote_job_dir, timeout=_EXIT_CODE_GRACE, attempt=record.attempt
                )
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
        self,
        node: RemoteNode,
        remote_job_dir: str,
        timeout: float = _EXIT_CODE_GRACE,
        attempt: int | None = None,
    ) -> int | None:
        """Poll for ``.exit_code`` for up to *timeout* seconds.

        When *attempt* is given, receipts declaring a different attempt are
        rejected (stale previous-attempt ``.exit_code`` never finalises the
        current attempt).
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ec = self._monitor.get_exit_code(node, remote_job_dir)
            if ec is not None:
                if attempt is None or self._receipt_is_current(node, remote_job_dir, attempt):
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
        "import json, os, sys\n"
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
        "from cccp.software import detect_version, resolve_executable\n"
        "exes = cfg.get('executables') or {}\n"
        "report = {}\n"
        "for name in names:\n"
        "    configured = ((exes.get(name) or {}).get('path')) or name\n"
        "    resolved = resolve_executable(name, configured_path=configured)\n"
        "    version = None\n"
        "    if resolved:\n"
        "        version = detect_version(name, resolved)\n"
        "    report[name] = {\n"
        "        'configured': configured,\n"
        "        'resolved': str(resolved) if resolved else None,\n"
        "        'version': version,\n"
        "    }\n"
        "print(json.dumps(report))\n"
    )

    def _resolve_node_python(self, node: RemoteNode, job_id: str | None = None) -> str:
        """Return a Python 3.10+ interpreter usable on *node*.

        ``node.python_executable`` is honoured when it satisfies the floor;
        otherwise the default candidates (named interpreters then common
        conda installs) are probed over SSH.  The result is cached per node
        for 300 s so repeated submissions don't re-probe every time.

        Raises:
            RemoteSubmissionError: When no interpreter on the node meets
                the floor — the job fails fast here with configuration
                guidance instead of being submitted only to crash the LSF
                script with an ``ImportError`` seconds later.
        """
        cached = self._python_probe_cache.get(node.name)
        if cached is not None and (time.monotonic() - cached[0]) < 300:
            return cached[1]

        probe = detect_node_python(self._ssh, node)
        if probe is None:
            configured = node.python_executable
            hint = (
                f"configured python_executable {configured!r} is not a runnable "
                "Python 3.10+ interpreter"
                if configured
                else "no Python 3.10+ interpreter found — configure "
                "cluster.nodes[].python_executable (e.g. an anaconda/miniconda "
                "python) or install Python 3.10+ on the node"
            )
            logger.error("Python probe on %s failed: %s", node.name, hint)
            raise RemoteSubmissionError(f"Node {node.name!r}: {hint}")
        logger.info(
            "Resolved node python for %s: %s (version %s)",
            node.name,
            probe.python_executable,
            probe.version,
        )
        self._python_probe_cache[node.name] = (time.monotonic(), probe.python_executable)
        return probe.python_executable

    def _probe_required_binaries(
        self, node: RemoteNode, spec: JobSpec, event_log: JobEventLog, job_id: str
    ) -> None:
        """Probe workflow-required binaries on *node* before submission.

        Resolves each binary with the same centralized resolver the
        workflow uses (:func:`cccp.software.resolve_executable` against
        the node-side ``~/.cccp.yaml`` merged with PATH, driven by the
        synced codebase under ``remote_code_dir``) so the probe and the
        job agree on what is available.  When
        ``cluster.require_all_binaries`` is set (default), any missing
        required binary raises :class:`RemoteNodeUnavailableError` with
        configuration guidance — the job is never submitted to crash
        seconds later.  With ``require_all_binaries: false`` missing
        binaries only log a warning (they may be provided by the LSF job
        environment).  A missing ``censo`` always raises.  SSH/transport
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

        py = self._resolve_node_python(node, job_id=job_id)
        script_arg = _shlex.quote(self._BINARY_PROBE_SCRIPT)
        args = " ".join(_shlex.quote(b) for b in binaries)
        # The probe imports cccp.software from the synced codebase — export
        # PYTHONPATH so it resolves exactly like the workflow will.
        command = (
            "bash -lc "
            + _shlex.quote(
                f"export PYTHONPATH={node.remote_code_dir}/src:$PYTHONPATH && "
                f"{py} -c {script_arg} {args}"
            )
        )

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

        if missing and self._config.require_all_binaries:
            detail = "\n".join(
                f"  - {name}: configured={report[name].get('configured')!r}"
                for name in missing
            )
            raise RemoteNodeUnavailableError(
                f"Node {node.name!r} cannot resolve required binaries for "
                f"workflow {spec.workflow!r} — submission aborted:\n{detail}\n"
                f"Fix per binary: add it to PATH (or ~/bin), set "
                f"CONFSEARCH_<NAME>_PATH, configure executables.<name>.path "
                f"in the node-side ~/.cccp.yaml, or declare it via "
                f"cluster.nodes[].bin_symlinks and re-bootstrap the node. "
                f"To keep the historical warn-only behaviour, set "
                f"cluster.require_all_binaries: false."
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
        """Map the current LSF status to the remote_execution stage state.

        ``paused`` is deliberately not mirrored: the stage-state
        vocabulary has no paused member, and regressing a started stage
        to ``pending`` would misrepresent it.  The last mirrored state
        stands until the LSF job resumes (``running``) or finalises.
        """
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


__all__ = ["RemoteJobRunner", "RemoteNodeUnavailableError", "RemoteSubmissionError"]
