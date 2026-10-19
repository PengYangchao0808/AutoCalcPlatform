"""Identity lifecycle tests (B09-6 / plan todo 73).

Acceptance coverage: complete state flow (invite -> accept -> login/key ->
disable -> revoke -> recover); disable is immediately effective for new
commands/downloads and for EXISTING event streams; revoke leaves no valid
session/key; tier without auth capability refuses enablement; no placeholder
authentication and no universal default admin password; no auto-merge by e-mail
and no auto-escalation by role; explicit Key-only scope; OIDC never advertised
available; local multi-project regression with the todo 7 identity model.

Only ``acp_services`` (plus the Services-native runtime event-stream types) are
imported — the dependency-direction rule forbids ``acp_services`` importing
``acp``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from acp_services.contracts.access import (
    AccessAction,
    DecisionEffect,
    DeploymentTier,
    ExecutionMode,
    Principal,
    PrincipalKind,
    Role,
    resolve_scope,
    tier_roles,
)
from acp_services.contracts.events import EventScope
from acp_services.runtime.events import (
    RESET_PERMISSION_CHANGED,
    AuthorizedStreamRegistry,
    StreamAuthorizationError,
)
from acp_services.workspace.boundary import TierCapability, TierCapabilityError
from acp_services.workspace.identity_lifecycle import (
    MIN_PASSWORD_LENGTH,
    OIDC_SUPPORTED,
    AccountStateError,
    AccountStatus,
    BootstrapRefusedError,
    DuplicateLoginError,
    EnrollmentNotSupportedError,
    IdentityLifecycle,
    IdentityLifecycleError,
    IdentityLifecyclePolicy,
    IdentityMode,
    IdentityReason,
    InvalidPasswordError,
    InvitationError,
    close_principal_streams,
    identity_support_scope,
    validate_password,
)
from acp_services.workspace.keys import ApiKeyManager, KeyReason

_NOW = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)
_LAB_CAPS = (TierCapability.AUTHENTICATION, TierCapability.ISOLATION)
_CHEAP_PARAMS = {"kdf": "pbkdf2_hmac_sha256", "iterations": 1000, "dklen": 32}
_GOOD_PASSWORD = "correct-horse-battery-staple"


def _lab_policy(**overrides: object) -> IdentityLifecyclePolicy:
    data: dict[str, object] = {
        "deployment_tier": DeploymentTier.LAB,
        "capabilities": _LAB_CAPS,
    }
    data.update(overrides)
    return IdentityLifecyclePolicy(**data)  # type: ignore[arg-type]


@pytest.fixture()
def lifecycle(tmp_path: Path) -> IdentityLifecycle:
    return IdentityLifecycle(
        tmp_path / "identity.db",
        policy=_lab_policy(),
        password_params=_CHEAP_PARAMS,
        token_iterations=1000,
    )


@pytest.fixture()
def keyed_lifecycle(tmp_path: Path) -> IdentityLifecycle:
    return IdentityLifecycle(
        tmp_path / "identity.db",
        policy=_lab_policy(),
        key_manager=ApiKeyManager(tmp_path / "keys.db", iterations=1000),
        password_params=_CHEAP_PARAMS,
        token_iterations=1000,
    )


def _enroll(
    lifecycle: IdentityLifecycle,
    *,
    principal_id: str = "principal_alice",
    login_id: str = "alice",
    password: str = _GOOD_PASSWORD,
    roles: tuple[str, ...] | None = None,
    email: str | None = None,
    now: datetime | None = None,
):
    invitation = lifecycle.invite_account(
        principal_id=principal_id,
        login_id=login_id,
        roles=roles,
        email=email,
        now=now,
    )
    return lifecycle.accept_invitation(invitation.token, password=password, now=now)


class _FakeLog:
    """Minimal in-memory ``EventLogPort`` for the stream-termination test."""

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def append(self, event_type: str, *, idempotency_key: str | None = None, **data) -> None:
        self.records.append({"type": event_type, **data})

    def count(self) -> int:
        return len(self.records)

    def read_all(self) -> list[dict[str, object]]:
        return list(self.records)

    def read_last(self) -> dict[str, object] | None:
        return self.records[-1] if self.records else None


# ---------------------------------------------------------------------------
# support scope / tier capability gate / axes / OIDC
# ---------------------------------------------------------------------------


def test_support_scope_reports_key_only_and_account_modes() -> None:
    key_only = identity_support_scope(DeploymentTier.LOCAL_SINGLE)
    assert key_only.enabled is True
    assert key_only.mode == IdentityMode.KEY_ONLY
    assert key_only.key_only is True
    assert key_only.sessions_supported is False
    assert key_only.enrollment_supported is False
    assert key_only.bootstrap_supported is False

    account = identity_support_scope(DeploymentTier.LAB, _LAB_CAPS)
    assert account.enabled is True
    assert account.mode == IdentityMode.ACCOUNT_SESSION
    assert account.sessions_supported is True
    assert account.enrollment_supported is True
    assert account.invitation_supported is True
    assert account.bootstrap_supported is True


def test_tier_without_auth_capability_refuses_enablement(tmp_path: Path) -> None:
    scope = identity_support_scope(DeploymentTier.LAB)
    assert scope.enabled is False
    assert scope.error_code == "tier_capability_missing"
    assert set(scope.missing_capabilities) == {
        TierCapability.AUTHENTICATION,
        TierCapability.ISOLATION,
    }

    lifecycle = IdentityLifecycle(
        tmp_path / "identity.db",
        policy=IdentityLifecyclePolicy(deployment_tier=DeploymentTier.LAB, capabilities=()),
        password_params=_CHEAP_PARAMS,
    )
    assert lifecycle.verify_enablement().enabled is False
    with pytest.raises(TierCapabilityError):
        lifecycle.require_enabled()
    with pytest.raises(TierCapabilityError):
        lifecycle.invite_account(principal_id="p", login_id="l")
    with pytest.raises(TierCapabilityError):
        lifecycle.bootstrap_first_admin(principal_id="p", login_id="l", password=_GOOD_PASSWORD)

    assert identity_support_scope(DeploymentTier.HOSTED).enabled is False
    assert identity_support_scope(DeploymentTier.HOSTED, _LAB_CAPS).enabled is True


def test_oidc_is_never_claimed_available() -> None:
    assert OIDC_SUPPORTED is False
    for tier in (DeploymentTier.LOCAL_SINGLE, DeploymentTier.LAB, DeploymentTier.HOSTED):
        scope = identity_support_scope(tier, _LAB_CAPS)
        assert scope.oidc_supported is False
        assert any("OIDC" in line for line in scope.limitations)


def test_deployment_tier_and_execution_mode_are_separate_axes() -> None:
    key_only = identity_support_scope(
        DeploymentTier.LOCAL_SINGLE, execution_mode=ExecutionMode.LSF
    )
    assert key_only.execution_mode == ExecutionMode.LSF
    assert key_only.mode == IdentityMode.KEY_ONLY
    assert key_only.sessions_supported is False

    account = identity_support_scope(
        DeploymentTier.LAB, _LAB_CAPS, execution_mode=ExecutionMode.SLURM
    )
    assert account.execution_mode == ExecutionMode.SLURM
    assert account.mode == IdentityMode.ACCOUNT_SESSION
    assert account.sessions_supported is True


# ---------------------------------------------------------------------------
# complete state flow
# ---------------------------------------------------------------------------


def test_complete_state_flow_enroll_login_disable_revoke_recover(
    lifecycle: IdentityLifecycle,
) -> None:
    account = _enroll(lifecycle, now=_NOW)
    assert account.status == AccountStatus.ACTIVE
    assert account.has_password is True

    login = lifecycle.login(login_id="alice", password=_GOOD_PASSWORD, now=_NOW)
    assert login.allowed is True
    assert login.token is not None
    assert lifecycle.verify_session(login.token, now=_NOW).allowed is True

    disabled = lifecycle.disable_account(account.account_id, actor="admin", now=_NOW)
    assert disabled.status == AccountStatus.DISABLED
    assert lifecycle.verify_session(login.token, now=_NOW).allowed is False
    assert lifecycle.authenticate(login_id="alice", password=_GOOD_PASSWORD) is None

    revoked = lifecycle.revoke_account(account.account_id, actor="admin", now=_NOW)
    assert revoked.status == AccountStatus.REVOKED

    recovered = lifecycle.recover_account(account.account_id, actor="admin", now=_NOW)
    assert recovered.status == AccountStatus.ACTIVE
    # The pre-disable session is never revived; a fresh login works again.
    assert lifecycle.verify_session(login.token, now=_NOW).allowed is False
    fresh = lifecycle.login(login_id="alice", password=_GOOD_PASSWORD, now=_NOW)
    assert fresh.allowed is True
    assert lifecycle.verify_session(fresh.token, now=_NOW).allowed is True


def test_login_rejects_wrong_password_and_unknown_login(
    lifecycle: IdentityLifecycle,
) -> None:
    _enroll(lifecycle, now=_NOW)
    wrong = lifecycle.login(login_id="alice", password="wrong-password-here", now=_NOW)
    assert wrong.allowed is False
    assert wrong.reason == IdentityReason.INVALID_CREDENTIALS
    unknown = lifecycle.login(login_id="nobody", password=_GOOD_PASSWORD, now=_NOW)
    assert unknown.allowed is False
    assert unknown.reason == IdentityReason.INVALID_CREDENTIALS


def test_invitation_cannot_be_replayed_or_expire(lifecycle: IdentityLifecycle) -> None:
    invitation = lifecycle.invite_account(
        principal_id="principal_alice", login_id="alice", now=_NOW
    )
    invited = lifecycle.get_account_by_login("alice")
    assert invited is not None and invited.status == AccountStatus.INVITED
    lifecycle.accept_invitation(invitation.token, password=_GOOD_PASSWORD, now=_NOW)
    with pytest.raises(InvitationError):
        lifecycle.accept_invitation(invitation.token, password=_GOOD_PASSWORD, now=_NOW)

    expired = lifecycle.invite_account(principal_id="principal_bob", login_id="bob", now=_NOW)
    with pytest.raises(InvitationError):
        lifecycle.accept_invitation(
            expired.token, password=_GOOD_PASSWORD, now=_NOW + timedelta(days=30)
        )


def test_wrong_password_on_invitation_is_rejected(lifecycle: IdentityLifecycle) -> None:
    invitation = lifecycle.invite_account(principal_id="p", login_id="l", now=_NOW)
    with pytest.raises(InvalidPasswordError):
        lifecycle.accept_invitation(invitation.token, password="password", now=_NOW)


# ---------------------------------------------------------------------------
# disable -> new commands/downloads refused + existing event streams terminated
# ---------------------------------------------------------------------------


def test_disable_blocks_new_commands_and_downloads(tmp_path: Path) -> None:
    lifecycle = IdentityLifecycle(
        tmp_path / "identity.db",
        policy=_lab_policy(),
        password_params=_CHEAP_PARAMS,
        token_iterations=1000,
    )
    account = _enroll(lifecycle, now=_NOW)
    assert lifecycle.authorize_command(account.principal_id).allowed is True
    assert lifecycle.authorize_download(account.principal_id).allowed is True

    lifecycle.disable_account(account.account_id, actor="admin", now=_NOW)

    command = lifecycle.authorize_command(account.principal_id)
    assert command.allowed is False
    assert command.reason_code == IdentityReason.ACCOUNT_DISABLED
    assert command.http_status == 403
    download = lifecycle.authorize_download(account.principal_id)
    assert download.allowed is False
    assert download.reason_code == IdentityReason.ACCOUNT_DISABLED


def test_disable_terminates_existing_event_stream(tmp_path: Path) -> None:
    registry = AuthorizedStreamRegistry()
    lifecycle = IdentityLifecycle(
        tmp_path / "identity.db",
        policy=_lab_policy(),
        stream_registry=registry,
        password_params=_CHEAP_PARAMS,
        token_iterations=1000,
    )
    account = _enroll(lifecycle, now=_NOW)
    authorizer = lifecycle.stream_authorizer(allow_unknown=False)

    log = _FakeLog()
    stream = registry.open(
        principal_id=account.principal_id,
        scope=EventScope.TASK,
        service_epoch="epoch-1",
        log_for=lambda job_id: log,
        job_ids=("job-a",),
        authorizer=authorizer,
    )
    assert stream.closed is False
    assert registry.active_count == 1

    lifecycle.disable_account(account.account_id, actor="admin", now=_NOW)

    assert stream.closed is True
    assert stream.closed_reason == RESET_PERMISSION_CHANGED
    batch = stream.poll([])
    assert batch.reset is True
    assert batch.reset_reason == RESET_PERMISSION_CHANGED
    assert registry.active_count == 0

    with pytest.raises(StreamAuthorizationError):
        registry.open(
            principal_id=account.principal_id,
            scope=EventScope.TASK,
            service_epoch="epoch-1",
            log_for=lambda job_id: log,
            job_ids=("job-a",),
            authorizer=authorizer,
        )


def test_close_principal_streams_is_noop_without_registry() -> None:
    assert close_principal_streams(None, "principal-x") == ()


# ---------------------------------------------------------------------------
# revoke -> no valid session / key remains
# ---------------------------------------------------------------------------


def test_revoke_refuses_old_keys_and_sessions(keyed_lifecycle: IdentityLifecycle) -> None:
    account = _enroll(keyed_lifecycle, now=_NOW)
    login = keyed_lifecycle.login(login_id="alice", password=_GOOD_PASSWORD, now=_NOW)
    assert login.allowed is True
    generated = keyed_lifecycle.issue_key(
        account.account_id, scope="workspace", actions=("read",), now=_NOW
    )

    key_manager = keyed_lifecycle._key_manager  # noqa: SLF001 - assert the primitive
    assert key_manager is not None
    assert key_manager.verify(generated.key, required_action="read", now=_NOW).allowed is True

    keyed_lifecycle.revoke_account(account.account_id, actor="admin", now=_NOW)

    assert (
        key_manager.verify(generated.key, required_action="read", now=_NOW).reason
        == KeyReason.REVOKED
    )
    assert keyed_lifecycle.verify_session(login.token, now=_NOW).allowed is False
    with pytest.raises(AccountStateError):
        keyed_lifecycle.issue_key(account.account_id, scope="workspace", actions=("read",))


def test_disable_revokes_keys_immediately(keyed_lifecycle: IdentityLifecycle) -> None:
    account = _enroll(keyed_lifecycle, now=_NOW)
    generated = keyed_lifecycle.issue_key(
        account.account_id, scope="workspace", actions=("read",), now=_NOW
    )
    key_manager = keyed_lifecycle._key_manager  # noqa: SLF001
    assert key_manager is not None
    keyed_lifecycle.disable_account(account.account_id, actor="admin", now=_NOW)
    assert key_manager.verify(generated.key, required_action="read", now=_NOW).allowed is False


# ---------------------------------------------------------------------------
# first-admin bootstrap — no universal default password
# ---------------------------------------------------------------------------


def test_bootstrap_rejects_weak_and_short_passwords(lifecycle: IdentityLifecycle) -> None:
    with pytest.raises(InvalidPasswordError):
        lifecycle.bootstrap_first_admin(
            principal_id="principal_admin", login_id="admin", password="admin", now=_NOW
        )
    with pytest.raises(InvalidPasswordError):
        lifecycle.bootstrap_first_admin(
            principal_id="principal_admin", login_id="admin", password="short", now=_NOW
        )
    with pytest.raises(InvalidPasswordError):
        validate_password("password", login_id="admin")
    assert MIN_PASSWORD_LENGTH >= 12


def test_bootstrap_first_admin_and_no_replay(lifecycle: IdentityLifecycle) -> None:
    admin = lifecycle.bootstrap_first_admin(
        principal_id="principal_admin", login_id="admin", password=_GOOD_PASSWORD, now=_NOW
    )
    assert admin.status == AccountStatus.ACTIVE
    assert admin.roles == (Role.ADMIN,)
    assert admin.bootstrap is True
    assert lifecycle.login(login_id="admin", password=_GOOD_PASSWORD, now=_NOW).allowed is True

    with pytest.raises(BootstrapRefusedError):
        lifecycle.bootstrap_first_admin(
            principal_id="principal_admin2",
            login_id="admin2",
            password=_GOOD_PASSWORD,
            now=_NOW,
        )


# ---------------------------------------------------------------------------
# no auto-merge / no auto-escalation
# ---------------------------------------------------------------------------


def test_same_email_does_not_merge_accounts(lifecycle: IdentityLifecycle) -> None:
    first = _enroll(
        lifecycle,
        principal_id="principal_a",
        login_id="alice",
        email="shared@example.org",
        now=_NOW,
    )
    second = _enroll(
        lifecycle,
        principal_id="principal_b",
        login_id="bob",
        email="shared@example.org",
        now=_NOW,
    )
    assert first.account_id != second.account_id
    assert first.principal_id != second.principal_id
    assert len(lifecycle.list_accounts()) == 2
    resolved = lifecycle.get_account_by_principal("principal_a")
    assert resolved is not None and resolved.account_id == first.account_id


def test_duplicate_login_is_refused_never_merged(
    lifecycle: IdentityLifecycle,
) -> None:
    _enroll(lifecycle, principal_id="principal_a", login_id="alice", now=_NOW)
    with pytest.raises(DuplicateLoginError):
        lifecycle.invite_account(principal_id="principal_b", login_id="alice", now=_NOW)


def test_roles_are_explicit_and_tier_grants_none(lifecycle: IdentityLifecycle) -> None:
    admin = lifecycle.bootstrap_first_admin(
        principal_id="principal_admin", login_id="admin", password=_GOOD_PASSWORD, now=_NOW
    )
    admin_principal = lifecycle.principal_for_account(admin.account_id, tenant_id="t1")
    assert AccessAction.ADMIN in (resolve_scope(admin_principal, None).available_actions or ())

    member = _enroll(
        lifecycle, principal_id="principal_member", login_id="member", roles=(), now=_NOW
    )
    member_principal = lifecycle.principal_for_account(member.account_id, tenant_id="t1")
    member_result = resolve_scope(member_principal, None)
    assert member_result.effect == DecisionEffect.ALLOW
    assert AccessAction.ADMIN not in (member_result.available_actions or ())

    # A tier never contributes a role (same rule as contracts.access.tier_roles).
    assert tier_roles(DeploymentTier.LAB) == ()
    assert set(member.roles) == set()


def test_unknown_role_is_refused(lifecycle: IdentityLifecycle) -> None:
    with pytest.raises(IdentityLifecycleError):
        lifecycle.invite_account(
            principal_id="p", login_id="l", roles=("superuser",), now=_NOW
        )


# ---------------------------------------------------------------------------
# explicit Key-only scope + fail-closed entries
# ---------------------------------------------------------------------------


def test_key_only_tier_declares_limitations(tmp_path: Path) -> None:
    lifecycle = IdentityLifecycle(
        tmp_path / "identity.db", policy=IdentityLifecyclePolicy()
    )
    scope = lifecycle.support_scope()
    assert scope.mode == IdentityMode.KEY_ONLY
    assert scope.sessions_supported is False
    assert any("Key-only" in line for line in scope.limitations)

    login = lifecycle.login(login_id="whoever", password=_GOOD_PASSWORD, now=_NOW)
    assert login.allowed is False
    assert login.reason == IdentityReason.SESSIONS_NOT_SUPPORTED
    with pytest.raises(EnrollmentNotSupportedError):
        lifecycle.invite_account(principal_id="p", login_id="l")
    with pytest.raises(BootstrapRefusedError):
        lifecycle.bootstrap_first_admin(principal_id="p", login_id="l", password=_GOOD_PASSWORD)


def test_unknown_entry_fails_closed(lifecycle: IdentityLifecycle) -> None:
    decision = lifecycle.authorize_entry("principal-x", "bogus")
    assert decision.allowed is False
    assert decision.reason_code == IdentityReason.UNKNOWN_ENTRY
    assert decision.http_status == 403


def test_password_is_stored_digest_only(tmp_path: Path) -> None:
    lifecycle = IdentityLifecycle(
        tmp_path / "identity.db",
        policy=_lab_policy(),
        password_params=_CHEAP_PARAMS,
        token_iterations=1000,
    )
    account = _enroll(lifecycle, password=_GOOD_PASSWORD, now=_NOW)
    assert account.has_password is True
    assert _GOOD_PASSWORD.encode() not in (tmp_path / "identity.db").read_bytes()

    conn = sqlite3.connect(str(lifecycle.db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT password_digest, password_salt FROM identity_accounts WHERE account_id=?",
            (account.account_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert bytes(row["password_digest"]) != _GOOD_PASSWORD.encode()
    assert len(bytes(row["password_salt"])) == 16


def test_local_multi_project_regression_with_todo7(tmp_path: Path) -> None:
    lifecycle = IdentityLifecycle(tmp_path / "identity.db", policy=IdentityLifecyclePolicy())
    scope = lifecycle.support_scope()
    assert scope.enabled is True
    assert scope.sessions_supported is False

    principal = Principal(
        principal_id="principal_local",
        kind=PrincipalKind.USER,
        tenant_id="local-single",
        roles=(),
        deployment_tier=DeploymentTier.LOCAL_SINGLE,
    )
    known = {"proj_explicit": "local-single", "proj_legacy": None}
    for project in ("proj_explicit", "proj_legacy"):
        resolution = resolve_scope(principal, project, known_projects=known)
        assert resolution.effect == DecisionEffect.ALLOW

    assert lifecycle.authorize_command("principal_local").allowed is True
    assert lifecycle.authorize_download("principal_local").allowed is True

