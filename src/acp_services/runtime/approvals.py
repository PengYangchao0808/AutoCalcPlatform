"""Persistent Proposal/ResearchRecord approvals bound to a frozen plan (B16).

An approval is a Runtime fact about *intent*, not about execution:

* the record binds ``plan_digest`` (the frozen scientific-plan definition),
  ``proposal_digest`` (the semantic Proposal projection), the server-resolved
  project, the requested budget and the allowed change range;
* state is ``draft → pending → approved|rejected`` and, once approved,
  ``approved → revoked``; an overdue approval becomes ``expired``;
* every mutation is one optimistic CAS on ``expected_revision``; a stale
  revision can never overwrite a decision;
* approvals and task execution are different facts.  This module never writes a
  job row, never adds a ``JobStatus``, never reuses ``WAITING_REVIEW`` and never
  changes the ``resume()`` review-only semantics.  After a successful submit the
  approval may record a TaskRef as evidence only (no state/revision change);
* the approver identity is always supplied by the caller *after* the HTTP
  boundary resolved it; a payload-supplied approver is rejected before this
  service is reached;
* events are committed to the existing ``job_outbox`` table (the single event
  store) in the same transaction as the state CAS.  An event whose revision is
  not the committed revision is dropped, so a stale event can never be used to
  reconstruct/overwrite state.  Approval-stream delivery is B19's job.

Canonicalization is **not** re-implemented here: digests come from
:mod:`acp_services.runtime.canonical_digest`, which B18 imports as well.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from acp_services.runtime.canonical_digest import (
    CANONICAL_DIGEST_VERSION,
    CanonicalDigestError,
    canonical_digest,
)
from acp_services.runtime.helpers import utc_now_iso
from acp_services.store.migrations import migrate

__all__ = [
    "Approval",
    "ApprovalConflictError",
    "ApprovalDeniedError",
    "ApprovalNotFoundError",
    "ApprovalService",
    "ApprovalState",
    "ApprovalValidationError",
    "APPROVAL_EVENT_TYPES",
    "APPROVAL_STATES",
    "budget_digest",
    "is_valid_approval_ref",
]

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_APPROVAL_REF_RE = re.compile(r"^ap_[A-Za-z0-9._:-]{4,123}$")

#: Wire/record states.  ``draft`` is the pre-request state (it is never shown
#: as an approvable record); the frozen C01/P2 wire status for a requested but
#: undecided approval is ``pending``.
APPROVAL_STATES: tuple[str, ...] = (
    "draft",
    "pending",
    "approved",
    "rejected",
    "expired",
    "revoked",
)

APPROVAL_EVENT_TYPES: tuple[str, ...] = (
    "approval.drafted",
    "approval.requested",
    "approval.approved",
    "approval.rejected",
    "approval.expired",
    "approval.revoked",
    "approval.task_bound",
)

_DECIDED_STATES = frozenset({"approved", "rejected", "expired", "revoked"})
_LIVE_STATES = frozenset({"draft", "pending", "approved"})


class ApprovalState(str, Enum):
    """Lifecycle state of one approval record."""

    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ApprovalError(RuntimeError):
    """Base class with a stable contract failure shape."""

    code = "revision_conflict"
    http_status = 409

    def __init__(self, rule: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message
        self.retryable = retryable

    def detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "params": {"rule": self.rule},
            "retryable": self.retryable,
        }


class ApprovalValidationError(ApprovalError):
    """Malformed approval input."""

    code = "input_invalid"
    http_status = 400


class ApprovalNotFoundError(ApprovalError):
    """Unknown approval reference (also used for cross-project non-disclosure)."""

    code = "access_denied"
    http_status = 403


class ApprovalDeniedError(ApprovalError):
    """The approval cannot authorize the requested action."""

    code = "access_denied"
    http_status = 403


class ApprovalConflictError(ApprovalError):
    """A state/revision/allocation conflict."""

    code = "revision_conflict"
    http_status = 409


def is_valid_approval_ref(value: str) -> bool:
    return bool(_APPROVAL_REF_RE.match(str(value or "")))


def _require_digest(value: Any, field: str) -> str:
    text = str(value or "")
    if not _DIGEST_RE.match(text):
        raise ApprovalValidationError("digest_invalid", f"{field} must be sha256:<64 hex>")
    return text


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ApprovalValidationError("timestamp_invalid", f"invalid timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now_dt(now: str) -> datetime:
    parsed = _parse_iso(now)
    assert parsed is not None
    return parsed


def budget_digest(budget: Mapping[str, Any] | None) -> str:
    """Canonical digest of the approval's budget binding (``''`` when absent)."""
    if not budget:
        return ""
    try:
        return canonical_digest({"budget": dict(budget)})
    except CanonicalDigestError as exc:
        raise ApprovalValidationError(exc.rule, str(exc)) from exc


@dataclass(frozen=True)
class Approval:
    """One persisted approval record."""

    approval_id: str
    proposal_ref: str
    proposal_digest: str
    plan_digest: str
    plan_definition: dict[str, Any]
    project_id: str
    requester_principal: str
    approver_principal: str | None
    budget: dict[str, Any] | None
    budget_digest: str
    #: D06 (M1-04a): the *scientific* plan digest this approval also binds
    #: (``experience/plan-identity.md`` §1).  Empty means the approval was
    #: requested without a proposal body, so only the execution digest is bound;
    #: that gap is reported as ``binding_status=execution-only`` and never
    #: silently presented as a full science binding.
    science_digest: str
    allowed_change_range: dict[str, Any]
    state: str
    expires_at: str | None
    revision: int
    created_at: str
    updated_at: str
    decided_at: str | None
    reason: str | None
    task_ref: dict[str, Any] | None
    idempotency_key: str | None

    @property
    def terminal(self) -> bool:
        return self.state in _DECIDED_STATES

    @property
    def live(self) -> bool:
        return self.state in _LIVE_STATES

    @property
    def binding_status(self) -> str:
        """``full`` (science + execution) or ``execution-only`` (no proposal)."""
        return "full" if self.science_digest else "execution-only"

    def binding(self) -> dict[str, Any]:
        """The immutable approval binding (R3-06 layer 3 + D06).

        Two digests are bound independently: ``plan_digest`` (execution plan) and
        ``science_digest`` (the C ``plan_definition_digest``).  Neither may stand
        in for the other, and a change to either invalidates the approval.
        """
        return {
            "plan_digest": self.plan_digest,
            "science_digest": self.science_digest,
            "binding_status": self.binding_status,
            "proposal_digest": self.proposal_digest,
            "project_id": self.project_id,
            "budget_digest": self.budget_digest,
            "allowed_change_range": self.allowed_change_range,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_ref": self.approval_id,
            "proposal_ref": self.proposal_ref,
            "proposal_digest": self.proposal_digest,
            "plan_digest": self.plan_digest,
            "science_digest": self.science_digest,
            "binding_status": self.binding_status,
            "plan_definition": self.plan_definition,
            "project_id": self.project_id,
            "requester_ref": self.requester_principal,
            "approver_ref": self.approver_principal,
            "budget": self.budget,
            "budget_digest": self.budget_digest,
            "allowed_change_range": self.allowed_change_range,
            "status": self.state,
            "state": self.state,
            "expires_at": self.expires_at,
            "revision": self.revision,
            "created_at": self.created_at,
            "requested_at": self.created_at,
            "updated_at": self.updated_at,
            "decided_at": self.decided_at,
            "reason": self.reason,
            "task_ref": self.task_ref,
            "canonical_digest": {
                "spec_id": "acp2.canonical-digest.v1",
                "version": CANONICAL_DIGEST_VERSION,
            },
        }


def _row_to_approval(row: sqlite3.Row) -> Approval:
    def _json(value: Any, default: Any) -> Any:
        if value in (None, ""):
            return default
        try:
            return json.loads(str(value))
        except json.JSONDecodeError:
            return default

    budget = _json(row["budget_json"], None)
    return Approval(
        approval_id=str(row["approval_id"]),
        proposal_ref=str(row["proposal_ref"]),
        proposal_digest=str(row["proposal_digest"]),
        plan_digest=str(row["plan_digest"]),
        plan_definition=_json(row["plan_definition_json"], {}) or {},
        project_id=str(row["project_id"]),
        requester_principal=str(row["requester_principal"]),
        approver_principal=row["approver_principal"],
        budget=budget,
        budget_digest=str(row["budget_digest"] or ""),
        science_digest=str(row["science_digest"] or ""),
        allowed_change_range=_json(row["allowed_change_range_json"], {}) or {},
        state=str(row["state"]),
        expires_at=row["expires_at"],
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        decided_at=row["decided_at"],
        reason=row["reason"],
        task_ref=_json(row["task_ref_json"], None),
        idempotency_key=row["idempotency_key"],
    )


class ApprovalService:
    """SQLite-backed approval authority over the jobs database.

    ``db_path`` is the same database the Controller uses for jobs.  Opening the
    service applies the additive ``023`` migration; no second authority or
    database is created.
    """

    def __init__(
        self,
        db_path: Path | str,
        *,
        clock: Callable[[], str] = utc_now_iso,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._id_factory = id_factory or (lambda: "ap_" + uuid.uuid4().hex)
        migrate(self.db_path)

    # -- connection helpers -------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _now(self) -> str:
        return self._clock()

    # -- create / request ---------------------------------------------------
    def create_draft(
        self,
        *,
        proposal_ref: str,
        proposal_digest: str,
        plan_digest: str,
        project_id: str,
        requester_principal: str,
        plan_definition: Mapping[str, Any] | None = None,
        budget: Mapping[str, Any] | None = None,
        allowed_change_range: Mapping[str, Any] | None = None,
        expires_at: str | None = None,
        idempotency_key: str | None = None,
        science_digest: str = "",
    ) -> Approval:
        """Persist an unrequested (``draft``) record.

        A draft is not approvable and does not authorize anything.  It exists so
        a Proposal can be assembled before it is shown to an approver.
        """
        proposal_ref = self._require_text(proposal_ref, "proposal_ref")
        project_id = self._require_text(project_id, "project_id")
        requester = self._require_text(requester_principal, "requester_principal")
        proposal_digest = _require_digest(proposal_digest, "proposal_digest")
        plan_digest = _require_digest(plan_digest, "plan_digest")
        science_digest = self._normalize_science_digest(science_digest)
        expires = self._normalize_expiry(expires_at)
        binding_budget = self._normalize_budget(budget)
        now = self._now()
        approval_id = self._id_factory()
        if not is_valid_approval_ref(approval_id):
            raise ApprovalValidationError(
                "approval_ref_invalid", "generated approval id is invalid"
            )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """INSERT INTO approval_records (
                           approval_id, proposal_ref, proposal_digest, plan_digest,
                           plan_definition_json, project_id, requester_principal,
                           approver_principal, budget_json, budget_digest,
                           allowed_change_range_json, state, expires_at, revision,
                           created_at, updated_at, decided_at, reason, task_ref_json,
                           idempotency_key, science_digest, last_event_revision)
                       VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 'draft', ?, 1,
                               ?, ?, NULL, NULL, NULL, ?, ?, 0)""",
                    (
                        approval_id,
                        proposal_ref,
                        proposal_digest,
                        plan_digest,
                        json.dumps(dict(plan_definition or {}), sort_keys=True, default=str),
                        project_id,
                        requester,
                        json.dumps(binding_budget, sort_keys=True) if binding_budget else None,
                        budget_digest(binding_budget) if binding_budget else "",
                        json.dumps(dict(allowed_change_range or {}), sort_keys=True),
                        expires,
                        now,
                        now,
                        idempotency_key,
                        science_digest,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM approval_records WHERE approval_id=?", (approval_id,)
                ).fetchone()
                assert row is not None
                approval = _row_to_approval(row)
                self._emit_event(conn, approval, "approval.drafted", previous_revision=0)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return approval

    def request(
        self,
        *,
        proposal_ref: str,
        proposal_digest: str,
        plan_digest: str,
        project_id: str,
        requester_principal: str,
        plan_definition: Mapping[str, Any] | None = None,
        budget: Mapping[str, Any] | None = None,
        allowed_change_range: Mapping[str, Any] | None = None,
        expires_at: str | None = None,
        idempotency_key: str | None = None,
        science_digest: str = "",
    ) -> tuple[Approval, bool]:
        """Create/return the live approval for ``(proposal_ref, plan_digest)``.

        At most one ``pending`` approval exists per requester/proposal/plan.
        A repeat request returns the existing record (``replayed=True``) and
        never creates a second one.  An approved record for the same pair is
        also replayed; a rejected/expired/revoked one allows a fresh request.
        """
        proposal_ref = self._require_text(proposal_ref, "proposal_ref")
        project_id = self._require_text(project_id, "project_id")
        requester = self._require_text(requester_principal, "requester_principal")
        proposal_digest = _require_digest(proposal_digest, "proposal_digest")
        plan_digest = _require_digest(plan_digest, "plan_digest")
        science_digest = self._normalize_science_digest(science_digest)
        expires = self._normalize_expiry(expires_at)
        binding_budget = self._normalize_budget(budget)
        budget_ref = budget_digest(binding_budget) if binding_budget else ""
        change_range = dict(allowed_change_range or {})
        plan_body = dict(plan_definition or {})
        now = self._now()

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = self._find_live_locked(
                    conn,
                    proposal_ref=proposal_ref,
                    plan_digest=plan_digest,
                    requester=requester,
                    now=now,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    if existing.proposal_digest != proposal_digest:
                        raise ApprovalConflictError(
                            "approval_proposal_changed",
                            "the live approval is bound to a different proposal digest",
                        )
                    if (
                        science_digest
                        and existing.science_digest
                        and existing.science_digest != science_digest
                    ):
                        raise ApprovalConflictError(
                            "approval_binding_mismatch",
                            "the live approval is bound to a different scientific plan digest",
                        )
                    if existing.state == "pending":
                        conn.commit()
                        return existing, True
                    if existing.state == "draft":
                        updated = self._transition_locked(
                            conn,
                            existing,
                            new_state="pending",
                            expected_revision=existing.revision,
                            fields={"expires_at": expires},
                        )
                        self._emit_event(conn, updated, "approval.requested", existing.revision)
                        conn.commit()
                        return updated, False
                    if existing.state == "approved":
                        conn.commit()
                        return existing, True

                approval_id = self._id_factory()
                if not is_valid_approval_ref(approval_id):
                    raise ApprovalValidationError(
                        "approval_ref_invalid", "generated approval id is invalid"
                    )
                conn.execute(
                    """INSERT INTO approval_records (
                           approval_id, proposal_ref, proposal_digest, plan_digest,
                           plan_definition_json, project_id, requester_principal,
                           approver_principal, budget_json, budget_digest,
                           allowed_change_range_json, state, expires_at, revision,
                           created_at, updated_at, decided_at, reason, task_ref_json,
                           idempotency_key, science_digest, last_event_revision)
                       VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 'pending', ?, 1,
                               ?, ?, NULL, NULL, NULL, ?, ?, 0)""",
                    (
                        approval_id,
                        proposal_ref,
                        proposal_digest,
                        plan_digest,
                        json.dumps(plan_body, sort_keys=True, default=str),
                        project_id,
                        requester,
                        json.dumps(binding_budget, sort_keys=True) if binding_budget else None,
                        budget_ref,
                        json.dumps(change_range, sort_keys=True),
                        expires,
                        now,
                        now,
                        idempotency_key,
                        science_digest,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM approval_records WHERE approval_id=?", (approval_id,)
                ).fetchone()
                assert row is not None
                approval = _row_to_approval(row)
                self._emit_event(conn, approval, "approval.requested", previous_revision=0)
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise ApprovalConflictError(
                    "approval_already_pending",
                    "another pending approval already exists for this proposal/plan",
                ) from exc
            except Exception:
                conn.rollback()
                raise
        return approval, False

    def _find_live_locked(
        self,
        conn: sqlite3.Connection,
        *,
        proposal_ref: str,
        plan_digest: str,
        requester: str,
        now: str,
        idempotency_key: str | None,
    ) -> Approval | None:
        if idempotency_key:
            row = conn.execute(
                """SELECT * FROM approval_records
                   WHERE requester_principal=? AND idempotency_key=?
                   ORDER BY created_at DESC LIMIT 1""",
                (requester, idempotency_key),
            ).fetchone()
            if row is not None:
                approval = _row_to_approval(row)
                if approval.proposal_ref != proposal_ref or approval.plan_digest != plan_digest:
                    raise ApprovalConflictError(
                        "idempotency_key_reuse",
                        "the idempotency key was used for a different proposal/plan",
                    )
                return approval
        rows = conn.execute(
            """SELECT * FROM approval_records
               WHERE requester_principal=? AND proposal_ref=? AND plan_digest=?
               ORDER BY created_at DESC""",
            (requester, proposal_ref, plan_digest),
        ).fetchall()
        for row in rows:
            approval = _row_to_approval(row)
            if approval.state in {"pending", "draft", "approved"}:
                expiry = _parse_iso(approval.expires_at) if approval.expires_at else None
                if expiry is not None and _now_dt(now) >= expiry:
                    if approval.state == "approved":
                        continue
                    self._expire_locked(conn, approval, now)
                    continue  # an expired record never blocks a fresh request
                return approval
        return None

    # -- decisions ----------------------------------------------------------
    def approve(
        self,
        approval_ref: str,
        *,
        approver_principal: str,
        expected_revision: int,
        reason: str | None = None,
    ) -> tuple[Approval, bool]:
        return self._decide(
            approval_ref,
            decision="approved",
            actor=approver_principal,
            expected_revision=expected_revision,
            reason=reason,
            event_type="approval.approved",
        )

    def reject(
        self,
        approval_ref: str,
        *,
        approver_principal: str,
        expected_revision: int,
        reason: str | None = None,
    ) -> tuple[Approval, bool]:
        return self._decide(
            approval_ref,
            decision="rejected",
            actor=approver_principal,
            expected_revision=expected_revision,
            reason=reason,
            event_type="approval.rejected",
        )

    def revoke(
        self,
        approval_ref: str,
        *,
        actor_principal: str,
        expected_revision: int,
        reason: str | None = None,
    ) -> tuple[Approval, bool]:
        actor_principal = self._require_text(actor_principal, "actor_principal")
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                approval = self._get_locked(conn, approval_ref)
                if approval is None:
                    raise ApprovalNotFoundError("approval_not_found", "approval not found")
                approval = self._expire_locked(conn, approval, now)
                if approval.state == "revoked":
                    if approval.approver_principal not in (None, actor_principal):
                        raise ApprovalDeniedError(
                            "approval_not_yours", "the approval was decided by another principal"
                        )
                    conn.commit()
                    return approval, True
                if approval.state != "approved":
                    raise ApprovalConflictError(
                        "approval_not_approved",
                        f"only an approved approval can be revoked (state={approval.state})",
                    )
                if expected_revision != approval.revision:
                    raise ApprovalConflictError(
                        "revision_conflict",
                        f"expected revision {expected_revision}, current {approval.revision}",
                        retryable=True,
                    )
                updated = self._transition_locked(
                    conn,
                    approval,
                    new_state="revoked",
                    expected_revision=approval.revision,
                    fields={"approver_principal": actor_principal, "reason": reason},
                )
                self._emit_event(conn, updated, "approval.revoked", approval.revision)
                conn.commit()
                return updated, False
            except Exception:
                conn.rollback()
                raise

    def _decide(
        self,
        approval_ref: str,
        *,
        decision: str,
        actor: str,
        expected_revision: int,
        reason: str | None,
        event_type: str,
    ) -> tuple[Approval, bool]:
        actor = self._require_text(actor, "approver_principal")
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                approval = self._get_locked(conn, approval_ref)
                if approval is None:
                    raise ApprovalNotFoundError("approval_not_found", "approval not found")
                approval = self._expire_locked(conn, approval, now)
                if approval.state == decision:
                    if approval.approver_principal not in (None, actor):
                        raise ApprovalDeniedError(
                            "approval_not_yours",
                            "the approval was decided by another principal",
                        )
                    conn.commit()
                    return approval, True
                if approval.state == "pending":
                    if expected_revision != approval.revision:
                        raise ApprovalConflictError(
                            "revision_conflict",
                            f"expected revision {expected_revision}, current {approval.revision}",
                            retryable=True,
                        )
                    updated = self._transition_locked(
                        conn,
                        approval,
                        new_state=decision,
                        expected_revision=approval.revision,
                        fields={
                            "approver_principal": actor,
                            "reason": reason,
                            "decided_at": now,
                        },
                    )
                    self._emit_event(conn, updated, event_type, approval.revision)
                    conn.commit()
                    return updated, False
                raise ApprovalConflictError(
                    "approval_already_decided",
                    f"approval is already {approval.state}; a different decision is a conflict",
                )
            except Exception:
                conn.rollback()
                raise

    # -- expiry / query -----------------------------------------------------
    def expire_due(self, *, limit: int = 1000) -> list[Approval]:
        """Move every due pending/approved approval to ``expired`` (lazy sweep)."""
        now = self._now()
        expired: list[Approval] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    """SELECT * FROM approval_records
                       WHERE state IN ('pending', 'approved') AND expires_at IS NOT NULL
                       ORDER BY expires_at LIMIT ?""",
                    (int(limit),),
                ).fetchall()
                for row in rows:
                    approval = _row_to_approval(row)
                    if _now_dt(now) < (_parse_iso(approval.expires_at) or _now_dt(now)):
                        continue
                    updated = self._expire_locked(conn, approval, now)
                    if updated.state == "expired":
                        expired.append(updated)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return expired

    def get(self, approval_ref: str, *, now: str | None = None) -> Approval | None:
        moment = now or self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                approval = self._get_locked(conn, approval_ref)
                if approval is None:
                    conn.commit()
                    return None
                approval = self._expire_locked(conn, approval, moment)
                conn.commit()
                return approval
            except Exception:
                conn.rollback()
                raise

    def query(
        self,
        *,
        project_id: str,
        principal_id: str | None = None,
        approval_ref: str | None = None,
        proposal_ref: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Approval]:
        self.expire_due()
        clauses = ["project_id=?"]
        params: list[Any] = [project_id]
        if approval_ref:
            clauses.append("approval_id=?")
            params.append(approval_ref)
        if proposal_ref:
            clauses.append("proposal_ref=?")
            params.append(proposal_ref)
        if status:
            if status not in APPROVAL_STATES:
                raise ApprovalValidationError(
                    "approval_status_invalid", f"unknown status {status!r}"
                )
            clauses.append("state=?")
            params.append(status)
        if principal_id:
            clauses.append("(requester_principal=? OR approver_principal=?)")
            params.extend([principal_id, principal_id])
        params.append(max(0, min(int(limit), 200)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT * FROM approval_records WHERE {" AND ".join(clauses)}
                    ORDER BY created_at DESC, approval_id ASC LIMIT ?""",
                params,
            ).fetchall()
        return [_row_to_approval(row) for row in rows]

    # -- submit authorization ----------------------------------------------
    def authorize_submission(
        self,
        *,
        plan_digest: str,
        proposal_ref: str,
        project_id: str,
        requester_principal: str,
        proposal_digest: str | None = None,
        budget: Mapping[str, Any] | None = None,
    ) -> Approval:
        """Return the approved record or raise the frozen structured denial.

        Refusal precedence follows the contract: authorization (no/revoked/
        expired/pending approval, other principal/project) before conflict
        (changed plan/proposal/budget).
        """
        plan_digest = _require_digest(plan_digest, "plan_digest")
        proposal_ref = self._require_text(proposal_ref, "proposal_ref")
        project_id = self._require_text(project_id, "project_id")
        requester_principal = self._require_text(requester_principal, "requester_principal")
        self.expire_due()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM approval_records WHERE proposal_ref=?
                   ORDER BY created_at DESC""",
                (proposal_ref,),
            ).fetchall()
        records = [_row_to_approval(row) for row in rows]
        if not records:
            raise ApprovalDeniedError("approval_required", "no approval exists for this proposal")
        other_user_same_plan = [
            record
            for record in records
            if record.plan_digest == plan_digest
            and record.requester_principal != requester_principal
        ]
        mine = [
            record
            for record in records
            if record.plan_digest == plan_digest
            and record.requester_principal == requester_principal
        ]
        if not mine:
            if other_user_same_plan:
                raise ApprovalDeniedError(
                    "approval_principal_mismatch",
                    "the approval belongs to a different verified principal",
                )
            mine_other_plan = [
                record for record in records if record.requester_principal == requester_principal
            ]
            if mine_other_plan:
                raise ApprovalConflictError(
                    "approved_plan_digest_mismatch",
                    "the approved plan digest differs from the submitted plan",
                )
            raise ApprovalDeniedError(
                "approval_required", "no approval exists for this principal and plan"
            )
        approval = mine[0]
        # A draft is never visible to a submit; it is an approval that was never
        # requested.
        if approval.state == "draft":
            raise ApprovalDeniedError("approval_not_requested", "the approval was never requested")
        if approval.state == "pending":
            raise ApprovalDeniedError("approval_pending", "the approval is still pending")
        if approval.state == "rejected":
            raise ApprovalDeniedError("approval_rejected", "the approval was rejected")
        if approval.state == "expired":
            raise ApprovalDeniedError("approval_expired", "the approval expired")
        if approval.state == "revoked":
            raise ApprovalDeniedError("approval_revoked", "the approval was revoked")
        if approval.state != "approved":
            raise ApprovalDeniedError("approval_required", f"approval state {approval.state}")
        if approval.project_id != project_id:
            raise ApprovalDeniedError(
                "project_scope_mismatch", "the approval belongs to a different project"
            )
        if (
            proposal_digest is not None
            and _require_digest(proposal_digest, "proposal_digest") != approval.proposal_digest
        ):
            raise ApprovalConflictError(
                "approved_proposal_digest_mismatch",
                "the proposal/Recipe projection changed after approval",
            )
        submitted_budget = self._normalize_budget(budget)
        submitted_budget_digest = budget_digest(submitted_budget) if submitted_budget else ""
        if submitted_budget_digest != approval.budget_digest:
            raise ApprovalConflictError(
                "approved_budget_mismatch", "the requested budget changed after approval"
            )
        return approval

    def bind_task_ref(
        self,
        approval_ref: str,
        task_ref: Mapping[str, Any],
        *,
        actor_principal: str | None = None,
    ) -> Approval:
        """Record the TaskRef created by an authorized submit (evidence only).

        This is an execution fact, not an approval decision: the state and
        ``revision`` are deliberately unchanged, and the bind is idempotent.
        """
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                approval = self._get_locked(conn, approval_ref)
                if approval is None:
                    raise ApprovalNotFoundError("approval_not_found", "approval not found")
                if approval.state != "approved":
                    raise ApprovalConflictError(
                        "approval_not_approved", "only an approved approval can bind a TaskRef"
                    )
                if actor_principal and approval.requester_principal != actor_principal:
                    raise ApprovalDeniedError(
                        "approval_principal_mismatch", "the TaskRef belongs to another principal"
                    )
                if approval.task_ref and approval.task_ref.get("task_id") == task_ref.get(
                    "task_id"
                ):
                    conn.commit()
                    return approval
                conn.execute(
                    """UPDATE approval_records
                       SET task_ref_json=?, updated_at=?
                       WHERE approval_id=? AND state='approved'""",
                    (json.dumps(dict(task_ref), sort_keys=True), now, approval_ref),
                )
                row = conn.execute(
                    "SELECT * FROM approval_records WHERE approval_id=?", (approval_ref,)
                ).fetchone()
                assert row is not None
                bound = _row_to_approval(row)
                # Binding a TaskRef does not advance the approval revision; the
                # event still names the committed revision, so the guard is given
                # the revision before it.
                self._emit_event(
                    conn,
                    bound,
                    "approval.task_bound",
                    max(0, approval.revision - 1),
                )
                conn.commit()
                return bound
            except Exception:
                conn.rollback()
                raise

    # -- internal -----------------------------------------------------------
    def _require_text(self, value: Any, field: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ApprovalValidationError("missing_field", f"{field} is required")
        return text

    def _normalize_expiry(self, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = _parse_iso(value)
        assert parsed is not None
        return parsed.isoformat().replace("+00:00", "Z")

    def _normalize_budget(self, budget: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if budget is None:
            return None
        if not isinstance(budget, Mapping):
            raise ApprovalValidationError("budget_invalid", "budget must be an object")
        result = dict(budget)
        if not result:
            return None
        return result

    def _normalize_science_digest(self, value: str | None) -> str:
        """Validate the optional D06 science digest (``""`` = not supplied)."""
        text = str(value or "").strip()
        if not text:
            return ""
        return _require_digest(text, "science_digest")

    def _get_locked(self, conn: sqlite3.Connection, approval_ref: str) -> Approval | None:
        row = conn.execute(
            "SELECT * FROM approval_records WHERE approval_id=?", (str(approval_ref),)
        ).fetchone()
        return _row_to_approval(row) if row is not None else None

    def _expire_locked(self, conn: sqlite3.Connection, approval: Approval, now: str) -> Approval:
        if approval.state not in {"pending", "approved"}:
            return approval
        if not approval.expires_at:
            return approval
        expiry = _parse_iso(approval.expires_at)
        if expiry is None or _now_dt(now) < expiry:
            return approval
        updated = self._transition_locked(
            conn,
            approval,
            new_state="expired",
            expected_revision=approval.revision,
            fields={"reason": "expired", "decided_at": now},
        )
        self._emit_event(conn, updated, "approval.expired", approval.revision)
        return updated

    def _transition_locked(
        self,
        conn: sqlite3.Connection,
        approval: Approval,
        *,
        new_state: str,
        expected_revision: int,
        fields: Mapping[str, Any] | None = None,
    ) -> Approval:
        if expected_revision != approval.revision:
            raise ApprovalConflictError(
                "revision_conflict",
                f"expected revision {expected_revision}, current {approval.revision}",
                retryable=True,
            )
        if new_state not in APPROVAL_STATES:
            raise ApprovalValidationError("approval_state_invalid", f"unknown state {new_state!r}")
        updates: dict[str, Any] = {
            "state": new_state,
            "revision": approval.revision + 1,
            "updated_at": self._now(),
        }
        for key, value in dict(fields or {}).items():
            if key in {
                "approver_principal",
                "expires_at",
                "reason",
                "decided_at",
            }:
                updates[key] = value
        assignments = ", ".join(f"{key}=?" for key in updates)
        values = [*updates.values(), approval.approval_id, approval.revision]
        cursor = conn.execute(
            f"""UPDATE approval_records SET {assignments}
                WHERE approval_id=? AND revision=? AND state=?""",
            (*values, approval.state),
        )
        if cursor.rowcount != 1:
            row = conn.execute(
                "SELECT * FROM approval_records WHERE approval_id=?", (approval.approval_id,)
            ).fetchone()
            current = _row_to_approval(row) if row is not None else None
            raise ApprovalConflictError(
                "revision_conflict",
                "approval changed concurrently"
                + (f" (current revision {current.revision})" if current else ""),
                retryable=True,
            )
        row = conn.execute(
            "SELECT * FROM approval_records WHERE approval_id=?", (approval.approval_id,)
        ).fetchone()
        assert row is not None
        return _row_to_approval(row)

    def _emit_event(
        self,
        conn: sqlite3.Connection,
        approval: Approval,
        event_type: str,
        previous_revision: int,
    ) -> None:
        """Append the committed fact to the existing outbox/event table.

        The event identity only ever names the revision that was *just*
        committed.  A caller attempting to emit an older revision is ignored
        (``previous_revision`` guard), and the unique event id makes a replayed
        commit a no-op.  Nothing here can write approval state.

        The row is retained as the durable approval fact.  Task-outbox delivery
        and approval-stream delivery are separate consumers (B19); marking the
        row delivered keeps the *task* delivery sweeper from treating an
        approval id as a job id.  B19 reads approval events by ``owner_type``.
        """
        if approval.revision <= previous_revision:
            return
        event_id = f"{approval.approval_id}:{approval.revision}:{event_type}"
        emitted_at = self._now()
        payload = {
            "approval_ref": approval.approval_id,
            "proposal_ref": approval.proposal_ref,
            "proposal_digest": approval.proposal_digest,
            "plan_digest": approval.plan_digest,
            "project_id": approval.project_id,
            "state": approval.state,
            "revision": approval.revision,
            "requester_ref": approval.requester_principal,
            "approver_ref": approval.approver_principal,
        }
        conn.execute(
            """INSERT OR IGNORE INTO job_outbox (
                   event_id, job_id, owner_type, scope, event_type, payload_json,
                   dedup_key, revision, attempt, emitted_at, delivered_at,
                   delivered_seq, delivery_attempts, last_error)
               VALUES (?, ?, 'project', 'project', ?, ?, ?, ?, NULL, ?, ?, ?, 0, NULL)""",
            (
                event_id,
                approval.approval_id,
                event_type,
                json.dumps(payload, sort_keys=True, default=str),
                event_id,
                approval.revision,
                emitted_at,
                emitted_at,
                approval.revision,
            ),
        )
        conn.execute(
            """UPDATE approval_records SET last_event_revision=?
               WHERE approval_id=? AND revision=?""",
            (approval.revision, approval.approval_id, approval.revision),
        )
