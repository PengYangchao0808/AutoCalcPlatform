"""Research application service — the ten P2 operations (M2-01c, G02/G14).

This is the **application use case** layer the P2 contract
(``runtime/p2-research-operations.v1.json``) names for each row.  Every entry
point — the HTTP routes (``acp.api.v2_research``), the ``ControllerClient`` P2
methods and the ten MCP research tools — calls one method here; none of them
writes a store directly and none of them re-derives a digest, an identity or a
state machine:

=======================================  ==========================================
P2 row   tool                            use case (single implementation)
=======================================  ==========================================
P2-R1    proposal create/read/update-cas ``ResearchWriter`` (M1-06) over
                                          ``ResearchResourceStore`` (B18 Data)
P2-R2    approval request/query          ``ApprovalService.request/query`` (B16)
P2-R3    approval decide/revoke          ``ApprovalService.approve/reject/revoke``
P2-R4    wait/snapshot                   bounded read over the authorities
                                          (approval / saga / record / proposal)
P2-R5    resume                          saga reconciliation + idempotent
                                          ``advance`` (never a second attempt)
=======================================  ==========================================

Idempotency: a caller-supplied ``idempotency_key`` is recorded with the request
digest in the coordination database (:class:`SagaStore.claim_operation`).  The
same key with the same digest replays the recorded reference; the same key with
a different digest is ``revision_conflict``/``idempotency_key_reuse`` — never a
second resource.  The submit step keeps M1-02's :class:`SubmitIntent` as its only
key (no second submit-idempotency mechanism).

Waiting is **bounded**: ``max_wait_s <= 60`` and ``limit <= 200``; a cursor is
opaque, time-bounded (:data:`WAIT_CURSOR_MAX_AGE_S`) and expiry is a typed
``cursor_expired`` refusal (snapshot reset), never a silent gap.

Isolation: stdlib plus first-party ``acp_services``; ``acp`` is never imported.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from acp_services.data.research_resources import (
    PROPOSAL_SCHEMA,
    RESEARCH_RECORD_SCHEMA,
    ResearchResourceError,
    ResearchResourceStore,
    research_resource_key,
)
from acp_services.runtime.approvals import Approval, ApprovalError, ApprovalService
from acp_services.runtime.canonical_digest import (
    CanonicalDigestError,
    canonical_digest,
    frozen_plan_definition,
    proposal_plan_definition_digest,
)
from acp_services.runtime.canonical_digest import plan_digest as canonical_plan_digest
from acp_services.runtime.canonical_digest import (
    proposal_digest as canonical_proposal_digest,
)
from acp_services.runtime.research_saga import (
    ResearchSaga,
    SagaConflictError,
    SagaError,
    SagaIntent,
    SagaReconciliation,
    SagaStore,
    saga_id_for,
)
from acp_services.runtime.research_writer import (
    ResearchWriteConflictError,
    ResearchWriteError,
    ResearchWriter,
    proposal_write_digests,
)
from acp_services.workspace.command_identity import CommandIdentity

__all__ = [
    "APPROVAL_DECISIONS",
    "WAIT_CURSOR_MAX_AGE_S",
    "WAIT_DEFAULT_LIMIT",
    "WAIT_MAX_LIMIT",
    "WAIT_MAX_WAIT_S",
    "P2_SERVICE_METHODS",
    "ResearchOperationError",
    "ResearchOperations",
    "decode_wait_cursor",
    "encode_wait_cursor",
]

logger = logging.getLogger(__name__)

#: The ten P2 operations and the application-service method of each one.  The
#: Controller client spells its transport methods differently
#: (``P2_OPERATION_METHODS``), so each layer keeps one name per operation and the
#: M2-01c guard asserts both maps cover exactly the ten promised tools.
P2_SERVICE_METHODS: dict[str, str] = {
    "proposal_create": "proposal_create",
    "proposal_read": "proposal_read",
    "proposal_update_cas": "proposal_update_cas",
    "approval_request": "approval_request",
    "approval_query": "approval_query",
    "approval_decide": "approval_decide",
    "approval_revoke": "approval_revoke",
    "wait": "research_wait",
    "snapshot": "research_snapshot",
    "resume": "research_resume",
}

#: Frozen wait bounds (``p2-research-operations.v1.json::waits.bounds``).
WAIT_MAX_WAIT_S = 60
WAIT_MAX_LIMIT = 200
WAIT_DEFAULT_LIMIT = 50
#: Frozen cursor age (``waits.cursor.max_age_s``); expiry is an explicit reset.
WAIT_CURSOR_MAX_AGE_S = 900

#: The decision vocabulary accepted by the decide tool (contract: approve/reject).
APPROVAL_DECISIONS: tuple[str, ...] = ("approve", "reject")
_DECISION_TO_STATE: dict[str, str] = {"approve": "approved", "reject": "rejected"}

_WAIT_CURSOR_VERSION = "w1"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ResearchOperationError(RuntimeError):
    """A typed refusal of one P2 operation (never a silent coercion)."""

    code = "research_operation_error"
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
            "retryable": self.http_status in {409, 503},
        }


class ResearchOperationDeniedError(ResearchOperationError):
    """The verified identity/project may not perform this operation."""

    code = "access_denied"
    http_status = 403


class ResearchOperationConflictError(ResearchOperationError):
    """A stale revision, a reused key with another digest or a decided approval."""

    code = "revision_conflict"
    http_status = 409


class ResearchOperationNotReadyError(ResearchOperationError):
    """A bounded wait/snapshot/resume target is not ready (retryable)."""

    code = "resource_not_ready"
    http_status = 409


# ---------------------------------------------------------------------------
# Wait cursor (opaque, time-bounded)
# ---------------------------------------------------------------------------


def encode_wait_cursor(*, snapshot_revision: str, project_id: str, issued_at: float) -> str:
    """Encode one opaque bounded-wait cursor (never a task identity)."""
    payload = {
        "v": _WAIT_CURSOR_VERSION,
        "rev": str(snapshot_revision or ""),
        "project": str(project_id or ""),
        "at": round(float(issued_at), 3),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_wait_cursor(
    token: str, *, project_id: str, now: float, max_age_s: int = WAIT_CURSOR_MAX_AGE_S
) -> Mapping[str, Any]:
    """Decode a cursor, refusing malformed, foreign or expired tokens.

    Raises:
        ResearchOperationError: ``input_invalid`` (malformed) /
            ``project_scope_mismatch`` (another project) /
            :class:`ResearchOperationNotReadyError` ``cursor_expired``.
    """
    text = str(token or "").strip()
    if not text:
        raise ResearchOperationError("input_invalid", "cursor must be non-empty")
    try:
        payload = json.loads(base64.urlsafe_b64decode(text.encode("ascii")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ResearchOperationError("input_invalid", f"cursor is not decodable: {exc}") from None
    if not isinstance(payload, Mapping) or payload.get("v") != _WAIT_CURSOR_VERSION:
        raise ResearchOperationError("input_invalid", "cursor version is not supported")
    if str(payload.get("project") or "") != str(project_id or ""):
        raise ResearchOperationDeniedError(
            "project_scope_mismatch", "the cursor belongs to another project"
        )
    issued = payload.get("at")
    if not isinstance(issued, int | float):
        raise ResearchOperationError("input_invalid", "cursor carries no issue time")
    if now - float(issued) > float(max_age_s):
        raise ResearchOperationNotReadyError(
            "cursor_expired",
            "the wait cursor expired; re-read the snapshot instead of replaying it",
            max_age_s=int(max_age_s),
        )
    return payload


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SnapshotEntry:
    kind: str
    ref: str
    state: str
    revision: int
    digest: str = ""
    observed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "state": self.state,
            "revision": self.revision,
            "digest": self.digest,
            "observed": self.observed,
        }


class ResearchOperations:
    """The ten P2 operations over the existing authorities (no second store)."""

    def __init__(
        self,
        *,
        writer: ResearchWriter,
        resources: ResearchResourceStore,
        approvals: ApprovalService,
        sagas: SagaStore | None = None,
        saga: ResearchSaga | None = None,
        submit: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if not isinstance(writer, ResearchWriter):
            raise ResearchOperationError("invalid_service", "a ResearchWriter is required")
        if not isinstance(resources, ResearchResourceStore):
            raise ResearchOperationError("invalid_service", "a ResearchResourceStore is required")
        if not isinstance(approvals, ApprovalService):
            raise ResearchOperationError("invalid_service", "an ApprovalService is required")
        self._writer = writer
        self._resources = resources
        self._approvals = approvals
        self._sagas = sagas
        self._submit = submit
        self._clock = clock or time.time
        self._sleeper = sleeper or time.sleep
        self._saga = saga
        if self._saga is None and sagas is not None:
            self._saga = ResearchSaga(
                store=sagas, writer=writer, approvals=approvals, submit=submit or _no_submit
            )

    # -- P2-R1: proposal create/read/update-cas ---------------------------

    def proposal_create(
        self,
        *,
        command: CommandIdentity,
        proposal: Mapping[str, Any],
        plan: Mapping[str, Any],
        session_seed: str = "",
        budget: Mapping[str, Any] | None = None,
        expires_at: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create revision 1 and request its double-digest approval."""
        document = self._scoped_document(command, proposal)
        digests = proposal_write_digests(proposal=document, plan=plan)
        resource_id = research_resource_key(
            str(command.project_id or ""), "proposal", str(document["proposal_id"])
        )
        replay = self._replay(
            command,
            operation="proposal_create",
            key=idempotency_key,
            request_digest=_request_digest(document, plan),
        )
        if replay is not None:
            stored = self._read_resource(command, resource_id)
            return self._proposal_payload(command, stored, digests=digests, replayed=True)
        try:
            result = self._writer.create_proposal(
                command=command,
                session_seed=session_seed,
                proposal=document,
                plan=plan,
                budget=budget,
                expires_at=expires_at,
            )
        except ResearchWriteError as exc:
            raise _write_error(exc) from exc
        self._remember(
            command,
            operation="proposal_create",
            key=idempotency_key,
            request_digest=_request_digest(document, plan),
            result_ref=result.record.resource_id,
        )
        payload = result.to_dict()
        payload["replayed"] = False
        return payload

    def proposal_read(
        self,
        *,
        command: CommandIdentity,
        proposal_ref: str,
        revision: int | None = None,
    ) -> dict[str, Any]:
        """Read one stored proposal revision (verbatim document + digest)."""
        resource_id = research_resource_key(
            str(command.project_id or ""), "proposal", str(proposal_ref)
        )
        record = self._read_resource(command, resource_id, revision=revision)
        if record.schema != PROPOSAL_SCHEMA:
            raise ResearchOperationConflictError(
                "proposal_schema_conflict", "the addressed resource is not a proposal"
            )
        return {
            "schema": PROPOSAL_SCHEMA,
            "proposal_ref": str(record.document.get("proposal_id") or ""),
            "proposal": dict(record.document),
            "proposal_digest": record.digest,
            "revision": record.revision,
            "unknown_fields_preserved": True,
        }

    def proposal_update_cas(
        self,
        *,
        command: CommandIdentity,
        proposal_ref: str,
        expected_revision: int,
        proposal: Mapping[str, Any],
        plan: Mapping[str, Any],
        session_seed: str = "",
        budget: Mapping[str, Any] | None = None,
        expires_at: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """CAS one proposal revision; a changed plan digest opens a new round."""
        document = self._scoped_document(command, proposal)
        document["proposal_id"] = str(proposal_ref)
        digests = proposal_write_digests(proposal=document, plan=plan)
        replay = self._replay(
            command,
            operation="proposal_update_cas",
            key=idempotency_key,
            request_digest=_request_digest(document, plan, extra=str(expected_revision)),
        )
        if replay is not None:
            resource_id = research_resource_key(
                str(command.project_id or ""), "proposal", str(proposal_ref)
            )
            stored = self._read_resource(command, resource_id)
            return self._proposal_payload(command, stored, digests=digests, replayed=True)
        try:
            result = self._writer.update_proposal(
                command=command,
                session_seed=session_seed,
                proposal_id=str(proposal_ref),
                expected_revision=int(expected_revision),
                proposal=document,
                plan=plan,
                budget=budget,
                expires_at=expires_at,
            )
        except ResearchWriteError as exc:
            raise _write_error(exc) from exc
        self._remember(
            command,
            operation="proposal_update_cas",
            key=idempotency_key,
            request_digest=_request_digest(document, plan, extra=str(expected_revision)),
            result_ref=result.record.resource_id,
        )
        payload = result.to_dict()
        payload["replayed"] = False
        return payload

    # -- P2-R2/R3: approval request / query / decide / revoke -------------

    def approval_request(
        self,
        *,
        command: CommandIdentity,
        proposal_ref: str,
        plan: Mapping[str, Any] | None = None,
        proposal: Mapping[str, Any] | None = None,
        plan_digest: str | None = None,
        proposal_digest: str | None = None,
        science_digest: str | None = None,
        budget: Mapping[str, Any] | None = None,
        allowed_change_range: Mapping[str, Any] | None = None,
        expires_at: str | None = None,
        idempotency_key: str | None = None,
        comment: str = "",
    ) -> dict[str, Any]:
        """Request (or replay) the approval of one frozen plan."""
        project = self._project(command)
        resolved_plan_digest = self._plan_digest(plan=plan, declared=plan_digest)
        resolved_proposal_digest = self._proposal_digest(
            proposal=proposal, declared=proposal_digest, plan_digest=resolved_plan_digest
        )
        resolved_science = self._science_digest(proposal=proposal, declared=science_digest)
        definition = (
            frozen_plan_definition(
                workflow=str(plan.get("workflow") or ""),
                input=plan.get("input") if isinstance(plan.get("input"), Mapping) else {},
                method=plan.get("method") if isinstance(plan.get("method"), Mapping) else {},
                resources=(
                    plan.get("resources") if isinstance(plan.get("resources"), Mapping) else {}
                ),
            )
            if isinstance(plan, Mapping)
            else None
        )
        replay = self._replay(
            command,
            operation="approval_request",
            key=idempotency_key,
            request_digest="\0".join(
                [proposal_ref, resolved_plan_digest, resolved_proposal_digest]
            ),
        )
        if replay is not None:
            approval = self._approvals.get(str(replay))
            if approval is not None:
                return _approval_payload(approval, replayed=True, comment=comment)
        try:
            approval, replayed = self._approvals.request(
                proposal_ref=str(proposal_ref),
                proposal_digest=resolved_proposal_digest,
                plan_digest=resolved_plan_digest,
                project_id=project,
                requester_principal=command.principal_id,
                plan_definition=definition,
                budget=budget,
                allowed_change_range=allowed_change_range,
                expires_at=expires_at,
                idempotency_key=idempotency_key,
                science_digest=resolved_science,
            )
        except ApprovalError as exc:
            raise _approval_error(exc) from exc
        self._remember(
            command,
            operation="approval_request",
            key=idempotency_key,
            request_digest="\0".join(
                [proposal_ref, resolved_plan_digest, resolved_proposal_digest]
            ),
            result_ref=approval.approval_id,
        )
        return _approval_payload(approval, replayed=replayed, comment=comment)

    def approval_query(
        self,
        *,
        command: CommandIdentity,
        approval_ref: str | None = None,
        proposal_ref: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Bounded query over the authoritative approval records."""
        bounded = _bounded_limit(limit)
        project = self._project(command)
        if cursor:
            # The cursor is the last observed approval revision of this project.
            offset = decode_wait_cursor(cursor, project_id=project, now=self._clock()).get(
                "rev", ""
            )
            if not str(offset).isdigit():
                raise ResearchOperationError("input_invalid", "cursor is not an approval cursor")
        try:
            approvals = list(
                self._approvals.query(
                    project_id=project,
                    principal_id=command.principal_id,
                    approval_ref=approval_ref,
                    proposal_ref=proposal_ref,
                    status=status,
                    limit=bounded,
                )
            )
        except ApprovalError as exc:
            raise _approval_error(exc) from exc
        records = [_approval_payload(item) for item in approvals]
        snapshot_revision = str(max((item.revision for item in approvals), default=0))
        next_cursor = (
            encode_wait_cursor(
                snapshot_revision=snapshot_revision,
                project_id=project,
                issued_at=self._clock(),
            )
            if len(records) >= bounded
            else None
        )
        return {
            "approvals": records,
            "cursor": next_cursor,
            "snapshot_revision": int(snapshot_revision),
            "limit": bounded,
        }

    def approval_decide(
        self,
        *,
        command: CommandIdentity,
        approval_ref: str,
        decision: str,
        expected_revision: int,
        reason: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Approve or reject one approval (approver identity is the command's)."""
        normalized = str(decision or "").strip().lower()
        if normalized in {"approved", "rejected"}:
            normalized = "approve" if normalized == "approved" else "reject"
        if normalized not in APPROVAL_DECISIONS:
            raise ResearchOperationError(
                "invalid_decision",
                f"decision must be one of {list(APPROVAL_DECISIONS)}",
                field="decision",
            )
        approval = self._require_approval(command, approval_ref)
        replay = self._replay(
            command,
            operation=f"approval_decide:{normalized}",
            key=idempotency_key,
            request_digest="\0".join([approval_ref, normalized, str(expected_revision)]),
        )
        if replay is not None:
            current = self._approvals.get(str(replay))
            if current is not None:
                return _approval_payload(current, replayed=True)
        try:
            if normalized == "approve":
                updated, replayed = self._approvals.approve(
                    approval.approval_id,
                    approver_principal=command.principal_id,
                    expected_revision=int(expected_revision),
                    reason=reason or None,
                )
            else:
                updated, replayed = self._approvals.reject(
                    approval.approval_id,
                    approver_principal=command.principal_id,
                    expected_revision=int(expected_revision),
                    reason=reason or None,
                )
        except ApprovalError as exc:
            raise _approval_error(exc) from exc
        self._remember(
            command,
            operation=f"approval_decide:{normalized}",
            key=idempotency_key,
            request_digest="\0".join([approval_ref, normalized, str(expected_revision)]),
            result_ref=updated.approval_id,
        )
        return _approval_payload(updated, replayed=replayed)

    def approval_revoke(
        self,
        *,
        command: CommandIdentity,
        approval_ref: str,
        expected_revision: int,
        reason: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Revoke one approval (terminal; it can no longer authorize a submit)."""
        approval = self._require_approval(command, approval_ref)
        replay = self._replay(
            command,
            operation="approval_revoke",
            key=idempotency_key,
            request_digest="\0".join([approval_ref, str(expected_revision)]),
        )
        if replay is not None:
            current = self._approvals.get(str(replay))
            if current is not None:
                return _approval_payload(current, replayed=True)
        try:
            updated, replayed = self._approvals.revoke(
                approval.approval_id,
                actor_principal=command.principal_id,
                expected_revision=int(expected_revision),
                reason=reason or "revoked",
            )
        except ApprovalError as exc:
            raise _approval_error(exc) from exc
        self._remember(
            command,
            operation="approval_revoke",
            key=idempotency_key,
            request_digest="\0".join([approval_ref, str(expected_revision)]),
            result_ref=updated.approval_id,
        )
        return _approval_payload(updated, replayed=replayed)

    # -- P2-R4: wait / snapshot -------------------------------------------

    def research_snapshot(
        self,
        *,
        command: CommandIdentity,
        refs: Sequence[str],
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Authoritative snapshot of the requested references (read-only)."""
        project = self._project(command, project_id)
        entries = [self._observe(command, str(ref).strip(), project) for ref in refs]
        snapshot = {
            "project_id": project,
            "refs": [entry.to_dict() for entry in entries],
        }
        return {
            "snapshot": snapshot,
            "snapshot_revision": _snapshot_revision(entries),
            "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._clock())),
        }

    def research_wait(
        self,
        *,
        command: CommandIdentity,
        kinds: Sequence[str],
        cursor: str | None = None,
        max_wait_s: float | int | None = None,
        limit: int | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Bounded wait: a changed authoritative snapshot or a timed-out poll.

        The wait never blocks past ``max_wait_s`` (<= 60 s), never holds a
        connection state and never invents an event: it returns the authoritative
        snapshot plus the opaque cursor that encodes the revision it observed.
        """
        project = self._project(command, project_id)
        normalized_kinds = _normalize_kinds(kinds)
        bounded_limit = _bounded_limit(limit)
        wait_s = _bounded_wait(max_wait_s)
        observed = self._snapshot_for_kinds(command, normalized_kinds, project, bounded_limit)
        revision = _snapshot_revision(observed)
        changed = True
        timed_out = False
        if cursor:
            decoded = decode_wait_cursor(cursor, project_id=project, now=self._clock())
            changed = str(decoded.get("rev") or "") != revision
        deadline = self._clock() + wait_s
        while not changed and self._clock() < deadline:
            self._sleeper(min(0.05, max(0.0, deadline - self._clock())))
            observed = self._snapshot_for_kinds(command, normalized_kinds, project, bounded_limit)
            revision = _snapshot_revision(observed)
            changed = str(decoded.get("rev") or "") != revision if cursor else True
        if not changed:
            timed_out = True
        events = (
            [
                {
                    "kind": entry.kind,
                    "ref": entry.ref,
                    "state": entry.state,
                    "revision": entry.revision,
                }
                for entry in observed
            ]
            if changed
            else []
        )
        return {
            "events": events,
            "cursor": encode_wait_cursor(
                snapshot_revision=revision, project_id=project, issued_at=self._clock()
            ),
            "snapshot": {
                "project_id": project,
                "refs": [entry.to_dict() for entry in observed],
            },
            "snapshot_revision": revision,
            "timed_out": timed_out,
            "kinds": list(normalized_kinds),
            "max_wait_s": wait_s,
            "limit": bounded_limit,
        }

    # -- P2-R5: resume -----------------------------------------------------

    def research_resume(
        self,
        *,
        command: CommandIdentity,
        proposal_ref: str,
        plan_digest: str,
        task_ref: Mapping[str, Any] | None = None,
        cursor: str | None = None,
        diagnostic_only: bool = False,
    ) -> dict[str, Any]:
        """Resume the saga of ``(proposal, plan_digest)`` without a second attempt.

        The saga id is derived, so a resume in a new session/process addresses the
        same chain.  ``diagnostic_only`` reports the position without running the
        remaining idempotent steps; otherwise ``advance`` may commit the steps the
        previous process did not reach — never a second proposal, task or record.
        """
        if self._saga is None or self._sagas is None:
            raise ResearchOperationNotReadyError(
                "saga_not_available", "no research saga is wired in this deployment"
            )
        project = self._project(command)
        saga_id = saga_id_for(
            project_id=project, proposal_ref=str(proposal_ref), plan_digest=str(plan_digest)
        )
        header = self._sagas.header(saga_id)
        if header is None:
            raise ResearchOperationNotReadyError(
                "saga_not_found",
                "no saga exists for this proposal/plan; create the proposal first",
                saga_id=saga_id,
            )
        if str(header.plan_digest) != str(plan_digest):
            raise ResearchOperationConflictError(
                "plan_digest_changed",
                "the saga was opened for another plan digest; a full recompute is required",
            )
        if cursor:
            decode_wait_cursor(cursor, project_id=project, now=self._clock())
        if not diagnostic_only and not header.terminal:
            self._saga.advance(saga_id)
        reconciliation = self._saga.reconcile(saga_id)
        resolved_task_ref = self._resume_task_ref(command, saga_id, reconciliation)
        if task_ref is not None and resolved_task_ref is not None:
            claimed = _normalize_task_ref(task_ref)
            if claimed and _normalize_task_ref(resolved_task_ref) != claimed:
                raise ResearchOperationConflictError(
                    "task_ref_mismatch",
                    "the supplied TaskRef is not the attempt this saga produced",
                )
        return {
            "resumed_from": "saga",
            "saga": reconciliation.header.to_dict(),
            "task_ref": resolved_task_ref,
            "plan_digest": str(plan_digest),
            "blocked_steps": [step.step for step in reconciliation.steps if step.blocked_reason]
            + [
                step.step
                for step in reconciliation.steps
                if step.state != "committed" and not step.blocked_reason
            ],
            "snapshot_revision": max((step.attempts for step in reconciliation.steps), default=0),
            "next_action": reconciliation.next_action,
            "retry_owner": reconciliation.retry_owner,
            "diagnostic_only": bool(diagnostic_only),
            "reconcile": [step.to_dict() for step in reconciliation.steps],
        }

    # -- P2-R1: round / evidence append -----------------------------------

    def record_append_round(
        self,
        *,
        command: CommandIdentity,
        research_id: str,
        expected_revision: int,
        record: Mapping[str, Any],
        session_seed: str = "",
        evidence_view: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Append one round (new evidence refs + decision) to an existing record.

        A round is a **record revision**, not a second record: the CAS targets the
        observed ``expected_revision`` of the same ``research_id`` and the resource
        revision carries its own outbox row in the same commit.
        """
        request_digest = _request_digest(
            dict(record), {"research_id": str(research_id)}, extra=str(expected_revision)
        )
        replay = self._replay(
            command,
            operation="record_append_round",
            key=idempotency_key,
            request_digest=request_digest,
        )
        if replay is not None:
            return self.record_read(command=command, research_id=str(research_id))
        try:
            written = self._writer.append_record_round(
                command=command,
                session_seed=session_seed,
                research_id=str(research_id),
                expected_revision=int(expected_revision),
                record=record,
                evidence_view=evidence_view,
            )
        except ResearchWriteError as exc:
            raise _write_error(exc) from exc
        self._remember(
            command,
            operation="record_append_round",
            key=idempotency_key,
            request_digest=request_digest,
            result_ref=written.record.resource_id,
        )
        payload = written.to_dict()
        payload["replayed"] = False
        payload["round"] = int(written.record.document.get("round") or 0)
        payload["record_revision"] = int(written.record.document.get("record_revision") or 1)
        return payload

    def record_read(
        self, *, command: CommandIdentity, research_id: str, revision: int | None = None
    ) -> dict[str, Any]:
        """Read one stored record revision (verbatim document + digest)."""
        resource_id = research_resource_key(
            str(command.project_id or ""), "record", str(research_id)
        )
        record = self._read_resource(command, resource_id, revision=revision)
        if record.schema != RESEARCH_RECORD_SCHEMA:
            raise ResearchOperationConflictError(
                "record_schema_conflict", "the addressed resource is not a research record"
            )
        return {
            "schema": RESEARCH_RECORD_SCHEMA,
            "resource_id": record.resource_id,
            "revision": record.revision,
            "digest": record.digest,
            "record": dict(record.document),
            "record_revision": int(record.document.get("record_revision") or 1),
            "round": int(record.document.get("round") or 0),
        }

    # -- saga reconciliation (read-only + explicit action) ----------------

    def saga_reconcile(
        self,
        *,
        command: CommandIdentity,
        saga_id: str,
        action: str = "",
        expected_revision: int = 0,
        reason: str = "",
    ) -> dict[str, Any]:
        """Read-only intent reconciliation, or one explicit action on it."""
        if self._saga is None:
            raise ResearchOperationNotReadyError(
                "saga_not_available", "no research saga is wired in this deployment"
            )
        header = self._sagas.header(str(saga_id)) if self._sagas is not None else None
        if header is None:
            raise ResearchOperationNotReadyError("saga_not_found", "no such saga")
        if self._project(command) != header.project_id:
            raise ResearchOperationDeniedError(
                "project_scope_mismatch", "the saga belongs to another project"
            )
        if not action:
            return self._saga.reconcile(str(saga_id)).to_dict()
        try:
            outcome = self._saga.reconcile_action(
                str(saga_id),
                action=str(action),
                expected_revision=int(expected_revision),
                reason=str(reason or ""),
            )
        except SagaConflictError as exc:
            raise ResearchOperationConflictError(exc.rule, exc.message) from exc
        except SagaError as exc:
            raise ResearchOperationError(exc.rule, exc.message) from exc
        if isinstance(outcome, SagaReconciliation):
            return outcome.to_dict()
        return outcome.to_dict()

    # -- internals ---------------------------------------------------------

    def _project(self, command: CommandIdentity, project_id: str | None = None) -> str:
        project = str(project_id or command.project_id or "")
        if not project:
            raise ResearchOperationError("missing_project", "a project scope is required")
        if command.project_id and project != str(command.project_id):
            raise ResearchOperationDeniedError(
                "project_scope_mismatch", "the request names another project"
            )
        return project

    def _scoped_document(
        self, command: CommandIdentity, proposal: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(proposal, Mapping):
            raise ResearchOperationError("invalid_proposal", "the proposal must be an object")
        document = dict(proposal)
        project = self._project(command)
        claimed = str(document.get("project_id") or "")
        if claimed and claimed != project:
            raise ResearchOperationDeniedError(
                "project_scope_mismatch", "the proposal names another project"
            )
        document["project_id"] = project
        return document

    def _read_resource(
        self, command: CommandIdentity, resource_id: str, *, revision: int | None = None
    ) -> Any:
        try:
            return self._resources.read(
                resource_id,
                principal_id=command.principal_id,
                project_id=str(command.project_id or ""),
                revision=revision,
            )
        except ResearchResourceError as exc:
            raise _resource_error(exc) from exc

    def _require_approval(self, command: CommandIdentity, approval_ref: str) -> Approval:
        approval = self._approvals.get(str(approval_ref))
        if approval is None:
            raise ResearchOperationNotReadyError(
                "approval_not_found", "no such approval", approval_ref=str(approval_ref)
            )
        if approval.project_id != self._project(command):
            raise ResearchOperationDeniedError(
                "project_scope_mismatch", "the approval belongs to another project"
            )
        return approval

    def _proposal_payload(
        self,
        command: CommandIdentity,
        record: Any,
        *,
        digests: Mapping[str, str],
        replayed: bool,
    ) -> dict[str, Any]:
        approval = self._approvals.query(
            project_id=str(command.project_id or ""),
            proposal_ref=str(record.document.get("proposal_id") or ""),
            limit=1,
        )
        return {
            "schema": record.schema,
            "resource_id": record.resource_id,
            "revision": record.revision,
            "digest": record.digest,
            "document": dict(record.document),
            "digests": dict(digests),
            "approval": _approval_payload(approval[0]) if approval else None,
            "replayed": True,
        }

    def _plan_digest(self, *, plan: Mapping[str, Any] | None, declared: str | None) -> str:
        if isinstance(plan, Mapping):
            try:
                computed = canonical_plan_digest(
                    frozen_plan_definition(
                        workflow=str(plan.get("workflow") or ""),
                        input=plan.get("input") if isinstance(plan.get("input"), Mapping) else {},
                        method=(
                            plan.get("method") if isinstance(plan.get("method"), Mapping) else {}
                        ),
                        resources=(
                            plan.get("resources")
                            if isinstance(plan.get("resources"), Mapping)
                            else {}
                        ),
                    )
                ).digest
            except CanonicalDigestError as exc:
                raise ResearchOperationError(exc.rule, str(exc)) from exc
            if declared and declared != computed:
                raise ResearchOperationError(
                    "plan_digest_mismatch",
                    "plan_digest does not match the frozen plan definition",
                    field="plan_digest",
                )
            return computed
        text = str(declared or "")
        if not text:
            raise ResearchOperationError(
                "missing_science_field", "plan_digest or plan is required", field="plan_digest"
            )
        return text

    def _proposal_digest(
        self,
        *,
        proposal: Mapping[str, Any] | None,
        declared: str | None,
        plan_digest: str,
    ) -> str:
        if isinstance(proposal, Mapping):
            document = dict(proposal)
            document.setdefault("plan_digest", plan_digest)
            try:
                computed = canonical_proposal_digest(document).digest
            except CanonicalDigestError as exc:
                raise ResearchOperationError(exc.rule, str(exc)) from exc
            if declared and declared != computed:
                raise ResearchOperationError(
                    "proposal_digest_mismatch",
                    "proposal_digest does not match the semantic projection",
                    field="proposal_digest",
                )
            return computed
        text = str(declared or "")
        if not text:
            raise ResearchOperationError(
                "missing_science_field",
                "proposal_digest or proposal is required",
                field="proposal_digest",
            )
        return text

    def _science_digest(self, *, proposal: Mapping[str, Any] | None, declared: str | None) -> str:
        if isinstance(proposal, Mapping) and {"recipe_digest", "science", "budget"} <= set(
            proposal
        ):
            try:
                computed = proposal_plan_definition_digest(dict(proposal)).digest
            except CanonicalDigestError as exc:
                raise ResearchOperationError(exc.rule, str(exc)) from exc
            if declared and declared != computed:
                raise ResearchOperationError(
                    "plan_digest_mismatch",
                    "science_digest does not match the frozen science projection",
                    field="science_digest",
                )
            return computed
        return str(declared or "")

    # -- bounded observation ------------------------------------------------

    def _snapshot_for_kinds(
        self,
        command: CommandIdentity,
        kinds: Sequence[str],
        project: str,
        limit: int,
    ) -> list[_SnapshotEntry]:
        entries: list[_SnapshotEntry] = []
        if "approval" in kinds:
            approvals = self._approvals.query(project_id=project, limit=limit)
            entries.extend(
                _SnapshotEntry(
                    kind="approval",
                    ref=item.approval_id,
                    state=item.state,
                    revision=item.revision,
                    digest=item.plan_digest,
                    observed=True,
                )
                for item in approvals
            )
        if "saga" in kinds and self._sagas is not None:
            for header in self._sagas.list_headers(project_id=project, limit=limit):
                entries.append(
                    _SnapshotEntry(
                        kind="saga",
                        ref=header.saga_id,
                        state=header.state,
                        revision=header.revision,
                        digest=header.plan_digest,
                        observed=True,
                    )
                )
        for kind, schema in (("proposal", PROPOSAL_SCHEMA), ("record", RESEARCH_RECORD_SCHEMA)):
            if kind not in kinds:
                continue
            for record in self._resources.list_heads(
                project_id=project, schema=schema, limit=limit
            ):
                document = record.document
                field_name = "proposal_id" if kind == "proposal" else "research_id"
                document_id = str(document.get(field_name) or "")
                entries.append(
                    _SnapshotEntry(
                        kind=kind,
                        ref=document_id,
                        state="revoked" if record.revoked_at else "stored",
                        revision=record.revision,
                        digest=record.digest,
                        observed=True,
                    )
                )
        del command  # the project scope already authorized the enumeration
        return entries

    def _observe(self, command: CommandIdentity, ref: str, project: str) -> _SnapshotEntry:
        kind, _, value = ref.partition(":")
        kind = kind.strip()
        value = value.strip() or kind
        if kind == "approval":
            approval = self._approvals.get(value)
            if approval is None or approval.project_id != project:
                return _SnapshotEntry(kind="approval", ref=value, state="unknown", revision=0)
            return _SnapshotEntry(
                kind="approval",
                ref=value,
                state=approval.state,
                revision=approval.revision,
                digest=approval.plan_digest,
                observed=True,
            )
        if kind == "saga" and self._sagas is not None:
            header = self._sagas.header(value)
            if header is None or header.project_id != project:
                return _SnapshotEntry(kind="saga", ref=value, state="unknown", revision=0)
            return _SnapshotEntry(
                kind="saga",
                ref=value,
                state=header.state,
                revision=header.revision,
                digest=header.plan_digest,
                observed=True,
            )
        if kind in {"proposal", "record"}:
            resource_id = research_resource_key(project, kind, value)
            try:
                record = self._resources.read(
                    resource_id, principal_id=command.principal_id, project_id=project
                )
            except ResearchResourceError:
                return _SnapshotEntry(kind=kind, ref=value, state="unknown", revision=0)
            return _SnapshotEntry(
                kind=kind,
                ref=value,
                state="stored",
                revision=record.revision,
                digest=record.digest,
                observed=True,
            )
        raise ResearchOperationError("invalid_ref", f"unsupported reference {ref!r}")

    # -- idempotency ledger -------------------------------------------------

    def _replay(
        self,
        command: CommandIdentity,
        *,
        operation: str,
        key: str | None,
        request_digest: str,
    ) -> str | None:
        if not key or self._sagas is None:
            return None
        scope = f"{command.project_id}|{operation}"
        try:
            recorded = self._sagas.operation_key(scope=scope, key=str(key))
        except SagaConflictError as exc:
            raise ResearchOperationConflictError(exc.rule, exc.message) from exc
        if recorded is None:
            return None
        if recorded.request_digest != request_digest:
            raise ResearchOperationConflictError(
                "idempotency_key_reuse",
                "this idempotency key was used for another request",
                idempotency_key=str(key),
            )
        return recorded.result_ref

    def _remember(
        self,
        command: CommandIdentity,
        *,
        operation: str,
        key: str | None,
        request_digest: str,
        result_ref: str,
    ) -> None:
        if not key or self._sagas is None:
            return
        self._sagas.claim_operation(
            scope=f"{command.project_id}|{operation}",
            key=str(key),
            request_digest=request_digest,
            result_ref=str(result_ref),
        )

    def _resume_task_ref(
        self, command: CommandIdentity, saga_id: str, reconciliation: SagaReconciliation
    ) -> dict[str, Any] | None:
        del command, reconciliation
        if self._sagas is None:
            return None
        intent: SagaIntent | None = self._sagas.intent(saga_id, "submit")
        if intent is None or not intent.committed:
            return None
        binding = intent.result.get("task_ref")
        if not isinstance(binding, Mapping):
            return None
        return {
            "project_id": str(binding.get("project_id") or ""),
            "job_id": str(binding.get("job_id") or ""),
            "attempt": int(binding.get("attempt") or 1),
            "release_id": str(binding.get("release_id") or ""),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _no_submit(_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    raise ResearchOperationNotReadyError(
        "submit_port_unavailable", "no execution submit port is wired in this deployment"
    )


def _scrub_none(value: Any) -> Any:
    """Drop ``None`` values recursively (the canonical digest forbids nulls)."""
    if isinstance(value, Mapping):
        return {str(key): _scrub_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list | tuple):
        return [_scrub_none(item) for item in value if item is not None]
    return value


def _request_digest(
    document: Mapping[str, Any], plan: Mapping[str, Any], *, extra: str = ""
) -> str:
    try:
        return canonical_digest(
            {
                "proposal": _scrub_none(dict(document)),
                "plan": _scrub_none(dict(plan)),
                "extra": extra,
            }
        )
    except CanonicalDigestError as exc:  # pragma: no cover - caller bug
        raise ResearchOperationError(exc.rule, str(exc)) from exc


def _snapshot_revision(entries: Sequence[_SnapshotEntry]) -> str:
    payload = [
        [entry.kind, entry.ref, entry.state, entry.revision, entry.digest] for entry in entries
    ]
    try:
        return canonical_digest({"refs": payload})
    except CanonicalDigestError:  # pragma: no cover - plain JSON
        return str(len(entries))


def _approval_payload(
    approval: Approval, *, replayed: bool = False, comment: str = ""
) -> dict[str, Any]:
    payload = approval.to_dict()
    payload["schema_version"] = 1
    payload["replayed"] = bool(replayed)
    if comment:
        payload["comment"] = comment
    return payload


def _approval_error(exc: ApprovalError) -> ResearchOperationError:
    status = int(getattr(exc, "http_status", 400))
    if status == 403:
        return ResearchOperationDeniedError(exc.rule, exc.message)
    if status == 409:
        return ResearchOperationConflictError(exc.rule, exc.message)
    if status == 404:
        return ResearchOperationNotReadyError(exc.rule, exc.message)
    return ResearchOperationError(exc.rule, exc.message)


def _write_error(exc: ResearchWriteError) -> ResearchOperationError:
    if isinstance(exc, ResearchWriteConflictError):
        detail = exc.detail()
        params = dict(detail.get("params") or {})
        return ResearchOperationConflictError(str(params.get("rule") or exc.rule), exc.message)
    if exc.http_status == 403:
        return ResearchOperationDeniedError(exc.rule, exc.message)
    if exc.http_status == 409:
        return ResearchOperationConflictError(exc.rule, exc.message)
    return ResearchOperationError(exc.rule, exc.message)


def _resource_error(exc: ResearchResourceError) -> ResearchOperationError:
    code = getattr(exc, "code", "research_resource_error")
    message = str(exc)
    if code in {"permission_denied", "project_scope_mismatch"}:
        return ResearchOperationDeniedError(code, message)
    if code in {"revision_conflict", "research_resource_revoked"}:
        return ResearchOperationConflictError(code, message)
    if code == "research_resource_not_found":
        return ResearchOperationNotReadyError("proposal_not_found", message)
    return ResearchOperationError(code, message)


def _bounded_limit(limit: int | None) -> int:
    if limit is None:
        return WAIT_DEFAULT_LIMIT
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise ResearchOperationError("input_invalid", f"limit is not an integer: {exc}") from None
    if value < 1:
        raise ResearchOperationError("input_invalid", "limit must be >= 1", field="limit")
    if value > WAIT_MAX_LIMIT:
        raise ResearchOperationError(
            "bound_exceeded",
            f"limit must be <= {WAIT_MAX_LIMIT}",
            field="limit",
            max_limit=WAIT_MAX_LIMIT,
        )
    return value


def _bounded_wait(max_wait_s: float | int | None) -> float:
    if max_wait_s is None:
        return 0.0
    try:
        value = float(max_wait_s)
    except (TypeError, ValueError) as exc:
        raise ResearchOperationError(
            "input_invalid", f"max_wait_s is not a number: {exc}", field="max_wait_s"
        ) from None
    if value < 0:
        raise ResearchOperationError("input_invalid", "max_wait_s must be >= 0", field="max_wait_s")
    if value > WAIT_MAX_WAIT_S:
        raise ResearchOperationError(
            "bound_exceeded",
            f"max_wait_s must be <= {WAIT_MAX_WAIT_S}",
            field="max_wait_s",
            max_wait_s=WAIT_MAX_WAIT_S,
        )
    return value


def _normalize_kinds(kinds: Sequence[str] | str) -> tuple[str, ...]:
    if isinstance(kinds, str):
        raw: Sequence[Any] = [part for part in kinds.split(",") if part.strip()]
    elif isinstance(kinds, Sequence):
        raw = list(kinds)
    else:
        raise ResearchOperationError("input_invalid", "kinds must be a list of names")
    allowed = {"approval", "saga", "proposal", "record"}
    normalized: list[str] = []
    for item in raw:
        name = str(item or "").strip()
        if name not in allowed:
            raise ResearchOperationError(
                "input_invalid",
                f"unknown wait kind {name!r}; supported={sorted(allowed)}",
                field="kinds",
            )
        if name not in normalized:
            normalized.append(name)
    if not normalized:
        raise ResearchOperationError("missing_field", "kinds must name at least one kind")
    return tuple(normalized)


def _normalize_task_ref(value: Mapping[str, Any]) -> tuple[str, int, str]:
    job_id = str(value.get("job_id") or value.get("task_id") or "")
    attempt = int(value.get("attempt") or 1)
    release_id = str(value.get("release_id") or "")
    return (job_id, attempt, release_id)
