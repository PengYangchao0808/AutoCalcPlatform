"""Server-side approval policy: who may decide, and who may never (M2-03a / G12).

Before this module the approval decision accepted any verified principal that
held the project's ``control`` action, and requester/approver separation was a
UI convention.  The pre-M2-03 negative case (a browser/model payload naming an
approver is refused) lived only in the HTTP router, so any other entry that
called :class:`~acp_services.runtime.approvals.ApprovalService` directly could
bypass it.

This module is the *policy* layer — one decision, resolved from the same
server-side profile :func:`acp_services.runtime.submit_policy.resolve_policy_profile`
uses, so an agent credential cannot reach a decision by choosing another entry:

=====================  ==============================  ====================
profile                decide a pending approval       separation
=====================  ==============================  ====================
``local-single``       allowed (self-approval)         not required
``manual``             allowed                         required
``agent``              **denied** (agent_cannot_decide) required
``strict``             allowed                         required
=====================  ==============================  ====================

"Separated" means requester and approver are different verified principals.
The rule is enforced by the server; a payload field that names an approver,
role or tenant is refused before the service is reached
(:data:`APPROVER_PAYLOAD_FIELDS` is the single definition — the HTTP router
imports it instead of keeping a private copy).

A model/service credential is refused even when its profile is not ``agent``:
:data:`SUBJECT_KINDS_THAT_NEVER_DECIDE` is the authoritative list, and a service
subject is never a human approver.

Isolation: stdlib + first-party ``acp_services`` only (no ``acp`` import).

Author: ACP Services (B lane, M2-03a).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from acp_services.runtime.submit_policy import (
    SUBMIT_POLICY_ENV,
    PolicyProfile,
    resolve_policy_profile,
)
from acp_services.workspace.command_identity import SubjectKind

__all__ = [
    "APPROVAL_POLICY_SCHEMA",
    "APPROVER_PAYLOAD_FIELDS",
    "APPROVAL_DECISION_ACTIONS",
    "APPROVAL_POLICY_MATRIX",
    "SUBJECT_KINDS_THAT_NEVER_DECIDE",
    "ApprovalPolicy",
    "ApprovalPolicyDecision",
    "ApprovalPolicyError",
    "approval_policy_statement",
    "authorize_decision",
    "payload_approver_fields",
    "resolve_approval_policy",
]

#: Contract identity of the approval policy.
APPROVAL_POLICY_SCHEMA = "acp2.runtime.approval-policy.v1"

#: Payload fields that would impersonate the authenticated approver.  The single
#: definition: the application approval router imports this tuple.
APPROVER_PAYLOAD_FIELDS: tuple[str, ...] = (
    "approver",
    "approver_ref",
    "approver_id",
    "approver_source",
    "approved_by",
    "role",
    "roles",
    "tenant",
    "tenant_id",
    "principal_id",
)

#: Subject kinds that may never decide an approval (model/service credentials).
SUBJECT_KINDS_THAT_NEVER_DECIDE: frozenset[str] = frozenset({SubjectKind.SERVICE})

#: The three decision actions this policy governs.
APPROVAL_DECISION_ACTIONS: tuple[str, ...] = ("approve", "reject", "revoke")

#: The frozen matrix (documented here and asserted by the M2-03a tests).
APPROVAL_POLICY_MATRIX: tuple[dict[str, Any], ...] = (
    {
        "profile": "local-single",
        "decide": "allowed",
        "separation_of_duties": False,
        "rule": "local_operator_self_approval",
    },
    {
        "profile": "manual",
        "decide": "allowed",
        "separation_of_duties": True,
        "rule": "separation_required",
    },
    {
        "profile": "strict",
        "decide": "allowed",
        "separation_of_duties": True,
        "rule": "separation_required",
    },
    {
        "profile": "agent",
        "decide": "denied",
        "separation_of_duties": True,
        "rule": "agent_cannot_decide_approval",
    },
)


class ApprovalPolicyError(ValueError):
    """The policy input is malformed (never a silent allow)."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"{rule}: {message}")
        self.rule = rule
        self.message = message


@dataclass(frozen=True, slots=True)
class ApprovalPolicy:
    """The resolved approval policy of one credential/entry pair."""

    profile: PolicyProfile
    decisions_allowed: bool
    separation_of_duties: bool
    source: str = ""
    subject_kind: str = SubjectKind.USER

    @property
    def name(self) -> str:
        return self.profile.name

    @property
    def agent(self) -> bool:
        return self.profile.agent

    @property
    def self_approval_allowed(self) -> bool:
        return not self.separation_of_duties

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": APPROVAL_POLICY_SCHEMA,
            "profile": self.profile.to_dict(),
            "name": self.name,
            "decisions_allowed": self.decisions_allowed,
            "separation_of_duties": self.separation_of_duties,
            "self_approval_allowed": self.self_approval_allowed,
            "subject_kind": self.subject_kind,
            "source": self.source,
            "payload_identity_fields": list(APPROVER_PAYLOAD_FIELDS),
        }


@dataclass(frozen=True, slots=True)
class ApprovalPolicyDecision:
    """The verdict of one approval decision attempt."""

    allowed: bool
    rule: str
    policy: ApprovalPolicy
    message: str = ""
    params: Mapping[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", dict(self.params or {}))

    @property
    def http_status(self) -> int:
        return 200 if self.allowed else 403

    def detail(self) -> dict[str, Any]:
        return {
            "code": "access_denied" if not self.allowed else "ok",
            "message": self.message or self.rule,
            "params": {"rule": self.rule, **dict(self.params)},
            "retryable": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": APPROVAL_POLICY_SCHEMA,
            "allowed": self.allowed,
            "rule": self.rule,
            "message": self.message,
            "profile": self.policy.name,
            "separation_of_duties": self.policy.separation_of_duties,
            "params": dict(self.params),
        }


def resolve_approval_policy(
    identity: Any,
    *,
    environ: Mapping[str, str] | None = None,
) -> ApprovalPolicy:
    """Resolve one server-side approval policy (never from the payload).

    Args:
        identity: The verified command identity (or any object exposing
            ``subject_kind``/``key_id``/``permission_scope``).
        environ: Environment override for the profile resolution (tests).

    Returns:
        The :class:`ApprovalPolicy`.  An agent scoped credential, the deployment
        profile :data:`~acp_services.runtime.submit_policy.SUBMIT_POLICY_ENV`
        and the local-operator default all come from the same resolver the
        submit path uses — there is no second profile logic.
    """
    profile = resolve_policy_profile(identity, environ=environ)
    subject = str(getattr(identity, "subject_kind", "") or SubjectKind.USER)
    service_subject = subject in SUBJECT_KINDS_THAT_NEVER_DECIDE
    decisions_allowed = not profile.agent and not service_subject
    separation = profile.name != "local-single" or profile.keyed
    source = profile.source or "default"
    if service_subject and not profile.agent:
        source = "credential-subject"
    return ApprovalPolicy(
        profile=profile,
        decisions_allowed=decisions_allowed,
        separation_of_duties=bool(separation),
        source=source,
        subject_kind=subject,
    )


def authorize_decision(
    policy: ApprovalPolicy,
    *,
    action: str,
    approver_principal: str,
    requester_principal: str,
    decided_by: str | None = None,
) -> ApprovalPolicyDecision:
    """Decide whether *approver_principal* may apply *action* to an approval.

    Refusal precedence: credential kind → separation of duties → a decision
    already taken by another principal.  A wrong/expired state is the service's
    answer, not this policy's.

    Args:
        policy: The resolved policy.
        action: ``approve`` / ``reject`` / ``revoke``.
        approver_principal: The verified principal of the decision.
        requester_principal: The approval's recorded requester.
        decided_by: The approval's recorded approver (``None`` while pending).

    Returns:
        The :class:`ApprovalPolicyDecision`; ``allowed=False`` carries the rule
        the caller renders as ``403 access_denied``.
    """
    if action not in APPROVAL_DECISION_ACTIONS:
        raise ApprovalPolicyError("unknown_decision_action", f"unknown action {action!r}")
    approver = str(approver_principal or "").strip()
    if not approver:
        raise ApprovalPolicyError("missing_approver", "a verified approver principal is required")
    if policy.agent:
        return ApprovalPolicyDecision(
            allowed=False,
            rule="agent_cannot_decide_approval",
            policy=policy,
            message=(
                "an agent/model credential can request an approval but never decide one; "
                "the approver must be an authenticated human principal"
            ),
            params={"profile": policy.name, "action": action},
        )
    if policy.subject_kind in SUBJECT_KINDS_THAT_NEVER_DECIDE:
        return ApprovalPolicyDecision(
            allowed=False,
            rule="service_credential_cannot_decide_approval",
            policy=policy,
            message=(
                "a service/model credential never signs a scientific approval, "
                "regardless of its project permissions"
            ),
            params={"profile": policy.name, "subject_kind": policy.subject_kind, "action": action},
        )
    if decided_by and str(decided_by) != approver:
        return ApprovalPolicyDecision(
            allowed=False,
            rule="approval_decided_by_another_principal",
            policy=policy,
            message="this approval was already decided by another principal",
            params={"action": action},
        )
    if policy.separation_of_duties and approver == str(requester_principal or "").strip():
        return ApprovalPolicyDecision(
            allowed=False,
            rule="requester_approver_separation_required",
            policy=policy,
            message=(
                "this deployment profile requires the approver to be a different "
                "verified principal from the requester"
            ),
            params={"profile": policy.name, "action": action},
        )
    return ApprovalPolicyDecision(allowed=True, rule="decision_allowed", policy=policy)


def payload_approver_fields(payload: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Return the approver-impersonating fields a payload actually carries."""
    source = dict(payload or {})
    return tuple(
        sorted(
            name for name in APPROVER_PAYLOAD_FIELDS if source.get(name) not in (None, "", [], {})
        )
    )


def approval_policy_statement() -> dict[str, Any]:
    """The machine-readable approval-policy section for capability projections."""
    return {
        "schema": APPROVAL_POLICY_SCHEMA,
        "profile_env": SUBMIT_POLICY_ENV,
        "matrix": [dict(row) for row in APPROVAL_POLICY_MATRIX],
        "payload_identity_fields": list(APPROVER_PAYLOAD_FIELDS),
        "never_decide_subject_kinds": sorted(SUBJECT_KINDS_THAT_NEVER_DECIDE),
        "approver_source": "authenticated principal (never a payload field)",
        "separation_authority": "server policy profile (never client/UI self-discipline)",
    }
