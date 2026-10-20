"""Controller-side attempt completion: receipt gate, terminal CAS, publication handoff.

B07-A / task 26 (07-B).  This module is the single Runtime use case that turns
one worker receipt into one terminal job transition and then hands the durable
result to the publication port:

* :class:`AttemptSnapshot` is the controller's frozen view of the *current*
  attempt.  ``release_id``/``release_digest``/``owner``/``fencing_token`` are
  copied from the frozen :class:`~acp_services.contracts.envelope.ExecutionEnvelope`
  (never recomputed); ``revision``/``status`` come from the persisted job row.
* :class:`AttemptCompletionService.complete` reads the receipt, runs
  :func:`~acp_services.runtime.receipts.verify_receipt`, and only then calls
  :func:`~acp_services.runtime.receipts.advance_job_state` once with the
  snapshot's ``expected_revision``/``expected_status``/``expected_attempt``.
  The same CAS writes ``terminal_side_effects_done=False`` and
  ``publication_pending=True`` into ``result`` (the manager's durable
  pending-publication marker), so a crash after the CAS but before publication
  is resumed on reconcile without a second QC run.
* Two idempotency rules stay separate: an already-terminal row with publication
  pending resumes publication; an already-published terminal row is a no-op.
* A stale/foreign receipt is a structured rejection: **zero CAS**, current job
  unchanged.  A current-attempt receipt that is missing/corrupt after the
  execution has confirmed exited is ``current_corrupt_after_exit`` — a typed
  failure fact for the controller (task 27) to write as exactly one CAS failure.
* Three execution states are explicit: a valid receipt (CAS/publication),
  a held claim (wait/diagnostic, no authoritative receipt write) and a lost
  holder (process/children must be verified stopped before any retry).

No second state machine or job writer is introduced: the only state write is
the injected :class:`~acp_services.contracts.injection.JobStateAdvancePort`
(``JobStore.transition`` CAS).  The worker still never touches the store.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from acp_services.contracts.envelope import (
    EnvelopeError,
    ExecutionEnvelope,
    ExecutionError,
    ExecutionReceipt,
)
from acp_services.contracts.injection import JobStateAdvancePort
from acp_services.runtime.helpers import utc_now_iso
from acp_services.runtime.receipts import (
    ReceiptRejectedError,
    ReceiptVerdict,
    advance_job_state,
    verify_receipt,
)
from acp_services.runtime.worker import (
    ReceiptConflictError,
    job_status_for_receipt,
    read_receipt,
    receipt_exit_code,
)
from acp_services.store.store import JobStateConflictError

__all__ = [
    "ACCEPTED_PENDING_PUBLICATION",
    "ALREADY_PUBLISHED",
    "ATTEMPT_COMPLETION_SCHEMA",
    "CLAIM_HELD",
    "CURRENT_CORRUPT_AFTER_EXIT",
    "HOLDER_LOST",
    "PUBLICATION_FAILED",
    "PUBLICATION_PENDING",
    "RECEIPT_PENDING",
    "SUPERSEDED",
    "AttemptCompletionError",
    "AttemptCompletionService",
    "AttemptCompletionVerdict",
    "AttemptExecutionState",
    "AttemptPublicationPort",
    "AttemptSnapshot",
    "AttemptSnapshotReader",
]

ATTEMPT_COMPLETION_SCHEMA: str = "attempt_completion_v1"

#: Receipt/failure verdict codes.
CURRENT_CORRUPT_AFTER_EXIT: str = "current_corrupt_after_exit"
CLAIM_HELD: str = "claim_held"
HOLDER_LOST: str = "holder_lost"
RECEIPT_PENDING: str = "receipt_pending"
SUPERSEDED: str = "superseded"
PUBLICATION_PENDING: str = "publication_pending"
PUBLICATION_FAILED: str = "publication_failed"
ACCEPTED_PENDING_PUBLICATION: str = "accepted_pending_publication"
ALREADY_PUBLISHED: str = "already_published"

_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})


class AttemptCompletionError(Exception):
    """The completion use case cannot be evaluated (invalid snapshot/envelope)."""


class AttemptExecutionState(str, Enum):
    """The three execution-right states the controller may observe."""

    VALID_RECEIPT = "valid_receipt"
    """A valid receipt is available and may be CAS-advanced/published once."""

    CLAIM_HELD = "claim_held"
    """The attempt claim is currently held: wait/diagnostic, never a failure fact."""

    HOLDER_LOST = "holder_lost"
    """This worker lost the right; verify the old process/children stopped first."""


@runtime_checkable
class AttemptPublicationPort(Protocol):
    """Publication handoff for one completed attempt (task 28 implements it)."""

    def publish(self, snapshot: AttemptSnapshot) -> Any:
        """Publish the durable result; raise on a retryable publication failure."""
        ...

    def is_published(self, snapshot: AttemptSnapshot) -> bool:
        """True when the durable publication fact already exists."""
        ...


@runtime_checkable
class AttemptSnapshotReader(Protocol):
    """Latest persisted snapshot of one job (used for the side-effect re-read)."""

    def read(self, job_id: str) -> AttemptSnapshot | None:
        """Return the current snapshot, or ``None`` when the row is gone."""
        ...


def _as_status(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _is_terminal(status: Any) -> bool:
    return _as_status(status) in _TERMINAL_STATUSES


@dataclass(frozen=True)
class AttemptSnapshot:
    """Frozen controller view of the current attempt.

    Identity fields are copied from the frozen envelope by
    :meth:`from_record`; they are never recomputed from the current host/process.
    ``result`` is the persisted job result mapping (carries the
    ``terminal_side_effects_done`` / ``publication_pending`` markers).
    """

    job_id: str
    attempt: int
    submission_id: str
    release_id: str
    owner: str
    fencing_token: str
    revision: int
    status: str
    release_digest: str = ""
    work_dir: str = ""
    result: Mapping[str, Any] = field(default_factory=dict)
    envelope: ExecutionEnvelope | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempt", int(self.attempt))
        object.__setattr__(self, "revision", int(self.revision))
        object.__setattr__(self, "status", _as_status(self.status))
        object.__setattr__(self, "result", dict(self.result or {}))

    @classmethod
    def from_record(cls, record: Any, envelope: ExecutionEnvelope) -> AttemptSnapshot:
        """Freeze the current attempt view from a job row + its frozen envelope."""
        record_attempt = int(getattr(record, "attempt", 1))
        if int(envelope.attempt) != record_attempt:
            raise AttemptCompletionError(
                f"frozen envelope attempt {envelope.attempt} != current record attempt "
                f"{record_attempt}; refusing to bind a stale envelope to the current row"
            )
        if str(getattr(record, "id", "")) != envelope.job_id:
            raise AttemptCompletionError(
                f"frozen envelope job {envelope.job_id!r} != record job "
                f"{getattr(record, 'id', '')!r}"
            )
        result = getattr(record, "result", None)
        return cls(
            job_id=envelope.job_id,
            attempt=int(envelope.attempt),
            submission_id=envelope.submission_id,
            release_id=envelope.release_id,
            owner=envelope.owner,
            fencing_token=envelope.fencing_token,
            revision=int(getattr(record, "revision", 0)),
            status=_as_status(getattr(record, "status", "")),
            release_digest=envelope.release_digest,
            work_dir=str(getattr(record, "work_dir", "") or ""),
            result=dict(result) if isinstance(result, Mapping) else {},
            envelope=envelope,
        )

    @property
    def identity_key(self) -> tuple[str, int, str, str, str, str]:
        """The attempt/owner identity used for the side-effect re-read."""
        return (
            self.job_id,
            self.attempt,
            self.submission_id,
            self.release_id,
            self.owner,
            self.fencing_token,
        )

    @property
    def terminal(self) -> bool:
        return _is_terminal(self.status)

    @property
    def publication_pending(self) -> bool:
        """Durable pending-publication marker written in the terminal CAS."""
        if self.result.get("publication_pending") is True:
            return True
        # Compatibility with the existing manager marker.
        return self.result.get("terminal_side_effects_done") is False

    @property
    def publication_done(self) -> bool:
        # An explicit pending marker always wins over the manager's unrelated
        # terminal-side-effect marker: T27 marks local side effects done while
        # publication is still outstanding.
        if self.result.get("publication_pending") is True:
            return False
        if self.result.get("publication_pending") is False:
            return True
        if self.result.get("publication_done") is True:
            return True
        return self.result.get("terminal_side_effects_done") is True

    def same_identity(self, other: AttemptSnapshot | None) -> bool:
        return other is not None and other.identity_key == self.identity_key


@dataclass(frozen=True)
class AttemptCompletionVerdict:
    """Typed completion outcome (never a second job-state machine)."""

    code: str
    accepted: bool = False
    advanced: bool = False
    cas_calls: int = 0
    published: bool = False
    publication_pending: bool = False
    receipt: ExecutionReceipt | None = None
    failure: ExecutionError | None = None
    rejection: ReceiptVerdict | None = None
    snapshot: AttemptSnapshot | None = None
    detail: str = ""
    requires_process_check: bool = False
    publication_outcome: Any = None

    @property
    def requires_controller_cas(self) -> bool:
        """True only for the typed current-corrupt failure fact (task 27 writes it)."""
        return self.code == CURRENT_CORRUPT_AFTER_EXIT

    @property
    def authoritative(self) -> bool:
        return self.receipt is not None and self.accepted

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": ATTEMPT_COMPLETION_SCHEMA,
            "code": self.code,
            "accepted": self.accepted,
            "advanced": self.advanced,
            "cas_calls": self.cas_calls,
            "published": self.published,
            "publication_pending": self.publication_pending,
            "requires_controller_cas": self.requires_controller_cas,
            "requires_process_check": self.requires_process_check,
            "detail": self.detail,
            "failure": self.failure.to_dict() if self.failure is not None else None,
            "rejection": (
                {
                    "accepted": self.rejection.accepted,
                    "code": self.rejection.code,
                    "reason": self.rejection.reason,
                }
                if self.rejection is not None
                else None
            ),
            "snapshot": (
                {
                    "job_id": self.snapshot.job_id,
                    "attempt": self.snapshot.attempt,
                    "submission_id": self.snapshot.submission_id,
                    "release_id": self.snapshot.release_id,
                    "release_digest": self.snapshot.release_digest,
                    "owner": self.snapshot.owner,
                    "fencing_token": self.snapshot.fencing_token,
                    "revision": self.snapshot.revision,
                    "status": self.snapshot.status,
                    "publication_pending": self.snapshot.publication_pending,
                    "publication_done": self.snapshot.publication_done,
                }
                if self.snapshot is not None
                else None
            ),
        }


def _normalize_execution_state(value: AttemptExecutionState | str | Any) -> AttemptExecutionState:
    if isinstance(value, AttemptExecutionState):
        return value
    text = _as_status(value).lower()
    if text in {"held", "not_held", "claim_held", "running"}:
        return AttemptExecutionState.CLAIM_HELD
    if text in {"lost", "holder_lost", "execution_right_lost"}:
        return AttemptExecutionState.HOLDER_LOST
    return AttemptExecutionState.VALID_RECEIPT


def _verdict(
    code: str,
    *,
    snapshot: AttemptSnapshot | None = None,
    accepted: bool = False,
    advanced: bool = False,
    cas_calls: int = 0,
    published: bool = False,
    publication_pending: bool = False,
    receipt: ExecutionReceipt | None = None,
    failure: ExecutionError | None = None,
    rejection: ReceiptVerdict | None = None,
    detail: str = "",
    requires_process_check: bool = False,
    publication_outcome: Any = None,
) -> AttemptCompletionVerdict:
    return AttemptCompletionVerdict(
        code=code,
        accepted=accepted,
        advanced=advanced,
        cas_calls=cas_calls,
        published=published,
        publication_pending=publication_pending,
        receipt=receipt,
        failure=failure,
        rejection=rejection,
        snapshot=snapshot,
        detail=detail,
        requires_process_check=requires_process_check,
        publication_outcome=publication_outcome,
    )


@dataclass(frozen=True)
class AttemptCompletionService:
    """Receipt → CAS → publication handoff for exactly one attempt."""

    advances: JobStateAdvancePort
    publication: AttemptPublicationPort | None = None
    snapshots: AttemptSnapshotReader | None = None

    # -- public use cases -------------------------------------------------- #

    def complete(
        self,
        snapshot: AttemptSnapshot,
        *,
        envelope: ExecutionEnvelope | None = None,
        receipt: ExecutionReceipt | None = None,
        receipt_path: Path | str | None = None,
        confirmed_exited: bool = False,
        execution_state: AttemptExecutionState | str = AttemptExecutionState.VALID_RECEIPT,
        result_extra: Mapping[str, Any] | None = None,
        publication: AttemptPublicationPort | None = None,
    ) -> AttemptCompletionVerdict:
        """Evaluate one attempt completion; at most one terminal CAS.

        Args:
            snapshot: Current frozen attempt view (identity from the envelope).
            envelope: Frozen envelope; defaults to ``snapshot.envelope``.
            receipt: Already-decoded receipt (tests/controller adapters).
            receipt_path: Receipt path; defaults to ``<work_dir>/execution_receipt.json``.
            confirmed_exited: The controller has confirmed the execution exited.
                Only then does a missing/corrupt current receipt become the typed
                ``current_corrupt_after_exit`` failure fact.
            execution_state: ``valid_receipt`` / ``claim_held`` / ``holder_lost``.
            result_extra: Extra durable keys merged into the job result in the
                same CAS boundary (never status/identity keys).
            publication: Optional per-call publication override.
        """
        state = _normalize_execution_state(execution_state)
        if state is AttemptExecutionState.CLAIM_HELD:
            return _verdict(
                CLAIM_HELD,
                snapshot=snapshot,
                detail="attempt claim is currently held; wait or retry after reconcile",
            )
        if state is AttemptExecutionState.HOLDER_LOST:
            return _verdict(
                HOLDER_LOST,
                snapshot=snapshot,
                detail=(
                    "worker lost the execution right; verify the old process/children are "
                    "stopped before any retry (lock-file existence/timeout is not proof)"
                ),
                requires_process_check=True,
            )

        if snapshot.terminal:
            verdict = self._resume_terminal(snapshot, publication=publication)
            return self._attach_reused_receipt(
                verdict,
                snapshot,
                envelope,
                receipt=receipt,
                receipt_path=receipt_path,
            )

        frozen = envelope if envelope is not None else snapshot.envelope
        if frozen is None:
            raise AttemptCompletionError(
                "attempt completion requires the frozen ExecutionEnvelope "
                "(snapshot.envelope or an explicit envelope)"
            )

        decoded, read_error = self._read_receipt(
            snapshot, receipt=receipt, receipt_path=receipt_path
        )
        if decoded is None:
            if confirmed_exited:
                failure = ExecutionError(
                    code=CURRENT_CORRUPT_AFTER_EXIT,
                    message=(
                        f"current attempt {snapshot.attempt} of job {snapshot.job_id!r} "
                        f"has no valid receipt after confirmed exit: {read_error}"
                    ),
                    retryable=False,
                )
                return _verdict(
                    CURRENT_CORRUPT_AFTER_EXIT,
                    snapshot=snapshot,
                    failure=failure,
                    detail=read_error,
                )
            return _verdict(
                RECEIPT_PENDING,
                snapshot=snapshot,
                detail=read_error,
            )

        verdict = verify_receipt(
            decoded,
            frozen,
            current_attempt=snapshot.attempt,
            current_submission_id=snapshot.submission_id,
            current_release_id=snapshot.release_id,
            owner_token=snapshot.owner,
            fencing_token=snapshot.fencing_token,
        )
        if not verdict.accepted:
            return _verdict(
                verdict.code,
                snapshot=snapshot,
                rejection=verdict,
                receipt=decoded,
                detail=verdict.reason,
            )

        target_status = job_status_for_receipt(decoded)
        durable_result = dict(snapshot.result)
        if result_extra:
            durable_result.update(dict(result_extra))
        # Written in the SAME CAS as the terminal transition (manager-compatible
        # pending-publication marker).  Publication then flips the durable
        # publication fact idempotently; a crash here resumes publication.
        durable_result["publication_pending"] = True
        durable_result["terminal_side_effects_done"] = False
        durable_result["receipt_state"] = decoded.state
        durable_result["receipt_result_digest"] = decoded.result_digest
        durable_result["receipt_written_at"] = decoded.written_at

        try:
            advance_job_state(
                self.advances,
                frozen,
                decoded,
                current_attempt=snapshot.attempt,
                current_submission_id=snapshot.submission_id,
                current_release_id=snapshot.release_id,
                owner_token=snapshot.owner,
                fencing_token=snapshot.fencing_token,
                expected_revision=snapshot.revision,
                expected_status=snapshot.status,
                expected_attempt=snapshot.attempt,
                status=target_status,
                exit_code=receipt_exit_code(decoded),
                completed_at=utc_now_iso(),
                result=durable_result,
            )
        except ReceiptRejectedError as exc:
            return _verdict(
                exc.verdict.code,
                snapshot=snapshot,
                rejection=exc.verdict,
                receipt=decoded,
                detail=exc.verdict.reason,
            )
        except JobStateConflictError as exc:
            return _verdict(
                "cas_conflict",
                snapshot=snapshot,
                receipt=decoded,
                detail=f"terminal CAS conflicted: {exc}",
            )

        # Side-effect boundary: never let an old attempt publish into the
        # directory of a rerun/new attempt.
        current = self._reread(snapshot)
        if current is None or not snapshot.same_identity(current):
            return _verdict(
                SUPERSEDED,
                snapshot=snapshot,
                accepted=True,
                advanced=True,
                cas_calls=1,
                receipt=decoded,
                detail="attempt changed between terminal CAS and publication; publication skipped",
            )

        if current.publication_done:
            return _verdict(
                ALREADY_PUBLISHED,
                snapshot=current,
                accepted=True,
                advanced=True,
                cas_calls=1,
                published=True,
                receipt=decoded,
            )

        published, outcome, publication_error, already = self._publish(current, publication)
        if publication_error:
            return _verdict(
                PUBLICATION_FAILED,
                snapshot=current,
                accepted=True,
                advanced=True,
                cas_calls=1,
                publication_pending=True,
                receipt=decoded,
                detail=publication_error,
                publication_outcome=outcome,
            )
        if already:
            return _verdict(
                ALREADY_PUBLISHED,
                snapshot=current,
                accepted=True,
                advanced=True,
                cas_calls=1,
                published=True,
                receipt=decoded,
            )
        if not published:
            return _verdict(
                ACCEPTED_PENDING_PUBLICATION,
                snapshot=current,
                accepted=True,
                advanced=True,
                cas_calls=1,
                publication_pending=True,
                receipt=decoded,
            )
        return _verdict(
            "published",
            snapshot=current,
            accepted=True,
            advanced=True,
            cas_calls=1,
            published=True,
            receipt=decoded,
            publication_outcome=outcome,
        )

    def resume_pending_publication(
        self,
        snapshot: AttemptSnapshot,
        *,
        publication: AttemptPublicationPort | None = None,
    ) -> AttemptCompletionVerdict:
        """Resume one terminal row's unfinished publication (no CAS, no QC run)."""
        current = self._reread(snapshot)
        if current is None:
            return _verdict(
                "job_missing",
                snapshot=snapshot,
                detail="job row disappeared before publication recovery",
            )
        if not snapshot.same_identity(current):
            return _verdict(
                SUPERSEDED,
                snapshot=current,
                detail="terminal row was superseded by a newer attempt; publication skipped",
            )
        if not current.terminal:
            return _verdict(
                "not_terminal",
                snapshot=current,
                detail="publication recovery only applies to terminal rows",
            )
        if current.publication_done:
            return _verdict(
                ALREADY_PUBLISHED,
                snapshot=current,
                published=True,
                detail="publication already complete; no-op",
            )
        if not current.publication_pending:
            return _verdict(
                "not_publication_pending",
                snapshot=current,
                detail="terminal row carries no pending-publication fact; no-op",
            )

        published, outcome, publication_error, already = self._publish(current, publication)
        if publication_error:
            return _verdict(
                PUBLICATION_FAILED,
                snapshot=current,
                publication_pending=True,
                detail=publication_error,
                publication_outcome=outcome,
            )
        if already:
            return _verdict(
                ALREADY_PUBLISHED,
                snapshot=current,
                published=True,
                detail="publication fact already exists; no-op",
            )
        if not published:
            return _verdict(
                PUBLICATION_PENDING,
                snapshot=current,
                publication_pending=True,
                detail="no publication port configured; publication remains pending",
            )
        return _verdict(
            "published",
            snapshot=current,
            published=True,
            publication_outcome=outcome,
            detail="pending publication completed on restart/reconcile",
        )

    # -- helpers ----------------------------------------------------------- #

    def _resume_terminal(
        self,
        snapshot: AttemptSnapshot,
        *,
        publication: AttemptPublicationPort | None,
    ) -> AttemptCompletionVerdict:
        verdict = self.resume_pending_publication(snapshot, publication=publication)
        if verdict.code == "not_publication_pending":
            return _verdict(
                "already_terminal",
                snapshot=verdict.snapshot or snapshot,
                published=verdict.published,
                detail="terminal row requires no receipt transition; no-op",
            )
        return verdict

    def _attach_reused_receipt(
        self,
        verdict: AttemptCompletionVerdict,
        snapshot: AttemptSnapshot,
        envelope: ExecutionEnvelope | None,
        *,
        receipt: ExecutionReceipt | None,
        receipt_path: Path | str | None,
    ) -> AttemptCompletionVerdict:
        """Attach a valid current receipt to a terminal no-op/resume verdict.

        This is the "valid receipt reuse" state: the existing receipt is
        returned as the authoritative fact without re-execution and without a
        second CAS.  Missing/corrupt receipts on an already-terminal row never
        become ``current_corrupt_after_exit`` (that fact is only for a
        non-terminal execution that has confirmed exited).
        """
        frozen = envelope if envelope is not None else snapshot.envelope
        if frozen is None:
            return verdict
        decoded, _ = self._read_receipt(snapshot, receipt=receipt, receipt_path=receipt_path)
        if decoded is None:
            return verdict
        check = verify_receipt(
            decoded,
            frozen,
            current_attempt=snapshot.attempt,
            current_submission_id=snapshot.submission_id,
            current_release_id=snapshot.release_id,
            owner_token=snapshot.owner,
            fencing_token=snapshot.fencing_token,
        )
        if not check.accepted:
            return verdict
        return replace(verdict, receipt=decoded, accepted=True)

    def _reread(self, snapshot: AttemptSnapshot) -> AttemptSnapshot | None:
        if self.snapshots is None:
            return snapshot
        try:
            return self.snapshots.read(snapshot.job_id)
        except Exception:  # noqa: BLE001 - re-read failure must not publish blind
            return None

    def _read_receipt(
        self,
        snapshot: AttemptSnapshot,
        *,
        receipt: ExecutionReceipt | None,
        receipt_path: Path | str | None,
    ) -> tuple[ExecutionReceipt | None, str]:
        if receipt is not None:
            if not isinstance(receipt, ExecutionReceipt):
                return None, f"receipt object is {type(receipt).__name__}, not ExecutionReceipt"
            return receipt, ""
        path = (
            Path(receipt_path)
            if receipt_path is not None
            else Path(snapshot.work_dir) / "execution_receipt.json"
            if snapshot.work_dir
            else None
        )
        if path is None:
            return None, "no receipt path available"
        try:
            decoded = read_receipt(path)
        except (ReceiptConflictError, EnvelopeError, OSError, ValueError) as exc:
            return None, f"receipt at {path} is missing or corrupt: {exc}"
        if decoded is None:
            return None, f"no receipt at {path}"
        return decoded, ""

    def _publish(
        self,
        snapshot: AttemptSnapshot,
        publication: AttemptPublicationPort | None,
    ) -> tuple[bool, Any, str, bool]:
        port = publication if publication is not None else self.publication
        if port is None:
            return False, None, "", False
        checker = getattr(port, "is_published", None)
        if checker is not None:
            try:
                if bool(checker(snapshot)):
                    return True, None, "", True
            except Exception:  # noqa: BLE001 - a broken probe must not skip a real publish
                pass
        try:
            outcome = port.publish(snapshot)
        except Exception as exc:  # noqa: BLE001 - publication failure stays retryable
            return False, None, f"publication failed: {type(exc).__name__}: {exc}", False
        return True, outcome, "", False
