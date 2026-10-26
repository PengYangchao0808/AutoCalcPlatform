"""M0-05 — one MCP idempotency authority, verified identity, per-call revalidation.

The gaps this file pins down (``reports/acp2_delivery_gap_audit_20261026/
evidence/verification-r3-20261026/``):

* ``G21`` — the MCP adapter kept its own result cache and folded the permission
  scope into the ledger key, so a cached answer was replayed without touching
  the Controller.  The cache is gone: the raw client key is forwarded unchanged
  and the Controller's persistent ledger is the only authority.
* ``G22`` — ``auto_`` was the old derived namespace while ``op_`` is an
  operation reference; they must never be mixed, and a pre-M0-05 ``auto_`` row
  must only replay for the binding that wrote it.
* ``G25`` — the stdio proxy resolved the credential once at startup, so a
  revoked key kept working from the cache.  Every tool call now revalidates the
  server-side binding.

The tests run **real** OS processes (uvicorn Controller with API-key identity +
``acp mcp serve`` stdio proxies), the real persistent ``idempotency_records``
ledger and the real Data prepare store.  Scientific dispatch is out of scope:
the ``fake`` workflow keeps this identity/ledger evidence, not QC evidence.
"""

from __future__ import annotations

import json
import os
import queue
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import pytest

from acp_services.contracts.access import LOCAL_PRINCIPAL_ID, LocalPrincipal
from acp_services.entrypoints import mcp
from acp_services.runtime.idempotency import (
    MIGRATION_STATE_VERIFIED,
    canonical_request_digest,
    resolve_scope_id,
)
from acp_services.workspace.command_identity import EntryClass, PermissionScope
from tests.test_acp2_runtime_multitenant import ALPHA, TENANT, _provision

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
PYTHON = sys.executable

SHARED_KEY = "m0-05-shared-" + uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# Real Controller / real stdio MCP process harness
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _env(run_root: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(SRC_ROOT) if not existing else f"{SRC_ROOT}{os.pathsep}{existing}"
    env["ACP_RUN_ROOT"] = str(run_root)
    env.update(extra)
    return env


def _http(url: str, *, api_key: str | None = None, timeout: float = 10) -> Any:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_health(base: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _http(base + "/api/health", timeout=1).get("status") == "ok":
                return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.1)
    raise RuntimeError("controller did not become healthy")


def _wait_port_free(port: int, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"port {port} did not become free")


def _start_controller(run_root: Path, port: int, **env: str) -> subprocess.Popen[bytes]:
    process_env = _env(
        run_root,
        ACP_HOST="127.0.0.1",
        ACP_PORT=str(port),
        ACP_POLL_INTERVAL="1",
        ACP_CANDIDATE_REVISION="m0-05-test",
        **env,
    )
    return subprocess.Popen(
        [
            PYTHON,
            "-m",
            "uvicorn",
            "acp.api.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=process_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


class _McpProcess:
    """One real ``acp mcp serve`` stdio process."""

    def __init__(self, run_root: Path, base: str, *, api_key: str | None = None) -> None:
        extra = {} if api_key is None else {"ACP_API_KEY": api_key}
        env = _env(run_root, **extra)
        if api_key is None:
            # The stdio proxy must not inherit a credential from the pytest env.
            env.pop("ACP_API_KEY", None)
        self.proc = subprocess.Popen(
            [
                PYTHON,
                "-m",
                "acp",
                "mcp",
                "serve",
                "--transport",
                "stdio",
                "--controller",
                base,
                "--log-level",
                "WARNING",
            ],
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            text=False,
        )
        self._next_id = 1
        self._frames: queue.Queue[bytes | None] = queue.Queue()
        self._stderr: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._frames.put(line)
        self._frames.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr.append(line.decode("utf-8", errors="replace"))

    def request(self, method: str, params: dict[str, Any], timeout: float = 60) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        assert self.proc.stdin is not None
        self.proc.stdin.write(
            (
                json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
                + "\n"
            ).encode()
        )
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(method)
            try:
                line = self._frames.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(method) from None
            if line is None:
                raise RuntimeError("mcp process closed stdout: " + "".join(self._stderr)[-1000:])
            message = json.loads(line)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "m0-05-test", "version": "1"},
            },
        )
        assert self.proc.stdin is not None
        self.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.proc.stdin.flush()

    def tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def submit(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.tool("acp_submit", arguments)

    def close(self) -> None:
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def _data(result: dict[str, Any]) -> dict[str, Any]:
    return (result.get("structuredContent") or {}).get("data") or {}


def _error_code(result: dict[str, Any]) -> str:
    return str(((result.get("structuredContent") or {}).get("error") or {}).get("code") or "")


def _task_count(base: str, api_key: str | None = None) -> int:
    return len(_http(base + "/api/v1/jobs?limit=1000", api_key=api_key).get("jobs") or [])


def _ledger_keys(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT idempotency_key FROM idempotency_records").fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


# ---------------------------------------------------------------------------
# 1. Two real MCP processes, two principals, one raw key
# ---------------------------------------------------------------------------


def test_two_principals_same_raw_key_conflict_and_never_cross_tasks(tmp_path: Path) -> None:
    run_root = tmp_path / "run-root"
    run_root.mkdir(parents=True, exist_ok=True)
    provisioned = _provision(tmp_path)
    bob_alpha = provisioned["lifecycle"].issue_key(
        provisioned["accounts"]["bob"].account_id,
        scope="workspace",
        actions=["read", "write", "control"],
        project=ALPHA,
    )
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    controller = _start_controller(
        run_root,
        port,
        ACP_IDENTITY_MODE="api-key",
        ACP_IDENTITY_DB=str(provisioned["db_path"]),
        ACP_IDENTITY_TENANT=TENANT,
    )
    probes: list[_McpProcess] = []
    try:
        _wait_health(base)
        before = _task_count(base, provisioned["keys"]["alice_write"].key)
        probes = [
            _McpProcess(run_root, base, api_key=provisioned["keys"]["alice_write"].key),
            _McpProcess(run_root, base, api_key=bob_alpha.key),
        ]
        for probe in probes:
            probe.initialize()
        arguments = {
            "workflow": "fake",
            "input": {"source": "C"},
            "project_id": ALPHA,
            "idempotency_key": SHARED_KEY,
        }
        barrier = threading.Barrier(2)
        results: list[dict[str, Any]] = [{} for _ in probes]
        failures: list[str] = ["" for _ in probes]

        def run(index: int) -> None:
            try:
                barrier.wait(timeout=30)
                results[index] = probes[index].submit(arguments)
            except Exception as exc:  # noqa: BLE001 - recorded as an assertion detail
                failures[index] = f"{type(exc).__name__}: {exc}"

        threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        assert failures == ["", ""], failures

        created = [item for item in results if item.get("isError") is not True]
        refused = [item for item in results if item.get("isError") is True]
        assert len(created) == 1, results
        assert len(refused) == 1, results
        # The second principal gets an explicit conflict, never the first TaskRef.
        assert _error_code(refused[0]) == "revision_conflict", refused[0]
        task_ref = _data(created[0]).get("task_ref") or {}
        task_id = task_ref.get("task_id") or _data(created[0]).get("reference", {}).get("id")
        assert task_id
        assert _data(refused[0]).get("task_ref") is None
        # Exactly one task, and it belongs to Alice's project scope.
        assert _task_count(base, provisioned["keys"]["alice_write"].key) == before + 1

        # The ledger key is the raw client key, never an ``op_``/``auto_`` fold.
        keys = _ledger_keys(run_root / "acp_jobs.db")
        assert SHARED_KEY in keys
        assert not any(key.startswith("op_") for key in keys)
    finally:
        for probe in probes:
            probe.close()
        _stop(controller)


# ---------------------------------------------------------------------------
# 2. Mid-session revocation (G25): write, control and read all refuse
# ---------------------------------------------------------------------------


def test_revoked_key_is_refused_mid_session_without_side_effects(tmp_path: Path) -> None:
    run_root = tmp_path / "run-root"
    run_root.mkdir(parents=True, exist_ok=True)
    provisioned = _provision(tmp_path)
    alice = provisioned["keys"]["alice_write"]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    controller = _start_controller(
        run_root,
        port,
        ACP_IDENTITY_MODE="api-key",
        ACP_IDENTITY_DB=str(provisioned["db_path"]),
        ACP_IDENTITY_TENANT=TENANT,
    )
    probe: _McpProcess | None = None
    try:
        _wait_health(base)
        probe = _McpProcess(run_root, base, api_key=alice.key)
        probe.initialize()
        first = probe.submit(
            {
                "workflow": "fake",
                "input": {"source": "C"},
                "project_id": ALPHA,
                "idempotency_key": "m0-05-revoke-1-" + uuid.uuid4().hex[:12],
            }
        )
        assert first.get("isError") is not True, first
        task_id = str(_data(first).get("task_ref", {}).get("task_id") or "")
        assert task_id
        count_before = _task_count(base, alice.key)
        assert count_before == 1

        assert provisioned["key_manager"].revoke(alice.key_id) is True

        # The same connection now refuses a write...
        refused = probe.submit(
            {
                "workflow": "fake",
                "input": {"source": "N"},
                "project_id": ALPHA,
                "idempotency_key": "m0-05-revoke-2-" + uuid.uuid4().hex[:12],
            }
        )
        assert refused.get("isError") is True, refused
        assert _error_code(refused) == "access_denied", refused
        # ...and a read: the poll path re-authorizes too.
        status = probe.tool("acp_status", {"reference": task_id})
        assert status.get("isError") is True, status
        assert _error_code(status) == "access_denied", status
        # Nothing was created by the refused calls.
        assert len(_ledger_keys(run_root / "acp_jobs.db")) == 1
    finally:
        if probe is not None:
            probe.close()
        _stop(controller)


# ---------------------------------------------------------------------------
# 3. Frozen legacy ``auto_`` row: same binding replays, another binding does not
# ---------------------------------------------------------------------------


def _seed_legacy_auto_row(
    run_root: Path,
    *,
    body: dict[str, Any],
    project_id: str,
    task_id: str,
) -> str:
    """Insert a pre-M0-05 MCP ledger row (real old derivation + real digest).

    The key comes from the frozen :func:`mcp.derive_idempotency_key` formula and
    the digest from the Controller's own canonical request digest over the V1
    request model, so this is exactly the row the old code left behind.  The
    row is then classified the way migration ``024`` classifies a committed,
    parseable legacy row: ``identity_version='1'`` + ``legacy-verified``.
    """
    from acp.api.v1_schemas import V1JobCreateRequest
    from acp_services.store.records import JobRecord, JobSpec, JobStatus
    from acp_services.store.store import JobStore

    auto_key = mcp.derive_idempotency_key(body)
    assert auto_key.startswith(mcp.LEGACY_DERIVED_KEY_PREFIX)
    normalized = V1JobCreateRequest.model_validate(body).model_dump(exclude_none=True)
    digest = canonical_request_digest(normalized)
    scope = resolve_scope_id(LOCAL_PRINCIPAL_ID, project_id)

    store = JobStore(run_root / "acp_jobs.db")
    # A committed claim replays only when its durable TaskRef still resolves to
    # a job row: the legacy fixture therefore carries the task it produced.
    work_dir = run_root / "legacy" / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    store.create(
        JobRecord(
            id=task_id,
            spec=JobSpec(
                workflow=str(body.get("workflow") or "fake"),
                name=task_id,
                input=dict(body.get("input") or {}),
                project_id=project_id,
            ),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            project_id=project_id,
            group_id=task_id,
        )
    )
    now = "2026-10-01T00:00:00+00:00"
    conn = sqlite3.connect(run_root / "acp_jobs.db")
    try:
        conn.execute(
            """INSERT INTO idempotency_records (
                   claim_id, scope_id, action, idempotency_key, request_digest,
                   state, owner_token, lease_epoch, lease_expires_at, task_id,
                   operation_id, created_at, updated_at, principal_id, project_id,
                   identity_version, request_digest_version, scope_origin,
                   migration_state)
               VALUES (?, ?, 'submit', ?, ?, 'committed', 'legacy-owner', 0, NULL, ?,
                       NULL, ?, ?, ?, ?, '1', '1', 'legacy-local', ?)""",
            (
                "idem_legacy_" + uuid.uuid4().hex[:12],
                scope,
                auto_key,
                digest,
                task_id,
                now,
                now,
                LOCAL_PRINCIPAL_ID,
                project_id,
                MIGRATION_STATE_VERIFIED,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    claim = store.find_idempotency_claim(scope, "submit", auto_key)
    assert claim is not None
    assert claim.migration_state == MIGRATION_STATE_VERIFIED
    assert claim.identity_version == "1"
    return auto_key


def test_legacy_auto_row_replays_on_the_same_binding_only(tmp_path: Path) -> None:
    # -- same binding: the local operator entry replays the old task ----------
    run_root = tmp_path / "local-run-root"
    run_root.mkdir(parents=True, exist_ok=True)
    legacy_task = "legacy-auto-task-" + uuid.uuid4().hex[:8]
    body = {"workflow": "fake", "input": {"source": "C"}, "project_id": ALPHA}
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    controller = _start_controller(run_root, port)
    probe: _McpProcess | None = None
    try:
        _wait_health(base)
        auto_key = _seed_legacy_auto_row(run_root, body=body, project_id=ALPHA, task_id=legacy_task)
        before = _task_count(base)
        probe = _McpProcess(run_root, base)
        probe.initialize()
        replayed = probe.submit(dict(body))
        assert replayed.get("isError") is not True, replayed
        data = _data(replayed)
        task_id = str((data.get("task_ref") or {}).get("task_id") or "")
        assert task_id == legacy_task, data
        assert data.get("idempotency_key_origin") == mcp.IDEMPOTENCY_KEY_ORIGIN_LEGACY
        assert data.get("idempotency", {}).get("origin") == mcp.IDEMPOTENCY_KEY_ORIGIN_LEGACY
        assert (
            data.get("idempotency", {}).get("legacy_rule_version") == mcp.LEGACY_DERIVED_KEY_VERSION
        )
        assert _task_count(base) == before
        keys = _ledger_keys(run_root / "acp_jobs.db")
        assert auto_key in keys
        assert not any(key.startswith("op_") for key in keys)
    finally:
        if probe is not None:
            probe.close()
        _stop(controller)
    _wait_port_free(port)

    # -- another binding: an authenticated principal never adopts it ---------
    bound_root = tmp_path / "bound-run-root"
    bound_root.mkdir(parents=True, exist_ok=True)
    bound_task = "legacy-bound-task-" + uuid.uuid4().hex[:8]
    bound_body = {"workflow": "fake", "input": {"source": "N"}, "project_id": ALPHA}
    provisioned = _provision(tmp_path)
    alice = provisioned["keys"]["alice_write"]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    controller = _start_controller(
        bound_root,
        port,
        ACP_IDENTITY_MODE="api-key",
        ACP_IDENTITY_DB=str(provisioned["db_path"]),
        ACP_IDENTITY_TENANT=TENANT,
    )
    probe = None
    try:
        _wait_health(base)
        _seed_legacy_auto_row(bound_root, body=bound_body, project_id=ALPHA, task_id=bound_task)
        probe = _McpProcess(bound_root, base, api_key=alice.key)
        probe.initialize()
        fresh = probe.submit(dict(bound_body))
        assert fresh.get("isError") is not True, fresh
        data = _data(fresh)
        task_id = str((data.get("task_ref") or {}).get("task_id") or "")
        assert task_id and task_id != bound_task, data
        # The bound identity is key-less (non-idempotent), so it never claims
        # the legacy ``auto_`` namespace.
        assert data.get("idempotency_key_origin") == mcp.IDEMPOTENCY_KEY_ORIGIN_NONE
        assert data.get("idempotency_key") is None
        # The legacy row is untouched and still replayable by its own binding.
        conn = sqlite3.connect(bound_root / "acp_jobs.db")
        try:
            row = conn.execute(
                "SELECT task_id, migration_state FROM idempotency_records WHERE idempotency_key=?",
                (mcp.derive_idempotency_key(bound_body),),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert str(row[0]) == bound_task
        assert str(row[1]) == MIGRATION_STATE_VERIFIED
    finally:
        if probe is not None:
            probe.close()
        _stop(controller)


# ---------------------------------------------------------------------------
# 4. CommandIdentity projection used by the MCP entry
# ---------------------------------------------------------------------------


def test_mcp_command_identity_projects_the_bound_credential() -> None:
    from acp_services.contracts.access import Principal

    binding = mcp.StdioCredentialBinding(
        principal=Principal(
            principal_id="principal_b15_alice", kind="user", tenant_id=TENANT, roles=("member",)
        ),
        verified_project=ALPHA,
        key_id="key-abc",
        key_scope="workspace",
        actions=("read", "write", "control"),
    )
    identity = mcp.IdentityMapper(
        permission_scope=mcp.STDIO_MULTIUSER_SCOPE, credential=binding
    ).command_identity(
        mcp.WorkspaceContext(
            principal=binding.principal,
            workspace_id="ws",
            tenant_id=TENANT,
            project_id=ALPHA,
            permission_scope=mcp.STDIO_MULTIUSER_SCOPE,
            available_actions=("read", "write", "control"),
        )
    )
    assert identity.principal_id == "principal_b15_alice"
    assert identity.entry_class == EntryClass.MCP
    assert identity.permission_scope == PermissionScope.STDIO
    assert identity.key_id == "key-abc"
    assert identity.idempotency_scope() == resolve_scope_id("principal_b15_alice", ALPHA)
    assert identity.is_operator is False


def test_mcp_command_identity_is_the_local_operator_without_a_credential() -> None:
    identity = mcp.IdentityMapper().command_identity(
        mcp.WorkspaceContext(
            principal=LocalPrincipal.resolve(),
            workspace_id="ws",
            tenant_id="tenant-local",
            project_id=ALPHA,
            permission_scope=mcp.STDIO_PERMISSION_SCOPE,
        )
    )
    assert identity.principal_id == LOCAL_PRINCIPAL_ID
    assert identity.entry_class == EntryClass.MCP
    assert identity.permission_scope == PermissionScope.STDIO
    assert identity.is_operator is True


# ---------------------------------------------------------------------------
# 5. Prepare key versioning (M0-05 owns the Data DB migration)
# ---------------------------------------------------------------------------


def _prepare_request(**overrides: Any) -> Any:
    from acp_services.data.contracts import ResourceKind
    from acp_services.data.operations import PrepareRequest

    values: dict[str, Any] = {
        "job_id": "job-prep",
        "attempt": 1,
        "resource_id": "catalog:Confsearch",
        "resource_kind": ResourceKind.ARTIFACT,
        "resource_digest": "sha256:" + "a" * 64,
        "permission_scope": "local-single",
        "resource_generation": 1,
        "payload": {"purpose": "artifact", "artifact_id": "catalog"},
    }
    values.update(overrides)
    return PrepareRequest(**values)


def test_prepare_v1_row_is_read_by_the_v1_predicate_and_v2_rows_are_versioned(
    tmp_path: Path,
) -> None:
    from acp_services.data import operations as ops

    service = ops.PrepareOperationService(tmp_path / "prepare.db")
    try:
        legacy = service.enqueue(_prepare_request(client_idempotency_key="legacy-client-key"))
        assert legacy.identity_version == ops.LEGACY_PREPARE_IDENTITY_VERSION
        assert legacy.idempotency_key.startswith(ops.LEGACY_PREPARE_KEY_PREFIX)
        assert legacy.principal_id == "" and legacy.project_id == ""

        alice = service.enqueue(
            _prepare_request(
                principal_id="principal_b15_alice",
                project_id=ALPHA,
                client_idempotency_key="alice-key",
            )
        )
        bob = service.enqueue(
            _prepare_request(
                principal_id="principal_b15_bob",
                project_id=ALPHA,
                client_idempotency_key="bob-key",
            )
        )
        assert alice.identity_version == ops.PREPARE_IDENTITY_VERSION
        assert alice.idempotency_key.startswith(ops.PREPARE_KEY_PREFIX)
        assert (alice.principal_id, alice.project_id) == ("principal_b15_alice", ALPHA)
        assert bob.identity_version == ops.PREPARE_IDENTITY_VERSION
        assert alice.operation_id != bob.operation_id

        # Same verified identity + same client key + same content merges.
        again = service.enqueue(
            _prepare_request(
                principal_id="principal_b15_alice",
                project_id=ALPHA,
                client_idempotency_key="alice-key",
            )
        )
        assert again.operation_id == alice.operation_id

        # Two principals may share a client key without colliding (v2 index).
        shared_alice = service.enqueue(
            _prepare_request(
                principal_id="principal_b15_alice",
                project_id=ALPHA,
                client_idempotency_key="shared-client-key",
            )
        )
        shared_bob = service.enqueue(
            _prepare_request(
                principal_id="principal_b15_bob",
                project_id=ALPHA,
                client_idempotency_key="shared-client-key",
            )
        )
        assert shared_alice.operation_id != shared_bob.operation_id

        # A verified caller cannot adopt the unattributed legacy row.
        with pytest.raises(ops.PrepareOperationConflictError):
            service.enqueue(
                _prepare_request(
                    principal_id="principal_b15_alice",
                    project_id=ALPHA,
                    client_idempotency_key="legacy-client-key",
                )
            )

        # The explicit local operator may reuse the row it wrote.
        reused = service.enqueue(
            _prepare_request(
                principal_id=LOCAL_PRINCIPAL_ID,
                project_id="uncategorized",
                client_idempotency_key="legacy-client-key",
            )
        )
        assert reused.operation_id == legacy.operation_id

        # ``op_`` stays the operation-reference namespace.
        assert reused.operation_id.startswith("op_")
        assert all(record.idempotency_key.startswith(("prep_", "op_")) for record in service.list())
    finally:
        service.close()
