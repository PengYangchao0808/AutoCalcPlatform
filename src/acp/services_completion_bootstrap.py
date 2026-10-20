"""ACP-side composition for the attempt-completion use case (07-B / task 26).

``acp_services`` owns the completion decision (:mod:`acp_services.runtime.
attempt_completion`), but it must never import the legacy ``acp`` package.  This
module is the ACP-side composition seam that wraps the existing store APIs:

* :class:`JobStoreAdvancePort` adapts ``JobStore.transition`` (revision/status/
  attempt CAS) onto
  :class:`acp_services.contracts.injection.JobStateAdvancePort`.  It is a thin
  forwarder — no second state machine and no direct SQL UPDATE.
* :class:`FrozenGroupSnapshotReader` resolves the current attempt snapshot from
  the persisted job row + the frozen envelope group under
  ``WORK/00_RUNTIME/frozen``.  Release/owner/fencing are copied from the frozen
  envelope, never recomputed.
* :func:`build_attempt_completion_service` wires those two adapters into
  :class:`acp_services.runtime.attempt_completion.AttemptCompletionService`.

Task 27 attaches the service to the manager terminal path; this module only
provides the composition surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from acp_services.runtime.attempt_completion import (
    AttemptCompletionService,
    AttemptPublicationPort,
    AttemptSnapshot,
    AttemptSnapshotReader,
)
from acp_services.runtime.execution_chain import FrozenGroupError, load_frozen_group
from acp_services.worker_bootstrap import load_frozen_envelope

__all__ = [
    "FrozenGroupSnapshotReader",
    "JobStoreAdvancePort",
    "build_attempt_completion_service",
    "build_job_state_advance_port",
]


class JobStoreAdvancePort:
    """``JobStateAdvancePort`` over the existing ``JobStore.transition`` CAS.

    Every call is the store's guarded single-row transition: a stale
    ``expected_revision`` / ``expected_status`` / ``expected_attempt`` raises
    :class:`acp_services.store.store.JobStateConflictError`, so this adapter can
    never blind-overwrite a row.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    def advance(
        self,
        job_id: str,
        *,
        expected_revision: int,
        expected_status: Any = None,
        expected_attempt: int | None = None,
        **fields: Any,
    ) -> Any:
        return self._store.transition(
            job_id,
            expected_status=expected_status,
            expected_revision=expected_revision,
            expected_attempt=expected_attempt,
            **fields,
        )


class FrozenGroupSnapshotReader:
    """Read the current attempt snapshot from the job row + frozen group."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def read(self, job_id: str) -> AttemptSnapshot | None:
        record = self._store.get(job_id)
        if record is None:
            return None
        work_dir = Path(getattr(record, "work_dir", "") or "")
        if not work_dir:
            return None
        try:
            group = load_frozen_group(work_dir)
        except FrozenGroupError:
            return None
        envelope = load_frozen_envelope(group.envelope_path)
        return AttemptSnapshot.from_record(record, envelope)


def build_job_state_advance_port(store: Any) -> JobStoreAdvancePort:
    """Build the CAS adapter over *store* (``JobStore.transition``)."""
    return JobStoreAdvancePort(store)


def build_attempt_completion_service(
    store: Any,
    *,
    publication: AttemptPublicationPort | None = None,
    snapshots: AttemptSnapshotReader | None = None,
) -> AttemptCompletionService:
    """Wire the completion service over the existing store + frozen groups."""
    return AttemptCompletionService(
        advances=JobStoreAdvancePort(store),
        publication=publication,
        snapshots=snapshots if snapshots is not None else FrozenGroupSnapshotReader(store),
    )
