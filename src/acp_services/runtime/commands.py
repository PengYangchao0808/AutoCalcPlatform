"""Runtime command use cases — one execution owner for job submit/control (B05-1).

This module is the Runtime *use-case* seam introduced by todo 25. The seven
commands — submit / pause / unpause / cancel / continue / rerun / edit (plus the
batch ``purge`` and the CLI completed-job ``register`` submit entry) — are
expressed here as one owner and forwarded through **explicit injected ports** to
the still-legacy ``acp.scheduler.manager.JobManager`` and
``acp.scheduler.registration.register_completed_cli_job``.

The legacy application composition root (``acp.services_bootstrap``) builds the
adapters and injects them; this package never imports, lazily imports, string
reflects or dynamically looks up the legacy implementation (see
``tests/test_services_injection_contracts.py`` and
``tests/test_acp_services_isolation.py``).

**Phase discipline — port convergence only (端口收敛).** This is *not* a second
state machine and *not* a second execution owner:

* every method forwards exactly once and returns the port's result untouched;
* no method writes job state, opens the jobs database, touches a store, or
  re-implements any legacy control logic;
* the single owner of control intent remains the manager until the migration
  tasks move the implementation into ``acp_services`` and delete the injection
  (todo 77). See ``tests/baseline/services-evidence/injection_registry.md`` and
  the single-owner statement in ``src/acp/scheduler/AGENTS.md`` STATE TRANSITIONS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from acp_services.contracts.injection import (
    JobControlPort,
    JobEditPort,
    JobRegistrationPort,
    JobSubmissionPort,
)

__all__ = ["RuntimeCommands"]


@dataclass(frozen=True)
class RuntimeCommands:
    """Injected-port command surface — the single Runtime entry for control.

    A frozen bundle of the four transactional ports the Runtime use cases need.
    Each method is a use case: it calls exactly one port method (the single
    execution owner) and returns that call's result unmodified. Tests assert the
    one-call forwarding chain per command; the composition root supplies adapters
    over the live legacy manager.
    """

    control: JobControlPort
    submission: JobSubmissionPort
    edit: JobEditPort
    registration: JobRegistrationPort

    # -- submit ------------------------------------------------------------
    def submit(
        self,
        spec: Any,
        *,
        group_id: str | None = None,
        idempotency: Any | None = None,
    ) -> Any:
        """Persist and enqueue *spec* through the injected submission port.

        ``idempotency`` is forwarded untouched (the port owns the persistent
        claim/finalize protocol); legacy callers omit it.
        """
        if idempotency is None:
            return self.submission.submit(spec, group_id=group_id)
        return self.submission.submit(spec, group_id=group_id, idempotency=idempotency)

    # -- control intents ---------------------------------------------------
    def pause(self, job_id: str) -> Any:
        """Pause a running job (SIGSTOP locally, ``bstop`` remotely)."""
        return self.control.pause_job(job_id)

    def unpause(self, job_id: str) -> Any:
        """Revive a paused job in place (SIGCONT locally, ``bresume`` remotely)."""
        return self.control.unpause_job(job_id)

    def cancel(self, job_id: str) -> Any:
        """Request cancellation; terminal records return unchanged."""
        return self.control.cancel(job_id)

    def continue_job(self, job_id: str, *, target_node: str | None = None) -> Any:
        """Re-enter a FAILED/CANCELLED job from its checkpoint."""
        return self.control.continue_job(job_id, target_node=target_node)

    def rerun(self, job_id: str, *, project_id: str | None = None) -> Any:
        """Re-run a terminal job in place (same directory, attempt + 1)."""
        return self.control.rerun_job(job_id, project_id=project_id)

    def purge(self, **kwargs: Any) -> Any:
        """Cascade-purge a job set through the injected control port."""
        return self.control.purge_jobs(**kwargs)

    # -- edit --------------------------------------------------------------
    def edit_recalculate(self, job_id: str, **kwargs: Any) -> Any:
        """Apply an edited spec through the injected edit port."""
        return self.edit.edit_recalculate(job_id, **kwargs)

    # -- CLI completed-job registration ------------------------------------
    def register_completed_job(self, **kwargs: Any) -> Any:
        """Persist a finished CLI run directory through the injection port."""
        return self.registration.register_completed_job(**kwargs)
