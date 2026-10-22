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

import functools
import json
import logging
import math
import os
import queue
import random
import shutil
import signal
import socket
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from acp.calculations.contracts import JsonValue
from acp.scheduler.artifacts import ArtifactRegistry
from acp.scheduler.capabilities import (
    NoCapableNodeError,
    derive_required_software,
    local_satisfies,
)
from acp.scheduler.events import JobEventLog
from acp.scheduler.input_snapshot import input_xyz_snapshot
from acp.scheduler.job_edit import (
    EditConflictError,
    JobEditOperationStore,
    attempt_number,
    compute_source_revision,
    normalize_for_compare,
)
from acp.scheduler.jobs import (
    ALL_WORKFLOWS,
    EXIT_WAITING_REVIEW,
    PUBLIC_WORKFLOWS,
    JobRecord,
    JobSpec,
    JobStatus,
)
from acp.scheduler.metrics import MetricsExtractor
from acp.scheduler.nodes import (
    LOCAL_NODE_NAME,
    ExecutionCapacityUnavailable,
    ExecutionTargetError,
    NodeRegistry,
    NodeSpec,
    validate_execution_request,
    validate_submission_target,
)
from acp.scheduler.processctl import pid_is_alive, read_cmdline, terminate_task_processes
from acp.scheduler.projects import ProjectManager
from acp.scheduler.provenance import compute_input_hash
from acp.scheduler.resources import with_job_resources
from acp.scheduler.runner import (
    JobRunner,
    find_workflow_state,
)
from acp.scheduler.stage_tasks import StageTaskObserver, StageTaskStore
from acp.scheduler.store import JobStateConflictError, JobStore
from acp.scheduler.tasks import PROJECTION_RECONCILE_BATCH, TaskIndex
from acp.services_events_bootstrap import open_outbox_delivery
from acp.storage.layout import TaskStorage, runtime_file, sanitize_existing_task_dir_name
from acp_services.contracts.envelope import EnvelopeError
from acp_services.data.contracts import ResourceKind
from acp_services.data.operations import (
    PrepareOperationService,
    PrepareOperationWorker,
    PrepareRequest,
)
from acp_services.runtime.attempt_freeze import ATTEMPT_LOCK_FILENAME
from acp_services.runtime.execution_chain import (
    ATTEMPT_PROTOCOL_FILENAME,
    BINDING_FILENAME,
    DEFAULT_ENVELOPE_WORKFLOWS,
    ENVELOPE_FILENAME,
    EXECUTION_ENTRY_ENVELOPE,
    FROZEN_GROUP_DIRNAME,
    FROZEN_GROUP_MANIFEST_FILENAME,
    PLAN_FILENAME,
    AttemptProtocolError,
    attempt_choice_from_result,
    read_attempt_choice,
)
from acp_services.runtime.helpers import utc_now_iso as _utc_now_iso
from acp_services.runtime.idempotency import (
    IdempotencyConflictError,
    IdempotencyContext,
    IdempotencyInProgressError,
    IdempotencyLeaseError,
    IdempotencyOutcome,
    IdempotencyReservationDeniedError,
    ReservationPort,
)
from acp_services.runtime.local_admission import AdmissionRefusedError
from acp_services.runtime.outbox import OutboxDeliveryResult, OutboxEventSpec
from acp_services.runtime.receipts import verify_receipt
from acp_services.runtime.worker import (
    TASK_RESULT_RELATIVE_PATH,
    ReceiptConflictError,
    read_receipt,
)

logger = logging.getLogger(__name__)

_CALCULATION_CHECKPOINT_PATH: Final = "WORK/00_RUNTIME/checkpoint.json"
_NO_CHECKPOINT_MESSAGE: Final = "该工作流不支持断点续算，请使用重算 (rerun)"
_BATCH_NO_CONTINUE_MESSAGE: Final = "BatchOptimize 不支持断点续算，请使用重算 (rerun)"
_RETIRED_INFLIGHT_REASON: Final[str] = (
    "[RESTART_FAILED] workflow retired by calc-refactor — 历史任务只读，可查看/清除，不可继续"
)
# Task-identity files preserved across an in-place rerun; everything else
# in the task directory (WORK, RESULT, legacy _attempts, logs, markers)
# is attempt-scoped and gets cleared.
_RERUN_STABLE_FILES: Final[frozenset[str]] = frozenset(
    {"input.xyz", "input_source.json", "task.json", "job.json"}
)
# Workflows with first-class resume support at startup-triage time; every
# other workflow only hints "try continue" when a generic checkpoint exists.
_STARTUP_RESUMABLE_WORKFLOWS: Final[frozenset[str]] = frozenset({"mechanism", "xtbmd_censo_energy"})
# Single-instance guard: run_root ownership marker file (see
# ``JobManager._acquire_instance_lock``).
_MANAGER_LOCK_NAME: Final = ".manager.lock"
# Delay before the startup release-GC tick (D03 plan (C) production
# trigger).  Keeps boot-time SSH traffic off the critical start sequence;
# the periodic `_cleanup_loop` tick covers later sweeps.  Tests patch this
# to 0.0 to assert prune_releases reachability deterministically.
_RELEASE_GC_STARTUP_DELAY: Final = 300.0

# The poll scan set — single owner of these statuses: ``_poll_loop`` iterates
# them and ``_poll_job`` refuses anything outside them.  STARTING and PAUSED
# are intentionally absent (submission reconciliation and pause/unpause own
# them); CANCELLING stays here because cancel confirmation is part of the
# regular poll pass.
_POLL_SCAN_STATUSES: Final[tuple[JobStatus, ...]] = (
    JobStatus.RUNNING,
    JobStatus.PENDING,
    JobStatus.CANCELLING,
)

_SUBMIT_RECONCILE_STATES: Final[frozenset[str]] = frozenset({"intent", "unconfirmed"})

# ``get_lsf_status`` classifications (contract A): the first set positively
# proves the job is gone/terminated → cancel confirmation evidence; the
# second means the job is still alive → bkill must be (re)sent.  Anything
# else (``unknown`` / communication failure) keeps CANCELLING unconfirmed.
_CANCEL_CONFIRMED_LSF: Final[frozenset[str]] = frozenset({"not_found", "done", "failed"})
_CANCEL_ALIVE_LSF: Final[frozenset[str]] = frozenset({"pending", "running", "paused"})

# Consecutive orphan-cancel failures before the stalled alert fires; retry
# keeps running at the capped (1h) backoff — never unbounded fast retries.
_ORPHAN_STALL_THRESHOLD: Final[int] = 5

# Upper bound on shutdown join passes. ``_closing`` makes new registrations
# impossible, so a single pass normally suffices; re-scanning is insurance and
# the bound only guards against a latent registration bug.
_SHUTDOWN_JOIN_PASS_LIMIT: Final[int] = 5


def _parse_iso_ts(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _needs_submission_reconcile(record: JobRecord) -> bool:
    """Category ① of the reconcile loop: STARTING with a pending submission."""
    if record.status != JobStatus.STARTING:
        return False
    return _has_pending_submission(record)


def _has_pending_submission(record: JobRecord) -> bool:
    """True when ``result["remote"]["submit_state"]`` is intent/unconfirmed."""
    remote_meta = (record.result or {}).get("remote")
    if not isinstance(remote_meta, dict):
        return False
    return remote_meta.get("submit_state") in _SUBMIT_RECONCILE_STATES


def _needs_publication_retry(record: JobRecord) -> bool:
    """Category ②b of the reconcile loop: terminal row with pending publication.

    The pending fact is written by the T26 terminal CAS together with the
    status transition; a crash after that CAS but before the publisher ran is
    recovered from this marker without a second QC execution.
    """
    if not record.status.is_terminal:
        return False
    if record.spec.workflow not in DEFAULT_ENVELOPE_WORKFLOWS:
        return False
    return (record.result or {}).get("publication_pending") is True


def _needs_side_effect_retry(record: JobRecord) -> bool:
    """Category ② of the reconcile loop: terminal but side effects unfinished.

    Only records whose terminal transition went through the new poll path
    carry an explicit ``False`` marker; legacy terminal jobs never carry the
    key and are skipped.
    """
    if not record.status.is_terminal:
        return False
    return (record.result or {}).get("terminal_side_effects_done") is False


def _derive_retired_workflows() -> frozenset[str]:
    """Derive retired workflow IDs from the catalog."""
    try:
        from acp.catalog import WORKFLOW_CATALOG
    except ImportError:
        return frozenset()
    return frozenset(w["id"] for w in WORKFLOW_CATALOG if w.get("status") == "retired")


_RETIRED_WORKFLOWS: frozenset[str] = _derive_retired_workflows()

# Type-only import to avoid requiring paramiko when remote execution is off.
if TYPE_CHECKING:
    from acp.results.remote_structure_cache import RemoteStructureCache
    from acp.scheduler.local_cleanup import LocalCleanup, LocalCleanupReport, RetentionPolicy
    from acp.scheduler.remote.cleanup import RemoteCleanup
    from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
    from acp.scheduler.remote.fetcher import RemoteResultFetcher
    from acp.scheduler.remote.monitor import RemoteJobMonitor
    from acp.scheduler.remote.node_manager import NodeManager
    from acp.scheduler.remote.runner import RemotePollObservation
    from acp.scheduler.remote.ssh import SSHConnectionPool


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
    # resume counter: v2 checkpoints serialise ``resume_count``; legacy v1
    # files keep the frozen ``attempts`` key (same counter, old name).
    resume_count = payload.get("resume_count", payload.get("attempts"))
    if not isinstance(task_id, str) or not isinstance(workflow, str):
        return None
    if not isinstance(fingerprint, str) or not fingerprint:
        return None
    if not isinstance(step_states, list) or not isinstance(items_state, dict):
        return None
    if not isinstance(resume_count, int) or isinstance(resume_count, bool):
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


class _RunRootClaim:
    """In-process ownership record for one resolved ``run_root``.

    ``initialized`` flips True only after the owning :class:`JobManager`
    finishes construction; until then the claim blocks concurrent same-root
    entries but is not yet a *live* owner for same-PID stale-lock detection.
    """

    __slots__ = ("key", "manager", "initialized")

    def __init__(self, key: str, manager: JobManager) -> None:
        self.key = key
        self.manager = manager
        self.initialized = False


#: Process-wide run_root ownership registry (resolved path -> claim).  A second
#: concurrent lifespan entry / JobManager for the same resolved run_root is
#: refused even before either one touches the file lock.
_RUN_ROOT_CLAIMS: dict[str, _RunRootClaim] = {}
_RUN_ROOT_CLAIMS_LOCK = threading.Lock()


def resolve_ownership_key(run_root: Path | str) -> str:
    """Canonical ownership key: relative paths and symlink aliases collapse."""
    return str(Path(run_root).resolve())


def _release_ownership_on_init_failure(init: Callable[..., None]) -> Callable[..., None]:
    """Release ownership when a ``JobManager`` constructor raises mid-init.

    The run_root claim is taken before initialization; a construction failure
    (lock held by a peer, store/index error, thread-start error) must release
    the claim, any acquired file lock and any started workers so that an
    immediate retry starts clean and leaves no residue.
    """

    @functools.wraps(init)
    def _wrapped(self: JobManager, *args: Any, **kwargs: Any) -> None:
        try:
            init(self, *args, **kwargs)
        except BaseException:
            self._rollback_failed_init()
            raise

    return _wrapped


class JobManager:
    """Central job orchestrator backed by SQLite + background poller."""

    @_release_ownership_on_init_failure
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
        reconnect_local_workers: bool = False,
        envelope_completion: bool | None = None,
        attempt_completion: Any | None = None,
        attempt_publication: Any | None = None,
        idempotency_reservations: ReservationPort | None = None,
        admission: Any | None = None,
    ):
        self._idempotency_reservations = idempotency_reservations
        self._admission = admission
        self._idempotency_adoption_lock = threading.Lock()
        #: Test-only crash-injection seam (mirrors JobStore's hook).  A callable
        #: invoked at named points of the staged idempotent submit; raising or
        #: ``os._exit`` from it models a process crash at that boundary.
        self._fault_point_hook: Callable[[str], None] | None = None
        #: Persistent controller/worker connection mode (B07-8 / todo 74):
        #: a *reconnecting controller* skips destructive startup recovery and
        #: queue draining, and re-attaches to worker processes that survived a
        #: previous controller via the persisted ``run.lock`` / ``/proc``
        #: identity (otherwise a second controller marks a still-computing
        #: RUNNING job ``[RESTART_FAILED]``).
        self._reconnect_local_workers = bool(reconnect_local_workers)
        # 07-B opt-in integration: only an envelope-selected local attempt whose
        # service is explicitly injected/enabled uses receipt-verified completion.
        # The ordinary default stays exit-code-only until task 29 activates it.
        self._attempt_completion = attempt_completion
        self._attempt_publication = attempt_publication
        if envelope_completion is None:
            envelope_completion = bool(DEFAULT_ENVELOPE_WORKFLOWS)
        self._envelope_completion_enabled = bool(
            envelope_completion or attempt_completion is not None or attempt_publication is not None
        )
        self._shutdown_lock = threading.Lock()
        self._shutdown_complete = False
        # Closing latch: set by ``_stop_background_work`` (under ``self._lock``)
        # before any worker is signalled or joined. Once set, no new submission
        # or prefetch worker may register/start, so the join pass is a closed
        # set — ownership is never released while a worker still runs.
        self._closing = False
        self.run_root = Path(run_root)
        self.run_root.mkdir(parents=True, exist_ok=True)
        # Ownership guard, two layers: an in-process registry keyed by the
        # RESOLVED run_root (refuses a second concurrent manager in this
        # process) and the cross-process ``.manager.lock`` file (refuses a
        # foreign ACP).  The registry claim is taken BEFORE initialization and
        # released by ``_rollback_failed_init`` on any construction error.
        self._ownership_key = resolve_ownership_key(self.run_root)
        self._manager_lock_path: Path | None = None
        self._manager_lock_token: str | None = None
        self._claim_run_root()
        self._acquire_instance_lock()
        logger.info("JobManager booted: pid=%s run_root=%s", os.getpid(), self.run_root)
        self.store = store or JobStore(self.run_root / "acp_jobs.db")
        if self._attempt_completion is None and self._envelope_completion_enabled:
            from acp.calculations.attempt_publication import AttemptResultPublication
            from acp.services_completion_bootstrap import build_attempt_completion_service

            publication = attempt_publication or AttemptResultPublication()
            self._attempt_publication = publication
            self._attempt_completion = build_attempt_completion_service(
                self.store, publication=publication
            )
        elif self._attempt_publication is None and self._attempt_completion is not None:
            self._attempt_publication = getattr(self._attempt_completion, "publication", None)
        self._publication_reconcile_enabled = bool(
            self._attempt_completion is not None and self._attempt_publication is not None
        )
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
        #: Batch size B for one task-projection reconcile scan (todo 5):
        #: N drifted rows converge in <= ceil(N/B) scans.
        self._task_reconcile_batch = PROJECTION_RECONCILE_BATCH

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

        # No ThreadPoolExecutor — all submitted jobs run concurrently via
        # the cluster/local system.  A background poller tracks status.
        self._cancel_events: dict[str, threading.Event] = {}
        self._submission_jobs: set[str] = set()
        self._submission_threads: dict[str, threading.Thread] = {}
        self._shutdown_event = threading.Event()
        self._poll_failures: dict[str, int] = {}
        # job_id -> monotonic timestamp of the last poll-path submission
        # reconcile (rate limiter; the reconcile loop is unthrottled).
        self._submit_reconcile_at: dict[str, float] = {}
        self._lock = threading.RLock()
        self._counter = 0
        self._metrics_extractor = MetricsExtractor()

        # Background poller thread.
        self._poll_stop = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="acp-poller")
        # Background reconcile thread (categories outside the poll scan set:
        # STARTING+pending submission, terminal side-effect retries).
        self._reconcile_thread = threading.Thread(
            target=self._reconcile_loop, daemon=True, name="acp-reconciler"
        )
        # Global terminal side-effect boundary.  Lock order is ALWAYS
        # ``_side_effect_lock`` BEFORE ``_lock``: terminal side effects
        # (_persist_terminal / _retry_terminal_side_effects) and the in-place
        # rerun/edit/continue cleanup (rerun_job / _edit_in_place /
        # continue_job, which wrap _inplace_requeue_locked and
        # _reset_work_dir_in_place) take this lock first, so a superseded
        # attempt can never interleave with the new attempt's cleanup.  No
        # code path may acquire ``_lock`` and then wait for this lock.
        self._side_effect_lock = threading.Lock()

        # Persistent prepare operations (B02-3/todo 13): the terminal
        # remote-catalog prefetch is a durable DB operation, not an in-process
        # queue that a restart drops.  The queue below is only a low-latency
        # wake signal; the ``data_operations`` table is the authority.
        self._catalog_prefetch_queue: queue.Queue[str | None] = queue.Queue()
        self._catalog_prefetch_thread: threading.Thread | None = None
        self._prepare_worker_stop = threading.Event()
        self._prepare_operations = PrepareOperationService(self.store.db_path)
        self._prepare_worker: PrepareOperationWorker | None = None
        self._prepare_executor = self._build_prepare_executor()

        # Background local-cleanup thread (Phase 5B step 5B.3).
        self._cleanup_thread: threading.Thread | None = None
        self._cleanup_stop_event: threading.Event | None = None
        self._cleanup_lock = threading.Lock()
        self._cleanup_interval_hours = max(1, int(local_cleanup_interval_hours))
        self._start_cleanup_thread()

        # D03 release-GC production trigger (startup + periodic tick below).
        self._release_gc_stop = threading.Event()
        self._release_gc_thread: threading.Thread | None = None
        self._start_release_gc_thread()

        self._sweep_idempotency_staging()
        if self._reconnect_local_workers:
            self._adopt_live_orphans_on_startup()
            with self._lock:
                self._rebuild_reservations()
        else:
            self._requeue_active_on_startup()
            with self._lock:
                self._rebuild_reservations()
            self._dispatch_queued_jobs()
        self._queue_startup_catalog_prefetch()
        self._resume_pending_publications()
        self._sweep_outbox()
        self._poll_thread.start()
        self._reconcile_thread.start()
        self._activate_run_root()

    # ------------------------------------------------------------------ #
    # Single-instance guard (run_root ownership)
    # ------------------------------------------------------------------ #

    def _claim_run_root(self) -> None:
        """Atomically reserve this resolved run_root in the in-process registry.

        Raises when another manager (initializing or live) already holds the
        same resolved run_root, so a concurrent second lifespan entry is
        refused before either one touches the cross-process file lock.
        """
        key = self._ownership_key
        with _RUN_ROOT_CLAIMS_LOCK:
            existing = _RUN_ROOT_CLAIMS.get(key)
            if existing is not None:
                holder = existing.manager
                holder_pid = os.getpid() if holder is not None else None
                raise RuntimeError(
                    f"run_root '{self.run_root}' is already owned by another ACP "
                    f"JobManager in this process (pid={holder_pid}); refusing to "
                    "start a second instance"
                ) from None
            _RUN_ROOT_CLAIMS[key] = _RunRootClaim(key=key, manager=self)

    def _activate_run_root(self) -> None:
        """Mark this manager's registry claim live (construction succeeded)."""
        key = self._ownership_key
        with _RUN_ROOT_CLAIMS_LOCK:
            claim = _RUN_ROOT_CLAIMS.get(key)
            if claim is not None and claim.manager is self:
                claim.initialized = True

    def _release_run_root_claim(self) -> None:
        """Drop this manager's in-process registry claim (idempotent)."""
        key = getattr(self, "_ownership_key", None)
        if key is None:
            return
        with _RUN_ROOT_CLAIMS_LOCK:
            claim = _RUN_ROOT_CLAIMS.get(key)
            if claim is not None and claim.manager is self:
                del _RUN_ROOT_CLAIMS[key]

    def _release_manager_lock(self) -> None:
        """Unlink the file lock only while it still belongs to this manager."""
        lock_path = getattr(self, "_manager_lock_path", None)
        if lock_path is None:
            return
        if self._manager_lock_belongs_to_me(lock_path):
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Failed to remove manager lock", exc_info=True)
        else:
            logger.debug("Manager lock %s no longer owned; leaving in place", lock_path)
        self._manager_lock_path = None

    def _manager_lock_belongs_to_me(self, lock_path: Path) -> bool:
        token = getattr(self, "_manager_lock_token", None)
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (
            isinstance(payload, dict)
            and payload.get("pid") == os.getpid()
            and payload.get("token") == token
        )

    def _rollback_failed_init(self) -> None:
        """Release claim/lock/workers after a failed construction (no residue)."""
        try:
            self._stop_background_work()
        except Exception:
            logger.exception("Error stopping background work during init rollback")
        try:
            self._release_manager_lock()
        except Exception:
            logger.debug("Error releasing manager lock during init rollback", exc_info=True)
        self._release_run_root_claim()

    def _acquire_instance_lock(self) -> None:
        """Claim exclusive run_root ownership for this server process.

        A leftover lock is stolen when its recorded owner is dead, unreadable,
        or a non-ACP process (PID recycling).  A lock recorded under this very
        PID is stale ONLY when the in-process registry holds no live owner for
        the resolved run_root — otherwise it is a genuinely live same-process
        owner and a second instance is refused.  Only a *live foreign ACP
        process* or a live same-process owner blocks startup.
        """
        lock_path = self.run_root / _MANAGER_LOCK_NAME
        for _attempt in range(3):
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner_pid = self._lock_owner_pid(lock_path)
                if owner_pid is not None and self._owner_is_live_acp(owner_pid):
                    raise RuntimeError(
                        f"run_root '{self.run_root}' is already owned by another ACP "
                        f"server process (pid={owner_pid}, cmdline="
                        f"{read_cmdline(owner_pid)!r}); refusing to start a second "
                        "instance — it would kill the owner's running jobs"
                    ) from None
                logger.warning(
                    "Stealing stale manager lock %s (recorded owner pid=%s)",
                    lock_path,
                    owner_pid,
                )
                lock_path.unlink(missing_ok=True)
                continue
            self._manager_lock_token = uuid.uuid4().hex
            payload = {
                "pid": os.getpid(),
                "cmdline": read_cmdline(os.getpid()),
                "acquired_at": datetime.now(timezone.utc).isoformat(),
                "run_root": str(self.run_root),
                "token": self._manager_lock_token,
            }
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            self._manager_lock_path = lock_path
            return
        raise RuntimeError(f"could not claim manager lock {lock_path} after retries")

    @staticmethod
    def _lock_owner_pid(lock_path: Path) -> int | None:
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        owner = payload.get("pid") if isinstance(payload, dict) else None
        return owner if isinstance(owner, int) and owner > 0 else None

    def _owner_is_live_acp(self, owner_pid: int) -> bool:
        if owner_pid != os.getpid():
            if not pid_is_alive(owner_pid):
                return False
            cmdline = read_cmdline(owner_pid).lower()
            return "acp" in cmdline or "uvicorn" in cmdline
        # Same PID: live only while the in-process registry still holds an
        # *initialized* owner for this resolved run_root.  No live owner means
        # the lock is stale debris from a manager that never shut down.
        with _RUN_ROOT_CLAIMS_LOCK:
            claim = _RUN_ROOT_CLAIMS.get(self._ownership_key)
        return bool(claim is not None and claim.initialized and claim.manager is not None)

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

        Covers all provenance sources: ``remote_job_id``,
        ``result.lsf_job_id``, ``result.execution_kind == "remote"``,
        ``result.target_node``, a pinned remote ``spec.target_node``, and
        the persisted ``result["remote"]`` intent (``submit_state`` / node /
        relative dir) written before an LSF id exists.  A job with no remote
        intent whatsoever is local.  The server default mode is never
        consulted for dispatched jobs.
        """
        if record.remote_job_id:
            return True
        result = record.result or {}
        if result.get("lsf_job_id") or result.get("execution_kind") == "remote":
            return True
        if result.get("target_node"):
            return True
        pinned = record.spec.target_node
        if pinned is not None and pinned != LOCAL_NODE_NAME:
            return True
        remote_meta = result.get("remote")
        if isinstance(remote_meta, dict):
            return bool(
                remote_meta.get("submit_state")
                or remote_meta.get("node")
                or remote_meta.get("relative")
            )
        return False

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
            job_store=self.store,
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
            # Periodic release-GC tick (D03 plan (C)) — DB-capable, so
            # prune_releases is reachable from production retention too.
            self._run_remote_release_gc()

    # ------------------------------------------------------------------ #
    # Remote release GC — production triggers (D03 plan (C))
    # ------------------------------------------------------------------ #

    def _start_release_gc_thread(self) -> None:
        """Schedule the startup release-GC tick (daemon, delay-gated).

        Without this, release GC would have no production caller once
        ``pre_submit_housekeeping`` is decoupled (plan (A)/(B)) and D03
        reclamation would be dead code reachable only from unit tests.
        """
        if self._remote_cleanup is None:
            return

        def _runner() -> None:
            if self._release_gc_stop.wait(_RELEASE_GC_STARTUP_DELAY):
                return
            self._run_remote_release_gc()

        self._release_gc_thread = threading.Thread(
            target=_runner, daemon=True, name="acp-release-gc"
        )
        self._release_gc_thread.start()
        logger.debug("Scheduled startup release GC (delay=%.0fs)", _RELEASE_GC_STARTUP_DELAY)

    def _run_remote_release_gc(self) -> None:
        """Run release GC (with job-dir retention) on every enabled node.

        This is the DB-capable production path: it calls
        ``cleanup_old_jobs(node, with_release_gc=True)`` with the injected
        ``job_store``, so ``prune_releases`` can refresh DB references
        inside the coordination lock before reclaiming anything.
        """
        cleanup = self._remote_cleanup
        if cleanup is None or self._remote_config is None:
            return
        for node in self._remote_config.enabled_nodes:
            try:
                cleanup.cleanup_old_jobs(node, with_release_gc=True)
            except Exception as exc:
                logger.warning("Release GC failed on node %s: %s", node.name, exc)

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

    def submit(
        self,
        spec: JobSpec,
        group_id: str | None = None,
        *,
        idempotency: IdempotencyContext | None = None,
    ) -> JobRecord:
        """Validate, persist, and enqueue a new job. Returns immediately.

        With ``idempotency`` the persistent staged protocol runs (claim ->
        reservation -> work directory -> committed job row -> adoption ->
        dispatch).  Without it the original legacy path is byte-for-byte the
        same, so every existing caller is unchanged.

        Args:
            spec: The job specification to submit.
            group_id: Queue-grouping key. Defaults to the new job's own id
                (self-rooted); pass an ancestor's id to link a clone (e.g.
                a ``rerun_job``) into the same group.
            idempotency: Optional verified-scope idempotency context.
        """
        if idempotency is None:
            return self._submit_legacy(spec, group_id=group_id)
        return self._submit_idempotent(spec, group_id=group_id, idempotency=idempotency)

    def _submit_legacy(self, spec: JobSpec, group_id: str | None = None) -> JobRecord:
        """Original submit path (unchanged for legacy callers)."""
        if spec.workflow not in ALL_WORKFLOWS:
            raise ValueError(
                f"Unsupported workflow: {spec.workflow}. Supported: {PUBLIC_WORKFLOWS}"
            )

        if spec.workflow == "irc":
            spec = self._verified_irc_spec(spec)

        if spec.workflow != "fake":
            spec = with_job_resources(spec)
        spec = replace(
            spec,
            project_id=spec.project_id or self.default_project_id,
            input_hash=spec.input_hash or compute_input_hash(spec),
        )

        job_id = self._next_job_id(spec)

        admission_result: dict[str, Any] | None = None
        if self._admission is not None:
            decision = self._admission.admit_job(job_id, spec, project_id=spec.project_id)
            if not decision.allowed:
                raise AdmissionRefusedError(decision)
            admission_result = self._admission.result_for_reservation(
                decision.reservation_id,
                decision=decision,
            )
        try:
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
                result=({"admission": admission_result} if admission_result is not None else None),
            )
            # BatchOptimize tasks may wait in the queue before the runner stages
            # their input. Keep the submitted geometry visible in the task folder
            # and structure viewer from the moment the task is created.
            if spec.workflow == "BatchOptimize":
                xyz_snapshot = input_xyz_snapshot(spec.input)
                if xyz_snapshot:
                    try:
                        TaskStorage(work_dir).write_input_xyz(xyz_snapshot)
                    except OSError:
                        logger.warning("Could not snapshot input.xyz for queued job %s", job_id)
            self.store.create(record)
        except BaseException:
            if admission_result is not None and self._admission is not None:
                self._admission.release_reservation_id(admission_result.get("reservation_id"))
            raise
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

    def _next_job_id(self, spec: JobSpec) -> str:
        with self._lock:
            self._counter += 1
            seq = self._counter
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        raw_name = spec.name or spec.workflow
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in raw_name)[:40]
        return f"{ts}_{seq:03d}_{safe_name}"

    def _fault_point(self, name: str) -> None:
        hook = self._fault_point_hook
        if hook is not None:
            hook(name)

    def _submit_idempotent(
        self,
        spec: JobSpec,
        *,
        group_id: str | None,
        idempotency: IdempotencyContext,
    ) -> JobRecord:
        if spec.workflow not in ALL_WORKFLOWS:
            raise ValueError(
                f"Unsupported workflow: {spec.workflow}. Supported: {PUBLIC_WORKFLOWS}"
            )
        if spec.workflow == "irc":
            spec = self._verified_irc_spec(spec)
        if spec.workflow != "fake":
            spec = with_job_resources(spec)
        spec = replace(
            spec,
            project_id=spec.project_id or self.default_project_id,
            input_hash=spec.input_hash or compute_input_hash(spec),
        )

        digest = idempotency.digest()
        outcome = self.store.claim_idempotency(
            idempotency.scope_id,
            idempotency.action,
            idempotency.key,
            digest,
            lease_seconds=idempotency.lease_seconds,
        )
        self._fault_point("idempotency.after_claim")
        if outcome.in_progress:
            outcome = self._await_idempotent_claim(idempotency, digest)
        if outcome.conflict:
            raise IdempotencyConflictError(outcome.detail or "idempotency conflict")
        if outcome.replay:
            return self._replay_idempotent_claim(outcome)
        claim = outcome.claim
        if claim is None or claim.owner_token is None:
            raise IdempotencyConflictError("idempotency claim was created without an owner")

        reservations = idempotency.reservations or self._idempotency_reservations
        staging = self._staging_dir(claim.claim_id)
        final_dir: Path | None = None
        reservation_id: str | None = None
        admission_result: dict[str, Any] | None = None
        try:
            if reservations is not None:
                reservation_id = reservations.reserve_for_claim(
                    claim.claim_id,
                    scope_id=idempotency.scope_id,
                    action=idempotency.action,
                    idempotency_key=idempotency.key,
                    spec=spec,
                )
                if self._admission is not None:
                    admission_result = self._admission.result_for_reservation(reservation_id)
            self._fault_point("idempotency.after_reservation")

            staging.mkdir(parents=True, exist_ok=True)
            TaskStorage(staging).ensure_layout()

            job_id = self._next_job_id(spec)
            bound_dir = self._find_claim_bound_dir(claim.claim_id, spec.project_id)
            if bound_dir is not None:
                final_dir = bound_dir
            else:
                final_dir = self._allocate_work_dir(spec, job_id)
                self._write_claim_binding(final_dir, claim)
            spec = replace(spec, name=final_dir.name)

            record = JobRecord(
                id=job_id,
                spec=spec,
                status=JobStatus.QUEUED,
                work_dir=str(final_dir),
                project_id=spec.project_id,
                input_hash=spec.input_hash,
                group_id=group_id or job_id,
                result=({"admission": admission_result} if admission_result is not None else None),
            )
            self._fault_point("idempotency.before_finalize")
            finalized = self.store.finalize_idempotent_job(
                record,
                claim.claim_id,
                claim.owner_token,
                claim.lease_epoch,
            )
            self._fault_point("idempotency.after_finalize")
            if finalized.claim is not None:
                claim = finalized.claim
            self._adopt_staging_dir(staging, final_dir, record, claim)
            self._fault_point("idempotency.after_adoption")
        except BaseException as exc:
            if reservations is not None:
                try:
                    reservations.release_for_claim(claim.claim_id)
                except (OSError, RuntimeError):
                    logger.warning("idempotency reservation sweep failed", exc_info=True)
            terminal_state = (
                "rejected" if isinstance(exc, IdempotencyReservationDeniedError) else "released"
            )
            try:
                self.store.release_idempotency(
                    claim.claim_id,
                    owner_token=claim.owner_token,
                    lease_epoch=claim.lease_epoch,
                    state=terminal_state,
                )
            except (IdempotencyConflictError, IdempotencyLeaseError):
                logger.debug("idempotency claim already moved", exc_info=True)
            if self.store.get(claim.task_id or "") is None:
                shutil.rmtree(staging, ignore_errors=True)
                if final_dir is not None and self._binding_matches(final_dir, claim):
                    shutil.rmtree(final_dir, ignore_errors=True)
            raise

        self._fault_point("idempotency.before_dispatch")
        self._dispatch_queued_jobs()
        return record

    def _await_idempotent_claim(
        self, idempotency: IdempotencyContext, digest: str
    ) -> IdempotencyOutcome:
        deadline = time.monotonic() + float(idempotency.wait_seconds)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IdempotencyInProgressError(
                    "another submission owns this idempotency key; retry with the same key"
                )
            time.sleep(min(0.05, remaining))
            outcome = self.store.claim_idempotency(
                idempotency.scope_id,
                idempotency.action,
                idempotency.key,
                digest,
                lease_seconds=idempotency.lease_seconds,
            )
            if not outcome.in_progress:
                return outcome

    def _replay_idempotent_claim(self, outcome: IdempotencyOutcome) -> JobRecord:
        claim = outcome.claim
        task_id = None if claim is None else claim.task_id
        record = None if not task_id else self.store.get(task_id)
        if record is None or claim is None:
            raise IdempotencyConflictError(
                "idempotency claim is committed but its job row is missing"
            )
        self._resume_idempotent_adoption(record, claim)
        self._dispatch_queued_jobs()
        setattr(record, "_idempotent_replay", True)
        setattr(record, "_idempotency_claim_id", claim.claim_id)
        return record

    def _staging_dir(self, claim_id: str) -> Path:
        return self.run_root / ".staging" / str(claim_id)

    def _write_claim_binding(self, work_dir: Path, claim: Any) -> None:
        path = work_dir / ".claim_binding"
        payload = {
            "claim_id": claim.claim_id,
            "attempt": 1,
            "scope_id": claim.scope_id,
            "action": claim.action,
            "request_digest": claim.request_digest,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _find_claim_bound_dir(self, claim_id: str, project_id: str | None) -> Path | None:
        project_root = self.run_root.resolve() / self._projects.dir_leaf_for(
            str(project_id or self.default_project_id)
        )
        if not project_root.is_dir():
            return None
        for candidate in project_root.iterdir():
            if not candidate.is_dir():
                continue
            binding = candidate / ".claim_binding"
            if not binding.is_file():
                continue
            try:
                payload = json.loads(binding.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict) and payload.get("claim_id") == claim_id:
                return candidate
        return None

    def _binding_matches(self, work_dir: Path, claim: Any) -> bool:
        path = work_dir / ".claim_binding"
        if not path.is_file():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(payload, dict) and payload.get("claim_id") == claim.claim_id

    def _adopt_staging_dir(
        self, staging: Path, work_dir: Path, record: JobRecord, claim: Any
    ) -> None:
        with self._idempotency_adoption_lock:
            work_dir.mkdir(parents=True, exist_ok=True)
            job_json = work_dir / "job.json"
            first_adoption = not job_json.exists()
            if staging.is_dir():
                for child in list(staging.iterdir()):
                    target = work_dir / child.name
                    if target.exists():
                        if child.is_dir():
                            shutil.copytree(child, target, dirs_exist_ok=True)
                        else:
                            shutil.copy2(child, target)
                    else:
                        shutil.move(str(child), str(target))
            TaskStorage(work_dir).ensure_layout()
            self._write_claim_binding(work_dir, claim)
            self._write_job_json(record)
            if first_adoption:
                self._event_log(record).append(
                    "job.created", job_id=record.id, workflow=record.spec.workflow
                )
            if record.spec.workflow == "BatchOptimize":
                xyz_snapshot = input_xyz_snapshot(record.spec.input)
                if xyz_snapshot:
                    try:
                        TaskStorage(work_dir).write_input_xyz(xyz_snapshot)
                    except OSError:
                        logger.warning("Could not snapshot input.xyz for queued job %s", record.id)
            if self.tasks is not None:
                try:
                    self.tasks.sync_from_job(record)
                except Exception:
                    logger.warning("Task index sync failed for job %s", record.id, exc_info=True)
            self._stage_task_observer.initialize_job_stages(record.id, record.spec)
            shutil.rmtree(staging, ignore_errors=True)

    def _resume_idempotent_adoption(self, record: JobRecord, claim: Any) -> None:
        work_dir = Path(record.work_dir)
        if (work_dir / "job.json").is_file():
            return
        staging = self._staging_dir(claim.claim_id)
        self._adopt_staging_dir(staging, work_dir, record, claim)

    def _idempotency_adoption_pending(self, record: JobRecord) -> bool:
        claim = self.store.get_idempotency_claim_for_task(record.id)
        if claim is None or not claim.is_committed:
            return False
        return not (Path(record.work_dir) / "job.json").is_file()

    def _sweep_idempotency_staging(self) -> None:
        """Expire stale claims, remove their staging dirs and sweep reservations."""
        try:
            expired = self.store.expire_stale_idempotency_claims()
        except (sqlite3.Error, OSError):
            logger.warning("idempotency lease sweep failed", exc_info=True)
            return
        reservations = self._idempotency_reservations
        for claim in expired:
            shutil.rmtree(self._staging_dir(claim.claim_id), ignore_errors=True)
            if reservations is not None:
                try:
                    reservations.release_for_claim(claim.claim_id)
                except (OSError, RuntimeError):
                    logger.warning(
                        "idempotency reservation sweep failed for claim %s",
                        claim.claim_id,
                        exc_info=True,
                    )

    def _verified_irc_spec(self, spec: JobSpec) -> JobSpec:
        """Build an IRC spec exclusively from a verified upstream TS result."""
        from acp.calculations.irc.source import resolve_verified_ts_source

        inp = spec.input
        job_id = str(inp.get("source_job_id") or "").strip()
        product_id = str(inp.get("source_product_id") or "").strip()
        if not job_id or not product_id:
            raise ValueError(
                "IRC requires source_job_id and source_product_id from a verified TS result"
            )
        source_record = self.store.get(job_id)
        if source_record is None:
            raise ValueError(f"IRC source job not found: {job_id}")
        source = resolve_verified_ts_source(
            source_record, product_id, getattr(self, "_remote_fetcher", None)
        )
        previous = inp.get("ts_source")
        if isinstance(previous, dict):
            current = source.provenance()
            locked_fields = (
                "job_id",
                "product_id",
                "geometry_sha256",
                "method",
                "basis",
                "charge",
                "multiplicity",
                "imaginary_frequency_cm1",
                "frequency_evidence_sha256",
                "source_completed_at",
            )
            if any(previous.get(key) != current[key] for key in locked_fields):
                raise ValueError("IRC source evidence changed since the previous submission")
        requested = dict(spec.method)
        levels = requested.get("levels")
        irc_level = levels.get("irc") if isinstance(levels, dict) else None
        for key, expected in (("charge", source.charge), ("multiplicity", source.multiplicity)):
            if key in inp and inp[key] is not None:
                try:
                    override = int(inp[key])
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(f"IRC {key} must match the source TS ({expected})") from exc
                if override != expected:
                    raise ValueError(f"IRC {key} must match the source TS ({expected})")
        for key in ("method", "basis", "functional"):
            for value in (
                requested.get(key),
                irc_level.get(key) if isinstance(irc_level, dict) else None,
            ):
                expected = source.basis if key == "basis" else source.method
                if value not in (None, "", expected):
                    raise ValueError(f"IRC {key} must match the source TS calculation ({expected})")
        for key in ("solvent", "solvent_model", "dispersion", "grid", "electronic_state"):
            for block in (requested, irc_level if isinstance(irc_level, dict) else {}):
                if block.get(key) not in (None, "", "none"):
                    raise ValueError(f"IRC {key} override is not supported for this TS source")
        for block in (requested, irc_level if isinstance(irc_level, dict) else {}):
            if block.get("engine") not in (None, "", "orca"):
                raise ValueError("IRC engine must match the ORCA TS calculation")
        maxpoints = (
            requested.get("maxpoints")
            or (irc_level.get("maxpoints") if isinstance(irc_level, dict) else None)
            or 100
        )
        step = (
            requested.get("step")
            or (irc_level.get("step") if isinstance(irc_level, dict) else None)
            or 0.1
        )
        try:
            maxpoints = int(maxpoints)
            step = float(step)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("IRC maxpoints and step must be valid numbers") from exc
        if maxpoints < 1 or not math.isfinite(step) or step <= 0:
            raise ValueError("IRC maxpoints and step must be positive finite numbers")
        directions = inp.get("directions") or ["forward", "reverse"]
        if (
            not isinstance(directions, (list, tuple))
            or not directions
            or set(directions) - {"forward", "reverse"}
        ):
            raise ValueError("IRC directions must be forward and/or reverse")
        # An XYZ snapshot is persisted in the IRC spec and materialized by
        # the runner. No path into another task is used during execution.
        return replace(
            spec,
            input={
                "source_type": "xyz_text",
                "source": source.xyz,
                "input_role": "transition_state",
                "directions": list(directions),
                "source_job_id": job_id,
                "source_product_id": product_id,
                "ts_source": source.provenance(),
                "charge": source.charge,
                "multiplicity": source.multiplicity,
            },
            method=source.method_payload(maxpoints=maxpoints, step=step),
            config_path=source_record.spec.config_path,
        )

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
        if self.tasks is not None:
            try:
                self.tasks.update_project(job_id, project_id)
            except Exception:
                logger.warning("Task index project sync failed for job %s", job_id, exc_info=True)
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
        ``job_id``/``work_dir`` pair.  One exception: a pre-sanitisation
        directory name carrying shell metacharacters is migrated to its
        sanitised form first (``_migrate_unsafe_task_dir``) — the task
        identity files move with the directory, only the unsafe leaf name
        is retired.

        Before requeueing, every process still bound to the task directory
        (including orphans from a previous service run) is terminated, then
        all attempt-scoped content (``WORK``/``RESULT``, legacy ``_attempts``,
        logs and markers) is cleared in place — no ``_attempts/`` archive is
        created.  Only ``input.xyz``/``input_source.json``/``task.json``/
        ``job.json`` survive; attempt history lives in the database
        ``result`` metadata alone.

        ``project_id`` is retained as a backwards-compatible request
        parameter.  It may only repeat the task's current project; moving or
        copying a task remains the responsibility of ``move``/``clone``.
        """
        record = self.store.get(job_id)
        if record is None:
            return None

        # Shared terminal-side-effect boundary: take _side_effect_lock BEFORE
        # _lock (documented order) so this cleanup cannot interleave with an
        # in-flight terminal side effect for the superseded attempt.
        with self._side_effect_lock:
            with self._lock:
                # Re-read under the manager lock so two rapid clicks cannot both
                # act on the same stale terminal snapshot.
                outcome = self._inplace_requeue_locked(
                    job_id,
                    mode="rerun",
                    new_spec=None,
                    expected_source_revision=None,
                    project_id=project_id,
                )
                if outcome is None:
                    return None
                record, attempts, old_status, killed, renamed_from = outcome

            self._finish_inplace_requeue(record, renamed_from=renamed_from)
            self._event_log(record).append(
                "job.rerun",
                job_id=job_id,
                rerun_from=old_status,
                attempts=attempts,
                work_dir=record.work_dir,
                killed_pids=killed,
                renamed_from=renamed_from,
            )
            self._start_submission_thread(job_id, f"acp-rerun-{job_id}")
        return record

    def _inplace_requeue_locked(
        self,
        job_id: str,
        *,
        mode: str,
        new_spec: JobSpec | None,
        expected_source_revision: str | None,
        project_id: str | None = None,
    ) -> tuple[JobRecord, int, str, list[int], str | None] | None:
        """Shared in-place requeue core (rerun + edit-recalculate, plan §10).

        Must be called while holding ``self._lock``; re-reads the record,
        validates the state machine, migrates a shell-unsafe directory name
        to its sanitised form, clears the task directory, optionally
        installs an edited spec, and flips the record to QUEUED.  Returns
        ``(record, new_attempts, old_status, killed_pids, renamed_from)``
        or ``None`` when the job vanished between reads.
        """
        record = self.store.get(job_id)
        if record is None:
            return None
        current_project = record.project_id or record.spec.project_id
        if project_id is not None and project_id != current_project:
            raise ValueError("原地重跑不能切换项目，请使用复制到项目")
        if not record.status.is_terminal:
            raise ValueError(f"rerun requires a terminal status; got {record.status.value}")
        if job_id in self._submission_jobs:
            raise ValueError(f"job {job_id} is already being submitted")
        if expected_source_revision is not None:
            current_revision = compute_source_revision(record)
            if current_revision != expected_source_revision:
                raise EditConflictError(
                    "源任务配置已变化（source_revision 不匹配）；请刷新编辑草稿后重试"
                )
        if new_spec is not None:
            if new_spec.workflow != record.spec.workflow:
                raise ValueError("原地重算锁定工作流；如需更换工作流请使用「创建新任务」")
            # Lock physical identity: the directory allocator is the single
            # source of truth for the task label; project moves go through
            # move_job.
            new_spec = replace(
                new_spec,
                name=record.spec.name,
                output_dir=record.spec.output_dir,
                project_id=current_project,
            )
        if record.spec.workflow == "irc":
            new_spec = self._verified_irc_spec(new_spec or record.spec)
        if record.spec.workflow != "fake":
            new_spec = with_job_resources(new_spec or record.spec)

        killed = self._terminate_stale_task_processes(record)
        if self._has_live_task_process(record):
            raise ValueError(
                f"job {job_id} still has live process(es) in its task directory; "
                "refusing to rerun — terminate them first"
            )

        from acp.results.structure_snapshots import preserve_outputs
        from acp.scheduler.job_edit import resolve_previous_outputs

        read_root = Path(record.work_dir)
        skipped_collections: list[dict[str, Any]] = []
        from acp.scheduler.structure_sources import StructureSourceService

        if StructureSourceService._is_remote(record):
            cached_root = self.structure_cache.fetch_catalog(record, record.spec.workflow)
            from acp.confsearch.manifest import find_confsearch_manifest
            from acp.results.manifest import load_result_manifest

            if cached_root is None or (
                load_result_manifest(cached_root) is None
                and find_confsearch_manifest(cached_root) is None
            ):
                logger.warning(
                    "No synced remote results for %s; rerun proceeds without "
                    "preserving old outputs",
                    record.id,
                )
                previous_outputs = []
            else:
                self.structure_cache.fetch_reusable_geometries(record, cached_root)
                previous_outputs = resolve_previous_outputs(
                    cached_root,
                    job_id=record.id,
                    project_id=current_project,
                    attempt=attempt_number(record),
                    strict=True,
                    skipped=skipped_collections,
                )
        else:
            previous_outputs = resolve_previous_outputs(
                read_root,
                job_id=record.id,
                project_id=current_project,
                attempt=attempt_number(record),
                strict=True,
                skipped=skipped_collections,
            )
        preserved_outputs = preserve_outputs(
            self.run_root,
            Path(record.work_dir),
            previous_outputs,
            job_id=record.id,
            attempt=attempt_number(record),
            input_spec=record.spec.input,
        )

        # Contract B (D01): archive this attempt's receipts + the previous
        # resume receipt BEFORE the reset clears stray files.
        _prev_attempt = attempt_number(record)
        self._archive_attempt_receipts(record, _prev_attempt, include_science=False)
        self._archive_previous_resume_source(record, previous_attempt=_prev_attempt)
        # Keep the persisted location unchanged if strict cleanup fails.
        self._reset_work_dir_in_place(record, strict=True)
        renamed_from = self._migrate_unsafe_task_dir(record)
        if renamed_from is not None and new_spec is not None:
            new_spec = replace(new_spec, name=record.spec.name)

        attempts = attempt_number(record) + 1
        old_status = record.status.value
        previous_result = dict(record.result or {})
        # Affinity capture (design §3.4): remember where this job ran so
        # the rerun's auto dispatch prefers the same node.
        source_target = previous_result.get("execution_target")
        history = previous_result.get("attempt_history")
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "attempt": attempts - 1,
                "mode": mode,
                "status": old_status,
                "completed_at": record.completed_at,
                "exit_code": record.exit_code,
                "previous_outputs": preserved_outputs,
                "skipped_collections": skipped_collections,
                "error": record.error,
            }
        )
        if new_spec is not None and normalize_for_compare(new_spec.input) != normalize_for_compare(
            record.spec.input
        ):
            # The runner re-materialises input.xyz from spec.input on the
            # next launch, but the preserved stale snapshot must never
            # shadow the new selection if materialisation fails (plan §6.2).
            for stale in ("input.xyz", "input_source.json"):
                try:
                    (Path(record.work_dir) / stale).unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "Could not remove stale %s before edit-recalculate of %s",
                        stale,
                        record.id,
                        exc_info=True,
                    )
        effective_spec = new_spec if new_spec is not None else record.spec
        # Contract B: same storage directory across attempts — keep
        # remote/remote_dir, clear only this attempt's submission fields;
        # jobs.attempt (requeue_with_spec) is the single counter, the legacy
        # result["attempts"] key is read-only.
        result: dict[str, Any] = {"attempt_history": history}
        remote_meta = previous_result.get("remote")
        if isinstance(remote_meta, dict):
            remote_meta = dict(remote_meta)
            for stale_key in (
                "lsf_job_id",
                "submit_state",
                "cancel_state",
                "command_line",
                "submission_id",
                "requested_at",
                "resume",
            ):
                remote_meta.pop(stale_key, None)
            result["remote"] = remote_meta
        if "remote_dir" in previous_result:
            result["remote_dir"] = previous_result["remote_dir"]
        if (
            isinstance(source_target, str)
            and source_target != LOCAL_NODE_NAME
            and effective_spec.target_node is None
            and effective_spec.execution_mode is None
        ):
            # Transient affinity hint for the auto branch; consumed when
            # the next execution target is recorded.
            result["affinity_node"] = source_target
        reset_fields: dict[str, Any] = {"result": result}
        if new_spec is not None:
            reset_fields["input_hash"] = compute_input_hash(new_spec)
        record = self._requeue_record_cas(
            job_id,
            new_spec=effective_spec,
            expected=record,
            expected_status=record.status,
            **reset_fields,
        )
        attempts = attempt_number(record)
        self._cancel_events[job_id] = threading.Event()
        return record, attempts, old_status, killed, renamed_from

    def _finish_inplace_requeue(
        self, record: JobRecord, *, renamed_from: str | None = None
    ) -> None:
        """Post-lock side effects shared by rerun and in-place edit-recalculate."""
        self._stage_task_observer.reset_job(record.id)
        self._reset_job_artifacts(record.id)
        TaskStorage(Path(record.work_dir)).ensure_layout()
        self._sync_task_status(record)
        if renamed_from is not None and self.tasks is not None:
            # sync_job_transition only writes status/stage/progress; the
            # path-derived identity columns need a full resync after a rename.
            try:
                self.tasks.sync_from_job(record)
            except Exception:
                logger.warning(
                    "Task index identity sync failed after work-dir rename of %s",
                    record.id,
                    exc_info=True,
                )
        self._update_task_json_status(
            Path(record.work_dir), record.status.value, record=record if renamed_from else None
        )
        self._write_job_json(record)

    def edit_recalculate(
        self,
        job_id: str,
        *,
        mode: str,
        new_spec: JobSpec,
        expected_source_revision: str | None,
        request_id: str,
        payload_hash: str,
        payload_json: str,
        diff_summary: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Apply an edited spec as a new attempt.

        Design reference: ``docs/archive/2026-10-16/history/ACP_Edit_And_Recalculate_Plan.md``.

        ``mode="in_place"`` reuses the shared in-place requeue core with a
        validated spec; ``mode="new_job"`` performs exactly one regular
        :meth:`submit` with lineage recorded on the source task.  The
        ``request_id`` operation record makes retries idempotent across
        network replays and service restarts.
        """
        if mode not in ("in_place", "new_job"):
            raise ValueError(f"invalid edit mode: {mode!r}")
        op_store = JobEditOperationStore(self.store.db_path)
        existing = op_store.get(request_id)
        if existing is not None:
            if existing["job_id"] != job_id:
                raise EditConflictError(
                    f"request_id {request_id} 已用于其他任务；请使用新的 request_id"
                )
            if existing["payload_hash"] != payload_hash:
                raise EditConflictError(
                    f"request_id {request_id} 已用于不同的提交负载；请使用新的 request_id"
                )
            if existing["status"] == "completed":
                result = json.loads(existing["result_json"] or "{}")
                result["replayed"] = True
                return result
            # "prepared"/"failed" rows are re-driven below (crash recovery,
            # plan §10): the in-place operation itself is convergent.
        else:
            op_store.insert_prepared(
                request_id=request_id,
                job_id=job_id,
                mode=mode,
                payload_hash=payload_hash,
                payload_json=payload_json,
            )

        record = self.store.get(job_id)
        if record is None:
            op_store.fail(request_id, f"job not found: {job_id}")
            raise KeyError(job_id)
        try:
            if mode == "in_place":
                result = self._edit_in_place(
                    record,
                    job_id,
                    new_spec=new_spec,
                    expected_source_revision=expected_source_revision,
                    diff_summary=diff_summary or [],
                )
            else:
                result = self._edit_new_job(record, new_spec, diff_summary or [])
        except BaseException as exc:
            op_store.fail(request_id, f"{type(exc).__name__}: {exc}")
            raise
        op_store.complete(request_id, json.dumps(result, default=str))
        return result

    def _edit_in_place(
        self,
        record: JobRecord,
        job_id: str,
        *,
        new_spec: JobSpec,
        expected_source_revision: str | None,
        diff_summary: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._side_effect_lock:
            with self._lock:
                outcome = self._inplace_requeue_locked(
                    job_id,
                    mode="edit_recalculate",
                    new_spec=new_spec,
                    expected_source_revision=expected_source_revision,
                )
                if outcome is None:
                    raise KeyError(job_id)
                record, attempts, old_status, killed, renamed_from = outcome

            self._finish_inplace_requeue(record, renamed_from=renamed_from)
            if self.tasks is not None:
                try:
                    # Identity columns (molecule/task/remark) may have changed.
                    self.tasks.sync_from_job(record)
                except Exception:
                    logger.warning(
                        "Task index identity sync failed for job %s", record.id, exc_info=True
                    )
            self._event_log(record).append(
                "job.edit_recalculate",
                job_id=job_id,
                mode="in_place",
                rerun_from=old_status,
                attempts=attempts,
                request_id=record.id,
                changed_fields=[entry.get("path") for entry in diff_summary],
                killed_pids=killed,
                work_dir=record.work_dir,
                renamed_from=renamed_from,
            )
            self._start_submission_thread(job_id, f"acp-edit-{job_id}")
        return {
            "job_id": job_id,
            "attempt": attempts,
            "operation": "in_place",
            "status": record.status.value,
            "replayed": False,
        }

    def _edit_new_job(
        self,
        record: JobRecord,
        new_spec: JobSpec,
        diff_summary: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if new_spec.workflow not in ALL_WORKFLOWS:
            raise ValueError(
                f"Unsupported workflow: {new_spec.workflow}. Supported: {PUBLIC_WORKFLOWS}"
            )
        new_spec = replace(new_spec, output_dir=None)
        created = self.submit(new_spec, group_id=record.group_id)
        source_ref = new_spec.input.get("source_ref") if isinstance(new_spec.input, dict) else None
        source_job_id = str(source_ref.get("job_id") or "") if isinstance(source_ref, dict) else ""
        if not source_job_id and isinstance(new_spec.input, dict):
            source_job_id = str(new_spec.input.get("source_job_id") or "")
        self._event_log(created).append(
            "job.edit_recalculate_parent",
            job_id=created.id,
            parent_job_id=record.id,
            source_job_id=source_job_id or None,
        )
        self._event_log(record).append(
            "job.edit_recalculate",
            job_id=record.id,
            mode="new_job",
            new_job_id=created.id,
            changed_fields=[entry.get("path") for entry in diff_summary],
        )
        return {
            "job_id": created.id,
            "attempt": 1,
            "operation": "new_job",
            "status": created.status.value,
            "replayed": False,
        }

    def _migrate_unsafe_task_dir(self, record: JobRecord) -> str | None:
        """Rename a pre-sanitisation task directory to a shell-safe leaf name.

        Directories created before the forbidden-char policy (incident
        2026-09-26: ``frame_1_(TS,_opt_freq_sp_thermo)_irc`` aborts ORCA's
        unquoted startup command with ``sh: Syntax error: "(" unexpected``)
        keep failing on every rerun because the in-place requeue reuses the
        original directory.  Must be called while the job is terminal, no
        live process holds the directory, and the manager lock is held.

        Returns:
            The previous work-dir path when a rename happened, else ``None``.

        Raises:
            RuntimeError: When the directory exists but cannot be renamed —
                the requeue must be blocked rather than queue another
                attempt inside the poison directory.
        """
        work_dir = Path(record.work_dir)
        safe_name = sanitize_existing_task_dir_name(work_dir.name)
        if safe_name == work_dir.name:
            return None
        target = self._dedupe_task_dir(work_dir.with_name(safe_name))
        if work_dir.exists() and not work_dir.is_dir():
            raise RuntimeError(f"任务目录不是文件夹，已阻断重跑: {work_dir}")
        moved = False
        if work_dir.is_dir():
            try:
                work_dir.rename(target)
                moved = True
            except OSError as exc:
                raise RuntimeError(
                    f"任务目录名含 shell 特殊字符且重命名失败，已阻断重跑: "
                    f"{work_dir} -> {target}: {exc}"
                ) from exc
        old_spec = record.spec
        record.work_dir = str(target)
        record.spec = replace(record.spec, name=target.name)
        try:
            self.store.update_work_dir_and_name(record)
        except Exception:
            record.work_dir = str(work_dir)
            record.spec = old_spec
            if moved:
                try:
                    target.rename(work_dir)
                except OSError:
                    logger.exception(
                        "Could not roll back task-dir rename %s -> %s", target, work_dir
                    )
            raise
        logger.info("Migrated unsafe task dir %s -> %s for job %s", work_dir, target, record.id)
        return str(work_dir)

    def _reset_work_dir_in_place(self, record: JobRecord, *, strict: bool = True) -> None:
        """Archive the closed attempt's content instead of deleting it (contract B).

        Moves ``WORK``/``RESULT``/legacy ``_attempts`` plus the local run
        receipts (``.exit_code``/``state.json``/``run.lock``/``events.jsonl``)
        into ``WORK/00_RUNTIME/attempts/<attempt>/`` so the old attempt stays
        recoverable; only stray logs/markers are deleted afterwards.  Any
        archiving failure aborts the requeue — never a fallback to deletion.
        ``strict`` still controls stray-file delete failures (§10.5).
        """
        work_dir = Path(record.work_dir)
        if not work_dir.is_dir():
            return
        attempt = attempt_number(record)
        archive_root = work_dir / "WORK" / "00_RUNTIME" / "attempts" / str(attempt)

        def _archive(src: Path, dest: Path) -> None:
            if not src.exists():
                return
            if dest.exists():
                raise RuntimeError(f"旧尝试回执归档目标已存在，已阻断重跑（请人工检查）: {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                src.rename(dest)
            except OSError as exc:
                raise RuntimeError(
                    f"归档旧尝试回执失败，已阻断重跑: {src} -> {dest}: {exc}"
                ) from exc

        work = work_dir / "WORK"
        if work.is_dir():
            for child in list(work.iterdir()):
                if child.name == "00_RUNTIME":
                    continue
                _archive(child, archive_root / "WORK" / child.name)
            runtime = work / "00_RUNTIME"
            if runtime.is_dir():
                for child in list(runtime.iterdir()):
                    if child.name == "attempts":
                        continue
                    _archive(child, archive_root / "WORK" / "00_RUNTIME" / child.name)
        _archive(work_dir / "RESULT", archive_root / "RESULT")
        for name in (
            ".exit_code",
            "state.json",
            "run.lock",
            "events.jsonl",
            "stdout.log",
            "stderr.log",
            "_attempts",
        ):
            _archive(work_dir / name, archive_root / name)

        failures: list[str] = []
        for child in list(work_dir.iterdir()):
            if child.name in _RERUN_STABLE_FILES or child.name == ".structure_history":
                continue
            if child.name == "WORK":
                # WORK now holds only the attempts archive (contract B).
                continue
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError:
                logger.warning(
                    "Could not clear %s before rerun of %s",
                    child,
                    record.id,
                    exc_info=True,
                )
                failures.append(str(child))
        if failures and strict:
            raise RuntimeError(
                f"清理旧尝试产物失败（{len(failures)} 项），已阻断排队: " + "; ".join(failures[:5])
            )

    def _archive_attempt_receipts(
        self, record: JobRecord, previous_attempt: int, *, include_science: bool = True
    ) -> None:
        """Archive the closed attempt's local receipts (contract B, D01).

        Always archives ``.exit_code``/``state.json``/``run.lock`` into
        ``WORK/00_RUNTIME/attempts/<n>/``; ``include_science=False`` (the
        continue path) leaves ``checkpoint.json``/``step_result*.json``/
        ``RESULT/`` in place so the new attempt **adopts** them.  A clean
        rerun archives the science through :meth:`_reset_work_dir_in_place`.
        Never deletes; an existing archive target aborts the operation.
        """
        work_dir = Path(record.work_dir)
        if not work_dir.is_dir() or previous_attempt < 1:
            return
        archive_root = work_dir / "WORK" / "00_RUNTIME" / "attempts" / str(previous_attempt)

        def _archive(src: Path, dest: Path) -> None:
            if not src.exists():
                return
            if dest.exists():
                raise RuntimeError(f"旧尝试回执归档目标已存在，已阻断（请人工检查）: {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                src.rename(dest)
            except OSError as exc:
                raise RuntimeError(f"归档旧尝试回执失败: {src} -> {dest}: {exc}") from exc

        for name in (".exit_code", "state.json"):
            _archive(work_dir / name, archive_root / name)
        runtime = work_dir / "WORK" / "00_RUNTIME"
        runtime_archive = archive_root / "WORK" / "00_RUNTIME"
        _archive(runtime / "run.lock", runtime_archive / "run.lock")
        # 07-D: every envelope/execution record is attempt-scoped.  The
        # continue path (include_science=False) keeps checkpoint/step_result/
        # RESULT for adoption but must not leave the old choice, group, claim
        # or worker receipts visible to the new attempt.
        _archive(work_dir / "execution_receipt.json", archive_root / "execution_receipt.json")
        _archive(
            runtime / ATTEMPT_PROTOCOL_FILENAME,
            runtime_archive / ATTEMPT_PROTOCOL_FILENAME,
        )
        _archive(
            runtime / FROZEN_GROUP_DIRNAME,
            runtime_archive / FROZEN_GROUP_DIRNAME,
        )
        for name in (
            ENVELOPE_FILENAME,
            PLAN_FILENAME,
            BINDING_FILENAME,
            FROZEN_GROUP_MANIFEST_FILENAME,
        ):
            _archive(runtime / name, runtime_archive / name)
        _archive(runtime / ATTEMPT_LOCK_FILENAME, runtime_archive / ATTEMPT_LOCK_FILENAME)
        task_result_name = TASK_RESULT_RELATIVE_PATH[-1]
        _archive(runtime / task_result_name, runtime_archive / task_result_name)
        if not include_science:
            return
        _archive(work_dir / "checkpoint.json", archive_root / "checkpoint.json")
        _archive(
            runtime / "checkpoint.json",
            archive_root / "WORK" / "00_RUNTIME" / "checkpoint.json",
        )
        for step in sorted(work_dir.glob("step_result*.json")):
            _archive(step, archive_root / step.name)
        if runtime.is_dir():
            for step in sorted(runtime.glob("step_result*.json")):
                _archive(step, archive_root / "WORK" / "00_RUNTIME" / step.name)
        _archive(work_dir / "RESULT", archive_root / "RESULT")

    def _archive_previous_resume_source(self, record: JobRecord, *, previous_attempt: int) -> None:
        """Archive an existing ``resume_source.json`` before a new one is written.

        Consecutive continues must not overwrite the previous resume
        receipt — it moves to ``WORK/00_RUNTIME/attempts/<n>/`` so both
        stay recoverable.
        """
        work_dir = Path(record.work_dir)
        src = work_dir / "resume_source.json"
        if not src.is_file():
            return
        dest = (
            work_dir
            / "WORK"
            / "00_RUNTIME"
            / "attempts"
            / str(previous_attempt)
            / "resume_source.json"
        )
        if dest.exists():
            raise RuntimeError(f"旧尝试 resume_source 归档目标已存在，已阻断（请人工检查）: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            src.rename(dest)
        except OSError as exc:
            raise RuntimeError(
                f"归档旧 resume_source 失败，已阻断: {src} -> {dest}: {exc}"
            ) from exc

    def _write_resume_source(
        self,
        record: JobRecord,
        *,
        continued_from: str,
        previous_attempt: int,
        compatible: bool = True,
        incompatible_reason: str = "",
    ) -> None:
        """Write this attempt's ``resume_source.json`` (continue provenance).

        ``compatible`` carries the pre-archive recovery-protocol verdict for
        the archived attempt (V02): readers refuse adoption when it is
        ``False`` and recompute everything.
        """
        work_dir = Path(record.work_dir)
        payload = {
            "attempt": record.attempt,
            "previous_attempt": previous_attempt,
            "continued_from": continued_from,
            "written_at": _utc_now_iso(),
            "compatible": bool(compatible),
        }
        if not compatible:
            payload["incompatible_reason"] = incompatible_reason
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            tmp = work_dir / "resume_source.json.tmp"
            tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, work_dir / "resume_source.json")
        except OSError:
            logger.warning("resume_source.json write failed for %s", record.id, exc_info=True)

    def _reset_job_artifacts(self, job_id: str) -> None:
        """Drop artifact rows captured by the previous attempt."""
        try:
            registry = ArtifactRegistry(self._stage_task_store.db_path)
            for artifact in registry.list_by_job(job_id):
                registry.delete(artifact.artifact_id)
        except Exception:
            logger.warning("Artifact reset failed for job %s", job_id, exc_info=True)

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
            *action* is ``"purged"`` | ``"purged_orphan"`` |
            ``"skipped_active"`` | ``"cancel_failed"`` | ``"error"``.
            ``"purged_orphan"`` means the ``jobs`` row was already gone
            but ghost child/index rows survived and were cascade-cleaned
            (DB rows only — no disk paths are guessed).
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
                if self.store.has_job_dependents(job_id):
                    # Ghost entries: the jobs row was deleted by a
                    # historical non-cascading path but child rows (tasks
                    # index, stage_tasks, artifacts, mechanism_studies)
                    # survived. Clean the DB rows only — the work_dir is
                    # unknown, so no local/remote paths are guessed or
                    # deleted.
                    self._purge_job_records(job_id)
                    report.append(
                        {
                            "job_id": job_id,
                            "ok": True,
                            "action": "purged_orphan",
                            "error": None,
                        }
                    )
                else:
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
        # Evict the remote structure cache for this job (todo 11).
        cache = getattr(self, "_remote_structure_cache", None)
        if cache is not None:
            try:
                cache.purge_job(job_id)
            except Exception:
                logger.debug("Failed to purge remote cache for job %s", job_id, exc_info=True)
        # Cascade-delete structure-source org rows (index + metadata +
        # tags + index_state) — design plan §5.3 last paragraph.
        try:
            from acp.scheduler.structure_source_store import StructureSourceStore

            org_store = StructureSourceStore(self.store.db_path)
            org_store.delete_by_job(job_id)
        except Exception:
            # Org tables must never block a purge.  Log with details so
            # orphan rows can be cleaned manually if needed.
            logger.warning("Org-table purge failed for job %s (non-fatal)", job_id, exc_info=True)

    def find_orphan_tasks(self) -> list[str]:
        """List ghost task-index entries whose ``jobs`` row no longer exists.

        These are produced by historical non-cascading deletes: the queue
        keeps showing (and counting) the task while every lookup by
        ``job_id`` fails.  Returns ``[]`` when the index cannot be
        validated (no ``jobs`` table in the index DB).
        """
        return self.tasks.find_orphan_task_ids()

    def purge_orphan_tasks(self) -> list[dict[str, Any]]:
        """Cascade-purge DB rows for ghost task entries; per-job report.

        For every orphan from :meth:`find_orphan_tasks` the full child-row
        set (``tasks`` / ``stage_tasks`` / ``artifacts`` /
        ``mechanism_studies``) is removed via :meth:`JobStore.purge_cascade`
        plus remote structure-cache eviction.  Disk directories are never
        touched: the ``jobs`` row — and with it ``work_dir`` — is gone, so
        no local or remote path can be resolved safely from the incomplete
        index rows.
        """
        report: list[dict[str, Any]] = []
        for task_id in self.tasks.find_orphan_task_ids():
            try:
                self._purge_job_records(task_id)
                report.append(
                    {"job_id": task_id, "ok": True, "action": "purged_orphan", "error": None}
                )
            except Exception as exc:
                logger.warning("Orphan purge failed for %s", task_id, exc_info=True)
                report.append(
                    {"job_id": task_id, "ok": False, "action": "error", "error": str(exc)}
                )
        return report

    def _delete_job_disk(self, record: JobRecord) -> None:
        """Remove a job's remote directories and local work directory.

        Remote targets come from the persisted storage identity
        (``result["remote"]["relative"]``), falling back to the derived
        run_root-relative path — never a hand-joined leaf.
        """
        job_id = record.id
        if self._is_remote_enabled() and self._remote_cleanup is not None:
            rel: str | None = None
            meta = (record.result or {}).get("remote")
            if isinstance(meta, dict):
                candidate = meta.get("relative")
                if isinstance(candidate, str) and candidate:
                    rel = candidate
            if rel is None:
                from acp.scheduler.remote.paths import storage_relative_path

                try:
                    rel = storage_relative_path(record, self.run_root)
                except ValueError:
                    rel = Path(record.work_dir).name
            try:
                self._remote_cleanup.delete_job_dirs(
                    job_id,
                    dir_names=[rel],
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
    def structure_cache(self) -> RemoteStructureCache:
        """Shared controlled-area cache for remote structure-viewer files.

        Lazy singleton: the API endpoints and the terminal-state catalog
        prefetcher must share ONE instance so path locks and the on-disk
        ``run_root/.remote_cache`` tree stay coherent.  The fetcher factory
        reads :attr:`remote_fetcher` lazily, so the cache degrades to a
        no-op when remote execution is disabled.
        """
        cache = getattr(self, "_remote_structure_cache", None)
        if cache is None:
            with self._lock:
                cache = getattr(self, "_remote_structure_cache", None)
                if cache is None:
                    from acp.results.remote_structure_cache import RemoteStructureCache

                    cache = RemoteStructureCache(
                        self.run_root,
                        fetcher_factory=lambda job_id: self._remote_fetcher,
                    )
                    self._remote_structure_cache = cache
        return cache

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
        cancelled or finish.  The DB delete cascades to ``tasks`` /
        ``stage_tasks`` / ``artifacts`` / ``mechanism_studies`` via
        :meth:`JobStore.purge_cascade` so no ghost index rows survive.
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
                self._purge_job_records(record.id)

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

    def _cas_write(
        self,
        record: JobRecord,
        *,
        expected_status: JobStatus | Iterable[JobStatus],
        decide: Callable[[JobRecord], JobRecord | None] | None = None,
        max_attempts: int = 3,
        **fields: Any,
    ) -> JobRecord | None:
        """Apply one conditional write pinned to *record*'s revision/attempt.

        A soft race (row still matches the status precondition, revision
        bumped by a concurrent progress write) retries against the fresh
        revision.  A hard mismatch (status moved, or ``attempt`` changed — an
        old-attempt replay) delegates to *decide* (re-read → decide from the
        latest state) or raises :class:`JobStateConflictError`; the pinned
        ``expected_attempt`` keeps a stale record from ever writing through
        to a newer attempt's row.
        """
        allowed = (
            {expected_status} if isinstance(expected_status, JobStatus) else set(expected_status)
        )
        revision = record.revision
        last: JobStateConflictError | None = None
        for _ in range(max_attempts):
            try:
                return self.store.transition(
                    record.id,
                    expected_status=allowed,
                    expected_revision=revision,
                    expected_attempt=record.attempt,
                    **fields,
                )
            except JobStateConflictError as exc:
                last = exc
                fresh = self.store.get(record.id)
                if fresh is None:
                    return None
                if fresh.status in allowed and fresh.attempt == record.attempt:
                    revision = fresh.revision
                    continue
                if decide is not None:
                    return decide(fresh)
                raise
        fresh = self.store.get(record.id)
        if fresh is not None and decide is not None:
            return decide(fresh)
        raise last if last is not None else JobStateConflictError(record.id, {}, None)

    def _requeue_record_cas(
        self,
        job_id: str,
        *,
        new_spec: JobSpec,
        expected: JobRecord,
        expected_status: JobStatus | Iterable[JobStatus],
        **reset_fields: Any,
    ) -> JobRecord:
        """Persist a requeue via ``store.requeue_with_spec`` (CAS, attempt+1).

        Pins the caller's revision/attempt: an old-attempt replay conflicts
        instead of clobbering the new attempt's row; retries only while the
        row still matches the caller's status/attempt precondition.
        """
        allowed = (
            {expected_status} if isinstance(expected_status, JobStatus) else set(expected_status)
        )
        revision = expected.revision
        attempt = expected.attempt
        last: JobStateConflictError | None = None
        for _ in range(3):
            try:
                return self.store.requeue_with_spec(
                    job_id,
                    new_spec=new_spec,
                    expected_revision=revision,
                    expected_attempt=attempt,
                    expected_status=allowed,
                    **reset_fields,
                )
            except JobStateConflictError as exc:
                last = exc
                fresh = self.store.get(job_id)
                if fresh is None:
                    raise KeyError(f"Unknown job {job_id!r}") from exc
                if fresh.attempt == attempt and fresh.status in allowed:
                    revision = fresh.revision
                    continue
                raise
        raise last if last is not None else JobStateConflictError(job_id, {}, None)

    def cancel(self, job_id: str) -> JobRecord | None:
        record = self.store.get(job_id)
        if record is None:
            return None
        if record.status.is_terminal:
            return record

        # Re-read → re-decide CAS loop pinned to the record just read;
        # on conflict the latest state wins (terminal is never clobbered).
        final: JobRecord | None = None
        for _ in range(4):
            target = (
                JobStatus.CANCELLED if record.status == JobStatus.QUEUED else JobStatus.CANCELLING
            )
            if target == JobStatus.CANCELLED:
                with self._lock:
                    ev = self._cancel_events.pop(job_id, None)
                if ev:
                    ev.set()
            fields: dict[str, Any] = {"status": target}
            if target == JobStatus.CANCELLED:
                fields["completed_at"] = _utc_now_iso()
            elif self._is_remote_job(record):
                # Contract A: persist the cancel intent the submit thread
                # and the reconcile chain both gate on.
                result = dict(record.result or {})
                meta = dict(result.get("remote") or {})
                meta["cancel_state"] = "requested"
                meta["requested_at"] = _utc_now_iso()
                meta["attempt"] = record.attempt
                result["remote"] = meta
                fields["result"] = result
            try:
                final = self.store.transition(
                    job_id,
                    expected_status=record.status,
                    expected_revision=record.revision,
                    expected_attempt=record.attempt,
                    **fields,
                )
                break
            except JobStateConflictError:
                fresh = self.store.get(job_id)
                if fresh is None:
                    return None
                if fresh.status.is_terminal:
                    return fresh
                record = fresh
        if final is None:
            return self.store.get(job_id)
        record = final

        if record.status == JobStatus.CANCELLED:
            self._record_admission_usage(record)
            self._release_admission(record)
            self._stage_task_observer.finalize_job(job_id, JobStatus.CANCELLED.value)
            self._sync_task_status(record)
            self._write_job_json(record)
            self._event_log(record).append("job.cancelled", job_id=job_id, attempt=record.attempt)
            self._dispatch_queued_jobs()
            return record

        with self._lock:
            ev = self._cancel_events.get(job_id)
        if ev:
            ev.set()

        if self._is_remote_job(record):
            # Contract A: a remote job is never killed locally — its
            # lifecycle belongs to LSF, and CANCELLED requires confirmed
            # evidence even when the remote layer is unavailable (the
            # startup/poll reconcile finishes the job later).
            if self.remote_runner is not None:
                if not record.remote_job_id and record.result and record.result.get("lsf_job_id"):
                    backfilled = self._cas_write(
                        record,
                        expected_status=JobStatus.CANCELLING,
                        remote_job_id=str(record.result["lsf_job_id"]),
                        decide=lambda fresh: fresh,
                    )
                    if backfilled is not None:
                        record = backfilled

                if record.remote_job_id:
                    try:
                        ok = self.remote_runner.cancel_remote(job_id, record)
                    except Exception:
                        logger.debug("cancel_remote raised for %s", job_id, exc_info=True)
                        ok = False
                    if ok:
                        self._append_cancel_state(record, "sent")
                        # Immediate bjobs confirmation — a second bkill is
                        # never sent on this path (resend is the next pass).
                        self._reconcile_cancelling_remote(record, resend_bkill=False)
                    else:
                        self._append_cancel_state(record, "unconfirmed")
                        self._event_log(record).append(
                            "remote.cancel_failed",
                            job_id=job_id,
                            reason="bkill did not succeed",
                            attempt=record.attempt,
                        )
                else:
                    # No LSF id yet: submission reconcile adopts the job and
                    # then sends bkill — no duplicate submission, no local kill.
                    self._event_log(record).append(
                        "remote.cancel_deferred",
                        job_id=job_id,
                        reason="awaiting submission reconciliation",
                        attempt=record.attempt,
                    )
        else:
            self.runner.cancel_local(job_id)
            if self._has_live_task_process(record):
                killed = self._terminate_stale_task_processes(record)
                if killed:
                    try:
                        self._event_log(record).append(
                            "process.cleaned",
                            job_id=job_id,
                            pids=killed,
                            reason="cross-process-cancel",
                        )
                    except OSError:
                        logger.debug("cancel cleanup event failed for %s", job_id, exc_info=True)

        self._event_log(record).append("job.cancelling", job_id=job_id, attempt=record.attempt)
        self._sync_task_status(record)
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

        def _conflict(fresh: JobRecord) -> JobRecord:
            raise ValueError(f"pause_for_review requires RUNNING status; got {fresh.status.value}")

        record = self._cas_write(
            record,
            expected_status=JobStatus.RUNNING,
            status=JobStatus.WAITING_REVIEW,
            result=result,
            decide=_conflict,
        )
        if record is None:
            raise KeyError(f"Unknown job {job_id!r}")
        self._sync_task_status(record)
        self._write_job_json(record)
        self._event_log(record).append(
            "job.waiting_review",
            job_id=job_id,
            payload=payload,
            attempt=record.attempt,
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
            rerun_submission = bool(resolution and resolution.get("requeue"))
            target = JobStatus.STARTING if rerun_submission else JobStatus.RUNNING

            def _conflict(fresh: JobRecord) -> JobRecord:
                raise ValueError(f"resume requires WAITING_REVIEW status; got {fresh.status.value}")

            record = self._cas_write(
                record,
                expected_status=JobStatus.WAITING_REVIEW,
                status=target,
                result=result,
                decide=_conflict,
            )
            if record is None:
                raise KeyError(f"Unknown job {job_id!r}")
            self._write_job_json(record)
        self._sync_task_status(record)
        self._event_log(record).append(
            "job.review_resumed",
            job_id=job_id,
            resolution=resolution,
            requeue=rerun_submission,
            attempt=record.attempt,
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
        if record.status == JobStatus.CANCELLING or record.status.is_terminal:
            # Entry-time invalid action: keep the historical 409 contract
            # (frontend guidance relies on it).  The genuine race — a pause
            # that loses the CAS to a concurrent cancel/terminal write — is
            # handled by the ``_conflict`` callback below.
            raise ValueError(f"pause_job requires RUNNING status; got {record.status.value}")
        if record.status != JobStatus.RUNNING:
            raise ValueError(f"pause_job requires RUNNING status; got {record.status.value}")

        if self._is_remote_job(record):
            if self.remote_runner is None:
                raise RuntimeError("remote pause unsupported: remote runner is disabled")
            self._remote_bstop_bresume(record, "bstop_job", "pause")
            mode = "bstop"
        elif self.runner.pause_local(job_id):
            mode = "sigstop"
        elif self._reconnect_local_workers and self._signal_reconnected_worker(
            record, signal.SIGSTOP
        ):
            mode = "sigstop-reconnected"
        else:
            # Process already gone (finished between check and signal) —
            # leave the record alone so the poller finalizes it normally.
            raise ValueError(
                f"job {job_id} has no live local process to pause (it may have just finished)"
            )

        def _conflict(fresh: JobRecord) -> JobRecord:
            if fresh.status == JobStatus.PAUSED:
                return fresh
            if fresh.status != JobStatus.RUNNING:
                self._compensate_pause_signal(job_id, mode)
            try:
                self._event_log(fresh).append(
                    "job.pause_conflict",
                    job_id=job_id,
                    attempt=fresh.attempt,
                    current_status=fresh.status.value,
                    action="pause",
                    mode=mode,
                )
            except OSError:
                logger.debug("pause_conflict event append failed for %s", job_id, exc_info=True)
            return fresh

        paused = self._cas_write(
            record,
            expected_status=JobStatus.RUNNING,
            status=JobStatus.PAUSED,
            decide=_conflict,
        )
        if paused is None:
            raise KeyError(f"Unknown job {job_id!r}")
        record = paused
        self._sync_task_status(record)
        self._write_job_json(record)
        self._event_log(record).append(
            "job.paused", job_id=job_id, mode=mode, attempt=record.attempt
        )
        return record

    def _compensate_pause_signal(self, job_id: str, mode: str) -> None:
        """Undo a pause signal whose CAS lost (row cancelled/finished meanwhile)."""
        try:
            if mode == "bstop":
                record = self.store.get(job_id)
                if (
                    record is not None
                    and self._is_remote_job(record)
                    and self.remote_runner is not None
                ):
                    self._remote_bstop_bresume(record, "bresume_job", "unpause")
            elif mode == "sigstop":
                self.runner.resume_local(job_id)
        except (OSError, RuntimeError, ValueError):
            logger.info("pause compensation for job %s failed", job_id, exc_info=True)

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
        elif self._reconnect_local_workers and self._signal_reconnected_worker(
            record, signal.SIGCONT
        ):
            mode = "sigcont-reconnected"
        else:
            # No tracked process (e.g. record survived a restart) — keep
            # PAUSED rather than flipping to RUNNING with nothing to poll.
            raise RuntimeError(f"cannot unpause local job {job_id}: process is no longer tracked")

        def _conflict(fresh: JobRecord) -> JobRecord:
            try:
                self._event_log(fresh).append(
                    "job.pause_conflict",
                    job_id=job_id,
                    attempt=fresh.attempt,
                    current_status=fresh.status.value,
                    action="unpause",
                    mode=mode,
                )
            except OSError:
                logger.debug("pause_conflict event append failed for %s", job_id, exc_info=True)
            return fresh

        resumed = self._cas_write(
            record,
            expected_status=JobStatus.PAUSED,
            status=JobStatus.RUNNING,
            decide=_conflict,
        )
        if resumed is None:
            raise KeyError(f"Unknown job {job_id!r}")
        record = resumed
        self._sync_task_status(record)
        self._write_job_json(record)
        self._event_log(record).append(
            "job.resumed", job_id=job_id, mode=mode, attempt=record.attempt
        )
        return record

    def continue_job(self, job_id: str, *, target_node: str | None = None) -> JobRecord:
        """Re-enter a FAILED/CANCELLED job from its checkpoint.

        Holds the shared terminal-side-effect boundary (``_side_effect_lock``
        before ``_lock``) for the whole archive + in-place requeue, so a
        superseded attempt's retry can never interleave with the new attempt.
        """
        with self._side_effect_lock:
            return self._continue_job_impl(job_id, target_node=target_node)

    def _continue_job_impl(self, job_id: str, *, target_node: str | None = None) -> JobRecord:
        """Re-enter a FAILED/CANCELLED job from its checkpoint (plan §4.4).

        Workflow matrix: ``xtbmd_censo_energy``
        first persists ``method.resume=true`` into the spec so the rebuilt
        CLI command carries ``--resume``; calculation-plan workflows resume
        when their generic checkpoint is present. ``BatchOptimize`` is
        always rejected — its checkpoint is a per-item cache, not a full
        resume contract. Other workflows without a checkpoint are rejected
        (the API maps the ``ValueError`` to 409 with a rerun hint).

        Execution-target semantics (design §3.4, D15): ``target_node=None``
        (the default) returns to the source node — the previous
        ``result["execution_target"]`` survives in the result and becomes
        the affinity hint for the next auto dispatch.  An explicit
        ``target_node`` re-pins the job's spec (override) and is validated
        with the same creation-time rules as a new submission
        (unknown/disabled/incapable → :class:`ExecutionTargetError`).

        Args:
            job_id: Job identifier.
            target_node: Optional explicit execution-node override (D15).

        Returns:
            Updated job record (status ``QUEUED``, re-dispatch started).

        Raises:
            KeyError: If the job does not exist.
            ValueError: If the status is not ``FAILED``/``CANCELLED``, a
                live process is still tracked, or the workflow cannot resume.
            ExecutionTargetError: If an explicit ``target_node`` override is
                unknown, disabled, or cannot satisfy the job requirements.
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
            killed = self._terminate_stale_task_processes(record)
            if self._has_live_task_process(record):
                raise ValueError(
                    f"job {job_id} still has live process(es) in its task directory; "
                    "refusing to continue — terminate them first"
                )
            if job_id in self._submission_jobs:
                raise ValueError(f"job {job_id} is already being submitted")

            # D15: explicit override supersedes any previous mode.
            effective = record.spec
            if target_node is not None and target_node != record.spec.target_node:
                effective = replace(
                    record.spec,
                    target_node=target_node,
                    execution_mode=None,
                )
                validate_execution_request(effective)
                validate_submission_target(effective, registry=self.registry)

            workflow = record.spec.workflow
            if workflow == "mechanism":
                pass
            elif workflow == "xtbmd_censo_energy":
                effective = replace(
                    effective,
                    method={**effective.method, "resume": True},
                )
            elif workflow == "BatchOptimize":
                # Its per-item checkpoint is a cache, not a full resume
                # contract — never let the API re-enter a BatchOptimize job.
                raise ValueError(_BATCH_NO_CONTINUE_MESSAGE)
            else:
                self._require_generic_checkpoint(record)

            old_status = record.status.value
            result = dict(record.result or {})
            result["continued_from"] = old_status
            # LSF runtime state only — node/execution_target/execution_kind
            # stay so dispatch returns to the source node (回源, §3.4/R2).
            for key in ("lsf_job_id", "remote_dir", "command_line"):
                result.pop(key, None)
            remote_meta = result.get("remote")
            if isinstance(remote_meta, dict):
                remote_meta = dict(remote_meta)
                for stale_key in ("lsf_job_id", "submit_state", "cancel_state", "command_line"):
                    remote_meta.pop(stale_key, None)
                # D01: the next attempt ADOPTS checkpoint/step_result/RESULT
                # (remote archiving keeps science in place on continue).
                remote_meta["resume"] = True
                result["remote"] = remote_meta
            pinned_attempt = record.attempt
            _prev_attempt = attempt_number(record)
            # V02: probe the OLD attempt's recovery-protocol markers BEFORE
            # archiving it; the verdict rides on resume_source.json and a
            # refusal means full recompute (no silent adoption).
            from acp.calculations.step_result import check_resume_protocol

            protocol_compatible, protocol_reason = check_resume_protocol(
                Path(record.work_dir),
                kind="batch" if workflow == "BatchOptimize" else "executor",
            )
            if not protocol_compatible:
                self._event_log(record).append(
                    "recovery.protocol_incompatible",
                    job_id=job_id,
                    reason=protocol_reason,
                    attempt=_prev_attempt,
                )
                logger.warning(
                    "recovery.protocol_incompatible for %s: %s",
                    job_id,
                    protocol_reason,
                )
            # Contract B: archive the closed attempt's local receipts (and
            # any previous resume receipt) before this continue writes new
            # ones — science results stay in place (adopted, not reset).
            self._archive_attempt_receipts(record, _prev_attempt, include_science=False)
            self._archive_previous_resume_source(record, previous_attempt=_prev_attempt)
            try:
                record = self._requeue_record_cas(
                    job_id,
                    new_spec=effective,
                    expected=record,
                    expected_status=(JobStatus.FAILED, JobStatus.CANCELLED),
                    result=result,
                )
            except JobStateConflictError as exc:
                fresh = self.store.get(job_id)
                if fresh is None:
                    raise KeyError(f"Unknown job {job_id!r}") from exc
                if fresh.attempt != pinned_attempt:
                    raise ValueError(
                        f"job {job_id} was requeued concurrently; refresh and retry"
                    ) from exc
                raise ValueError(
                    "continue_job requires FAILED or CANCELLED status; got " + fresh.status.value
                ) from exc
            attempts = attempt_number(record)
            self._cancel_events[job_id] = threading.Event()
            self._write_resume_source(
                record,
                continued_from=old_status,
                previous_attempt=_prev_attempt,
                compatible=protocol_compatible,
                incompatible_reason=protocol_reason,
            )
        self._write_job_json(record)
        self._event_log(record).append(
            "job.continued",
            job_id=job_id,
            continued_from=old_status,
            attempt=attempts,
            attempts=attempts,
            workflow=workflow,
            killed_pids=killed,
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

    def _has_live_task_process(self, record: JobRecord) -> bool:
        """True when any live process is still bound to the task directory.

        Unlike :meth:`_has_live_process` this survives service restarts: it
        scans ``/proc`` for processes whose cmdline/cwd references the task
        work_dir (ORCA children included) and treats zombies as dead.
        """
        return bool(self.runner._live_task_pids(record))

    def _terminate_stale_task_processes(self, record: JobRecord) -> list[int]:
        """Kill every process still bound to the task work_dir.

        Escalates SIGCONT → SIGTERM → SIGKILL per process group and waits
        for exit.  The recorded ``record.pid`` is only signalled when it
        actually references the task directory (PID-recycling guard).
        """
        return terminate_task_processes(Path(record.work_dir), extra_pids=[record.pid or 0])

    def _signal_reconnected_worker(self, record: JobRecord, sig: signal.Signals) -> bool:
        """SIGSTOP/SIGCONT a worker that survived a previous controller process.

        Resolves the worker from the persisted ``run.lock`` / ``/proc`` task
        identity (never the in-memory ``_processes`` table) so a fresh
        controller can pause/revive the original task; returns whether any
        signal was delivered.
        """
        pids = self.runner._live_task_pids(record)
        if not pids:
            return False
        delivered = False
        own_group = os.getpgrp()
        for pid in pids:
            try:
                pgid = os.getpgid(pid)
            except OSError:
                pgid = pid
            try:
                if pgid == own_group:
                    os.kill(pid, sig)
                else:
                    os.killpg(pgid, sig)
                delivered = True
            except OSError:
                logger.debug("reconnect %s signal failed for pid %s", sig, pid, exc_info=True)
        return delivered

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

    def _background_threads(self) -> list[threading.Thread]:
        """Every background worker owned by this manager (running or not)."""
        threads: list[threading.Thread] = []
        for name in (
            "_poll_thread",
            "_reconcile_thread",
            "_cleanup_thread",
            "_release_gc_thread",
            "_catalog_prefetch_thread",
        ):
            thread = getattr(self, name, None)
            if isinstance(thread, threading.Thread):
                threads.append(thread)
        submission_threads = getattr(self, "_submission_threads", None)
        if submission_threads is not None:
            lock = getattr(self, "_lock", None)
            if lock is not None:
                with lock:
                    threads.extend(submission_threads.values())
            else:
                threads.extend(submission_threads.values())
        return threads

    def _stop_background_work(self) -> None:
        """Signal every worker to stop, then join all of them to completion.

        Joining blocks until each worker exits: ownership must never be released
        while background work still runs (a new manager would then overlap it).
        All stop events are checked promptly by the loops, so this returns fast
        in the normal case.

        The ``self._closing`` latch is set UNDER ``self._lock`` before anything
        else. ``_start_submission_thread`` and the lazy prefetch spawner take
        that same lock, so every registration either fully completes (thread
        started and therefore visible to the join pass) before the latch, or is
        refused. That makes the joined set closed; the re-scan only exists as
        insurance against a latent registration bug.
        """
        lock = getattr(self, "_lock", None)
        if lock is not None:
            with lock:
                self._closing = True
        else:
            self._closing = True
        for event_name in (
            "_poll_stop",
            "_shutdown_event",
            "_cleanup_stop_event",
            "_release_gc_stop",
            "_prepare_worker_stop",
        ):
            event = getattr(self, event_name, None)
            if event is not None:
                event.set()
        prefetch_queue = getattr(self, "_catalog_prefetch_queue", None)
        if prefetch_queue is not None:
            try:
                prefetch_queue.put(None)
            except Exception:
                logger.debug("Failed to signal catalog prefetch worker", exc_info=True)
        current = threading.current_thread()
        for _attempt in range(_SHUTDOWN_JOIN_PASS_LIMIT):
            pending = [
                thread
                for thread in self._background_threads()
                if thread is not current and thread.is_alive()
            ]
            if not pending:
                break
            for thread in pending:
                thread.join()
        else:
            alive = [
                thread.name
                for thread in self._background_threads()
                if thread is not current and thread.is_alive()
            ]
            if alive:
                logger.error("Background threads still alive after shutdown: %s", alive)
        prefetch_thread = getattr(self, "_catalog_prefetch_thread", None)
        if prefetch_thread is not None and not prefetch_thread.is_alive():
            self._catalog_prefetch_thread = None

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._shutdown_complete:
                return
            # 1. Stop and join every background worker BEFORE releasing ownership.
            self._stop_background_work()
            # 2. Only now release the in-process registry entry and file lock.
            self._release_manager_lock()
            self._release_run_root_claim()
            try:
                self._prepare_operations.close()
            except Exception:
                logger.debug("Error closing prepare-operation store", exc_info=True)
            # 3. Cancel any remaining job signals / close pools (ownership-neutral).
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
            self._shutdown_complete = True

    def _event_log(self, record: JobRecord) -> JobEventLog:
        return JobEventLog(runtime_file(record.work_dir, "events.jsonl"))

    def _outbox_event_path(self, job_id: str) -> Path:
        record = self.store.get(job_id)
        if record is None:
            raise KeyError(job_id)
        return runtime_file(record.work_dir, "events.jsonl")

    def _deliver_outbox(self, job_id: str | None = None) -> OutboxDeliveryResult | None:
        """Deliver committed outbox rows to the durable JSONL (post-commit).

        Delivery only appends events — it never touches job state.  A failure is
        non-fatal: the row stays pending and the next sweep retries it.
        """
        if not self.store.has_pending_outbox(job_id=job_id):
            return None
        try:
            service = open_outbox_delivery(self.store, path_for=self._outbox_event_path)
            return service.deliver_pending(job_id=job_id)
        except Exception:
            logger.debug("outbox delivery failed", exc_info=True)
            return None

    def _sweep_outbox(self) -> None:
        """Deliver every undelivered outbox row (startup + periodic recovery)."""
        try:
            self._deliver_outbox()
        except Exception:
            logger.debug("outbox sweep failed", exc_info=True)

    def _write_job_json(self, record: JobRecord) -> None:
        path = Path(record.work_dir) / "job.json"
        path.write_text(
            json.dumps(record.to_dict(), indent=2, default=str),
            encoding="utf-8",
        )

    def _write_effective_config(self, record: JobRecord) -> None:
        """Persist the resolved method config for BatchOptimize jobs.

        Writes ``effective_config.json`` to the task root so the detail API
        and result provenance can display the actual parameters used.
        """
        try:
            from acp.calculations.batch.effective_config import (
                compute_effective_from_method,
                write_effective_config,
            )

            method_payload = record.spec.method or {}
            config = compute_effective_from_method(method_payload)
            write_effective_config(Path(record.work_dir), config)
        except Exception:
            logger.warning(
                "Could not write effective_config.json for job %s",
                record.id,
                exc_info=True,
            )

    def _sync_task_status(self, record: JobRecord) -> None:
        """Best-effort task-index refresh after a status transition."""
        if self.tasks is None:
            return
        try:
            self.tasks.sync_job_transition(record)
        except Exception:
            logger.warning("Task index status sync failed for job %s", record.id, exc_info=True)

    def _start_submission_thread(self, job_id: str, thread_name: str) -> bool:
        """Start one submission worker unless that job is already dispatching.

        Returns ``False`` when the manager is closing or the job is already
        dispatching. The closing check, duplicate check, bookkeeping, thread
        registration and ``start()`` all happen under one ``self._lock``
        acquisition, so shutdown either observes a fully-started worker or the
        registration is refused — never a half-registered ghost.
        """
        with self._lock:
            if self._closing:
                return False
            if job_id in self._submission_jobs:
                return False
            self._submission_jobs.add(job_id)
            self._cancel_events.setdefault(job_id, threading.Event())
            thread = threading.Thread(
                target=self._execute_submission,
                args=(job_id,),
                daemon=True,
                name=thread_name,
            )
            self._submission_threads[job_id] = thread
            try:
                thread.start()
            except BaseException:
                self._submission_jobs.discard(job_id)
                self._submission_threads.pop(job_id, None)
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
                self._submission_threads.pop(job_id, None)

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
                try:
                    submitted = self._submit_job(job_id)
                except NoCapableNodeError as exc:
                    # Config drift at dispatch (creation-time validation is
                    # T5): record the event, then degrade to capacity-retry
                    # semantics so the job is retried instead of failing —
                    # an admin fixing node capabilities unblocks it (R13).
                    drift = self.store.get(job_id)
                    if drift is not None:
                        self._event_log(drift).append(
                            "execution.no_capable_node",
                            job_id=job_id,
                            message=str(exc),
                            missing_software=list(exc.missing_software),
                            missing_tags=list(exc.missing_tags),
                        )
                    raise ExecutionCapacityUnavailable(
                        f"no capable node (capability drift): {exc}"
                    ) from exc
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
                    if not record.status.is_terminal:
                        cancelled = self._cas_write(
                            record,
                            expected_status=record.status,
                            status=JobStatus.CANCELLED,
                            completed_at=_utc_now_iso(),
                            decide=lambda fresh: None,
                        )
                        if cancelled is not None:
                            record = cancelled
                            self._sync_task_status(record)
                            self._write_job_json(record)
                            self._event_log(record).append(
                                "job.cancelled",
                                job_id=job_id,
                                reason="cancelled while waiting for execution capacity",
                                attempt=record.attempt,
                            )
                            self._stage_task_observer.finalize_job(job_id, "cancelled")
                    if record.status == JobStatus.CANCELLED:
                        self._record_admission_usage(record)
                        self._release_admission(record)
                    self._release_reservation(job_id)
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
                    attempt=record.attempt,
                )
                if self._shutdown_event.wait(retry_delay):
                    # Server is shutting down: leave the job durable for
                    # restart recovery instead of cancelling it.
                    return
            except Exception as exc:
                logger.exception("Submission failed for job %s", job_id)
                record = self.store.get(job_id)
                if record and not record.status.is_terminal:
                    # A failed submission must never leave a live worker
                    # behind (startup-window orphan): confirm-stop it before
                    # persisting FAILED.
                    if not self._is_remote_job(record):
                        self._cleanup_local_orphans(record)
                    # Terminal event first — same poller-consistency invariant
                    # as the fake-completion branch in _submit_job; the CAS
                    # below drops the write when a concurrent writer already
                    # finished the job (never FAILED-on-conflict).
                    self._event_log(record).append(
                        "job.failed", job_id=job_id, error=str(exc), attempt=record.attempt
                    )
                    failed = self._cas_write(
                        record,
                        expected_status=record.status,
                        status=JobStatus.FAILED,
                        error=f"Submission error: {exc}",
                        completed_at=_utc_now_iso(),
                        decide=lambda fresh: None,
                    )
                    if failed is not None:
                        record = failed
                        self._sync_task_status(record)
                        self._write_job_json(record)
                        self._stage_task_observer.finalize_job(job_id, "failed")
                        self._record_admission_usage(record)
                        self._release_admission(record)
                self._release_reservation(job_id)
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
        """Re-dispatch durable queued jobs in FIFO order.

        An idempotent job whose committed row predates directory adoption is
        first resumed from its claim-keyed staging directory.  The job is never
        dispatched while its adoption fact (``job.json``) is missing, so a
        crash between the job row and adoption cannot start a half-created
        task.  Once adopted, the committed QUEUED row is the single intent and
        this pass starts exactly one submission worker.
        """
        records = self.store.list(status=JobStatus.QUEUED.value, limit=10000)
        records.sort(key=lambda record: (record.created_at or "", record.id))
        for record in records:
            if self._poll_stop.is_set():
                return
            if self._idempotency_adoption_pending(record):
                claim = self.store.get_idempotency_claim_for_task(record.id)
                if claim is None:
                    continue
                try:
                    self._resume_idempotent_adoption(record, claim)
                except (OSError, ValueError):
                    logger.warning(
                        "idempotent adoption failed for job %s", record.id, exc_info=True
                    )
                if self._idempotency_adoption_pending(record):
                    continue
            self._start_submission_thread(record.id, f"acp-queue-{record.id}")

    def _persist_storage_identity(self, record: JobRecord) -> JobRecord | None:
        """Persist contract-B storage identity into ``result["remote"]`` (D01).

        Merges ``{"schema": 1, "relative": <run_root-relative incl. project
        leaf + __NN dedupe>, "attempt": record.attempt}`` after work-dir
        allocation and stores it through the CAS progress API (todo 5 folds
        this into the submit-intent write).  Returns the fresh record, or
        ``None`` when the row moved (the winner persisted it already).
        """
        from acp.scheduler.remote.paths import storage_relative_path

        try:
            rel = storage_relative_path(record, self.run_root)
        except ValueError:
            logger.warning(
                "work_dir %s not under run_root %s; storage identity not persisted",
                record.work_dir,
                self.run_root,
            )
            return None
        result = dict(record.result or {})
        meta = dict(result.get("remote") or {})
        if (
            meta.get("schema") == 1
            and meta.get("relative") == rel
            and meta.get("attempt") == record.attempt
        ):
            return record
        meta["schema"] = 1
        meta["relative"] = rel
        meta["attempt"] = record.attempt
        result["remote"] = meta
        try:
            return self.store.update_progress(
                record.id, expected_revision=record.revision, result=result
            )
        except JobStateConflictError:
            logger.debug("storage identity persist lost CAS for %s; winner keeps it", record.id)
            return None

    def _stored_remote_dir_arg(self, record: JobRecord, target: NodeSpec) -> str | None:
        """Already-resolved remote dir for ``submit_remote`` (mapping only).

        Returns ``None`` for records without a persisted mapping so the
        runner performs the legacy dual-candidate ownership probe itself.
        """
        from acp.scheduler.remote.paths import stored_remote_dir

        if self._remote_config is None:
            return None
        node = self._remote_config.get_node(str(target.name))
        if node is None:
            return None
        return stored_remote_dir(record, node)

    # ------------------------------------------------------------------ #
    # D02 submit protocol: intent + lease, id persistence, reconcile, orphans
    # ------------------------------------------------------------------ #

    def _persist_submit_intent(
        self,
        record: JobRecord,
        node_name: str,
        *,
        code_release: str | None = None,
    ) -> JobRecord | None:
        """Persist contract-A submit intent (submission_id + lease) before bsub.

        Re-reads and retries on soft revision conflicts; returns ``None``
        only when the row left {STARTING, CANCELLING} (or the attempt
        moved) — the caller then never submits.

        ``code_release`` (todo 8) binds the verified release through the
        SAME conditional write: the manager's pre-bsub call omits it, and
        the runner's ``on_code_release_bound`` callback re-runs this writer
        with the id just before ``bsub`` — one writer, one transaction
        shape, no second write path.
        """
        from acp.scheduler.remote.paths import compose_remote_dir
        from acp.scheduler.remote.submission import (
            build_owner_token,
            lease_deadline_iso,
            lease_ttl_seconds,
            submission_id_for,
        )

        ttl = lease_ttl_seconds(self._remote_config)
        last: JobStateConflictError | None = None
        for _ in range(4):
            fresh = self.store.get(record.id)
            if fresh is None or fresh.attempt != record.attempt:
                return None
            if fresh.status not in (JobStatus.STARTING, JobStatus.CANCELLING):
                return None
            result = dict(fresh.result or {})
            meta = dict(result.get("remote") or {})
            meta.setdefault("schema", 1)
            meta["attempt"] = fresh.attempt
            meta["submission_id"] = submission_id_for(fresh.id, fresh.attempt)
            meta["node"] = node_name
            meta["submit_state"] = "intent"
            meta["submit_owner"] = build_owner_token(fresh.attempt)
            meta["lease_expires_at"] = lease_deadline_iso(ttl)
            meta["intent_at"] = _utc_now_iso()
            if code_release is not None:
                meta["code_release"] = code_release
            for stale in ("lsf_job_id", "aborted_at", "unconfirmed_at", "submitted_at"):
                meta.pop(stale, None)
            result["remote"] = meta
            result.setdefault("node", node_name)
            relative = meta.get("relative")
            if isinstance(relative, str) and relative:
                node = self._remote_config.get_node(node_name) if self._remote_config else None
                if node is not None:
                    # Contract B: keep the single path key in sync with metadata.
                    result["remote_dir"] = compose_remote_dir(relative, node)
            try:
                return self.store.transition(
                    fresh.id,
                    expected_status=(JobStatus.STARTING, JobStatus.CANCELLING),
                    expected_revision=fresh.revision,
                    expected_attempt=fresh.attempt,
                    result=result,
                )
            except JobStateConflictError as exc:
                last = exc
                continue
        logger.debug("submit intent persist lost CAS for %s: %s", record.id, last)
        return None

    def _submit_checkpoint(self, job_id: str) -> bool:
        """Pre-bsub CAS re-read: a persisted cancel request aborts the submit.

        Returns ``True`` when submission may proceed (lease renewed in the
        same transaction); ``False`` after persisting
        ``submit_state="aborted_before_bsub"`` or when the row moved —
        ``bsub`` is never called on ``False``.
        """
        from acp.scheduler.remote.submission import lease_deadline_iso, lease_ttl_seconds

        for _ in range(4):
            fresh = self.store.get(job_id)
            if fresh is None or fresh.status.is_terminal:
                return False
            if fresh.status not in (JobStatus.STARTING, JobStatus.CANCELLING):
                return False
            meta = dict((fresh.result or {}).get("remote") or {})
            if meta.get("cancel_state") == "requested":
                self._persist_aborted_before_bsub(fresh)
                return False
            meta["lease_expires_at"] = lease_deadline_iso(lease_ttl_seconds(self._remote_config))
            result = dict(fresh.result or {})
            result["remote"] = meta
            try:
                self.store.transition(
                    job_id,
                    expected_status=(JobStatus.STARTING, JobStatus.CANCELLING),
                    expected_revision=fresh.revision,
                    expected_attempt=fresh.attempt,
                    result=result,
                )
                return True
            except JobStateConflictError:
                continue
        return False

    def _persist_aborted_before_bsub(self, record: JobRecord) -> JobRecord | None:
        """Persist ``aborted_before_bsub`` — the positive no-job evidence a
        confirmed cancellation may rely on without querying LSF."""
        submission_id = str((record.result or {}).get("remote", {}).get("submission_id") or "")
        for _ in range(4):
            fresh = self.store.get(record.id)
            if fresh is None:
                return None
            result = dict(fresh.result or {})
            meta = dict(result.get("remote") or {})
            if meta.get("submit_state") == "aborted_before_bsub":
                return fresh
            meta["submit_state"] = "aborted_before_bsub"
            meta["aborted_at"] = _utc_now_iso()
            result["remote"] = meta
            try:
                stored = self.store.transition(
                    fresh.id,
                    expected_status=(JobStatus.STARTING, JobStatus.CANCELLING),
                    expected_revision=fresh.revision,
                    expected_attempt=fresh.attempt,
                    result=result,
                )
            except JobStateConflictError:
                continue
            if submission_id:
                from acp.scheduler.remote.submission import release_submit_worker

                release_submit_worker(submission_id)
            try:
                self._event_log(stored).append(
                    "remote.submit_aborted",
                    job_id=stored.id,
                    attempt=stored.attempt,
                    submission_id=meta.get("submission_id"),
                    evidence="aborted_before_bsub",
                )
            except OSError:
                logger.debug("submit_aborted event write failed for %s", stored.id)
            return stored
        return None

    def _on_submitted(self, job_id: str, lsf_job_id: str, expected_attempt: int) -> None:
        """Persist the LSF id from the ``on_submitted`` callback (contract A).

        Re-reads the row for EVERY attempt (never a pre-bsub revision —
        Oracle r12 F1), CAS-persists id + ``submit_state="submitted"``
        with bounded retry, keeps a CANCELLING row CANCELLING (id only),
        and records a persistent orphan when the row already reached a
        terminal state instead of silently dropping the id.
        """
        last: JobStateConflictError | None = None
        for _ in range(6):
            fresh = self.store.get(job_id)
            if fresh is None:
                return
            if fresh.attempt != expected_attempt:
                return
            if fresh.status.is_terminal:
                self._record_submitted_orphan(fresh, lsf_job_id)
                return
            if fresh.remote_job_id == lsf_job_id:
                return
            result = dict(fresh.result or {})
            meta = dict(result.get("remote") or {})
            meta["submit_state"] = "submitted"
            meta["lsf_job_id"] = lsf_job_id
            meta["submitted_at"] = _utc_now_iso()
            result["remote"] = meta
            result["lsf_job_id"] = lsf_job_id
            fields: dict[str, Any] = {"remote_job_id": lsf_job_id, "result": result}
            if fresh.status == JobStatus.STARTING:
                fields["status"] = JobStatus.PENDING
            try:
                stored = self.store.transition(
                    job_id,
                    expected_status=(JobStatus.STARTING, JobStatus.CANCELLING),
                    expected_revision=fresh.revision,
                    expected_attempt=fresh.attempt,
                    **fields,
                )
            except JobStateConflictError as exc:
                last = exc
                continue
            try:
                self._event_log(stored).append(
                    "remote.submitted_persisted",
                    job_id=job_id,
                    lsf_job_id=lsf_job_id,
                    attempt=stored.attempt,
                    status=stored.status.value,
                )
            except OSError:
                logger.debug("submitted_persisted event write failed for %s", job_id)
            if stored.status == JobStatus.PENDING:
                self._sync_task_status(stored)
            return
        raise last if last is not None else JobStateConflictError(job_id, {}, None)

    def _record_submitted_orphan(self, record: JobRecord, lsf_job_id: str) -> None:
        """Terminal-row id conflict: persist an orphan + first cancel attempt."""
        now = _utc_now_iso()
        for _ in range(3):
            fresh = self.store.get(record.id)
            if fresh is None or fresh.attempt != record.attempt:
                return
            result = dict(fresh.result or {})
            meta = dict(result.get("remote") or {})
            raw_orphans = meta.get("orphans")
            orphans = (
                [dict(o) for o in raw_orphans if isinstance(o, dict)]
                if isinstance(raw_orphans, list)
                else []
            )
            orphans.append(
                {
                    "node": str(meta.get("node") or result.get("node") or ""),
                    "lsf_job_id": lsf_job_id,
                    "attempt": fresh.attempt,
                    "cancel_state": "unconfirmed",
                    "requested_at": now,
                    # None until the first cancel attempt runs, so the very
                    # first pass never waits out the backoff window.
                    "last_attempt_at": None,
                    "failures": 0,
                }
            )
            meta["orphans"] = orphans
            result["remote"] = meta
            try:
                self.store.update_progress(
                    fresh.id, expected_revision=fresh.revision, result=result
                )
            except JobStateConflictError:
                continue
            record = fresh
            break
        try:
            self._event_log(record).append(
                "remote.submitted_orphan",
                job_id=record.id,
                lsf_job_id=lsf_job_id,
                attempt=record.attempt,
                node=str((record.result or {}).get("remote", {}).get("node") or ""),
            )
        except OSError:
            logger.debug("submitted_orphan event write failed for %s", record.id)
        persisted = self.store.get(record.id) or record
        self._orphan_cancel_pass(persisted)

    @staticmethod
    def _pending_orphans(result: dict[str, Any]) -> list[dict[str, Any]]:
        remote_meta = result.get("remote")
        if not isinstance(remote_meta, dict):
            return []
        raw = remote_meta.get("orphans")
        if not isinstance(raw, list):
            return []
        return [dict(entry) for entry in raw if isinstance(entry, dict)]

    def _orphan_backoff_seconds(self, entry: dict[str, Any]) -> int:
        """Bounded exponential backoff: min(30s * 2**failures, 1h)."""
        failures = int(entry.get("failures", 0) or 0)
        return min(30 * (2 ** min(failures, 10)), 3600)

    def _orphan_cancel_pass(self, record: JobRecord) -> bool:
        """One bounded retry pass over *record*'s pending orphans.

        Returns True when at least one orphan was confirmed-and-cleared.
        The original job row stays terminal throughout; only
        ``result["remote"]["orphans"]`` changes.
        """
        entries = self._pending_orphans(record.result or {})
        if not entries:
            return False
        now = datetime.now(timezone.utc)
        changed = False
        cleared = False
        survivors: list[dict[str, Any]] = []
        for entry in entries:
            if entry.get("cancel_state") == "confirmed":
                changed = True
                cleared = True
                continue
            last_attempt = _parse_iso_ts(entry.get("last_attempt_at"))
            if last_attempt is not None and (
                now.timestamp() - last_attempt < self._orphan_backoff_seconds(entry)
            ):
                survivors.append(entry)
                continue
            outcome = self._orphan_cancel_attempt(entry)
            # Any attempt mutates last_attempt_at/failures — always persist
            # them, otherwise the backoff window restarts from scratch.
            changed = True
            if outcome == "confirmed":
                changed = True
                cleared = True
                try:
                    self._event_log(record).append(
                        "remote.orphan_cancel_confirmed",
                        job_id=record.id,
                        node=entry.get("node"),
                        lsf_job_id=entry.get("lsf_job_id"),
                        attempt=entry.get("attempt"),
                        cancel_state="confirmed",
                    )
                except OSError:
                    logger.debug("orphan confirm event failed for %s", record.id)
                continue
            failures = int(entry.get("failures", 0) or 0)
            if failures >= _ORPHAN_STALL_THRESHOLD and not entry.get("stalled_alert"):
                entry["stalled_alert"] = True
                changed = True
                try:
                    self._event_log(record).append(
                        "remote.orphan_cancel_stalled",
                        job_id=record.id,
                        node=entry.get("node"),
                        lsf_job_id=entry.get("lsf_job_id"),
                        attempt=entry.get("attempt"),
                        failures=failures,
                    )
                except OSError:
                    logger.debug("orphan stall event failed for %s", record.id)
            survivors.append(entry)
        if not changed:
            return False
        fresh = self.store.get(record.id)
        if fresh is None:
            return cleared
        result = dict(fresh.result or {})
        meta = dict(result.get("remote") or {})
        if survivors:
            meta["orphans"] = survivors
        else:
            meta.pop("orphans", None)
        result["remote"] = meta
        try:
            self.store.update_progress(fresh.id, expected_revision=fresh.revision, result=result)
        except JobStateConflictError:
            logger.debug("orphan persist lost CAS for %s", record.id)
        return cleared

    def _orphan_cancel_attempt(self, entry: dict[str, Any]) -> str:
        """Query + bkill one orphan. Returns ``confirmed``/``retry``/``unknown``."""
        entry["last_attempt_at"] = _utc_now_iso()
        node_name = str(entry.get("node") or "")
        lsf_job_id = str(entry.get("lsf_job_id") or "")
        node = self._remote_config.get_node(node_name) if self._remote_config else None
        if node is None or self._remote_monitor is None or not lsf_job_id:
            entry["failures"] = int(entry.get("failures", 0) or 0) + 1
            return "unknown"
        try:
            status = self._remote_monitor.get_lsf_status(node, lsf_job_id)
        except Exception:
            logger.warning(
                "Orphan cancel status query failed (node=%s, lsf_job_id=%s, "
                "attempt=%s) — treating status as unknown",
                node_name,
                lsf_job_id,
                entry.get("attempt"),
                exc_info=True,
            )
            status = "unknown"
        if status in ("not_found", "done", "failed"):
            return "confirmed"
        if status == "unknown":
            entry["failures"] = int(entry.get("failures", 0) or 0) + 1
            return "unknown"
        try:
            ok = self._remote_monitor.cancel_job(node, lsf_job_id)
        except Exception:
            logger.warning(
                "Orphan cancel bkill failed (node=%s, lsf_job_id=%s, attempt=%s) — will retry",
                node_name,
                lsf_job_id,
                entry.get("attempt"),
                exc_info=True,
            )
            ok = False
        if not ok:
            entry["failures"] = int(entry.get("failures", 0) or 0) + 1
            return "unknown"
        return "retry"

    def _handle_rejected_submission(self, record: JobRecord, exc: Exception) -> None:
        """Contract A: definitive rejection → FAILED only, never a dir delete."""
        fresh = self.store.get(record.id)
        if fresh is None or fresh.status.is_terminal or fresh.attempt != record.attempt:
            return
        result = dict(fresh.result or {})
        meta = dict(result.get("remote") or {})
        meta["submit_state"] = "not_accepted"
        result["remote"] = meta
        try:
            self._event_log(fresh).append(
                "job.failed",
                job_id=fresh.id,
                error=str(exc),
                reason="remote_submit_not_accepted",
                attempt=fresh.attempt,
            )
        except OSError:
            logger.debug("job.failed event write failed for %s", fresh.id)
        failed = self._cas_write(
            fresh,
            expected_status=fresh.status,
            status=JobStatus.FAILED,
            error=f"remote_submit_not_accepted: {exc}",
            completed_at=_utc_now_iso(),
            result=result,
            decide=lambda row: None,
        )
        if failed is None:
            return
        self._sync_task_status(failed)
        self._write_job_json(failed)
        self._stage_task_observer.finalize_job(failed.id, "failed")
        self._release_reservation(failed.id)
        self._dispatch_queued_jobs()

    def _handle_indeterminate_submission(self, record: JobRecord, exc: Exception) -> None:
        """Contract A: unknown outcome → keep STARTING, persist ``unconfirmed``."""
        for _ in range(3):
            fresh = self.store.get(record.id)
            if fresh is None or fresh.status.is_terminal or fresh.attempt != record.attempt:
                return
            result = dict(fresh.result or {})
            meta = dict(result.get("remote") or {})
            meta["submit_state"] = "unconfirmed"
            meta["unconfirmed_at"] = _utc_now_iso()
            result["remote"] = meta
            try:
                self.store.transition(
                    fresh.id,
                    expected_status=(JobStatus.STARTING, JobStatus.CANCELLING),
                    expected_revision=fresh.revision,
                    expected_attempt=fresh.attempt,
                    result=result,
                )
            except JobStateConflictError:
                continue
            try:
                # Same idempotency key as the runner's emit: a real runner
                # may already have written this event before re-raising.
                self._event_log(fresh).append(
                    "remote.submit_unconfirmed",
                    job_id=fresh.id,
                    attempt=fresh.attempt,
                    submission_id=meta.get("submission_id"),
                    reason=str(exc),
                    idempotency_key=f"submit-unconfirmed:{fresh.id}:{fresh.attempt}",
                )
            except OSError:
                logger.debug("submit_unconfirmed event write failed", exc_info=True)
            return
        logger.warning(
            "unconfirmed submit state persist failed for %s (%s); reconcile retries",
            record.id,
            exc,
        )

    def _reconcile_submission_record(self, record: JobRecord) -> None:
        """Converge one pending submission via ``reconcile_submission``."""
        if self.remote_runner is None:
            return
        if record.remote_job_id or (record.result or {}).get("lsf_job_id"):
            # Id already known: rebuild poll state; STARTING → PENDING.
            if not self.remote_runner.recover_job_state(record):
                return
            if record.status != JobStatus.STARTING:
                return
            try:
                stored = self.store.transition(
                    record.id,
                    expected_status=JobStatus.STARTING,
                    expected_revision=record.revision,
                    expected_attempt=record.attempt,
                    status=JobStatus.PENDING,
                )
            except JobStateConflictError:
                return
            try:
                self._event_log(stored).append(
                    "remote.submit_reconciled",
                    job_id=record.id,
                    attempt=record.attempt,
                )
            except OSError:
                logger.debug("reconcile event append failed for job %s", record.id, exc_info=True)
            self._sync_task_status(stored)
            return
        try:
            verdict = self.remote_runner.reconcile_submission(record)
        except Exception as exc:
            logger.warning(
                "reconcile_submission failed for %s (keeping pending): %s",
                record.id,
                exc,
            )
            return
        if verdict == "found":
            self._adopt_submission(record)
        elif verdict == "not_accepted":
            self._mark_not_accepted(record)
        else:
            logger.info("Submission for %s still indeterminate — keeping pending", record.id)

    def _adopt_submission(self, record: JobRecord) -> None:
        """Persist a reconciled LSF id; CANCELLING keeps its status (id only)."""
        lsf_job_id = record.remote_job_id or str((record.result or {}).get("lsf_job_id") or "")
        if not lsf_job_id:
            return
        for _ in range(4):
            fresh = self.store.get(record.id)
            if fresh is None or fresh.status.is_terminal or fresh.attempt != record.attempt:
                return
            if fresh.remote_job_id == lsf_job_id:
                break
            result = dict(fresh.result or {})
            meta = dict(result.get("remote") or {})
            meta["submit_state"] = "submitted"
            meta["lsf_job_id"] = lsf_job_id
            meta["submitted_at"] = _utc_now_iso()
            result["remote"] = meta
            result["lsf_job_id"] = lsf_job_id
            fields: dict[str, Any] = {"remote_job_id": lsf_job_id, "result": result}
            if fresh.status == JobStatus.STARTING:
                fields["status"] = JobStatus.PENDING
            try:
                stored = self.store.transition(
                    fresh.id,
                    expected_status=(JobStatus.STARTING, JobStatus.CANCELLING),
                    expected_revision=fresh.revision,
                    expected_attempt=fresh.attempt,
                    **fields,
                )
            except JobStateConflictError:
                continue
            fresh = stored
            try:
                self._event_log(stored).append(
                    "remote.submit_reconciled",
                    job_id=stored.id,
                    lsf_job_id=lsf_job_id,
                    attempt=stored.attempt,
                    status=stored.status.value,
                )
            except OSError:
                logger.debug("submit_reconciled event write failed for %s", stored.id)
            if stored.status == JobStatus.PENDING:
                self._sync_task_status(stored)
            break
        else:
            return
        if self.remote_runner is not None:
            try:
                adopted = self.store.get(record.id)
                if adopted is not None and not adopted.status.is_terminal:
                    self.remote_runner.recover_job_state(adopted)
                    if adopted.status == JobStatus.CANCELLING:
                        self._reconcile_cancelling_remote(adopted)
            except Exception:
                logger.debug("post-adopt recover failed for %s", record.id, exc_info=True)

    def _reconcile_cancelling_remote(self, record: JobRecord, *, resend_bkill: bool = True) -> None:
        """Contract A: publish CANCELLED only with LSF confirmation evidence.

        Classification comes from ``monitor.get_lsf_status``'s return value
        (never from exceptions): alive → (re)send ``bkill`` and keep
        CANCELLING; gone/DONE/EXIT → confirm CANCELLED with
        ``remote.cancel_confirmed``; ``STATUS_UNKNOWN`` or a communication
        failure → keep CANCELLING with ``cancel_state="unconfirmed"`` for
        the next pass.  A row without a job id first reconciles the pending
        submission (adopt id → bkill in the post-adopt chain, never a second
        ``bsub``); ``aborted_before_bsub`` is positive no-job evidence and
        confirms without any LSF query.
        """
        if record.status != JobStatus.CANCELLING or not self._is_remote_job(record):
            return
        remote_meta = (record.result or {}).get("remote")
        if (
            isinstance(remote_meta, dict)
            and remote_meta.get("submit_state") == "aborted_before_bsub"
        ):
            self._confirm_aborted_cancellation(record)
            return
        if self.remote_runner is None:
            return
        if not record.remote_job_id:
            if _has_pending_submission(record):
                self._reconcile_submission_record(record)
            return
        lsf_job_id = str(record.remote_job_id)
        result = record.result or {}
        node_name = str(result.get("node") or (result.get("remote") or {}).get("node") or "")
        node = self._remote_config.get_node(node_name) if self._remote_config else None
        if node is None or self._remote_monitor is None:
            self._append_cancel_state(record, "unconfirmed")
            return
        status = self._lsf_status_or_unknown(node, lsf_job_id)
        if status in _CANCEL_CONFIRMED_LSF:
            self._publish_cancel_confirmed(record, lsf_job_id, f"bjobs:{status}")
            return
        if status not in _CANCEL_ALIVE_LSF:
            self._append_cancel_state(record, "unconfirmed")
            return
        if not resend_bkill:
            # cancel() delivered bkill moments ago; the next pass confirms.
            return
        try:
            sent = self._remote_monitor.cancel_job(node, lsf_job_id)
        except Exception:
            logger.debug("bkill raised for job %s", record.id, exc_info=True)
            sent = False
        self._append_cancel_state(record, "sent" if sent else "unconfirmed")
        if not sent:
            try:
                self._event_log(record).append(
                    "remote.cancel_failed",
                    job_id=record.id,
                    lsf_job_id=lsf_job_id,
                    reason="bkill did not succeed",
                    attempt=record.attempt,
                )
            except OSError:
                logger.debug("cancel_failed event write failed for %s", record.id)
            return
        status = self._lsf_status_or_unknown(node, lsf_job_id)
        if status in _CANCEL_CONFIRMED_LSF:
            self._publish_cancel_confirmed(record, lsf_job_id, f"bkill+bjobs:{status}")

    def _lsf_status_or_unknown(self, node: RemoteNode, lsf_job_id: str) -> str:
        """bjobs classification; any communication failure is ``unknown``."""
        try:
            assert self._remote_monitor is not None
            return str(self._remote_monitor.get_lsf_status(node, lsf_job_id))
        except Exception:
            logger.debug("bjobs classification failed for %s", lsf_job_id, exc_info=True)
            return "unknown"

    def _publish_cancel_confirmed(self, record: JobRecord, lsf_job_id: str, evidence: str) -> None:
        """CAS CANCELLING → CANCELLED with the confirmation evidence event."""
        for _ in range(3):
            fresh = self.store.get(record.id)
            if fresh is None or fresh.status != JobStatus.CANCELLING:
                return
            meta = dict((fresh.result or {}).get("remote") or {})
            meta["cancel_state"] = "confirmed"
            meta["confirmed_at"] = _utc_now_iso()
            result_payload = dict(fresh.result or {})
            result_payload["remote"] = meta
            try:
                stored = self.store.transition(
                    fresh.id,
                    expected_status=JobStatus.CANCELLING,
                    expected_revision=fresh.revision,
                    expected_attempt=fresh.attempt,
                    status=JobStatus.CANCELLED,
                    completed_at=_utc_now_iso(),
                    result=result_payload,
                    outbox=OutboxEventSpec(
                        event_type="remote.cancel_confirmed",
                        payload={
                            "job_id": fresh.id,
                            "lsf_job_id": lsf_job_id,
                            "evidence": evidence,
                            "attempt": fresh.attempt,
                        },
                        attempt=fresh.attempt,
                    ),
                )
            except JobStateConflictError:
                continue
            self._sync_task_status(stored)
            self._write_job_json(stored)
            self._stage_task_observer.finalize_job(stored.id, "cancelled")
            self._deliver_outbox(stored.id)
            self._release_reservation(stored.id)
            self._dispatch_queued_jobs()
            return

    def _append_cancel_state(self, record: JobRecord, state: str) -> None:
        for _ in range(3):
            fresh = self.store.get(record.id)
            if fresh is None or fresh.status != JobStatus.CANCELLING:
                return
            meta = dict((fresh.result or {}).get("remote") or {})
            meta["cancel_state"] = state
            payload = dict(fresh.result or {})
            payload["remote"] = meta
            try:
                self.store.transition(
                    fresh.id,
                    expected_status=JobStatus.CANCELLING,
                    expected_revision=fresh.revision,
                    expected_attempt=fresh.attempt,
                    result=payload,
                )
                return
            except JobStateConflictError:
                continue

    def _mark_not_accepted(self, record: JobRecord) -> None:
        """Contract A: full positive evidence → FAILED (``remote_submit_not_accepted``)."""
        fresh = self.store.get(record.id)
        if fresh is None or fresh.status.is_terminal or fresh.attempt != record.attempt:
            return
        if fresh.status not in (JobStatus.STARTING, JobStatus.PENDING):
            return
        self._handle_rejected_submission(
            fresh, RuntimeError("submission not accepted (reconciled with full evidence)")
        )

    def _confirm_aborted_cancellation(self, record: JobRecord) -> None:
        """``aborted_before_bsub`` is the persisted positive no-job evidence."""
        if record.status != JobStatus.CANCELLING:
            return
        remote_meta = (record.result or {}).get("remote")
        if not isinstance(remote_meta, dict) or remote_meta.get("submit_state") != (
            "aborted_before_bsub"
        ):
            return
        for _ in range(3):
            fresh = self.store.get(record.id)
            if fresh is None or fresh.status != JobStatus.CANCELLING:
                return
            meta = dict((fresh.result or {}).get("remote") or {})
            meta["cancel_state"] = "confirmed"
            payload = dict(fresh.result or {})
            payload["remote"] = meta
            try:
                stored = self.store.transition(
                    fresh.id,
                    expected_status=JobStatus.CANCELLING,
                    expected_revision=fresh.revision,
                    expected_attempt=fresh.attempt,
                    status=JobStatus.CANCELLED,
                    completed_at=_utc_now_iso(),
                    result=payload,
                )
            except JobStateConflictError:
                continue
            self._sync_task_status(stored)
            self._write_job_json(stored)
            self._stage_task_observer.finalize_job(stored.id, "cancelled")
            try:
                self._event_log(stored).append(
                    "remote.cancel_confirmed",
                    job_id=stored.id,
                    evidence="aborted_before_bsub",
                    attempt=stored.attempt,
                )
            except OSError:
                logger.debug("cancel_confirmed event write failed for %s", stored.id)
            self._record_admission_usage(stored)
            self._release_admission(stored)
            self._release_reservation(stored.id)
            self._dispatch_queued_jobs()
            return

    def _finalize_after_submit(
        self, record: JobRecord, job_id: str, lsf_job_id: str
    ) -> JobRecord | None:
        """Post-``submit_remote`` write: tolerates an id the callback already wrote."""
        for _ in range(4):
            fresh = self.store.get(job_id)
            if fresh is None or fresh.attempt != record.attempt:
                return None
            if fresh.status.is_terminal:
                return fresh
            merged = dict(record.result or {})
            db_remote = dict((fresh.result or {}).get("remote") or {})
            our_remote = dict((record.result or {}).get("remote") or {})
            for key, value in db_remote.items():
                our_remote.setdefault(key, value)
            for key in ("submit_state", "lsf_job_id", "submitted_at", "orphans"):
                if key in db_remote:
                    our_remote[key] = db_remote[key]
            our_remote["submit_state"] = "submitted"
            our_remote["lsf_job_id"] = lsf_job_id
            merged["remote"] = our_remote
            merged["lsf_job_id"] = lsf_job_id
            if fresh.remote_job_id == lsf_job_id:
                if merged != (fresh.result or {}):
                    try:
                        return self.store.update_progress(
                            job_id, expected_revision=fresh.revision, result=merged
                        )
                    except JobStateConflictError:
                        continue
                return fresh
            fields: dict[str, Any] = {"remote_job_id": lsf_job_id, "result": merged}
            if fresh.status == JobStatus.STARTING:
                fields["status"] = JobStatus.PENDING
            try:
                return self.store.transition(
                    job_id,
                    expected_status=(JobStatus.STARTING, JobStatus.CANCELLING),
                    expected_revision=fresh.revision,
                    expected_attempt=fresh.attempt,
                    **fields,
                )
            except JobStateConflictError:
                continue
        return None

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
            claimed = self._cas_write(
                record,
                expected_status=record.status,
                status=JobStatus.STARTING,
                started_at=_utc_now_iso(),
                decide=lambda fresh: None,
            )
            if claimed is None:
                # Row moved (cancel/terminal/requeue during the claim) — the
                # winner owns the job; never submit on top of it.
                return True
            record = claimed
            self._sync_task_status(record)
            self._write_job_json(record)

        if self._admission is not None:
            decision = self._admission.revalidate_job(record)
            if decision is not None and not decision.allowed:
                self._fail_admission_revalidation(record, decision)
                return True

        cancel_event = self._cancel_events.get(job_id, threading.Event())
        event_log = self._event_log(record)

        # Snapshot the effective config for BatchOptimize jobs (plan §5.5).
        if record.spec.workflow == "BatchOptimize":
            self._write_effective_config(record)

        # ------------------------------------------------------------------
        # Fake workflow: run in-process to completion, mark COMPLETED now.
        # ------------------------------------------------------------------
        if record.spec.workflow == "fake":
            self.runner.submit(record, event_log, cancel_event)
            exit_code = record.exit_code if record.exit_code is not None else 0
            # Publish the terminal event before the terminal status so pollers
            # that observe COMPLETED always find a consistent event tail.
            event_log.append(
                "job.completed",
                job_id=job_id,
                exit_code=exit_code,
                attempt=record.attempt,
            )

            def _complete_from(fresh: JobRecord) -> JobRecord | None:
                if fresh.status.is_terminal:
                    return None
                try:
                    return self.store.transition(
                        job_id,
                        expected_status=fresh.status,
                        expected_revision=fresh.revision,
                        expected_attempt=fresh.attempt,
                        status=JobStatus.COMPLETED,
                        exit_code=exit_code,
                        progress=1.0,
                        completed_at=_utc_now_iso(),
                    )
                except JobStateConflictError:
                    return None

            completed = self._cas_write(
                record,
                expected_status=record.status,
                status=JobStatus.COMPLETED,
                exit_code=exit_code,
                progress=1.0,
                completed_at=_utc_now_iso(),
                decide=_complete_from,
            )
            if completed is None:
                with self._lock:
                    self._cancel_events.pop(job_id, None)
                self._dispatch_queued_jobs()
                return True
            record = completed
            self._sync_task_status(record)
            self._write_job_json(record)
            self._stage_task_observer.finalize_job(job_id, "completed")
            self._record_admission_usage(record)
            self._release_admission(record)
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
        if target.kind == "remote":
            fresh = self._persist_storage_identity(record)
            if fresh is not None:
                record = fresh

        if target.kind == "local":
            # Admission gate + dispatch under the lock so concurrent
            # submission threads cannot oversubscribe local slots (M5).
            with self._lock:
                self._admit_local(record)
                adopted = self._adopt_existing_task_process(record)
                if not adopted:
                    self.runner.submit(record, event_log, cancel_event)
                    self._fault_point("dispatch.after_local_spawn")
                running = self._cas_write(
                    record,
                    expected_status=record.status,
                    status=JobStatus.RUNNING,
                    pid=record.pid,
                    decide=lambda fresh: None,
                )
                if running is None:
                    # Cancelled/requeued during dispatch — the cancel event
                    # stops the spawned process; the latest state wins.
                    return True
                record = running
                self._sync_task_status(record)
                self._write_job_json(record)
            return True

        if self.remote_runner is None:
            raise ExecutionTargetError(
                "Remote execution target resolved but no remote runner is "
                "available (no enabled remote nodes configured)"
            )

        from acp.scheduler.remote.submission import (
            heartbeat_submit_worker,
            lease_ttl_seconds,
            register_submit_worker,
            release_submit_worker,
        )
        from acp.scheduler.remote.submission import submission_id_for as _sub_id

        try:
            from acp.scheduler.remote import runner as _remote_runner

            remote_submission_indeterminate = _remote_runner.RemoteSubmissionIndeterminate
            remote_submission_rejected = _remote_runner.RemoteSubmissionRejected
        except ImportError:  # pragma: no cover - paramiko missing
            remote_submission_indeterminate = ()  # type: ignore[assignment,misc]
            remote_submission_rejected = ()  # type: ignore[assignment,misc]

        try:
            self._ensure_remote_capacity(record, target)
        except BaseException:
            self._release_reservation(record.id)
            raise

        intent = self._persist_submit_intent(record, node_name=target.name)
        if intent is None:
            fresh = self.store.get(job_id)
            if fresh is not None and fresh.status == JobStatus.CANCELLING:
                self._persist_aborted_before_bsub(fresh)
            self._release_reservation(record.id)
            return True
        record = intent
        submission_id = str(record.result["remote"]["submission_id"])
        try:
            self._event_log(record).append(
                "remote.submit_intent",
                job_id=job_id,
                submission_id=submission_id,
                node=target.name,
                attempt=record.attempt,
                relative=(record.result or {}).get("remote", {}).get("relative"),
            )
        except OSError:
            logger.debug("submit_intent event write failed for %s", job_id)

        owner = str(record.result["remote"].get("submit_owner") or _sub_id(job_id))
        register_submit_worker(submission_id, owner, lease_ttl_seconds(self._remote_config))
        intent_record = record

        def _on_code_release_bound(release_id: str) -> None:
            # Contract D (todo 8): bind the verified release through the
            # SAME conditional submit-intent write, strictly before bsub.
            # A lost intent fails the submission closed — never submit
            # unbound, never emit a second write path.
            bound = self._persist_submit_intent(intent_record, target.name, code_release=release_id)
            if bound is None:
                raise remote_submission_rejected(
                    f"submit intent no longer valid for job {job_id}; code "
                    f"release {release_id} could not be bound before bsub"
                )

        try:
            # Pre-bsub barrier: a persisted cancel request aborts here —
            # ``bsub`` is never called (contract A: exactly one outcome).
            if not self._submit_checkpoint(job_id):
                return True
            heartbeat_submit_worker(submission_id)
            lsf_job_id = self.remote_runner.submit_remote(
                record,
                event_log,
                target_node=target.name,
                remote_job_dir=self._stored_remote_dir_arg(record, target),
                on_submitted=lambda lsf_id: self._on_submitted(job_id, lsf_id, record.attempt),
                submission_id=submission_id,
                on_code_release_bound=_on_code_release_bound,
            )
        except remote_submission_rejected as exc:
            self._handle_rejected_submission(record, exc)
            return True
        except remote_submission_indeterminate as exc:
            self._handle_indeterminate_submission(record, exc)
            return True
        except BaseException:
            # The select→submit window closed without a live LSF job —
            # give the reservation back before the error propagates.
            self._release_reservation(record.id)
            raise
        finally:
            # Released only after the outcome (id/rejected/aborted/
            # unconfirmed) was persisted by the branches above or below.
            release_submit_worker(submission_id)

        submitted = self._finalize_after_submit(record, job_id, lsf_job_id)
        if submitted is None:
            return True
        record = submitted
        # Full re-sync now that remote_job_id / result.node are known: the
        # submit-time row predates dispatch and still says node_id="local".
        self._sync_task_status(record)
        if self.tasks is not None:
            try:
                self.tasks.sync_from_job(record)
            except Exception:
                logger.warning("Task index node sync failed for job %s", job_id, exc_info=True)
        self._write_job_json(record)
        return True

    # ------------------------------------------------------------------ #
    # Execution target resolution (single decision point — P4)
    # ------------------------------------------------------------------ #

    def _resolve_execution_target(self, record: JobRecord) -> NodeSpec:
        """Resolve the execution target: target_node > execution_mode > default.

        This is the only place the server default mode is consulted.

        Auto semantics (design D14) apply when the spec pins neither a
        ``target_node`` nor an ``execution_mode``: an empty derived
        requirement follows the server default; a non-empty requirement
        the local machine satisfies stays local; anything else escalates
        to a capability-matched remote node.  An explicit
        ``execution_mode="remote"`` (or a remote server default) selects
        through the same capability/tag filter — only an empty derived
        set keeps the legacy pure least-loaded selection.  Every remote
        selection is prefetched, then selected + reserved atomically
        under the manager lock (design §3.3); explicit remote targets
        occupy the same select→submit reservation.  The derived set is
        stashed on ``record.result["required_software"]`` for audit
        (design §1.3) and persisted by the following
        :meth:`_record_execution_target` call.
        """
        spec = record.spec
        validate_execution_request(spec)
        derived = derive_required_software(spec)
        if derived:
            result = dict(record.result or {})
            result["required_software"] = sorted(derived)
            record.result = result
        if spec.target_node:
            target = self.registry.require(
                spec.target_node,
                required=derived,
                required_tags=frozenset(spec.node_tags or ()),
            )
            if target.kind == "remote":
                with self._lock:
                    self.registry.reserve(target.name, record.id)
            return target
        if spec.execution_mode is None and derived:
            if local_satisfies(derived):
                return self.registry.local
            return self._select_and_reserve_remote(
                record,
                required=derived,
                required_tags=frozenset(spec.node_tags or ()),
            )
        mode = spec.execution_mode or self.default_execution_mode
        if mode == "local":
            return self.registry.local
        return self._select_and_reserve_remote(
            record,
            required=derived,
            required_tags=frozenset(spec.node_tags or ()),
        )

    def _select_and_reserve_remote(
        self,
        record: JobRecord,
        *,
        required: frozenset[str],
        required_tags: frozenset[str],
    ) -> NodeSpec:
        """Select + reserve a remote node atomically under the manager lock.

        Node statuses are prefetched **outside** the lock first: a status
        cache miss means a live SSH probe (30 s timeout × retries), which
        must never block lock-holding operations — after the prefetch the
        in-lock ``select_remote`` only hits the warmed cache.

        Raises:
            NoCapableNodeError: No enabled remote node is configured or
                none satisfies the requirements (permanent; the dispatch
                loop degrades it to a capacity retry, R13).
        """
        self.registry.prefetch_statuses()
        with self._lock:
            try:
                target = self.registry.select_remote(
                    required=required,
                    required_tags=required_tags,
                    affinity_node=self._execution_affinity(record),
                )
            except ExecutionTargetError as exc:
                # Zero enabled remote nodes: the match set is trivially
                # empty — same no-capable semantics as an empty match.
                raise NoCapableNodeError(str(exc)) from exc
            self.registry.reserve(target.name, record.id)
        return target

    def _execution_affinity(self, record: JobRecord) -> str | None:
        """Preferred remote node for an auto job (design §3.4, D6/D15).

        The affinity source is the previous attempt's ``execution_target``:
        continue keeps it in the result (回源), rerun re-stores it under the
        transient ``affinity_node`` key before its result reset.  Only the
        auto path (no pinned ``target_node`` / ``execution_mode``) consults
        it, and only remote names count — ``local`` never carries affinity.
        """
        spec = record.spec
        if spec.target_node is not None or spec.execution_mode is not None:
            return None
        result = record.result or {}
        target = result.get("execution_target")
        if not isinstance(target, str) or target == LOCAL_NODE_NAME:
            target = result.get("affinity_node")
        if not isinstance(target, str) or target == LOCAL_NODE_NAME:
            return None
        return target

    def _record_execution_target(self, record: JobRecord, target: NodeSpec) -> None:
        """Persist execution provenance so poll/cancel/recovery never need
        the server default mode again for this job."""
        result = dict(record.result or {})
        result.pop("affinity_node", None)
        result["execution_target"] = target.name
        result["execution_kind"] = target.kind
        record.result = result
        # Persist the chosen execution target in the jobs columns as well
        # (migration 013).  Local jobs record the head-node hostname; remote
        # jobs record the configured node host.  Historical rows stay NULL —
        # read side falls back to ``result`` / ``spec.target_node``.
        record.node_id = target.name
        record.host = socket.gethostname() if target.kind == "local" else target.host
        revision = record.revision
        for _ in range(3):
            try:
                stored = self.store.update_execution_identity(
                    record.id,
                    expected_revision=revision,
                    expected_attempt=record.attempt,
                    result=result,
                    node_id=record.node_id,
                    host=record.host,
                )
                break
            except JobStateConflictError:
                fresh = self.store.get(record.id)
                if fresh is None or fresh.attempt != record.attempt:
                    # Old-attempt replay or vanished row — drop the write.
                    logger.info("Dropping execution-target write for %s (row moved)", record.id)
                    return
                revision = fresh.revision
        else:
            return
        record.revision = stored.revision
        self._event_log(record).append(
            "execution.target_resolved",
            job_id=record.id,
            target=target.name,
            kind=target.kind,
            attempt=record.attempt,
        )

    def _fail_admission_revalidation(
        self,
        record: JobRecord,
        decision: Any,
    ) -> None:
        """Take the defined non-dispatching FAILED transition on revalidation refusal.

        The reservation is released exactly once (the ledger release is
        idempotent) and no QC process or remote submission is started.
        """
        if self._admission is not None:
            self._admission.release_reservation_id(
                self._admission.reservation_id_for_record(record)
            )
        result = dict(record.result or {})
        admission = dict(result.get("admission") or {})
        admission["revalidation"] = decision.to_dict()
        admission["released"] = True
        result["admission"] = admission
        error = f"admission_revalidation_failed: {decision.reason_code}"
        try:
            stored = self.store.transition(
                record.id,
                expected_status=record.status,
                expected_revision=record.revision,
                expected_attempt=record.attempt,
                status=JobStatus.FAILED,
                error=error,
                completed_at=_utc_now_iso(),
                result=result,
            )
        except JobStateConflictError:
            logger.info("admission revalidation lost the state CAS for %s", record.id)
            return
        self._sync_task_status(stored)
        self._write_job_json(stored)
        try:
            self._event_log(stored).append(
                "job.admission_revalidation_failed",
                job_id=stored.id,
                reason_code=decision.reason_code,
                detail=decision.detail,
                attempt=stored.attempt,
            )
        except OSError:
            logger.debug("admission revalidation event write failed for %s", stored.id)
        self._stage_task_observer.finalize_job(stored.id, "failed")
        self._dispatch_queued_jobs()

    def _record_admission_usage(self, record: JobRecord) -> None:
        if self._admission is None:
            return
        try:
            self._admission.record_usage(record)
        except (OSError, ValueError, RuntimeError):
            logger.warning("admission usage receipt failed for job %s", record.id, exc_info=True)

    def _release_admission(self, record: JobRecord) -> None:
        if self._admission is None:
            return
        try:
            self._admission.release_job(record)
        except (OSError, RuntimeError):
            logger.warning("admission release failed for job %s", record.id, exc_info=True)

    def _release_reservation(self, job_id: str) -> None:
        """Idempotently release a job's in-flight node reservation."""
        with self._lock:
            self.registry.release_job(job_id)

    def _rebuild_reservations(self) -> None:
        """Rebuild in-flight node reservations from persisted targets.

        Startup counterpart of the restart-recovery scan (design §3.3):
        every job still RUNNING/PENDING/PAUSED after recovery with a
        persisted remote ``execution_target`` re-claims its reservation,
        so a restart never under-counts in-flight work (soft cap).  Must
        run AFTER ``_requeue_active_on_startup`` — jobs the recovery scan
        finalised no longer hold a slot.  Caller holds ``self._lock``.
        """
        rebuilt = 0
        for status in (JobStatus.RUNNING, JobStatus.PENDING, JobStatus.PAUSED):
            for record in self.store.list(status=status.value, limit=10000):
                result = record.result or {}
                target = result.get("execution_target")
                if not isinstance(target, str) or target == LOCAL_NODE_NAME:
                    continue
                if self.registry.get(target) is None:
                    continue  # node no longer configured — nothing to reserve
                self.registry.reserve(target, record.id)
                rebuilt += 1
        if rebuilt:
            logger.info("Rebuilt %d in-flight node reservation(s) after restart", rebuilt)

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

    def _ensure_remote_capacity(self, record: JobRecord, target: NodeSpec) -> None:
        """Capacity check for a remote dispatch target.

        Auto-selected nodes are already capacity-filtered by
        ``NodeRegistry.select_remote``; this is the submit-time gate for
        every remote target.  In-flight reservations held by *other*
        jobs count toward the load — the LSF running count only sees
        submitted jobs, so without this two concurrent explicit
        dispatches to the same node would oversubscribe it.  Offline/full
        targets are temporary conditions — the caller retries rather
        than failing the job.
        """
        running = self.registry.remote_running_jobs(target.name)
        if running is None:
            raise ExecutionCapacityUnavailable(
                f"target node '{target.name}' is offline or unreachable"
            )
        in_flight = self.registry.reservations.get(target.name, set()) - {record.id}
        if running + len(in_flight) >= target.max_jobs:
            raise ExecutionCapacityUnavailable(
                f"target node '{target.name}' is at capacity "
                f"({running + len(in_flight)}/{target.max_jobs})"
            )

    # ------------------------------------------------------------------ #
    # Remote catalog prefetch (terminal remote jobs) — persistent (todo 13)
    # ------------------------------------------------------------------ #

    def _build_prepare_executor(self) -> Any:
        from acp.services_data_bootstrap import (
            build_local_prepare_identity_provider,
            build_prepare_executor,
        )

        # D06: the executor must receive the authoritative current identity.
        # local-single states its policy explicitly (no generation/binding/
        # authorization registry, always authorized) instead of falling back
        # to "no provider means allow".
        return build_prepare_executor(
            job_lookup=self.store.get,
            identity_provider=build_local_prepare_identity_provider(self.store.get),
            cache_provider=lambda: self.structure_cache,
            is_remote=self._is_remote_job,
        )

    def _prepare_request(self, record: JobRecord) -> PrepareRequest:
        workflow = str(record.spec.workflow or "")
        return PrepareRequest(
            job_id=record.id,
            attempt=record.attempt,
            resource_id=f"catalog:{workflow}",
            resource_kind=ResourceKind.CACHE,
            resource_digest=f"{record.id}\x1f{record.attempt}\x1f{workflow}",
            permission_scope="local-single",
            payload={"workflow": workflow, "job_id": record.id, "attempt": record.attempt},
        )

    def _ensure_prepare_worker(self) -> None:
        if self._remote_fetcher is None:
            return
        with self._lock:
            if self._closing:
                return
            worker = self._prepare_worker
            if worker is None or not worker.is_alive:
                worker = PrepareOperationWorker(
                    self._prepare_operations,
                    self._prepare_executor,
                    stop_event=self._prepare_worker_stop,
                    wake_queue=self._catalog_prefetch_queue,
                )
                worker.start()
                self._prepare_worker = worker
                self._catalog_prefetch_thread = worker.thread

    def _queue_catalog_prefetch(self, job_id: str) -> None:
        """Enqueue one terminal remote job as a persistent prepare operation.

        No-op when remote fetching is not configured.  The operation is durable
        (deduplicated by its stable scoped key); the wake signal below only
        shortens the worker's poll latency and is never the source of truth.
        """
        if self._remote_fetcher is None:
            return
        record = self.store.get(job_id)
        if record is None or not record.status.is_terminal or not self._is_remote_job(record):
            return
        try:
            self._prepare_operations.enqueue(self._prepare_request(record))
        except Exception:
            logger.warning("Failed to persist prepare operation for %s", job_id, exc_info=True)
            return
        self._ensure_prepare_worker()
        try:
            self._catalog_prefetch_queue.put(job_id)
        except Exception:  # pragma: no cover - unbounded queue never raises
            pass

    def _prefetch_remote_catalog(self, job_id: str) -> None:
        """Pull one terminal remote job's small catalog files into the cache.

        The backend owns this sync so that ``availability`` flips from
        ``pending_fetch`` to ``ready`` without depending on a browser issuing
        ``?fetch=1`` (cached workbench JS may lack the terminal retry).  Geometry
        stays lazy; failures are logged and never propagate.  The real fetch is
        delegated to the injected executor so the worker and this entry point
        share one implementation.
        """
        result = self._prepare_executor.prefetch(job_id)
        if result.prepared and result.detail == "fetched":
            logger.info("Prefetched remote structure catalog for job %s", job_id)
        elif not result.prepared:
            logger.debug(
                "Remote structure catalog not ready after prefetch for %s (%s)",
                job_id,
                result.detail,
            )

    def _queue_startup_catalog_prefetch(self) -> None:
        """Replay undecided prepare operations and enqueue recent terminal jobs.

        Repairs jobs that reached a terminal state before this process started
        (e.g. across an upgrade) so their viewers are ready on first open, and
        replays operations an earlier process left undecided (DB authority).
        """
        if self._remote_fetcher is None:
            return
        try:
            report = self._prepare_operations.recover()
            if report.requeued:
                logger.info(
                    "Recovered %d undecided prepare operation(s) on startup", report.requeued
                )
        except Exception:
            logger.warning("Prepare-operation recovery failed", exc_info=True)
        queued = 0
        try:
            for status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                for record in self.store.list(status=status.value, limit=100):
                    if queued >= 300:
                        break
                    if not self._is_remote_job(record):
                        continue
                    cache = self.structure_cache
                    workflow = str(record.spec.workflow or "")
                    if cache.catalog_ready(Path(record.work_dir), workflow):
                        continue
                    if cache.catalog_ready(cache.job_root(record.id), workflow):
                        continue
                    self._queue_catalog_prefetch(record.id)
                    queued += 1
        except Exception:
            logger.warning("Startup remote catalog prefetch sweep failed", exc_info=True)
        if queued:
            logger.info("Queued %d remote catalog prefetch(es) on startup", queued)
        if queued or self._prepare_operations.pending_count() > 0:
            self._ensure_prepare_worker()

    def _poll_job(self, job_id: str) -> None:
        """Single non-blocking check of one job's status.

        Single owner of the poll scan set (see ``_POLL_SCAN_STATUSES``):
        state transitions and terminal persistence go through conditional
        CAS writes (``store.transition`` / ``store.update_progress``) so a
        stale observation can never override a concurrent pause or resurrect
        a terminal state; terminal side effects run only after the terminal
        CAS succeeds.
        """
        record = self.store.get(job_id)
        if record is None or record.status.is_terminal or record.status not in _POLL_SCAN_STATUSES:
            return

        # D02: a pending/aborted submission is reconciled here — never
        # through poll_remote's no-``_job_states`` terminal fast path,
        # which would mis-finalise an indeterminate job as FAILED.
        if self._poll_pending_submission(record):
            return

        # D05: a remote CANCELLING row is single-owned by the cancel
        # confirmation reconcile — never finalised from a raw poll
        # observation (cancel_event + non-zero exit is not evidence).
        if record.status == JobStatus.CANCELLING and self._is_remote_job(record):
            self._reconcile_cancelling_remote(record)
            return

        loaded_revision = record.revision
        loaded_attempt = record.attempt
        cancel_event = self._cancel_events.get(job_id, threading.Event())
        event_log = self._event_log(record)
        is_remote = self._is_remote_job(record) and self.remote_runner is not None

        observation: RemotePollObservation | None = None
        observed_status: JobStatus | None = None
        if is_remote:
            try:
                observation = self.remote_runner.poll_remote(  # type: ignore[union-attr]
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
            is_terminal = observation.terminal
            exit_code = observation.exit_code
            observed_status = observation.observed_status
        else:
            try:
                is_terminal, exit_code = self.runner.poll(record)
            except Exception as exc:
                self._fail_local_poll(
                    record, job_id, event_log, loaded_revision, loaded_attempt, exc
                )
                return
            self._metrics_extractor.extract(record.id, Path(record.work_dir))

        if not is_terminal:
            self._persist_poll_observation(
                record,
                job_id,
                loaded_revision,
                loaded_attempt,
                observed_status,
                event_log,
                is_remote,
            )
            return

        self._persist_terminal(
            record,
            job_id,
            loaded_revision,
            loaded_attempt,
            exit_code,
            cancel_event,
            event_log,
            is_remote,
            observation,
        )

    def _poll_pending_submission(self, record: JobRecord) -> bool:
        """Rate-limited reconcile for scan-set rows with a pending submission.

        Returns True when the poll was consumed by reconciliation (the
        caller must NOT fall through to ``poll_remote``).  A persisted
        ``aborted_before_bsub`` confirms CANCELLED directly — the marker
        is the positive no-job evidence (contract A).
        """
        remote_meta = (record.result or {}).get("remote")
        if not isinstance(remote_meta, dict):
            return False
        submit_state = remote_meta.get("submit_state")
        if submit_state == "aborted_before_bsub" and record.status == JobStatus.CANCELLING:
            self._confirm_aborted_cancellation(record)
            return True
        if submit_state not in _SUBMIT_RECONCILE_STATES:
            return False
        now = time.monotonic()
        last = self._submit_reconcile_at.get(record.id, 0.0)
        if now - last < float(self.poll_interval):
            return True
        self._submit_reconcile_at[record.id] = now
        self._reconcile_submission_record(record)
        return True

    def _drop_stale_poll(
        self,
        job_id: str,
        exc: JobStateConflictError | None,
        event_log: JobEventLog,
        *,
        context: str,
        actual: dict[str, Any] | None = None,
    ) -> None:
        """Discard an observation whose target row moved — never a job failure."""
        actual_state = actual if actual is not None else (exc.actual if exc is not None else None)
        logger.info(
            "Dropping stale poll observation for job %s (%s): expected %s, actual %s",
            job_id,
            context,
            exc.expected if exc is not None else None,
            actual_state,
        )
        try:
            event_log.append(
                "job.poll_dropped_stale",
                job_id=job_id,
                context=context,
                expected=exc.expected if exc is not None else None,
                actual=actual_state,
            )
        except OSError:
            logger.debug("stale-poll event append failed for %s", job_id, exc_info=True)

    def _fail_local_poll(
        self,
        record: JobRecord,
        job_id: str,
        event_log: JobEventLog,
        loaded_revision: int,
        loaded_attempt: int,
        exc: Exception,
    ) -> None:
        logger.exception("Local poll failed for job %s", job_id)
        try:
            stored = self.store.transition(
                job_id,
                expected_status=_POLL_SCAN_STATUSES,
                expected_revision=loaded_revision,
                expected_attempt=loaded_attempt,
                status=JobStatus.FAILED,
                error=f"Local poll error: {exc}",
                completed_at=_utc_now_iso(),
            )
        except JobStateConflictError as conflict:
            self._drop_stale_poll(job_id, conflict, event_log, context="local-poll-error")
            return
        self._sync_task_status(stored)
        self._write_job_json(stored)
        event_log.append("job.failed", job_id=job_id, error=str(exc))
        self._stage_task_observer.finalize_job(job_id, "failed")
        self._record_admission_usage(stored)
        self._release_admission(stored)
        self._release_reservation(job_id)
        self._dispatch_queued_jobs()

    def _persist_poll_observation(
        self,
        record: JobRecord,
        job_id: str,
        loaded_revision: int,
        loaded_attempt: int,
        observed_status: JobStatus | None,
        event_log: JobEventLog,
        is_remote: bool,
    ) -> None:
        """Persist a non-terminal poll: state transition or narrow progress write."""
        fresh = self.store.get(job_id)
        if fresh is None or fresh.status != record.status or fresh.attempt != loaded_attempt:
            actual: dict[str, Any] | None = None
            if fresh is not None:
                actual = {
                    "status": fresh.status.value,
                    "revision": fresh.revision,
                    "attempt": fresh.attempt,
                }
            self._drop_stale_poll(
                job_id,
                None,
                event_log,
                context="poll-progress",
                actual=actual,
            )
            return
        try:
            if observed_status is not None and observed_status != record.status:
                stored = self.store.transition(
                    job_id,
                    expected_status=(JobStatus.PENDING, JobStatus.PAUSED, JobStatus.RUNNING),
                    expected_revision=loaded_revision,
                    expected_attempt=loaded_attempt,
                    status=observed_status,
                    progress=record.progress,
                    current_stage=record.current_stage,
                )
            else:
                stored = self.store.update_progress(
                    job_id,
                    expected_revision=loaded_revision,
                    progress=record.progress,
                    current_stage=record.current_stage,
                    pid=record.pid,
                )
        except JobStateConflictError as exc:
            self._drop_stale_poll(job_id, exc, event_log, context="poll-progress")
            return
        self._sync_task_status(stored)
        if is_remote and stored.status == JobStatus.RUNNING:
            # Release the select→submit reservation only once LSF has actually
            # started the job (PEND/PSUSP jobs do not count toward the node's
            # running-jobs probe); terminal transitions release below.
            self._release_reservation(job_id)

    def _try_envelope_completion(
        self,
        record: JobRecord,
        job_id: str,
        loaded_revision: int,
        loaded_attempt: int,
        exit_code: int | None,
        cancel_event: threading.Event,
        event_log: JobEventLog,
        is_remote: bool,
        observation: RemotePollObservation | None = None,
    ) -> bool:
        """Opt-in receipt-verified completion for an envelope-selected attempt.

        Returns ``True`` when this path consumed the terminal observation (CAS
        performed or the observation isolated/deferred) and the legacy
        exit-code terminal decision must not run.  Returns ``False`` for a
        legacy-selected attempt or when pause/cancel precedence keeps the
        existing path (a stale/foreign receipt is isolated before that check).
        """
        if (
            not self._envelope_completion_enabled
            or self._attempt_completion is None
            or exit_code == EXIT_WAITING_REVIEW
            or record.spec.workflow not in DEFAULT_ENVELOPE_WORKFLOWS
        ):
            return False
        work_dir = Path(record.work_dir)
        cancel_intent = record.status == JobStatus.CANCELLING or cancel_event.is_set()
        try:
            choice = read_attempt_choice(work_dir)
        except AttemptProtocolError as exc:
            if cancel_intent:
                return False
            self._cas_envelope_failure(
                record,
                job_id,
                loaded_revision,
                loaded_attempt,
                exit_code,
                "attempt_protocol_corrupt",
                str(exc),
                event_log,
                is_remote=is_remote,
                observation=observation,
            )
            return True
        if choice is None:
            choice = attempt_choice_from_result(record.result)
        if (
            choice is None
            or choice.entry != EXECUTION_ENTRY_ENVELOPE
            or choice.attempt != record.attempt
        ):
            return False

        from acp.services_completion_bootstrap import FrozenGroupSnapshotReader

        snapshot = FrozenGroupSnapshotReader(self.store).read(job_id)
        if snapshot is not None:
            # The remote poll mutates the in-memory record before the terminal
            # CAS; keep the current result/revision/status in the snapshot so
            # the T26 CAS does not drop remote provenance/metadata.
            snapshot = replace(
                snapshot,
                result=dict(record.result or {}),
                revision=record.revision,
                status=str(record.status.value),
            )
        if snapshot is None or snapshot.envelope is None:
            if cancel_intent:
                return False
            self._cas_envelope_failure(
                record,
                job_id,
                loaded_revision,
                loaded_attempt,
                exit_code,
                "current_corrupt_after_exit",
                "frozen attempt snapshot unavailable after the execution exited",
                event_log,
                is_remote=is_remote,
                observation=observation,
            )
            return True

        receipt_path = work_dir / "execution_receipt.json"
        decoded = None
        try:
            decoded = read_receipt(receipt_path)
        except (ReceiptConflictError, EnvelopeError, OSError, ValueError):
            decoded = None

        if decoded is not None:
            check = verify_receipt(
                decoded,
                snapshot.envelope,
                current_attempt=record.attempt,
                current_submission_id=snapshot.submission_id,
                current_release_id=snapshot.release_id,
                owner_token=snapshot.owner,
                fencing_token=snapshot.fencing_token,
            )
            if not check.accepted:
                event_log.append(
                    "job.completion_receipt_rejected",
                    job_id=job_id,
                    code=check.code,
                    attempt=record.attempt,
                    receipt_attempt=decoded.attempt,
                    detail=check.reason,
                    idempotency_key=(f"completion-rejected:{job_id}:{record.attempt}:{check.code}"),
                )
                return True
            if cancel_intent:
                # Pause/cancel intent keeps precedence; the current receipt is
                # not used to override it.
                return False
            verdict = self._attempt_completion.complete(
                snapshot, receipt=decoded, confirmed_exited=True
            )
        else:
            if cancel_intent:
                return False
            verdict = self._attempt_completion.complete(
                snapshot,
                receipt_path=receipt_path,
                confirmed_exited=True,
            )

        if verdict.code in {
            "published",
            "accepted_pending_publication",
            "already_published",
        }:
            stored = self.store.get(job_id)
            if stored is not None:
                self._run_terminal_side_effects(stored, job_id, event_log, is_remote, observation)
            return True
        if verdict.code == "current_corrupt_after_exit":
            failure = verdict.failure
            self._cas_envelope_failure(
                record,
                job_id,
                loaded_revision,
                loaded_attempt,
                exit_code,
                failure.code if failure is not None else "current_corrupt_after_exit",
                failure.message if failure is not None else "current receipt missing or corrupt",
                event_log,
                is_remote=is_remote,
                observation=observation,
            )
            return True
        event_log.append(
            "job.completion_deferred",
            job_id=job_id,
            code=verdict.code,
            attempt=record.attempt,
            detail=verdict.detail,
        )
        return True

    def _cas_envelope_failure(
        self,
        record: JobRecord,
        job_id: str,
        expected_revision: int,
        expected_attempt: int,
        exit_code: int | None,
        code: str,
        message: str,
        event_log: JobEventLog,
        *,
        is_remote: bool = False,
        observation: RemotePollObservation | None = None,
    ) -> None:
        """Write the one CAS failure for a current attempt with no valid receipt."""
        result = dict(record.result or {})
        result["terminal_side_effects_done"] = False
        result["completion_failure"] = {"code": code, "message": message}
        failure_exit = exit_code if exit_code not in (None, 0) else 1
        try:
            stored = self.store.transition(
                job_id,
                expected_status=_POLL_SCAN_STATUSES,
                expected_revision=expected_revision,
                expected_attempt=expected_attempt,
                status=JobStatus.FAILED,
                exit_code=failure_exit,
                completed_at=_utc_now_iso(),
                error=f"{code}: {message}",
                result=result,
            )
        except JobStateConflictError as exc:
            self._drop_stale_poll(job_id, exc, event_log, context="envelope-completion-failure")
            return
        event_log.append(
            "job.completion_failure",
            job_id=job_id,
            code=code,
            attempt=expected_attempt,
        )
        self._run_terminal_side_effects(stored, job_id, event_log, is_remote, observation)

    def _persist_terminal(
        self,
        record: JobRecord,
        job_id: str,
        loaded_revision: int,
        loaded_attempt: int,
        exit_code: int | None,
        cancel_event: threading.Event,
        event_log: JobEventLog,
        is_remote: bool,
        observation: RemotePollObservation | None,
    ) -> None:
        """Persist the terminal transition first, then run retryable side effects."""
        if self._try_envelope_completion(
            record,
            job_id,
            loaded_revision,
            loaded_attempt,
            exit_code,
            cancel_event,
            event_log,
            is_remote,
            observation,
        ):
            return
        progress = record.progress
        current_stage = record.current_stage
        error = record.error
        result_payload = dict(record.result or {})
        if is_remote and observation is not None and observation.final_state is not None:
            final = observation.final_state
            if isinstance(final.get("result"), dict):
                result_payload = dict(final["result"])
            if final.get("error") is not None:
                error = final["error"]
            if observation.progress is not None:
                progress = observation.progress
            current_stage = observation.current_stage

        review_payload: Any = None
        if exit_code == EXIT_WAITING_REVIEW:
            # A mechanism study paused at a review gate: translate the
            # dedicated exit code into WAITING_REVIEW instead of a terminal
            # state — no side-effect marker (not terminal).
            review_payload = _load_review_payload(Path(record.work_dir))
            if review_payload is not None:
                result_payload["review_payload"] = review_payload
            fields: dict[str, Any] = {
                "status": JobStatus.WAITING_REVIEW,
                "exit_code": exit_code,
                "result": result_payload,
                "progress": progress,
                "current_stage": current_stage,
                "error": error,
            }
        else:
            if record.status == JobStatus.CANCELLING and not is_remote:
                # A local control intent survives a controller restart: the
                # process being gone IS the confirmed stop for a local task,
                # so the durable CANCELLING row must finish CANCELLED instead
                # of being overwritten by the stale/absent exit observation.
                status = JobStatus.CANCELLED
                if exit_code in (None, 0):
                    exit_code = 130
            elif cancel_event.is_set() and exit_code and exit_code != 0:
                if is_remote:
                    # Contract A (D05): a remote cancel is finalised only
                    # after bjobs confirms the job is gone — never from
                    # cancel_event + non-zero exit alone; the CANCELLING
                    # reconcile owns that confirmation.
                    fresh = self.store.get(job_id)
                    if fresh is not None and fresh.status == JobStatus.CANCELLING:
                        self._reconcile_cancelling_remote(fresh)
                    return
                status = JobStatus.CANCELLED
            elif exit_code == 0:
                status = JobStatus.COMPLETED
                progress = 1.0
                record.result = result_payload
                # Capture/provenance is a terminal side effect (local COMPLETED
                # only): it runs inside the post-CAS boundary below so a stale
                # CAS never registers anything and a retry is idempotent.
                record.result = self._collect_result(record)
                result_payload = dict(record.result or {})
            else:
                status = JobStatus.FAILED
                error = error or f"workflow exited with code {exit_code}"
            if status.is_terminal:
                # Explicit False scopes the reconcile side-effect retry to
                # jobs whose terminal transition went through this path —
                # legacy terminal rows never carry the key.
                result_payload["terminal_side_effects_done"] = False
            fields = {
                "status": status,
                "exit_code": exit_code,
                "completed_at": _utc_now_iso(),
                "progress": progress,
                "current_stage": current_stage,
                "error": error,
                "result": result_payload,
                "pid": record.pid,
            }

        try:
            stored = self.store.transition(
                job_id,
                expected_status=_POLL_SCAN_STATUSES,
                expected_revision=loaded_revision,
                expected_attempt=loaded_attempt,
                **fields,
            )
        except JobStateConflictError as exc:
            self._drop_stale_poll(job_id, exc, event_log, context="terminal")
            return

        self._run_terminal_side_effects(
            stored,
            job_id,
            event_log,
            is_remote,
            observation,
            review_payload,
        )

    def _run_terminal_side_effects(
        self,
        stored: JobRecord,
        job_id: str,
        event_log: JobEventLog,
        is_remote: bool,
        observation: RemotePollObservation | None,
        review_payload: Any = None,
    ) -> None:
        """Run the idempotent post-CAS terminal side effects (shared boundary).

        The caller has already committed the terminal CAS.  This helper re-reads
        the row under ``_side_effect_lock`` so a superseded attempt cannot write
        into a rerun directory, then emits/captures and finally persists the
        ``terminal_side_effects_done`` marker on success.
        """
        side_effects_ok = True
        with self._side_effect_lock:
            stored = self._reread_side_effect_scope(stored)
            if stored is None:
                logger.info(
                    "Terminal side effects for job %s superseded by a newer attempt; skipped",
                    job_id,
                )
                return
            try:
                self._record_admission_usage(stored)
                self._release_admission(stored)
                self._release_reservation(job_id)
                self._sync_task_status(stored)
                if stored.status.is_terminal:
                    if is_remote and self.remote_runner is not None:
                        stage_events = observation.stage_events if observation is not None else ()
                        self.remote_runner.apply_terminal_side_effects(
                            stored, event_log, stage_events
                        )
                    else:
                        self._emit_local_terminal_event(stored, event_log)
                        if stored.status == JobStatus.COMPLETED:
                            self._capture_local_completion(stored)
                self._write_job_json(stored)
                with self._lock:
                    self._cancel_events.pop(job_id, None)
            except Exception:
                side_effects_ok = False
                logger.warning(
                    "Terminal side effects incomplete for job %s; reconcile will retry",
                    job_id,
                    exc_info=True,
                )

        if stored.status == JobStatus.WAITING_REVIEW:
            event_log.append("job.waiting_review", job_id=job_id, payload=review_payload)
            return

        self._dispatch_queued_jobs()
        if is_remote:
            self._queue_catalog_prefetch(job_id)
        if side_effects_ok:
            with self._side_effect_lock:
                self._mark_terminal_side_effects_done(stored)

    def _reread_side_effect_scope(self, stored: JobRecord) -> JobRecord | None:
        """Re-read the row inside the side-effect boundary before writing.

        Returns the fresh row when it is still the same attempt+status the CAS
        committed, or ``None`` when a rerun/edit/continue superseded it — a
        stale attempt's side effects must never touch the new attempt's
        ``job.json``, tasks projection, stage provenance, or cancel event.
        """
        fresh = self.store.get(stored.id)
        if fresh is None:
            return None
        if fresh.attempt != stored.attempt or fresh.status != stored.status:
            return None
        return fresh

    def _emit_local_terminal_event(self, record: JobRecord, event_log: JobEventLog) -> None:
        """Emit the local terminal completion event idempotently (post-CAS)."""
        if record.status == JobStatus.COMPLETED:
            event_type = "job.completed"
        elif record.status == JobStatus.CANCELLED:
            event_type = "job.cancelled"
        else:
            event_type = "job.failed"
        exit_code = record.exit_code
        if exit_code is None:
            exit_code = 0 if record.status == JobStatus.COMPLETED else 1
        event_log.append(
            event_type,
            job_id=record.id,
            exit_code=exit_code,
            idempotency_key=f"terminal:{record.id}:{record.attempt}:{event_type}",
        )

    def _capture_local_completion(self, record: JobRecord) -> None:
        """Local COMPLETED artifact capture + provenance (post-CAS, idempotent)."""
        self.runner._capture_artifacts(record, Path(record.work_dir))
        self.runner._store_provenance(
            record,
            command_line=(record.result or {}).get("command_line", ""),
        )

    def _mark_terminal_side_effects_done(self, record: JobRecord) -> None:
        result = dict(record.result or {})
        if result.get("terminal_side_effects_done") is True:
            return
        result["terminal_side_effects_done"] = True
        try:
            self.store.update_progress(record.id, expected_revision=record.revision, result=result)
        except JobStateConflictError:
            logger.debug("Side-effect marker write raced for job %s; reconcile retries", record.id)

    def _retry_terminal_side_effects(self, record: JobRecord) -> None:
        """Category ②: re-run terminal side effects until the marker persists.

        Status/attempt guarded: only the *current* terminal attempt owns its
        side effects.  A superseded attempt (rerun/edit/continue bumped it)
        exits without touching the new attempt.  Local COMPLETED also rebuilds
        artifacts/provenance idempotently.
        """
        with self._side_effect_lock:
            fresh = self.store.get(record.id)
            if fresh is None or not fresh.status.is_terminal or fresh.attempt != record.attempt:
                return
            event_log = self._event_log(fresh)
            try:
                self._record_admission_usage(fresh)
                self._release_admission(fresh)
                self._release_reservation(fresh.id)
                if self._is_remote_job(fresh) and self.remote_runner is not None:
                    self.remote_runner.apply_terminal_side_effects(fresh, event_log)
                else:
                    self._emit_local_terminal_event(fresh, event_log)
                    if fresh.status == JobStatus.COMPLETED:
                        self._capture_local_completion(fresh)
                self._sync_task_status(fresh)
                self._write_job_json(fresh)
                with self._lock:
                    self._cancel_events.pop(fresh.id, None)
                self._mark_terminal_side_effects_done(fresh)
            except Exception:
                logger.warning(
                    "Terminal side-effect retry failed for job %s (will retry)",
                    fresh.id,
                    exc_info=True,
                )

    def _resume_pending_publications(self, *, limit: int = 100) -> None:
        """Startup/reconcile sweep: continue terminal rows with pending publication.

        The T26 terminal CAS writes ``publication_pending=True``; a crash after
        that CAS but before/during the publisher leaves this marker.  Recovery
        retries only publication — it never calls the executor.
        """
        if not self._publication_reconcile_enabled:
            return
        for status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            for record in self.store.list(status=status.value, limit=limit):
                if _needs_publication_retry(record):
                    self._resume_attempt_publication(record)

    def _resume_attempt_publication(self, record: JobRecord) -> None:
        """Retry one terminal row's pending publication through the T26 service."""
        if not self._publication_reconcile_enabled or self._attempt_completion is None:
            return
        from acp.services_completion_bootstrap import FrozenGroupSnapshotReader

        try:
            snapshot = FrozenGroupSnapshotReader(self.store).read(record.id)
        except Exception:
            logger.warning("Publication snapshot read failed for job %s", record.id, exc_info=True)
            return
        if snapshot is None:
            self._event_log(record).append(
                "job.publication_pending",
                job_id=record.id,
                code="frozen_snapshot_unavailable",
                attempt=record.attempt,
                idempotency_key=f"publication-pending:{record.id}:{record.attempt}:snapshot",
            )
            return
        try:
            verdict = self._attempt_completion.resume_pending_publication(snapshot)
        except Exception as exc:
            logger.warning("Publication retry failed for job %s", record.id, exc_info=True)
            self._event_log(record).append(
                "job.publication_pending",
                job_id=record.id,
                code=type(exc).__name__,
                attempt=record.attempt,
                detail=str(exc),
                idempotency_key=(
                    f"publication-pending:{record.id}:{record.attempt}:{type(exc).__name__}"
                ),
            )
            return
        if verdict.code in {"published", "already_published"}:
            self._event_log(record).append(
                "job.publication_completed",
                job_id=record.id,
                attempt=record.attempt,
                result_id=getattr(verdict.publication_outcome, "result_id", ""),
                idempotency_key=f"publication-completed:{record.id}:{record.attempt}",
            )
            return
        self._event_log(record).append(
            "job.publication_pending",
            job_id=record.id,
            code=verdict.code,
            attempt=record.attempt,
            detail=verdict.detail,
            idempotency_key=(f"publication-pending:{record.id}:{record.attempt}:{verdict.code}"),
        )

    def _reconcile_starting_submission(self, record: JobRecord) -> None:
        """Category ①: converge a recoverable STARTING submission to PENDING."""
        if record.id in self._submission_jobs:
            # An in-process submit thread owns this row: it added the id in
            # _start_submission_thread and discards it only when the
            # submission attempt finished. Its bsub may not have run yet,
            # so absence evidence must not fail the job (F2, todo 5 d2).
            return
        self._reconcile_submission_record(record)

    def _reconcile_once(self) -> None:
        """One pass over the categories outside the regular poll scan.

        Category ① STARTING + pending submission intent; category ②
        terminal jobs whose side effects have not been marked done;
        category ③ terminal rows carrying unconfirmed orphans plus the
        CANCELLING rows whose submission is pending or was aborted
        before ``bsub``.
        """
        for record in self.store.list(status=JobStatus.STARTING.value, limit=10000):
            if _needs_submission_reconcile(record):
                self._reconcile_starting_submission(record)
        for record in self.store.list(status=JobStatus.CANCELLING.value, limit=10000):
            if self._is_remote_job(record):
                self._reconcile_cancelling_remote(record)
        for status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            for record in self.store.list(status=status.value, limit=10000):
                side_effect_retry = _needs_side_effect_retry(record)
                if side_effect_retry:
                    self._retry_terminal_side_effects(record)
                if self._publication_reconcile_enabled and _needs_publication_retry(record):
                    self._resume_attempt_publication(record)
                if not side_effect_retry and self._pending_orphans(record.result or {}):
                    self._orphan_cancel_pass(record)
        self._reconcile_task_projection()
        self._sweep_outbox()

    def _reconcile_task_projection(self) -> None:
        """Repair one bounded batch of jobs/tasks projection drift.

        Pseudocode of the bound: with batch size B per scan, N drifted rows
        converge within ``ceil(N/B)`` scans — each scan repairs up to B and
        the repaired rows leave the drift set, so the next scan reaches the
        next-oldest drift (no starvation). Errors are non-fatal: the next
        scan retries.
        """
        if self.tasks is None:
            return
        try:
            self.tasks.reconcile_projection(self._task_reconcile_batch)
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
            logger.warning("Task projection reconcile failed", exc_info=True)

    def _poll_loop(self) -> None:
        """Background daemon: periodically poll all RUNNING jobs."""
        logger.info(
            "Poll loop started (interval=%ds, remote=%s)",
            self.poll_interval,
            self._is_remote_enabled(),
        )
        while not self._poll_stop.wait(self.poll_interval):
            try:
                # WAITING_REVIEW, PAUSED and STARTING jobs are intentionally
                # excluded here: WAITING_REVIEW is held for manual review, a
                # PAUSED job is frozen (SIGSTOP / bstop) with nothing for
                # polling to observe, and STARTING submissions are owned by
                # the reconcile loop (_reconcile_loop).
                for status in _POLL_SCAN_STATUSES:
                    records = self.store.list(status=status.value, limit=10000)
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
                self._sweep_idempotency_staging()
                self._dispatch_queued_jobs()
            except Exception:
                logger.exception("Poll loop iteration failed")
        logger.info("Poll loop stopped")

    def _reconcile_loop(self) -> None:
        """Background daemon: converge categories outside the regular scan.

        Runs in parallel with ``_poll_loop`` and owns only: ① STARTING jobs
        with a pending submission intent/unconfirmation, ② terminal jobs
        whose ``terminal_side_effects_done`` marker is still False (side
        effects crashed before the marker persisted).  CANCELLING stays with
        the regular poll scan.
        """
        logger.info("Reconcile loop started (interval=%ds)", self.poll_interval)
        while not self._poll_stop.wait(self.poll_interval):
            try:
                self._reconcile_once()
            except Exception:
                logger.exception("Reconcile loop iteration failed")
        logger.info("Reconcile loop stopped")

    def _collect_result(self, record: JobRecord) -> dict[str, Any]:
        state_path = find_workflow_state(Path(record.work_dir))
        result: dict[str, Any] = dict(record.result or {})
        if state_path is not None and state_path.exists():
            try:
                result["state"] = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return result

    def _sweep_retired_inflight_jobs(self) -> None:
        """Mark in-flight retired-workflow jobs as FAILED on startup.

        Local PAUSED → SIGCONT cleanup; remote PAUSED → bkill cleanup.
        Read-only detail and purge remain available.
        """
        if not _RETIRED_WORKFLOWS:
            return
        active_statuses = (
            JobStatus.RUNNING.value,
            JobStatus.QUEUED.value,
            JobStatus.PAUSED.value,
        )
        for status_val in active_statuses:
            for record in self.store.list(status=status_val, limit=100000):
                if record.spec.workflow not in _RETIRED_WORKFLOWS:
                    continue
                if record.status == JobStatus.PAUSED:
                    if self._is_remote_job(record) and self.remote_runner is not None:
                        try:
                            self.remote_runner.cancel_remote(record.id, record)
                        except Exception:
                            logger.debug(
                                "bkill failed for retired PAUSED job %s", record.id, exc_info=True
                            )
                    else:
                        try:
                            import os
                            import signal

                            if record.pid and hasattr(os, "killpg"):
                                os.killpg(record.pid, signal.SIGCONT)
                        except (OSError, ProcessLookupError):
                            pass
                failed = self._cas_write(
                    record,
                    expected_status=record.status,
                    status=JobStatus.FAILED,
                    error=_RETIRED_INFLIGHT_REASON,
                    completed_at=_utc_now_iso(),
                    decide=lambda fresh: None,
                )
                if failed is None:
                    continue
                record = failed
                self._stage_task_observer.finalize_job(record.id, JobStatus.FAILED.value)
                self._sync_task_status(record)
                logger.info(
                    "Swept retired inflight job %s (workflow=%s, was=%s) → FAILED",
                    record.id,
                    record.spec.workflow,
                    status_val,
                )

    def _run_lock_peer_pid(self, record: JobRecord) -> int | None:
        """Live foreign ACP server process still owning the task's run.lock."""
        try:
            payload = json.loads(
                runtime_file(Path(record.work_dir), "run.lock").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None
        owner = payload.get("owner_pid") if isinstance(payload, dict) else None
        if not isinstance(owner, int) or owner <= 0:
            return None
        if owner == os.getpid() or not pid_is_alive(owner):
            return None
        cmdline = read_cmdline(owner).lower()
        if "acp" in cmdline or "uvicorn" in cmdline:
            return owner
        return None

    def _run_lock_payload(self, record: JobRecord) -> dict[str, object]:
        try:
            payload = json.loads(
                runtime_file(Path(record.work_dir), "run.lock").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _adopt_existing_task_process(self, record: JobRecord) -> bool:
        """Adopt a live worker left behind in the submission window.

        A controller killed between spawning the worker and persisting the
        RUNNING row must not cause the next controller to either double
        dispatch or fail the job while the worker keeps computing.  Identity
        is proven from the task directory's live process set plus the
        attempt-tagged ``run.lock``; a live foreign ACP owner still wins
        (never adopt a peer's task).  Returns True when the worker was
        adopted, in which case the caller must not spawn another process.
        """
        if self._is_remote_job(record):
            return False
        pids = list(self.runner._live_task_pids(record) or ())
        if not pids:
            return False
        peer = self._run_lock_peer_pid(record)
        if peer is not None:
            raise RuntimeError(
                f"task {record.id} still has live process(es) {pids} in "
                f"{record.work_dir}; refusing to start a second execution"
            )
        payload = self._run_lock_payload(record)
        recorded_attempt = payload.get("attempt")
        if isinstance(recorded_attempt, int) and not isinstance(recorded_attempt, bool):
            if recorded_attempt != record.attempt:
                self._terminate_stale_task_processes(record)
                return False
        recorded_pid = payload.get("task_pid")
        pid = recorded_pid if isinstance(recorded_pid, int) and recorded_pid in pids else pids[0]
        if not isinstance(pid, int) or isinstance(pid, bool):
            return False
        record.pid = pid
        self.runner.attach_run_lock(record, task_pid=pid)
        try:
            self._event_log(record).append(
                "process.adopted",
                job_id=record.id,
                pid=pid,
                pids=list(pids),
                attempt=record.attempt,
            )
        except OSError:
            logger.debug("adoption event failed for %s", record.id, exc_info=True)
        return True

    def _adopt_live_orphans_on_startup(self) -> None:
        """Adopt live workers whose rows never reached RUNNING.

        The reconnecting controller skips destructive startup recovery, so
        the submission-window orphan would otherwise stay unobserved (and be
        double-dispatched if its row was still QUEUED).
        """
        for status in (JobStatus.STARTING, JobStatus.QUEUED, JobStatus.PENDING):
            for record in self.store.list(status=status.value, limit=10000):
                if self._is_remote_job(record) or _has_pending_submission(record):
                    continue
                if self._skip_recovery_for_peer(record):
                    continue
                try:
                    if not self._adopt_existing_task_process(record):
                        continue
                except RuntimeError:
                    logger.warning(
                        "Could not adopt live worker for job %s (peer owns it)", record.id
                    )
                    continue
                adopted = self._cas_write(
                    record,
                    expected_status=record.status,
                    status=JobStatus.RUNNING,
                    pid=record.pid,
                    decide=lambda fresh: None,
                )
                if adopted is None:
                    continue
                self._sync_task_status(adopted)
                self._write_job_json(adopted)
                logger.info(
                    "Adopted startup-window worker for job %s (pid=%s, attempt=%s)",
                    adopted.id,
                    adopted.pid,
                    adopted.attempt,
                )

    def _skip_recovery_for_peer(self, record: JobRecord) -> bool:
        """True when a live peer server owns this task — leave it alone.

        Belt-and-braces companion to the run_root instance lock: even if a
        second manager somehow booted, restart-recovery must not kill a task
        whose ``run.lock`` owner is still alive and computing.
        """
        peer = self._run_lock_peer_pid(record)
        if peer is None:
            return False
        logger.warning(
            "Skipping restart-recovery for job %s: run.lock held by live server pid=%s",
            record.id,
            peer,
        )
        try:
            self._event_log(record).append(
                "manager.peer_conflict", job_id=record.id, owner_pid=peer
            )
        except OSError:
            logger.debug("peer_conflict event failed for %s", record.id, exc_info=True)
        return True

    def _requeue_active_on_startup(self) -> None:
        self._sweep_retired_inflight_jobs()

        # Mark interrupted jobs FAILED so their work_dir is retained for
        # triage.  The ``[RESTART_FAILED]`` prefix lets LocalCleanup
        # apply a shorter retention window (risk 5 mitigation, Phase 5B)
        # since these dirs hold no useful partial results.
        #
        # Local subprocesses orphaned by the restart are detected through
        # /proc and terminated BEFORE the job is finalised, so "failed" in
        # the UI/DB can never coexist with a still-computing ORCA process.
        # Remote jobs with a valid remote_job_id are recovered instead —
        # the poller re-checks bjobs + .exit_code.
        restart_marker = "[RESTART_FAILED] interrupted by server restart"
        # CANCELLING jobs interrupted mid-cancellation: local ones honour
        # the user's cancel intent immediately (no remote lifecycle to
        # confirm); remote ones are reconciled against LSF first — only a
        # confirmed-gone/finished classification publishes CANCELLED.
        for record in self.store.list(status=JobStatus.CANCELLING.value):
            if self._skip_recovery_for_peer(record):
                continue
            if self._is_remote_job(record):
                self._reconcile_cancelling_remote(record)
                logger.info(
                    "Reconciling remote CANCELLING job %s after restart (never a direct CANCELLED)",
                    record.id,
                )
                continue
            self._cleanup_local_orphans(record)
            if self._has_live_task_process(record):
                logger.warning(
                    "CANCELLING job %s still has live processes after stop sweep; "
                    "keeping CANCELLING for the next confirmation pass",
                    record.id,
                )
                continue
            self._finalize_restarted_job(
                record,
                JobStatus.CANCELLED,
                None,
                "job.cancelled",
                exit_code=130,
                reason="confirmed_local_stop",
            )
            logger.info("Marked CANCELLING job %s as CANCELLED after restart", record.id)

        # PAUSED jobs: local ones lost their owning server — terminate any
        # surviving frozen process group, then fail them with a resumption
        # hint matching the workflow's real resume support.  Remote ones
        # keep their bstop state on the LSF side: recover the polling state
        # and KEEP them PAUSED until an explicit unpause (bresume).
        paused_marker = "[RESTART_FAILED] paused job frozen at restart"
        for record in self.store.list(status=JobStatus.PAUSED.value):
            if self._try_recover_remote_job(record):
                logger.info(
                    "Recovered remote paused job %s (lsf=%s), kept PAUSED",
                    record.id,
                    record.remote_job_id,
                )
                continue
            if self._skip_recovery_for_peer(record):
                continue
            self._cleanup_local_orphans(record)
            self._finalize_restarted_job(
                record,
                JobStatus.FAILED,
                paused_marker + self._restart_resume_hint(record),
                "job.failed",
            )
            logger.info("Marked paused job %s as FAILED after restart", record.id)

        # WAITING_REVIEW jobs are intentionally excluded here: a server restart
        # must preserve their paused review state rather than marking them failed.
        for status in (JobStatus.RUNNING, JobStatus.STARTING, JobStatus.PENDING):
            for record in self.store.list(status=status.value):
                if _needs_submission_reconcile(record) or _has_pending_submission(record):
                    # D02: reconcile BEFORE recover/finalise so an
                    # indeterminate submission is never restart-failed.
                    self._reconcile_submission_record(record)
                    record = self.store.get(record.id)
                    if record is None or record.status.is_terminal:
                        continue
                    if _needs_submission_reconcile(record) or _has_pending_submission(record):
                        logger.info(
                            "Deferring unconfirmed submission for job %s to the reconcile loop",
                            record.id,
                        )
                        continue
                if self._try_recover_remote_job(record):
                    logger.info(
                        "Recovered remote job %s (lsf=%s) on restart, poller will resume",
                        record.id,
                        record.remote_job_id,
                    )
                    continue
                if self._skip_recovery_for_peer(record):
                    continue
                # Restart race guard: the workflow may have finished exactly
                # as the server went down — probe disk before failing (Q12).
                # Never accept completion while a live task process remains:
                # an alive worker is either a peer (skipped above) or an
                # orphan that the destructive-startup path terminates below.
                evidence: int | None = None
                if not self._is_remote_job(record) and not self._has_live_task_process(record):
                    evidence = self._local_attempt_completion_exit_code(record)
                if evidence == 0 or self._disk_shows_completed(
                    Path(record.work_dir), record.attempt
                ):
                    result = self._collect_result(record)
                    # Scope the reconcile retry to this startup finalization:
                    # the idempotent success side effects below mark it done.
                    result["terminal_side_effects_done"] = False
                    completed = self._cas_write(
                        record,
                        expected_status=record.status,
                        status=JobStatus.COMPLETED,
                        exit_code=0,
                        progress=1.0,
                        completed_at=_utc_now_iso(),
                        result=result,
                        decide=lambda fresh: None,
                    )
                    if completed is None:
                        continue
                    record = completed
                    self._stage_task_observer.finalize_job(record.id, JobStatus.COMPLETED.value)
                    # Emit job.completed + artifact capture exactly once,
                    # identical to the poll-path terminal side effects.
                    self._retry_terminal_side_effects(record)
                    logger.info("Marked interrupted job %s as COMPLETED (disk probe)", record.id)
                    continue
                self._cleanup_local_orphans(record)
                self._finalize_restarted_job(
                    record,
                    JobStatus.FAILED,
                    restart_marker + self._restart_resume_hint(record),
                    "job.failed",
                    exit_code=evidence,
                )
                logger.info("Marked interrupted job %s as FAILED", record.id)

        # Orphaned LSF ids from terminal-row submit races keep being
        # cancelled across restarts (bounded backoff, contract A r16 P1).
        for status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            for record in self.store.list(status=status.value, limit=10000):
                if self._pending_orphans(record.result or {}):
                    self._orphan_cancel_pass(record)

    def _restart_resume_hint(self, record: JobRecord) -> str:
        """Restart-failure hint matching the workflow's real resume support.

        BatchOptimize is never pointed at continue (its checkpoint is not a
        full resume contract); checkpoint-less workflows are pointed at
        rerun instead.
        """
        if record.spec.workflow in _STARTUP_RESUMABLE_WORKFLOWS:
            return " — 可尝试续算 (try continue)"
        if record.spec.workflow == "BatchOptimize" or self._read_generic_checkpoint(record) is None:
            return " — 请使用重算 (rerun)"
        return " — 可尝试续算 (try continue)"

    def _cleanup_local_orphans(self, record: JobRecord) -> list[int]:
        """Terminate local processes orphaned by the restart; log the sweep.

        Returns the terminated PIDs (empty for remote jobs, whose lifecycle
        is owned by LSF).
        """
        if self._is_remote_job(record):
            return []
        killed = self._terminate_stale_task_processes(record)
        if killed:
            try:
                self._event_log(record).append("process.cleaned", job_id=record.id, pids=killed)
                self._event_log(record).append(
                    "manager.recovered",
                    job_id=record.id,
                    recovered_by_pid=os.getpid(),
                    killed_pid_count=len(killed),
                    reason="startup-recovery",
                )
            except OSError:
                logger.debug("recovery event failed for %s", record.id, exc_info=True)
        return killed

    def _finalize_restarted_job(
        self,
        record: JobRecord,
        status: JobStatus,
        error: str | None,
        event_type: str,
        *,
        exit_code: int | None = None,
        reason: str = "server_restart",
    ) -> None:
        """Persist a restart-finalised state across DB, disk, and events.

        Clears the stale PID and refreshes the database row, task index,
        ``job.json``, on-disk ``task.json``, and ``events.jsonl`` so every
        surface reports the same terminal status.  ``exit_code`` is written
        only when the caller has positive evidence (e.g. a confirmed local
        cancel), never as a placeholder for "unknown".
        """
        fields: dict[str, Any] = {
            "pid": None,
            "error": error,
            "completed_at": _utc_now_iso(),
        }
        if exit_code is not None:
            fields["exit_code"] = exit_code
        final = self._cas_write(
            record,
            expected_status=record.status,
            status=status,
            decide=lambda fresh: None,
            **fields,
        )
        if final is None:
            return
        record = final
        self._stage_task_observer.finalize_job(record.id, status.value)
        self._sync_task_status(record)
        self._record_admission_usage(record)
        self._release_admission(record)
        try:
            self._write_job_json(record)
        except OSError:
            logger.debug("job.json refresh failed for %s", record.id, exc_info=True)
        self._update_task_json_status(Path(record.work_dir), status.value)
        try:
            runtime_file(Path(record.work_dir), "run.lock").unlink(missing_ok=True)
        except OSError:
            logger.debug("run.lock cleanup failed for %s", record.id, exc_info=True)
        try:
            self._event_log(record).append(
                event_type,
                job_id=record.id,
                error=error,
                reason=reason,
                attempt=record.attempt,
            )
        except OSError:
            logger.debug("Restart event failed for %s", record.id, exc_info=True)

    def _update_task_json_status(
        self, work_dir: Path, status: str, *, record: JobRecord | None = None
    ) -> None:
        """Best-effort status refresh of the on-disk ``task.json``."""
        path = TaskStorage(work_dir).task_json()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        payload["status"] = status
        payload["updated_at"] = _utc_now_iso()
        if record is not None:
            payload["task_dir_name"] = work_dir.name
            payload["display_name"] = work_dir.name
            payload["node_path"] = str(work_dir)
        try:
            path.write_text(
                json.dumps(payload, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
        except OSError:
            logger.debug("task.json refresh failed for %s", work_dir, exc_info=True)

    def _local_attempt_completion_exit_code(self, record: JobRecord) -> int | None:
        """Exit code proven by the *current* local attempt's durable receipts.

        A local worker that finished after its controller exited writes no
        ``.exit_code`` marker (that sentinel is wrapper/remote-only), so
        startup recovery must read the workflow receipt instead.  Evidence is
        accepted only for the row's current attempt:

        1. ``.exit_code`` / live ``state.json`` (delegated to the runner);
        2. the closed attempt's archived receipt under
           ``WORK/00_RUNTIME/attempts/<n>/`` (in-place rerun contract B);
        3. a ``RESULT/result_manifest.json`` declaring ``completed``.

        A receipt declaring a different attempt, or a still-running stage
        map, yields ``None`` (never a fabricated success).
        """
        evidence = self.runner.disk_completion_exit_code(record)
        if evidence is not None:
            return evidence

        archive = Path(record.work_dir) / "WORK" / "00_RUNTIME" / "attempts" / str(record.attempt)
        archived_marker = archive / ".exit_code"
        try:
            marker = archived_marker.read_text(encoding="utf-8").strip()
        except OSError:
            marker = ""
        if marker:
            try:
                return int(marker)
            except ValueError:
                logger.debug("Ignoring non-numeric archived .exit_code in %s", archive)

        state_paths: list[Path] = []
        root_state = archive / "state.json"
        if root_state.is_file():
            state_paths.append(root_state)
        else:
            state_paths.extend(
                sorted(archive.rglob("state.json"), key=lambda p: len(p.relative_to(archive).parts))
            )
        for state_path in state_paths:
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            declared = data.get("attempt")
            if isinstance(declared, int) and not isinstance(declared, bool):
                if declared != record.attempt:
                    continue
            status = data.get("status")
            if status == "completed":
                return 0
            if status == "failed":
                return 1
            stages = data.get("stages")
            if isinstance(stages, dict) and stages:
                if all(
                    isinstance(info, dict) and info.get("status") in ("completed", "skipped")
                    for info in stages.values()
                ):
                    return 0

        for manifest_path in (
            Path(record.work_dir) / "RESULT" / "result_manifest.json",
            archive / "RESULT" / "result_manifest.json",
        ):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict) and payload.get("status") == "completed":
                return 0
        return None

    def _disk_shows_completed(self, work_dir: Path, attempt: int | None = None) -> bool:
        """Probe disk for a job that finished exactly as the server died.

        True when the workflow ``state.json`` parses and shows every stage
        completed or skipped, and the ``.exit_code`` marker file holds
        ``0`` (the wrapper-script completion sentinel).  When *attempt* is
        given, a receipt declaring a different (older) attempt is rejected
        so a previous attempt's receipt never finalises this one.
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
        if attempt is not None:
            declared = data.get("attempt")
            if isinstance(declared, int) and declared != attempt:
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


__all__ = ["JobManager"]
