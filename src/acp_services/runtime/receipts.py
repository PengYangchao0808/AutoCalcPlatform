"""Controller-side receipt verification + eligibility-gated state advance (B06-2).

The controller is the *decision* half of the execution-integrity split. It must
never trust a worker's receipt: before advancing a job it re-checks the receipt
against the **current** frozen envelope and the job's current attempt/submission/
release/owner/fencing, and rejects any stale or forged observation. Only after
:func:`verify_receipt` accepts does :func:`advance_job_state` call the injected
CAS port (:class:`~acp_services.contracts.injection.JobStateAdvancePort`, mapped
onto ``JobStore.transition``'s revision/status/attempt guards); on rejection the
port is never called and job state is unchanged.

Rejection matrix (each maps to a stable ``code``):

===============  ==========================================================
``job_mismatch``  receipt belongs to a different job
``stale_attempt`` receipt attempt != current/envelope attempt
``forged_submission`` receipt's submission is not the deterministic derivation
``stale_submission`` receipt submission != current/envelope submission
``stale_release`` receipt release != current/envelope release
``wrong_owner``   receipt owner != current owner token
``wrong_fencing`` receipt fencing != current fencing token
``digest_mismatch`` plan/step/release digests disagree with the envelope
===============  ==========================================================

Historical usage (M0-07)
------------------------

:func:`verify_receipt` is the **current-attempt** guard that gates a job state
advance.  A late usage receipt for an *older* attempt must not advance anything,
yet its real consumption must still be booked.  :func:`verify_historical_receipt`
therefore re-checks the receipt against the immutable identity that was bound
for its own ``(job, attempt)`` — never against the current attempt — and its
callers only ever write usage rows.  ``attempt_mismatch`` /
``reservation_mismatch`` are the historical codes; there is no current-owner or
current-fencing input on this path by construction.

Author: ACP Services (B lane, todo 31).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from acp_services.contracts.envelope import (
    ExecutionEnvelope,
    ExecutionReceipt,
    expected_submission_id,
)
from acp_services.contracts.injection import JobStateAdvancePort
from acp_services.runtime.errors import WorkerError
from acp_services.runtime.receipt_identity import ReceiptIdentity
from acp_services.runtime.receipt_identity import identity_digest as _identity_digest

__all__ = [
    "ReceiptHistoryBinding",
    "ReceiptRejectedError",
    "ReceiptVerdict",
    "advance_job_from_worker_result",
    "advance_job_state",
    "verify_historical_receipt",
    "verify_receipt",
]


@dataclass(frozen=True)
class ReceiptVerdict:
    """Typed outcome of receipt verification (``accepted`` + stable reason)."""

    accepted: bool
    code: str = ""
    reason: str = ""

    def __bool__(self) -> bool:
        return self.accepted


class ReceiptRejectedError(WorkerError):
    """A receipt failed eligibility; carries the typed verdict."""

    def __init__(self, verdict: ReceiptVerdict) -> None:
        super().__init__(f"receipt rejected ({verdict.code}): {verdict.reason}")
        self.verdict = verdict


def _reject(code: str, reason: str) -> ReceiptVerdict:
    return ReceiptVerdict(accepted=False, code=code, reason=reason)


def verify_receipt(
    receipt: ExecutionReceipt,
    envelope: ExecutionEnvelope,
    *,
    current_attempt: int,
    current_submission_id: str,
    current_release_id: str,
    owner_token: str,
    fencing_token: str,
) -> ReceiptVerdict:
    """Re-check a receipt against the current frozen identity.

    Returns an accepted verdict only when the receipt matches **both** the
    frozen envelope and the job's current attempt/submission/release/owner/
    fencing. The first failing check decides the verdict code.
    """
    if receipt.job_id != envelope.job_id:
        return _reject(
            "job_mismatch",
            f"receipt job {receipt.job_id!r} != envelope job {envelope.job_id!r}",
        )
    if receipt.attempt != current_attempt or receipt.attempt != envelope.attempt:
        return _reject(
            "stale_attempt",
            f"receipt attempt {receipt.attempt} is not current "
            f"{current_attempt} (envelope {envelope.attempt})",
        )
    derived = expected_submission_id(receipt.job_id, receipt.attempt)
    if receipt.submission_id != derived:
        return _reject(
            "forged_submission",
            f"receipt submission {receipt.submission_id!r} is not the derivation {derived!r}",
        )
    if (
        receipt.submission_id != current_submission_id
        or receipt.submission_id != envelope.submission_id
    ):
        return _reject(
            "stale_submission",
            f"receipt submission {receipt.submission_id!r} is not the current "
            f"{current_submission_id!r} (envelope {envelope.submission_id!r})",
        )
    if receipt.release_id != current_release_id or receipt.release_id != envelope.release_id:
        return _reject(
            "stale_release",
            f"receipt release {receipt.release_id!r} is not the current "
            f"{current_release_id!r} (envelope {envelope.release_id!r})",
        )
    if receipt.owner != owner_token or receipt.owner != envelope.owner:
        return _reject(
            "wrong_owner",
            f"receipt owner {receipt.owner!r} != current {owner_token!r} "
            f"(envelope {envelope.owner!r})",
        )
    if receipt.fencing_token != fencing_token or receipt.fencing_token != envelope.fencing_token:
        return _reject(
            "wrong_fencing",
            f"receipt fencing {receipt.fencing_token!r} != current {fencing_token!r} "
            f"(envelope {envelope.fencing_token!r})",
        )
    if (
        receipt.plan_identity != envelope.plan_identity
        or tuple(receipt.step_identities) != tuple(envelope.step_identities)
        or receipt.release_digest != envelope.release_digest
    ):
        return _reject(
            "digest_mismatch",
            "receipt plan/step/release digests disagree with the frozen envelope",
        )
    return ReceiptVerdict(accepted=True)


@dataclass(frozen=True)
class ReceiptHistoryBinding:
    """The immutable execution identity bound to one historical ``(job, attempt)``.

    It is persisted (admission DB) when the attempt is first bound / its first
    trusted usage receipt is booked, and is never rewritten afterwards.  A late
    receipt is compared against *this* row, so booking attempt 1 after attempt 2
    has started neither requires nor consults the current attempt, the current
    fencing token or the job's work directory.
    """

    job_id: str
    attempt: int
    submission_id: str = ""
    release_id: str = ""
    release_digest: str = ""
    owner: str = ""
    fencing_token: str = ""
    plan_identity: str = ""
    step_identities: tuple[str, ...] = ()
    reservation_id: str = ""
    chain_digest: str = ""

    @property
    def identity(self) -> ReceiptIdentity:
        return ReceiptIdentity(
            receipt_version=1,
            job_id=self.job_id,
            attempt=self.attempt,
            submission_id=self.submission_id,
            release_id=self.release_id,
            release_digest=self.release_digest,
            owner=self.owner,
            fencing_token=self.fencing_token,
            plan_identity=self.plan_identity,
            step_identities=tuple(self.step_identities),
            data_bindings=(),
        )

    def computed_digest(self) -> str:
        """Chain summary of this binding (computed from the identity fields)."""
        return _identity_digest(self.identity)

    @classmethod
    def from_receipt(
        cls, receipt: ExecutionReceipt, *, reservation_id: str = ""
    ) -> ReceiptHistoryBinding:
        """Project an observed receipt onto the immutable binding shape."""
        return cls(
            job_id=receipt.job_id,
            attempt=int(receipt.attempt),
            submission_id=receipt.submission_id,
            release_id=receipt.release_id,
            release_digest=receipt.release_digest,
            owner=receipt.owner,
            fencing_token=receipt.fencing_token,
            plan_identity=receipt.plan_identity,
            step_identities=tuple(receipt.step_identities),
            reservation_id=str(reservation_id or ""),
            chain_digest="",
        )

    @classmethod
    def from_envelope(
        cls, envelope: ExecutionEnvelope, *, reservation_id: str = ""
    ) -> ReceiptHistoryBinding:
        """Project a frozen envelope onto the immutable binding shape."""
        return cls(
            job_id=envelope.job_id,
            attempt=int(envelope.attempt),
            submission_id=envelope.submission_id,
            release_id=envelope.release_id,
            release_digest=envelope.release_digest,
            owner=envelope.owner,
            fencing_token=envelope.fencing_token,
            plan_identity=envelope.plan_identity,
            step_identities=tuple(envelope.step_identities),
            reservation_id=str(reservation_id or ""),
            chain_digest="",
        )

    def with_chain_digest(self) -> ReceiptHistoryBinding:
        """Return a copy whose ``chain_digest`` is the computed identity digest."""
        computed = self.computed_digest()
        if self.chain_digest == computed:
            return self
        return ReceiptHistoryBinding(
            job_id=self.job_id,
            attempt=self.attempt,
            submission_id=self.submission_id,
            release_id=self.release_id,
            release_digest=self.release_digest,
            owner=self.owner,
            fencing_token=self.fencing_token,
            plan_identity=self.plan_identity,
            step_identities=tuple(self.step_identities),
            reservation_id=self.reservation_id,
            chain_digest=computed,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "attempt": self.attempt,
            "submission_id": self.submission_id,
            "release_id": self.release_id,
            "release_digest": self.release_digest,
            "owner": self.owner,
            "fencing_token": self.fencing_token,
            "plan_identity": self.plan_identity,
            "step_identities": list(self.step_identities),
            "reservation_id": self.reservation_id,
            "chain_digest": self.chain_digest,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> ReceiptHistoryBinding:
        """Rebuild a binding from its persisted ``to_dict`` projection."""
        data = dict(payload or {})
        return cls(
            job_id=str(data.get("job_id") or ""),
            attempt=int(data.get("attempt") or 0),
            submission_id=str(data.get("submission_id") or ""),
            release_id=str(data.get("release_id") or ""),
            release_digest=str(data.get("release_digest") or ""),
            owner=str(data.get("owner") or ""),
            fencing_token=str(data.get("fencing_token") or ""),
            plan_identity=str(data.get("plan_identity") or ""),
            step_identities=tuple(str(item) for item in data.get("step_identities") or ()),
            reservation_id=str(data.get("reservation_id") or ""),
            chain_digest=str(data.get("chain_digest") or ""),
        )


def verify_historical_receipt(
    receipt: ExecutionReceipt,
    binding: ReceiptHistoryBinding,
    *,
    reservation_id: str | None = None,
) -> ReceiptVerdict:
    """Verify a late receipt against its **own** immutable attempt binding.

    Unlike :func:`verify_receipt` this consults no current attempt, owner or
    fencing token: the binding is the original attempt's frozen identity.  The
    caller only books usage on acceptance; job state is never advanced here.
    """
    if not str(binding.job_id):
        return _reject("binding_invalid", "the historical attempt binding has no job id")
    if receipt.job_id != binding.job_id:
        return _reject(
            "job_mismatch",
            f"receipt job {receipt.job_id!r} != bound job {binding.job_id!r}",
        )
    if int(receipt.attempt) != int(binding.attempt):
        return _reject(
            "attempt_mismatch",
            f"receipt attempt {receipt.attempt} != bound attempt {binding.attempt}",
        )
    derived = expected_submission_id(receipt.job_id, receipt.attempt)
    if receipt.submission_id != derived:
        return _reject(
            "forged_submission",
            f"receipt submission {receipt.submission_id!r} is not the derivation {derived!r}",
        )
    if receipt.submission_id != binding.submission_id:
        return _reject(
            "stale_submission",
            f"receipt submission {receipt.submission_id!r} != bound {binding.submission_id!r}",
        )
    if reservation_id is not None and binding.reservation_id != str(reservation_id):
        return _reject(
            "reservation_mismatch",
            f"bound reservation {binding.reservation_id!r} != receipt reservation "
            f"{str(reservation_id)!r}",
        )
    if receipt.release_id != binding.release_id or receipt.release_digest != binding.release_digest:
        return _reject(
            "stale_release",
            f"receipt release {receipt.release_id!r}/{receipt.release_digest!r} != bound "
            f"{binding.release_id!r}/{binding.release_digest!r}",
        )
    if receipt.owner != binding.owner or receipt.fencing_token != binding.fencing_token:
        return _reject(
            "wrong_owner",
            f"receipt owner/fencing {receipt.owner!r}/{receipt.fencing_token!r} != bound "
            f"{binding.owner!r}/{binding.fencing_token!r}",
        )
    if receipt.plan_identity != binding.plan_identity or tuple(receipt.step_identities) != tuple(
        binding.step_identities
    ):
        return _reject(
            "digest_mismatch",
            "receipt plan/step identity disagrees with the bound attempt identity",
        )
    expected_digest = binding.computed_digest()
    if binding.chain_digest and binding.chain_digest != expected_digest:
        return _reject(
            "digest_mismatch",
            f"bound chain digest {binding.chain_digest!r} != recomputed {expected_digest!r}",
        )
    return ReceiptVerdict(accepted=True)


def advance_job_from_worker_result(
    port: JobStateAdvancePort,
    envelope: ExecutionEnvelope,
    result: Any,
    **kwargs: Any,
) -> Any:
    """Consume a worker result, performing a CAS advance only for a receipt.

    A :class:`~acp_services.runtime.execution_right.WorkerRefusal` (competition
    loser / not-held / lost right) is **not** an authoritative failure fact: this
    returns ``None`` without calling the CAS port, so the job cannot transition
    to FAILED from a refusal.  Integrity is delegated to :func:`advance_job_state`
    for the authoritative receipt branch.
    """
    if not isinstance(result, ExecutionReceipt):
        return None
    return advance_job_state(port, envelope, result, **kwargs)


def advance_job_state(
    port: JobStateAdvancePort,
    envelope: ExecutionEnvelope,
    receipt: ExecutionReceipt,
    *,
    current_attempt: int,
    current_submission_id: str,
    current_release_id: str,
    owner_token: str,
    fencing_token: str,
    expected_revision: int,
    expected_status: Any = None,
    expected_attempt: int | None = None,
    **fields: Any,
) -> Any:
    """Advance the job through the CAS port **only after** eligibility passes.

    Runs :func:`verify_receipt` first; a rejected receipt raises
    :class:`ReceiptRejectedError` and the port is never touched, so the job state
    is unchanged. On acceptance the single guarded CAS advance is performed with
    the envelope's attempt as the default ``expected_attempt``.
    """
    verdict = verify_receipt(
        receipt,
        envelope,
        current_attempt=current_attempt,
        current_submission_id=current_submission_id,
        current_release_id=current_release_id,
        owner_token=owner_token,
        fencing_token=fencing_token,
    )
    if not verdict.accepted:
        raise ReceiptRejectedError(verdict)
    if expected_attempt is None:
        expected_attempt = envelope.attempt
    return port.advance(
        envelope.job_id,
        expected_revision=expected_revision,
        expected_status=expected_status,
        expected_attempt=expected_attempt,
        **fields,
    )
