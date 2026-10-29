"""Execution-right port at the worker failure seam (D02 / task 7).

Exactly one worker may hold the exclusive right to write the authoritative
receipt/``task_result`` for one frozen ``(job, attempt)``.  The worker consults
this port twice:

* :meth:`ExecutionRightPort.acquire` before it invokes the executor — a
  competition loser or duplicate delivery gets ``NOT_HELD`` and writes only a
  separate diagnostic/retry document;
* :meth:`ExecutionRightPort.verify` immediately before it would write an
  authoritative failed receipt — a worker that lost the right (fencing
  superseded, lease taken over) gets ``LOST`` and likewise writes only a
  separate diagnostic document.

Task 24 supplies the persistent claim implementation (``attempt.lock``/owner
token/lease).  Task 7 defines the seam and this module's transitional default
:class:`AlwaysHeldExecutionRight` keeps the single-worker path behaving exactly
as before; the holder/loser matrix is exercised with an injected fake port.

A :class:`WorkerRefusal` is deliberately **not** an
:class:`~acp_services.contracts.envelope.ExecutionReceipt`: the controller
cannot mistake it for an authoritative failure fact and
:func:`acp_services.runtime.receipts.advance_job_from_worker_result` performs no
job CAS for it.

This module imports only the dependency-free envelope/identity contracts, so
``worker_bootstrap`` and ``receipts`` may both import it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from acp_services.contracts.envelope import ExecutionEnvelope, ExecutionReceipt
from acp_services.runtime.receipt_identity import ReceiptIdentity

__all__ = [
    "EXIT_EXECUTION_REFUSED",
    "REFUSAL_SCHEMA",
    "AlwaysHeldExecutionRight",
    "ExecutionRightPort",
    "ExecutionRightState",
    "ExecutionRightVerdict",
    "WorkerExecutionResult",
    "WorkerRefusal",
]

#: Distinct non-zero process exit code for a non-authoritative refusal/retry.
#: Never 0 (a naive exit-code consumer must not read a refusal as success) and
#: never 1 (exit 1 is reserved for the authoritative failed receipt).
EXIT_EXECUTION_REFUSED: int = 3

#: Diagnostic document schema written under ``<receipt>.refused.json``.
REFUSAL_SCHEMA: str = "worker_execution_refusal"


class ExecutionRightState(str, Enum):
    """Whether this worker may write the authoritative result for the attempt."""

    HELD = "held"
    """This worker holds the exclusive right for the current attempt."""

    NOT_HELD = "not_held"
    """Another worker/claim owns the right; this worker waits or refuses."""

    LOST = "lost"
    """This worker previously held the right and has since lost it (fencing)."""


@dataclass(frozen=True)
class ExecutionRightVerdict:
    """Typed execution-right observation (``state`` + optional ``detail``)."""

    state: ExecutionRightState = ExecutionRightState.HELD
    detail: str = ""

    @property
    def held(self) -> bool:
        """True only for :attr:`ExecutionRightState.HELD`."""
        return self.state is ExecutionRightState.HELD

    @property
    def authoritative(self) -> bool:
        """Alias of :attr:`held` (only the holder may write authoritative facts)."""
        return self.held

    def to_dict(self) -> dict[str, str]:
        return {"state": self.state.value, "detail": self.detail}


#: Shared immutable verdict for the transitional always-held port.
_HELD = ExecutionRightVerdict()


class ExecutionRightPort(Protocol):
    """The injected execution-right seam (task 24 implements it persistently)."""

    def acquire(self, envelope: ExecutionEnvelope) -> ExecutionRightVerdict:
        """Claim/observe the right before the executor is invoked.

        Implementations must return ``HELD`` for the *same* live owner/attempt
        so a duplicate delivery of the same execution fact stays idempotent
        (the worker then reuses its matching receipt instead of refusing).
        """
        ...

    def verify(self, envelope: ExecutionEnvelope) -> ExecutionRightVerdict:
        """Re-check the right immediately before an authoritative write."""
        ...


@dataclass(frozen=True)
class AlwaysHeldExecutionRight:
    """Transitional default: one standalone worker assumption of ``HELD``.

    The persistent claim/lease (``attempt.lock`` owner token + fencing) arrives
    with task 24; until the controller injects it, a standalone worker keeps its
    historical single-holder behavior.
    """

    def acquire(self, envelope: ExecutionEnvelope) -> ExecutionRightVerdict:
        return _HELD

    def verify(self, envelope: ExecutionEnvelope) -> ExecutionRightVerdict:
        return _HELD


@dataclass(frozen=True)
class WorkerRefusal:
    """Non-authoritative worker result: wait (``NOT_HELD``) or retry (``LOST``).

    Written only as ``<receipt>.refused.json``; it never occupies the canonical
    receipt path and never persists a ``task_result``.
    """

    envelope: ExecutionEnvelope
    verdict: ExecutionRightVerdict
    receipt_path: str = ""
    exit_code: int = EXIT_EXECUTION_REFUSED

    @property
    def authoritative(self) -> bool:
        return False

    @property
    def state(self) -> str:
        return "wait" if self.verdict.state is ExecutionRightState.NOT_HELD else "retry"

    @property
    def code(self) -> str:
        if self.verdict.state is ExecutionRightState.NOT_HELD:
            return "execution_right_not_held"
        return "execution_right_lost"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": REFUSAL_SCHEMA,
            "state": self.state,
            "code": self.code,
            "authoritative": False,
            "exit_code": self.exit_code,
            "detail": self.verdict.detail,
            "receipt_path": self.receipt_path,
            "execution_right": self.verdict.to_dict(),
            "current_identity": ReceiptIdentity.from_envelope(self.envelope).to_dict(),
            "receipt": None,
        }


#: What :meth:`WorkerRuntime.run` may return: the authoritative receipt or a
#: non-authoritative refusal.  Consumers must discriminate on type.
WorkerExecutionResult = ExecutionReceipt | WorkerRefusal
