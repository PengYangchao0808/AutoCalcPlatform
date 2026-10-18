"""Explicit injection mechanism for the transitional Services assembly.

The legacy ``acp`` application bootstrap (``acp.services_bootstrap``) builds a
frozen :class:`ServicesPorts` and hands it to Services use cases. There is no
global mutable singleton and no dynamic lookup: a Services use case receives
the context explicitly and may only touch the Protocols it declares.

**This phase is "port convergence" (端口收敛).** Services independence is *not*
claimed here — it is proven only once the per-port migration tasks move the
implementations into ``acp_services`` and remove the injection (todo 77 /
todo 78; see ``tests/baseline/services-evidence/injection_registry.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from acp_services.contracts.injection import (
    JobControlPort,
    JobStorePort,
    MethodConfigPort,
    MigrationsPort,
    PlanExecutionPort,
    ProjectReadPort,
    RemotePathsPort,
    RemoteReleasePort,
    RemoteSubmissionPort,
    ResultPublicationPort,
    StepRequirementPort,
    TaskIndexPort,
    TaskViewQueryPort,
)

__all__ = ["ServicesPorts", "open_project_read_path"]


@dataclass(frozen=True)
class ServicesPorts:
    """Frozen bundle of legacy implementations injected behind Services ports.

    Equality is structural; use :func:`dataclasses.replace` to swap a single
    port (e.g. a test spy) without mutating a live context. Every field is a
    transitional seam with a registered migration/removal task.
    """

    job_store: JobStorePort
    migrations: MigrationsPort
    tasks: TaskIndexPort
    task_views: TaskViewQueryPort
    control: JobControlPort
    planning: PlanExecutionPort
    step_requirements: StepRequirementPort
    publication: ResultPublicationPort
    catalog: MethodConfigPort
    remote_paths: RemotePathsPort
    remote_release: RemoteReleasePort
    remote_submission: RemoteSubmissionPort
    projects: ProjectReadPort


def open_project_read_path(ports: ServicesPorts) -> list[dict[str, Any]]:
    """First slice (todo 72): read the project directory through injected ports.

    Exercises the store+schema closure of ``ProjectManager`` (the dependency
    sample): initialise the schema via the job-store port, run the versioned
    migration port, then ensure + list projects via the injected project read
    port. The returned list is the real legacy result — no re-implementation.
    """
    ports.job_store.initialize_schema()
    ports.migrations.migrate(ports.job_store.db_path)
    default_project_id = ports.projects.ensure_default_project()
    listing = ports.projects.list_projects()
    if not any(row.get("project_id") == default_project_id for row in listing):
        raise RuntimeError(
            "injected project read port returned a listing without the default "
            f"project {default_project_id!r}"
        )
    return listing
