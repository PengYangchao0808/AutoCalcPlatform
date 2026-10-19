"""B10-4 (todo 56) — decouple usage collection from science completion / result access.

The main plan §20.6 and work package B10-4 require that **accounting never blocks
science** and that **science completion never gates usage collection**.  This
module is the executable form of that separation.  It does not compute core
hours (that is :mod:`acp_services.runtime.metering_policy`, todo 53) nor collect
them (that is :mod:`acp_services.runtime.usage`, todo 54); it wires the two
already-frozen contracts so they can never entangle.

Three independent channels
--------------------------

The platform has three facts that must never depend on each other:

* **Usage ingestion** — an append-only :class:`UsageLedgerPort` keyed by
  ``usage_id``.  ``UsageRecord`` writes are *order-independent* (they may happen
  before, after or without a publication) and *retryable* (idempotent by key);
  the write path consults no publication/execution state.
* **Publication** — a separate :class:`PublicationPort`.  A publication failure
  is returned as a :class:`PublicationOutcome`; it never rolls back, blocks or
  mutates a usage record, and vice versa.  This mirrors the executor's
  publication-retry isolation (``acp/calculations/executor.py`` — "a publication
  failure stays a publication failure; the next resume retries only the
  publish").
* **Result access** — a :class:`ResultAccessGate` whose decision depends *only*
  on :class:`ResultAvailabilityPort` (is the result prepared?).  Accounting or
  quota state is carried for observation but has **no** vote: an exhausted
  balance, a depleted quota or a pending-reconciliation ledger never locks an
  already-produced result.

Non-ledger signals
------------------

Progress updates, client/browser timers and monitoring samples are explicitly
**not** usage bases (plan §20.2/§20.3).  They are rejected both structurally
(there is no code path from such a signal to a ``UsageRecord``) and by an
executable guard (:func:`require_trusted_usage_source` / :func:`is_usage_basis`).
Monitoring samples may report availability; they never become, or replace, the
ledger.

Dependency direction
--------------------

``src/acp_services/**`` must never import ``acp`` (nor ``cccp``).  This module is
stdlib plus the sibling :mod:`acp_services.runtime.usage` and
:mod:`acp_services.runtime.metering_policy` contracts.

Author: ACP Services (B lane, todo 56).
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from acp_services.runtime.usage import (
    CollectionSource,
    UsageRecord,
)

logger = logging.getLogger(__name__)

__all__ = [
    # vocabularies / notes
    "RejectedLedgerSource",
    "DecouplingViolationKind",
    "DECOUPLING_NOT_SCIENTIFIC_NOTE",
    "MONITORING_IS_NOT_LEDGER_NOTE",
    # ports
    "UsageLedgerPort",
    "PublicationPort",
    "ResultAvailabilityPort",
    # value objects
    "UsageIngestReceipt",
    "PublicationOutcome",
    "ResultAccessDecision",
    "DecoupledAttemptOutcome",
    "DecouplingViolation",
    "DecouplingAudit",
    # errors
    "DecouplingError",
    "UntrustedUsageBasisError",
    "UsageLedgerError",
    "PublicationError",
    # channels
    "UsageIngestChannel",
    "PublicationChannel",
    "ResultAccessGate",
    "DecoupledAttemptCoordinator",
    # functions
    "is_usage_basis",
    "require_trusted_usage_source",
    "audit_decoupling",
]

#: Executable statement that accounting/usage state is a platform fact with no
#: authority over scientific completion or result reads.
DECOUPLING_NOT_SCIENTIFIC_NOTE = (
    "Usage collection, publication and result access are independent platform "
    "facts: a UsageRecord is never written from the publication/completion path, "
    "and accounting/quota state never blocks reading an already-produced "
    "scientific result (plan §20.6)."
)

#: Executable statement that a monitoring sample is evidence about availability,
#: never a replacement for the usage ledger.
MONITORING_IS_NOT_LEDGER_NOTE = (
    "A monitoring sample is availability/telemetry evidence only; it is never a "
    "usage basis and never becomes or replaces the usage ledger (plan §20.2/§20.3)."
)


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------


class RejectedLedgerSource(str):
    """A signal that may never be a usage source (plan §20.2/§20.3).

    These are deliberately disjoint from :class:`usage.CollectionSource`; there
    is no overlap and no conversion between them.
    """

    PROGRESS = "progress"
    BROWSER_TIMER = "browser-timer"
    MONITOR_SAMPLE = "monitoring-sample"
    ALL = frozenset({PROGRESS, BROWSER_TIMER, MONITOR_SAMPLE})


class DecouplingViolationKind(str):
    """Audit violation kinds produced by :func:`audit_decoupling`."""

    FORBIDDEN_IMPORT = "forbidden-import"
    REJECTED_SIGNAL_TERM = "rejected-signal-term"
    ALL = frozenset({FORBIDDEN_IMPORT, REJECTED_SIGNAL_TERM})


_REJECTED_SIGNAL_TERMS = frozenset(
    {"progress", "browser", "elapsed", "wall_clock", "percent", "monitor"}
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DecouplingError(RuntimeError):
    """Base error for the decoupled metering boundary."""


class UntrustedUsageBasisError(DecouplingError):
    """A non-ledger signal was offered as a usage basis (fail closed)."""


class UsageLedgerError(DecouplingError):
    """The usage ledger (sink) could not be written."""


class PublicationError(DecouplingError):
    """The publication step failed (isolated; never touches the ledger)."""


# ---------------------------------------------------------------------------
# Ports (structural)
# ---------------------------------------------------------------------------


@runtime_checkable
class UsageLedgerPort(Protocol):
    """The append-only usage ledger — the only balance/usage authority.

    ``append`` must be idempotent by ``usage_id``; callers additionally guard
    with :meth:`get` so a repeated collection cannot double-count.
    """

    def append(self, record: UsageRecord) -> None:
        """Append one usage record (idempotent by ``usage_id``)."""
        ...

    def get(self, usage_id: str) -> UsageRecord | None:
        """Return the stored record for *usage_id*, or ``None``."""
        ...


@runtime_checkable
class PublicationPort(Protocol):
    """Publishes a completed step/result.  Never writes usage records."""

    def publish(self, job_id: str, attempt: int) -> None:
        """Publish the attempt result; raise :class:`PublicationError` on failure."""
        ...


@runtime_checkable
class ResultAvailabilityPort(Protocol):
    """Reports whether a produced result is prepared/readable (no accounting)."""

    def is_readable(self, job_id: str) -> bool:
        """True when the result body is prepared and can be read."""
        ...


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageIngestReceipt:
    """The outcome of one ledger write attempt.

    ``accepted`` means a new record was appended; ``idempotent`` means the same
    ``usage_id`` was already present (a safe retry).  ``error`` is set when every
    attempt failed — the write is retryable, never a partial state.
    """

    usage_id: str
    accepted: bool
    idempotent: bool = False
    attempts: int = 1
    error: str = ""

    @property
    def recorded(self) -> bool:
        """True when the record is durably present after this write."""
        return self.accepted or self.idempotent


@dataclass(frozen=True)
class PublicationOutcome:
    """The isolated outcome of a publication attempt (never a usage mutation)."""

    job_id: str
    published: bool
    attempts: int = 1
    error: str = ""


@dataclass(frozen=True)
class ResultAccessDecision:
    """Whether a produced result may be read, and why.

    ``readable`` depends only on :class:`ResultAvailabilityPort`.  The
    ``accounting_state`` is carried for diagnostics and can never deny a prepared
    result — ``blocked_by_accounting`` is always ``False`` by construction.
    """

    job_id: str
    readable: bool
    reason: str
    accounting_state: str = "unknown"
    blocked_by_accounting: bool = False


@dataclass(frozen=True)
class DecoupledAttemptOutcome:
    """The combined, order-independent outcome of one decoupled run."""

    job_id: str
    attempt: int
    usage: UsageIngestReceipt
    publication: PublicationOutcome
    result_access: ResultAccessDecision
    order: tuple[str, str]

    @property
    def usage_recorded(self) -> bool:
        return self.usage.recorded


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


class UsageIngestChannel:
    """Write :class:`UsageRecord` values to the ledger, independent of everything else.

    The channel has no reference to a publisher or executor: usage collection can
    run before, during or after a publication (or with none at all), and a failed
    write is retryable without touching science.  Only a real ``UsageRecord`` is
    accepted — a progress/timer/monitoring object can never be appended.
    """

    def __init__(self, ledger: UsageLedgerPort) -> None:
        self._ledger = ledger

    @property
    def ledger(self) -> UsageLedgerPort:
        return self._ledger

    def record(self, record: UsageRecord, *, max_attempts: int = 1) -> UsageIngestReceipt:
        """Append *record*, idempotently, retrying transient ledger failures."""
        if not isinstance(record, UsageRecord):
            raise DecouplingError(
                "usage ledger accepts only a UsageRecord, got "
                f"{type(record).__name__!r} (progress/timer/monitoring signals are "
                "never usage)"
            )
        if int(max_attempts) < 1:
            raise DecouplingError("max_attempts must be >= 1")

        last_error = ""
        for attempt in range(1, int(max_attempts) + 1):
            try:
                if self._ledger.get(record.usage_id) is not None:
                    return UsageIngestReceipt(
                        usage_id=record.usage_id,
                        accepted=False,
                        idempotent=True,
                        attempts=attempt,
                    )
                self._ledger.append(record)
                return UsageIngestReceipt(usage_id=record.usage_id, accepted=True, attempts=attempt)
            except UsageLedgerError as exc:
                last_error = str(exc) or type(exc).__name__
                logger.warning(
                    "usage ledger write failed (attempt %d/%d) for %s: %s",
                    attempt,
                    max_attempts,
                    record.usage_id,
                    last_error,
                )
        return UsageIngestReceipt(
            usage_id=record.usage_id,
            accepted=False,
            attempts=int(max_attempts),
            error=last_error,
        )


class PublicationChannel:
    """Publish results as an isolated step.

    A publication failure is captured as a failed :class:`PublicationOutcome`; it
    never propagates into the caller's usage path and never mutates a
    ``UsageRecord``.
    """

    def __init__(self, publisher: PublicationPort) -> None:
        self._publisher = publisher

    def publish(self, job_id: str, attempt: int, *, max_attempts: int = 1) -> PublicationOutcome:
        """Publish, retrying transient failures; never raises for a typed failure."""
        if int(max_attempts) < 1:
            raise DecouplingError("max_attempts must be >= 1")

        last_error = ""
        for tries in range(1, int(max_attempts) + 1):
            try:
                self._publisher.publish(job_id, attempt)
                return PublicationOutcome(job_id=job_id, published=True, attempts=tries)
            except PublicationError as exc:
                last_error = str(exc) or type(exc).__name__
                logger.warning(
                    "publication failed (attempt %d/%d) for %s: %s",
                    tries,
                    max_attempts,
                    job_id,
                    last_error,
                )
        return PublicationOutcome(
            job_id=job_id, published=False, attempts=int(max_attempts), error=last_error
        )


class ResultAccessGate:
    """Decide result readability from prepared availability alone.

    Accounting/quota state is accepted only to be *observed*; it is never part of
    the decision.  An exhausted balance or a depleted quota therefore cannot lock
    an already-produced result (plan §20.6).
    """

    def __init__(self, availability: ResultAvailabilityPort) -> None:
        self._availability = availability

    def decide(self, job_id: str, *, accounting_state: str = "unknown") -> ResultAccessDecision:
        """Return the read decision for *job_id* (accounting has no vote)."""
        readable = bool(self._availability.is_readable(job_id))
        return ResultAccessDecision(
            job_id=job_id,
            readable=readable,
            reason="prepared" if readable else "not_prepared",
            accounting_state=str(accounting_state),
            blocked_by_accounting=False,
        )


class DecoupledAttemptCoordinator:
    """Run usage ingestion and publication as two independent, order-free steps.

    Whichever step runs first, a failure in one never prevents the other, and the
    result read decision is computed only from availability.  The ``order``
    parameter exists precisely so order-independence is executable and testable.
    """

    _STEPS = ("usage", "publication")

    def __init__(
        self,
        *,
        usage: UsageIngestChannel,
        publication: PublicationChannel,
        results: ResultAccessGate,
    ) -> None:
        self._usage = usage
        self._publication = publication
        self._results = results

    def run(
        self,
        record: UsageRecord,
        *,
        job_id: str,
        attempt: int,
        accounting_state: str = "unknown",
        order: tuple[str, str] = ("usage", "publication"),
    ) -> DecoupledAttemptOutcome:
        """Execute both channels in *order* and report the independent outcomes."""
        if tuple(order) not in self._PERMUTATIONS:
            raise DecouplingError(f"order must be a permutation of {self._STEPS}, got {order!r}")
        usage_receipt: UsageIngestReceipt | None = None
        publication: PublicationOutcome | None = None
        for step in order:
            if step == "usage":
                usage_receipt = self._usage.record(record)
            else:
                publication = self._publication.publish(job_id, attempt)
        assert usage_receipt is not None and publication is not None  # order validated

        result_access = self._results.decide(job_id, accounting_state=accounting_state)
        return DecoupledAttemptOutcome(
            job_id=job_id,
            attempt=int(attempt),
            usage=usage_receipt,
            publication=publication,
            result_access=result_access,
            order=(order[0], order[1]),
        )

    _PERMUTATIONS = (("usage", "publication"), ("publication", "usage"))


# ---------------------------------------------------------------------------
# Trusted-basis guards (negative assertions, executable)
# ---------------------------------------------------------------------------


def is_usage_basis(source: Any) -> bool:
    """True only for a named, trusted :class:`usage.CollectionSource`.

    Progress, browser timers and monitoring samples are never a usage basis.
    """
    text = str(source)
    if text in RejectedLedgerSource.ALL:
        return False
    return text in CollectionSource.ALL


def require_trusted_usage_source(source: Any) -> str:
    """Return the source when trusted, else raise :class:`UntrustedUsageBasisError`.

    This is the single choke point that refuses ``progress`` / ``browser-timer`` /
    ``monitoring-sample`` (and any unknown value) as a usage basis.
    """
    text = str(source)
    if text in RejectedLedgerSource.ALL:
        raise UntrustedUsageBasisError(
            f"{text!r} is a non-ledger signal and can never be a usage basis "
            f"({MONITORING_IS_NOT_LEDGER_NOTE})"
        )
    if text not in CollectionSource.ALL:
        raise UntrustedUsageBasisError(
            f"{text!r} is not a trusted usage CollectionSource; trusted sources are "
            f"{sorted(CollectionSource.ALL)}"
        )
    return text


# ---------------------------------------------------------------------------
# Static audit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecouplingViolation:
    """One static decoupling violation (module + kind + detail)."""

    kind: str
    module: str
    detail: str


@dataclass(frozen=True)
class DecouplingAudit:
    """The result of :func:`audit_decoupling` over source texts."""

    violations: tuple[DecouplingViolation, ...]
    checked_modules: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.violations

    def kinds(self) -> tuple[str, ...]:
        return tuple(item.kind for item in self.violations)


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _identifier_terms(tree: ast.AST) -> set[str]:
    terms: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            terms.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            terms.add(node.attr.lower())
        elif isinstance(node, ast.arg):
            terms.add(node.arg.lower())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            terms.add(node.name.lower())
    return terms


def audit_decoupling(
    *,
    ledger_source: str,
    decoupling_source: str,
    forbidden_import_roots: Iterable[str],
) -> DecouplingAudit:
    """Statically audit the decoupling of the ledger and decoupling modules.

    Reports:

    * a ``forbidden-import`` violation for every import whose root is listed in
      *forbidden_import_roots* in either module, and
    * a ``rejected-signal-term`` violation for every progress/browser-timer/
      monitoring identifier in the **ledger** module (the ledger must have no such
      vocabulary at all).

    Source texts and the forbidden-root vocabulary are injected by the caller so
    the runtime module performs no file I/O and carries no legacy-package literal.
    """
    violations: list[DecouplingViolation] = []
    forbidden = frozenset(str(root) for root in forbidden_import_roots)
    ledger_tree = ast.parse(ledger_source)
    decoupling_tree = ast.parse(decoupling_source)

    for module_name, tree in (("ledger", ledger_tree), ("decoupling", decoupling_tree)):
        for root in sorted(_import_roots(tree)):
            if root in forbidden:
                violations.append(
                    DecouplingViolation(
                        kind=DecouplingViolationKind.FORBIDDEN_IMPORT,
                        module=module_name,
                        detail=root,
                    )
                )

    ledger_terms = _identifier_terms(ledger_tree)
    for term in sorted(_REJECTED_SIGNAL_TERMS):
        if any(term in name for name in ledger_terms):
            violations.append(
                DecouplingViolation(
                    kind=DecouplingViolationKind.REJECTED_SIGNAL_TERM,
                    module="ledger",
                    detail=term,
                )
            )

    return DecouplingAudit(violations=tuple(violations), checked_modules=("ledger", "decoupling"))
