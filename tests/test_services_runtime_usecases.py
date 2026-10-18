"""Runtime use-case extraction gate (B05-1 / todo 25).

Covers the acceptance clauses for the ``acp_services.runtime.commands``
extraction:

1. **Single execution owner** — each of the seven commands (submit / pause /
   unpause / cancel / continue / rerun / edit) plus ``purge`` and the CLI
   completed-job registration forwards to its injected port **exactly once** and
   returns that call's result untouched. No second state machine, no store
   access in the Runtime layer.
2. **API entry equivalence** — ``src/acp/api/v1_routes.py`` submit/control
   endpoints reach the live manager *only* through the Runtime use cases and
   return identical status/response semantics.
3. **CLI entry equivalence** — the CLI submit (``--register``) entry goes
   through the single Runtime boundary.
4. **Negative / bypass gate** — a branch that bypasses Runtime and calls the
   store directly (or a manager control verb) is detected and FAILS the
   architecture assertion.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_SERVICES_RUNTIME = (
    Path(__file__).resolve().parents[1] / "src" / "acp_services" / "runtime" / "commands.py"
)
_V1_ROUTES = Path(__file__).resolve().parents[1] / "src" / "acp" / "api" / "v1_routes.py"
_CLI = Path(__file__).resolve().parents[1] / "src" / "acp" / "cli.py"

# Control verbs that a submit/control entry file must never call directly.
_CONTROL_VERBS = frozenset(
    {
        "submit",
        "pause_job",
        "unpause_job",
        "cancel",
        "continue_job",
        "rerun_job",
        "edit_recalculate",
        "purge_jobs",
    }
)
# JobStore mutation entry points a bypassing branch would reach for.
_STORE_MUTATIONS = frozenset(
    {
        "transition",
        "create",
        "update",
        "update_progress",
        "update_execution_identity",
        "requeue_with_spec",
        "purge_cascade",
    }
)


# ---------------------------------------------------------------------------
# Bypass scanner (AST): entry files must go through Runtime, never a store or a
# raw manager control verb.
# ---------------------------------------------------------------------------


def _looks_like_store(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return "store" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "store" in node.attr.lower()
    return False


def _is_runtime_boundary(node: ast.AST) -> bool:
    """True when *node* is the Runtime command surface (legitimate verb owner)."""
    if isinstance(node, ast.Call):
        func = node.func
        return isinstance(func, ast.Name) and func.id == "_commands"
    if isinstance(node, ast.Name):
        return node.id in {"commands", "runtime", "runtime_commands"}
    if isinstance(node, ast.Attribute):
        return node.attr in {"commands", "runtime", "runtime_commands"}
    return False


def scan_for_runtime_bypass(source: str) -> list[str]:
    """Return descriptors of direct control/store access (empty == clean).

    A call whose verb is a control verb or a store mutation is a bypass unless
    it is issued on the Runtime command surface itself (``_commands(request)``).
    """
    violations: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        if _is_runtime_boundary(node.func.value):
            continue
        if attr in _CONTROL_VERBS:
            violations.append(f"direct control call .{attr}(")
        elif attr in _STORE_MUTATIONS and _looks_like_store(node.func.value):
            violations.append(f"direct store mutation .{attr}(")
    return violations


# ---------------------------------------------------------------------------
# Fake injected ports (single-owner forwarding)
# ---------------------------------------------------------------------------

_SENTINEL = object()


class _ControlSpy:
    def __init__(self, result: object = _SENTINEL) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.result = result

    def _record(self, name: str, *args: object, **kwargs: object) -> object:
        self.calls.append((name, args, kwargs))
        return self.result

    def pause_job(self, job_id: str) -> object:
        return self._record("pause_job", job_id)

    def unpause_job(self, job_id: str) -> object:
        return self._record("unpause_job", job_id)

    def cancel(self, job_id: str) -> object:
        return self._record("cancel", job_id)

    def continue_job(self, job_id: str, *, target_node: str | None = None) -> object:
        return self._record("continue_job", job_id, target_node=target_node)

    def rerun_job(self, job_id: str, project_id: str | None = None) -> object:
        return self._record("rerun_job", job_id, project_id=project_id)

    def purge_jobs(self, **kwargs: object) -> object:
        return self._record("purge_jobs", **kwargs)


class _SubmissionSpy:
    def __init__(self, result: object = _SENTINEL) -> None:
        self.calls: list[tuple[object, str | None]] = []
        self.result = result

    def submit(self, spec: object, *, group_id: str | None = None) -> object:
        self.calls.append((spec, group_id))
        return self.result


class _EditSpy:
    def __init__(self, result: object = _SENTINEL) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.result = result

    def edit_recalculate(self, job_id: str, **kwargs: object) -> object:
        self.calls.append((job_id, kwargs))
        return self.result


class _RegistrationSpy:
    def __init__(self, result: object = _SENTINEL) -> None:
        self.calls: list[dict[str, object]] = []
        self.result = result

    def register_completed_job(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.result


def _runtime(
    *,
    control: _ControlSpy | None = None,
    submission: _SubmissionSpy | None = None,
    edit: _EditSpy | None = None,
    registration: _RegistrationSpy | None = None,
):
    from acp_services.runtime.commands import RuntimeCommands

    return RuntimeCommands(
        control=control or _ControlSpy(),
        submission=submission or _SubmissionSpy(),
        edit=edit or _EditSpy(),
        registration=registration or _RegistrationSpy(),
    )


# ---------------------------------------------------------------------------
# (1) single-owner forwarding — one port call per use case
# ---------------------------------------------------------------------------


def test_submit_forwards_once_to_submission_port() -> None:
    sub, ctrl, edit, reg = _SubmissionSpy(), _ControlSpy(), _EditSpy(), _RegistrationSpy()
    cmds = _runtime(control=ctrl, submission=sub, edit=edit, registration=reg)
    spec = object()

    assert cmds.submit(spec, group_id="g1") is _SENTINEL
    assert sub.calls == [(spec, "g1")]
    assert not ctrl.calls and not edit.calls and not reg.calls


def test_pause_forwards_once_to_control_port() -> None:
    ctrl, sub, edit, reg = _ControlSpy(), _SubmissionSpy(), _EditSpy(), _RegistrationSpy()
    cmds = _runtime(control=ctrl, submission=sub, edit=edit, registration=reg)

    assert cmds.pause("j1") is _SENTINEL
    assert ctrl.calls == [("pause_job", ("j1",), {})]
    assert not sub.calls and not edit.calls and not reg.calls


def test_unpause_forwards_once_to_control_port() -> None:
    ctrl = _ControlSpy()
    cmds = _runtime(control=ctrl)

    assert cmds.unpause("j1") is _SENTINEL
    assert ctrl.calls == [("unpause_job", ("j1",), {})]


def test_cancel_forwards_once_to_control_port() -> None:
    ctrl = _ControlSpy()
    cmds = _runtime(control=ctrl)

    assert cmds.cancel("j1") is _SENTINEL
    assert ctrl.calls == [("cancel", ("j1",), {})]


def test_continue_forwards_once_to_control_port() -> None:
    ctrl = _ControlSpy()
    cmds = _runtime(control=ctrl)

    assert cmds.continue_job("j1", target_node="comp-01") is _SENTINEL
    assert ctrl.calls == [("continue_job", ("j1",), {"target_node": "comp-01"})]


def test_rerun_forwards_once_to_control_port() -> None:
    ctrl = _ControlSpy()
    cmds = _runtime(control=ctrl)

    assert cmds.rerun("j1", project_id="proj") is _SENTINEL
    assert ctrl.calls == [("rerun_job", ("j1",), {"project_id": "proj"})]


def test_edit_recalculate_forwards_once_to_edit_port() -> None:
    edit, ctrl = _EditSpy(), _ControlSpy()
    cmds = _runtime(control=ctrl, edit=edit)
    spec = object()

    result = cmds.edit_recalculate(
        "j1",
        mode="in_place",
        new_spec=spec,
        expected_source_revision="rev",
        request_id="req-1",
        payload_hash="hash",
        payload_json="{}",
        diff_summary=[],
    )

    assert result is _SENTINEL
    assert len(edit.calls) == 1
    job_id, kwargs = edit.calls[0]
    assert job_id == "j1"
    assert kwargs["mode"] == "in_place"
    assert kwargs["new_spec"] is spec
    assert kwargs["request_id"] == "req-1"
    assert not ctrl.calls


def test_purge_forwards_once_to_control_port() -> None:
    ctrl = _ControlSpy()
    cmds = _runtime(control=ctrl)

    assert cmds.purge(job_ids=["j1", "j2"], status="failed") is _SENTINEL
    assert ctrl.calls == [
        ("purge_jobs", (), {"job_ids": ["j1", "j2"], "status": "failed"})
    ]


def test_register_completed_job_forwards_once_to_registration_port() -> None:
    reg = _RegistrationSpy()
    cmds = _runtime(registration=reg)

    assert cmds.register_completed_job(workflow="XtbPathSearch") is _SENTINEL
    assert reg.calls == [{"workflow": "XtbPathSearch"}]


def test_runtime_commands_is_a_frozen_port_bundle() -> None:
    import dataclasses

    from acp_services.runtime.commands import RuntimeCommands

    assert dataclasses.is_dataclass(RuntimeCommands)
    assert RuntimeCommands.__dataclass_params__.frozen
    assert {f.name for f in dataclasses.fields(RuntimeCommands)} == {
        "control",
        "submission",
        "edit",
        "registration",
    }


def test_runtime_module_never_touches_a_store() -> None:
    source = _SERVICES_RUNTIME.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in _STORE_MUTATIONS, f"store write .{node.func.attr}("
    assert "JobStore" not in source
    assert "store" not in {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


# ---------------------------------------------------------------------------
# Composition root: build_runtime_commands injects adapters over the manager
# ---------------------------------------------------------------------------


class _StubManager:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def pause_job(self, job_id: str) -> str:
        self.calls.append(("pause_job", job_id))
        return "paused"

    def submit(self, spec: object, group_id: str | None = None) -> str:
        self.calls.append(("submit", spec, group_id))
        return "submitted"

    def edit_recalculate(self, job_id: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("edit_recalculate", job_id, kwargs))
        return {"operation": "in_place"}


def test_build_runtime_commands_adapts_live_manager() -> None:
    from acp.services_bootstrap import build_runtime_commands

    manager = _StubManager()
    cmds = build_runtime_commands(manager)

    assert cmds.pause("j1") == "paused"
    assert cmds.submit("spec", group_id="g") == "submitted"
    assert cmds.edit_recalculate("j1", mode="in_place") == {"operation": "in_place"}
    assert manager.calls == [
        ("pause_job", "j1"),
        ("submit", "spec", "g"),
        ("edit_recalculate", "j1", {"mode": "in_place"}),
    ]


def test_build_runtime_commands_without_manager_fails_loudly() -> None:
    from acp.services_bootstrap import build_runtime_commands

    cmds = build_runtime_commands(None)
    with pytest.raises(RuntimeError, match="JobManager"):
        cmds.pause("j1")
    with pytest.raises(RuntimeError, match="JobManager"):
        cmds.submit(object())


def test_build_runtime_commands_registration_wired_without_manager(monkeypatch) -> None:
    import acp.scheduler.registration as registration
    from acp.services_bootstrap import build_runtime_commands

    sentinel = object()
    monkeypatch.setattr(registration, "register_completed_cli_job", lambda **kwargs: sentinel)

    cmds = build_runtime_commands(None)
    assert cmds.register_completed_job(workflow="XtbPathSearch") is sentinel


# ---------------------------------------------------------------------------
# (2) API entry equivalence — v1_routes reaches the manager only via Runtime
# ---------------------------------------------------------------------------


def _fake_record(job_id: str = "job-1"):
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    return JobRecord(
        id=job_id,
        spec=JobSpec(workflow="fake", name=job_id, input={}, method={}),
        status=JobStatus.QUEUED,
        work_dir=f"/tmp/{job_id}",
        project_id="uncategorized",
    )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


def _stub_manager_method(manager, name: str, result_factory):
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    original = getattr(manager, name)

    def _stub(*args: object, **kwargs: object):
        calls.append((args, kwargs))
        return result_factory(*args, **kwargs)

    setattr(manager, name, _stub)
    return calls, original


@pytest.mark.parametrize(
    ("method", "path", "verb"),
    [
        ("pause_job", "pause", "pause"),
        ("unpause_job", "unpause", "unpause"),
        ("cancel", "cancel", "cancel"),
    ],
)
def test_api_state_endpoints_route_through_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str, path: str, verb: str
) -> None:
    with _client(tmp_path, monkeypatch) as client:
        manager = client.app.state.job_manager
        calls, original = _stub_manager_method(
            manager, method, lambda job_id, *a, **k: _fake_record(job_id)
        )
        try:
            response = client.post(f"/api/v1/jobs/j1/{path}")
            assert response.status_code == 200
            assert response.json()["id"] == "j1"
            assert len(calls) == 1 and calls[0][0][0] == "j1"
        finally:
            setattr(manager, method, original)


def test_api_continue_routes_through_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch) as client:
        manager = client.app.state.job_manager
        calls, original = _stub_manager_method(
            manager,
            "continue_job",
            lambda job_id, *a, **k: _fake_record(job_id),
        )
        try:
            response = client.post("/api/v1/jobs/j1/continue")
            assert response.status_code == 200
            assert response.json()["id"] == "j1"
            assert len(calls) == 1
            assert calls[0][1].get("target_node") is None
        finally:
            setattr(manager, "continue_job", original)


def test_api_rerun_routes_through_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        manager = client.app.state.job_manager
        calls, original = _stub_manager_method(
            manager,
            "rerun_job",
            lambda job_id, *a, **k: _fake_record(job_id),
        )
        try:
            response = client.post("/api/v1/jobs/j1/rerun")
            assert response.status_code == 200
            assert response.json()["id"] == "j1"
            assert len(calls) == 1
        finally:
            setattr(manager, "rerun_job", original)


def test_api_submit_routes_through_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(tmp_path, monkeypatch) as client:
        manager = client.app.state.job_manager
        calls, original = _stub_manager_method(
            manager,
            "submit",
            lambda spec, *a, **k: _fake_record("submitted-1"),
        )
        try:
            response = client.post(
                "/api/v1/jobs",
                json={
                    "workflow": "fake",
                    "name": "demo",
                    "input": {"source": "CCO"},
                    "method": {"protocol": "ext"},
                },
            )
            assert response.status_code == 201
            assert response.json()["job_id"] == "submitted-1"
            assert len(calls) == 1
        finally:
            setattr(manager, "submit", original)


def test_api_purge_routes_through_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        manager = client.app.state.job_manager
        calls, original = _stub_manager_method(
            manager,
            "purge_jobs",
            lambda *a, **k: [],
        )
        try:
            response = client.post("/api/v1/jobs/purge", json={"job_ids": ["j1"]})
            assert response.status_code == 200
            assert response.json()["results"] == []
            assert len(calls) == 1
            assert calls[0][0][0] == ["j1"]
        finally:
            setattr(manager, "purge_jobs", original)


def test_api_edit_endpoint_calls_runtime_use_case() -> None:
    source = _V1_ROUTES.read_text(encoding="utf-8")
    assert "_commands(request).edit_recalculate(" in source
    assert "manager.edit_recalculate(" not in source


# ---------------------------------------------------------------------------
# (3) CLI entry equivalence — single Runtime boundary
# ---------------------------------------------------------------------------


def test_cli_job_runtime_boundary_forwards_registration(monkeypatch) -> None:
    import acp.scheduler.registration as registration
    from acp import cli
    from acp_services.runtime.commands import RuntimeCommands

    sentinel = object()
    monkeypatch.setattr(registration, "register_completed_cli_job", lambda **kwargs: sentinel)

    commands = cli.job_runtime_commands()
    assert isinstance(commands, RuntimeCommands)
    assert commands.register_completed_job(workflow="XtbPathSearch") is sentinel


def test_cli_register_paths_use_runtime_boundary() -> None:
    source = _CLI.read_text(encoding="utf-8")
    assert source.count("job_runtime_commands().register_completed_job(") == 2
    assert "register_completed_cli_job" not in source


# ---------------------------------------------------------------------------
# (4) Negative: a bypassing branch must FAIL the architecture assertion
# ---------------------------------------------------------------------------


def test_entry_files_do_not_bypass_runtime() -> None:
    for path in (_V1_ROUTES, _CLI):
        violations = scan_for_runtime_bypass(path.read_text(encoding="utf-8"))
        assert violations == [], f"{path.name} bypasses Runtime: {violations}"


def test_scanner_flags_injected_direct_store_branch(tmp_path: Path) -> None:
    original = _V1_ROUTES.read_text(encoding="utf-8")
    mutated = original.replace(
        "def _commands(request: Request)",
        "def _injected(store):\n"
        "    store.transition('j1', expected_revision=1)\n\n\n"
        "def _commands(request: Request)",
        1,
    )
    assert mutated != original, "mutation did not apply"
    violations = scan_for_runtime_bypass(mutated)
    assert any("transition" in item for item in violations)
    assert scan_for_runtime_bypass(original) == []


def test_scanner_flags_injected_direct_manager_control_branch(tmp_path: Path) -> None:
    original = _V1_ROUTES.read_text(encoding="utf-8")
    mutated = original.replace(
        "def _commands(request: Request)",
        "def _injected(manager):\n"
        "    manager.cancel('j1')\n\n\n"
        "def _commands(request: Request)",
        1,
    )
    assert mutated != original, "mutation did not apply"
    violations = scan_for_runtime_bypass(mutated)
    assert any("cancel" in item for item in violations)
