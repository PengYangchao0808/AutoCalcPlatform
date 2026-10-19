"""B09-4 (todo 51) — admin authorization, audit trail, restricted broker.

Acceptance clauses from the plan (``acp-parallel-plan-2-services`` todo 51):

* a **normal API key** hitting a management entry is denied with ``403``;
* a **tier upgrade grants no admin role** (tier != role);
* management entries are **re-authorized per entry** (a filtered menu is not
  the gate; the whole catalogue is always returned, the gate runs per access);
* every management action records an **audit fact** with actor / object /
  before-and-after version / reason / timestamp;
* a **restricted broker** accepts only whitelisted install-time operations and
  refuses arbitrary ``sudo``/shell/argv arguments.

The module under test imports **only** ``acp_services`` (dependency-direction
rule: ``acp_services`` never imports ``acp``).

Run with: pytest tests/test_services_admin.py -q
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from acp_services.contracts.access import (
    AccessAction,
    ExecutionMode,
    Principal,
    PrincipalKind,
    Role,
    Tier,
    User,
)
from acp_services.workspace import admin as admin_mod
from acp_services.workspace.admin import (
    ADMIN_HTTP_FORBIDDEN,
    ADMIN_HTTP_OK,
    AdminAuditLog,
    AdminAuthorizer,
    AdminEntry,
    AdminReason,
    AdminService,
    BrokerOperation,
    BrokerReason,
    BrokerRequest,
    RestrictedBroker,
    admin_management_use_cases,
    tier_grants_management,
)
from acp_services.workspace.keys import ApiKeyManager, KeyDecision

MANAGEMENT_ENTRIES = admin_management_use_cases()

EXPECTED_USE_CASES = {
    "users",
    "members",
    "roles",
    "tier",
    "quota",
    "nodes",
    "queues",
    "active_tasks",
    "storage",
    "usage",
    "audit",
    "credential_revocation",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _principal(
    *,
    principal_id: str = "principal_admin",
    roles: tuple[str, ...] = (Role.ADMIN,),
    tier: str = Tier.LOCAL_SINGLE,
    kind: str = PrincipalKind.USER,
) -> Principal:
    return Principal(
        principal_id=principal_id,
        kind=kind,
        tenant_id="t1",
        roles=roles,
        deployment_tier=tier,
    )


def _admin_key(key_id: str = "k_admin") -> KeyDecision:
    return KeyDecision(
        allowed=True,
        reason="granted",
        key_id=key_id,
        scope="management",
        actions=(AccessAction.READ, AccessAction.WRITE, AccessAction.ADMIN),
        decided_at="2026-10-19T00:00:00+00:00",
    )


def _normal_key(key_id: str = "k_reader") -> KeyDecision:
    return KeyDecision(
        allowed=True,
        reason="granted",
        key_id=key_id,
        scope="project",
        actions=(AccessAction.READ, AccessAction.WRITE),
        decided_at="2026-10-19T00:00:00+00:00",
    )


def _audit(tmp_path: Path) -> AdminAuditLog:
    return AdminAuditLog(tmp_path / "admin_audit.db")


# ---------------------------------------------------------------------------
# Catalogue — complete, never filtered by caller
# ---------------------------------------------------------------------------


def test_catalogue_lists_all_management_use_cases() -> None:
    assert set(MANAGEMENT_ENTRIES) == EXPECTED_USE_CASES
    assert len(MANAGEMENT_ENTRIES) == 12
    assert AdminEntry.MANAGEMENT == frozenset(EXPECTED_USE_CASES)
    assert AdminEntry.SCIENTIFIC_CONTENT in AdminEntry.ALL


def test_catalogue_is_not_filtered_by_caller(tmp_path: Path) -> None:
    # A caller with no management authority still receives the whole catalogue:
    # hiding a menu item is not access control.
    service = AdminService(_audit(tmp_path))
    assert service.management_catalogue() == MANAGEMENT_ENTRIES
    assert admin_management_use_cases() == MANAGEMENT_ENTRIES


# ---------------------------------------------------------------------------
# Authorization matrix
# ---------------------------------------------------------------------------


def test_normal_key_is_denied_every_management_entry_with_403() -> None:
    authorizer = AdminAuthorizer()
    principal = _principal()
    for entry in MANAGEMENT_ENTRIES:
        decision = authorizer.authorize(entry, principal=principal, key_decision=_normal_key())
        assert decision.allowed is False, entry
        assert decision.http_status == ADMIN_HTTP_FORBIDDEN, entry
        assert decision.reason_code == AdminReason.KEY_LACKS_ADMIN, entry
        assert decision.to_dict()["http_status"] == 403


@pytest.mark.parametrize("tier", sorted(Tier.ALL))
def test_tier_upgrade_never_grants_management(tier: str) -> None:
    # roles=() — a tier alone (local-single/lab/hosted) confers no authority.
    principal = _principal(roles=(), tier=tier)
    decision = AdminAuthorizer().authorize(
        AdminEntry.USERS, principal=principal, key_decision=_admin_key()
    )
    assert decision.allowed is False
    assert decision.reason_code == AdminReason.ROLE_REQUIRED
    assert tier_grants_management(tier) is False


def test_admin_role_with_admin_key_allows_every_management_entry() -> None:
    authorizer = AdminAuthorizer()
    principal = _principal()
    decisions = authorizer.authorize_all(principal=principal, key_decision=_admin_key())
    assert len(decisions) == len(MANAGEMENT_ENTRIES)
    for decision in decisions:
        assert decision.allowed is True, decision.entry
        assert decision.http_status == ADMIN_HTTP_OK
        assert decision.reason_code == AdminReason.GRANTED


def test_role_required_but_key_present() -> None:
    decision = AdminAuthorizer().authorize(
        AdminEntry.QUOTA,
        principal=_principal(roles=(Role.MEMBER,)),
        key_decision=_admin_key(),
    )
    assert decision.allowed is False
    assert decision.reason_code == AdminReason.ROLE_REQUIRED


def test_operator_role_is_management_but_key_still_required() -> None:
    authorizer = AdminAuthorizer()
    operator = _principal(roles=(Role.OPERATOR,))
    assert authorizer.authorize(
        AdminEntry.NODES, principal=operator, key_decision=_admin_key()
    ).allowed
    assert (
        authorizer.authorize(
            AdminEntry.NODES, principal=operator, key_decision=_normal_key()
        ).reason_code
        == AdminReason.KEY_LACKS_ADMIN
    )


def test_disabled_user_is_denied() -> None:
    decision = AdminAuthorizer().authorize(
        AdminEntry.USERS,
        principal=_principal(),
        key_decision=_admin_key(),
        user=User(user_id="principal_admin", roles=(Role.ADMIN,), disabled=True),
    )
    assert decision.allowed is False
    assert decision.reason_code == AdminReason.ACCOUNT_DISABLED


def test_anonymous_principal_is_denied() -> None:
    decision = AdminAuthorizer().authorize(
        AdminEntry.AUDIT,
        principal=_principal(kind=PrincipalKind.ANONYMOUS),
        key_decision=_admin_key(),
    )
    assert decision.allowed is False
    assert decision.reason_code == AdminReason.NO_GRANT


def test_unknown_entry_is_denied_fail_closed() -> None:
    decision = AdminAuthorizer().authorize(
        "shell", principal=_principal(), key_decision=_admin_key()
    )
    assert decision.allowed is False
    assert decision.reason_code == AdminReason.UNKNOWN_ENTRY
    assert decision.http_status == ADMIN_HTTP_FORBIDDEN


def test_per_entry_reauthorization_reflects_current_state(tmp_path: Path) -> None:
    """Same service, same entry: authority is re-derived on every call."""
    service = AdminService(_audit(tmp_path))
    principal = _principal()
    enabled = User(user_id="principal_admin", roles=(Role.ADMIN,), disabled=False)
    disabled = User(user_id="principal_admin", roles=(Role.ADMIN,), disabled=True)

    first = service.enter(
        AdminEntry.USERS, principal=principal, key_decision=_admin_key(), user=enabled
    )
    assert first.allowed is True
    second = service.enter(
        AdminEntry.USERS, principal=principal, key_decision=_admin_key(), user=disabled
    )
    assert second.allowed is False
    assert second.reason_code == AdminReason.ACCOUNT_DISABLED


def test_real_normal_api_key_is_denied(tmp_path: Path) -> None:
    """Bind to the real key primitive: a read/write key has no admin power."""
    manager = ApiKeyManager(tmp_path / "keys.db")
    normal = manager.generate(scope="project", actions=["read", "write"])
    verified = manager.verify(normal.key, required_action=AccessAction.READ)
    assert verified.allowed is True

    decision = AdminAuthorizer().authorize(
        AdminEntry.CREDENTIAL_REVOCATION, principal=_principal(), key_decision=verified
    )
    assert decision.allowed is False
    assert decision.reason_code == AdminReason.KEY_LACKS_ADMIN


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


def test_service_requires_audit_sink() -> None:
    with pytest.raises(ValueError):
        AdminService(None)  # type: ignore[arg-type]


def test_management_read_of_scientific_content_is_audited(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    service = AdminService(audit)
    decision = service.read_scientific_content(
        principal=_principal(),
        key_decision=_admin_key(),
        object_type="structure",
        object_id="mol-42",
        version="rev-7",
        reason="support investigation",
    )
    assert decision.allowed is True
    events = audit.list_events(object_type="structure", object_id="mol-42")
    assert len(events) == 1
    record = events[0]
    assert record.actor == "principal_admin"
    assert record.action == "read"
    assert record.before_version == "rev-7"
    assert record.after_version == "rev-7"
    assert record.reason == "support investigation"
    assert record.created_at  # timestamp present


def test_audit_record_fields_complete(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    record = audit.record(
        actor="principal_admin",
        credential="k_admin",
        object_type="user",
        object_id="u-9",
        action="set_role",
        before_version="member",
        after_version="operator",
        reason="promotion approved",
    )
    data = record.to_dict()
    for field in (
        "actor",
        "object_type",
        "object_id",
        "action",
        "before_version",
        "after_version",
        "reason",
        "created_at",
    ):
        assert data[field], field
    assert audit.count() == 1
    assert audit.list_events()[0].to_dict() == data


@pytest.mark.parametrize("missing", ["actor", "object_type", "object_id", "action", "reason"])
def test_audit_record_rejects_incomplete_fields(tmp_path: Path, missing: str) -> None:
    audit = _audit(tmp_path)
    kwargs: dict[str, Any] = {
        "actor": "principal_admin",
        "object_type": "user",
        "object_id": "u-1",
        "action": "set_role",
        "reason": "why",
    }
    kwargs[missing] = ""
    with pytest.raises(ValueError):
        audit.record(**kwargs)


def test_mutation_audit_captures_before_and_after_versions(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    service = AdminService(audit)
    decision = service.mutate(
        AdminEntry.ROLES,
        principal=_principal(),
        key_decision=_admin_key(),
        object_type="member",
        object_id="m-3",
        action="set_role",
        before_version="member",
        after_version="operator",
        reason="review board",
    )
    assert decision.allowed is True
    record = audit.list_events(object_type="member", object_id="m-3")[0]
    assert (record.before_version, record.after_version) == ("member", "operator")
    assert record.reason == "review board"


def test_denied_read_is_still_audited(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    service = AdminService(audit)
    decision = service.read_scientific_content(
        principal=_principal(),
        key_decision=_normal_key(),
        object_type="structure",
        object_id="mol-1",
        version="rev-1",
        reason="snoop attempt",
    )
    assert decision.allowed is False
    record = audit.list_events()[0]
    assert record.reason == AdminReason.KEY_LACKS_ADMIN


def test_credential_revocation_is_audited(tmp_path: Path) -> None:
    audit = _audit(tmp_path)
    service = AdminService(audit)
    decision = service.revoke_credential(
        principal=_principal(),
        key_decision=_admin_key(),
        target_key_id="k_stolen",
        before_version="active",
        after_version="revoked",
        reason="lost laptop",
    )
    assert decision.allowed is True
    record = audit.list_events(object_type="api_key", object_id="k_stolen")[0]
    assert record.action == "revoke"
    assert (record.before_version, record.after_version) == ("active", "revoked")


# ---------------------------------------------------------------------------
# Restricted broker
# ---------------------------------------------------------------------------


def test_broker_whitelist_is_bounded() -> None:
    broker = RestrictedBroker()
    assert set(broker.whitelist()) == {
        BrokerOperation.CREATE_SYSTEM_USER,
        BrokerOperation.SET_QUOTA,
        BrokerOperation.SETUP_ISOLATION,
    }


@pytest.mark.parametrize(
    ("operation", "parameters"),
    [
        (BrokerOperation.CREATE_SYSTEM_USER, {"username": "acpworker"}),
        (
            BrokerOperation.SET_QUOTA,
            {"subject": "proj_a", "dimension": "cores", "value": "64"},
        ),
        (
            BrokerOperation.SETUP_ISOLATION,
            {"tier": Tier.LAB, "execution_mode": ExecutionMode.PODMAN},
        ),
    ],
)
def test_broker_allows_whitelisted_operations(operation: str, parameters: dict[str, str]) -> None:
    decision = RestrictedBroker().execute(BrokerRequest(operation, parameters))
    assert decision.allowed is True
    assert decision.reason_code == BrokerReason.GRANTED


def test_broker_refuses_unknown_operation() -> None:
    decision = RestrictedBroker().execute(BrokerRequest("run_shell", {"cmd": "id"}))
    assert decision.allowed is False
    assert decision.reason_code == BrokerReason.OPERATION_NOT_ALLOWED


@pytest.mark.parametrize("name", ["sudo", "shell", "command", "args", "script", "exec"])
def test_broker_refuses_arbitrary_root_and_shell_parameters(name: str) -> None:
    decision = RestrictedBroker().execute(
        BrokerRequest(BrokerOperation.CREATE_SYSTEM_USER, {"username": "acpworker", name: "x"})
    )
    assert decision.allowed is False
    assert decision.reason_code == BrokerReason.PARAMETER_NOT_ALLOWED


@pytest.mark.parametrize("username", ["-rf", "root;rm -rf /", "a$(id)", "b`id`", "a|b", "a\nb"])
def test_broker_refuses_argv_and_shell_injection_values(username: str) -> None:
    decision = RestrictedBroker().execute(
        BrokerRequest(BrokerOperation.CREATE_SYSTEM_USER, {"username": username})
    )
    assert decision.allowed is False
    assert decision.reason_code == BrokerReason.INVALID_VALUE


def test_broker_refuses_missing_parameter() -> None:
    decision = RestrictedBroker().execute(
        BrokerRequest(BrokerOperation.SET_QUOTA, {"subject": "proj_a", "dimension": "cores"})
    )
    assert decision.allowed is False
    assert decision.reason_code == BrokerReason.MISSING_PARAMETER


@pytest.mark.parametrize(
    ("operation", "parameters"),
    [
        (BrokerOperation.SET_QUOTA, {"subject": "p_a", "dimension": "warp", "value": "1"}),
        (BrokerOperation.SET_QUOTA, {"subject": "p_a", "dimension": "cores", "value": "-1"}),
        (BrokerOperation.SET_QUOTA, {"subject": "p_a", "dimension": "cores", "value": "lots"}),
        (BrokerOperation.SETUP_ISOLATION, {"tier": "galaxy", "execution_mode": "podman"}),
        (BrokerOperation.SETUP_ISOLATION, {"tier": "lab", "execution_mode": "telepathy"}),
        (BrokerOperation.CREATE_SYSTEM_USER, {"username": "Bad Name"}),
    ],
)
def test_broker_refuses_invalid_values(operation: str, parameters: dict[str, str]) -> None:
    decision = RestrictedBroker().execute(BrokerRequest(operation, parameters))
    assert decision.allowed is False
    assert decision.reason_code == BrokerReason.INVALID_VALUE


def test_broker_decision_is_structured_data_not_a_command() -> None:
    decision = RestrictedBroker().execute(
        BrokerRequest(
            BrokerOperation.SET_QUOTA,
            {"subject": "p_a", "dimension": "cores", "value": "4"},
        )
    )
    payload = decision.to_dict()
    assert isinstance(payload["parameters"], dict)
    assert "command" not in payload
    assert all(isinstance(v, str) for v in payload["parameters"].values())


def test_broker_has_no_root_or_shell_channel_statically() -> None:
    source = Path(admin_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not ({"subprocess", "pty", "os"} & imported)
    # No process-spawning call may appear in the code (strings/docstrings excluded).
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"system", "popen", "spawn", "execv", "execvp"}, (
                f"admin module exposes a process channel: {node.func.attr}"
            )


def test_admin_module_does_not_import_legacy_acp() -> None:
    source = Path(admin_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("acp") or alias.name.startswith("acp_services"), (
                    alias.name
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module.startswith("acp_services") or not module.startswith("acp")
