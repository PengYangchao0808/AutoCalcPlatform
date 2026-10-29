"""The single submit classification point (M1-04b / D06 + G12).

Before this module the Controller answered "is this a research launch?" twice,
with two different field sets: :func:`acp.api.v1_routes._is_research_launch`
(``proposal_ref or budget or proposal_digest or plan_digest``) and
:func:`acp.api.v1_routes._enforce_approval`
(``proposal_ref or proposal_digest or plan_digest``).  A request carrying only
``budget`` was therefore "research" for the project-scope branch and "not
research" for the approval branch — an approval bypass that only needed a
budget field.  :func:`classify_submit` is the one decision:

* **one probe** over every research-signalling field,
* **one policy** resolved from a *server-side* credential scope/policy profile
  (never a payload field, and without inventing ``PrincipalKind.AGENT``),
* **one classification** with a frozen submit class, an approval requirement
  and an optional typed denial.

The frozen matrix (``SUBMIT_MATRIX``) is:

===========================  ==================  =====================  =========================
request shape                credential profile  submit_class           approval
===========================  ==================  =====================  =========================
proposal+plan (+budget)      any                 ``research``           required (server approval)
agent-scoped, no research    ``agent``           ``agent``              required (omit-all denied)
agent-scoped, budget only    ``agent``           ``agent``              denied (no downgrade)
agent-scoped, no proposal    ``agent``           ``agent``              denied (no downgrade)
plain task, local-single     ``local-single``    ``local-single``       not required (recorded)
plain task, keyed credential ``manual-legacy``   ``manual-legacy``      not required (recorded)
plain task, ``strict``       ``strict``          ``manual-legacy``      denied (profile refuses)
research fields incomplete   any                 ``research``           denied (typed field error)
===========================  ==================  =====================  =========================

"Agent key 的 omit-all、budget-only、缺 proposal 均不能降级" is the row set
above: an agent-scoped credential never falls back to ``local-single`` /
``manual-legacy``, and a missing field is a denial, not a downgrade.

Isolation: stdlib only; ``acp`` never imported.  The HTTP route passes the
server-resolved :class:`~acp_services.workspace.command_identity.CommandIdentity`
and an optional :class:`~acp_services.runtime.approvals.Approval`; this module
performs no I/O.

Author: ACP Services (B lane, M1-04b).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from acp_services.workspace.command_identity import (
    EntryClass,
    PermissionScope,
    SubjectKind,
)

__all__ = [
    "SUBMIT_CLASS_AGENT",
    "SUBMIT_CLASS_LOCAL_SINGLE",
    "SUBMIT_CLASS_MANUAL_LEGACY",
    "SUBMIT_CLASS_RESEARCH",
    "SUBMIT_CLASSES",
    "SUBMIT_MATRIX",
    "SUBMIT_POLICY_ENV",
    "SUBMIT_POLICY_SCHEMA",
    "PolicyProfile",
    "SubmitClassification",
    "SubmitPolicy",
    "SubmitProbe",
    "classify_submit",
    "commitment_required_fields",
    "resolve_policy_profile",
]

#: Contract identity of this classification vocabulary.
SUBMIT_POLICY_SCHEMA = "acp2.runtime.submit-policy.v1"

#: Server-side policy-profile override (deployment configuration, never a wire
#: field).  Values: ``local-single`` (default) / ``manual`` / ``agent`` / ``strict``.
SUBMIT_POLICY_ENV = "ACP_SUBMIT_POLICY_PROFILE"

SUBMIT_CLASS_RESEARCH = "research"
SUBMIT_CLASS_AGENT = "agent"
SUBMIT_CLASS_LOCAL_SINGLE = "local-single"
SUBMIT_CLASS_MANUAL_LEGACY = "manual-legacy"

SUBMIT_CLASSES: tuple[str, ...] = (
    SUBMIT_CLASS_RESEARCH,
    SUBMIT_CLASS_AGENT,
    SUBMIT_CLASS_LOCAL_SINGLE,
    SUBMIT_CLASS_MANUAL_LEGACY,
)

#: Frozen matrix rows (documented in the M1-04b report and asserted by tests).
SUBMIT_MATRIX: tuple[dict[str, str], ...] = (
    {
        "request": "proposal_ref+plan_digest(+budget, proposal_digest)",
        "profile": "any",
        "submit_class": SUBMIT_CLASS_RESEARCH,
        "approval": "required",
        "rule": "approval_required",
    },
    {
        "request": "no research field",
        "profile": "agent",
        "submit_class": SUBMIT_CLASS_AGENT,
        "approval": "required",
        "rule": "agent_approval_required",
    },
    {
        "request": "budget only",
        "profile": "agent",
        "submit_class": SUBMIT_CLASS_AGENT,
        "approval": "denied",
        "rule": "agent_budget_only_cannot_downgrade",
    },
    {
        "request": "plan/proposal digest without proposal_ref",
        "profile": "agent",
        "submit_class": SUBMIT_CLASS_AGENT,
        "approval": "denied",
        "rule": "agent_proposal_required",
    },
    {
        "request": "no research field",
        "profile": "local-single",
        "submit_class": SUBMIT_CLASS_LOCAL_SINGLE,
        "approval": "not_required",
        "rule": "legacy_allowed",
    },
    {
        "request": "no research field",
        "profile": "manual",
        "submit_class": SUBMIT_CLASS_MANUAL_LEGACY,
        "approval": "not_required",
        "rule": "legacy_allowed",
    },
    {
        "request": "no research field",
        "profile": "strict",
        "submit_class": SUBMIT_CLASS_MANUAL_LEGACY,
        "approval": "denied",
        "rule": "submit_requires_research_fields",
    },
)

#: Rules whose answer is a 403 ``access_denied`` (authorization, not shape).
_ACCESS_RULES = frozenset(
    {
        "approval_required",
        "agent_approval_required",
        "agent_budget_only_cannot_downgrade",
        "agent_proposal_required",
        "submit_requires_research_fields",
        "approval_binding_mismatch",
        "approval_revoked",
        "science_binding_unverifiable",
    }
)

#: Rules whose answer is a 400/422 ``input_invalid`` (request shape).
_INPUT_RULES = frozenset(
    {
        "plan_digest_required",
        "proposal_required",
        "clarification_required",
        "plan_digest_mismatch",
    }
)


@dataclass(frozen=True, slots=True)
class PolicyProfile:
    """The server-side policy profile of one credential/entry.

    Attributes:
        name: ``local-single`` / ``manual`` / ``agent`` / ``strict``.
        source: Where the profile came from (``deployment-env`` /
            ``credential-scope`` / ``local-operator`` / ``default``).
        credential_kind: ``operator`` / ``user`` / ``service`` / ``anonymous``.
        keyed: Whether the entry presented a service credential.
    """

    name: str = "local-single"
    source: str = "default"
    credential_kind: str = SubjectKind.OPERATOR
    keyed: bool = False

    @property
    def agent(self) -> bool:
        return self.name == "agent"

    @property
    def strict(self) -> bool:
        return self.name == "strict"

    @property
    def local_operator(self) -> bool:
        return self.name == "local-single" and not self.keyed

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "credential_kind": self.credential_kind,
            "keyed": self.keyed,
            "agent": self.agent,
        }


@dataclass(frozen=True, slots=True)
class SubmitProbe:
    """Every research-signalling field of one submit request (one field set)."""

    workflow: str = ""
    has_proposal_ref: bool = False
    has_proposal_digest: bool = False
    has_plan_digest: bool = False
    has_budget: bool = False
    has_science_inputs: bool = False

    @property
    def research_fields(self) -> tuple[str, ...]:
        """The research-signalling fields this request actually carries."""
        present: list[str] = []
        if self.has_proposal_ref:
            present.append("proposal_ref")
        if self.has_proposal_digest:
            present.append("proposal_digest")
        if self.has_plan_digest:
            present.append("plan_digest")
        if self.has_budget:
            present.append("budget")
        return tuple(present)

    @property
    def has_any(self) -> bool:
        return bool(self.research_fields)

    @property
    def complete(self) -> bool:
        """A complete research binding needs a proposal reference and a plan."""
        return self.has_proposal_ref and self.has_plan_digest

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SubmitProbe:
        """Build the probe from a raw request mapping (HTTP/MCP/CLI)."""

        def _present(name: str) -> bool:
            value = payload.get(name)
            if value is None:
                return False
            if isinstance(value, str):
                return bool(value.strip())
            if isinstance(value, (Mapping, list, tuple, set)):
                return bool(value)
            return True

        return cls(
            workflow=str(payload.get("workflow") or ""),
            has_proposal_ref=_present("proposal_ref"),
            has_proposal_digest=_present("proposal_digest"),
            has_plan_digest=_present("plan_digest"),
            has_budget=_present("budget"),
            has_science_inputs=_present("science") or _present("science_inputs"),
        )


@dataclass(frozen=True, slots=True)
class SubmitPolicy:
    """The resolved policy of one submit request."""

    profile: PolicyProfile
    require_approval: bool
    allow_legacy: bool
    submit_class: str
    project_scope_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SUBMIT_POLICY_SCHEMA,
            "profile": self.profile.to_dict(),
            "require_approval": self.require_approval,
            "allow_legacy": self.allow_legacy,
            "submit_class": self.submit_class,
            "project_scope_required": self.project_scope_required,
        }


@dataclass(frozen=True, slots=True)
class SubmitClassification:
    """The single submit decision (class + approval + optional denial)."""

    submit_class: str
    profile: PolicyProfile
    approval_required: bool
    allowed: bool
    rule: str = "legacy_allowed"
    message: str = ""
    params: Mapping[str, Any] = None  # type: ignore[assignment]
    research: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", dict(self.params or {}))

    @property
    def http_status(self) -> int:
        """The HTTP status this classification answers when it denies."""
        if self.allowed:
            return 200
        if self.rule in _ACCESS_RULES:
            return 403
        if self.rule in _INPUT_RULES:
            return 422 if self.rule == "clarification_required" else 400
        return 400

    def detail(self) -> dict[str, Any]:
        return {
            "code": "access_denied" if self.http_status == 403 else "input_invalid",
            "message": self.message or self.rule,
            "params": {"rule": self.rule, **dict(self.params)},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SUBMIT_POLICY_SCHEMA,
            "submit_class": self.submit_class,
            "profile": self.profile.to_dict(),
            "approval_required": self.approval_required,
            "allowed": self.allowed,
            "rule": self.rule,
            "research": self.research,
        }


def resolve_policy_profile(
    identity: Any,
    *,
    environ: Mapping[str, str] | None = None,
) -> PolicyProfile:
    """Resolve the server-side policy profile of one verified command identity.

    Precedence (all server-side, no payload influence):

    1. a credential whose scope is ``agent`` (issued by the server for an agent
       workload) is an agent credential — it can never be downgraded;
    2. the deployment policy profile ``ACP_SUBMIT_POLICY_PROFILE``
       (``agent`` / ``strict`` / ``manual``) applies to every non-operator entry;
    3. a trusted ``local-single`` operator entry is ``local-single``;
    4. any other keyed credential is ``manual``.

    Args:
        identity: A server-resolved
            :class:`~acp_services.workspace.command_identity.CommandIdentity`
            (or any object exposing ``key_id``/``actions``/``permission_scope``/
            ``subject_kind``).
        environ: Environment mapping override (tests); defaults to ``os.environ``.

    Returns:
        The resolved :class:`PolicyProfile`.
    """
    import os

    env = dict(os.environ if environ is None else environ)
    key_id = str(getattr(identity, "key_id", "") or "")
    scope = str(getattr(identity, "permission_scope", "") or "")
    subject = str(getattr(identity, "subject_kind", "") or SubjectKind.USER)
    keyed = bool(key_id) or scope == PermissionScope.API_KEY
    entry_class = str(getattr(identity, "entry_class", "") or EntryClass.HTTP)

    if scope == "agent" or str(env.get("ACP_CREDENTIAL_SCOPE") or "").strip() == "agent":
        return PolicyProfile(
            name="agent",
            source="credential-scope",
            credential_kind=subject,
            keyed=True,
        )

    configured = str(env.get(SUBMIT_POLICY_ENV) or "").strip().lower()
    if configured in {"agent", "strict", "manual", "local-single"}:
        if configured == "agent":
            return PolicyProfile(
                name="agent",
                source="deployment-env",
                credential_kind=subject,
                keyed=keyed,
            )
        if configured in {"strict", "manual"}:
            return PolicyProfile(
                name=configured,
                source="deployment-env",
                credential_kind=subject,
                keyed=keyed,
            )
        # An explicit local-single deployment profile never widens a keyed
        # credential into the local operator path.
        if keyed:
            return PolicyProfile(
                name="manual",
                source="deployment-env",
                credential_kind=subject,
                keyed=True,
            )
        return PolicyProfile(
            name="local-single",
            source="deployment-env",
            credential_kind=subject,
            keyed=False,
        )

    local_operator = (
        not keyed
        and subject == SubjectKind.OPERATOR
        and scope in {PermissionScope.LOCAL_SINGLE, PermissionScope.LOCAL_OPERATOR, ""}
        and entry_class in {EntryClass.HTTP, EntryClass.CLI, EntryClass.CONTROLLER}
    )
    if local_operator:
        return PolicyProfile(
            name="local-single",
            source="local-operator",
            credential_kind=subject,
            keyed=False,
        )
    return PolicyProfile(name="manual", source="default", credential_kind=subject, keyed=keyed)


def commitment_required_fields(probe: SubmitProbe, profile: PolicyProfile) -> tuple[str, ...]:
    """Fields this request must carry before any submit side effect.

    A research or agent submit must name a proposal and a plan; an agent
    credential must do so even when it sent nothing (omit-all).
    """
    if probe.has_any or profile.agent:
        required: list[str] = []
        if not probe.has_proposal_ref:
            required.append("proposal_ref")
        if not probe.has_plan_digest:
            required.append("plan_digest")
        return tuple(required)
    return ()


def classify_submit(
    probe: SubmitProbe,
    identity: Any,
    *,
    profile: PolicyProfile | None = None,
    environ: Mapping[str, str] | None = None,
    approval: Any | None = None,
    science_digest: str = "",
    approval_science_digest: str = "",
    defer_approval: bool = False,
) -> SubmitClassification:
    """Classify one submit and decide whether it may reach the manager.

    Args:
        probe: The request's research-signal probe.
        identity: The verified command identity (server-resolved).
        profile: Pre-resolved policy profile (avoids a second env read).
        environ: Environment override for profile resolution (tests).
        approval: The authorizing approval when the caller already resolved one
            (used only to name the binding rule; never to authorize here).
        science_digest: The science digest computed for this submit's proposal
            (empty when no proposal body is available).
        approval_science_digest: The science digest the approval recorded.
        defer_approval: When true, a *complete* research submit whose approval is
            resolved later (the HTTP route authorizes after this classification)
            is allowed with ``rule="approval_deferred"`` instead of denied.  It
            defers only the approval denial — field/shape/agent denials still
            apply, so a missing plan/proposal or an agent downgrade is never
            deferred.

    Returns:
        A :class:`SubmitClassification`.  ``allowed`` is false for every
        downgrade attempt; the caller raises :meth:`SubmitClassification.detail`.
    """
    policy_profile = profile or resolve_policy_profile(identity, environ=environ)
    research = probe.has_any

    if policy_profile.agent:
        # Agent credentials never downgrade: an omit-all, budget-only or
        # proposal-less submit is a denial with the same typed rule.
        if not probe.has_proposal_ref:
            rule = (
                "agent_budget_only_cannot_downgrade"
                if probe.has_budget and not probe.has_proposal_digest and not probe.has_plan_digest
                else "agent_proposal_required"
            )
            return SubmitClassification(
                submit_class=SUBMIT_CLASS_AGENT,
                profile=policy_profile,
                approval_required=True,
                allowed=False,
                rule=rule,
                message=(
                    "an agent credential must submit a proposal-bound plan; "
                    "budget-only and omit-all submits cannot fall back to a legacy path"
                ),
                research=True,
            )
        if not probe.has_plan_digest:
            return SubmitClassification(
                submit_class=SUBMIT_CLASS_AGENT,
                profile=policy_profile,
                approval_required=True,
                allowed=False,
                rule="agent_approval_required",
                message="an agent submit requires the frozen execution plan_digest",
                params={"field": "plan_digest"},
                research=True,
            )

    if research:
        if not probe.has_plan_digest and not probe.has_proposal_ref:
            # A non-empty budget with no proposal/plan cannot be bound to an
            # approval.  Silently dropping it (the pre-M1-04 behaviour) is the
            # D09 violation this single decision point removes.
            return SubmitClassification(
                submit_class=SUBMIT_CLASS_RESEARCH,
                profile=policy_profile,
                approval_required=True,
                allowed=False,
                rule="approval_required",
                message=(
                    "a submit budget requires an approved proposal and the frozen "
                    "plan_digest; a non-empty budget is never silently ignored"
                ),
                params={"fields": ["proposal_ref", "plan_digest"]},
                research=True,
            )
        if not probe.has_plan_digest:
            return SubmitClassification(
                submit_class=SUBMIT_CLASS_RESEARCH,
                profile=policy_profile,
                approval_required=True,
                allowed=False,
                rule="plan_digest_required",
                message="a research launch requires plan_digest",
                params={"field": "plan_digest"},
                research=True,
            )
        if not probe.has_proposal_ref:
            return SubmitClassification(
                submit_class=SUBMIT_CLASS_RESEARCH,
                profile=policy_profile,
                approval_required=True,
                allowed=False,
                rule="approval_required",
                message=("a research launch requires a proposal reference with a server approval"),
                params={"field": "proposal_ref"},
                research=True,
            )
        if approval is None:
            if defer_approval:
                return SubmitClassification(
                    submit_class=SUBMIT_CLASS_RESEARCH,
                    profile=policy_profile,
                    approval_required=True,
                    allowed=True,
                    rule="approval_deferred",
                    message="the research approval is resolved before any side effect",
                    research=True,
                )
            return SubmitClassification(
                submit_class=SUBMIT_CLASS_RESEARCH,
                profile=policy_profile,
                approval_required=True,
                allowed=False,
                rule="approval_required",
                message="a research launch requires a server approval bound to this plan",
                research=True,
            )
        if (
            science_digest
            and approval_science_digest
            and (science_digest != approval_science_digest)
        ):
            return SubmitClassification(
                submit_class=SUBMIT_CLASS_RESEARCH,
                profile=policy_profile,
                approval_required=True,
                allowed=False,
                rule="approval_binding_mismatch",
                message="the approval binds a different scientific plan digest",
                params={"field": "science_digest"},
                research=True,
            )
        if approval_science_digest and not science_digest:
            return SubmitClassification(
                submit_class=SUBMIT_CLASS_RESEARCH,
                profile=policy_profile,
                approval_required=True,
                allowed=False,
                rule="science_binding_unverifiable",
                message=(
                    "the approval binds a scientific digest but this submit carries no "
                    "verifiable proposal body; the science binding cannot be checked"
                ),
                params={"field": "proposal_ref"},
                research=True,
            )
        return SubmitClassification(
            submit_class=SUBMIT_CLASS_RESEARCH,
            profile=policy_profile,
            approval_required=True,
            allowed=True,
            rule="approval_bound",
            message="approved research launch",
            research=True,
        )

    if policy_profile.strict:
        return SubmitClassification(
            submit_class=SUBMIT_CLASS_MANUAL_LEGACY,
            profile=policy_profile,
            approval_required=True,
            allowed=False,
            rule="submit_requires_research_fields",
            message="this deployment profile accepts research submits only",
            research=False,
        )

    submit_class = (
        SUBMIT_CLASS_LOCAL_SINGLE if policy_profile.local_operator else SUBMIT_CLASS_MANUAL_LEGACY
    )
    return SubmitClassification(
        submit_class=submit_class,
        profile=policy_profile,
        approval_required=False,
        allowed=True,
        rule="legacy_allowed",
        message=f"legacy {submit_class} submit (recorded, no approval)",
        research=False,
    )
