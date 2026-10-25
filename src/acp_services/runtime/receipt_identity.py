"""Dependency-free receipt-identity predicate (D02).

One canonical projection of the frozen execution identity, compared between an
:class:`~acp_services.contracts.envelope.ExecutionReceipt` (an observation) and
the current :class:`~acp_services.contracts.envelope.ExecutionEnvelope` (the
frozen truth). The predicate is the single place that decides whether an
existing receipt belongs to the current attempt; the worker and the controller
both reuse it instead of keeping their own field lists.

What it compares (all of it, never a subset):

``receipt_version``, ``job_id``, ``attempt``, ``submission_id`` (also against
the deterministic :func:`~acp_services.contracts.envelope.expected_submission_id`
derivation), ``release_id``, ``release_digest``, ``owner``, ``fencing_token``,
``plan_identity``, ``step_identities`` and ``data_bindings`` (binding ids plus
their artifact digests, canonically ordered).

Import contract: this module imports only the standard library and the
dependency-free ``acp_services.contracts.envelope`` layer. It must never import
``acp_services.runtime.worker`` or ``acp_services.runtime.receipts`` —
``worker`` and ``receipts`` both import *it*, which keeps the runtime import
graph acyclic in either import order (guarded by
``tests/test_services_worker_contract.py``).

Author: ACP Services (B lane, D02).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields
from typing import Any

from acp_services.contracts.envelope import (
    CURRENT_RECEIPT_VERSION,
    ExecutionEnvelope,
    ExecutionReceipt,
    StorageBinding,
    expected_submission_id,
)
from acp_services.runtime.errors import WorkerError

__all__ = [
    "IdentityConflict",
    "ReceiptIdentity",
    "ReceiptIdentityComparison",
    "ReceiptIdentityError",
    "assert_receipt_identity",
    "compare_receipt_identity",
    "receipt_identity_matches",
]

#: Stable conflict code for a receipt whose ``submission_id`` is not the
#: deterministic derivation of its own ``(job_id, attempt)``.
SUBMISSION_DERIVATION_CODE: str = "submission_derivation"

#: Canonical, hashable form of one ``StorageBinding``'s identity: the binding id
#: plus its sorted artifact digests. Display-only binding fields (tenant/root/
#: relative path/...) are deliberately not part of the execution identity.
_BindingKey = tuple[str, tuple[tuple[str, str], ...]]


def _canonical_bindings(bindings: Iterable[StorageBinding]) -> tuple[_BindingKey, ...]:
    return tuple(
        sorted(
            (
                binding.binding_id,
                tuple(
                    sorted(
                        (str(key), str(value)) for key, value in binding.artifact_digests.items()
                    )
                ),
            )
            for binding in bindings
        )
    )


@dataclass(frozen=True)
class ReceiptIdentity:
    """Canonical projection of the frozen identity both carriers must agree on.

    ``ReceiptIdentity.from_receipt()`` and ``ReceiptIdentity.from_envelope()``
    are the only two constructors; :func:`compare_receipt_identity` diffs the
    projections field by field, so adding a field to the contract (and to this
    projection) automatically joins the comparison.
    """

    receipt_version: int
    job_id: str
    attempt: int
    submission_id: str
    release_id: str
    release_digest: str
    owner: str
    fencing_token: str
    plan_identity: str
    step_identities: tuple[str, ...]
    data_bindings: tuple[_BindingKey, ...]

    @classmethod
    def from_receipt(cls, receipt: ExecutionReceipt) -> ReceiptIdentity:
        """Project an observed receipt onto the identity fields."""
        return cls(
            receipt_version=receipt.receipt_version,
            job_id=receipt.job_id,
            attempt=receipt.attempt,
            submission_id=receipt.submission_id,
            release_id=receipt.release_id,
            release_digest=receipt.release_digest,
            owner=receipt.owner,
            fencing_token=receipt.fencing_token,
            plan_identity=receipt.plan_identity,
            step_identities=tuple(receipt.step_identities),
            data_bindings=_canonical_bindings(receipt.data_bindings),
        )

    @classmethod
    def from_envelope(cls, envelope: ExecutionEnvelope) -> ReceiptIdentity:
        """Project the frozen envelope onto the identity fields.

        A receipt written for this envelope must carry exactly these values; the
        expected receipt version is the contract's current version.
        """
        return cls(
            receipt_version=CURRENT_RECEIPT_VERSION,
            job_id=envelope.job_id,
            attempt=envelope.attempt,
            submission_id=envelope.submission_id,
            release_id=envelope.release_id,
            release_digest=envelope.release_digest,
            owner=envelope.owner,
            fencing_token=envelope.fencing_token,
            plan_identity=envelope.plan_identity,
            step_identities=tuple(envelope.step_identities),
            data_bindings=_canonical_bindings(envelope.data_bindings),
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly projection (diagnostics/conflict documents)."""
        return {
            "receipt_version": self.receipt_version,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "submission_id": self.submission_id,
            "release_id": self.release_id,
            "release_digest": self.release_digest,
            "owner": self.owner,
            "fencing_token": self.fencing_token,
            "plan_identity": self.plan_identity,
            "step_identities": list(self.step_identities),
            "data_bindings": [
                {"binding_id": binding_id, "artifact_digests": dict(digests)}
                for binding_id, digests in self.data_bindings
            ],
        }


_IDENTITY_FIELDS: tuple[str, ...] = tuple(field.name for field in fields(ReceiptIdentity))


@dataclass(frozen=True)
class IdentityConflict:
    """One mismatching identity field (stable ``field`` code + both values)."""

    field: str
    expected: Any
    actual: Any

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "expected": self.expected, "actual": self.actual}


@dataclass(frozen=True)
class ReceiptIdentityComparison:
    """Typed outcome of :func:`compare_receipt_identity`.

    ``matches`` is the only success signal; ``code`` is the first conflicting
    field (stable, machine-readable) and ``conflicts`` carries every mismatch.
    """

    conflicts: tuple[IdentityConflict, ...] = ()

    @property
    def matches(self) -> bool:
        return not self.conflicts

    @property
    def accepted(self) -> bool:
        """Alias for :attr:`matches` (verdict symmetry with ``ReceiptVerdict``)."""
        return self.matches

    @property
    def code(self) -> str:
        return self.conflicts[0].field if self.conflicts else ""

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(conflict.field for conflict in self.conflicts)

    def describe(self) -> str:
        return (
            "; ".join(
                f"{conflict.field}: expected {conflict.expected!r} got {conflict.actual!r}"
                for conflict in self.conflicts
            )
            or "identity matches"
        )

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "conflicts": [c.to_dict() for c in self.conflicts]}


class ReceiptIdentityError(WorkerError):
    """A receipt does not carry the frozen envelope's full identity.

    The typed conflict detail travels on :attr:`comparison` (``None`` when the
    error is raised for an unreadable receipt rather than a mismatch).
    """

    def __init__(
        self,
        message: str,
        *,
        comparison: ReceiptIdentityComparison | None = None,
    ) -> None:
        super().__init__(message)
        self.comparison = comparison
        self.code = (
            comparison.code if comparison is not None and comparison.code else "receipt_identity"
        )


def compare_receipt_identity(
    receipt: ExecutionReceipt, envelope: ExecutionEnvelope
) -> ReceiptIdentityComparison:
    """Compare *receipt* against *envelope* on every frozen identity field.

    Returns a typed comparison instead of raising so callers can decide between
    reuse (``matches``) and a typed conflict. Never mutates either carrier.
    """
    actual = ReceiptIdentity.from_receipt(receipt)
    expected = ReceiptIdentity.from_envelope(envelope)
    conflicts = [
        IdentityConflict(field=name, expected=getattr(expected, name), actual=getattr(actual, name))
        for name in _IDENTITY_FIELDS
        if getattr(expected, name) != getattr(actual, name)
    ]
    derived = expected_submission_id(actual.job_id, actual.attempt)
    if actual.submission_id != derived:
        conflicts.append(
            IdentityConflict(
                field=SUBMISSION_DERIVATION_CODE,
                expected=derived,
                actual=actual.submission_id,
            )
        )
    return ReceiptIdentityComparison(tuple(conflicts))


def receipt_identity_matches(receipt: ExecutionReceipt, envelope: ExecutionEnvelope) -> bool:
    """True only when the receipt carries the envelope's full frozen identity."""
    return compare_receipt_identity(receipt, envelope).matches


def assert_receipt_identity(
    receipt: ExecutionReceipt,
    envelope: ExecutionEnvelope,
    *,
    context: str = "",
) -> None:
    """Raise :class:`ReceiptIdentityError` unless the identities are identical."""
    comparison = compare_receipt_identity(receipt, envelope)
    if comparison.matches:
        return
    prefix = f"{context}: " if context else ""
    raise ReceiptIdentityError(
        f"{prefix}receipt identity does not match the frozen envelope ({comparison.describe()})",
        comparison=comparison,
    )
