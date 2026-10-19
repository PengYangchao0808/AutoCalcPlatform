"""Runtime environment provisioning — consume Workspace intent → Data namespace.

B09-1 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 48, MU-03).  The
main plan §22.2 assigns "开通个人环境" to three owners with a strict direction:

1. **Workspace** registers the identity / default project and *persists* the
   environment provisioning intent (personal space / default project /
   namespace) plus its environment status.
2. **Runtime** owns the ``ProvisionWorkspace`` use case: it consumes the
   persisted intent (through the read-only :class:`ProvisioningIntentSource`
   port), calls the public Data ``ensure_namespace`` entry point
   (:class:`NamespaceProvisionerPort`), then commits the environment state
   back through the Workspace public command port
   (:class:`EnvironmentStatePort`).
3. **Data** creates the namespace + storage binding exactly once.

Direction is therefore ``Workspace intent → Runtime → Data`` and **never**
``Workspace → Data``.  Runtime may call Workspace public commands, but it never
reaches into the Workspace implementation or the Data store directly — every
effect goes through an injected port.

Hard properties

* **Idempotent.**  Re-provisioning the same intent re-invokes the idempotent
  ``ensure_namespace`` and converges to the *same* namespace; a replay produces
  no new namespace and no duplicate audit entry.  A retry after a failure
  converges without ever creating a second default space.
* **State committed + audited.**  Every outcome commits an explicit
  ``provisioned`` / ``failed`` environment state; a failure is recorded with a
  typed ``error_code`` and an audit entry — never silent.
* **Reconcilable.**  :meth:`ProvisioningService.reconcile` drains the open
  intents (pending / previously failed) and reports provisioned / reconciled /
  failed counts.
* **No system-account side effects.**  This module only uses the three injected
  ports.  It never SSHes, spawns a subprocess, opens a socket or creates a
  system account; the read-only registration surface performs no side effect.

Author: ACP Services (B lane, todo 48).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from acp_services.data.namespaces import (
    EnsureNamespaceRequest,
    NamespaceKind,
    NamespaceRecord,
)
from acp_services.runtime.helpers import utc_now_iso

logger = logging.getLogger(__name__)

__all__ = [
    "ProvisioningState",
    "ProvisioningIntentKind",
    "ProvisioningIntent",
    "ProvisioningAuditEntry",
    "EnvironmentStateRecord",
    "ProvisioningResult",
    "ReconcileReport",
    "ProvisioningError",
    "ProvisioningFailedError",
    "ProvisioningIntentSource",
    "NamespaceProvisionerPort",
    "EnvironmentStatePort",
    "ProvisioningService",
]


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class ProvisioningState(str):
    """Environment provisioning state committed back to Workspace."""

    PENDING = "pending"
    PROVISIONED = "provisioned"
    FAILED = "failed"
    ALL = frozenset({PENDING, PROVISIONED, FAILED})


class ProvisioningIntentKind(str):
    """Kind of openable space the intent asks for (Data namespace vocabulary)."""

    PERSONAL_SPACE = NamespaceKind.PERSONAL_SPACE
    DEFAULT_PROJECT = NamespaceKind.DEFAULT_PROJECT
    NAMESPACE = NamespaceKind.NAMESPACE
    ALL = NamespaceKind.ALL


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ProvisioningError(RuntimeError):
    """Base error for environment provisioning."""


class ProvisioningFailedError(ProvisioningError):
    """A provisioning attempt failed (carries the typed :class:`ProvisioningResult`)."""

    def __init__(self, result: ProvisioningResult) -> None:
        super().__init__(result.detail or f"provisioning failed for {result.intent_id!r}")
        self.result = result
        self.error_code = result.error_code


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvisioningIntent:
    """A persisted Workspace provisioning intent (read-only Runtime view).

    ``namespace_key`` is the stable logical identity shared with Data; when it
    is empty it is derived deterministically from the intent kind + scope, so
    two intents for the same personal space always resolve to the same
    namespace.
    """

    intent_id: str
    principal_id: str
    tenant_id: str = ""
    kind: str = ProvisioningIntentKind.NAMESPACE
    project_id: str = ""
    display_name: str = ""
    root: str = ""
    relative_path: str = ""
    endpoint: str = ""
    namespace_key: str = ""
    requested_at: str = ""

    def __post_init__(self) -> None:
        if not str(self.intent_id).strip():
            raise ProvisioningError("intent_id must be a non-empty string")
        if str(self.kind) not in ProvisioningIntentKind.ALL:
            raise ProvisioningError(
                f"unknown provisioning kind {self.kind!r}; "
                f"expected one of {sorted(ProvisioningIntentKind.ALL)}"
            )

    @property
    def resolved_namespace_key(self) -> str:
        """Stable logical namespace key (explicit value, else derived)."""
        if str(self.namespace_key).strip():
            return str(self.namespace_key)
        material = "\x1f".join(
            [str(self.kind), str(self.tenant_id), str(self.principal_id), str(self.project_id)]
        )
        suffix = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
        return f"{self.kind}:{suffix}"

    def to_namespace_request(self) -> EnsureNamespaceRequest:
        """Project the intent onto the Data ``ensure_namespace`` request."""
        return EnsureNamespaceRequest(
            namespace_key=self.resolved_namespace_key,
            namespace_kind=str(self.kind),
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            principal_id=self.principal_id,
            root=self.root,
            relative_path=self.relative_path,
            endpoint=self.endpoint,
            display_name=self.display_name,
        )


@dataclass(frozen=True)
class EnvironmentStateRecord:
    """Environment state committed back to Workspace (idempotent upsert)."""

    intent_id: str
    state: str
    namespace_id: str = ""
    namespace_key: str = ""
    detail: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if str(self.state) not in ProvisioningState.ALL:
            raise ProvisioningError(
                f"unknown environment state {self.state!r}; "
                f"expected one of {sorted(ProvisioningState.ALL)}"
            )


@dataclass(frozen=True)
class ProvisioningAuditEntry:
    """One audit observation of a provisioning outcome (append-only)."""

    event: str
    intent_id: str
    namespace_key: str
    state: str
    namespace_id: str = ""
    detail: str = ""
    recorded_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "intent_id": self.intent_id,
            "namespace_key": self.namespace_key,
            "state": self.state,
            "namespace_id": self.namespace_id,
            "detail": self.detail,
            "recorded_at": self.recorded_at,
        }


@dataclass(frozen=True)
class ProvisioningResult:
    """Outcome of provisioning one intent.

    ``reconciled`` is true when the intent was already provisioned and a replay
    returned the same namespace without a new namespace or audit entry.
    """

    intent_id: str
    state: str
    namespace_id: str = ""
    namespace_key: str = ""
    detail: str = ""
    error_code: str | None = None
    reconciled: bool = False

    @property
    def ok(self) -> bool:
        return str(self.state) == ProvisioningState.PROVISIONED


@dataclass(frozen=True)
class ReconcileReport:
    """Aggregate outcome of one :meth:`ProvisioningService.reconcile` pass."""

    provisioned: int = 0
    reconciled: int = 0
    failed: int = 0
    skipped: int = 0
    results: tuple[ProvisioningResult, ...] = ()

    @property
    def total(self) -> int:
        return len(self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provisioned": self.provisioned,
            "reconciled": self.reconciled,
            "failed": self.failed,
            "skipped": self.skipped,
            "total": self.total,
            "results": [
                {
                    "intent_id": result.intent_id,
                    "state": result.state,
                    "namespace_id": result.namespace_id,
                    "namespace_key": result.namespace_key,
                    "error_code": result.error_code,
                    "reconciled": result.reconciled,
                }
                for result in self.results
            ],
        }


# ---------------------------------------------------------------------------
# Injected ports
# ---------------------------------------------------------------------------


@runtime_checkable
class ProvisioningIntentSource(Protocol):
    """Workspace-owned, read-only source of persisted provisioning intents."""

    def open_intents(self) -> tuple[ProvisioningIntent, ...]:
        """Return the intents still awaiting provisioning (pending / failed)."""
        ...


@runtime_checkable
class NamespaceProvisionerPort(Protocol):
    """Data-owned ``ensure_namespace`` use case (idempotent, atomic binding).

    Implemented by :class:`acp_services.data.namespaces.NamespaceStore`; kept as
    a Protocol so Runtime never depends on the SQLite implementation.
    """

    def ensure_namespace(self, request: EnsureNamespaceRequest) -> NamespaceRecord:
        """Ensure the namespace + binding for *request*; safe to repeat."""
        ...


@runtime_checkable
class EnvironmentStatePort(Protocol):
    """Workspace-owned public command port for environment state + audit."""

    def get_state(self, intent_id: str) -> EnvironmentStateRecord | None:
        """Return the committed environment state for *intent_id* (or ``None``)."""
        ...

    def commit_state(self, record: EnvironmentStateRecord) -> EnvironmentStateRecord:
        """Idempotently commit environment state (upsert by ``intent_id``)."""
        ...

    def append_audit(self, entry: ProvisioningAuditEntry) -> None:
        """Append one audit observation (append-only)."""
        ...


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ProvisioningService:
    """Runtime ``ProvisionWorkspace`` use case over injected Workspace/Data ports.

    Args:
        intents: Workspace-owned read-only intent source.
        namespaces: Data-owned ``ensure_namespace`` use case.
        states: Workspace-owned environment-state + audit command port.
        clock: Optional ``() -> str`` timestamp override (deterministic tests).
    """

    def __init__(
        self,
        *,
        intents: ProvisioningIntentSource,
        namespaces: NamespaceProvisionerPort,
        states: EnvironmentStatePort,
        clock: Any | None = None,
    ) -> None:
        self._intents = intents
        self._namespaces = namespaces
        self._states = states
        self._clock = clock or utc_now_iso

    # -- provisioning -------------------------------------------------------

    def provision(
        self,
        intent: ProvisioningIntent,
        *,
        raise_on_failure: bool = False,
    ) -> ProvisioningResult:
        """Provision one intent, committing environment state + audit.

        Idempotently calls Data ``ensure_namespace``; on success commits
        ``provisioned`` (a replay of an already-provisioned intent commits
        nothing new and reports ``reconciled``).  On any failure it commits a
        visible ``failed`` state with a typed ``error_code`` and an audit
        entry — the failure is never silent.

        Args:
            intent: The persisted Workspace intent to materialise.
            raise_on_failure: When ``True`` a failure raises
                :class:`ProvisioningFailedError` after the state/audit commit.

        Returns:
            The :class:`ProvisioningResult` (state + namespace identity).
        """
        previous = self._states.get_state(intent.intent_id)
        request = intent.to_namespace_request()
        try:
            record = self._namespaces.ensure_namespace(request)
        except Exception as exc:  # port boundary: any Data error is a visible failure
            detail = f"{type(exc).__name__}: {exc}"
            failed_state = EnvironmentStateRecord(
                intent_id=intent.intent_id,
                state=ProvisioningState.FAILED,
                namespace_key=request.namespace_key,
                detail=detail,
                updated_at=self._clock(),
            )
            self._states.commit_state(failed_state)
            self._states.append_audit(
                ProvisioningAuditEntry(
                    event=ProvisioningState.FAILED,
                    intent_id=intent.intent_id,
                    namespace_key=request.namespace_key,
                    state=ProvisioningState.FAILED,
                    detail=detail,
                    recorded_at=failed_state.updated_at,
                )
            )
            logger.warning(
                "provisioning failed intent=%s key=%s error=%s",
                intent.intent_id,
                request.namespace_key,
                detail,
            )
            result = ProvisioningResult(
                intent_id=intent.intent_id,
                state=ProvisioningState.FAILED,
                namespace_key=request.namespace_key,
                detail=detail,
                error_code=type(exc).__name__,
            )
            if raise_on_failure:
                raise ProvisioningFailedError(result) from exc
            return result

        reconciled = (
            previous is not None
            and str(previous.state) == ProvisioningState.PROVISIONED
            and previous.namespace_id == record.namespace_id
        )
        provisioned_state = EnvironmentStateRecord(
            intent_id=intent.intent_id,
            state=ProvisioningState.PROVISIONED,
            namespace_id=record.namespace_id,
            namespace_key=request.namespace_key,
            updated_at=self._clock(),
        )
        self._states.commit_state(provisioned_state)
        if not reconciled:
            self._states.append_audit(
                ProvisioningAuditEntry(
                    event=ProvisioningState.PROVISIONED,
                    intent_id=intent.intent_id,
                    namespace_key=request.namespace_key,
                    state=ProvisioningState.PROVISIONED,
                    namespace_id=record.namespace_id,
                    recorded_at=provisioned_state.updated_at,
                )
            )
            logger.info(
                "namespace provisioned intent=%s key=%s id=%s",
                intent.intent_id,
                request.namespace_key,
                record.namespace_id,
            )
        return ProvisioningResult(
            intent_id=intent.intent_id,
            state=ProvisioningState.PROVISIONED,
            namespace_id=record.namespace_id,
            namespace_key=request.namespace_key,
            reconciled=reconciled,
        )

    def provision_or_raise(self, intent: ProvisioningIntent) -> ProvisioningResult:
        """Like :meth:`provision` but raise on failure (loud call for callers)."""
        return self.provision(intent, raise_on_failure=True)

    # -- reconciliation -----------------------------------------------------

    def reconcile(
        self,
        intents: tuple[ProvisioningIntent, ...] | None = None,
    ) -> ReconcileReport:
        """Drain the open intents and reconcile every one of them.

        With no explicit ``intents`` the Workspace-owned source decides which
        intents are open (pending / previously failed).  Failed intents are
        retried here; because ``ensure_namespace`` is idempotent a retry never
        creates a second default space.
        """
        pending = self._intents.open_intents() if intents is None else intents
        results: list[ProvisioningResult] = []
        provisioned = reconciled = failed = 0
        for intent in pending:
            result = self.provision(intent)
            results.append(result)
            if result.ok and result.reconciled:
                reconciled += 1
            elif result.ok:
                provisioned += 1
            else:
                failed += 1
        return ReconcileReport(
            provisioned=provisioned,
            reconciled=reconciled,
            failed=failed,
            skipped=0,
            results=tuple(results),
        )

    # -- read-only registration (no side effect) ----------------------------

    def registration_view(self) -> tuple[dict[str, Any], ...]:
        """Return a read-only view of open intents — performs **no** side effect.

        This is the only "registration" surface Runtime exposes here: it reads
        the Workspace source and returns serialisable descriptors.  It never
        calls Data, never SSHes and never creates a system account.
        """
        return tuple(
            {
                "intent_id": intent.intent_id,
                "namespace_key": intent.resolved_namespace_key,
                "kind": intent.kind,
                "principal_id": intent.principal_id,
                "tenant_id": intent.tenant_id,
                "project_id": intent.project_id,
            }
            for intent in self._intents.open_intents()
        )
