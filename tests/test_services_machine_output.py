"""Machine-output contract gate (B08-1 / todo 42).

Proves the four acceptance clauses of the unified machine interface:

1. **Envelope + error schema** — :class:`MachineResult` / :class:`ErrorObject`
   carry operation identity, status, stable ``code``, structured ``params``,
   safe ``message``, ``retryable`` and a ``diagnostic_ref`` for B08-6 / todo 24.
2. **Exit-code table** — ``0/1/2/130`` keep their legacy meaning; the typed
   categories map to stable new integers.
3. **Dry-run zero side effects** — ``--dry-run`` never calls the workflow
   handler nor ``cccp.calculation.run_*``; the envelope asserts
   ``executed=False`` / ``metering_consumed=False`` / ``transfers_attempted=False``.
4. **Old text-mode compatibility** — without the new flags stdout is byte-for-byte
   the legacy text; the additive JSON mode puts text on stderr.

Plus a secret/path-leakage redaction check and a real ``python -m acp`` smoke.

The science is never executed here (QC binaries are absent); the dry-run path
makes that a *tested* property, not an assumption.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from acp import cli
from acp_services.entrypoints import machine_output as mo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CLI_PATH = _REPO_ROOT / "src" / "acp" / "cli.py"
_MACHINE_OUTPUT_PATH = _REPO_ROOT / "src" / "acp_services" / "entrypoints" / "machine_output.py"

_XYZ_TEXT = "3\nwater\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\nH 0.0 0.93 -0.24\n"

#: Legacy exit codes whose meaning is frozen (root AGENTS.md / cli.py today).
_LEGACY_EXIT = {
    mo.ExitCode.OK: 0,
    mo.ExitCode.FAILED: 1,
    mo.ExitCode.USAGE: 2,
    mo.ExitCode.INTERRUPTED: 130,
}


class _RunSpy:
    """Records every call; the dry-run gate asserts the list stays empty."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - never expected
        self.calls.append((args, kwargs))
        raise AssertionError("execution entrypoint reached during dry-run")

    def assert_not_called(self) -> None:
        assert self.calls == [], f"dry-run must not execute; calls={self.calls!r}"


def _assert_no_execution(*spies: _RunSpy) -> None:
    for spy in spies:
        spy.assert_not_called()


def _install_execution_spies(monkeypatch: pytest.MonkeyPatch) -> tuple[_RunSpy, _RunSpy, _RunSpy]:
    """Replace every execution node on the dry-run path with a loud spy."""
    import cccp.calculation as cccp_calculation
    import cccp.calculation.tasks.singlepoint as task_module

    handler = _RunSpy()
    workflow = _RunSpy()
    task = _RunSpy()
    monkeypatch.setattr(cli, "_handle_singlepoint", handler)
    monkeypatch.setattr("acp.workflows.simple.run_singlepoint", workflow, raising=False)
    monkeypatch.setattr(task_module, "run_singlepoint", task)
    monkeypatch.setattr(cccp_calculation, "run_singlepoint", task, raising=False)
    return handler, workflow, task


_MISSING = object()


@pytest.fixture(autouse=True)
def _restore_execution_hooks():
    """Force-restore the execution hooks after each test.

    ``_install_execution_spies`` patches ``cccp.calculation.run_singlepoint``
    (and the task-module symbol).  Depending on teardown order a later test can
    observe the spy, which breaks unrelated batch tests when this file runs
    first; restoring the saved originals here makes the order independent.
    """
    import cccp.calculation as cccp_calculation
    import cccp.calculation.tasks.singlepoint as task_module

    saved_cccp = getattr(cccp_calculation, "run_singlepoint", _MISSING)
    saved_task = task_module.run_singlepoint
    yield
    task_module.run_singlepoint = saved_task
    if saved_cccp is _MISSING:
        try:
            delattr(cccp_calculation, "run_singlepoint")
        except AttributeError:
            pass
    else:
        cccp_calculation.run_singlepoint = saved_cccp


def _write_xyz(tmp_path: Path) -> Path:
    path = tmp_path / "water.xyz"
    path.write_text(_XYZ_TEXT, encoding="utf-8")
    return path


def _capture_main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> tuple[int, str, str]:
    import contextlib
    import io

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------- #
# 1. Envelope + error schema
# --------------------------------------------------------------------------- #


def test_error_object_schema_for_every_code() -> None:
    for code in mo.ErrorCode.ALL:
        spec = mo.error_spec(code)
        obj = mo.error_object(code, "boom", params={"field": "x"}, diagnostic_ref="trace://abc")
        assert obj.code == code
        assert obj.category == spec.category
        assert obj.exit_code == spec.exit_code
        assert obj.retryable is spec.retryable
        assert obj.diagnostic_ref == "trace://abc"
        payload = obj.to_dict()
        assert set(payload) == {
            "code",
            "category",
            "message",
            "retryable",
            "params",
            "diagnostic_ref",
        }
        assert isinstance(payload["retryable"], bool)


def test_machine_result_invariants() -> None:
    ok = mo.MachineResult(operation="singlepoint", status="ok")
    assert ok.error is None and ok.exit_code == mo.ExitCode.OK
    err = mo.error_object(mo.ErrorCode.BACKEND_UNAVAILABLE, "missing orca")
    failed = mo.MachineResult(operation="singlepoint", status="error", exit_code=3, error=err)
    assert failed.to_dict()["error"]["code"] == mo.ErrorCode.BACKEND_UNAVAILABLE

    with pytest.raises(ValueError):
        mo.MachineResult(operation="x", status="ok", error=err)
    with pytest.raises(ValueError):
        mo.MachineResult(operation="x", status="error")
    with pytest.raises(ValueError):
        mo.MachineResult(operation="x", status="bogus")


def test_error_from_exception_classification() -> None:
    assert mo.error_from_exception(KeyboardInterrupt()).code == mo.ErrorCode.INTERRUPTED
    assert mo.error_from_exception(FileNotFoundError("x")).code == mo.ErrorCode.INPUT_INVALID
    assert mo.error_from_exception(RuntimeError("x")).code == mo.ErrorCode.INTERNAL_ERROR


# --------------------------------------------------------------------------- #
# 2. Exit-code table
# --------------------------------------------------------------------------- #


def test_exit_code_table_and_legacy_compatibility() -> None:
    assert (mo.ExitCode.OK, mo.ExitCode.FAILED, mo.ExitCode.USAGE, mo.ExitCode.INTERRUPTED) == (
        0,
        1,
        2,
        130,
    )
    assert (
        len(
            {
                mo.ExitCode.UNAVAILABLE,
                mo.ExitCode.CONFLICT,
                mo.ExitCode.DENIED,
                mo.ExitCode.INTERNAL,
            }
        )
        == 4
    )
    assert mo.ExitCode.UNAVAILABLE not in _LEGACY_EXIT

    assert mo.exit_code_for(mo.ErrorCode.USAGE_ERROR) == 2
    assert mo.exit_code_for(mo.ErrorCode.INPUT_INVALID) == 2
    assert mo.exit_code_for(mo.ErrorCode.UNSUPPORTED_CAPABILITY) == 2
    assert mo.exit_code_for(mo.ErrorCode.BACKEND_UNAVAILABLE) == 3
    assert mo.exit_code_for(mo.ErrorCode.RESOURCE_NOT_READY) == 3
    assert mo.exit_code_for(mo.ErrorCode.REVISION_CONFLICT) == 4
    assert mo.exit_code_for(mo.ErrorCode.ACCESS_DENIED) == 5
    assert mo.exit_code_for(mo.ErrorCode.INTERNAL_ERROR) == 6
    assert mo.exit_code_for(mo.ErrorCode.NOT_CONVERGED) == 1
    assert mo.exit_code_for(mo.ErrorCode.INTERRUPTED) == 130
    assert mo.exit_code_for("totally-unknown") == 1


def test_catalog_is_total_and_categories_consistent() -> None:
    assert set(mo.ERROR_CATALOG) == set(mo.ErrorCode.ALL)
    categories = {spec.category for spec in mo.ERROR_CATALOG.values()}
    assert categories <= mo.ErrorCategory.ALL
    for spec in mo.ERROR_CATALOG.values():
        assert spec.exit_code == _category_exit_ref()[spec.category]


def _category_exit_ref() -> dict[str, int]:
    return {
        mo.ErrorCategory.OK: mo.ExitCode.OK,
        mo.ErrorCategory.FAILED: mo.ExitCode.FAILED,
        mo.ErrorCategory.USAGE: mo.ExitCode.USAGE,
        mo.ErrorCategory.UNAVAILABLE: mo.ExitCode.UNAVAILABLE,
        mo.ErrorCategory.CONFLICT: mo.ExitCode.CONFLICT,
        mo.ErrorCategory.DENIED: mo.ExitCode.DENIED,
        mo.ErrorCategory.INTERNAL: mo.ExitCode.INTERNAL,
        mo.ErrorCategory.INTERRUPTED: mo.ExitCode.INTERRUPTED,
    }


def test_retryable_categories() -> None:
    for code in (
        mo.ErrorCode.BACKEND_UNAVAILABLE,
        mo.ErrorCode.RESOURCE_NOT_READY,
        mo.ErrorCode.REVISION_CONFLICT,
    ):
        assert mo.error_spec(code).retryable is True
    for code in (
        mo.ErrorCode.INPUT_INVALID,
        mo.ErrorCode.ACCESS_DENIED,
        mo.ErrorCode.NOT_CONVERGED,
    ):
        assert mo.error_spec(code).retryable is False


# --------------------------------------------------------------------------- #
# 3. Redaction — no host path or secret may leak
# --------------------------------------------------------------------------- #


def test_redaction_removes_paths_and_secrets() -> None:
    message = "read /home/alice/secret/run.log failed; token=abcDEF123456 and Bearer eyJhbGci.zzz"
    cleaned = mo.redact(message)
    assert "/home/alice" not in cleaned
    assert "abcDEF123456" not in cleaned
    assert "eyJhbGci" not in cleaned
    assert mo.REDACTED_PATH in cleaned


def test_redaction_keeps_lowercase_hex_identity() -> None:
    identity = "v2:3f2a1b9c8d7e6f5a4b3c2d1e0f9a8b7c"
    assert mo.redact(identity) == identity


# 2026-10-30 guidance batch: the three frozen pes2ts payload ids are published
# contract identifiers (they name the request schema an agent must submit);
# the opaque net must not rewrite them inside guidance bodies.
def test_redaction_keeps_published_pes2ts_wire_identifiers() -> None:
    for identifier in (
        "pes2ts_xtb_path_request_v1",
        "pes2ts_orca_gradient_request_v1",
        "pes2ts_xtb_constrained_opt_request_v1",
    ):
        assert mo.redact(identifier) == identifier, identifier
        assert mo.redact(f"consume {identifier} payload") == f"consume {identifier} payload"


# M3-01b (F5): the published platform identifiers must survive so RPH can
# attribute a submit.  The net keeps its named namespaces; a random opaque run
# is still redacted.
_PROJECT_ID = "6f1c2a9e-4b3d-4f80-9a11-2c7d5e8b0f42"
_APPROVAL_REF = "ap_" + "d" * 32
_RESERVATION_ID = "rsv_" + "e" * 32
_SESSION_ID = "session_" + "f" * 32


def test_redaction_keeps_published_platform_identifiers() -> None:
    for identifier in (_PROJECT_ID, _APPROVAL_REF, _RESERVATION_ID, _SESSION_ID):
        assert mo.redact(identifier) == identifier, identifier
        assert mo.redact(f"project {identifier} approved") == f"project {identifier} approved"


def test_redaction_keeps_identifiers_inside_a_result_payload() -> None:
    payload = {
        "project_id": _PROJECT_ID,
        "approval_ref": _APPROVAL_REF,
        "reservation_id": _RESERVATION_ID,
        "task_ref": {"task_id": "20261027_001_fake", "attempt": 1},
    }
    cleaned = mo.redact_value(payload)
    assert cleaned == payload
    serialized = json.dumps(cleaned)
    assert "<redacted" not in serialized


def test_redaction_still_hides_random_opaque_runs() -> None:
    secret = "Zx9Qv2LmN8pR4sT6uW0yB3cD5eF7gH1j"
    cleaned = mo.redact(f"value {secret}")
    assert secret not in cleaned
    assert mo.REDACTED_SECRET in cleaned


# M4-01/F8: the bounded-wait cursor is a **server-issued opaque handle**, not a
# credential.  The envelope must carry it back verbatim (a client that cannot
# replay it cannot continue the wait).  The exemption is explicit (a declared
# field name) *and* shape-checked (the published cursor encoding) — never a
# general "long string" exemption.
def _wait_cursor() -> str:
    from acp_services.runtime.research_service import encode_wait_cursor

    return encode_wait_cursor(
        snapshot_revision="sha256:" + "a" * 64,
        project_id=_PROJECT_ID,
        issued_at=1_790_000_000.0,
    )


def test_redaction_keeps_a_server_issued_wait_cursor() -> None:
    cursor = _wait_cursor()
    assert mo.is_opaque_wait_cursor(cursor) is True
    payload = {"events": [], "cursor": cursor, "snapshot_revision": 3, "timed_out": True}
    cleaned = mo.redact_value(payload)
    assert cleaned == payload
    envelope = mo.MachineResult(operation="acp_research_wait_v1", status="ok", data=payload)
    serialized = json.dumps(envelope.to_dict())
    assert cursor in serialized
    assert "<redacted" not in serialized
    # Both frames agree: the text frame is the same envelope, not a second one.
    assert cursor in envelope.to_json()


def test_redaction_still_hides_a_credential_under_the_cursor_field() -> None:
    import base64

    for credential in (
        "Bearer " + "a" * 24,
        "Zx9Qv2LmN8pR4sT6uW0yB3cD5eF7gH1j",
        "sk-" + "b" * 40,
        base64.urlsafe_b64encode(b'{"api_key":"live-secret-value"}').decode("ascii"),
    ):
        cleaned = mo.redact_value({"cursor": credential})["cursor"]
        assert cleaned != credential, credential
        assert "<redacted" in cleaned, credential
    # A declared field whose value is not the published encoding is not exempt.
    assert mo.is_opaque_wait_cursor("cursor-1") is False
    assert mo.redact_value({"cursor": "cursor-1"})["cursor"] == "cursor-1"


def test_redaction_does_not_exempt_an_undeclared_field() -> None:
    cursor = _wait_cursor()
    cleaned = mo.redact_value({"continuation": cursor, "cursor": cursor})
    assert cleaned["cursor"] == cursor
    assert cleaned["continuation"] != cursor
    assert mo.REDACTED_SECRET in cleaned["continuation"]


def test_redaction_keeps_the_credential_path_intact() -> None:
    # A real credential is still caught by the assignment/bearer nets even when
    # it sits next to a preserved identifier.
    cleaned = mo.redact(f"project {_PROJECT_ID} token=abcDEF123456")
    assert _PROJECT_ID in cleaned
    assert "abcDEF123456" not in cleaned


def test_error_object_redacts_message_and_params() -> None:
    obj = mo.error_object(
        mo.ErrorCode.INPUT_INVALID,
        "cannot read /etc/shadow",
        params={"path": "/etc/shadow", "hint": "password=hunter2"},
    )
    serialized = json.dumps(obj.to_dict())
    assert "/etc/shadow" not in serialized
    assert "hunter2" not in serialized
    assert "<redacted" in serialized


def test_url_secret_and_userinfo_redacted() -> None:
    cleaned = mo.redact("https://user:pw@example.org/a?token=deadbeef&x=1")
    assert "user:pw" not in cleaned
    assert "deadbeef" not in cleaned


# --------------------------------------------------------------------------- #
# 4. Dry-run zero side effects (primary acceptance)
# --------------------------------------------------------------------------- #


def test_dry_run_zero_side_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xyz = _write_xyz(tmp_path)
    handler_spy, workflow_spy, task_spy = _install_execution_spies(monkeypatch)

    code, out, _err = _capture_main(
        monkeypatch,
        [
            "run",
            "singlepoint",
            "--input",
            str(xyz),
            "--output",
            str(tmp_path / "out"),
            "--json",
            "--dry-run",
        ],
    )

    _assert_no_execution(handler_spy, workflow_spy, task_spy)
    assert code == 0
    payload = json.loads(out)
    assert payload["operation"] == "singlepoint"
    assert payload["status"] == "ok"
    assert payload["dry_run"] is True
    assert payload["exit_code"] == 0
    assert payload["error"] is None
    assert payload["operation_identity"].startswith("v2:")
    data = payload["data"]
    assert data["executed"] is False
    assert data["metering_consumed"] is False
    assert data["transfers_attempted"] is False
    assert data["core_hours_reserved"] == 0
    assert data["plan"]["steps"] == ["single_point"]


def test_dry_run_alone_emits_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    xyz = _write_xyz(tmp_path)
    handler_spy, workflow_spy, task_spy = _install_execution_spies(monkeypatch)
    code, out, _err = _capture_main(
        monkeypatch,
        ["run", "frequency", "--input", str(xyz), "--dry-run"],
    )
    _assert_no_execution(handler_spy, workflow_spy, task_spy)
    assert code == 0
    assert json.loads(out)["data"]["executed"] is False


def test_dry_run_guard_has_teeth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Injecting an execution call into the dry-run path must trip the gate."""
    _handler, workflow_spy, _task = _install_execution_spies(monkeypatch)
    with pytest.raises(AssertionError):
        workflow_spy("injected")
    with pytest.raises(AssertionError):
        _assert_no_execution(workflow_spy)


# --------------------------------------------------------------------------- #
# 5. Old text-mode compatibility + JSON real mode
# --------------------------------------------------------------------------- #


def test_text_mode_output_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_handler(_args: argparse.Namespace) -> int:
        print("TEXT-OK")
        return 0

    monkeypatch.setattr(cli, "_handle_singlepoint", fake_handler)
    code, out, _err = _capture_main(monkeypatch, ["run", "singlepoint", "--input", "x.xyz"])
    assert code == 0
    assert out == "TEXT-OK\n"
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_json_mode_keeps_stdout_clean_and_preserves_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_handler(_args: argparse.Namespace) -> int:
        print("NOISE")
        return 1

    monkeypatch.setattr(cli, "_handle_singlepoint", fake_handler)
    code, out, err = _capture_main(
        monkeypatch, ["run", "singlepoint", "--input", "x.xyz", "--json"]
    )
    assert code == 1
    assert "NOISE" in err
    payload = json.loads(out)
    assert payload["status"] == "error"
    assert payload["exit_code"] == 1
    assert payload["error"]["code"] == mo.ErrorCode.RUNTIME_FAILURE


# --------------------------------------------------------------------------- #
# 6. Static guards + real-command smoke
# --------------------------------------------------------------------------- #


def test_machine_output_module_imports_no_acp() -> None:
    tree = ast.parse(_MACHINE_OUTPUT_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(not (a.name == "acp" or a.name.startswith("acp.")) for a in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not (node.module == "acp" or node.module.startswith("acp."))


def test_dry_run_plan_body_never_calls_run_star() -> None:
    tree = ast.parse(_CLI_PATH.read_text(encoding="utf-8"))
    targets = {"_dry_run_plan", "_dispatch_machine_output"}
    checked = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in targets:
            checked += 1
            for call in ast.walk(node):
                if isinstance(call, ast.Call):
                    func = call.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    assert not name.startswith("run_"), f"{node.name} must not call {name}"
    assert checked == 2


def test_real_command_smoke(tmp_path: Path) -> None:
    xyz = _write_xyz(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "acp",
            "run",
            "singlepoint",
            "--json",
            "--dry-run",
            "--input",
            str(xyz),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["dry_run"] is True
    assert payload["data"]["executed"] is False


def test_cli_text_logging_stays_off_stdout() -> None:
    """`acp task query --json` output must be json.load-able (round-6 #8).

    The text logging handler moved to stderr; machine envelopes keep stdout.
    """
    import logging
    import sys

    from acp import cli

    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers.clear()
    try:
        cli.setup_logging("INFO", structured=False)
        assert root.handlers, "setup_logging must install a handler"
        streams = [getattr(handler, "stream", None) for handler in root.handlers]
        assert sys.stdout not in streams
        assert sys.stderr in streams
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved:
            root.addHandler(handler)


# --------------------------------------------------------------------------- #
# 7. Round-7 wire-shape fixes (#7b idempotency key, #7e bounded cancel ack)
# --------------------------------------------------------------------------- #


def test_widened_idempotency_pattern_is_a_machine_readable_param() -> None:
    """#7b: a chemistry-shaped key is accepted; a refusal stays actionable.

    The pattern is part of the error envelope (a client corrects the value
    without prose parsing), it survives the secret/path net unchanged, and the
    malformed value itself is never echoed back.
    """
    from acp_services.entrypoints.application_params import (
        IDEMPOTENCY_KEY_PATTERN,
        ApplicationParamError,
        normalize_submit,
    )

    accepted = "exp:sp-ccsd(t)-ts:[v2]0001"
    assert (
        normalize_submit(
            {"workflow": "singlepoint", "input": {"source": "CCO"}, "idempotency_key": accepted}
        )["idempotency_key"]
        == accepted
    )

    with pytest.raises(ApplicationParamError) as excinfo:
        normalize_submit(
            {"workflow": "singlepoint", "input": {"source": "CCO"}, "idempotency_key": "not a key"}
        )
    params = dict(excinfo.value.params)
    assert excinfo.value.code == mo.ErrorCode.INPUT_INVALID
    assert params == {
        "field": "idempotency_key",
        "rule": "pattern",
        "pattern": IDEMPOTENCY_KEY_PATTERN,
    }
    envelope = mo.error_object(excinfo.value.code, excinfo.value.message, params=params)
    assert envelope.to_dict()["params"]["pattern"] == IDEMPOTENCY_KEY_PATTERN
    assert "not a key" not in json.dumps(envelope.to_dict())
    # The published pattern and a legal key are not secrets: redaction keeps them.
    assert mo.redact_value({"pattern": IDEMPOTENCY_KEY_PATTERN}) == {
        "pattern": IDEMPOTENCY_KEY_PATTERN
    }
    assert mo.redact_value({"idempotency_key": accepted}) == {"idempotency_key": accepted}


def test_cancel_acknowledgement_envelope_is_bounded_and_spec_free() -> None:
    """#7e: the MCP cancel result never ships the Controller's spec body."""
    from acp_services.entrypoints.mcp import _cancel_acknowledgement

    job_id = "20261101_004_singlepoint"
    record = {
        "id": job_id,
        "status": "cancelled",
        "attempt": 3,
        "updated_at": "2026-11-01T04:10:00Z",
        "spec": {
            "workflow": "singlepoint",
            "input": {"source": {"xyz_text": "3\nwater\nO 0 0 0\n" * 4000}},
        },
        "admission": {"limits": {"cores": 8}},
        "provenance": {"release_id": "release-" + "r" * 4000},
        "artifacts": [{"artifact_id": f"artifact-{i}"} for i in range(100)],
    }
    data = _cancel_acknowledgement(record, job_id)
    envelope = mo.MachineResult(operation="acp_cancel", status="ok", data=data).to_dict()
    serialized = json.dumps(envelope, ensure_ascii=False)
    assert len(serialized) < 4096
    for withheld in ("spec", "xyz_text", "admission", "provenance", "artifacts"):
        assert withheld not in serialized
    assert envelope["data"]["job_id"] == job_id
    assert envelope["data"]["attempt"] == 3
    assert envelope["data"]["cancelled"] is True
    assert mo.redact_value(data) == data
