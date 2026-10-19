"""B09-1 (todo 48) — idempotent environment provisioning.

Exercises the Runtime ``ProvisionWorkspace`` use case
(``acp_services.runtime.provisioning``) over the Data ``ensure_namespace`` use
case (``acp_services.data.namespaces``) through injected Workspace/Data ports:

1. Data idempotency — repeating one ``EnsureNamespaceRequest`` returns the
   *same* namespace + binding and leaves exactly one persisted row; a reused key
   with a different identity is refused, never silently reused.
2. Runtime provisioning — the intent is consumed, the namespace ensured, and an
   explicit ``provisioned`` environment state + audit record committed; a replay
   changes nothing (single space, no duplicate audit).
3. Failure reconciliation — a mid-way failure commits a visible ``failed`` state
   with a typed ``error_code`` (never silent); a retry converges to a single
   namespace with no duplicates.
4. Zero system-account side effects + direction — static + behavioural guards:
   no SSH/subprocess/socket, Runtime touches only the injected ports, the
   Workspace side never imports Data (no reverse call).
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

from acp_services.data.namespaces import (
    EnsureNamespaceRequest,
    NamespaceConflictError,
    NamespaceKind,
    NamespaceState,
    NamespaceStore,
    NamespaceValidationError,
    namespace_id_for,
)
from acp_services.runtime import provisioning as prov

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PROVISIONING_SRC = _REPO_ROOT / "src" / "acp_services" / "runtime" / "provisioning.py"
_NAMESPACES_SRC = _REPO_ROOT / "src" / "acp_services" / "data" / "namespaces.py"
_WORKSPACE_ROOT = _REPO_ROOT / "src" / "acp_services" / "workspace"


# ====================================================================== #
# Fakes (injected ports)
# ====================================================================== #


class _IntentSource:
    """Read-only stand-in for the Workspace persistent intent source."""

    def __init__(self, *intents: prov.ProvisioningIntent) -> None:
        self._intents = list(intents)

    def open_intents(self) -> tuple[prov.ProvisioningIntent, ...]:
        return tuple(self._intents)


class _StateStore:
    """In-memory stand-in for the Workspace environment-state command port."""

    def __init__(self) -> None:
        self.states: dict[str, prov.EnvironmentStateRecord] = {}
        self.audits: list[prov.ProvisioningAuditEntry] = []
        self.calls: list[str] = []

    def get_state(self, intent_id: str) -> prov.EnvironmentStateRecord | None:
        self.calls.append("get_state")
        return self.states.get(intent_id)

    def commit_state(self, record: prov.EnvironmentStateRecord) -> prov.EnvironmentStateRecord:
        self.calls.append("commit_state")
        self.states[record.intent_id] = record
        return record

    def append_audit(self, entry: prov.ProvisioningAuditEntry) -> None:
        self.calls.append("append_audit")
        self.audits.append(entry)


class _NamespacePort:
    """Wrap the real store, optionally failing the first *fail_times* calls.

    A failure is raised *before* delegating, so the retry creates exactly one
    namespace (no partial/duplicate space).
    """

    def __init__(self, store: NamespaceStore, *, fail_times: int = 0) -> None:
        self._store = store
        self._fail_times = int(fail_times)
        self.calls = 0

    def ensure_namespace(self, request: EnsureNamespaceRequest):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("simulated data failure")
        return self._store.ensure_namespace(request)


def _intent(intent_id: str = "intent_1", **overrides) -> prov.ProvisioningIntent:
    base: dict = {
        "intent_id": intent_id,
        "principal_id": "alice",
        "tenant_id": "lab",
        "kind": prov.ProvisioningIntentKind.PERSONAL_SPACE,
        "project_id": "proj_alice",
        "namespace_key": "personal_space:alice",
        "root": "/data/runs",
        "relative_path": "lab/proj_alice/personal_alice",
    }
    base.update(overrides)
    return prov.ProvisioningIntent(**base)


def _request(**overrides) -> EnsureNamespaceRequest:
    base: dict = {
        "namespace_key": "personal_space:alice",
        "namespace_kind": NamespaceKind.PERSONAL_SPACE,
        "tenant_id": "lab",
        "project_id": "proj_alice",
        "principal_id": "alice",
        "root": "/data/runs",
        "relative_path": "lab/proj_alice/personal_alice",
    }
    base.update(overrides)
    return EnsureNamespaceRequest(**base)


def _service(tmp_path: Path, *, fail_times: int = 0, intents=()):
    store = NamespaceStore(tmp_path / "acp_jobs.db")
    namespaces = _NamespacePort(store, fail_times=fail_times)
    states = _StateStore()
    service = prov.ProvisioningService(
        intents=_IntentSource(*intents),
        namespaces=namespaces,
        states=states,
    )
    return service, store, namespaces, states


# ====================================================================== #
# 1. Data idempotency
# ====================================================================== #


def test_ports_satisfied_by_fakes_and_real_store(tmp_path: Path) -> None:
    store = NamespaceStore(tmp_path / "acp_jobs.db")
    assert isinstance(store, prov.NamespaceProvisionerPort)
    assert isinstance(_IntentSource(), prov.ProvisioningIntentSource)
    assert isinstance(_StateStore(), prov.EnvironmentStatePort)


def test_ensure_namespace_is_idempotent_single_space(tmp_path: Path) -> None:
    store = NamespaceStore(tmp_path / "acp_jobs.db")
    request = _request()
    first = store.ensure_namespace(request)
    second = store.ensure_namespace(_request())

    assert first.namespace_id == second.namespace_id == namespace_id_for(request.namespace_key)
    assert first.storage_binding == second.storage_binding
    assert first.state == NamespaceState.PROVISIONED
    assert store.count() == 1
    assert len(store.list()) == 1


def test_ensure_namespace_persists_state_and_binding(tmp_path: Path) -> None:
    db = tmp_path / "acp_jobs.db"
    store = NamespaceStore(db)
    record = store.ensure_namespace(_request())

    # Persisted, not in-memory: a fresh store over the same DB sees the row.
    reopened = NamespaceStore(db)
    assert reopened.count() == 1
    found = reopened.find_by_key("personal_space:alice")
    assert found is not None
    assert found.namespace_id == record.namespace_id
    assert found.storage_binding.binding_id == record.storage_binding.binding_id
    assert found.namespace_kind == NamespaceKind.PERSONAL_SPACE
    # human-readable relative path, no ids/timestamps (ANTI #15)
    assert record.storage_binding.relative_path == "lab/proj_alice/personal_alice"


def test_ensure_namespace_conflicting_identity_is_refused_not_reused(tmp_path: Path) -> None:
    store = NamespaceStore(tmp_path / "acp_jobs.db")
    store.ensure_namespace(_request())
    with pytest.raises(NamespaceConflictError):
        store.ensure_namespace(_request(relative_path="lab/other/personal_alice"))
    # The conflict did not create a second space.
    assert store.count() == 1


def test_ensure_namespace_validation(tmp_path: Path) -> None:
    store = NamespaceStore(tmp_path / "acp_jobs.db")
    with pytest.raises(NamespaceValidationError):
        _request(namespace_key="")
    with pytest.raises(NamespaceValidationError):
        _request(namespace_kind="bogus")
    with pytest.raises(NamespaceValidationError):
        _request(root="")
    with pytest.raises(NamespaceValidationError):
        _request(relative_path="")
    with pytest.raises(NamespaceValidationError):
        store.ensure_namespace("not-a-request")  # type: ignore[arg-type]


# ====================================================================== #
# 2. Runtime provisioning — happy path + replay
# ====================================================================== #


def test_provision_commits_state_and_audit(tmp_path: Path) -> None:
    service, store, _namespaces, states = _service(tmp_path, intents=(_intent(),))
    result = service.provision(_intent())

    assert result.ok and result.state == prov.ProvisioningState.PROVISIONED
    assert result.namespace_id == namespace_id_for("personal_space:alice")
    assert store.count() == 1
    committed = states.states["intent_1"]
    assert committed.state == prov.ProvisioningState.PROVISIONED
    assert committed.namespace_id == result.namespace_id
    assert len(states.audits) == 1
    assert states.audits[0].event == prov.ProvisioningState.PROVISIONED
    assert states.audits[0].namespace_id == result.namespace_id


def test_provision_replay_is_no_side_effect(tmp_path: Path) -> None:
    service, store, namespaces, states = _service(tmp_path, intents=(_intent(),))
    first = service.provision(_intent())
    audits_after_first = len(states.audits)
    calls_after_first = namespaces.calls

    second = service.provision(_intent())

    assert second.namespace_id == first.namespace_id
    assert second.reconciled is True
    assert store.count() == 1, "replay must not create a second space"
    # Re-invokes the idempotent Data call, but commits no new audit observation.
    assert namespaces.calls == calls_after_first + 1
    assert len(states.audits) == audits_after_first, "replay must not duplicate audit"
    assert states.states["intent_1"].state == prov.ProvisioningState.PROVISIONED


def test_provision_or_raise_succeeds(tmp_path: Path) -> None:
    service, _store, _namespaces, _states = _service(tmp_path)
    result = service.provision_or_raise(_intent())
    assert result.ok


# ====================================================================== #
# 3. Failure reconciliation (visible, never silent)
# ====================================================================== #


def test_failure_commits_visible_failed_state(tmp_path: Path) -> None:
    service, store, _namespaces, states = _service(tmp_path, fail_times=1)
    result = service.provision(_intent())

    assert not result.ok
    assert result.state == prov.ProvisioningState.FAILED
    assert result.error_code == "RuntimeError"
    assert "simulated data failure" in result.detail
    # The failure is committed + audited, never silent.
    assert states.states["intent_1"].state == prov.ProvisioningState.FAILED
    assert len(states.audits) == 1
    assert states.audits[0].event == prov.ProvisioningState.FAILED
    # No namespace was created by the failed attempt.
    assert store.count() == 0


def test_provision_or_raise_raises_failed_error(tmp_path: Path) -> None:
    service, _store, _namespaces, states = _service(tmp_path, fail_times=1)
    with pytest.raises(prov.ProvisioningFailedError) as excinfo:
        service.provision_or_raise(_intent())
    assert excinfo.value.error_code == "RuntimeError"
    assert states.states["intent_1"].state == prov.ProvisioningState.FAILED


def test_failure_then_retry_reconciles_without_duplicates(tmp_path: Path) -> None:
    service, store, _namespaces, states = _service(tmp_path, fail_times=1, intents=(_intent(),))
    # First reconcile pass: the Data call fails once -> visible failure.
    first = service.reconcile()
    assert first.failed == 1
    assert store.count() == 0
    assert states.states["intent_1"].state == prov.ProvisioningState.FAILED

    # Retry pass: converges, exactly one namespace, no duplicate default space.
    second = service.reconcile()
    assert second.provisioned == 1 and second.failed == 0
    assert store.count() == 1
    assert len(store.list(state=NamespaceState.PROVISIONED)) == 1
    assert states.states["intent_1"].state == prov.ProvisioningState.PROVISIONED
    assert [a.event for a in states.audits] == [
        prov.ProvisioningState.FAILED,
        prov.ProvisioningState.PROVISIONED,
    ]


def test_reconcile_counts_mixed_outcomes(tmp_path: Path) -> None:
    service, store, _namespaces, states = _service(
        tmp_path,
        intents=(
            _intent("intent_1"),
            _intent(
                "intent_2",
                namespace_key="personal_space:bob",
                project_id="proj_bob",
                relative_path="lab/proj_bob/personal_bob",
            ),
        ),
    )
    # Provision intent_1 already, so the reconcile pass reports it reconciled.
    service.provision(_intent("intent_1"))
    report = service.reconcile()

    assert report.total == 2
    assert report.reconciled == 1
    assert report.provisioned == 1
    assert report.failed == 0
    assert report.skipped == 0
    assert store.count() == 2


# ====================================================================== #
# 4a. Read-only registration has no side effect
# ====================================================================== #


def test_registration_view_is_read_only_no_side_effects(tmp_path: Path) -> None:
    service, store, namespaces, states = _service(tmp_path, intents=(_intent(),))
    view = service.registration_view()

    assert len(view) == 1
    assert view[0]["intent_id"] == "intent_1"
    assert view[0]["namespace_key"] == "personal_space:alice"
    # No Data call, no state commit, no namespace created.
    assert namespaces.calls == 0
    assert states.calls == []
    assert store.count() == 0


def test_service_touches_only_injected_ports(tmp_path: Path) -> None:
    service, _store, _namespaces, states = _service(tmp_path, intents=(_intent(),))
    service.reconcile()
    assert set(states.calls) <= {"get_state", "commit_state", "append_audit"}


# ====================================================================== #
# 4b. Zero system-account side effects (static guard)
# ====================================================================== #

_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {"subprocess", "socket", "paramiko", "pty", "shlex", "pwd", "grp", "ctypes"}
)
_FORBIDDEN_CODE_TOKENS = (
    "subprocess",
    "paramiko",
    "os.system",
    "popen",
    "popen_ssh",
    "useradd",
    "adduser",
    "chown",
    "chmod",
    "ssh",
    "socket",
)


def _import_roots(source: str) -> set[str]:
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _code_only(source: str) -> str:
    """Code tokens with STRING/COMMENT stripped (docstrings never trip the guard)."""
    tokens: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE):
            continue
        tokens.append(tok.string)
    return " ".join(tokens)


@pytest.mark.parametrize("path", [_PROVISIONING_SRC, _NAMESPACES_SRC])
def test_no_system_account_or_network_side_effects(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    assert not (_import_roots(source) & _FORBIDDEN_IMPORT_ROOTS), "forbidden import"
    code = _code_only(source)
    for token in _FORBIDDEN_CODE_TOKENS:
        assert token not in code, f"{path.name} references {token!r} in code"


# ====================================================================== #
# 4c. Direction guards — Workspace intent -> Runtime -> Data
# ====================================================================== #


def test_provisioning_does_not_import_data_store_or_workspace() -> None:
    source = _PROVISIONING_SRC.read_text(encoding="utf-8")
    roots = _import_roots(source)
    assert "sqlite3" not in roots, "Runtime must not touch the DB directly"
    assert not any(root.startswith("acp_services.workspace") for root in roots), (
        "Runtime must consume Workspace via a port, not import its implementation"
    )
    # Runtime -> Data: it imports and calls the Data use case.
    assert "from acp_services.data.namespaces import" in source
    assert "self._namespaces.ensure_namespace(" in source


def test_workspace_never_imports_data() -> None:
    """No reverse Workspace -> Data call (main plan §22.2 direction rule)."""
    offenders: dict[str, list[str]] = {}
    for path in sorted(_WORKSPACE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits = [
            (node.module or "")
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("acp_services.data")
        ]
        hits += [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name.startswith("acp_services.data")
        ]
        if hits:
            offenders[str(path.relative_to(_REPO_ROOT))] = hits
    assert not offenders, f"Workspace imports Data (reverse call): {offenders}"
