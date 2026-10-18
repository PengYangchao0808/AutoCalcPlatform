"""Identity model layering tests (B01-2 / plan todo 7).

Exercises the additive identity layer in
``acp_services.contracts.access``: the user / tenant(project) / role / tier /
grant / billing split, the permission matrix, deterministic legacy adoption,
and the rule that client-supplied ``requested_project`` is only ever turned
into authority through :func:`resolve_scope`.

The module imports **only** ``acp_services`` (the dependency-direction rule:
``acp_services`` never imports ``acp``).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from acp_services.contracts import access

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVICES_ROOT = _REPO_ROOT / "src" / "acp_services"


def _principal(
    tenant_id: str = "t1",
    *,
    principal_id: str = "principal_t1",
    kind: str = access.PrincipalKind.USER,
    roles: tuple[str, ...] = (),
    tier: str = access.Tier.LOCAL_SINGLE,
) -> access.Principal:
    return access.Principal(
        principal_id=principal_id,
        kind=kind,
        tenant_id=tenant_id,
        roles=roles,
        deployment_tier=tier,
    )


def _grant(
    principal_id: str = "principal_t1",
    *,
    resource_id: str = "proj1",
    scope_id: str = "s1",
    actions: tuple[str, ...] = ("read",),
    grant_id: str = "g1",
) -> access.ResourceGrantSpec:
    return access.ResourceGrantSpec(
        grant_id=grant_id,
        principal_id=principal_id,
        scope_id=scope_id,
        resource_id=resource_id,
        actions=actions,
        granted_at="2026-10-18T00:00:00Z",
        cores=4,
    )


# ---------------------------------------------------------------------------
# happy path + forged cross-project (the QA double branch)
# ---------------------------------------------------------------------------


def test_allow_local_many_projects_explicit_and_legacy() -> None:
    principal = _principal(tier=access.Tier.LOCAL_SINGLE)
    known = {"proj_explicit": "t1", "proj_legacy": None}

    explicit = access.resolve_scope(principal, "proj_explicit", known_projects=known)
    assert explicit.effect == access.DecisionEffect.ALLOW
    assert explicit.reason_code == access.ReasonCode.GRANTED
    assert explicit.project_scope is not None
    assert explicit.project_scope.source == access.SOURCE_EXPLICIT
    assert explicit.adopted_legacy is False
    assert explicit.available_actions == ("control", "read", "write")
    assert access.AccessAction.ADMIN not in explicit.available_actions
    assert explicit.tenant_id == "t1"

    legacy = access.resolve_scope(principal, "proj_legacy", known_projects=known)
    assert legacy.effect == access.DecisionEffect.ALLOW
    assert legacy.adopted_legacy is True
    assert legacy.project_scope is not None
    assert legacy.project_scope.source == access.SOURCE_LEGACY_ADOPTED


def test_forged_cross_project_is_denied() -> None:
    """QA failure branch: a request carrying another project's id is denied."""
    principal_t1 = _principal("t1", principal_id="principal_t1")
    principal_t2 = _principal("t2", principal_id="principal_t2")
    known = {"proj_a": "t1", "proj_b": "t2"}

    forged = access.resolve_scope(principal_t2, "proj_a", known_projects=known)
    assert forged.effect == access.DecisionEffect.DENY
    assert forged.reason_code == access.ReasonCode.SCOPE_MISMATCH
    assert forged.available_actions is None
    assert forged.allowed is False

    reverse = access.resolve_scope(principal_t1, "proj_b", known_projects=known)
    assert reverse.effect == access.DecisionEffect.DENY
    assert reverse.reason_code == access.ReasonCode.SCOPE_MISMATCH


def test_unknown_project_is_explicitly_denied() -> None:
    principal = _principal("t1")
    result = access.resolve_scope(principal, "ghost", known_projects={"proj1": "t1"})
    assert result.effect == access.DecisionEffect.DENY
    assert result.reason_code == access.ReasonCode.NO_GRANT


def test_tenant_wide_scope_allows_many_projects() -> None:
    principal = _principal("t1")
    result = access.resolve_scope(principal, None, known_projects={"proj1": "t1"})
    assert result.effect == access.DecisionEffect.ALLOW
    assert result.project_scope is None
    assert result.scope_id == "tenant:t1"


def test_disabled_user_is_denied() -> None:
    principal = _principal("t1")
    user = access.User(user_id="u1", disabled=True)
    result = access.resolve_scope(principal, "proj1", known_projects={"proj1": "t1"}, user=user)
    assert result.effect == access.DecisionEffect.DENY
    assert result.reason_code == access.ReasonCode.REVOKED


def test_anonymous_and_unknown_kind_are_denied() -> None:
    anon = access.resolve_scope(
        _principal("t1", kind=access.PrincipalKind.ANONYMOUS),
        "proj1",
        known_projects={"proj1": "t1"},
    )
    assert anon.effect == access.DecisionEffect.DENY
    assert anon.reason_code == access.ReasonCode.NO_GRANT

    weird = access.resolve_scope(
        _principal("t1", kind="robot"),
        "proj1",
        known_projects={"proj1": "t1"},
    )
    assert weird.effect == access.DecisionEffect.DENY


def test_tenant_scope_mismatch_is_denied() -> None:
    principal = _principal("t1")
    result = access.resolve_scope(
        principal,
        "proj1",
        known_projects={"proj1": "t1"},
        tenant_scope=access.TenantScope(tenant_id="t2"),
    )
    assert result.effect == access.DecisionEffect.DENY
    assert result.reason_code == access.ReasonCode.SCOPE_MISMATCH


def test_non_member_user_is_denied() -> None:
    principal = _principal("t1")
    tenant = access.TenantScope(tenant_id="t1", member_user_ids=("u2",))
    result = access.resolve_scope(
        principal,
        "proj1",
        known_projects={"proj1": "t1"},
        tenant_scope=tenant,
        user=access.User(user_id="u1"),
    )
    assert result.effect == access.DecisionEffect.DENY
    assert result.reason_code == access.ReasonCode.SCOPE_MISMATCH


# ---------------------------------------------------------------------------
# tier / role separation (tier NEVER grants a role nor admin)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tier", "expected"),
    [
        (access.Tier.LOCAL_SINGLE, ("read", "write", "control")),
        (access.Tier.LAB, ("read", "write")),
        (access.Tier.HOSTED, ("read",)),
        ("unrecognised-tier", ("read",)),
    ],
)
def test_tier_baseline_downgrade_minimal_set(tier: str, expected: tuple[str, ...]) -> None:
    assert access.tier_baseline_actions(tier) == expected
    assert access.AccessAction.ADMIN not in access.tier_baseline_actions(tier)


def test_tier_never_grants_a_role_or_admin() -> None:
    for tier in (access.Tier.LOCAL_SINGLE, access.Tier.LAB, access.Tier.HOSTED):
        assert access.tier_roles(tier) == ()
        assert access.tier_roles(tier) != access.Role.ALL

    for tier in (access.Tier.LOCAL_SINGLE, access.Tier.LAB, access.Tier.HOSTED):
        result = access.resolve_scope(_principal("t1", tier=tier), None)
        assert result.effect == access.DecisionEffect.ALLOW
        assert access.AccessAction.ADMIN not in (result.available_actions or ())

    # A tier name is not a role; the vocabularies are disjoint.
    assert access.Tier.ALL.isdisjoint(access.Role.ALL)


def test_explicit_admin_role_adds_admin_action() -> None:
    member = access.resolve_scope(_principal("t1"), None)
    assert access.AccessAction.ADMIN not in (member.available_actions or ())

    admin = access.resolve_scope(_principal("t1", roles=(access.Role.ADMIN,)), None)
    assert access.AccessAction.ADMIN in (admin.available_actions or ())

    operator = access.resolve_scope(_principal("t1", roles=(access.Role.OPERATOR,)), None)
    assert access.AccessAction.ADMIN in (operator.available_actions or ())


def test_management_user_flag_honours_disabled() -> None:
    assert access.User(user_id="u1", roles=(access.Role.ADMIN,)).is_management is True
    assert (
        access.User(user_id="u1", roles=(access.Role.ADMIN,), disabled=True).is_management is False
    )
    assert access.User(user_id="u1", roles=(access.Role.MEMBER,)).is_management is False


# ---------------------------------------------------------------------------
# deterministic legacy adoption
# ---------------------------------------------------------------------------


def test_legacy_adoption_is_deterministic_for_local_single() -> None:
    principal = _principal("local-single", tier=access.Tier.LOCAL_SINGLE)
    known = ["old_a", "old_b"]  # plain iterable: all legacy (tenant-less)

    first = access.resolve_scope(principal, "old_a", known_projects=known)
    second = access.resolve_scope(principal, "old_a", known_projects=known)
    assert first.effect == access.DecisionEffect.ALLOW
    assert first.adopted_legacy is True
    assert first.project_scope is not None
    assert first.project_scope.source == access.SOURCE_LEGACY_ADOPTED
    assert first.decision.decision_id == second.decision.decision_id
    assert first.to_dict()["project_scope"] == second.to_dict()["project_scope"]


def test_legacy_projects_not_adopted_outside_local_single() -> None:
    for tier in (access.Tier.LAB, access.Tier.HOSTED):
        result = access.resolve_scope(
            _principal("t1", tier=tier),
            "old_a",
            known_projects=["old_a"],
        )
        assert result.effect == access.DecisionEffect.DENY
        assert result.reason_code == access.ReasonCode.SCOPE_MISMATCH


# ---------------------------------------------------------------------------
# deployment_tier vs execution_mode separation
# ---------------------------------------------------------------------------


def test_remote_execution_does_not_imply_multi_user_hosting() -> None:
    principal = _principal("local-single", tier=access.Tier.LOCAL_SINGLE)
    result = access.resolve_scope(
        principal,
        "proj1",
        known_projects={"proj1": "local-single", "legacy": None},
        execution_mode=access.ExecutionMode.LSF,
    )
    assert result.effect == access.DecisionEffect.ALLOW
    assert result.execution_mode == access.ExecutionMode.LSF
    assert result.deployment_tier == access.Tier.LOCAL_SINGLE
    # Remote execution widens neither the tier baseline nor the roles.
    assert set(result.available_actions or ()) == set(
        access.tier_baseline_actions(access.Tier.LOCAL_SINGLE)
    )
    assert access.AccessAction.ADMIN not in (result.available_actions or ())

    legacy = access.resolve_scope(
        principal,
        "legacy",
        known_projects={"proj1": "local-single", "legacy": None},
        execution_mode=access.ExecutionMode.SLURM,
    )
    assert legacy.effect == access.DecisionEffect.ALLOW
    assert legacy.adopted_legacy is True


# ---------------------------------------------------------------------------
# ResourceGrant variant: placeholders + action containment
# ---------------------------------------------------------------------------


def test_resource_grant_spec_is_a_resource_grant_with_placeholders() -> None:
    spec = _grant(actions=("read", "write"))
    assert isinstance(spec, access.ResourceGrant)
    assert spec.cores == 4
    assert spec.memory_mb is None
    assert spec.concurrency is None
    assert spec.duration_s is None
    assert spec.storage_bytes is None
    assert spec.queue is None

    spec_fields = {f.name for f in dataclasses.fields(access.ResourceGrantSpec)}
    assert {
        "cores",
        "memory_mb",
        "concurrency",
        "duration_s",
        "storage_bytes",
        "queue",
    } <= spec_fields


def test_grants_extend_actions_but_never_widen_beyond_explicit_grants() -> None:
    principal = _principal("t1")
    granted = access.resolve_scope(
        principal,
        "proj1",
        known_projects={"proj1": "t1"},
        grants=[_grant(actions=("read", "write", "admin"))],
    )
    assert granted.effect == access.DecisionEffect.ALLOW
    assert access.AccessAction.ADMIN in (granted.available_actions or ())

    # A grant for another principal or another project is not consulted.
    other_principal = access.resolve_scope(
        principal,
        "proj2",
        known_projects={"proj2": "t1"},
        grants=[_grant(resource_id="proj1", actions=("admin",))],
    )
    assert access.AccessAction.ADMIN not in (other_principal.available_actions or ())


# ---------------------------------------------------------------------------
# validate_scope relationship checks
# ---------------------------------------------------------------------------


def _consistent_bundle() -> dict:
    return {
        "user": access.User(user_id="u1", roles=(access.Role.ADMIN,)),
        "tenant": access.TenantScope(
            tenant_id="t1",
            member_user_ids=("u1", "u2"),
            project_ids=("proj1",),
        ),
        "project": access.ProjectScope(project_id="proj1", tenant_id="t1"),
        "grants": [_grant(resource_id="proj1")],
        "billing_subject": access.BillingSubject(
            billing_subject_id="b1",
            owner_tenant_id="t1",
        ),
    }


def test_validate_scope_accepts_consistent_bundle() -> None:
    access.validate_scope(**_consistent_bundle())  # must not raise


@pytest.mark.parametrize(
    ("override", "expected_fragment"),
    [
        ({"user": access.User(user_id="u1", roles=("superuser",))}, "unknown role"),
        ({"user": access.User(user_id="outsider")}, "not a member"),
        ({"project": access.ProjectScope(project_id="proj1", tenant_id="t2")}, "belongs to tenant"),
        ({"project": access.ProjectScope(project_id="proj9", tenant_id="t1")}, "not declared"),
        ({"grants": [_grant(resource_id="proj9")]}, "outside tenant"),
        (
            {
                "billing_subject": access.BillingSubject(
                    billing_subject_id="b1", owner_tenant_id="t2"
                )
            },
            "billing subject",
        ),
    ],
)
def test_validate_scope_rejects_contradictions(override: dict, expected_fragment: str) -> None:
    bundle = _consistent_bundle()
    bundle.update(override)
    with pytest.raises(access.ScopeContradictionError) as excinfo:
        access.validate_scope(**bundle)
    assert expected_fragment in str(excinfo.value)
    assert isinstance(excinfo.value, access.IdentityError)


def test_validate_scope_rejects_empty_tenant_id() -> None:
    with pytest.raises(access.ScopeContradictionError):
        access.validate_scope(tenant=access.TenantScope(tenant_id=""))


# ---------------------------------------------------------------------------
# billing subject is a separate fact with no authorisation effect
# ---------------------------------------------------------------------------


def test_billing_subject_has_no_authorisation_fields() -> None:
    fields = {f.name for f in dataclasses.fields(access.BillingSubject)}
    assert fields.isdisjoint({"roles", "actions", "effect", "tier", "deployment_tier", "scope_id"})
    resolve_params = inspect.signature(access.resolve_scope).parameters
    assert all("billing" not in name for name in resolve_params)
    # validate_scope takes billing only to check reference consistency.
    assert "billing_subject" in inspect.signature(access.validate_scope).parameters


def test_billing_subject_does_not_change_authorisation() -> None:
    principal = _principal("t1")
    fixed_now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    baseline = access.resolve_scope(
        principal, "proj1", known_projects={"proj1": "t1"}, now=fixed_now
    )
    with_billing = access.resolve_scope(
        principal,
        "proj1",
        known_projects={"proj1": "t1"},
        grants=[],
        now=fixed_now,
    )
    assert baseline.decision == with_billing.decision
    assert baseline.available_actions == with_billing.available_actions

    # Reference-consistent billing passes validation without granting anything.
    tenant = access.TenantScope(tenant_id="t1", project_ids=("proj1",))
    access.validate_scope(
        tenant=tenant,
        billing_subject=access.BillingSubject(billing_subject_id="b1", owner_tenant_id="t1"),
    )
    with pytest.raises(access.ScopeContradictionError):
        access.validate_scope(
            tenant=tenant,
            billing_subject=access.BillingSubject(
                billing_subject_id="b2", owner_tenant_id="someone-else"
            ),
        )


# ---------------------------------------------------------------------------
# shape + single trust point
# ---------------------------------------------------------------------------


def test_scope_resolution_is_access_decision_shaped() -> None:
    principal = _principal("t1")
    allowed = access.resolve_scope(principal, "proj1", known_projects={"proj1": "t1"})
    assert isinstance(allowed.decision, access.AccessDecision)
    assert allowed.effect == allowed.decision.effect
    assert allowed.reason_code == allowed.decision.reason_code
    assert allowed.available_actions == allowed.decision.available_actions
    assert allowed.principal_id == principal.principal_id
    payload = allowed.to_dict()
    assert payload["decision"]["effect"] == access.DecisionEffect.ALLOW
    assert payload["project_scope"]["project_id"] == "proj1"
    assert payload["tenant_id"] == "t1"

    denied = access.resolve_scope(principal, "ghost", known_projects={"proj1": "t1"})
    assert denied.available_actions is None
    assert denied.project_scope is None


def test_tenant_always_derived_from_principal() -> None:
    principal = _principal("authoritative-tenant")
    result = access.resolve_scope(
        principal,
        "proj1",
        known_projects={"proj1": "authoritative-tenant"},
    )
    assert result.tenant_id == "authoritative-tenant"
    assert result.project_scope is not None
    assert result.project_scope.tenant_id == "authoritative-tenant"


def test_requested_project_is_only_consumed_by_resolve_scope() -> None:
    """Guard: no other callable in Services may take a ``requested_project``."""
    consumers: set[str] = set()
    field_owners: set[str] = set()
    for path in _SERVICES_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names = {arg.arg for arg in node.args.args + node.args.kwonlyargs}
                if "requested_project" in names:
                    consumers.add(node.name)
            elif isinstance(node, ast.ClassDef):
                if any(
                    isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and stmt.target.id == "requested_project"
                    for stmt in node.body
                ):
                    field_owners.add(node.name)
    assert consumers == {"resolve_scope"}, consumers
    assert field_owners <= {"ScopeResolution"}, field_owners
