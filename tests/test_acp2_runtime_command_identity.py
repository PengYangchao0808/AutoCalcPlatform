"""M0-04 (D02/R09) — one CommandIdentity for HTTP/CLI, real idempotency scope.

The gap this file pins down:

* the HTTP submit entry hard-coded ``LOCAL_PRINCIPAL_ID`` in the idempotency
  scope and the admission ledger recorded the Controller-process default
  principal, so in the opt-in multi-user deployment **Bob's retry of the same
  key replayed Alice's task** (``reports/acp2_delivery_gap_audit_20261026/
  evidence/verification-r3-20261026/http.json::two_principals_same_project``);
* the CLI local task manager assembled no admission at all, silently bypassing
  the quota every HTTP/MCP submit goes through.

The tests below use the real ASGI Controller, the real identity store (API
keys minted through ``ApiKeyManager``/``IdentityLifecycle``), the real
persistent ``idempotency_records``/``admission_reservations`` rows and the real
CLI entry.  Scientific dispatch is suppressed: this is identity/admission
evidence, not QC evidence.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp_services.contracts.access import LOCAL_PRINCIPAL_ID
from acp_services.runtime.idempotency import (
    IDENTITY_VERSION,
    LEGACY_IDENTITY_VERSION,
    MIGRATION_STATE_NATIVE,
    MIGRATION_STATE_PENDING,
    MIGRATION_STATE_UNVERIFIED,
    MIGRATION_STATE_VERIFIED,
    REQUEST_DIGEST_VERSION,
    SCOPE_ORIGIN_LEGACY_LOCAL,
    SCOPE_ORIGIN_VERIFIED,
    canonical_request_digest,
    resolve_scope_id,
    split_scope_id,
)
from acp_services.runtime.local_admission import AdmissionRefusedError
from acp_services.store.records import JobSpec
from acp_services.store.store import JobStore
from acp_services.workspace.command_identity import (
    CommandIdentity,
    EntryClass,
    SubjectKind,
)
from acp_services.workspace.server_identity import ServerIdentity
from tests.test_acp2_runtime_multitenant import ALPHA, BETA, TENANT, _provision

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
PYTHON = sys.executable

PAYLOAD: dict[str, Any] = {
    "workflow": "fake",
    "input": {"source": "C"},
    "project_id": ALPHA,
    "resources": {"cores": 1, "memory_mb": 256},
}


def _payload(**overrides: Any) -> dict[str, Any]:
    return {**PAYLOAD, **overrides}


def _headers(key: str | None) -> dict[str, str]:
    return {} if key is None else {"X-API-Key": key}


def _post(client: TestClient, key: str | None, body: dict[str, Any]) -> Any:
    return client.post("/api/v1/jobs", json=body, headers=_headers(key))


def _reservation_rows(db_path: Path) -> list[tuple[str, str, str | None]]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT reservation_id, principal_id, key_id FROM admission_reservations"
        ).fetchall()
    finally:
        conn.close()
    return [(str(row[0]), str(row[1]), None if row[2] is None else str(row[2])) for row in rows]


@pytest.fixture()
def multiuser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    provisioned = _provision(tmp_path)
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_IDENTITY_MODE", "api-key")
    monkeypatch.setenv("ACP_IDENTITY_DB", str(provisioned["db_path"]))
    monkeypatch.setenv("ACP_IDENTITY_TENANT", TENANT)
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=2, poll_interval=60)) as client:
        manager = client.app.state.job_manager
        suppressed: list[str] = []
        monkeypatch.setattr(
            manager,
            "_start_submission_thread",
            lambda *args, **kwargs: suppressed.append("dispatch"),
        )
        bob_alpha = provisioned["lifecycle"].issue_key(
            provisioned["accounts"]["bob"].account_id,
            scope="workspace",
            actions=["read", "write", "control"],
            project=ALPHA,
        )
        yield {
            "client": client,
            "admission_db": tmp_path / "acp_admission.db",
            "alice_key": provisioned["keys"]["alice_write"].key,
            "bob_alpha_key": bob_alpha.key,
            "suppressed": suppressed,
            "provisioned": provisioned,
        }


# ---------------------------------------------------------------------------
# 1. CommandIdentity projections
# ---------------------------------------------------------------------------


def test_from_local_is_an_explicit_operator_identity() -> None:
    identity = CommandIdentity.from_local(project_id=ALPHA)
    assert identity.principal_id == LOCAL_PRINCIPAL_ID
    assert identity.subject_kind == SubjectKind.OPERATOR
    assert identity.entry_class == EntryClass.CLI
    assert identity.permission_scope == "local-operator"
    assert identity.scope_origin == SCOPE_ORIGIN_VERIFIED
    assert identity.is_operator is True
    # A local operator declares no credential and no ceiling.
    assert identity.key_id is None
    assert identity.actions == ()
    assert identity.allows("write") is True
    assert identity.idempotency_scope() == resolve_scope_id(LOCAL_PRINCIPAL_ID, ALPHA)


def test_from_server_marks_the_local_operator_explicitly() -> None:
    server = ServerIdentity.build(verified_project=ALPHA)
    identity = CommandIdentity.from_server(server, entry_class=EntryClass.HTTP)
    assert identity.principal_id == LOCAL_PRINCIPAL_ID
    assert identity.subject_kind == SubjectKind.OPERATOR
    assert identity.project_id == ALPHA
    assert identity.key_id is None
    assert identity.actions == ("read", "write", "control")
    assert identity.to_dict()["scope_origin"] == SCOPE_ORIGIN_VERIFIED


def test_identity_never_carries_the_raw_credential() -> None:
    identity = CommandIdentity.from_local(project_id=ALPHA)
    serialized = json.dumps(identity.to_dict())
    assert "secret" not in serialized
    projection = identity.admission_projection()
    assert projection["principal_id"] == LOCAL_PRINCIPAL_ID
    assert projection["subject_kind"] == SubjectKind.OPERATOR
    assert projection["scope_origin"] == SCOPE_ORIGIN_VERIFIED


def test_from_verified_http_projects_the_credential_principal(multiuser: dict[str, Any]) -> None:
    boundary = multiuser["client"].app.state.identity_boundary
    resolved, denial = boundary.resolve(multiuser["alice_key"])
    assert denial is None and resolved is not None

    identity = CommandIdentity.from_verified_http(resolved, project_id=ALPHA)
    assert identity.principal_id == resolved.principal.principal_id
    assert identity.principal_id != LOCAL_PRINCIPAL_ID
    assert identity.subject_kind == SubjectKind.USER
    assert identity.key_id == resolved.key_id
    assert identity.key_id is not None
    assert identity.actions == ("read", "write", "control")
    assert identity.permission_scope == "workspace"
    assert identity.scope_origin == SCOPE_ORIGIN_VERIFIED
    assert identity.idempotency_scope(project_id=ALPHA) == resolve_scope_id(
        resolved.principal.principal_id, ALPHA
    )
    # The scope id itself separates users: same project, different principal.
    bob = multiuser["client"].app.state.identity_boundary.resolve(multiuser["bob_alpha_key"])[0]
    assert bob is not None
    assert identity.idempotency_scope(project_id=ALPHA) != CommandIdentity.from_verified_http(
        bob, project_id=ALPHA
    ).idempotency_scope(project_id=ALPHA)


# ---------------------------------------------------------------------------
# 2. HTTP submit: cross-user conflict, same-user replay, cross-project conflict
# ---------------------------------------------------------------------------


def test_two_principals_same_project_same_key_is_an_explicit_conflict(
    multiuser: dict[str, Any],
) -> None:
    client = multiuser["client"]
    alice = _post(client, multiuser["alice_key"], _payload(idempotency_key="m0-04-shared"))
    assert alice.status_code == 201, alice.text
    first = alice.json()
    assert first["idempotent_replay"] is False

    bob = _post(client, multiuser["bob_alpha_key"], _payload(idempotency_key="m0-04-shared"))
    assert bob.status_code == 409, bob.text
    detail = bob.json()["detail"]
    assert detail["code"] == "idempotency_conflict"
    assert detail["retryable"] is False

    # Bob never receives Alice's task and no second task was created.
    manager = client.app.state.job_manager
    jobs = manager.store.list(limit=100)
    assert [job.id for job in jobs] == [first["job_id"]]
    assert first["identity"]["principal_id"] != detail.get("principal_id")


def test_same_principal_retry_replays_the_same_complete_task_ref(
    multiuser: dict[str, Any],
) -> None:
    client = multiuser["client"]
    key = multiuser["alice_key"]
    first = _post(client, key, _payload(idempotency_key="m0-04-retry"))
    second = _post(client, key, _payload(idempotency_key="m0-04-retry"))
    assert first.status_code == 201 and second.status_code == 201
    assert second.json()["idempotent_replay"] is True
    assert second.json()["task_ref"] == first.json()["task_ref"]
    assert second.json()["job_id"] == first.json()["job_id"]
    assert len(client.app.state.job_manager.store.list(limit=100)) == 1


def test_same_principal_cross_project_same_key_stays_a_conflict(
    multiuser: dict[str, Any],
) -> None:
    client = multiuser["client"]
    provisioned = multiuser["provisioned"]
    alice_beta = provisioned["lifecycle"].issue_key(
        provisioned["accounts"]["alice"].account_id,
        scope="workspace",
        actions=["read", "write", "control"],
        project=BETA,
    )
    alpha = _post(client, multiuser["alice_key"], _payload(idempotency_key="m0-04-cross"))
    assert alpha.status_code == 201, alpha.text
    beta = _post(
        client,
        alice_beta.key,
        _payload(idempotency_key="m0-04-cross", project_id=BETA),
    )
    assert beta.status_code == 409, beta.text
    assert beta.json()["detail"]["code"] == "idempotency_conflict"
    assert len(client.app.state.job_manager.store.list(limit=100)) == 1


# ---------------------------------------------------------------------------
# 3. Admission attribution per principal (keyed and legacy submit paths)
# ---------------------------------------------------------------------------


def test_admission_principal_is_not_merged_across_users(multiuser: dict[str, Any]) -> None:
    client = multiuser["client"]
    alice = _post(client, multiuser["alice_key"], _payload(idempotency_key="m0-04-adm-a"))
    bob = _post(client, multiuser["bob_alpha_key"], _payload(idempotency_key="m0-04-adm-b"))
    assert alice.status_code == 201 and bob.status_code == 201

    alice_id = alice.json()["identity"]["principal_id"]
    bob_id = bob.json()["identity"]["principal_id"]
    assert alice_id != bob_id
    assert alice.json()["admission"]["principal_id"] == alice_id
    assert bob.json()["admission"]["principal_id"] == bob_id

    rows = _reservation_rows(multiuser["admission_db"])
    principals = {principal for _, principal, _ in rows}
    assert principals == {alice_id, bob_id}
    # The credential reference follows the reservation (never the secret).
    keys = {key_id for _, _, key_id in rows}
    assert None not in keys and len(keys) == 2


def test_submit_without_idempotency_key_still_attributes_the_real_principal(
    multiuser: dict[str, Any],
) -> None:
    client = multiuser["client"]
    response = _post(client, multiuser["bob_alpha_key"], _payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["idempotency_key"] is None
    assert body["admission"]["principal_id"] == body["identity"]["principal_id"]
    rows = _reservation_rows(multiuser["admission_db"])
    assert [principal for _, principal, _ in rows] == [body["identity"]["principal_id"]]


# ---------------------------------------------------------------------------
# 4. CLI local task manager: explicit operator identity + same admission
# ---------------------------------------------------------------------------


def _cli_spec() -> JobSpec:
    return JobSpec(workflow="fake", name="m0-04-cli", input={"source": "C"}, project_id=ALPHA)


def test_cli_local_submit_records_the_operator_admission_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp import cli

    monkeypatch.setenv("ACP_SERVER_PROJECT", ALPHA)
    identity = cli._task_identity()
    assert identity.principal_id == LOCAL_PRINCIPAL_ID
    assert identity.subject_kind == SubjectKind.OPERATOR
    assert identity.entry_class == EntryClass.CLI
    assert identity.project_id == ALPHA

    manager = cli._task_manager(tmp_path)
    monkeypatch.setattr(manager, "_start_submission_thread", lambda *a, **k: None)
    try:
        record = cli.job_submit_command(_cli_spec(), manager=manager, identity=identity)
        admission = (record.result or {}).get("admission")
        assert admission is not None
        assert admission["principal_id"] == LOCAL_PRINCIPAL_ID
    finally:
        manager.shutdown()

    rows = _reservation_rows(tmp_path / "acp_admission.db")
    assert [principal for _, principal, _ in rows] == [LOCAL_PRINCIPAL_ID]


def test_cli_local_submit_is_no_longer_a_quota_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behaviour change (M0-04): the CLI now goes through the same admission.

    Before this change ``_task_manager`` built a ``JobManager`` with no
    admission at all, so a CLI submit was never quota-checked.  The assertion
    is a real refusal, not a mocked one.
    """
    from acp import cli

    monkeypatch.setenv("ACP_ADMISSION_MAX_TASKS", "0")
    identity = cli._task_identity()
    manager = cli._task_manager(tmp_path)
    monkeypatch.setattr(manager, "_start_submission_thread", lambda *a, **k: None)
    try:
        with pytest.raises(AdmissionRefusedError):
            cli.job_submit_command(_cli_spec(), manager=manager, identity=identity)
        assert manager.store.list(limit=10) == []
    finally:
        manager.shutdown()


def test_cli_task_execute_wires_the_operator_identity_end_to_end(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            PYTHON,
            "-m",
            "acp.cli",
            "task",
            "execute",
            "--run-root",
            str(tmp_path),
            "--workflow",
            "fake",
            "--input",
            "C",
            "--follow",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "PYTHONPATH": str(SRC_ROOT)},
    )
    assert completed.returncode == 0, completed.stderr
    json_lines = [
        line
        for line in completed.stdout.splitlines()
        if line.startswith("{") and line.endswith("}")
    ]
    assert json_lines, completed.stdout
    payload = json.loads(json_lines[-1])
    assert payload["status"] == "completed"
    rows = _reservation_rows(tmp_path / "acp_admission.db")
    assert [principal for _, principal, _ in rows] == [LOCAL_PRINCIPAL_ID]


# ---------------------------------------------------------------------------
# 5. R09 legacy-row migration and reconciliation
# ---------------------------------------------------------------------------


LEGACY_SCOPE = resolve_scope_id(LOCAL_PRINCIPAL_ID, "legacy-project")
LEGACY_DIGEST = canonical_request_digest(PAYLOAD)


def _legacy_jobs_db(path: Path) -> None:
    """Build a pre-M0-04 ledger (schema ≤ 023) with real v1 rows.

    The rows use the frozen migration-022 column set — exactly what the code
    wrote before this change — and are *not* generated by the new insert path.
    """
    from acp_services.store import migrations

    original = list(migrations._MIGRATIONS)
    try:
        migrations._MIGRATIONS[:] = [item for item in original if item["id"] <= "023"]
        migrations.migrate(path)
    finally:
        migrations._MIGRATIONS[:] = original

    now = "2026-10-01T00:00:00+00:00"
    rows = (
        (
            "idem_legacy_committed",
            LEGACY_SCOPE,
            "legacy-committed",
            "committed",
            "legacy-task-1",
            "legacy-owner",
            None,
        ),
        (
            "idem_legacy_pending",
            LEGACY_SCOPE,
            "legacy-pending",
            "claimed",
            None,
            "legacy-owner",
            "2026-10-01T00:01:00+00:00",
        ),
        (
            "idem_legacy_unknown",
            "unattributable-scope",
            "legacy-unknown",
            "committed",
            "legacy-task-2",
            "legacy-owner",
            None,
        ),
    )
    conn = sqlite3.connect(path)
    try:
        for claim_id, scope, key, state, task_id, owner, expires in rows:
            conn.execute(
                """INSERT INTO idempotency_records (
                       claim_id, scope_id, action, idempotency_key, request_digest,
                       state, owner_token, lease_epoch, lease_expires_at, task_id,
                       operation_id, created_at, updated_at)
                   VALUES (?, ?, 'submit', ?, ?, ?, ?, 0, ?, ?, NULL, ?, ?)""",
                (claim_id, scope, key, LEGACY_DIGEST, state, owner, expires, task_id, now, now),
            )
        conn.commit()
    finally:
        conn.close()


def test_legacy_rows_are_versioned_and_verifiable_rows_still_replay(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_jobs.db"
    _legacy_jobs_db(db_path)
    store = JobStore(db_path)

    committed = store.find_idempotency_claim(LEGACY_SCOPE, "submit", "legacy-committed")
    assert committed is not None
    assert committed.identity_version == LEGACY_IDENTITY_VERSION
    assert committed.request_digest_version == REQUEST_DIGEST_VERSION
    assert committed.scope_origin == SCOPE_ORIGIN_LEGACY_LOCAL
    assert committed.migration_state == MIGRATION_STATE_VERIFIED
    assert committed.principal_id == LOCAL_PRINCIPAL_ID
    assert committed.project_id == "legacy-project"

    # Same verified scope (the local operator retrying the same key) still
    # replays the durable TaskRef — the old task is not lost.
    outcome = store.claim_idempotency(LEGACY_SCOPE, "submit", "legacy-committed", LEGACY_DIGEST)
    assert outcome.replay is True
    assert outcome.task_ref is not None
    assert outcome.task_ref["task_id"] == "legacy-task-1"

    # A different principal never adopts that row.
    other_scope = resolve_scope_id("principal_b15_alice", "legacy-project")
    stolen = store.claim_idempotency(other_scope, "submit", "legacy-committed", LEGACY_DIGEST)
    assert stolen.conflict is True
    assert stolen.task_ref is None


def test_legacy_pending_row_takes_over_and_keeps_its_provenance(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_pending.db"
    _legacy_jobs_db(db_path)
    store = JobStore(db_path)

    pending = store.find_idempotency_claim(LEGACY_SCOPE, "submit", "legacy-pending")
    assert pending is not None and pending.migration_state == MIGRATION_STATE_PENDING

    outcome = store.claim_idempotency(LEGACY_SCOPE, "submit", "legacy-pending", LEGACY_DIGEST)
    assert outcome.created is True
    assert outcome.claim is not None
    assert outcome.claim.owner_token is not None
    assert outcome.claim.identity_version == LEGACY_IDENTITY_VERSION


def test_unattributable_legacy_row_is_a_typed_conflict_not_a_replay(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_unknown.db"
    _legacy_jobs_db(db_path)
    store = JobStore(db_path)

    unknown = store.find_idempotency_claim("unattributable-scope", "submit", "legacy-unknown")
    assert unknown is not None
    assert unknown.migration_state == MIGRATION_STATE_UNVERIFIED
    assert unknown.principal_id is None and unknown.project_id is None
    assert unknown.is_replayable is False

    outcome = store.claim_idempotency(
        "unattributable-scope", "submit", "legacy-unknown", LEGACY_DIGEST
    )
    assert outcome.conflict is True
    assert outcome.task_ref is None
    assert "reconcile" in outcome.detail


def _insert_legacy_row(
    path: Path,
    *,
    claim_id: str,
    scope: str,
    key: str,
    state: str,
    task_id: str | None,
) -> None:
    now = "2026-10-01T00:00:00+00:00"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """INSERT INTO idempotency_records (
                   claim_id, scope_id, action, idempotency_key, request_digest,
                   state, owner_token, lease_epoch, lease_expires_at, task_id,
                   operation_id, created_at, updated_at)
               VALUES (?, ?, 'submit', ?, ?, ?, 'legacy-owner', 0, NULL, ?,
                       NULL, ?, ?)""",
            (claim_id, scope, key, LEGACY_DIGEST, state, task_id, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def test_legacy_auto_derived_mcp_key_is_a_distinct_reconciliation_namespace(
    tmp_path: Path,
) -> None:
    """Old MCP `auto_` keys stay a separate namespace from new raw client keys.

    The fixture key is produced by the **old** derivation helper (not hand
    written), so it is exactly the row an old MCP client left behind.  Design
    for M0-05's lookup: current identity + raw key first, then the versioned
    legacy ``auto_`` key under the legacy scope.  A new principal's raw key
    never collides with it, and an explicit reuse of the ``auto_`` key by that
    principal is a typed conflict — never Alice's task.
    """
    from acp_services.entrypoints.mcp import derive_idempotency_key

    auto_key = derive_idempotency_key(PAYLOAD)
    assert auto_key.startswith("auto_") and not auto_key.startswith("op_")

    db_path = tmp_path / "legacy_auto.db"
    _legacy_jobs_db(db_path)
    _insert_legacy_row(
        db_path,
        claim_id="idem_legacy_auto",
        scope=LEGACY_SCOPE,
        key=auto_key,
        state="committed",
        task_id="legacy-task-auto",
    )
    store = JobStore(db_path)

    legacy = store.find_idempotency_claim(LEGACY_SCOPE, "submit", auto_key)
    assert legacy is not None
    assert legacy.migration_state == MIGRATION_STATE_VERIFIED
    assert legacy.scope_origin == SCOPE_ORIGIN_LEGACY_LOCAL
    assert legacy.principal_id == LOCAL_PRINCIPAL_ID

    alice_scope = resolve_scope_id("principal_b15_alice", ALPHA)
    fresh = store.claim_idempotency(alice_scope, "submit", "rph-round-1-submit-1", LEGACY_DIGEST)
    assert fresh.created is True

    adopted = store.claim_idempotency(alice_scope, "submit", auto_key, LEGACY_DIGEST)
    assert adopted.conflict is True
    assert adopted.task_ref is None

    # The legacy local operator can still recover its own old task.
    replay = store.claim_idempotency(LEGACY_SCOPE, "submit", auto_key, LEGACY_DIGEST)
    assert replay.replay is True
    assert replay.task_ref is not None
    assert replay.task_ref["task_id"] == "legacy-task-auto"


def test_new_rows_are_native_and_carry_the_verified_attribution(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "fresh.db")
    scope = resolve_scope_id("principal_b15_alice", ALPHA)
    outcome = store.claim_idempotency(
        scope,
        "submit",
        "fresh-key",
        LEGACY_DIGEST,
        principal_id="principal_b15_alice",
        project_id=ALPHA,
    )
    assert outcome.created is True
    claim = outcome.claim
    assert claim is not None
    assert claim.identity_version == IDENTITY_VERSION
    assert claim.migration_state == MIGRATION_STATE_NATIVE
    assert claim.scope_origin == SCOPE_ORIGIN_VERIFIED
    assert claim.principal_id == "principal_b15_alice"
    assert claim.project_id == ALPHA
    assert split_scope_id(claim.scope_id) == ("principal_b15_alice", ALPHA)
