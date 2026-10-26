"""Research session identity, planning claim and submit-intent recovery (M1-02).

The ACP 2.0 research loop needs an identity **before** a Proposal, an
approval or a TaskRef exists: a model planning call may be charged, retried and
reconciled while no scientific plan has been proposed yet.  This module owns
that pre-planning vocabulary and the submit-intent lifecycle around it:

* :class:`ResearchIdentity` — the server-allocated ``principal / project /
  research / session`` tuple.  Every segment is derived from a server-verified
  :class:`~acp_services.workspace.command_identity.CommandIdentity`; a client
  can neither choose a project nor invent a research/session id.
* :func:`planning_claim` — the frozen planning claim id
  ``plan|<project>|<research>|<session>|<round>|<call_seq>`` (D-IDENT-04) and
  the model receipt id
  ``modelrcpt|<project>|<research>|<session>|<round>|<call_seq>|<request_digest>``
  (D-IDENT-05).  Both are *planning reservation* identities: they never enter
  the submit idempotency ledger and never name a job/attempt.
* :class:`SubmitIntent` — the RPH-derived, opaque submit key bound to
  ``project + research + round + plan_digest`` (D-IDENT-07).  The platform
  stores and echoes the key verbatim; it never completes, rewrites or
  re-derives it.  A changed ``plan_digest`` cannot reuse the old key: the old
  approval is invalidated and a new round/revision with a new approval intent
  is opened (:func:`supersede_approval_for_plan_change`).
* :func:`classify_crash_point` — the frozen crash-point table.  Each stage
  states what is *already persisted*, what is allowed and what is forbidden, so
  recovery is decided from durable facts (RPH intent store, approval CAS,
  research-record CAS, event cursor) and never from in-memory state or a WAL.
* :func:`recover_research_session` — reuses
  :class:`~acp_services.runtime.research_events.ResearchRecoveryCoordinator`
  (opaque cursor + snapshot + action-ledger reconciliation) and deliberately
  does **not** call ``JobManager.resume()``, which is review-only
  (``WAITING_REVIEW``) and unrelated to a research session.

Digests come from :mod:`acp_services.runtime.canonical_digest`; the planning
budget id comes from :mod:`acp_services.runtime.research_budget`.  Neither rule
is re-implemented here.

Isolation: stdlib + first-party ``acp_services`` only; this module never
imports ``acp`` and never writes an execution fact (no job row, no TaskRef bind
beyond the evidence-only approval bind).

Author: ACP Services (B lane, M1-02).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from acp_services.runtime.approvals import (
    Approval,
    ApprovalError,
    ApprovalService,
)
from acp_services.runtime.canonical_digest import (
    CANONICAL_DIGEST_VERSION,
    CanonicalDigestError,
    canonical_digest,
)
from acp_services.runtime.research_budget import budget_id_for
from acp_services.runtime.research_events import (
    RecoveryOutcome,
    ResearchRecoveryCoordinator,
)
from acp_services.workspace.command_identity import DEFAULT_COMMAND_PROJECT, CommandIdentity

__all__ = [
    "CONTRACT_DOCUMENT",
    "CRASH_POINT_RULES",
    "CRASH_POINT_STAGES",
    "DECISION_VALUES",
    "EVIDENCE_REF_FIELDS",
    "MODEL_RECEIPT_PREFIX",
    "PLANNING_CLAIM_PREFIX",
    "PLANNING_UNIT",
    "RESEARCH_ID_PREFIX",
    "RESEARCH_SESSION_FIELDS",
    "RESEARCH_SESSION_SCHEMA",
    "SESSION_ID_PREFIX",
    "STAGE_AWAITING",
    "STAGE_BEFORE_SUBMIT",
    "STAGE_EVIDENCE_READ",
    "STAGE_RECORD_CAS_PENDING",
    "STAGE_SETTLED",
    "STAGE_SUBMITTED_UNACKED",
    "SUBMIT_INTENT_FIELDS",
    "SUBMIT_INTENT_STATES",
    "TASK_BINDING_FIELDS",
    "WAIT_CURSOR_FIELDS",
    "CrashPointRule",
    "EvidenceRef",
    "PlanChangeOutcome",
    "PlanningClaim",
    "ResearchIdentity",
    "ResearchSession",
    "ResearchSessionConflictError",
    "ResearchSessionError",
    "ResearchSessionValidationError",
    "SessionRecovery",
    "SubmitIntent",
    "TaskBinding",
    "classify_crash_point",
    "is_submit_ledger_key",
    "model_receipt_id",
    "parse_planning_claim_id",
    "planning_claim",
    "recover_research_session",
    "supersede_approval_for_plan_change",
]

#: Frozen contract identity of the research-session document
#: (``runtime/research-session.v1.md``).
RESEARCH_SESSION_SCHEMA = "acp2.runtime.research-session.v1"

#: Planning claim / model receipt namespaces (D-IDENT-04 / D-IDENT-05).
PLANNING_CLAIM_PREFIX = "plan"
MODEL_RECEIPT_PREFIX = "modelrcpt"
CLAIM_SEPARATOR = "|"

#: The budget unit a planning claim is charged against (B17 vocabulary).
PLANNING_UNIT = "model_request"

RESEARCH_ID_PREFIX = "res-"
SESSION_ID_PREFIX = "ses-"

#: Research decision vocabulary (identical to C's ResearchRecord schema).
DECISION_VALUES: tuple[str, ...] = ("accept", "revise", "reject", "defer", "escalate")

SUBMIT_INTENT_PREPARED = "prepared"
SUBMIT_INTENT_SUBMITTED = "submitted"
SUBMIT_INTENT_SETTLED = "settled"
SUBMIT_INTENT_SUPERSEDED = "superseded"
SUBMIT_INTENT_STATES: tuple[str, ...] = (
    SUBMIT_INTENT_PREPARED,
    SUBMIT_INTENT_SUBMITTED,
    SUBMIT_INTENT_SETTLED,
    SUBMIT_INTENT_SUPERSEDED,
)

# ---------------------------------------------------------------------------
# Frozen field tables (the contract document renders these verbatim)
# ---------------------------------------------------------------------------

#: Fields of one research-session identity (allocated before the first plan).
RESEARCH_SESSION_FIELDS: tuple[str, ...] = (
    "principal_id",
    "project_id",
    "research_id",
    "session_id",
    "round",
    "revision",
)

#: Fields of one persisted submit intent.
SUBMIT_INTENT_FIELDS: tuple[str, ...] = (
    "intent_key",
    "project_id",
    "research_id",
    "session_id",
    "round",
    "plan_digest",
    "approval_ref",
    "revision",
    "state",
)

#: Fields of one TaskRef binding (C TaskRef + project scope).
TASK_BINDING_FIELDS: tuple[str, ...] = ("project_id", "job_id", "attempt", "release_id")

#: Fields of the opaque wait cursor record.
WAIT_CURSOR_FIELDS: tuple[str, ...] = ("cursor", "recorded_revision")

#: Fields of one evidence reference (C ResearchRecord ``evidence_ref``).
EVIDENCE_REF_FIELDS: tuple[str, ...] = ("evidence_id", "kind", "sha256", "relative_path")

# ---------------------------------------------------------------------------
# Crash-point stages (the frozen recovery table)
# ---------------------------------------------------------------------------

STAGE_BEFORE_SUBMIT = "before_submit"
STAGE_SUBMITTED_UNACKED = "submitted_unacked"
STAGE_AWAITING = "awaiting"
STAGE_EVIDENCE_READ = "evidence_read"
STAGE_RECORD_CAS_PENDING = "record_cas_pending"
STAGE_SETTLED = "settled"

CRASH_POINT_STAGES: tuple[str, ...] = (
    STAGE_BEFORE_SUBMIT,
    STAGE_SUBMITTED_UNACKED,
    STAGE_AWAITING,
    STAGE_EVIDENCE_READ,
    STAGE_RECORD_CAS_PENDING,
    STAGE_SETTLED,
)

_ACTION_REPLAY_CURSOR = "replay_cursor_or_snapshot"
_ACTION_RESUBMIT_SAME_KEY = "resubmit_same_intent_key"
_ACTION_DERIVE_NEW_KEY = "derive_intent_key_and_persist"
_ACTION_OBSERVE = "observe_task_status"
_ACTION_READ_EVIDENCE = "read_digest_bound_evidence"
_ACTION_CAS_RECORD = "cas_record_with_observed_revision"
_ACTION_REPAIR_RECORD = "cas_record_only_after_reread"
_ACTION_ACCEPT = "record_decision"
_FORBID_NEW_KEY = "derive_a_second_intent_key_for_the_same_plan"
_FORBID_SUBMIT_WITHOUT_INTENT = "submit_without_a_persisted_intent_key"
_FORBID_RESUME = "call_job_manager_review_resume"
_FORBID_FABRICATE_TASK_REF = "fabricate_a_task_ref_from_a_missing_receipt"
_FORBID_RESUBMIT = "resubmit_a_task_that_already_has_a_task_ref"
_FORBID_BLIND_CAS = "blind_cas_on_a_stale_record_revision"
_FORBID_PUBLISH_DECISION = "publish_a_decision_without_digest_bound_evidence"
_FORBID_REUSE_APPROVAL = "reuse_the_previous_approval_for_a_changed_plan"

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ResearchSessionError(RuntimeError):
    """Base error with a stable contract failure shape."""

    code = "research_session_error"

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "params": {"rule": self.rule}}


class ResearchSessionValidationError(ResearchSessionError):
    """The identity, claim or intent violates the frozen contract."""

    code = "invalid_research_session"


class ResearchSessionConflictError(ResearchSessionError):
    """A stale revision or a changed plan digest is a typed conflict."""

    code = "revision_conflict"

    def __init__(
        self,
        rule: str,
        message: str,
        *,
        next_round: int | None = None,
        current_revision: int | None = None,
    ) -> None:
        super().__init__(rule, message)
        self.next_round = next_round
        self.current_revision = current_revision

    def detail(self) -> dict[str, Any]:
        payload = super().detail()
        payload["params"]["next_round"] = self.next_round
        payload["params"]["current_revision"] = self.current_revision
        return payload


# ---------------------------------------------------------------------------
# Segment helpers
# ---------------------------------------------------------------------------

_SEGMENT_BANNED = ("|", "\n", "\r")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
#: The planning-claim grammar, parsed by the single implementation below.
_CLAIM_RE = re.compile(
    r"^plan\|(?P<project>[^|]+)\|(?P<research>[^|]+)\|(?P<session>[^|]+)"
    r"\|(?P<round>\d+)\|(?P<call_seq>[1-9]\d*)$"
)
_RECEIPT_RE = re.compile(
    r"^modelrcpt\|(?P<project>[^|]+)\|(?P<research>[^|]+)\|(?P<session>[^|]+)"
    r"\|(?P<round>\d+)\|(?P<call_seq>[1-9]\d*)\|(?P<request_digest>sha256:[0-9a-f]{64})$"
)


def _require_segment(value: Any, label: str) -> str:
    """Return *value* as a claim/intent segment or raise."""
    text = str(value or "")
    if not text or not text.strip():
        raise ResearchSessionValidationError("missing_segment", f"{label} is required")
    if text != text.strip():
        raise ResearchSessionValidationError(
            "invalid_segment", f"{label} must not carry leading/trailing whitespace"
        )
    for banned in _SEGMENT_BANNED:
        if banned in text:
            raise ResearchSessionValidationError(
                "invalid_segment", f"{label} must not contain {banned!r}"
            )
    if len(text) > 128:
        raise ResearchSessionValidationError("invalid_segment", f"{label} is too long")
    return text


def _require_identifier(value: Any, label: str, *, prefix: str) -> str:
    text = _require_segment(value, label)
    if not text.startswith(prefix) or _ID_RE.match(text) is None:
        raise ResearchSessionValidationError(
            "invalid_identifier", f"{label} must match {prefix}<id> ({_ID_RE.pattern})"
        )
    return text


def _require_digest(value: Any, label: str) -> str:
    text = str(value or "")
    if _DIGEST_RE.match(text) is None:
        raise ResearchSessionValidationError(
            "invalid_digest", f"{label} must be sha256:<64 lowercase hex>"
        )
    return text


def _non_negative_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ResearchSessionValidationError(
            "invalid_integer", f"{label} must be an integer"
        ) from exc
    if number < 0:
        raise ResearchSessionValidationError("invalid_integer", f"{label} must be >= 0")
    return number


def _positive_int(value: Any, label: str) -> int:
    number = _non_negative_int(value, label)
    if number < 1:
        raise ResearchSessionValidationError("invalid_integer", f"{label} must be >= 1")
    return number


# ---------------------------------------------------------------------------
# Identity (allocated before the first planning call)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResearchIdentity:
    """The server-allocated ``principal / project / research / session`` tuple.

    The tuple exists before any Proposal or TaskRef: planning budget, planning
    claims and model receipts are addressed by it.  Every member is supplied by
    the server; a client payload can never set ``project_id``, ``research_id``
    or ``session_id``.
    """

    principal_id: str
    project_id: str
    research_id: str
    session_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "principal_id", _require_segment(self.principal_id, "principal_id")
        )
        object.__setattr__(self, "project_id", _require_segment(self.project_id, "project_id"))
        object.__setattr__(
            self,
            "research_id",
            _require_identifier(self.research_id, "research_id", prefix=RESEARCH_ID_PREFIX),
        )
        object.__setattr__(
            self,
            "session_id",
            _require_identifier(self.session_id, "session_id", prefix=SESSION_ID_PREFIX),
        )

    @classmethod
    def allocate(cls, identity: CommandIdentity, *, session_seed: str = "") -> ResearchIdentity:
        """Derive the research identity from one verified command identity.

        Args:
            identity: The server-resolved command identity (M0-04).  Its
                ``principal_id`` and project are authoritative; an unresolved
                project falls back to
                :data:`~acp_services.workspace.command_identity.DEFAULT_COMMAND_PROJECT`
                exactly like the idempotency scope does.
            session_seed: A non-secret, non-authoritative seed (for example an
                already-persisted client request id).  It can only *separate* two
                sessions of the same principal/project; it can never select a
                principal, a project or another principal's research.

        Returns:
            The deterministic identity tuple for that server identity and seed.
        """
        if not isinstance(identity, CommandIdentity):
            raise ResearchSessionValidationError(
                "invalid_identity", "allocate requires a server-resolved CommandIdentity"
            )
        project = str(identity.project_id or DEFAULT_COMMAND_PROJECT)
        base = {
            "entry_class": str(identity.entry_class),
            "principal_id": str(identity.principal_id),
            "project_id": project,
            "session_seed": str(session_seed or ""),
        }
        try:
            research_digest = canonical_digest({**base, "identity_kind": "research"})
            session_digest = canonical_digest({**base, "identity_kind": "session"})
        except CanonicalDigestError as exc:  # pragma: no cover - identity fields are canonical
            raise ResearchSessionValidationError(exc.rule, str(exc)) from exc
        return cls(
            principal_id=str(identity.principal_id),
            project_id=project,
            research_id=RESEARCH_ID_PREFIX + research_digest.split(":", 1)[1][:24],
            session_id=SESSION_ID_PREFIX + session_digest.split(":", 1)[1][:24],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RESEARCH_SESSION_SCHEMA,
            "principal_id": self.principal_id,
            "project_id": self.project_id,
            "research_id": self.research_id,
            "session_id": self.session_id,
        }


# ---------------------------------------------------------------------------
# Planning claim / model receipt (D-IDENT-04 / D-IDENT-05)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanningClaim:
    """One planning/model reservation identity (never a submit ledger key)."""

    claim_id: str
    project_id: str
    research_id: str
    session_id: str
    round: int
    call_seq: int
    request_digest: str
    model_receipt_id: str
    budget_id: str
    unit: str = PLANNING_UNIT
    canonical_digest_version: str = CANONICAL_DIGEST_VERSION

    @property
    def segments(self) -> tuple[str, str, str, str, str, str]:
        """The six claim segments (prefix + the five identity/round segments)."""
        return (
            PLANNING_CLAIM_PREFIX,
            self.project_id,
            self.research_id,
            self.session_id,
            str(self.round),
            str(self.call_seq),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "project_id": self.project_id,
            "research_id": self.research_id,
            "session_id": self.session_id,
            "round": self.round,
            "call_seq": self.call_seq,
            "request_digest": self.request_digest,
            "model_receipt_id": self.model_receipt_id,
            "budget_id": self.budget_id,
            "unit": self.unit,
            "canonical_digest_version": self.canonical_digest_version,
            "is_submit_ledger_key": False,
        }


def planning_claim_id(
    *, project_id: str, research_id: str, session_id: str, round: int, call_seq: int
) -> str:
    """Build ``plan|<project>|<research>|<session>|<round>|<call_seq>``.

    Every segment is non-empty and contains no ``|``; ``call_seq`` is a
    positive integer that increases per ``(project, research, session, round)``.
    """
    segments = (
        PLANNING_CLAIM_PREFIX,
        _require_segment(project_id, "project_id"),
        _require_identifier(research_id, "research_id", prefix=RESEARCH_ID_PREFIX),
        _require_identifier(session_id, "session_id", prefix=SESSION_ID_PREFIX),
        str(_non_negative_int(round, "round")),
        str(_positive_int(call_seq, "call_seq")),
    )
    return CLAIM_SEPARATOR.join(segments)


def model_receipt_id(
    *,
    project_id: str,
    research_id: str,
    session_id: str,
    round: int,
    call_seq: int,
    request_digest: str,
) -> str:
    """Build ``modelrcpt|<project>|<research>|<session>|<round>|<call_seq>|<digest>``.

    The receipt is the settlement key of exactly one model call.  It names no
    job and no attempt: a planning call happens before any task exists and a
    rejected proposal still consumed model budget.
    """
    segments = (
        MODEL_RECEIPT_PREFIX,
        _require_segment(project_id, "project_id"),
        _require_identifier(research_id, "research_id", prefix=RESEARCH_ID_PREFIX),
        _require_identifier(session_id, "session_id", prefix=SESSION_ID_PREFIX),
        str(_non_negative_int(round, "round")),
        str(_positive_int(call_seq, "call_seq")),
        _require_digest(request_digest, "request_digest"),
    )
    receipt = CLAIM_SEPARATOR.join(segments)
    if _RECEIPT_RE.match(receipt) is None:  # pragma: no cover - defensive
        raise ResearchSessionValidationError("invalid_receipt", "model receipt id is malformed")
    return receipt


def parse_planning_claim_id(value: str) -> tuple[str, str, str, int, int]:
    """Parse a claim id into ``(project, research, session, round, call_seq)``."""
    match = _CLAIM_RE.match(str(value or ""))
    if match is None:
        raise ResearchSessionValidationError(
            "invalid_claim", f"malformed planning claim id {value!r}"
        )
    return (
        match.group("project"),
        match.group("research"),
        match.group("session"),
        int(match.group("round")),
        int(match.group("call_seq")),
    )


def planning_claim(
    identity: ResearchIdentity,
    *,
    round: int,
    call_seq: int,
    request: Mapping[str, Any],
) -> PlanningClaim:
    """Derive the planning claim and model receipt for one model call.

    Args:
        identity: The server-allocated research identity.
        round: The research round the call belongs to (>= 0).
        call_seq: Monotonic call counter per ``(project, research, session,
            round)`` (>= 1).
        request: The canonical planning request body.  Its digest comes from
            :func:`acp_services.runtime.canonical_digest.canonical_digest`; this
            module never hashes by itself.

    Returns:
        The claim carrying its request digest, model receipt id and the
        server-derived planning budget id.
    """
    if not isinstance(identity, ResearchIdentity):
        raise ResearchSessionValidationError("invalid_identity", "a ResearchIdentity is required")
    if not isinstance(request, Mapping):
        raise ResearchSessionValidationError(
            "invalid_request", "planning request must be an object"
        )
    try:
        request_digest = canonical_digest(dict(request))
    except CanonicalDigestError as exc:
        raise ResearchSessionValidationError(exc.rule, str(exc)) from exc
    claim_id = planning_claim_id(
        project_id=identity.project_id,
        research_id=identity.research_id,
        session_id=identity.session_id,
        round=round,
        call_seq=call_seq,
    )
    receipt = model_receipt_id(
        project_id=identity.project_id,
        research_id=identity.research_id,
        session_id=identity.session_id,
        round=round,
        call_seq=call_seq,
        request_digest=request_digest,
    )
    # The planning-budget identity is project + research/session, not a
    # proposal_ref that does not exist yet (M1-04c additive exception).
    planning_ref = f"planning:{identity.research_id}:{identity.session_id}"
    return PlanningClaim(
        claim_id=claim_id,
        project_id=identity.project_id,
        research_id=identity.research_id,
        session_id=identity.session_id,
        round=_non_negative_int(round, "round"),
        call_seq=_positive_int(call_seq, "call_seq"),
        request_digest=request_digest,
        model_receipt_id=receipt,
        budget_id=budget_id_for(identity.project_id, planning_ref),
    )


def is_submit_ledger_key(value: str) -> bool:
    """Whether *value* is a submit-ledger key (a planning claim never is)."""
    text = str(value or "")
    return (
        bool(text)
        and not text.startswith(f"{PLANNING_CLAIM_PREFIX}{CLAIM_SEPARATOR}")
        and not (text.startswith(f"{MODEL_RECEIPT_PREFIX}{CLAIM_SEPARATOR}"))
    )


# ---------------------------------------------------------------------------
# Submit intent / task binding / session value
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubmitIntent:
    """One persisted submit intent (the key is RPH-derived and opaque).

    ``intent_key`` is written by the client before it calls the platform and is
    echoed verbatim: :mod:`acp_services` never completes, rewrites or re-derives
    it.  ``plan_digest`` binds the intent to the approved science, so a changed
    plan cannot reuse the key.
    """

    intent_key: str
    project_id: str
    research_id: str
    session_id: str
    round: int
    plan_digest: str
    revision: int = 1
    approval_ref: str = ""
    state: str = SUBMIT_INTENT_PREPARED

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_key", _require_segment(self.intent_key, "intent_key"))
        object.__setattr__(self, "project_id", _require_segment(self.project_id, "project_id"))
        object.__setattr__(
            self,
            "research_id",
            _require_identifier(self.research_id, "research_id", prefix=RESEARCH_ID_PREFIX),
        )
        object.__setattr__(
            self,
            "session_id",
            _require_identifier(self.session_id, "session_id", prefix=SESSION_ID_PREFIX),
        )
        object.__setattr__(self, "round", _non_negative_int(self.round, "round"))
        object.__setattr__(self, "plan_digest", _require_digest(self.plan_digest, "plan_digest"))
        object.__setattr__(self, "revision", _positive_int(self.revision, "revision"))
        if str(self.state) not in SUBMIT_INTENT_STATES:
            raise ResearchSessionValidationError(
                "invalid_state", f"unknown submit-intent state {self.state!r}"
            )
        if self.approval_ref and not _ID_RE.match(str(self.approval_ref)):
            raise ResearchSessionValidationError("invalid_identifier", "approval_ref is malformed")

    def matches_plan(self, plan_digest: str) -> bool:
        """Whether this intent was derived for *plan_digest*."""
        return str(plan_digest) == self.plan_digest

    def with_state(self, state: str) -> SubmitIntent:
        """Return the same intent in another lifecycle state (no key change)."""
        return replace(self, state=state)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_key": self.intent_key,
            "project_id": self.project_id,
            "research_id": self.research_id,
            "session_id": self.session_id,
            "round": self.round,
            "plan_digest": self.plan_digest,
            "approval_ref": self.approval_ref,
            "revision": self.revision,
            "state": self.state,
        }


@dataclass(frozen=True, slots=True)
class TaskBinding:
    """The TaskRef a submit actually produced (evidence, not an approval fact)."""

    project_id: str
    job_id: str
    attempt: int
    release_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _require_segment(self.project_id, "project_id"))
        object.__setattr__(self, "job_id", _require_segment(self.job_id, "job_id"))
        object.__setattr__(self, "attempt", _positive_int(self.attempt, "attempt"))
        object.__setattr__(self, "release_id", _require_segment(self.release_id, "release_id"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "release_id": self.release_id,
        }


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """One digest-bound evidence reference of a research round."""

    evidence_id: str
    kind: str
    sha256: str
    relative_path: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _require_segment(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "kind", _require_segment(self.kind, "kind"))
        object.__setattr__(self, "sha256", _require_digest(self.sha256, "sha256"))
        if self.relative_path is not None:
            object.__setattr__(
                self, "relative_path", _require_segment(self.relative_path, "relative_path")
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "sha256": self.sha256,
            "relative_path": self.relative_path,
        }


@dataclass(frozen=True, slots=True)
class ResearchSession:
    """The persisted research-session value (revision-guarded).

    The session is *not* an authority for execution: the approval CAS, the
    research-record CAS and the RPH intent store are.  It carries the frozen
    ``round / revision / submit_intent / task_binding / wait_cursor /
    evidence_ref / decision`` fields and applies one optimistic CAS per
    transition.
    """

    identity: ResearchIdentity
    round: int = 0
    revision: int = 1
    submit_intent: SubmitIntent | None = None
    task_binding: TaskBinding | None = None
    wait_cursor: str = ""
    wait_cursor_revision: int = 0
    evidence_refs: tuple[EvidenceRef, ...] = ()
    decision: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ResearchIdentity):
            raise ResearchSessionValidationError(
                "invalid_identity", "a ResearchIdentity is required"
            )
        object.__setattr__(self, "round", _non_negative_int(self.round, "round"))
        object.__setattr__(self, "revision", _positive_int(self.revision, "revision"))
        if self.decision and str(self.decision) not in DECISION_VALUES:
            raise ResearchSessionValidationError(
                "invalid_decision", f"unknown research decision {self.decision!r}"
            )

    # -- construction ------------------------------------------------------ #

    @classmethod
    def start(cls, identity: ResearchIdentity) -> ResearchSession:
        """Open round 0 at revision 1 for *identity*."""
        return cls(identity=identity)

    # -- transitions ------------------------------------------------------- #

    def record_submit_intent(
        self, intent: SubmitIntent, *, expected_revision: int | None = None
    ) -> ResearchSession:
        """Persist the first intent of this round (CAS on ``revision``).

        A second intent for the same round is only legal when it is the same
        intent (same key + digest).  A different digest is a typed
        ``plan_digest_changed`` conflict carrying ``next_round``: the caller must
        open a new round instead of reusing the old key.
        """
        self._require_intent_scope(intent)
        self._expect_revision(expected_revision)
        existing = self.submit_intent
        if existing is not None:
            if existing.intent_key == intent.intent_key and existing.matches_plan(
                intent.plan_digest
            ):
                return self
            if not existing.matches_plan(intent.plan_digest):
                raise ResearchSessionConflictError(
                    "plan_digest_changed",
                    "the round already holds an intent for a different plan digest; "
                    "open a new round",
                    next_round=self.round + 1,
                    current_revision=self.revision,
                )
            raise ResearchSessionConflictError(
                "intent_key_reuse",
                "the round already holds an intent with another key for the same plan",
                next_round=self.round,
                current_revision=self.revision,
            )
        return replace(self, submit_intent=intent, revision=self.revision + 1, decision="")

    def bind_task(
        self, binding: TaskBinding, *, expected_revision: int | None = None
    ) -> ResearchSession:
        """Record the TaskRef a submit produced (idempotent by ``job_id``)."""
        if binding.project_id != self.identity.project_id:
            raise ResearchSessionValidationError(
                "project_mismatch", "task binding crosses the session project"
            )
        if self.task_binding == binding:
            return self
        if self.task_binding is not None:
            raise ResearchSessionConflictError(
                "task_binding_conflict",
                "the session already binds another TaskRef",
                current_revision=self.revision,
            )
        self._expect_revision(expected_revision)
        return replace(self, task_binding=binding, revision=self.revision + 1)

    def record_wait_cursor(
        self, cursor: str, *, expected_revision: int | None = None
    ) -> ResearchSession:
        """Store the opaque wait cursor (never parsed, never authority)."""
        text = str(cursor or "")
        if not text:
            raise ResearchSessionValidationError("invalid_cursor", "wait cursor is required")
        self._expect_revision(expected_revision)
        return replace(
            self,
            wait_cursor=text,
            wait_cursor_revision=self.revision + 1,
            revision=self.revision + 1,
        )

    def add_evidence(
        self, ref: EvidenceRef, *, expected_revision: int | None = None
    ) -> ResearchSession:
        """Append one digest-bound evidence reference (idempotent by id+digest)."""
        if not isinstance(ref, EvidenceRef):
            raise ResearchSessionValidationError("invalid_evidence", "an EvidenceRef is required")
        for current in self.evidence_refs:
            if current.evidence_id == ref.evidence_id:
                if current == ref:
                    return self
                raise ResearchSessionConflictError(
                    "evidence_digest_mismatch",
                    "the evidence id already carries another digest",
                    current_revision=self.revision,
                )
        self._expect_revision(expected_revision)
        return replace(
            self,
            evidence_refs=(*self.evidence_refs, ref),
            revision=self.revision + 1,
        )

    def record_decision(
        self, decision: str, *, expected_revision: int | None = None
    ) -> ResearchSession:
        """Record the research decision for this round (never a task status)."""
        if str(decision) not in DECISION_VALUES:
            raise ResearchSessionValidationError(
                "invalid_decision", f"unknown research decision {decision!r}"
            )
        if self.decision == decision:
            return self
        if self.decision:
            raise ResearchSessionConflictError(
                "decision_already_recorded",
                "the round already carries a decision; open a new round",
                next_round=self.round + 1,
                current_revision=self.revision,
            )
        self._expect_revision(expected_revision)
        return replace(self, decision=str(decision), revision=self.revision + 1)

    def next_round(
        self, intent: SubmitIntent | None = None, *, expected_revision: int | None = None
    ) -> ResearchSession:
        """Open ``round + 1`` with a fresh intent (the old key is not reused)."""
        self._expect_revision(expected_revision)
        if intent is not None:
            if intent.round != self.round + 1:
                raise ResearchSessionValidationError(
                    "invalid_round", "the new intent must belong to round + 1"
                )
            self._require_intent_scope(intent, expected_round=self.round + 1)
        return replace(
            self,
            round=self.round + 1,
            revision=self.revision + 1,
            submit_intent=intent,
            task_binding=None,
            wait_cursor="",
            wait_cursor_revision=0,
            evidence_refs=(),
            decision="",
        )

    # -- projection -------------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RESEARCH_SESSION_SCHEMA,
            "identity": self.identity.to_dict(),
            "principal_id": self.identity.principal_id,
            "project_id": self.identity.project_id,
            "research_id": self.identity.research_id,
            "session_id": self.identity.session_id,
            "round": self.round,
            "revision": self.revision,
            "submit_intent": (None if self.submit_intent is None else self.submit_intent.to_dict()),
            "task_binding": None if self.task_binding is None else self.task_binding.to_dict(),
            "wait_cursor": self.wait_cursor,
            "wait_cursor_revision": self.wait_cursor_revision,
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "decision": self.decision,
        }

    # -- internals --------------------------------------------------------- #

    def _expect_revision(self, expected_revision: int | None) -> None:
        if expected_revision is None:
            return
        if int(expected_revision) != self.revision:
            raise ResearchSessionConflictError(
                "revision_conflict",
                f"expected revision {expected_revision}, current {self.revision}",
                current_revision=self.revision,
            )

    def _require_intent_scope(
        self, intent: SubmitIntent, *, expected_round: int | None = None
    ) -> None:
        if not isinstance(intent, SubmitIntent):
            raise ResearchSessionValidationError("invalid_intent", "a SubmitIntent is required")
        if (
            intent.project_id != self.identity.project_id
            or intent.research_id != self.identity.research_id
            or intent.session_id != self.identity.session_id
        ):
            raise ResearchSessionValidationError(
                "scope_mismatch", "the intent belongs to another research/session"
            )
        if intent.round != (self.round if expected_round is None else expected_round):
            raise ResearchSessionValidationError(
                "invalid_round", "the intent must belong to the session round"
            )


# ---------------------------------------------------------------------------
# Plan-digest change: invalidate the approval, open a new round/intent
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanChangeOutcome:
    """Result of invalidating an intent after the plan digest changed.

    The platform never invents the new client key: ``intent_key_required`` says
    the client must derive and persist a fresh key for the new round.
    """

    previous_approval: Approval | None
    approval: Approval
    previous_state: str
    round: int
    revision: int
    intent_key_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_approval_ref": (
                None if self.previous_approval is None else self.previous_approval.approval_id
            ),
            "previous_state": self.previous_state,
            "approval": self.approval.to_dict(),
            "round": self.round,
            "revision": self.revision,
            "intent_key_required": self.intent_key_required,
        }


def supersede_approval_for_plan_change(
    approvals: ApprovalService,
    *,
    intent: SubmitIntent,
    new_plan_digest: str,
    requester_principal: str,
    proposal_ref: str,
    proposal_digest: str,
    actor_principal: str = "",
    new_plan_definition: Mapping[str, Any] | None = None,
    budget: Mapping[str, Any] | None = None,
    allowed_change_range: Mapping[str, Any] | None = None,
    expires_at: str | None = None,
) -> PlanChangeOutcome:
    """Invalidate the old approval and request a new one for the new plan.

    The old intent/key is never rewritten.  When the previous approval is
    ``approved`` it is revoked through the normal optimistic CAS; a ``pending``
    or ``draft`` approval cannot authorize the new digest anyway, so it is left
    for the approver and only reported.  No job row, ``JobStatus`` or
    ``WAITING_REVIEW`` fact is written; the returned round/revision are the only
    new numbers, and the client must derive the new (opaque) key.

    Raises:
        ResearchSessionValidationError: the digest did not change or an input is
            malformed.
        ResearchSessionConflictError: the previous approval changed concurrently.
    """
    if not isinstance(approvals, ApprovalService):
        raise ResearchSessionValidationError("invalid_service", "an ApprovalService is required")
    if not isinstance(intent, SubmitIntent):
        raise ResearchSessionValidationError("invalid_intent", "a SubmitIntent is required")
    digest = _require_digest(new_plan_digest, "new_plan_digest")
    if digest == intent.plan_digest:
        raise ResearchSessionValidationError(
            "plan_unchanged", "supersede requires a changed plan_digest"
        )
    if intent.round < 0:  # pragma: no cover - SubmitIntent validates this
        raise ResearchSessionValidationError("invalid_round", "round must be >= 0")
    previous: Approval | None = None
    previous_state = ""
    if intent.approval_ref:
        previous = approvals.get(intent.approval_ref)
        if previous is None:
            raise ResearchSessionConflictError(
                "approval_not_found", "the intent names an approval that no longer exists"
            )
        previous_state = previous.state
        if previous.plan_digest == digest:
            raise ResearchSessionValidationError(
                "plan_unchanged", "the approval is already bound to the requested digest"
            )
        if previous.state == "approved":
            try:
                previous, _replayed = approvals.revoke(
                    previous.approval_id,
                    actor_principal=actor_principal or previous.requester_principal,
                    expected_revision=previous.revision,
                    reason="plan_digest_changed",
                )
            except ApprovalError as exc:
                raise ResearchSessionConflictError(
                    exc.rule,
                    f"the previous approval could not be revoked: {exc.message}",
                    next_round=intent.round + 1,
                ) from exc
    try:
        approval, _replayed = approvals.request(
            proposal_ref=proposal_ref,
            proposal_digest=proposal_digest,
            plan_digest=digest,
            project_id=intent.project_id,
            requester_principal=requester_principal,
            plan_definition=new_plan_definition,
            budget=budget,
            allowed_change_range=allowed_change_range,
            expires_at=expires_at,
        )
    except ApprovalError as exc:
        raise ResearchSessionConflictError(
            exc.rule,
            f"the new approval could not be requested: {exc.message}",
            next_round=intent.round + 1,
        ) from exc
    return PlanChangeOutcome(
        previous_approval=previous,
        approval=approval,
        previous_state=previous_state,
        round=intent.round + 1,
        revision=intent.revision + 1,
    )


# ---------------------------------------------------------------------------
# Crash-point table (frozen)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CrashPointRule:
    """One row of the frozen recovery table."""

    stage: str
    persisted_facts: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    next_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "persisted_facts": list(self.persisted_facts),
            "allowed_actions": list(self.allowed_actions),
            "forbidden_actions": list(self.forbidden_actions),
            "next_action": self.next_action,
        }


CRASH_POINT_RULES: Mapping[str, CrashPointRule] = {
    STAGE_BEFORE_SUBMIT: CrashPointRule(
        stage=STAGE_BEFORE_SUBMIT,
        persisted_facts=(
            "research identity (principal/project/research/session)",
            "planning claim + model receipt for every planning call",
            "approved approval bound to plan_digest",
            "submit intent key + plan_digest persisted by RPH",
        ),
        allowed_actions=(_ACTION_DERIVE_NEW_KEY, _ACTION_RESUBMIT_SAME_KEY, _ACTION_REPLAY_CURSOR),
        forbidden_actions=(
            _FORBID_NEW_KEY,
            _FORBID_SUBMIT_WITHOUT_INTENT,
            _FORBID_RESUME,
        ),
        next_action="submit_once_with_the_persisted_intent_key",
    ),
    STAGE_SUBMITTED_UNACKED: CrashPointRule(
        stage=STAGE_SUBMITTED_UNACKED,
        persisted_facts=(
            "submit intent key marked submitted (no receipt yet)",
            "the platform ledger row for that key, if the request landed",
            "no TaskRef bound on the session",
        ),
        allowed_actions=(_ACTION_RESUBMIT_SAME_KEY, _ACTION_REPLAY_CURSOR, _ACTION_OBSERVE),
        forbidden_actions=(
            _FORBID_NEW_KEY,
            _FORBID_FABRICATE_TASK_REF,
            _FORBID_RESUME,
        ),
        next_action="resubmit_the_same_key_and_accept_the_ledger_replay",
    ),
    STAGE_AWAITING: CrashPointRule(
        stage=STAGE_AWAITING,
        persisted_facts=(
            "TaskRef bound (project/job_id/attempt/release_id)",
            "wait cursor issued by the durable event stream",
            "the task projection is not terminal",
        ),
        allowed_actions=(_ACTION_REPLAY_CURSOR, _ACTION_OBSERVE, _ACTION_READ_EVIDENCE),
        forbidden_actions=(
            _FORBID_RESUBMIT,
            _FORBID_RESUME,
            _FORBID_FABRICATE_TASK_REF,
        ),
        next_action="replay_from_the_cursor_or_rebuild_from_the_snapshot",
    ),
    STAGE_EVIDENCE_READ: CrashPointRule(
        stage=STAGE_EVIDENCE_READ,
        persisted_facts=(
            "TaskRef bound and terminal",
            "evidence_refs read with verified sha256 digests",
            "research record at the last known record_revision",
        ),
        allowed_actions=(_ACTION_CAS_RECORD, _ACTION_REPLAY_CURSOR),
        forbidden_actions=(
            _FORBID_PUBLISH_DECISION,
            _FORBID_RESUBMIT,
            _FORBID_REUSE_APPROVAL,
            _FORBID_BLIND_CAS,
            _FORBID_RESUME,
        ),
        next_action="cas_the_record_with_expected_revision_and_those_evidence_refs",
    ),
    STAGE_RECORD_CAS_PENDING: CrashPointRule(
        stage=STAGE_RECORD_CAS_PENDING,
        persisted_facts=(
            "a record CAS was attempted; the outcome is unknown",
            "the last observed record_revision",
            "evidence_refs + decision to publish",
        ),
        allowed_actions=(_ACTION_REPAIR_RECORD, _ACTION_REPLAY_CURSOR),
        forbidden_actions=(
            _FORBID_BLIND_CAS,
            _FORBID_RESUBMIT,
            _FORBID_PUBLISH_DECISION,
            _FORBID_RESUME,
        ),
        next_action="reread_the_record_then_cas_only_on_the_observed_revision",
    ),
    STAGE_SETTLED: CrashPointRule(
        stage=STAGE_SETTLED,
        persisted_facts=(
            "the record carries the round decision at record_revision",
            "budget/model receipts are settled",
            "the task projection is terminal",
        ),
        allowed_actions=(_ACTION_REPLAY_CURSOR, "start_the_next_round"),
        forbidden_actions=(
            _FORBID_RESUBMIT,
            _FORBID_REUSE_APPROVAL,
            _FORBID_RESUME,
        ),
        next_action="no_reconciliation_needed",
    ),
}


def classify_crash_point(
    *,
    intent: SubmitIntent | None,
    task_binding: TaskBinding | None = None,
    wait_cursor: str = "",
    evidence_refs: Sequence[EvidenceRef] = (),
    decision: str = "",
    record_revision: int = 0,
    record_cas_pending: bool = False,
) -> CrashPointRule:
    """Classify the crash point from durable facts only.

    Args:
        intent: The persisted submit intent (``None`` = nothing submitted yet).
        task_binding: The bound TaskRef, when the session observed one.
        wait_cursor: The last durable cursor (informational; never parsed).
        evidence_refs: Digest-bound evidence already read.
        decision: The recorded research decision (empty = not decided).
        record_revision: The last known ``record_revision`` of the record.
        record_cas_pending: Whether a record CAS was attempted and its outcome is
            unknown (an RPH-persisted flag, not a memory-only guess).

    Returns:
        The matching :class:`CrashPointRule` of the frozen table.
    """
    del wait_cursor  # the cursor itself never decides the stage
    if record_cas_pending:
        return CRASH_POINT_RULES[STAGE_RECORD_CAS_PENDING]
    if record_revision >= 1 and str(decision or "") in DECISION_VALUES:
        return CRASH_POINT_RULES[STAGE_SETTLED]
    if task_binding is not None:
        if evidence_refs and record_revision < 1:
            return CRASH_POINT_RULES[STAGE_EVIDENCE_READ]
        return CRASH_POINT_RULES[STAGE_AWAITING]
    if intent is None:
        return CRASH_POINT_RULES[STAGE_BEFORE_SUBMIT]
    if intent.state == SUBMIT_INTENT_SUBMITTED:
        return CRASH_POINT_RULES[STAGE_SUBMITTED_UNACKED]
    if intent.state in {SUBMIT_INTENT_SETTLED, SUBMIT_INTENT_SUPERSEDED}:
        return CRASH_POINT_RULES[STAGE_SETTLED]
    return CRASH_POINT_RULES[STAGE_BEFORE_SUBMIT]


# ---------------------------------------------------------------------------
# Recovery (reuses the B19 coordinator; never JobManager.resume)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SessionRecovery:
    """One research-session recovery pass (rule + coordinator outcome)."""

    rule: CrashPointRule
    outcome: RecoveryOutcome
    blocked_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule.to_dict(),
            "outcome": self.outcome.to_dict(),
            "blocked_reason": self.blocked_reason,
        }


def recover_research_session(
    coordinator: ResearchRecoveryCoordinator,
    *,
    cursor: str | None,
    session: ResearchSession,
    submit_key: str = "",
    request_digest: str = "",
    submit: Any = None,
) -> SessionRecovery:
    """Replay/reconcile once and classify the crash point.

    The B19 coordinator owns the cursor/snapshot/action-ledger semantics.  This
    function adds the research-session view: a projection that already carries a
    TaskRef counts as a binding even when the local session has not seen it, and
    a cursor reset is reported as ``blocked_reason`` so the caller re-reads the
    snapshot instead of re-submitting.

    A research session resume is **not** a ``JobManager.resume()`` call: that
    method only accepts ``WAITING_REVIEW`` jobs for manual review, while a
    research session may resume around a queued/running/completed task and never
    changes a job's control state.
    """
    if not isinstance(coordinator, ResearchRecoveryCoordinator):
        raise ResearchSessionValidationError(
            "invalid_coordinator", "a ResearchRecoveryCoordinator is required"
        )
    if not isinstance(session, ResearchSession):
        raise ResearchSessionValidationError("invalid_session", "a ResearchSession is required")
    intent = session.submit_intent
    key = submit_key or ("" if intent is None else intent.intent_key)
    digest = request_digest or ("" if intent is None else intent.plan_digest)
    outcome = coordinator.recover(cursor, submit_key=key, request_digest=digest, submit=submit)
    binding = session.task_binding
    projection_ref = outcome.projection.task_ref
    if binding is None and outcome.projection.submitted and isinstance(projection_ref, Mapping):
        binding = _binding_from_projection(session.identity.project_id, projection_ref)
    rule = classify_crash_point(
        intent=intent,
        task_binding=binding,
        wait_cursor=outcome.next_cursor,
        evidence_refs=session.evidence_refs,
        decision=session.decision,
    )
    blocked = f"cursor_reset_requires_snapshot:{outcome.reset_reason}" if outcome.reset else ""
    if rule.stage == STAGE_SUBMITTED_UNACKED and not blocked and not key:
        blocked = "missing_intent_key"
    return SessionRecovery(rule=rule, outcome=outcome, blocked_reason=blocked)


def _binding_from_projection(project_id: str, task_ref: Mapping[str, Any]) -> TaskBinding | None:
    """Best-effort TaskRef projection (a partial ref is not a binding)."""
    job_id = str(task_ref.get("job_id") or task_ref.get("task_id") or "")
    attempt = task_ref.get("attempt")
    release_id = str(task_ref.get("release_id") or task_ref.get("release") or "")
    if not job_id or attempt is None or not release_id:
        return None
    try:
        return TaskBinding(
            project_id=str(task_ref.get("project_id") or project_id),
            job_id=job_id,
            attempt=int(attempt),
            release_id=release_id,
        )
    except ResearchSessionValidationError:
        return None


#: The frozen contract document that renders the tables above.
CONTRACT_DOCUMENT = "docs/reference/next-change/acp2-contracts/runtime/research-session.v1.md"
