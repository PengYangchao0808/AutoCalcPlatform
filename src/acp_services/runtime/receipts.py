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
from acp_services.runtime.worker import WorkerError

__all__ = [
    "ReceiptRejectedError",
    "ReceiptVerdict",
    "advance_job_state",
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
