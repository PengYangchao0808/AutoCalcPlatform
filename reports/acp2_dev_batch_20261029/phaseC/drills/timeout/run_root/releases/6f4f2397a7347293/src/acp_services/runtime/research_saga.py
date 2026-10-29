"""Cross-database research saga — proposal → approval → submit → TaskRef → evidence.

One research fact is written by **four** databases that never share a
transaction (see :mod:`acp_services.runtime.local_outbox` for the coverage): no
2PC, no distributed lock, no "the platform will fix it later".  What this module
adds is the durable **intent chain** that makes a retry safe across them:

    prepare intent (committed)  ──▶  business write (idempotent by intent key)
                                ──▶  commit intent + its outbox row (one commit)

Every step's intent is persisted *before* the business call, and the business
call of every step is idempotent by that intent:

=================  ==================================================  ============
step               business write                                      idempotency
=================  ==================================================  ============
``proposal``       ``ResearchWriter.create_proposal``                  resource CAS:
                                                                       "already exists" →
                                                                       adopt the stored revision
``approval``       read-only observation of ``ApprovalService``        approval state is the
                                                                       authority; never decided
                                                                       by the saga
``submit``         the injected execution submit port                 M1-02 ``SubmitIntent``
                                                                       key + the Controller
                                                                       submit ledger
``task_ref``       ``ApprovalService.bind_task_ref``                   one TaskRef per
                                                                       approval (evidence only)
``evidence_ref``   ``ResearchWriter.create_record``                     create-once; a retry
                                                                       adopts the same digest
=================  ==================================================  ============

Consequences that the tests pin:

* a break point at **any** step followed by a retry produces **no second fact**
  (one proposal revision, one approval, one task, one TaskRef, one record);
* the intent chain is the reconciliation evidence: a read-only
  :meth:`ResearchSaga.reconcile` never mutates anything, and only the explicit
  :meth:`ResearchSaga.reconcile_action` (``advance``/``abandon``) writes;
* every step names **who retries it** (:data:`STEP_RETRY_OWNER`): a blocked
  approval is the approver's, a failed proposal write is the requester's, a
  TaskRef binding is the Controller's — the platform never silently retries a
  decision it does not own.

Reuse, never a second mechanism: the submit step constructs M1-02's
:class:`~acp_services.runtime.research_session.SubmitIntent` (one state
machine), the approval step reuses :class:`~acp_services.runtime.approvals.ApprovalService`
(B16), the proposal/record steps reuse
:class:`~acp_services.runtime.research_writer.ResearchWriter` (M1-06), and every
digest comes from :mod:`acp_services.runtime.canonical_digest`.

Isolation: stdlib plus first-party ``acp_services``; ``acp`` is never imported.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp_services.contracts.events import EventScope, OwnerType
from acp_services.data.research_resources import (
    PROPOSAL_SCHEMA,
    RESEARCH_RECORD_SCHEMA,
    ResearchResourceError,
    ResearchResourceRecord,
)
from acp_services.runtime.approvals import Approval, ApprovalError, ApprovalService
from acp_services.runtime.canonical_digest import CanonicalDigestError, canonical_digest
from acp_services.runtime.helpers import utc_now_iso as _utc_now_iso
from acp_services.runtime.local_outbox import LocalOutboxTable
from acp_services.runtime.outbox import OutboxEventSpec, OutboxRecord
from acp_services.runtime.research_session import (
    SUBMIT_INTENT_PREPARED,
    SUBMIT_INTENT_SUBMITTED,
    ResearchIdentity,
    ResearchSessionValidationError,
    SubmitIntent,
    TaskBinding,
)
from acp_services.runtime.research_writer import (
    ResearchWriteConflictError,
    ResearchWriteError,
    ResearchWriter,
    proposal_write_digests,
)
from acp_services.workspace.command_identity import (
    CommandIdentity,
    EntryClass,
    PermissionScope,
    SubjectKind,
)

__all__ = [
    "SAGA_INTENT_STATES",
    "SAGA_STEPS",
    "OperationKey",
    "SagaConflictError",
    "SagaError",
    "SagaHeader",
    "SagaIntent",
    "SagaReconciliation",
    "SagaSimulatedCrashError",
    "SagaStatus",
    "SagaStepState",
    "ResearchSaga",
    "SagaStore",
    "STEP_RETRY_OWNER",
    "saga_id_for",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frozen vocabulary
# ---------------------------------------------------------------------------

SAGA_SCHEMA = "acp2.runtime.research-saga.v1"

#: The five steps, in execution order.  A step may only start once every earlier
#: step is ``committed`` (a saga never skips a step to "make progress").
SAGA_STEPS: tuple[str, ...] = ("proposal", "approval", "submit", "task_ref", "evidence_ref")

STEP_PROPOSAL, STEP_APPROVAL, STEP_SUBMIT, STEP_TASK_REF, STEP_EVIDENCE = SAGA_STEPS

SAGA_INTENT_PREPARED = "prepared"
SAGA_INTENT_COMMITTED = "committed"
SAGA_INTENT_FAILED = "failed"
SAGA_INTENT_SUPERSEDED = "superseded"
SAGA_INTENT_STATES: tuple[str, ...] = (
    SAGA_INTENT_PREPARED,
    SAGA_INTENT_COMMITTED,
    SAGA_INTENT_FAILED,
    SAGA_INTENT_SUPERSEDED,
)

SAGA_STATE_OPEN = "open"
SAGA_STATE_COMPLETED = "completed"
SAGA_STATE_BLOCKED = "blocked"
SAGA_STATE_ABANDONED = "abandoned"
SAGA_STATES: tuple[str, ...] = (
    SAGA_STATE_OPEN,
    SAGA_STATE_COMPLETED,
    SAGA_STATE_BLOCKED,
    SAGA_STATE_ABANDONED,
)

#: Who owns the retry of each step (never "the platform, silently").
STEP_RETRY_OWNER: Mapping[str, tuple[str, str]] = {
    STEP_PROPOSAL: (
        "requester",
        "the proposal write is the requester's own CAS; retry the same intent key",
    ),
    STEP_APPROVAL: (
        "approver",
        "only a verified human approver can decide an approval; the saga never does",
    ),
    STEP_SUBMIT: (
        "requester",
        "the submit key is the client's M1-02 intent key; the platform replays it",
    ),
    STEP_TASK_REF: (
        "controller",
        "the Controller records the TaskRef it created under the approved approval",
    ),
    STEP_EVIDENCE: (
        "requester",
        "only the client can cite the digest-bound evidence it read",
    ),
}

#: Event types staged for every intent transition (owner = this slice).
SAGA_EVENT_TYPES: tuple[str, ...] = (
    "research.saga.intent_prepared",
    "research.saga.intent_committed",
    "research.saga.intent_failed",
    "research.saga.abandoned",
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SagaError(RuntimeError):
    """Base error of the research saga; every refusal is named."""

    code = "research_saga_error"
    http_status = 400

    def __init__(self, rule: str, message: str, **params: Any) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message
        self.params: dict[str, Any] = dict(params)

    def detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "params": {"rule": self.rule, **self.params},
        }


class SagaConflictError(SagaError):
    """A stale saga revision, a changed digest or an abandoned chain."""

    code = "revision_conflict"
    http_status = 409


class SagaBlockedError(SagaError):
    """The step cannot run yet and nobody on the platform may force it."""

    code = "resource_not_ready"
    http_status = 409

    def __init__(self, rule: str, message: str, *, step: str, retry_owner: str) -> None:
        super().__init__(rule, message, step=step, retry_owner=retry_owner)
        self.step = step
        self.retry_owner = retry_owner


class SagaSimulatedCrashError(RuntimeError):
    """Test-only break point: the process "dies" at a named step boundary."""


def _require_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SagaError("missing_field", f"{label} is required", field=label)
    return text


def _payload_digest(payload: Mapping[str, Any]) -> str:
    try:
        return canonical_digest(dict(payload))
    except CanonicalDigestError as exc:  # pragma: no cover - caller bug
        raise SagaError(exc.rule, str(exc)) from exc


def saga_id_for(*, project_id: str, proposal_ref: str, plan_digest: str) -> str:
    """Deterministic saga id of one ``(project, proposal, plan)`` identity.

    A retry in a **new process** therefore finds the same saga instead of opening
    a second chain — the identity is derived, never allocated.
    """
    seed = "\0".join([str(project_id), str(proposal_ref), str(plan_digest)])
    return "saga-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SagaHeader:
    """The persisted saga identity + its immutable request material."""

    saga_id: str
    project_id: str
    principal_id: str
    research_id: str
    session_id: str
    round: int
    plan_digest: str
    proposal_ref: str
    approval_ref: str
    state: str
    revision: int
    request: Mapping[str, Any]
    entry_class: str = EntryClass.CONTROLLER
    subject_kind: str = SubjectKind.USER
    permission_scope: str = PermissionScope.LOCAL_SINGLE
    abandon_reason: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def terminal(self) -> bool:
        return self.state in {SAGA_STATE_COMPLETED, SAGA_STATE_ABANDONED}

    def command(self) -> CommandIdentity:
        """Rebuild the requester identity recorded at ``start`` (server-derived).

        The saga never trusts a client-supplied identity: the values come from
        the persisted ``CommandIdentity`` projection of the *verified* start
        command, so a resume in a new process acts as the same principal.  The
        entry class is preserved too, because the M1-02 research identity is
        derived from it — a Controller-side resume must not silently produce a
        different ``research_id`` than the client that opened the round.
        """
        return CommandIdentity(
            principal_id=self.principal_id,
            project_id=self.project_id,
            subject_kind=self.subject_kind,
            entry_class=self.entry_class,
            permission_scope=self.permission_scope,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SAGA_SCHEMA,
            "saga_id": self.saga_id,
            "project_id": self.project_id,
            "principal_id": self.principal_id,
            "research_id": self.research_id,
            "session_id": self.session_id,
            "round": self.round,
            "plan_digest": self.plan_digest,
            "proposal_ref": self.proposal_ref,
            "approval_ref": self.approval_ref,
            "state": self.state,
            "revision": self.revision,
            "terminal": self.terminal,
            "abandon_reason": self.abandon_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class OperationKey:
    """One recorded P2 idempotency key (scope + key + request digest)."""

    scope: str
    key: str
    request_digest: str
    result_ref: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "key": self.key,
            "request_digest": self.request_digest,
            "result_ref": self.result_ref,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class SagaIntent:
    """One persisted step intent (the retry identity of that step)."""

    saga_id: str
    step: str
    intent_key: str
    state: str
    attempts: int
    request_digest: str
    result: Mapping[str, Any]
    result_ref: str = ""
    last_error: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def committed(self) -> bool:
        return self.state == SAGA_INTENT_COMMITTED

    @property
    def retry_owner(self) -> str:
        return STEP_RETRY_OWNER[self.step][0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "intent_key": self.intent_key,
            "state": self.state,
            "attempts": self.attempts,
            "request_digest": self.request_digest,
            "result": dict(self.result),
            "result_ref": self.result_ref,
            "last_error": self.last_error,
            "retry_owner": self.retry_owner,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class SagaStepState:
    """One step's reconciliation row (read-only projection)."""

    step: str
    state: str
    intent_key: str
    attempts: int
    retry_owner: str
    retry_reason: str
    next_action: str
    result_ref: str = ""
    last_error: str = ""
    blocked_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "state": self.state,
            "intent_key": self.intent_key,
            "attempts": self.attempts,
            "retry_owner": self.retry_owner,
            "retry_reason": self.retry_reason,
            "next_action": self.next_action,
            "result_ref": self.result_ref,
            "last_error": self.last_error,
            "blocked_reason": self.blocked_reason,
        }


@dataclass(frozen=True, slots=True)
class SagaStatus:
    """The forward-run result of one saga (header + per-step intents)."""

    header: SagaHeader
    intents: tuple[SagaIntent, ...]
    blocked_step: str = ""
    blocked_reason: str = ""
    outbox_pending: int = 0

    @property
    def terminal(self) -> bool:
        return self.header.terminal

    @property
    def completed(self) -> bool:
        return self.header.state == SAGA_STATE_COMPLETED

    def intent(self, step: str) -> SagaIntent | None:
        for intent in self.intents:
            if intent.step == step:
                return intent
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SAGA_SCHEMA,
            "saga": self.header.to_dict(),
            "steps": [intent.to_dict() for intent in self.intents],
            "blocked_step": self.blocked_step,
            "blocked_reason": self.blocked_reason,
            "outbox_pending": self.outbox_pending,
            "terminal": self.terminal,
        }


@dataclass(frozen=True, slots=True)
class SagaReconciliation:
    """Read-only intent reconciliation of one saga (never mutates anything)."""

    header: SagaHeader
    steps: tuple[SagaStepState, ...]
    outbox_pending: int
    next_action: str
    retry_owner: str

    @property
    def consistent(self) -> bool:
        return not any(step.blocked_reason for step in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SAGA_SCHEMA,
            "saga": self.header.to_dict(),
            "reconcile": [step.to_dict() for step in self.steps],
            "outbox_pending": self.outbox_pending,
            "next_action": self.next_action,
            "retry_owner": self.retry_owner,
            "consistent": self.consistent,
        }


# ---------------------------------------------------------------------------
# Intent store (coordination database)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_sagas (
    saga_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    research_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    round INTEGER NOT NULL DEFAULT 0,
    plan_digest TEXT NOT NULL,
    proposal_ref TEXT NOT NULL,
    approval_ref TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    request_json TEXT NOT NULL,
    entry_class TEXT NOT NULL DEFAULT 'controller',
    subject_kind TEXT NOT NULL DEFAULT 'user',
    permission_scope TEXT NOT NULL DEFAULT 'local-single',
    abandon_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_saga_intents (
    saga_id TEXT NOT NULL,
    step TEXT NOT NULL,
    intent_key TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    request_digest TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    result_ref TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (saga_id, step)
);

CREATE TABLE IF NOT EXISTS research_operation_keys (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    result_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);
"""


def _row_to_header(row: sqlite3.Row) -> SagaHeader:
    return SagaHeader(
        saga_id=str(row["saga_id"]),
        project_id=str(row["project_id"]),
        principal_id=str(row["principal_id"]),
        research_id=str(row["research_id"]),
        session_id=str(row["session_id"]),
        round=int(row["round"]),
        plan_digest=str(row["plan_digest"]),
        proposal_ref=str(row["proposal_ref"]),
        approval_ref=str(row["approval_ref"]),
        state=str(row["state"]),
        revision=int(row["revision"]),
        request=json.loads(row["request_json"]),
        entry_class=str(row["entry_class"]),
        subject_kind=str(row["subject_kind"]),
        permission_scope=str(row["permission_scope"]),
        abandon_reason=str(row["abandon_reason"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_operation_key(row: sqlite3.Row) -> OperationKey:
    return OperationKey(
        scope=str(row["scope"]),
        key=str(row["key"]),
        request_digest=str(row["request_digest"]),
        result_ref=str(row["result_ref"]),
        created_at=str(row["created_at"]),
    )


def _row_to_intent(row: sqlite3.Row) -> SagaIntent:
    result = json.loads(row["result_json"] or "{}")
    return SagaIntent(
        saga_id=str(row["saga_id"]),
        step=str(row["step"]),
        intent_key=str(row["intent_key"]),
        state=str(row["state"]),
        attempts=int(row["attempts"]),
        request_digest=str(row["request_digest"]),
        result=result if isinstance(result, Mapping) else {},
        result_ref=str(row["result_ref"]),
        last_error=str(row["last_error"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class SagaStore:
    """Durable saga header + intent chain + its own local outbox rows.

    Every mutation runs inside one ``BEGIN IMMEDIATE`` together with the outbox
    row of that transition, so an intent can never advance without its event and
    an event can never exist for a rolled-back intent.
    """

    def __init__(self, db_path: str | Path, *, clock: Callable[[], str] | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _utc_now_iso
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._conn.executescript(_SCHEMA_SQL)
            LocalOutboxTable.ensure(self._conn)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover - already closed
                pass

    def __enter__(self) -> SagaStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- transaction helper -------------------------------------------------

    def _write(
        self,
        business: Callable[[sqlite3.Connection], Any],
        *,
        owner_id: str,
        events: Sequence[OutboxEventSpec] = (),
        revision: int = 0,
    ) -> Any:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                result = business(self._conn)
                for spec in events:
                    LocalOutboxTable.stage(
                        self._conn,
                        spec,
                        owner_id=owner_id,
                        revision=revision,
                        emitted_at=self._clock(),
                    )
                self._conn.execute("COMMIT")
            except BaseException:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            return result

    @staticmethod
    def _event(saga_id: str, step: str, event_type: str, **extra: Any) -> OutboxEventSpec:
        payload = {"saga_id": saga_id, "step": step, **extra}
        return OutboxEventSpec(
            event_type=event_type,
            payload=payload,
            scope=EventScope.PROJECT,
            owner_type=OwnerType.DATA,
        )

    # -- header -------------------------------------------------------------

    def header(self, saga_id: str) -> SagaHeader | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM research_sagas WHERE saga_id=?", (str(saga_id),)
            ).fetchone()
        return None if row is None else _row_to_header(row)

    def ensure_saga(
        self,
        *,
        saga_id: str,
        project_id: str,
        principal_id: str,
        plan_digest: str,
        proposal_ref: str,
        request: Mapping[str, Any],
        round_index: int = 0,
        entry_class: str = EntryClass.CONTROLLER,
        subject_kind: str = SubjectKind.USER,
        permission_scope: str = PermissionScope.LOCAL_SINGLE,
    ) -> tuple[SagaHeader, bool]:
        """Create the saga header exactly once; return ``(header, created)``."""
        now = self._clock()
        payload_json = json.dumps(dict(request), sort_keys=True, ensure_ascii=False)

        def business(conn: sqlite3.Connection) -> bool:
            existing = conn.execute(
                "SELECT * FROM research_sagas WHERE saga_id=?", (saga_id,)
            ).fetchone()
            if existing is not None:
                header = _row_to_header(existing)
                if header.plan_digest != plan_digest or header.proposal_ref != proposal_ref:
                    raise SagaConflictError(
                        "saga_identity_conflict",
                        "the saga id already names another plan/proposal identity",
                        existing_plan_digest=header.plan_digest,
                    )
                return False
            conn.execute(
                "INSERT INTO research_sagas (saga_id, project_id, principal_id, research_id,"
                " session_id, round, plan_digest, proposal_ref, approval_ref, state, revision,"
                " request_json, entry_class, subject_kind, permission_scope, created_at,"
                " updated_at) VALUES (?, ?, ?, '', '', ?, ?, ?, '', ?, 1, ?, ?, ?, ?, ?, ?)",
                (
                    saga_id,
                    project_id,
                    principal_id,
                    int(round_index),
                    plan_digest,
                    proposal_ref,
                    SAGA_STATE_OPEN,
                    payload_json,
                    str(entry_class),
                    str(subject_kind),
                    str(permission_scope),
                    now,
                    now,
                ),
            )
            return True

        created = self._write(business, owner_id=saga_id)
        header = self.header(saga_id)
        assert header is not None
        return header, bool(created)

    def update_saga(
        self,
        saga_id: str,
        *,
        expected_revision: int,
        research_id: str | None = None,
        session_id: str | None = None,
        approval_ref: str | None = None,
        state: str = "",
    ) -> SagaHeader:
        """CAS one header projection (identity fields filled in as they are known)."""
        now = self._clock()

        def business(conn: sqlite3.Connection) -> None:
            current = conn.execute(
                "SELECT * FROM research_sagas WHERE saga_id=?", (saga_id,)
            ).fetchone()
            if current is None:
                raise SagaConflictError("saga_not_found", f"saga {saga_id!r} was not found")
            if int(current["revision"]) != int(expected_revision):
                raise SagaConflictError(
                    "stale_saga_revision",
                    "the saga revision changed; re-read before retrying",
                    current_revision=int(current["revision"]),
                )
            if str(current["state"]) == SAGA_STATE_ABANDONED:
                raise SagaConflictError("saga_abandoned", "an abandoned saga is terminal")
            if state and str(state) not in SAGA_STATES:
                raise SagaError("invalid_state", f"unknown saga state {state!r}")
            conn.execute(
                "UPDATE research_sagas SET research_id=?, session_id=?, approval_ref=?,"
                " state=?, revision=revision+1, updated_at=? WHERE saga_id=?",
                (
                    str(current["research_id"]) if research_id is None else research_id,
                    str(current["session_id"]) if session_id is None else session_id,
                    str(current["approval_ref"]) if approval_ref is None else approval_ref,
                    str(current["state"]) if not state else state,
                    now,
                    saga_id,
                ),
            )

        self._write(business, owner_id=saga_id, revision=int(expected_revision) + 1)
        header = self.header(saga_id)
        assert header is not None
        return header

    def abandon(self, saga_id: str, *, expected_revision: int, reason: str) -> SagaHeader:
        now = self._clock()

        def business(conn: sqlite3.Connection) -> None:
            current = conn.execute(
                "SELECT * FROM research_sagas WHERE saga_id=?", (saga_id,)
            ).fetchone()
            if current is None:
                raise SagaConflictError("saga_not_found", f"saga {saga_id!r} was not found")
            if int(current["revision"]) != int(expected_revision):
                raise SagaConflictError(
                    "stale_saga_revision",
                    "the saga revision changed; re-read before retrying",
                    current_revision=int(current["revision"]),
                )
            conn.execute(
                "UPDATE research_sagas SET state=?, abandon_reason=?, revision=revision+1,"
                " updated_at=? WHERE saga_id=?",
                (SAGA_STATE_ABANDONED, str(reason or ""), now, saga_id),
            )
            conn.execute(
                "UPDATE research_saga_intents SET state=?, updated_at=? "
                "WHERE saga_id=? AND state != ?",
                (SAGA_INTENT_SUPERSEDED, now, saga_id, SAGA_INTENT_COMMITTED),
            )

        self._write(
            business,
            owner_id=saga_id,
            events=[
                self._event(
                    saga_id,
                    "",
                    "research.saga.abandoned",
                    reason=str(reason or ""),
                )
            ],
            revision=int(expected_revision) + 1,
        )
        header = self.header(saga_id)
        assert header is not None
        return header

    def merge_request(
        self, saga_id: str, *, patch: Mapping[str, Any], expected_revision: int | None = None
    ) -> SagaHeader:
        """Attach late-discovered material (evidence/record) to an open saga.

        A saga is opened before the round record exists (the record needs the
        approval and the TaskRef the *later* steps produce), so the record
        material arrives after :meth:`ensure_saga`.  Only a key that is still
        empty may be filled; an existing value is never overwritten — a changed
        plan/evidence payload is a typed conflict, not a silent rewrite.
        """
        now = self._clock()

        def business(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT * FROM research_sagas WHERE saga_id=?", (str(saga_id),)
            ).fetchone()
            if row is None:
                raise SagaConflictError("saga_not_found", f"saga {saga_id!r} was not found")
            header = _row_to_header(row)
            if header.state == SAGA_STATE_ABANDONED:
                raise SagaConflictError("saga_abandoned", "an abandoned saga is terminal")
            if expected_revision is not None and int(expected_revision) != header.revision:
                raise SagaConflictError(
                    "stale_saga_revision",
                    "the saga revision changed; re-read before retrying",
                    current_revision=header.revision,
                )
            request = dict(header.request)
            for key, value in patch.items():
                current = request.get(key)
                if current not in (None, {}, "", []):
                    if current != value:
                        raise SagaConflictError(
                            "saga_request_conflict",
                            f"the saga already carries another {key!r} payload",
                            field=str(key),
                        )
                    continue
                request[key] = value
            conn.execute(
                "UPDATE research_sagas SET request_json=?, revision=revision+1, updated_at=? "
                "WHERE saga_id=?",
                (json.dumps(request, sort_keys=True, ensure_ascii=False), now, str(saga_id)),
            )

        self._write(business, owner_id=str(saga_id), revision=(expected_revision or 0) + 1)
        header = self.header(saga_id)
        assert header is not None
        return header

    # -- intents ------------------------------------------------------------

    def intent(self, saga_id: str, step: str) -> SagaIntent | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM research_saga_intents WHERE saga_id=? AND step=?",
                (str(saga_id), str(step)),
            ).fetchone()
        return None if row is None else _row_to_intent(row)

    def intents(self, saga_id: str) -> tuple[SagaIntent, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM research_saga_intents WHERE saga_id=? ORDER BY rowid",
                (str(saga_id),),
            ).fetchall()
        return tuple(_row_to_intent(row) for row in rows)

    def prepare_intent(
        self, saga_id: str, step: str, *, request_digest: str, intent_key: str = ""
    ) -> SagaIntent:
        """Persist (or re-arm) the step intent before the business call.

        A committed intent is returned unchanged (the step is already done); a
        prepared/failed intent is retried with ``attempts + 1``; an intent that
        names another request digest is a typed conflict — the plan changed, so
        the old key cannot be reused (M1-02 ``plan_digest_changed``).
        """
        if step not in SAGA_STEPS:
            raise SagaError("unknown_step", f"unknown saga step {step!r}", step=step)
        key = str(intent_key or f"{saga_id}:{step}")
        now = self._clock()
        attempts_seen: list[int] = []

        def business(conn: sqlite3.Connection) -> None:
            current = conn.execute(
                "SELECT * FROM research_saga_intents WHERE saga_id=? AND step=?",
                (saga_id, step),
            ).fetchone()
            if current is None:
                conn.execute(
                    "INSERT INTO research_saga_intents (saga_id, step, intent_key, state,"
                    " attempts, request_digest, result_json, result_ref, last_error,"
                    " created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, '{}', '', '', ?, ?)",
                    (saga_id, step, key, SAGA_INTENT_PREPARED, request_digest, now, now),
                )
                attempts_seen.append(1)
                return
            intent = _row_to_intent(current)
            if intent.state == SAGA_INTENT_COMMITTED:
                attempts_seen.append(intent.attempts)
                return
            if intent.state == SAGA_INTENT_SUPERSEDED:
                raise SagaConflictError("intent_superseded", "the saga was abandoned")
            if intent.request_digest != request_digest:
                raise SagaConflictError(
                    "plan_digest_changed",
                    "the step request changed; open a new saga instead of reusing the key",
                    step=step,
                    current_digest=intent.request_digest,
                )
            attempts = intent.attempts + 1
            conn.execute(
                "UPDATE research_saga_intents SET state=?, attempts=?, intent_key=?,"
                " updated_at=? WHERE saga_id=? AND step=?",
                (SAGA_INTENT_PREPARED, attempts, intent.intent_key, now, saga_id, step),
            )
            attempts_seen.append(attempts)

        self._write(
            business,
            owner_id=saga_id,
            events=[
                self._event(
                    saga_id,
                    step,
                    "research.saga.intent_prepared",
                    intent_key=key,
                    request_digest=request_digest,
                    attempt=attempts_seen[0] if attempts_seen else 1,
                )
            ],
        )
        intent = self.intent(saga_id, step)
        assert intent is not None
        return intent

    def commit_intent(
        self,
        saga_id: str,
        step: str,
        *,
        result: Mapping[str, Any],
        result_ref: str = "",
    ) -> SagaIntent:
        """Mark the step committed with its business result (one transaction)."""
        now = self._clock()
        result_json = json.dumps(dict(result), sort_keys=True, ensure_ascii=False)
        digest = _payload_digest(result)

        def business(conn: sqlite3.Connection) -> None:
            current = conn.execute(
                "SELECT * FROM research_saga_intents WHERE saga_id=? AND step=?",
                (saga_id, step),
            ).fetchone()
            if current is None:
                raise SagaConflictError("intent_not_prepared", "prepare the intent first")
            intent = _row_to_intent(current)
            if intent.state == SAGA_INTENT_COMMITTED:
                if _payload_digest(intent.result) != digest:
                    raise SagaConflictError(
                        "intent_result_conflict",
                        "the step was already committed with another result",
                        step=step,
                    )
                return
            if intent.state == SAGA_INTENT_SUPERSEDED:
                raise SagaConflictError("intent_superseded", "the saga was abandoned")
            conn.execute(
                "UPDATE research_saga_intents SET state=?, result_json=?, result_ref=?,"
                " last_error='', updated_at=? WHERE saga_id=? AND step=?",
                (SAGA_INTENT_COMMITTED, result_json, str(result_ref or ""), now, saga_id, step),
            )

        self._write(
            business,
            owner_id=saga_id,
            events=[
                self._event(
                    saga_id,
                    step,
                    "research.saga.intent_committed",
                    result_ref=str(result_ref or ""),
                    result_digest=digest,
                )
            ],
        )
        intent = self.intent(saga_id, step)
        assert intent is not None
        return intent

    def fail_intent(
        self, saga_id: str, step: str, *, error: str, retryable: bool = True
    ) -> SagaIntent:
        """Record a failed attempt without losing the intent (retryable)."""
        now = self._clock()

        def business(conn: sqlite3.Connection) -> None:
            current = conn.execute(
                "SELECT * FROM research_saga_intents WHERE saga_id=? AND step=?",
                (saga_id, step),
            ).fetchone()
            if current is None:
                raise SagaConflictError("intent_not_prepared", "prepare the intent first")
            conn.execute(
                "UPDATE research_saga_intents SET state=?, last_error=?, updated_at=? "
                "WHERE saga_id=? AND step=? AND state != ?",
                (SAGA_INTENT_FAILED, str(error), now, saga_id, step, SAGA_INTENT_COMMITTED),
            )

        self._write(
            business,
            owner_id=saga_id,
            events=[
                self._event(
                    saga_id,
                    step,
                    "research.saga.intent_failed",
                    error=str(error),
                    retryable=bool(retryable),
                )
            ],
        )
        intent = self.intent(saga_id, step)
        assert intent is not None
        return intent

    # -- operation idempotency keys -----------------------------------------

    def operation_key(self, *, scope: str, key: str) -> OperationKey | None:
        """Read one recorded P2 idempotency key (no write)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM research_operation_keys WHERE scope=? AND key=?",
                (str(scope), str(key)),
            ).fetchone()
        return None if row is None else _row_to_operation_key(row)

    def claim_operation(
        self, *, scope: str, key: str, request_digest: str, result_ref: str
    ) -> OperationKey:
        """Record one P2 idempotency key exactly once (same key, same digest).

        A key already recorded for a *different* request digest is a typed
        ``idempotency_key_reuse`` conflict — the caller must not silently replay
        another operation's result (contract ``P2-R1`` idempotency rule).
        """
        now = self._clock()

        def business(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT * FROM research_operation_keys WHERE scope=? AND key=?",
                (str(scope), str(key)),
            ).fetchone()
            if row is not None:
                recorded = _row_to_operation_key(row)
                if recorded.request_digest != str(request_digest):
                    raise SagaConflictError(
                        "idempotency_key_reuse",
                        "this idempotency key was used for another request",
                        idempotency_key=str(key),
                    )
                return
            conn.execute(
                "INSERT INTO research_operation_keys (scope, key, request_digest,"
                " result_ref, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(scope), str(key), str(request_digest), str(result_ref or ""), now),
            )

        self._write(business, owner_id=str(scope))
        recorded = self.operation_key(scope=scope, key=key)
        assert recorded is not None
        return recorded

    # -- reads --------------------------------------------------------------

    def list_headers(self, *, project_id: str, limit: int = 100) -> tuple[SagaHeader, ...]:
        """Latest-generation saga headers of one project (bounded, read-only)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM research_sagas WHERE project_id=? "
                "ORDER BY created_at DESC, saga_id LIMIT ?",
                (str(project_id), max(1, int(limit))),
            ).fetchall()
        return tuple(_row_to_header(row) for row in rows)

    # -- outbox delivery half (EventOutboxStorePort) ------------------------

    def pending_outbox(
        self, *, job_id: str | None = None, limit: int | None = None
    ) -> Sequence[OutboxRecord]:
        with self._lock:
            return LocalOutboxTable.pending(self._conn, owner_id=job_id, limit=limit)

    def mark_outbox_delivered(self, event_id: str, *, seq: int, delivered_at: str) -> bool:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                acked = LocalOutboxTable.ack(
                    self._conn, event_id, seq=seq, delivered_at=delivered_at
                )
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            return acked

    def mark_outbox_failed(self, event_id: str, *, error: str) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                LocalOutboxTable.mark_failed(self._conn, event_id, error=error)
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def undelivered_count(self, *, owner_id: str | None = None) -> int:
        with self._lock:
            return LocalOutboxTable.undelivered(self._conn, owner_id=owner_id)


# ---------------------------------------------------------------------------
# The driver
# ---------------------------------------------------------------------------


def _first_mapping(*candidates: Any) -> Mapping[str, Any]:
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            return candidate
    return {}


class ResearchSaga:
    """Drive the five-step cross-database research chain, restartably.

    Args:
        store: The durable header/intent chain (:class:`SagaStore`).
        writer: The M1-06 writer (proposal + record), the only research writer.
        approvals: The B16 approval authority (observed, never decided here).
        submit: Execution submit port; called with an ``idempotency_key`` and must
            replay the same TaskRef for the same key (the Controller ledger does).
        clock: Optional clock (tests).
        fault_hook: Optional ``hook(step, phase)`` break point used by the tests
            to simulate a crash between the business write and the intent commit.
    """

    def __init__(
        self,
        *,
        store: SagaStore,
        writer: ResearchWriter,
        approvals: ApprovalService,
        submit: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        clock: Callable[[], str] | None = None,
        fault_hook: Callable[[str, str], None] | None = None,
    ) -> None:
        if not isinstance(store, SagaStore):
            raise SagaError("invalid_store", "a SagaStore is required")
        if not isinstance(writer, ResearchWriter):
            raise SagaError("invalid_writer", "a ResearchWriter is required")
        if not isinstance(approvals, ApprovalService):
            raise SagaError("invalid_approvals", "an ApprovalService is required")
        if not callable(submit):
            raise SagaError("invalid_submit_port", "a callable submit port is required")
        self._store = store
        self._writer = writer
        self._approvals = approvals
        self._submit = submit
        self._clock = clock or _utc_now_iso
        self._fault = fault_hook

    # -- construction ------------------------------------------------------

    def start(
        self,
        *,
        command: CommandIdentity,
        session_seed: str,
        proposal: Mapping[str, Any],
        plan: Mapping[str, Any],
        budget: Mapping[str, Any] | None = None,
        expires_at: str | None = None,
        record: Mapping[str, Any] | None = None,
        evidence_view: Mapping[str, Any] | None = None,
    ) -> SagaStatus:
        """Open (or reopen) the saga of this ``(project, proposal, plan)`` identity.

        Opening is idempotent: the saga id is derived from the identity, so a
        retry in a new process returns the same chain instead of a second one.
        """
        if not isinstance(command, CommandIdentity):
            raise SagaError("invalid_identity", "a verified CommandIdentity is required")
        document = dict(proposal)
        proposal_ref = _require_text(document.get("proposal_id"), "proposal.proposal_id")
        project = str(command.project_id or "")
        if not project:
            raise SagaError("missing_project", "the verified command names no project")
        document["project_id"] = project
        digests = proposal_write_digests(proposal=document, plan=plan)
        saga_id = saga_id_for(
            project_id=project,
            proposal_ref=proposal_ref,
            plan_digest=digests["plan_digest"],
        )
        request = {
            "schema": SAGA_SCHEMA,
            "session_seed": str(session_seed or ""),
            "proposal": document,
            "plan": dict(plan),
            "budget": dict(budget) if budget else None,
            "expires_at": expires_at,
            "record": dict(record) if isinstance(record, Mapping) else None,
            "evidence_view": dict(evidence_view) if isinstance(evidence_view, Mapping) else None,
            "digests": dict(digests),
        }
        header, created = self._store.ensure_saga(
            saga_id=saga_id,
            project_id=project,
            principal_id=command.principal_id,
            plan_digest=digests["plan_digest"],
            proposal_ref=proposal_ref,
            request=request,
            round_index=int(document.get("round") or 0),
            entry_class=command.entry_class,
            subject_kind=command.subject_kind,
            permission_scope=command.permission_scope,
        )
        if not created:
            patch = {
                key: request[key]
                for key in ("record", "evidence_view", "budget", "expires_at")
                if request.get(key) not in (None, {}, "")
                and header.request.get(key) in (None, {}, "", [])
            }
            if patch:
                header = self._store.merge_request(saga_id, patch=patch)
        return self.status(header.saga_id)

    # -- forward run -------------------------------------------------------

    def advance(self, saga_id: str) -> SagaStatus:
        """Run every step that is runnable, in order, exactly once per fact."""
        header = self._require_header(saga_id)
        if header.state == SAGA_STATE_ABANDONED:
            raise SagaConflictError("saga_abandoned", "an abandoned saga cannot advance")
        blocked_step = ""
        blocked_reason = ""
        for step in SAGA_STEPS:
            try:
                header = self._run_step(header, step)
            except SagaBlockedError as exc:
                blocked_step, blocked_reason = exc.step, exc.rule
                break
            except SagaConflictError:
                raise
            except SagaError as exc:
                self._store.fail_intent(header.saga_id, step, error=str(exc))
                raise
        header = self._require_header(saga_id)
        all_done = all(
            (intent := self._store.intent(saga_id, step)) is not None and intent.committed
            for step in SAGA_STEPS
        )
        if all_done and header.state != SAGA_STATE_COMPLETED:
            header = self._store.update_saga(
                saga_id, expected_revision=header.revision, state=SAGA_STATE_COMPLETED
            )
        elif not all_done and header.state not in {
            SAGA_STATE_COMPLETED,
            SAGA_STATE_ABANDONED,
        }:
            header = self._store.update_saga(
                saga_id,
                expected_revision=header.revision,
                state=SAGA_STATE_BLOCKED if blocked_step else SAGA_STATE_OPEN,
            )
        return self.status(saga_id, blocked_step=blocked_step, blocked_reason=blocked_reason)

    def status(
        self, saga_id: str, *, blocked_step: str = "", blocked_reason: str = ""
    ) -> SagaStatus:
        header = self._require_header(saga_id)
        return SagaStatus(
            header=header,
            intents=self._store.intents(saga_id),
            blocked_step=blocked_step,
            blocked_reason=blocked_reason,
            outbox_pending=self._store.undelivered_count(owner_id=saga_id),
        )

    # -- reconciliation ----------------------------------------------------

    def reconcile(self, saga_id: str) -> SagaReconciliation:
        """Read-only intent reconciliation (never writes, never retries)."""
        header = self._require_header(saga_id)
        intents = {intent.step: intent for intent in self._store.intents(saga_id)}
        steps: list[SagaStepState] = []
        next_action = "no_reconciliation_needed"
        retry_owner = "none"
        for step in SAGA_STEPS:
            owner, reason = STEP_RETRY_OWNER[step]
            intent = intents.get(step)
            if intent is None:
                steps.append(
                    SagaStepState(
                        step=step,
                        state="pending",
                        intent_key=f"{saga_id}:{step}",
                        attempts=0,
                        retry_owner=owner,
                        retry_reason=reason,
                        next_action=f"prepare_and_run:{step}",
                    )
                )
                if next_action == "no_reconciliation_needed":
                    next_action, retry_owner = f"run:{step}", owner
                continue
            if intent.committed:
                steps.append(
                    SagaStepState(
                        step=step,
                        state=SAGA_INTENT_COMMITTED,
                        intent_key=intent.intent_key,
                        attempts=intent.attempts,
                        retry_owner=owner,
                        retry_reason=reason,
                        next_action=f"next_after:{step}",
                        result_ref=intent.result_ref,
                    )
                )
                continue
            if saga_abandoned := (header.state == SAGA_STATE_ABANDONED):
                state = SAGA_INTENT_SUPERSEDED
                action = "none:abandoned"
            else:
                state = intent.state
                action = f"retry:{step}"
            steps.append(
                SagaStepState(
                    step=step,
                    state=state,
                    intent_key=intent.intent_key,
                    attempts=intent.attempts,
                    retry_owner=owner,
                    retry_reason=reason,
                    next_action=action,
                    result_ref=intent.result_ref,
                    last_error=intent.last_error,
                    blocked_reason="" if not saga_abandoned else "saga_abandoned",
                )
            )
            if next_action == "no_reconciliation_needed":
                next_action, retry_owner = action, owner
        if header.state == SAGA_STATE_COMPLETED:
            next_action, retry_owner = "no_reconciliation_needed", "none"
        return SagaReconciliation(
            header=header,
            steps=tuple(steps),
            outbox_pending=self._store.undelivered_count(owner_id=saga_id),
            next_action=next_action,
            retry_owner=retry_owner,
        )

    def reconcile_action(
        self,
        saga_id: str,
        *,
        action: str,
        expected_revision: int,
        reason: str = "",
    ) -> SagaStatus | SagaReconciliation:
        """Apply one explicit reconciliation action (the only mutating entry).

        Args:
            action: ``advance`` (retry the next runnable step with its persisted
                intent) or ``abandon`` (terminal; remaining intents superseded).
            expected_revision: The observed header revision; a stale value is a
                typed conflict (no blind write).
            reason: Required for ``abandon`` (recorded on the header).

        Raises:
            SagaConflictError: stale revision / terminal saga / unknown action.
        """
        header = self._require_header(saga_id)
        if int(expected_revision) != header.revision:
            raise SagaConflictError(
                "stale_saga_revision",
                "the saga revision changed; re-read before reconciling",
                current_revision=header.revision,
            )
        if action == "advance":
            return self.advance(saga_id)
        if action == "abandon":
            if not str(reason or "").strip():
                raise SagaError("missing_reason", "abandon requires a reason")
            self._store.abandon(
                saga_id, expected_revision=header.revision, reason=str(reason).strip()
            )
            return self.reconcile(saga_id)
        raise SagaError("unknown_action", f"unsupported reconcile action {action!r}")

    # -- internals ---------------------------------------------------------

    def _require_header(self, saga_id: str) -> SagaHeader:
        header = self._store.header(str(saga_id))
        if header is None:
            raise SagaConflictError("saga_not_found", f"saga {saga_id!r} was not found")
        return header

    def _run_step(self, header: SagaHeader, step: str) -> SagaHeader:
        request = header.request
        payload_digest = _payload_digest(
            {
                "step": step,
                "plan_digest": header.plan_digest,
                "proposal_ref": header.proposal_ref,
                "request": request.get("digests"),
            }
        )
        intent = self._store.prepare_intent(header.saga_id, step, request_digest=payload_digest)
        if intent.committed:
            return header
        self._break_point(step, "after_prepare")
        try:
            result, header = self._apply(header, step)
        except SagaBlockedError:
            raise
        self._break_point(step, "after_business")
        self._store.commit_intent(
            header.saga_id,
            step,
            result=result,
            result_ref=str(result.get("result_ref") or ""),
        )
        return header

    def _apply(self, header: SagaHeader, step: str) -> tuple[Mapping[str, Any], SagaHeader]:
        if step == STEP_PROPOSAL:
            return self._apply_proposal(header)
        if step == STEP_APPROVAL:
            return self._apply_approval(header)
        if step == STEP_SUBMIT:
            return self._apply_submit(header)
        if step == STEP_TASK_REF:
            return self._apply_task_ref(header)
        return self._apply_evidence(header)

    def _apply_proposal(self, header: SagaHeader) -> tuple[Mapping[str, Any], SagaHeader]:
        request = header.request
        command = header.command()
        proposal = _first_mapping(request.get("proposal"))
        plan = _first_mapping(request.get("plan"))
        budget = request.get("budget")
        try:
            written = self._writer.create_proposal(
                command=command,
                session_seed=str(request.get("session_seed") or ""),
                proposal=proposal,
                plan=plan,
                budget=budget if isinstance(budget, Mapping) else None,
                expires_at=request.get("expires_at"),
            )
            approval = written.approval
            record = written.record
            result = {
                "proposal_ref": header.proposal_ref,
                "revision": record.revision,
                "digest": record.digest,
                "approval_ref": approval.approval_id,
                "approval_state": approval.state,
                "plan_digest": written.digests["plan_digest"],
                "proposal_digest": written.digests["proposal_digest"],
                "research_id": written.identity.research_id,
                "session_id": written.identity.session_id,
                "result_ref": record.resource_id,
            }
        except ResearchWriteConflictError as exc:
            # A break point between the create and the intent commit: the stored
            # revision is the fact, so adopt it instead of writing a second one.
            if exc.current is None:
                raise
            adopted = self._adopt_proposal(header, exc.current)
            result = adopted
        header = self._store.update_saga(
            header.saga_id,
            expected_revision=header.revision,
            research_id=str(result.get("research_id") or ""),
            session_id=str(result.get("session_id") or ""),
            approval_ref=str(result.get("approval_ref") or ""),
        )
        return result, header

    def _adopt_proposal(
        self, header: SagaHeader, current: ResearchResourceRecord
    ) -> Mapping[str, Any]:
        if current.schema != PROPOSAL_SCHEMA:
            raise SagaConflictError(
                "proposal_schema_conflict",
                "the existing resource is not a proposal",
            )
        document = current.document
        if str(document.get("proposal_id") or "") != header.proposal_ref:
            raise SagaConflictError(
                "proposal_identity_conflict", "the stored proposal names another id"
            )
        digests = _first_mapping(header.request.get("digests"))
        declared = str(document.get("plan_definition_digest") or "")
        expected_science = str(digests.get("science_digest") or "")
        if expected_science and declared and declared != expected_science:
            raise SagaConflictError(
                "plan_digest_changed",
                "the stored proposal binds another science digest; open a new round",
            )
        approval = self._live_approval(header.project_id, header.proposal_ref)
        if approval is None:
            raise SagaConflictError(
                "approval_missing",
                "the adopted proposal has no live approval; re-request it explicitly",
            )
        # The research identity is derived from the verified command + seed, so an
        # adopted revision carries the same identity the create would have used.
        identity = ResearchIdentity.allocate(
            header.command(), session_seed=str(header.request.get("session_seed") or "")
        )
        return {
            "proposal_ref": header.proposal_ref,
            "revision": current.revision,
            "digest": current.digest,
            "approval_ref": approval.approval_id,
            "approval_state": approval.state,
            "plan_digest": approval.plan_digest,
            "proposal_digest": approval.proposal_digest,
            "research_id": identity.research_id,
            "session_id": identity.session_id,
            "result_ref": current.resource_id,
            "adopted": True,
        }

    def _live_approval(self, project_id: str, proposal_ref: str) -> Approval | None:
        for state in ("approved", "pending", "draft"):
            matches = self._approvals.query(
                project_id=project_id, proposal_ref=proposal_ref, status=state, limit=1
            )
            if matches:
                return matches[0]
        return None

    def _apply_approval(self, header: SagaHeader) -> tuple[Mapping[str, Any], SagaHeader]:
        approval_ref = str(header.approval_ref or "")
        if not approval_ref:
            raise SagaError("approval_missing", "the proposal step bound no approval")
        approval = self._approvals.get(approval_ref)
        if approval is None:
            raise SagaBlockedError(
                "approval_missing",
                "the approval record is gone",
                step=STEP_APPROVAL,
                retry_owner=STEP_RETRY_OWNER[STEP_APPROVAL][0],
            )
        if approval.project_id != header.project_id:
            raise SagaError("project_scope_mismatch", "the approval belongs to another project")
        if approval.state != "approved":
            raise SagaBlockedError(
                "approval_not_approved",
                f"the approval state is {approval.state!r}; only a human approver can decide",
                step=STEP_APPROVAL,
                retry_owner=STEP_RETRY_OWNER[STEP_APPROVAL][0],
            )
        if approval.task_ref:
            # The approved approval is already executed: never submit a second task.
            return (
                {
                    "approval_ref": approval.approval_id,
                    "state": approval.state,
                    "revision": approval.revision,
                    "plan_digest": approval.plan_digest,
                    "task_ref": dict(approval.task_ref),
                    "result_ref": approval.approval_id,
                    "already_submitted": True,
                },
                header,
            )
        return (
            {
                "approval_ref": approval.approval_id,
                "state": approval.state,
                "revision": approval.revision,
                "plan_digest": approval.plan_digest,
                "result_ref": approval.approval_id,
            },
            header,
        )

    def _apply_submit(self, header: SagaHeader) -> tuple[Mapping[str, Any], SagaHeader]:
        request = header.request
        digests = _first_mapping(request.get("digests"))
        plan = _first_mapping(request.get("plan"))
        intent = SubmitIntent(
            intent_key=f"{header.saga_id}:submit",
            project_id=header.project_id,
            research_id=header.research_id,
            session_id=header.session_id,
            round=header.round,
            plan_digest=header.plan_digest,
            approval_ref=header.approval_ref,
            state=SUBMIT_INTENT_PREPARED,
        )
        payload = {
            "schema": SAGA_SCHEMA,
            "workflow": str(plan.get("workflow") or ""),
            "project_id": header.project_id,
            "proposal_ref": header.proposal_ref,
            "proposal_digest": str(digests.get("proposal_digest") or ""),
            "plan_digest": header.plan_digest,
            "approval_ref": header.approval_ref,
            "idempotency_key": intent.intent_key,
            "submit_intent": intent.to_dict(),
            "plan": dict(plan),
        }
        result = self._submit(payload)
        binding = self._binding(header, result)
        submitted = intent.with_state(SUBMIT_INTENT_SUBMITTED)
        return (
            {
                "intent_key": intent.intent_key,
                "submit_intent": submitted.to_dict(),
                "task_ref": binding.to_dict(),
                "result_ref": f"{binding.project_id}:{binding.job_id}@{binding.attempt}",
            },
            header,
        )

    def _binding(self, header: SagaHeader, result: Mapping[str, Any]) -> TaskBinding:
        task_ref = _first_mapping(result.get("task_ref"), result)
        job_id = str(task_ref.get("task_id") or task_ref.get("job_id") or task_ref.get("id") or "")
        attempt = task_ref.get("attempt", result.get("attempt", 1))
        release_id = str(
            task_ref.get("release_id")
            or _first_mapping(task_ref.get("release")).get("release_id")
            or result.get("release_id")
            or ""
        )
        if not job_id or not release_id:
            raise SagaError(
                "submit_missing_task_ref",
                "the submit port returned no TaskRef (job id + release id)",
                job_id=job_id,
            )
        try:
            return TaskBinding(
                project_id=str(task_ref.get("project_id") or header.project_id),
                job_id=job_id,
                attempt=int(attempt),
                release_id=release_id,
            )
        except ResearchSessionValidationError as exc:
            raise SagaError(exc.rule, str(exc)) from exc

    def _apply_task_ref(self, header: SagaHeader) -> tuple[Mapping[str, Any], SagaHeader]:
        submit_intent = self._store.intent(header.saga_id, STEP_SUBMIT)
        if submit_intent is None or not submit_intent.committed:
            raise SagaError("submit_not_committed", "the submit step is not committed")
        binding = _first_mapping(submit_intent.result.get("task_ref"))
        task_ref = {
            "task_id": str(binding.get("job_id") or ""),
            "job_id": str(binding.get("job_id") or ""),
            "attempt": int(binding.get("attempt") or 1),
            "release": {"release_id": str(binding.get("release_id") or "")},
            "project_id": header.project_id,
        }
        try:
            approval = self._approvals.bind_task_ref(
                header.approval_ref, task_ref, actor_principal=header.principal_id
            )
        except ApprovalError as exc:
            raise SagaError(exc.rule, exc.message) from exc
        return (
            {
                "approval_ref": approval.approval_id,
                "approval_revision": approval.revision,
                "task_ref": dict(approval.task_ref or task_ref),
                "result_ref": str(task_ref["task_id"]),
            },
            header,
        )

    def _apply_evidence(self, header: SagaHeader) -> tuple[Mapping[str, Any], SagaHeader]:
        request = header.request
        record_payload = request.get("record")
        evidence_view = request.get("evidence_view")
        command = header.command()
        session_seed = str(request.get("session_seed") or "")
        if isinstance(record_payload, Mapping):
            payload = dict(record_payload)
            try:
                written = self._writer.create_record(
                    command=command,
                    session_seed=session_seed,
                    record=payload,
                    evidence_view=evidence_view if isinstance(evidence_view, Mapping) else None,
                )
            except ResearchWriteConflictError as exc:
                if exc.current is None or exc.current.schema != RESEARCH_RECORD_SCHEMA:
                    raise
                return (
                    {
                        "record_ref": exc.current.resource_id,
                        "record_revision": exc.current.revision,
                        "digest": exc.current.digest,
                        "decision": str(exc.current.document.get("decision") or ""),
                        "result_ref": exc.current.resource_id,
                        "adopted": True,
                    },
                    header,
                )
            except ResearchResourceError as exc:  # pragma: no cover - typed upstream
                raise SagaError(exc.code, str(exc)) from exc
            return (
                {
                    "record_ref": written.record.resource_id,
                    "record_revision": written.record.revision,
                    "digest": written.record.digest,
                    "decision": written.decision,
                    "projection_digest": written.projection_digest,
                    "result_ref": written.record.resource_id,
                },
                header,
            )
        # No evidence material in the request: adopt an existing record, else the
        # client must cite the evidence it read (the saga never invents one).
        if header.research_id:
            try:
                existing = self._writer.read_record(
                    principal_id=header.principal_id,
                    project_id=header.project_id,
                    research_id=header.research_id,
                )
            except ResearchWriteError:
                existing = None
            if existing is not None:
                return (
                    {
                        "record_ref": existing.resource_id,
                        "record_revision": existing.revision,
                        "digest": existing.digest,
                        "decision": str(existing.document.get("decision") or ""),
                        "result_ref": existing.resource_id,
                        "adopted": True,
                    },
                    header,
                )
        raise SagaBlockedError(
            "evidence_not_cited",
            "no digest-bound evidence was cited for this round",
            step=STEP_EVIDENCE,
            retry_owner=STEP_RETRY_OWNER[STEP_EVIDENCE][0],
        )

    def _break_point(self, step: str, phase: str) -> None:
        if self._fault is not None:
            self._fault(step, phase)
