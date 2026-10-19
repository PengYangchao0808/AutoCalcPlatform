"""B06-3 (todo 32) — execution-chain switch to the envelope/worker entry.

Acceptance clauses verified here:

1. **Frozen capability table + single dispatcher**: the C01 (HN-01) integrated
   eight are the enabled scope; the other seven stay the legacy path; an
   unknown workflow is rejected.
2. **Per-attempt protocol freeze**: one entry per attempt; an unchanged attempt
   reuses the recorded choice (resume never re-selects); a new attempt
   re-selects; an in-flight attempt keeps its protocol + release snapshot.
3. **Local + remote entry switch**: an envelope-selected local attempt runs the
   Services worker bootstrap; an envelope-selected remote attempt emits the
   worker argv; the two entries are mutually exclusive (no double dispatch).
4. **Worker entry independence**: a real subprocess runs
   ``python -m acp_services.worker_bootstrap`` from frozen data under an empty
   run root and writes a completed receipt, never opening a DB.
5. **No Services -> legacy CLI**: AST + string gate over ``src/acp_services``.
6. **Stale receipt rejection**: a receipt from another attempt/release is
   rejected and the CAS port is never called.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec
from acp.scheduler.remote.script_gen import (
    build_remote_cli_command,
    build_remote_worker_command,
)
from acp.scheduler.runner import JobRunner
from acp_services.contracts.envelope import (
    ExecutionEnvelope,
    expected_submission_id,
)
from acp_services.runtime import execution_chain as chain
from acp_services.runtime.receipts import (
    ReceiptRejectedError,
    advance_job_state,
    verify_receipt,
)
from acp_services.runtime.worker import FrozenStep, TaskExecutionOutcome, build_receipt
from acp_services.worker_bootstrap import (
    freeze_frozen_plan,
    frozen_plan_to_dict,
)
from tests.test_acp_services_isolation import (
    _code_only_text,
    scan_for_acp_imports,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVICES_ROOT = _REPO_ROOT / "src" / "acp_services"

_JOB = "job-chain-32"
_OWNER = "owner-32"
_FENCE = "fence-32"
_RELEASE = "rel-32"

_C01_EIGHT = {
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "casscf",
    "xtb_optimize",
    "OrcaGradient",
}
_LEGACY_SEVEN = {
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "XtbPathSearch",
    "XtbConstrainedOpt",
    "tsmode",
    "nmr",
}


# ---------------------------------------------------------------------------
# frozen-data helpers
# ---------------------------------------------------------------------------


def _plan(workflow: str = "singlepoint"):
    return freeze_frozen_plan(
        payload={"task": workflow},
        steps=(FrozenStep(kind=workflow, payload={"task": workflow}),),
        item_symbols=("C", "O"),
        plan_identity=f"v2:{workflow}",
        step_identities=(f"v2:{workflow}:step0",),
    )


def _envelope(workflow: str = "singlepoint", *, attempt: int = 1) -> ExecutionEnvelope:
    plan = _plan(workflow)
    return ExecutionEnvelope(
        envelope_version=1,
        job_id=_JOB,
        attempt=attempt,
        submission_id=expected_submission_id(_JOB, attempt),
        release_id=_RELEASE,
        owner=_OWNER,
        fencing_token=_FENCE,
        plan_digest=plan.identity.plan_digest,
        plan_identity=plan.identity.plan_identity,
        step_identities=plan.identity.step_identities,
        entrypoint="worker.bootstrap",
        protocol="local",
    )


def _write_frozen_artifacts(work_dir: Path, workflow: str = "singlepoint") -> None:
    runtime = work_dir.joinpath(*chain.RUNTIME_PROTOCOL_DIR)
    runtime.mkdir(parents=True, exist_ok=True)
    envelope = _envelope(workflow)
    plan = _plan(workflow)
    (runtime / chain.ENVELOPE_FILENAME).write_text(
        json.dumps(envelope.to_dict()), encoding="utf-8"
    )
    (runtime / chain.PLAN_FILENAME).write_text(
        json.dumps(frozen_plan_to_dict(plan)), encoding="utf-8"
    )


def _record(workflow: str, *, attempt: int = 1, input_payload: dict | None = None) -> JobRecord:
    spec = JobSpec(
        workflow=workflow,
        name="chain32",
        input=input_payload if input_payload is not None else {"smiles": "CCO"},
        method={},
    )
    return JobRecord(id=_JOB, spec=spec, attempt=attempt, work_dir="")


# ---------------------------------------------------------------------------
# 1. frozen capability table + dispatcher
# ---------------------------------------------------------------------------


def test_capability_table_marks_c01_eight_enabled_and_seven_legacy() -> None:
    assert set(chain.FROZEN_WORKFLOW_CAPABILITY_TABLE) == _C01_EIGHT | _LEGACY_SEVEN
    assert set(chain.ENABLED_WORKFLOWS) == _C01_EIGHT
    for workflow in _C01_EIGHT:
        assert chain.capability_for(workflow).closure == "integrated"
    for workflow in _LEGACY_SEVEN:
        assert chain.capability_for(workflow).closure == "legacy"


def test_unknown_workflow_is_rejected_by_capability_lookup() -> None:
    with pytest.raises(chain.UnsupportedExecutionWorkflowError):
        chain.capability_for("not-a-workflow")
    # The dispatcher stays total: an unknown/retired workflow keeps the legacy path.
    selection = chain.select_execution_entry(
        "not-a-workflow", remote=False, envelope_available=True
    )
    assert selection.entry == chain.EXECUTION_ENTRY_LEGACY


@pytest.mark.parametrize(
    ("workflow", "remote", "available", "entry"),
    (
        ("singlepoint", False, True, chain.EXECUTION_ENTRY_ENVELOPE),
        ("singlepoint", False, False, chain.EXECUTION_ENTRY_LEGACY),
        ("singlepoint", True, True, chain.EXECUTION_ENTRY_ENVELOPE),
        ("casscf", True, True, chain.EXECUTION_ENTRY_LEGACY),  # local-only
        ("OrcaGradient", True, True, chain.EXECUTION_ENTRY_LEGACY),  # local-only
        ("nmr", False, True, chain.EXECUTION_ENTRY_LEGACY),  # closure not integrated
        ("Confsearch", True, True, chain.EXECUTION_ENTRY_LEGACY),
    ),
)
def test_select_execution_entry(
    workflow: str, remote: bool, available: bool, entry: str
) -> None:
    selection = chain.select_execution_entry(
        workflow, remote=remote, envelope_available=available
    )
    assert selection.entry == entry
    assert selection.protocol == (
        chain.EXECUTION_PROTOCOL_LSF if remote else chain.EXECUTION_PROTOCOL_LOCAL
    )


def test_one_enabled_and_one_legacy_workflow_selected_in_parallel() -> None:
    results: dict[str, str] = {}

    def run(workflow: str, *, available: bool) -> None:
        results[workflow] = chain.select_execution_entry(
            workflow, remote=False, envelope_available=available
        ).entry

    threads = [
        threading.Thread(target=run, args=("singlepoint",), kwargs={"available": True}),
        threading.Thread(target=run, args=("nmr",), kwargs={"available": True}),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results["singlepoint"] == chain.EXECUTION_ENTRY_ENVELOPE
    assert results["nmr"] == chain.EXECUTION_ENTRY_LEGACY


# ---------------------------------------------------------------------------
# 2. per-attempt protocol freeze
# ---------------------------------------------------------------------------


def test_resolve_reuses_recorded_choice_for_unchanged_attempt(tmp_path: Path) -> None:
    first = chain.resolve_attempt_choice(
        workflow="singlepoint", attempt=1, remote=False, envelope_available=True,
        release_id="rel-1",
    )
    assert first.entry == chain.EXECUTION_ENTRY_ENVELOPE
    # Resume: same attempt, artifacts no longer visible -> the recorded choice wins.
    resumed = chain.resolve_attempt_choice(
        workflow="singlepoint", attempt=1, remote=False, envelope_available=False,
        previous=first, release_id="rel-1",
    )
    assert resumed == first  # never re-selected
    assert resumed.release_id == "rel-1"
    # New attempt may select anew.
    next_attempt = chain.resolve_attempt_choice(
        workflow="singlepoint", attempt=2, remote=False, envelope_available=False,
        previous=first, release_id="rel-2",
    )
    assert next_attempt.entry == chain.EXECUTION_ENTRY_LEGACY
    assert next_attempt.attempt == 2


def test_attempt_choice_roundtrip_file(tmp_path: Path) -> None:
    work_dir = tmp_path / "task"
    choice = chain.AttemptExecutionChoice(
        attempt=3, workflow="optimize", entry="legacy", protocol="local",
        closure="integrated", release_id="rel-x",
    )
    assert chain.read_attempt_choice(work_dir) is None
    written = chain.write_attempt_choice(work_dir, choice)
    expected_path = work_dir.joinpath(
        *chain.RUNTIME_PROTOCOL_DIR, chain.ATTEMPT_PROTOCOL_FILENAME
    )
    assert written == expected_path
    decoded = chain.read_attempt_choice(work_dir)
    assert decoded == choice
    assert chain.AttemptExecutionChoice.from_dict(choice.to_dict()) == choice


# ---------------------------------------------------------------------------
# 3. local + remote entry switch, no double dispatch
# ---------------------------------------------------------------------------


def test_local_entry_switches_to_worker_and_legacy_is_preserved(tmp_path: Path) -> None:
    runner = JobRunner(python_executable="python")

    # Enabled workflow with a frozen envelope -> Services worker entry.
    envelope_dir = tmp_path / "enabled"
    envelope_dir.mkdir()
    _write_frozen_artifacts(envelope_dir, "singlepoint")
    enabled = runner._build_execution_cmd(_record("singlepoint"), envelope_dir, "CCO", {})

    # Legacy-closure workflow -> legacy CLI entry (never the worker).
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    legacy = runner._build_execution_cmd(_record("Confsearch"), legacy_dir, "CCO", {})

    enabled_join = " ".join(enabled)
    legacy_join = " ".join(legacy)
    assert "acp_services.worker_bootstrap" in enabled_join
    assert "acp.cli" not in enabled_join
    assert "acp.cli" in legacy_join
    assert "acp_services.worker_bootstrap" not in legacy_join


def test_same_attempt_dispatch_is_idempotent(tmp_path: Path) -> None:
    runner = JobRunner(python_executable="python")
    work_dir = tmp_path / "task"
    work_dir.mkdir()
    _write_frozen_artifacts(work_dir, "optimize")
    record = _record("optimize")

    first = runner._build_execution_cmd(record, work_dir, "CCO", {})
    protocol_file = work_dir.joinpath(
        *chain.RUNTIME_PROTOCOL_DIR, chain.ATTEMPT_PROTOCOL_FILENAME
    )
    recorded = chain.read_attempt_choice(work_dir)
    assert recorded is not None and recorded.entry == chain.EXECUTION_ENTRY_ENVELOPE
    assert record.result is not None
    assert record.result["execution"]["entry"] == chain.EXECUTION_ENTRY_ENVELOPE

    # Resume of the SAME attempt yields the identical entry, never a second one.
    second = runner._build_execution_cmd(record, work_dir, "CCO", {})
    assert second == first
    assert protocol_file.is_file()


def test_remote_entry_switches_to_worker_and_legacy_default_kept() -> None:
    spec = JobSpec(workflow="singlepoint", name="sp", input={"smiles": "CCO"}, method={})
    legacy = build_remote_cli_command(spec)
    assert legacy[:3] == ["python", "-m", "acp.cli"]  # legacy default unchanged

    launch = chain.ExecutionLaunch(
        entry=chain.EXECUTION_ENTRY_ENVELOPE,
        protocol="lsf",
        closure="integrated",
        work_dir="/remote/work",
        envelope_path="/remote/work/envelope.json",
        plan_path="/remote/work/plan.json",
        binding_path="/remote/work/binding.json",
    )
    switched = build_remote_cli_command(spec, execution=launch)
    assert "acp_services.worker_bootstrap" in " ".join(switched)
    assert "acp.cli" not in " ".join(switched)


def test_remote_worker_command_delegates_to_single_root() -> None:
    direct = build_remote_worker_command(
        envelope_path="/r/e.json",
        plan_path="/r/p.json",
        work_dir="/r/work",
        receipt_path="/r/work/receipt.json",
        binding_path="/r/b.json",
        python_executable="python3",
    )
    launch = chain.ExecutionLaunch(
        entry=chain.EXECUTION_ENTRY_ENVELOPE,
        protocol="lsf",
        closure="integrated",
        work_dir="/r/work",
        envelope_path="/r/e.json",
        plan_path="/r/p.json",
        binding_path="/r/b.json",
    )
    expected = chain.worker_argv(
        "python3", launch, work_dir="/r/work", receipt_path="/r/work/receipt.json"
    )
    assert direct == expected


def test_remote_resolver_selects_envelope_only_when_frozen(tmp_path: Path) -> None:
    from acp.scheduler.remote.runner import RemoteJobRunner

    work_dir = tmp_path / "task"
    work_dir.mkdir()
    record = _record("singlepoint")
    # No frozen artifacts -> legacy via the remote dispatcher.
    choice = RemoteJobRunner._resolve_remote_choice(None, record, work_dir)
    assert choice.entry == chain.EXECUTION_ENTRY_LEGACY
    assert choice.protocol == chain.EXECUTION_PROTOCOL_LSF

    # A fresh attempt/work dir with the frozen pair -> envelope.
    envelope_dir = tmp_path / "task-envelope"
    envelope_dir.mkdir()
    _write_frozen_artifacts(envelope_dir, "singlepoint")
    record2 = _record("singlepoint")
    choice2 = RemoteJobRunner._resolve_remote_choice(None, record2, envelope_dir)
    assert choice2.entry == chain.EXECUTION_ENTRY_ENVELOPE
    assert chain.read_attempt_choice(envelope_dir) == choice2


# ---------------------------------------------------------------------------
# 4. worker entry independence (real subprocess, empty run root)
# ---------------------------------------------------------------------------


def _child_env(run_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{_REPO_ROOT / 'src'}{os.pathsep}{existing}" if existing else str(_REPO_ROOT / "src")
    )
    env["ACP_RUN_ROOT"] = str(run_root)
    for key in [k for k in env if k.startswith("CONFSEARCH_")]:
        env.pop(key, None)
    return env


def test_worker_entry_runs_from_frozen_data_without_db(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_frozen_artifacts(work_dir, "singlepoint")
    runtime = work_dir.joinpath(*chain.RUNTIME_PROTOCOL_DIR)
    run_root = tmp_path / "empty_run_root"
    run_root.mkdir()

    argv = chain.worker_argv(
        sys.executable,
        chain.ExecutionLaunch(
            entry=chain.EXECUTION_ENTRY_ENVELOPE,
            protocol="local",
            closure="integrated",
            work_dir=str(work_dir),
            envelope_path=str(runtime / chain.ENVELOPE_FILENAME),
            plan_path=str(runtime / chain.PLAN_FILENAME),
        ),
        work_dir=str(work_dir),
        probe_executor="ok",
    )
    assert "acp.cli" not in " ".join(argv)
    result = subprocess.run(
        argv, cwd=str(_REPO_ROOT), env=_child_env(run_root),
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    assert receipt["state"] == "completed"
    assert list(run_root.iterdir()) == []  # no DB / no run-root writes


# ---------------------------------------------------------------------------
# 5. no Services -> legacy CLI (AST + string gate)
# ---------------------------------------------------------------------------


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ids.add(id(body[0].value))
    return ids


def _services_sources() -> list[Path]:
    return sorted(
        path
        for path in _SERVICES_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _legacy_cli_hits(source: str, rel: str = "<mem>") -> list[str]:
    """AST + string violations for the no-legacy-CLI gate."""
    hits: list[str] = []
    code = _code_only_text(source)
    if "acp.cli" in code:
        hits.append(f"{rel}: code references the legacy CLI")
    if "subprocess" in code:
        hits.append(f"{rel}: code references subprocess")
    tree = ast.parse(source)
    doc_ids = _docstring_ids(tree)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in doc_ids
            and "acp.cli" in node.value
        ):
            hits.append(f"{rel}:{node.lineno}: non-docstring string names the legacy CLI")
    return hits


def test_services_never_reference_legacy_cli_or_subprocess() -> None:
    violations: list[str] = []
    for path in _services_sources():
        source = path.read_text(encoding="utf-8")
        rel = path.relative_to(_REPO_ROOT).as_posix()
        assert scan_for_acp_imports(source) == [], rel
        violations.extend(_legacy_cli_hits(source, rel))
    assert violations == []


def test_legacy_cli_gate_fires_on_injected_forwarder() -> None:
    injected = (
        "import subprocess\n"
        "def launch(cmd):\n"
        '    return subprocess.run(["python", "-m", "acp.cli", "run", "singlepoint"])\n'
    )
    hits = _legacy_cli_hits(injected)
    assert any("subprocess" in hit for hit in hits)
    assert any("legacy CLI" in hit for hit in hits)


# ---------------------------------------------------------------------------
# 6. stale receipt rejection + eligibility-gated advance
# ---------------------------------------------------------------------------


class _AdvanceSpy:
    def __init__(self) -> None:
        self.calls = 0

    def advance(self, *args: object, **kwargs: object) -> str:
        self.calls += 1
        return "advanced"


def test_stale_receipt_is_rejected_and_port_untouched() -> None:
    envelope = _envelope("singlepoint", attempt=1)
    receipt = build_receipt(
        envelope, TaskExecutionOutcome(state="completed", result_digest="sha256:r")
    )
    verdict = verify_receipt(
        receipt,
        envelope,
        current_attempt=2,
        current_submission_id=expected_submission_id(_JOB, 2),
        current_release_id="rel-other",
        owner_token=_OWNER,
        fencing_token=_FENCE,
    )
    assert verdict.accepted is False
    assert verdict.code == "stale_attempt"

    port = _AdvanceSpy()
    with pytest.raises(ReceiptRejectedError):
        advance_job_state(
            port,
            envelope,
            receipt,
            current_attempt=2,
            current_submission_id=expected_submission_id(_JOB, 2),
            current_release_id="rel-other",
            owner_token=_OWNER,
            fencing_token=_FENCE,
            expected_revision=0,
        )
    assert port.calls == 0  # state unchanged


def test_matching_receipt_advances_once() -> None:
    envelope = _envelope("singlepoint", attempt=1)
    receipt = build_receipt(
        envelope, TaskExecutionOutcome(state="completed", result_digest="sha256:r")
    )
    port = _AdvanceSpy()
    result = advance_job_state(
        port,
        envelope,
        receipt,
        current_attempt=1,
        current_submission_id=expected_submission_id(_JOB, 1),
        current_release_id=_RELEASE,
        owner_token=_OWNER,
        fencing_token=_FENCE,
        expected_revision=0,
    )
    assert result == "advanced"
    assert port.calls == 1
