"""B15 (plan todo 17) — multi-user authorization and revocation without a second authority.

Coverage:

* a real HTTP credential -> principal path (API keys minted through
  ``ApiKeyManager``/``IdentityLifecycle``, resolved by the Controller);
* a forged workspace/project/role hint never changes the resolved principal and
  never widens the verified project;
* cross-project read/control/prepare is denied with the same 404 shape as an
  unknown task (no existence disclosure);
* a revoked key and a revoked account stop reads **and** an already-open event
  subscription at the next poll;
* the stdio proxy binds to the server-resolved principal and applies the
  credential action ceiling (tool ``_meta`` cannot elevate);
* the default deployment stays ``local-single`` and reports remote identity
  providers as ``unsupported``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp_services.contracts.access import Principal
from acp_services.entrypoints import mcp
from acp_services.store.layout import runtime_file
from acp_services.workspace.identity_lifecycle import (
    AccountStatus,
    IdentityLifecycle,
    IdentityLifecyclePolicy,
)
from acp_services.workspace.keys import ApiKeyManager

TENANT = "acp-test-multiuser"
ALPHA = "project_alpha"
BETA = "project_beta"
ALPHA_JOB = "b15-alpha-paused-test"
BETA_JOB = "b15-beta-paused-test"


def _provision(tmp_path: Path) -> dict[str, Any]:
    db_path = tmp_path / "acp_identity.db"
    key_manager = ApiKeyManager(db_path, iterations=2_000)
    policy = IdentityLifecyclePolicy(
        deployment_tier="lab",
        capabilities=("authentication", "isolation"),
    )
    lifecycle = IdentityLifecycle(
        db_path,
        policy=policy,
        key_manager=key_manager,
        password_params={"kdf": "pbkdf2_hmac_sha256", "iterations": 2_000, "salt_bytes": 16},
    )
    accounts: dict[str, Any] = {}
    for name, principal_id, login_id in (
        ("alice", "principal_b15_alice", "b15-alice"),
        ("bob", "principal_b15_bob", "b15-bob"),
    ):
        invitation = lifecycle.invite_account(
            principal_id=principal_id, login_id=login_id, roles=["member"]
        )
        accounts[name] = lifecycle.accept_invitation(
            invitation.token, password=f"B15-Strong-Passw0rd-{name}!"
        )
    keys = {
        "alice_write": lifecycle.issue_key(
            accounts["alice"].account_id,
            scope="workspace",
            actions=["read", "write", "control"],
            project=ALPHA,
        ),
        "bob_write": lifecycle.issue_key(
            accounts["bob"].account_id,
            scope="workspace",
            actions=["read", "write", "control"],
            project=BETA,
        ),
        "bob_read": lifecycle.issue_key(
            accounts["bob"].account_id,
            scope="workspace",
            actions=["read"],
            project=BETA,
        ),
    }
    return {
        "db_path": db_path,
        "key_manager": key_manager,
        "lifecycle": lifecycle,
        "accounts": accounts,
        "keys": keys,
    }


def _seed_paused_job(client: TestClient, job_id: str, project_id: str) -> None:
    manager = client.app.state.job_manager
    work_dir = Path(client.app.state.run_root) / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    JobEventLog(runtime_file(work_dir, "events.jsonl")).append(
        "job.paused", idempotency_key=f"seed:{job_id}", stage="paused"
    )
    manager.store.create(
        JobRecord(
            id=job_id,
            spec=JobSpec(workflow="fake", name=job_id, project_id=project_id),
            status=JobStatus.PAUSED,
            work_dir=str(work_dir),
            project_id=project_id,
            group_id=job_id,
        )
    )


@pytest.fixture()
def multiuser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    provisioned = _provision(tmp_path)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_IDENTITY_MODE", "api-key")
    monkeypatch.setenv("ACP_IDENTITY_DB", str(provisioned["db_path"]))
    monkeypatch.setenv("ACP_IDENTITY_TENANT", TENANT)
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as client:
        _seed_paused_job(client, ALPHA_JOB, ALPHA)
        _seed_paused_job(client, BETA_JOB, BETA)
        yield {"client": client, **provisioned}


def _headers(key: str | None, **extra: str) -> dict[str, str]:
    headers = dict(extra)
    if key is not None:
        headers["X-API-Key"] = key
    return headers


# ---------------------------------------------------------------------------
# Real credential -> principal
# ---------------------------------------------------------------------------


def test_capabilities_report_api_key_mode_and_unsupported_idps(multiuser: dict[str, Any]) -> None:
    payload = multiuser["client"].get("/api/capabilities").json()
    assert payload["identity_mode"] == "api-key"
    assert payload["multi_tenant_security"] == "api-key-scoped"
    assert payload["remote_identity_providers"]["oidc"]["state"] == "unsupported"
    assert payload["remote_identity_providers"]["external_iam"]["state"] == "unsupported"
    binding = payload["stdio_identity_binding"]
    assert binding["shared_launch_allowed"] is False
    assert binding["tool_arguments_are_identity"] is False
    assert set(binding["permission_ceiling"]) == {"read", "write", "control"}


def test_valid_key_resolves_to_server_principal(multiuser: dict[str, Any]) -> None:
    client = multiuser["client"]
    key = multiuser["keys"]["alice_write"].key
    response = client.get("/api/identity", headers=_headers(key))
    assert response.status_code == 200
    body = response.json()
    assert body["principal"]["principal_id"] == "principal_b15_alice"
    assert body["verified_project"] == ALPHA
    assert body["principal"]["roles"] == ["member"]


def test_forged_hints_do_not_elevate(multiuser: dict[str, Any]) -> None:
    client = multiuser["client"]
    key = multiuser["keys"]["alice_write"].key
    baseline = client.get("/api/identity", headers=_headers(key)).json()
    forged = client.get(
        "/api/identity",
        headers=_headers(
            key,
            **{
                "X-ACP-Project": BETA,
                "X-ACP-Workspace": BETA,
                "X-ACP-Tenant": "forged-tenant",
                "X-ACP-Role": "admin",
                "X-ACP-Roles": "admin,operator",
            },
        ),
    ).json()
    assert forged["principal"] == baseline["principal"]
    assert forged["verified_project"] == ALPHA
    assert "admin" not in forged["principal"]["roles"]

    forged_submit = client.post(
        "/api/v1/jobs",
        json={"workflow": "fake", "input": {"source": "C"}, "project_id": BETA},
        headers=_headers(key),
    )
    assert forged_submit.status_code == 403
    assert (
        client.get("/api/identity", headers=_headers(key)).json()["principal"]
        == baseline["principal"]
    )


def test_missing_and_unknown_keys_share_one_401(multiuser: dict[str, Any]) -> None:
    client = multiuser["client"]
    missing = client.get("/api/identity")
    unknown = client.get(
        "/api/identity", headers=_headers("acpk_0000000000000000.not-a-real-secret")
    )
    assert missing.status_code == unknown.status_code == 401
    assert missing.json() == unknown.json()


# ---------------------------------------------------------------------------
# Cross-project isolation and non-disclosure
# ---------------------------------------------------------------------------


def test_cross_project_read_control_prepare_non_disclosure(multiuser: dict[str, Any]) -> None:
    client = multiuser["client"]
    alice = _headers(multiuser["keys"]["alice_write"].key)
    bob = _headers(multiuser["keys"]["bob_write"].key)

    assert client.get(f"/api/v1/jobs/{ALPHA_JOB}", headers=alice).status_code == 200
    assert client.get(f"/api/v1/jobs/{BETA_JOB}", headers=bob).status_code == 200

    cross = client.get(f"/api/v1/jobs/{ALPHA_JOB}", headers=bob)
    missing = client.get("/api/v1/jobs/b15-no-such-task", headers=bob)
    assert cross.status_code == missing.status_code == 404
    assert set(cross.json()) == set(missing.json()) == {"detail"}

    assert client.post(f"/api/v1/jobs/{ALPHA_JOB}/cancel", headers=bob).status_code == 404
    assert (
        client.post(
            f"/api/v1/jobs/{ALPHA_JOB}/prepare",
            json={"purpose": "artifact", "artifact_id": "manifest"},
            headers=bob,
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/jobs/{BETA_JOB}", headers=alice).status_code == 404
    assert client.get(f"/api/v1/jobs/{ALPHA_JOB}/files/note.txt", headers=bob).status_code == 404


def test_job_list_is_scoped_to_the_verified_project(multiuser: dict[str, Any]) -> None:
    client = multiuser["client"]
    alice = client.get(
        "/api/v1/jobs?limit=1000", headers=_headers(multiuser["keys"]["alice_write"].key)
    ).json()
    bob = client.get(
        "/api/v1/jobs?limit=1000", headers=_headers(multiuser["keys"]["bob_write"].key)
    ).json()
    assert {item["project_id"] for item in alice["jobs"]} == {ALPHA}
    assert {item["project_id"] for item in bob["jobs"]} == {BETA}
    # A forged cross-project filter is a not-found, not a second scope.
    forged = client.get(
        f"/api/v1/jobs?project_id={BETA}",
        headers=_headers(multiuser["keys"]["alice_write"].key),
    )
    assert forged.status_code == 404


def test_api_key_action_ceiling_is_enforced(multiuser: dict[str, Any]) -> None:
    client = multiuser["client"]
    read_only = _headers(multiuser["keys"]["bob_read"].key)
    assert client.get(f"/api/v1/jobs/{BETA_JOB}", headers=read_only).status_code == 200
    submit = client.post(
        "/api/v1/jobs",
        json={"workflow": "fake", "input": {"source": "C"}, "project_id": BETA},
        headers=read_only,
    )
    assert submit.status_code == 403
    assert client.post(f"/api/v1/jobs/{BETA_JOB}/cancel", headers=read_only).status_code == 404


# ---------------------------------------------------------------------------
# Revocation: read + already-open event subscription
# ---------------------------------------------------------------------------


def _open_service_stream(multiuser: dict[str, Any], key: str, job_id: str) -> tuple[Any, Any, Any]:
    from acp.api.routes import _ReauthorizingStreamAuthorizer
    from acp_services.contracts.events import EventScope
    from acp_services.runtime.events import AuthorizedEventStream

    client = multiuser["client"]
    boundary = client.app.state.identity_boundary
    manager = client.app.state.job_manager
    identity = boundary.resolve(key)[0]
    assert identity is not None
    stream = AuthorizedEventStream(
        principal_id=identity.principal.principal_id,
        scope=EventScope.TASK,
        service_epoch="b15-test-epoch",
        log_for=lambda _job_id: None,
    )
    stream.subscribe([job_id])
    guard = _ReauthorizingStreamAuthorizer(boundary, key, manager)
    assert stream.check_authorization(guard, job_ids=[job_id]) is True
    return stream, guard, identity


def test_revoked_key_closes_an_open_subscription(multiuser: dict[str, Any]) -> None:
    from acp_services.runtime.events import RESET_PERMISSION_CHANGED

    key_entry = multiuser["keys"]["alice_write"]
    stream, guard, _identity = _open_service_stream(multiuser, key_entry.key, ALPHA_JOB)
    multiuser["key_manager"].revoke(key_entry.key_id)
    assert stream.check_authorization(guard, job_ids=[ALPHA_JOB]) is False
    assert stream.closed is True
    assert stream.closed_reason == RESET_PERMISSION_CHANGED
    batch = stream.poll([])
    assert batch.reset is True
    assert batch.reset_reason == RESET_PERMISSION_CHANGED
    headers = _headers(key_entry.key)
    assert multiuser["client"].get(f"/api/v1/jobs/{ALPHA_JOB}", headers=headers).status_code == 401


def test_revoked_account_closes_an_open_subscription(multiuser: dict[str, Any]) -> None:
    from acp_services.runtime.events import RESET_PERMISSION_CHANGED

    key_entry = multiuser["keys"]["bob_write"]
    stream, guard, _identity = _open_service_stream(multiuser, key_entry.key, BETA_JOB)
    multiuser["lifecycle"].revoke_account(multiuser["accounts"]["bob"].account_id, actor="test")
    assert stream.check_authorization(guard, job_ids=[BETA_JOB]) is False
    assert stream.closed is True
    assert stream.closed_reason == RESET_PERMISSION_CHANGED
    batch = stream.poll([])
    assert batch.reset is True
    assert batch.reset_reason == RESET_PERMISSION_CHANGED
    headers = _headers(key_entry.key)
    assert multiuser["client"].get(f"/api/v1/jobs/{BETA_JOB}", headers=headers).status_code == 401
    account = multiuser["lifecycle"].get_account(multiuser["accounts"]["bob"].account_id)
    assert account is not None and account.status == AccountStatus.REVOKED


# ---------------------------------------------------------------------------
# Stdio proxy identity binding + permission ceiling
# ---------------------------------------------------------------------------


def test_stdio_mapper_is_bound_and_capped() -> None:
    bound = Principal(
        principal_id="principal_bound",
        kind="user",
        tenant_id="tenant-bound",
        roles=("admin",),
    )
    read_only = mcp.IdentityMapper(
        configured_tier=mcp.STDIO_PERMISSION_SCOPE,
        permission_scope=mcp.STDIO_MULTIUSER_SCOPE,
        bound_principal=bound,
        bound_project=ALPHA,
        bound_actions=("read",),
    )
    context = read_only.map(
        {"project_id": ALPHA, "roles": ["admin"], "tenant_id": "forged", "kind": "service"}
    )
    assert context.principal.principal_id == "principal_bound"
    assert context.project_id == ALPHA
    assert context.available_actions == ("read",)

    with pytest.raises(mcp.McpToolError):
        read_only.map({"project_id": BETA})

    full_ceiling = mcp.IdentityMapper(
        configured_tier=mcp.STDIO_PERMISSION_SCOPE,
        bound_principal=bound,
        bound_project=ALPHA,
        bound_actions=("read", "write", "control", "admin"),
    )
    capped = full_ceiling.map({"project_id": ALPHA})
    assert "admin" not in capped.available_actions
    assert set(capped.available_actions) == {"read", "write", "control"}


class _UnusedBackend:
    def capabilities(self) -> Any:
        raise AssertionError("capabilities must not be called for a denied tool")

    def preflight(self, request: Any, *, workspace_id: str) -> Any:
        raise AssertionError("preflight must not be called for a denied tool")

    def submit(self, spec: Any, *, idempotency_key: str, workspace_id: str) -> Any:
        raise AssertionError("submit must not be called for a denied tool")

    def get(self, reference: str, *, workspace_id: str) -> Any:
        raise AssertionError("status must not be called for a denied tool")

    def cancel(self, reference: str, *, workspace_id: str) -> Any:
        raise AssertionError("cancel must not be called for a denied tool")

    def prepare(self, request: Any, *, idempotency_key: str, workspace_id: str) -> Any:
        raise AssertionError("prepare must not be called for a denied tool")

    def read(self, request: Any, *, workspace_id: str) -> Any:
        raise AssertionError("read must not be called for a denied tool")

    def analyze(self, request: Any, *, workspace_id: str) -> Any:
        raise AssertionError("analyze must not be called for a denied tool")


def test_stdio_read_only_key_cannot_call_a_write_tool() -> None:
    backend = _UnusedBackend()
    mapper = mcp.IdentityMapper(
        bound_principal=Principal(
            principal_id="principal_bound", kind="user", tenant_id="tenant-bound"
        ),
        bound_project=ALPHA,
        bound_actions=("read",),
    )
    server = mcp.McpServer(
        backends=mcp.McpBackends(
            capabilities=backend,
            preflight=backend,
            submit=backend,
            status=backend,
            cancel=backend,
            artifacts=backend,
            analysis=backend,
        ),
        identity=mapper,
    )
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "acp_submit",
                "arguments": {"workflow": "fake", "input": {"source": "C"}},
                "_meta": {"roles": ["admin"], "project_id": ALPHA},
            },
        }
    )
    assert response is not None
    assert response["result"]["isError"] is True
    error = response["result"]["structuredContent"]["error"]
    assert error["code"] == "access_denied"


# ---------------------------------------------------------------------------
# Default stays local-single
# ---------------------------------------------------------------------------


def test_default_deployment_remains_local_single(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ACP_IDENTITY_MODE", raising=False)
    monkeypatch.delenv("ACP_IDENTITY_DB", raising=False)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as client:
        capabilities = client.get("/api/capabilities").json()
        assert capabilities["identity_mode"] == "local-single"
        assert capabilities["multi_tenant_security"] is False
        assert client.get("/api/v1/jobs?limit=10").status_code == 200, (
            "trusted local discovery/read must not require a credential"
        )


def test_local_single_identity_endpoint_reports_local_principal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ACP_IDENTITY_MODE", raising=False)
    monkeypatch.delenv("ACP_IDENTITY_DB", raising=False)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=1)) as client:
        body = client.get("/api/identity").json()
        assert body["principal_id"] == "principal_local"
        assert body["mode"] == "local-single"
