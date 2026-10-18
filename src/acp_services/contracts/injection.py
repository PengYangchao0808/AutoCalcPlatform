"""Transitional dependency-injection contracts for the ACP Services cut-over.

**Scope (read this first).** This module defines the *minimal* Protocols that
let the legacy ``acp`` application bootstrap hand its still-legacy
implementations to ``acp_services`` use cases. This phase declares **port
convergence only** (端口收敛). It does **not** claim that Services is
independent: independence is proven only after the per-port migration and
removal tasks (see ``tests/baseline/services-evidence/injection_registry.md``,
removal owner todo 77 / todo 78) actually move the implementations into this
package and delete the injection.

Rules enforced by ``tests/test_services_injection_contracts.py``:

* ``src/acp_services/**`` must never import, lazily import, type-check-import,
  string-reflect, ``importlib``-load, ``__import__`` or ``sys.modules``-look-up
  the ``acp`` package. These Protocols are the *only* seam.
* Every Protocol here is deliberately **partial**: it names exactly the
  methods/fields the consuming Services slice needs, never a mirror of a whole
  legacy class. Later todos implement against these shapes.
* The ports are grouped by ownership so the migration ledger can assign each
  one a receiving todo and a removal condition:

  - **platform** — job store, migrations, tasks/task_views, manager control,
    catalog/parameter parsing, remote paths/release/submission.
  - **science/planning** — plan execution, step requirements, result
    publication/recovery.
  - **transitional workspace** — the project read path needed by the first
    slice (the ``ProjectManager`` dependency-closure sample, todo 6).

These are *not* the frozen X2 first-slice public contracts
(``Principal``/``DataResourcePort``/... owned by todo 6+); they are the
assembly seam used while those contracts are still backed by legacy code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "JobStateAdvancePort",
    "JobStorePort",
    "MigrationsPort",
    "TaskIndexPort",
    "TaskViewQueryPort",
    "JobControlPort",
    "JobSubmissionPort",
    "JobEditPort",
    "JobRegistrationPort",
    "PlanExecutionPort",
    "StepRequirementPort",
    "ResultPublicationPort",
    "MethodConfigPort",
    "RemotePathsPort",
    "RemoteReleasePort",
    "RemoteSubmissionPort",
    "ProjectReadPort",
]


# ---------------------------------------------------------------------------
# Platform ports
# ---------------------------------------------------------------------------


@runtime_checkable
class JobStorePort(Protocol):
    """Job persistence surface needed by the dependency closure.

    Minimal shape: the on-disk database location plus a schema-initialisation
    entry point. Later Runtime/store todos widen this against the CAS API
    (``acp/scheduler/store.py``) without re-interview.
    """

    @property
    def db_path(self) -> Path:
        """Path of the SQLite database backing the job store."""
        ...

    def initialize_schema(self) -> None:
        """Create/migrate the schema so the database is safe to read."""
        ...


@runtime_checkable
class JobStateAdvancePort(Protocol):
    """CAS job-state advance seam used by the Runtime controller (B06-2).

    The controller (:func:`acp_services.runtime.receipts.advance_job_state`)
    calls :meth:`advance` **only** after a receipt has passed
    :func:`acp_services.runtime.receipts.verify_receipt`; it is the *only*
    state-write the controller performs, and it is a seam onto the existing CAS
    API (``JobStore.transition``, ``src/acp/scheduler/store.py:194-367``) with
    its revision/status/attempt guards — **not** a second state machine. A
    stale writer (old revision/status/attempt) is rejected by the implementation
    and must surface as a conflict; :meth:`advance` never blind-overwrites.

    The command surface is deliberately minimal — it mirrors the guarded CAS
    transition: ``expected_revision`` + optional ``expected_status`` /
    ``expected_attempt`` plus the whitelisted ``fields`` to write. The injected
    adapter is removed by todo 77 when the store moves into Services.
    """

    def advance(
        self,
        job_id: str,
        *,
        expected_revision: int,
        expected_status: Any = None,
        expected_attempt: int | None = None,
        **fields: Any,
    ) -> Any:
        """CAS-advance one job row; raise on a stale expectation."""
        ...


@runtime_checkable
class MigrationsPort(Protocol):
    """Versioned schema migration entry (``acp/scheduler/migrations.migrate``)."""

    def migrate(self, db_path: Path | str) -> int:
        """Apply all pending migrations to *db_path* and return the count."""
        ...


@runtime_checkable
class TaskIndexPort(Protocol):
    """Task index read + sync surface (``acp/scheduler/tasks.TaskIndex``)."""

    def sync_from_job(self, record: Any, layout_version: int = 2) -> None:
        """Mirror one persisted job into the task index."""
        ...

    def get(self, task_id: str) -> dict[str, Any] | None:
        """Return one task projection, or ``None`` when absent."""
        ...

    def list_by_project(self, project_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """Return the task projections of one project."""
        ...


@runtime_checkable
class TaskViewQueryPort(Protocol):
    """Read-only task view query entry (``acp/scheduler/task_views``)."""

    def query_project_tasks(self, index: Any, query: Any) -> dict[str, Any]:
        """Group/filter/sort/facet tasks over *index* for *query*."""
        ...


@runtime_checkable
class JobControlPort(Protocol):
    """Control-intent entry points (``acp/scheduler/manager.JobManager``).

    Only user-control intents are exposed here — no ``submit``. The Runtime
    todos (25—26) migrate these onto one execution owner; the injected adapter
    is removed by todo 77.
    """

    def pause_job(self, job_id: str) -> Any:
        """Pause a running job (SIGSTOP locally, ``bstop`` remotely)."""
        ...

    def unpause_job(self, job_id: str) -> Any:
        """Resume a paused job (SIGCONT locally, ``bresume`` remotely)."""
        ...

    def cancel(self, job_id: str) -> Any | None:
        """Request cancellation; terminal records are returned unchanged."""
        ...

    def continue_job(self, job_id: str, *, target_node: str | None = None) -> Any:
        """Re-enter a FAILED/CANCELLED job from its checkpoint."""
        ...

    def rerun_job(self, job_id: str, project_id: str | None = None) -> Any | None:
        """Re-run a terminal job in place (same directory, attempt + 1)."""
        ...

    def purge_jobs(
        self,
        job_ids: list[str] | None = None,
        status: str | None = None,
        project_id: str | None = None,
        older_than_days: float | None = None,
        delete_data: bool = False,
        force_cancel: bool = False,
    ) -> list[dict[str, Any]]:
        """Cascade-purge a job set, returning a per-job report."""
        ...


@runtime_checkable
class JobSubmissionPort(Protocol):
    """New-job submission entry (``acp/scheduler/manager.JobManager.submit``).

    The Runtime submit use case (:class:`acp_services.runtime.commands.
    RuntimeCommands`) is the single entry; the injected adapter delegates to the
    legacy manager until todo 77 moves store/control into Services. This is a
    port, not a second state machine: :meth:`submit` is called exactly once and
    its return value is passed through untouched.
    """

    def submit(self, spec: Any, *, group_id: str | None = None) -> Any:
        """Validate, persist and enqueue *spec*; return the created record."""
        ...


@runtime_checkable
class JobEditPort(Protocol):
    """Edit-recalculate entry (``manager.edit_recalculate``).

    Exposes the same keyword-only operation contract the legacy manager
    implements (in-place attempt or new job, exactly-once via ``request_id``).
    Injected by the application composition root; removed by todo 77.
    """

    def edit_recalculate(
        self,
        job_id: str,
        *,
        mode: str,
        new_spec: Any,
        expected_source_revision: str | None,
        request_id: str,
        payload_hash: str,
        payload_json: str,
        diff_summary: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Apply the edited spec and return the operation result."""
        ...


@runtime_checkable
class JobRegistrationPort(Protocol):
    """CLI completed-job registration entry (store-layer, no JobManager).

    The CLI ``--register`` submit entry persists a finished local run directory
    as a COMPLETED job. The Runtime registration use case forwards to the
    existing implementation; the injected adapter is removed by todo 77.
    """

    def register_completed_job(self, **kwargs: Any) -> Any:
        """Persist a finished CLI run directory as a COMPLETED job."""
        ...


@runtime_checkable
class MethodConfigPort(Protocol):
    """Catalog/parameter parsing (``acp/catalog`` + CLI flag conversion).

    The two case-folding choke points required by ANTI #25: method-config
    normalization and argparse string-choice normalization.
    """

    def normalize_and_validate_method_config(
        self,
        method: dict[str, Any],
        schema: dict[str, Any],
        warnings_out: list[str] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Return ``(normalized_method, errors)`` for *method* under *schema*."""
        ...

    def normalize_string_choices(self, parser: Any) -> None:
        """Install case-folding ``type=`` wrappers on a parser's choices."""
        ...


@runtime_checkable
class RemotePathsPort(Protocol):
    """Remote storage/dir identity allocation (``acp/scheduler/remote/paths``)."""

    def storage_relative_path(self, record: Any, run_root: str | Path) -> str:
        """Return *record.work_dir* relative to *run_root* (POSIX form)."""
        ...

    def compose_remote_dir(self, relative: str, node: Any) -> str:
        """Join *relative* under the node's ``remote_work_dir``."""
        ...

    def resolve_remote_dir(
        self,
        record: Any,
        node: Any,
        *,
        explicit: str | None = None,
        probe: Any = None,
        run_root: str | Path | None = None,
    ) -> tuple[str, bool]:
        """Resolve the remote job directory (legacy-acceptance probe chain)."""
        ...


@runtime_checkable
class RemoteReleasePort(Protocol):
    """Content-addressed release ensure/verify/GC (``remote/release``)."""

    def ensure_node_release(
        self,
        node: Any,
        manifest: Any,
        *,
        stager: Any,
        ssh: Any,
        state_dir: Path | str,
        force: bool = False,
        project_root: Path | str | None = None,
        timeout: float = ...,
    ) -> Any:
        """Publish (verify + pin) *manifest* on *node*; return the binding."""
        ...

    def verify_existing_release(
        self,
        node: Any,
        release_id: str,
        *,
        stager: Any,
        ssh: Any,
        timeout: float = ...,
    ) -> Any:
        """Verify an already-published release without uploading."""
        ...

    def prune_releases(
        self,
        node: Any,
        *,
        stager: Any,
        ssh: Any,
        job_store: Any,
        retention_days: int,
        min_age_hours: int = 24,
        dry_run: bool = False,
        timeout: float = ...,
    ) -> Any:
        """Reclaim unreferenced, aged-out releases from *node*."""
        ...


@runtime_checkable
class RemoteSubmissionPort(Protocol):
    """Submission id/intent/lease (``acp/scheduler/remote/submission``)."""

    def submission_id_for(self, job_id: str, attempt: int | None) -> str:
        """Deterministic ``sub_`` + digest submission id."""
        ...

    def submission_lsf_name(self, submission_id: str) -> str:
        """LSF ``#BSUB -J`` name for *submission_id*."""
        ...

    def lease_ttl_seconds(self, config: Any = None) -> int:
        """Lease TTL derived from the remote config timeouts."""
        ...

    def submit_lease_valid(self, remote_meta: Any, *, ttl_seconds: int | None = None) -> bool:
        """``True`` while the submission right for this intent is still held."""
        ...


# ---------------------------------------------------------------------------
# Science / planning ports
# ---------------------------------------------------------------------------


@runtime_checkable
class PlanExecutionPort(Protocol):
    """Frozen-plan execution + resume (``calculations/executor``)."""

    def execute(
        self,
        plan: Any,
        task_root: Path,
        *,
        plan_fingerprint: str | None = None,
    ) -> Any:
        """Execute *plan* under *task_root* with checkpoint/resume."""
        ...


@runtime_checkable
class StepRequirementPort(Protocol):
    """Step prerequisite gate (``calculations/step_requirements``)."""

    def evaluate_prerequisite(
        self,
        prior: Any,
        kind: Any,
        item_symbols: Any,
    ) -> Any:
        """Return the ``RequirementOutcome`` for *kind* against *prior*."""
        ...


@runtime_checkable
class ResultPublicationPort(Protocol):
    """Result publication + recovery read (``calculations/result_publication``)."""

    def register_result_manifest(self, result_dir: Path | str, manifest: Any) -> Path:
        """Register/refresh the result manifest under *result_dir*."""
        ...

    def load_scientific_result(self, result_dir: Path | str) -> Any | None:
        """Load the per-step scientific record used for adoption/resume."""
        ...


# ---------------------------------------------------------------------------
# Transitional workspace port (first slice / ProjectManager closure)
# ---------------------------------------------------------------------------


@runtime_checkable
class ProjectReadPort(Protocol):
    """Project read path injected for the todo 72 first slice.

    ``acp/scheduler/projects.py`` (line 15—16 imports, line 50 constructor,
    line 56 ``migrate`` call) is the dependency-closure sample. todo 6 moves
    ``ProjectManager`` into ``acp_services.workspace``; that migration removes
    this injection.
    """

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        """Return one project record, or ``None`` when absent."""
        ...

    def list_projects(self) -> list[dict[str, Any]]:
        """Return all project records (updated_at desc, created_at desc)."""
        ...

    def ensure_default_project(self) -> str:
        """Return the id of the default project, creating it when missing."""
        ...
