"""Legacy application bootstrap — the ONE place allowed to construct old impls.

This module is the *composition root* for the transitional Services cut-over
(todo 72). It builds the real legacy ``acp`` instances and wires them into a
frozen :class:`acp_services.bootstrap.ServicesPorts`. ``acp_services`` code
never imports or dynamically looks up these implementations; it only receives
this context object.

Importing this module must **not** start the scheduler/poller: the
``JobManager`` (which claims the run root and spawns background threads) is
only attached when the caller passes an already-built ``manager``. Without it
the control port is an unattached placeholder that fails loudly on use.

Every injected implementation is registered with a migration task and a
removal condition in ``tests/baseline/services-evidence/injection_registry.md``.
This phase is port convergence only — Services independence is proven after
the removal tasks (todo 77 / todo 78).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from acp.calculations.executor import CalculationPlanExecutor
from acp.calculations.result_publication import (
    load_scientific_result,
    register_result_manifest,
)
from acp.catalog import normalize_and_validate_method_config
from acp.scheduler.projects import ProjectManager
from acp.scheduler.remote import paths as remote_paths
from acp.scheduler.remote import release as remote_release
from acp.scheduler.remote import submission as remote_submission
from acp.scheduler.task_views import query_project_tasks
from acp.scheduler.tasks import TaskIndex
from acp.services_completion_bootstrap import JobStoreAdvancePort
from acp.services_publication_bootstrap import build_publication_data_port
from acp_services.bootstrap import ServicesPorts
from acp_services.runtime.commands import RuntimeCommands
from acp_services.runtime.recovery import RuntimeRecovery

__all__ = [
    "JobStoreAdvancePort",
    "build_runtime_commands",
    "build_runtime_recovery",
    "build_services_ports",
]


class _JobControlAdapter:
    """Delegate :class:`JobControlPort` to a live ``JobManager`` instance."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def pause_job(self, job_id: str) -> Any:
        return self._manager.pause_job(job_id)

    def unpause_job(self, job_id: str) -> Any:
        return self._manager.unpause_job(job_id)

    def cancel(self, job_id: str) -> Any | None:
        return self._manager.cancel(job_id)

    def continue_job(self, job_id: str, *, target_node: str | None = None) -> Any:
        return self._manager.continue_job(job_id, target_node=target_node)

    def rerun_job(self, job_id: str, project_id: str | None = None) -> Any | None:
        return self._manager.rerun_job(job_id, project_id)

    def purge_jobs(
        self,
        job_ids: list[str] | None = None,
        status: str | None = None,
        project_id: str | None = None,
        older_than_days: float | None = None,
        delete_data: bool = False,
        force_cancel: bool = False,
    ) -> list[dict[str, Any]]:
        return self._manager.purge_jobs(
            job_ids,
            status,
            project_id,
            older_than_days,
            delete_data,
            force_cancel,
        )


class _UnattachedJobControl:
    """Control port placeholder used until the app attaches its live manager."""

    _MESSAGE = (
        "no JobManager attached to the Services control port; call "
        "build_services_ports(..., manager=job_manager) from the application "
        "bootstrap (this module must not start the scheduler/poller itself)"
    )

    def _unattached(self) -> Any:
        raise RuntimeError(self._MESSAGE)

    def pause_job(self, job_id: str) -> Any:
        return self._unattached()

    def unpause_job(self, job_id: str) -> Any:
        return self._unattached()

    def cancel(self, job_id: str) -> Any | None:
        return self._unattached()

    def continue_job(self, job_id: str, *, target_node: str | None = None) -> Any:
        return self._unattached()

    def rerun_job(self, job_id: str, project_id: str | None = None) -> Any | None:
        return self._unattached()

    def purge_jobs(
        self,
        job_ids: list[str] | None = None,
        status: str | None = None,
        project_id: str | None = None,
        older_than_days: float | None = None,
        delete_data: bool = False,
        force_cancel: bool = False,
    ) -> list[dict[str, Any]]:
        return self._unattached()


def _unattached_message() -> str:
    return (
        "no JobManager attached to the Services runtime command surface; call "
        "build_runtime_commands(manager=job_manager) from the application "
        "bootstrap (this module must not start the scheduler/poller itself)"
    )


class _JobSubmissionAdapter:
    """Delegate :class:`JobSubmissionPort` to a live ``JobManager`` instance."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def submit(
        self,
        spec: Any,
        *,
        group_id: str | None = None,
        idempotency: Any | None = None,
        budget: Any | None = None,
        identity: Any | None = None,
    ) -> Any:
        if identity is not None:
            return self._manager.submit(
                spec, group_id, idempotency=idempotency, budget=budget, identity=identity
            )
        if budget is None:
            if idempotency is None:
                return self._manager.submit(spec, group_id)
            return self._manager.submit(spec, group_id, idempotency=idempotency)
        return self._manager.submit(spec, group_id, idempotency=idempotency, budget=budget)


class _JobEditAdapter:
    """Delegate :class:`JobEditPort` to a live ``JobManager`` instance."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def edit_recalculate(self, job_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._manager.edit_recalculate(job_id, **kwargs)


class _JobRegistrationAdapter:
    """Delegate :class:`JobRegistrationPort` to the store-layer registration API.

    Completed CLI runs are registered without constructing a ``JobManager``
    (single-instance lock); the adapter imports the legacy implementation lazily
    so this module stays light and the symbol can be patched in tests.
    """

    def register_completed_job(self, **kwargs: Any) -> Any:
        from acp.scheduler.registration import register_completed_cli_job

        return register_completed_cli_job(**kwargs)


class _UnattachedJobSubmission:
    """Submission port placeholder used until the app attaches its live manager."""

    def submit(
        self,
        spec: Any,
        *,
        group_id: str | None = None,
        idempotency: Any | None = None,
        budget: Any | None = None,
        identity: Any | None = None,
    ) -> Any:
        del idempotency, budget, identity
        raise RuntimeError(_unattached_message())


class _UnattachedJobEdit:
    """Edit port placeholder used until the app attaches its live manager."""

    def edit_recalculate(self, job_id: str, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(_unattached_message())


def build_services_ports(
    run_root: str | Path,
    *,
    db_path: str | Path | None = None,
    manager: Any | None = None,
    task_index: TaskIndex | None = None,
) -> ServicesPorts:
    """Build the transitional :class:`ServicesPorts` from legacy instances.

    Args:
        run_root: Task data root; the project read path is rooted here.
        db_path: SQLite path; defaults to ``<run_root>/acp_jobs.db``.
        manager: Live ``JobManager`` to expose via the control port. When
            omitted, the scheduler is **not** started and the control port
            fails loudly if used.
        task_index: Pre-built ``TaskIndex`` (created when omitted).

    Returns:
        A frozen :class:`ServicesPorts` wired to the real legacy objects.

    The store/schema closure (``JobStore`` / migrations) is Services-native
    since B05-7 and is therefore **not** injected: ``ProjectManager`` owns its
    own schema initialisation through ``acp_services.store``.
    """
    run_root_path = Path(run_root)
    resolved_db_path = Path(db_path) if db_path is not None else run_root_path / "acp_jobs.db"
    index = task_index or TaskIndex(resolved_db_path)
    projects = ProjectManager(None, run_root_path, db_path=resolved_db_path)
    control = _JobControlAdapter(manager) if manager is not None else _UnattachedJobControl()

    # CLI flag conversion is imported lazily so importing this module stays
    # light and never pulls the argparse surface unless wiring is requested.
    from acp.cli import _normalize_string_choices

    catalog_port = SimpleNamespace(
        normalize_and_validate_method_config=normalize_and_validate_method_config,
        normalize_string_choices=_normalize_string_choices,
    )

    return ServicesPorts(
        tasks=index,
        task_views=SimpleNamespace(query_project_tasks=query_project_tasks),
        control=control,
        planning=CalculationPlanExecutor(publication=build_publication_data_port()),
        publication=SimpleNamespace(
            register_result_manifest=register_result_manifest,
            load_scientific_result=load_scientific_result,
        ),
        catalog=catalog_port,
        remote_paths=SimpleNamespace(
            storage_relative_path=remote_paths.storage_relative_path,
            compose_remote_dir=remote_paths.compose_remote_dir,
            resolve_remote_dir=remote_paths.resolve_remote_dir,
        ),
        remote_release=SimpleNamespace(
            ensure_node_release=remote_release.ensure_node_release,
            verify_existing_release=remote_release.verify_existing_release,
            prune_releases=remote_release.prune_releases,
        ),
        remote_submission=SimpleNamespace(
            submission_id_for=remote_submission.submission_id_for,
            submission_lsf_name=remote_submission.submission_lsf_name,
            lease_ttl_seconds=remote_submission.lease_ttl_seconds,
            submit_lease_valid=remote_submission.submit_lease_valid,
        ),
        projects=projects,
    )


def build_runtime_recovery(
    run_root: str | Path,
    *,
    db_path: str | Path | None = None,
) -> RuntimeRecovery:
    """Build the Runtime planning/recovery seam over the injected legacy ports.

    Exposes the single planning/recovery owner (todo 27) so the CLI ``recover``
    entry reuses the same two-layer identity / ``resume_source`` /
    publication-retry semantics the Web API reaches — never a second executor.
    """
    ports = build_services_ports(run_root, db_path=db_path)
    return RuntimeRecovery(planning=ports.planning, publication=ports.publication)


def build_runtime_commands(manager: Any | None = None) -> RuntimeCommands:
    """Build the Runtime command surface (the single submit/control owner).

    This is the *only* place the legacy ``JobManager`` is adapted onto the
    Runtime use-case ports (todo 25). API and CLI submit/control entries call
    :class:`~acp_services.runtime.commands.RuntimeCommands`; they never reach the
    manager or the store directly.

    Args:
        manager: Live ``JobManager`` to adapt. When ``None`` the control,
            submission and edit ports are unattached placeholders that fail
            loudly, while the CLI completed-job registration port stays wired
            (it needs no manager and must not start the scheduler).

    Returns:
        A frozen :class:`RuntimeCommands` bound to the injected adapters.
    """
    if manager is not None:
        control: Any = _JobControlAdapter(manager)
        submission: Any = _JobSubmissionAdapter(manager)
        edit: Any = _JobEditAdapter(manager)
    else:
        control = _UnattachedJobControl()
        submission = _UnattachedJobSubmission()
        edit = _UnattachedJobEdit()
    return RuntimeCommands(
        control=control,
        submission=submission,
        edit=edit,
        registration=_JobRegistrationAdapter(),
    )
