"""B06-3 (todo 32) + 07-A (todo 24) — execution-chain switch + frozen-attempt producer.

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
7. **07-A frozen-attempt producer + execution claim (todo 24)**: the group
   commit (staging + one rename + ``frozen_group.json`` marker), reader
   strictness (no partial/mixed adoption), identity refusals, idempotent
   re-freeze, the persistent ``attempt.lock`` claim matrix, the two-real-worker
   race (exactly one executor entry; loser writes only a refusal, never an
   authoritative receipt/task_result, never a job failure CAS), and the
   killed-writer proof.
8. **07-A singlepoint freeze spec + immutable local release (todo 25)**: the
   spec reuses the workflow request builder and the ACP identity authority,
   freezes work-dir-relative paths, binds an immutable content-addressed
   release, and never activates a workflow.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from acp.calculations import attempt_freeze as acp_freeze
from acp.calculations.attempt_publication import (
    AttemptResultPublication,
    PublicationIntegrityError,
    PublicationSupersededError,
)
from acp.calculations.contracts import StepKind
from acp.calculations.identity import compute_identity
from acp.calculations.result_publication import (
    load_publication_state,
    load_scientific_result,
)
from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.manager import JobManager
from acp.scheduler.remote.release import build_release_manifest
from acp.scheduler.remote.runner import RemotePollObservation
from acp.scheduler.remote.script_gen import (
    build_remote_cli_command,
    build_remote_worker_command,
)
from acp.scheduler.remote.sync import build_sync_file_list
from acp.scheduler.runner import JobRunner
from acp.scheduler.store import JobStateConflictError, JobStore
from acp.services_completion_bootstrap import (
    FrozenGroupSnapshotReader,
    JobStoreAdvancePort,
    build_attempt_completion_service,
)
from acp.storage.manifest import ResultManifest
from acp_services.contracts.envelope import (
    ExecutionEnvelope,
    StorageBinding,
    expected_submission_id,
)
from acp_services.runtime import attempt_freeze as af
from acp_services.runtime import execution_chain as chain
from acp_services.runtime.attempt_completion import (
    ALREADY_PUBLISHED,
    CURRENT_CORRUPT_AFTER_EXIT,
    AttemptSnapshot,
)
from acp_services.runtime.attempt_freeze import (
    AttemptFreezeError,
    AttemptFreezeSpec,
    AttemptLockClaim,
    AttemptLockExecutionRight,
    AttemptRecoveryProof,
    attempt_lock_path,
    write_frozen_attempt,
)
from acp_services.runtime.execution_right import (
    ExecutionRightState,
    WorkerRefusal,
)
from acp_services.runtime.receipts import (
    ReceiptRejectedError,
    advance_job_from_worker_result,
    advance_job_state,
    verify_receipt,
)
from acp_services.runtime.worker import (
    CompletionDecision,
    FrozenStep,
    TaskExecutionOutcome,
    build_receipt,
    read_task_result,
    write_receipt_atomic,
    write_task_result,
)
from acp_services.worker_bootstrap import (
    assemble_worker,
    freeze_frozen_plan,
    frozen_plan_body_digest,
)
from cccp.calculation.requests import TaskRequest
from tests.conftest import requires_orca
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


def _binding() -> StorageBinding:
    return StorageBinding(
        binding_id="sb_chain32",
        endpoint="local",
        relative_path="proj/chain32",
        artifact_digests={"input.xyz": "sha256:chain32"},
    )


def _freeze_spec(
    workflow: str = "singlepoint",
    *,
    attempt: int = 1,
    envelope: ExecutionEnvelope | None = None,
    plan=None,
    binding: StorageBinding | None = None,
) -> AttemptFreezeSpec:
    return AttemptFreezeSpec(
        workflow=workflow,
        envelope=envelope if envelope is not None else _envelope(workflow, attempt=attempt),
        plan=plan if plan is not None else _plan(workflow),
        binding=binding,
    )


def _write_frozen_artifacts(work_dir: Path, workflow: str = "singlepoint") -> None:
    """Freeze a complete input group through the 07-A producer."""
    write_frozen_attempt(work_dir, _freeze_spec(workflow))


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
    # M2-04d adds one envelope-only recipe workflow.  It is NOT a sixteenth
    # catalog workflow and it does not change the C01 scientific closure: the
    # C01 eight stay the enabled *catalog* scope, and the recipe row is
    # envelope-only (``requires_envelope``), never a legacy fallback.
    assert set(chain.FROZEN_WORKFLOW_CAPABILITY_TABLE) == (
        _C01_EIGHT | _LEGACY_SEVEN | chain.RECIPE_WORKFLOWS
    )
    assert set(chain.ENABLED_WORKFLOWS) == _C01_EIGHT | chain.RECIPE_WORKFLOWS
    for workflow in chain.RECIPE_WORKFLOWS:
        assert chain.capability_for(workflow).requires_envelope is True
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
def test_select_execution_entry(workflow: str, remote: bool, available: bool, entry: str) -> None:
    selection = chain.select_execution_entry(workflow, remote=remote, envelope_available=available)
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
        workflow="singlepoint",
        attempt=1,
        remote=False,
        envelope_available=True,
        release_id="rel-1",
    )
    assert first.entry == chain.EXECUTION_ENTRY_ENVELOPE
    # Resume: same attempt, artifacts no longer visible -> the recorded choice wins.
    resumed = chain.resolve_attempt_choice(
        workflow="singlepoint",
        attempt=1,
        remote=False,
        envelope_available=False,
        previous=first,
        release_id="rel-1",
    )
    assert resumed == first  # never re-selected
    assert resumed.release_id == "rel-1"
    # New attempt may select anew.
    next_attempt = chain.resolve_attempt_choice(
        workflow="singlepoint",
        attempt=2,
        remote=False,
        envelope_available=False,
        previous=first,
        release_id="rel-2",
    )
    assert next_attempt.entry == chain.EXECUTION_ENTRY_LEGACY
    assert next_attempt.attempt == 2


def test_attempt_choice_roundtrip_file(tmp_path: Path) -> None:
    work_dir = tmp_path / "task"
    choice = chain.AttemptExecutionChoice(
        attempt=3,
        workflow="optimize",
        entry="legacy",
        protocol="local",
        closure="integrated",
        release_id="rel-x",
    )
    assert chain.read_attempt_choice(work_dir) is None
    written = chain.write_attempt_choice(work_dir, choice)
    expected_path = work_dir.joinpath(*chain.RUNTIME_PROTOCOL_DIR, chain.ATTEMPT_PROTOCOL_FILENAME)
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
    protocol_file = work_dir.joinpath(*chain.RUNTIME_PROTOCOL_DIR, chain.ATTEMPT_PROTOCOL_FILENAME)
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
    group = chain.load_frozen_group(work_dir)
    run_root = tmp_path / "empty_run_root"
    run_root.mkdir()

    argv = chain.worker_argv(
        sys.executable,
        chain.ExecutionLaunch(
            entry=chain.EXECUTION_ENTRY_ENVELOPE,
            protocol="local",
            closure="integrated",
            work_dir=str(work_dir),
            envelope_path=str(group.envelope_path),
            plan_path=str(group.plan_path),
        ),
        work_dir=str(work_dir),
        probe_executor="ok",
    )
    assert "acp.cli" not in " ".join(argv)
    result = subprocess.run(
        argv,
        cwd=str(_REPO_ROOT),
        env=_child_env(run_root),
        capture_output=True,
        text=True,
        timeout=120,
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
    return sorted(path for path in _SERVICES_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def _legacy_cli_hits(source: str, rel: str = "<mem>") -> list[str]:
    """AST + string violations for the no-legacy-CLI gate."""
    hits: list[str] = []
    code = _code_only_text(source)
    if "acp.cli" in code:
        hits.append(f"{rel}: code references the legacy CLI")
    tree = ast.parse(source)
    # "subprocess" is matched at the *usage* level, not as a substring: the
    # frozen cccp plugin-manifest field ``requires_subprocess`` is data a
    # service may legitimately read (cccp/plugin/metadata.py), while an
    # import/attribute/name use is always a real spawn surface.
    subprocess_used = any(
        (isinstance(node, ast.Import) and any(alias.name == "subprocess" for alias in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "subprocess")
        or (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "subprocess"
        )
        or (isinstance(node, ast.Name) and node.id == "subprocess")
        for node in ast.walk(tree)
    )
    if subprocess_used:
        hits.append(f"{rel}: code references subprocess")
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
        # module= wires the M1-01c wire-literal allowlist (value scoped to the
        # single reviewed station) into this shared scanner as well; imports
        # and reflection are still refused everywhere, station included.
        module = rel.removesuffix(".py").removeprefix("src/").replace("/", ".")
        assert scan_for_acp_imports(source, module=module) == [], rel
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


# ---------------------------------------------------------------------------
# 7. 07-A frozen-attempt producer + persistent execution claim (todo 24)
# ---------------------------------------------------------------------------


class _CountingExecutor:
    """Deterministic in-process executor that counts real entries."""

    def __init__(self, outcome: TaskExecutionOutcome | None = None) -> None:
        self.calls = 0
        self.outcome = outcome or TaskExecutionOutcome(
            state="completed", result_digest="sha256:counting"
        )

    def execute(self, scope: object) -> TaskExecutionOutcome:
        self.calls += 1
        return self.outcome


def _member_signature(group: chain.FrozenGroup) -> dict[str, tuple[int, int]]:
    return {
        entry.name: (entry.stat().st_ino, entry.stat().st_mtime_ns)
        for entry in sorted(group.directory.iterdir())
    }


def test_fresh_group_is_complete_recomputable_and_db_free(tmp_path: Path) -> None:
    work = tmp_path / "task"
    spec = _freeze_spec("singlepoint")
    group = write_frozen_attempt(work, spec)

    assert {entry.name for entry in group.directory.iterdir()} == {
        chain.ENVELOPE_FILENAME,
        chain.PLAN_FILENAME,
        chain.FROZEN_GROUP_MANIFEST_FILENAME,
    }
    manifest = json.loads(
        (group.directory / chain.FROZEN_GROUP_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert set(manifest["members"]) == {chain.ENVELOPE_FILENAME, chain.PLAN_FILENAME}
    for name, digest in manifest["members"].items():
        actual = hashlib.sha256((group.directory / name).read_bytes()).hexdigest()
        assert actual == digest
    assert manifest["attempt"] == 1
    assert manifest["workflow"] == "singlepoint"
    assert manifest["submission_id"] == expected_submission_id(_JOB, 1)
    assert chain.frozen_artifacts_present(work) is True

    staging_root = work.joinpath(*chain.RUNTIME_PROTOCOL_DIR, af.STAGING_DIRNAME)
    assert not staging_root.exists() or list(staging_root.iterdir()) == []
    assert not any(tmp_path.rglob("*.db"))  # no jobs DB anywhere


def test_same_spec_rerun_is_idempotent_without_rewrite(tmp_path: Path) -> None:
    work = tmp_path / "task"
    first = write_frozen_attempt(work, _freeze_spec("singlepoint"))
    before = _member_signature(first)

    second = write_frozen_attempt(work, _freeze_spec("singlepoint"))

    assert second.manifest.identity_key == first.manifest.identity_key
    assert _member_signature(second) == before  # existing group returned verbatim


def test_different_attempt_refused_and_group_untouched(tmp_path: Path) -> None:
    work = tmp_path / "task"
    first = write_frozen_attempt(work, _freeze_spec("singlepoint"))
    envelope_bytes = first.envelope_path.read_bytes()

    attempt2 = _envelope("singlepoint", attempt=2)  # new submission derivation included
    spec2 = _freeze_spec(envelope=attempt2)
    with pytest.raises(AttemptFreezeError, match="different attempt"):
        write_frozen_attempt(work, spec2)

    assert chain.load_frozen_group(work).manifest.attempt == 1
    assert first.envelope_path.read_bytes() == envelope_bytes  # never replaced


def test_tampered_member_body_rejected_by_reader_and_writer(tmp_path: Path) -> None:
    work = tmp_path / "task"
    group = write_frozen_attempt(work, _freeze_spec("singlepoint"))

    target = group.plan_path
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert chain.frozen_artifacts_present(work) is False
    with pytest.raises(chain.FrozenGroupError):
        chain.load_frozen_group(work)
    with pytest.raises(AttemptFreezeError, match="refusing to delete or replace"):
        write_frozen_attempt(work, _freeze_spec("singlepoint"))


def test_reader_rejects_incomplete_and_mixed_groups(tmp_path: Path) -> None:
    for damage in ("manifest", "member", "extra"):
        work = tmp_path / f"task-{damage}"
        group = write_frozen_attempt(work, _freeze_spec("singlepoint"))

        if damage == "manifest":
            (group.directory / chain.FROZEN_GROUP_MANIFEST_FILENAME).unlink()
        elif damage == "member":
            group.plan_path.unlink()
        else:
            (group.directory / "rogue.json").write_text("{}", encoding="utf-8")

        assert chain.frozen_artifacts_present(work) is False, damage
        with pytest.raises(chain.FrozenGroupError):
            chain.load_frozen_group(work)


def test_binding_membership_rules(tmp_path: Path) -> None:
    envelope_with_binding = replace(_envelope("singlepoint"), data_bindings=(_binding(),))

    declared = write_frozen_attempt(
        tmp_path / "w1", _freeze_spec(envelope=envelope_with_binding, binding=_binding())
    )
    assert declared.binding_path is not None
    assert chain.FROZEN_GROUP_MANIFEST_FILENAME in {
        entry.name for entry in declared.directory.iterdir()
    }
    assert declared.manifest.member_digests[chain.BINDING_FILENAME]
    assert chain.frozen_artifacts_present(tmp_path / "w1")

    with pytest.raises(AttemptFreezeError, match="required group member"):
        write_frozen_attempt(tmp_path / "w2", _freeze_spec(envelope=envelope_with_binding))

    with pytest.raises(AttemptFreezeError, match="not declared"):
        write_frozen_attempt(tmp_path / "w3", _freeze_spec(binding=_binding()))

    other_only = replace(
        _envelope("singlepoint"),
        data_bindings=(replace(_binding(), binding_id="sb_other"),),
    )
    with pytest.raises(AttemptFreezeError, match="not declared"):
        write_frozen_attempt(tmp_path / "w4", _freeze_spec(envelope=other_only, binding=_binding()))


def test_reader_enforces_binding_membership(tmp_path: Path) -> None:
    envelope_with_binding = replace(_envelope("singlepoint"), data_bindings=(_binding(),))

    missing = tmp_path / "missing-binding"
    group = write_frozen_attempt(
        missing, _freeze_spec(envelope=envelope_with_binding, binding=_binding())
    )
    manifest_path = group.directory / chain.FROZEN_GROUP_MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["members"].pop(chain.BINDING_FILENAME)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    group.binding_path.unlink()
    with pytest.raises(chain.FrozenGroupError, match="no binding.json member"):
        chain.load_frozen_group(missing)

    forged = tmp_path / "forged-binding"
    group = write_frozen_attempt(
        forged, _freeze_spec(envelope=envelope_with_binding, binding=_binding())
    )
    forged_binding = replace(_binding(), binding_id="sb_forged")
    forged_blob = (json.dumps(forged_binding.to_dict(), sort_keys=True, indent=2) + "\n").encode()
    group.binding_path.write_bytes(forged_blob)
    manifest_path = group.directory / chain.FROZEN_GROUP_MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["members"][chain.BINDING_FILENAME] = hashlib.sha256(forged_blob).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(chain.FrozenGroupError, match="not declared by the envelope"):
        chain.load_frozen_group(forged)


def test_freezer_refuses_identity_violations(tmp_path: Path) -> None:
    base = _envelope("singlepoint")
    forged = replace(base, submission_id="sub_forged000000000")
    with pytest.raises(AttemptFreezeError, match="expected derivation"):
        write_frozen_attempt(tmp_path / "w1", _freeze_spec(envelope=forged))

    for field, bad in (("release_id", ""), ("owner", ""), ("fencing_token", "")):
        with pytest.raises(AttemptFreezeError, match=field):
            write_frozen_attempt(
                tmp_path / f"w-{field}", _freeze_spec(envelope=replace(base, **{field: bad}))
            )

    plan = _plan("singlepoint")
    tampered_body = replace(plan, payload={"task": "optimized"}, identity=plan.identity)
    assert frozen_plan_body_digest(tampered_body) != plan.identity.plan_digest
    with pytest.raises(AttemptFreezeError, match="tampered plan body"):
        write_frozen_attempt(tmp_path / "w-body", _freeze_spec(plan=tampered_body))

    foreign_plan = freeze_frozen_plan(
        payload={"task": "optimize"},
        steps=(FrozenStep(kind="optimize", payload={"task": "optimize"}),),
        item_symbols=("C", "O"),
    )
    with pytest.raises(AttemptFreezeError, match="plan_digest"):
        write_frozen_attempt(tmp_path / "w-foreign", _freeze_spec(plan=foreign_plan))


def test_worker_rejects_tampered_group_before_execution(tmp_path: Path) -> None:
    work = tmp_path / "task"
    group = write_frozen_attempt(work, _freeze_spec("singlepoint"))
    target = group.envelope_path
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    run_root = tmp_path / "empty_run_root"
    run_root.mkdir()

    argv = [
        sys.executable,
        "-m",
        "acp_services.worker_bootstrap",
        "--envelope",
        str(group.envelope_path),
        "--plan",
        str(group.plan_path),
        "--work-dir",
        str(work),
        "--probe-executor",
        "ok",
    ]
    result = subprocess.run(
        argv,
        cwd=str(_REPO_ROOT),
        env=_child_env(run_root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout.strip().splitlines()[-1])["code"] == "frozen_group_invalid"
    # R04: the canonical receipt path is never touched by an unbound assembly
    # failure — the diagnostic lands beside it and a pre-existing receipt
    # would keep its bytes.
    receipt_path = work / "execution_receipt.json"
    assert not receipt_path.exists()
    unbound = json.loads((work / "execution_receipt.json.unbound.json").read_text(encoding="utf-8"))
    assert unbound["schema"] == "worker_assembly_error"
    assert "receipt_version" not in unbound
    assert not (work / "WORK" / "00_RUNTIME" / "task_result.json").exists()


# ── persistent execution claim (attempt.lock) ───────────────────────────────


def _write_foreign_claim(
    lock_path: Path,
    *,
    pid: int,
    owner: str = "foreign-worker",
    fencing: str = "foreign-fence",
    pid_starttime: str = "",
    lease_seconds: float = 3600.0,
) -> None:
    claim = AttemptLockClaim(
        job_id=_JOB,
        attempt=1,
        owner=owner,
        fencing_token=fencing,
        pid=pid,
        pid_starttime=pid_starttime,
        host=socket.gethostname(),
        lease_expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        ).isoformat(),
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(claim.to_dict(), indent=2), encoding="utf-8")


def test_claim_acquire_is_idempotent_within_one_process(tmp_path: Path) -> None:
    work = tmp_path / "task"
    envelope = _envelope("singlepoint")
    right = AttemptLockExecutionRight(attempt_lock_path(work))

    assert right.acquire(envelope).held is True
    assert right.acquire(envelope).held is True  # same process re-acquire stays HELD
    assert right.verify(envelope).held is True
    assert right.release(envelope) is True
    assert right.verify(envelope).state is ExecutionRightState.LOST


def test_claim_verify_lost_on_foreign_takeover(tmp_path: Path) -> None:
    work = tmp_path / "task"
    envelope = _envelope("singlepoint")
    right = AttemptLockExecutionRight(attempt_lock_path(work))
    assert right.acquire(envelope).held is True

    _write_foreign_claim(attempt_lock_path(work), pid=os.getpid())
    verdict = right.verify(envelope)
    assert verdict.state is ExecutionRightState.LOST
    assert right.release(envelope) is False  # only the holder may release


def test_claim_corrupt_payload_is_never_blindly_stolen(tmp_path: Path) -> None:
    work = tmp_path / "task"
    envelope = _envelope("singlepoint")
    lock_path = attempt_lock_path(work)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_bytes(b"\x00not-json\xff")

    verdict = AttemptLockExecutionRight(lock_path).acquire(envelope)
    assert verdict.state is ExecutionRightState.NOT_HELD
    assert "unreadable" in verdict.detail
    assert lock_path.exists()  # untouched: no blind steal


def test_claim_live_holder_is_not_stolen_dead_holder_is_checked_stolen(
    tmp_path: Path,
) -> None:
    work = tmp_path / "task"
    envelope = _envelope("singlepoint")
    lock_path = attempt_lock_path(work)
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    right = AttemptLockExecutionRight(lock_path)
    try:
        _write_foreign_claim(
            lock_path,
            pid=holder.pid,
            pid_starttime=af._proc_start_time(holder.pid),
        )
        verdict = right.acquire(envelope)
        assert verdict.state is ExecutionRightState.NOT_HELD
        assert "claimed by" in verdict.detail
    finally:
        holder.kill()
        holder.wait(timeout=30)

    # A dead worker can leave live QC children; parent death alone is not
    # checked recovery.  This fixture never created any children, so an
    # explicit reconciliation authority can confirm the complete stopped fact.
    assert right.acquire(envelope).held is False

    class ReconciledChildlessHolder:
        def confirm_stopped(self, claim: AttemptLockClaim) -> AttemptRecoveryProof:
            return AttemptRecoveryProof(
                claim.content_digest(), True, True, "test: reaped childless holder"
            )

    right = AttemptLockExecutionRight(lock_path, recovery_check=ReconciledChildlessHolder())
    assert right.acquire(envelope).held is True
    assert right.verify(envelope).held is True
    assert right.release(envelope) is True


def test_claim_recycled_pid_does_not_prove_execution_children_stopped(tmp_path: Path) -> None:
    work = tmp_path / "task"
    envelope = _envelope("singlepoint")
    lock_path = attempt_lock_path(work)
    # Same pid, wrong /proc start-time: the recorded process ended and the pid
    # was recycled — kill(0) alone would lie.
    _write_foreign_claim(lock_path, pid=os.getpid(), pid_starttime="999999999")

    verdict = AttemptLockExecutionRight(lock_path).acquire(envelope)
    assert verdict.held is False
    assert "children are not confirmed stopped" in verdict.detail


def test_loser_worker_writes_only_a_refusal_diagnostic(tmp_path: Path) -> None:
    work = tmp_path / "task"
    work.mkdir(parents=True)
    envelope = _envelope("singlepoint")
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    receipt_path = work / "execution_receipt.json"
    executor = _CountingExecutor()
    try:
        _write_foreign_claim(
            attempt_lock_path(work),
            pid=holder.pid,
            pid_starttime=af._proc_start_time(holder.pid),
        )
        runtime = assemble_worker(
            envelope=envelope,
            plan=_plan("singlepoint"),
            work_dir=work,
            receipt_path=receipt_path,
            executor=executor,
            execution_right=AttemptLockExecutionRight(attempt_lock_path(work)),
        )
        result = runtime.run()
    finally:
        holder.kill()
        holder.wait(timeout=30)

    assert isinstance(result, WorkerRefusal)
    assert result.state == "wait"
    assert executor.calls == 0  # the executor was never entered
    assert not receipt_path.exists()  # no authoritative receipt on the canonical path
    refusal_doc = json.loads(
        receipt_path.with_name(receipt_path.name + ".refused.json").read_text(encoding="utf-8")
    )
    assert refusal_doc["authoritative"] is False
    assert refusal_doc["code"] == "execution_right_not_held"
    assert not (work / "WORK" / "00_RUNTIME" / "task_result.json").exists()

    spy = _AdvanceSpy()
    assert advance_job_from_worker_result(spy, envelope, result) is None
    assert spy.calls == 0  # a refusal never triggers a job failure CAS


def test_worker_that_loses_right_at_write_seam_writes_no_receipt(tmp_path: Path) -> None:
    work = tmp_path / "task"
    work.mkdir()
    envelope = _envelope("singlepoint")
    receipt_path = work / "execution_receipt.json"

    class StealingExecutor:
        calls = 0

        def execute(self, scope: object) -> TaskExecutionOutcome:
            self.calls += 1
            # A foreign claim takes over while the executor is running.
            _write_foreign_claim(attempt_lock_path(work), pid=os.getpid())
            return TaskExecutionOutcome(state="completed", result_digest="sha256:stolen")

    executor = StealingExecutor()
    runtime = assemble_worker(
        envelope=envelope,
        plan=_plan("singlepoint"),
        work_dir=work,
        receipt_path=receipt_path,
        executor=executor,
        execution_right=AttemptLockExecutionRight(attempt_lock_path(work)),
    )
    result = runtime.run()

    assert executor.calls == 1
    assert isinstance(result, WorkerRefusal)
    assert result.state == "retry"
    assert not receipt_path.exists()  # the write seam blocked the authoritative receipt
    refusal_doc = json.loads(
        receipt_path.with_name(receipt_path.name + ".refused.json").read_text(encoding="utf-8")
    )
    assert refusal_doc["code"] == "execution_right_lost"
    assert refusal_doc["authoritative"] is False
    assert not (work / "WORK" / "00_RUNTIME" / "task_result.json").exists()


# ── two REAL worker processes racing the same group ─────────────────────────


_RACE_CHILD_SCRIPT = """
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, {src!r})

from acp_services.runtime.attempt_completion import (
    ALREADY_PUBLISHED,
    CURRENT_CORRUPT_AFTER_EXIT,
    AttemptCompletionService,
    AttemptSnapshot,
)
from acp_services.runtime.attempt_freeze import (
    AttemptLockExecutionRight,
    attempt_lock_path,
)
from acp_services.runtime.execution_right import WorkerRefusal
from acp_services.runtime.worker import TaskExecutionOutcome, outcome_from_task_result
from acp_services.worker_bootstrap import load_and_assemble


class CountingExecutor:
    def __init__(self, marker, peer_done):
        self._marker = Path(marker)
        self._peer_done = Path(peer_done)

    def execute(self, scope):
        with self._marker.open("a", encoding="utf-8") as handle:
            handle.write("entry pid={}\\n".format(os.getpid()))
        # Barrier: keep the exclusive right held until the competing process
        # has observed the live claim and written its refusal diagnostic. This
        # makes "winner still executing -> loser refuses" deterministic (the
        # window is never lost to a fast winner exiting first).
        deadline = time.monotonic() + 60.0
        while not self._peer_done.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        from types import SimpleNamespace

        result = SimpleNamespace(
            task="optimize",
            symbols=("C", "O"),
            payload=None,
            metadata={},
            status="completed",
            complete=True,
            coordinates=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
            errors=(),
        )
        return outcome_from_task_result(result, work_dir=scope.work_dir)


runtime = load_and_assemble(
    envelope_path=sys.argv[1],
    plan_path=sys.argv[2],
    work_dir=sys.argv[3],
    receipt_path=Path(sys.argv[3]) / "execution_receipt.json",
    execution_right=AttemptLockExecutionRight(attempt_lock_path(sys.argv[3])),
    executor=CountingExecutor(sys.argv[4], sys.argv[5]),
)
result = runtime.run()
if isinstance(result, WorkerRefusal):
    Path(sys.argv[5]).write_text("refusal\\n", encoding="utf-8")
    print(json.dumps({"kind": "refusal", "state": result.state}))
    sys.exit(3)
print(json.dumps({"kind": "receipt", "state": result.state}))
"""


def test_two_real_worker_processes_execute_exactly_once(tmp_path: Path) -> None:
    work = tmp_path / "task"
    work.mkdir()
    group = write_frozen_attempt(work, _freeze_spec("singlepoint"))
    marker = tmp_path / "executor_entries.txt"
    peer_done = tmp_path / "peer_done.txt"
    script = tmp_path / "race_child.py"
    script.write_text(
        _RACE_CHILD_SCRIPT.replace("{src!r}", repr(str(_REPO_ROOT / "src"))),
        encoding="utf-8",
    )
    run_root = tmp_path / "empty_run_root"
    run_root.mkdir()

    argv = [
        sys.executable,
        str(script),
        str(group.envelope_path),
        str(group.plan_path),
        str(work),
        str(marker),
        str(peer_done),
    ]
    procs = [
        subprocess.Popen(
            argv,
            cwd=str(_REPO_ROOT),
            env=_child_env(run_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outs = [proc.communicate(timeout=120) for proc in procs]
    codes = sorted(proc.returncode for proc in procs)

    assert codes == [0, 3], outs
    kinds = sorted(json.loads(out[0].strip().splitlines()[-1])["kind"] for out in outs)
    assert kinds == ["receipt", "refusal"]

    entries = marker.read_text(encoding="utf-8").splitlines()
    assert len(entries) == 1  # exactly ONE executor entry across both processes

    receipt_path = work / "execution_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["state"] == "completed"  # holder's authoritative receipt, never failed
    refusal_doc = json.loads(
        receipt_path.with_name(receipt_path.name + ".refused.json").read_text(encoding="utf-8")
    )
    assert refusal_doc["authoritative"] is False
    assert not receipt_path.with_name(receipt_path.name + ".conflict.json").exists()
    # task_result.json is a post-run artifact of the holder only.
    assert (work / "WORK" / "00_RUNTIME" / "task_result.json").is_file()
    assert list(run_root.iterdir()) == []  # no jobs DB from either worker


def test_cli_attempt_lock_flag_selects_the_persistent_claim(tmp_path: Path) -> None:
    """The shipped CLI flag constructs the persistent claim (live holder -> refusal).

    The cross-process exactly-one-executor race is proved separately with the
    injected barrier port (`test_two_real_worker_processes_execute_exactly_once`);
    here the holder is held alive so the CLI selection itself is deterministic.
    """
    work = tmp_path / "task"
    work.mkdir()
    group = write_frozen_attempt(work, _freeze_spec("singlepoint"))
    run_root = tmp_path / "empty_run_root"
    run_root.mkdir()
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        _write_foreign_claim(
            attempt_lock_path(work),
            pid=holder.pid,
            pid_starttime=af._proc_start_time(holder.pid),
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "acp_services.worker_bootstrap",
                "--envelope",
                str(group.envelope_path),
                "--plan",
                str(group.plan_path),
                "--work-dir",
                str(work),
                "--probe-executor",
                "ok",
                "--attempt-lock",
            ],
            cwd=str(_REPO_ROOT),
            env=_child_env(run_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        holder.kill()
        holder.wait(timeout=30)

    assert result.returncode == 3, result.stdout + result.stderr
    receipt_path = work / "execution_receipt.json"
    assert not receipt_path.exists()  # loser never writes an authoritative receipt
    refusal_doc = json.loads(
        receipt_path.with_name(receipt_path.name + ".refused.json").read_text(encoding="utf-8")
    )
    assert refusal_doc["code"] == "execution_right_not_held"
    assert refusal_doc["authoritative"] is False
    assert attempt_lock_path(work).is_file()
    assert list(run_root.iterdir()) == []


# ── killed group writer never exposes a mixed group ─────────────────────────


_KILLED_WRITER_SCRIPT = """
import sys
import time
from pathlib import Path

sys.path.insert(0, {src!r})

from acp_services.runtime import attempt_freeze as af
from acp_services.worker_bootstrap import load_frozen_envelope, load_frozen_plan

original = af._write_staged_member
written = {"count": 0}


def pause_after_first_member(path, blob):
    original(path, blob)
    written["count"] += 1
    if written["count"] == 1:
        print("READY", flush=True)
        time.sleep(120)


af._write_staged_member = pause_after_first_member
spec = af.AttemptFreezeSpec(
    workflow="singlepoint",
    envelope=load_frozen_envelope(sys.argv[2]),
    plan=load_frozen_plan(sys.argv[3]),
)
af.write_frozen_attempt(sys.argv[1], spec)
"""


def test_killed_real_writer_never_yields_mixed_group(tmp_path: Path) -> None:
    work = tmp_path / "task"
    work.mkdir()
    envelope_file = tmp_path / "envelope.json"
    plan_file = tmp_path / "plan.json"
    envelope_file.write_text(json.dumps(_envelope("singlepoint").to_dict()), encoding="utf-8")
    plan_file.write_text(json.dumps(af.frozen_plan_to_dict(_plan("singlepoint"))), encoding="utf-8")
    script = tmp_path / "killed_writer.py"
    script.write_text(
        _KILLED_WRITER_SCRIPT.replace("{src!r}", repr(str(_REPO_ROOT / "src"))),
        encoding="utf-8",
    )

    proc = subprocess.Popen(
        [sys.executable, str(script), str(work), str(envelope_file), str(plan_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready = proc.stdout.readline() if proc.stdout else ""
        assert ready.strip() == "READY"
        proc.kill()
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:  # pragma: no cover - defensive cleanup
            proc.kill()
            proc.wait(timeout=30)

    staging = work.joinpath(*chain.RUNTIME_PROTOCOL_DIR, af.STAGING_DIRNAME, "1")
    assert (staging / chain.ENVELOPE_FILENAME).is_file()  # killed between group files
    assert chain.frozen_artifacts_present(work) is False
    with pytest.raises(chain.FrozenGroupError):
        chain.load_frozen_group(work)

    group = write_frozen_attempt(work, _freeze_spec("singlepoint"))  # restart re-freezes
    assert chain.frozen_artifacts_present(work) is True
    assert group.manifest.attempt == 1
    assert not staging.exists()  # stale staging cleaned, never adopted


# ---------------------------------------------------------------------------
# 8. 07-A singlepoint freeze spec + immutable local release (todo 25)
# ---------------------------------------------------------------------------

_T25_XYZ = """3
water
O 0.000000 0.000000 0.000000
H 0.960000 0.000000 0.000000
H -0.240000 0.930000 0.000000
"""


def _t25_record(*, attempt: int = 1, with_method: bool = True) -> JobRecord:
    method: dict = {}
    if with_method:
        method = {
            "levels": {
                "single_point": {
                    "functional": "wB97X-D4",
                    "basis": "def2-SVP",
                }
            }
        }
    spec = JobSpec(
        workflow="singlepoint",
        name="t25",
        input={"charge": 0, "multiplicity": 1},
        method=method,
    )
    return JobRecord(id="job-t25", spec=spec, attempt=attempt, work_dir="")


@pytest.fixture(scope="module")
def t25_release(tmp_path_factory: pytest.TempPathFactory):
    """One immutable release of the real execution tree for the T25 tests."""
    run_root = tmp_path_factory.mktemp("t25-release-runs")
    return acp_freeze.build_local_release(_REPO_ROOT, run_root)


def _t25_work(tmp_path: Path) -> Path:
    work = tmp_path / "task"
    work.mkdir()
    (work / "input.xyz").write_text(_T25_XYZ, encoding="utf-8")
    return work


def test_t25_singlepoint_freeze_spec_creates_complete_recomputable_group(
    tmp_path: Path, t25_release: acp_freeze.LocalRelease
) -> None:
    work = _t25_work(tmp_path)
    record = _t25_record()
    release = t25_release

    spec = acp_freeze.build_singlepoint_freeze_spec(record, work, work / "input.xyz", release)

    assert spec.workflow == "singlepoint"
    envelope = spec.envelope
    assert envelope.job_id == record.id
    assert envelope.attempt == 1
    assert envelope.submission_id == expected_submission_id(record.id, record.attempt)
    assert envelope.release_id == release.release_id
    assert envelope.release_digest == release.release_digest
    assert envelope.owner == f"local:{socket.gethostname()}:{os.getpid()}:1"
    assert len(envelope.fencing_token) == 32
    int(envelope.fencing_token, 16)  # 32 hex characters
    assert envelope.plan_digest == frozen_plan_body_digest(spec.plan)
    assert envelope.plan_digest == spec.plan.identity.plan_digest
    assert envelope.plan_identity == spec.plan.identity.plan_identity
    assert tuple(envelope.step_identities) == tuple(spec.plan.identity.step_identities)

    task_request = TaskRequest.from_dict(spec.plan.payload)
    assert task_request.structure is not None
    assert task_request.structure.path == Path("input.xyz")
    assert task_request.output_dir == Path(".")
    assert task_request.level.method == "wB97X-D4"
    assert task_request.level.basis == "def2-SVP"
    assert spec.plan.item_symbols == ("O", "H", "H")

    binding = spec.binding
    assert binding is not None
    assert binding.kind == "local"
    assert binding.endpoint == "local"
    assert binding.relative_path == work.name
    assert binding.artifact_digests["input.xyz"] == (
        "sha256:" + hashlib.sha256((work / "input.xyz").read_bytes()).hexdigest()
    )

    group = write_frozen_attempt(work, spec)
    assert {entry.name for entry in group.directory.iterdir()} == {
        chain.ENVELOPE_FILENAME,
        chain.PLAN_FILENAME,
        chain.BINDING_FILENAME,
        chain.FROZEN_GROUP_MANIFEST_FILENAME,
    }
    manifest = json.loads(
        (group.directory / chain.FROZEN_GROUP_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["release_id"] == release.release_id
    assert manifest["release_digest"] == release.release_digest
    assert manifest["owner"] == envelope.owner
    assert manifest["fencing_token"] == envelope.fencing_token
    assert manifest["plan_identity"] == envelope.plan_identity
    for name, digest in manifest["members"].items():
        assert hashlib.sha256((group.directory / name).read_bytes()).hexdigest() == digest

    # Recompute the ACP science identity through the existing authority.
    context, request, task = acp_freeze._build_singlepoint_request(  # noqa: SLF001
        record, work, work / "input.xyz"
    )
    # R02: the frozen payload = the typed request body + the reserved
    # environment key carrying the resolved scientific configuration snapshot.
    payload_without_env = dict(spec.plan.payload)
    environment = payload_without_env.pop("environment", None)
    assert task.to_dict() == payload_without_env
    assert isinstance(environment, dict) and environment
    assert environment == request.resources["config"]
    calculation_plan = acp_freeze._build_plan(  # noqa: SLF001
        StepKind.SINGLEPOINT, [request], [StepKind.SINGLEPOINT]
    )
    identity = compute_identity(calculation_plan, location_root=work)
    assert identity.plan_identity == envelope.plan_identity
    assert identity.step_identities == envelope.step_identities
    assert task_request.structure.path.is_absolute() is False

    # No jobs DB is created by the freeze path.
    assert not list(tmp_path.rglob("*.db"))


def test_t25_same_attempt_reuses_group_and_a_new_attempt_conflicts(
    tmp_path: Path, t25_release: acp_freeze.LocalRelease
) -> None:
    work = _t25_work(tmp_path)
    first = acp_freeze.build_singlepoint_freeze_spec(
        _t25_record(), work, work / "input.xyz", t25_release
    )
    group = write_frozen_attempt(work, first)

    reused = acp_freeze.build_singlepoint_freeze_spec(
        _t25_record(), work, work / "input.xyz", t25_release
    )
    assert reused.envelope.fencing_token == first.envelope.fencing_token
    assert reused.envelope.owner == first.envelope.owner
    again = write_frozen_attempt(work, reused)
    assert again.manifest.identity_key == group.manifest.identity_key

    with pytest.raises(acp_freeze.AttemptFreezeConflictError, match="belongs to attempt 1"):
        acp_freeze.build_singlepoint_freeze_spec(
            _t25_record(attempt=2), work, work / "input.xyz", t25_release
        )
    assert chain.load_frozen_group(work).manifest.attempt == 1


def test_t25_release_digest_tracks_acp_services_and_stale_release_refuses(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    (project / "src" / "acp").mkdir(parents=True)
    (project / "src" / "acp_services").mkdir(parents=True)
    (project / "src" / "cccp").mkdir(parents=True)
    (project / "config").mkdir(parents=True)
    (project / "src" / "acp" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (project / "src" / "acp_services" / "svc.py").write_text("S = 1\n", encoding="utf-8")
    (project / "src" / "cccp" / "c.py").write_text("C = 1\n", encoding="utf-8")
    (project / "requirements-node.txt").write_text("numpy\n", encoding="utf-8")
    (project / "config" / "defaults.yaml").write_text("x: 1\n", encoding="utf-8")

    run_root = tmp_path / "runs"
    first = acp_freeze.build_local_release(project, run_root)
    first_identity = acp_freeze.local_release_identity(project)
    assert first.release_id == first_identity.release_id
    assert first.release_digest == first_identity.release_digest
    assert len(first.release_id) == 16
    assert len(first.release_digest) == 64
    first.assert_current()

    (project / "src" / "acp_services" / "svc.py").write_text("S = 2\n", encoding="utf-8")
    second_identity = acp_freeze.local_release_identity(project)
    assert second_identity.release_id != first_identity.release_id
    assert second_identity.release_digest != first_identity.release_digest
    with pytest.raises(acp_freeze.StaleReleaseError):
        first.assert_current()

    work = _t25_work(tmp_path)
    with pytest.raises(acp_freeze.StaleReleaseError):
        acp_freeze.build_singlepoint_freeze_spec(_t25_record(), work, work / "input.xyz", first)

    second = acp_freeze.build_local_release(project, run_root)
    assert second.release_id == second_identity.release_id
    assert second.release_id != first.release_id
    assert second.root != first.root
    assert first.root.is_dir()  # the old immutable release is never deleted
    assert second.root.is_dir()


def test_t25_worker_loads_acp_services_from_the_release(
    tmp_path: Path, t25_release: acp_freeze.LocalRelease
) -> None:
    work = _t25_work(tmp_path)
    group = acp_freeze.freeze_singlepoint_attempt(
        _t25_record(), work, work / "input.xyz", t25_release
    )
    run_root = tmp_path / "empty_run_root"
    run_root.mkdir()
    env = _child_env(run_root)
    env["PYTHONPATH"] = str(t25_release.worker_pythonpath) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import acp_services, acp_services.worker_bootstrap as w; "
            "print(acp_services.__file__); print(w.__file__)",
        ],
        cwd=str(work),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert probe.returncode == 0, probe.stderr
    module_paths = [Path(line) for line in probe.stdout.splitlines() if line.strip()]
    assert len(module_paths) == 2
    for module_path in module_paths:
        assert module_path.resolve().is_relative_to(t25_release.root.resolve())

    assert group.binding_path is not None
    worker = subprocess.run(
        [
            sys.executable,
            "-m",
            "acp_services.worker_bootstrap",
            "--envelope",
            str(group.envelope_path),
            "--plan",
            str(group.plan_path),
            "--binding",
            str(group.binding_path),
            "--work-dir",
            str(work),
            "--probe-executor",
            "ok",
        ],
        cwd=str(work),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert worker.returncode == 0, worker.stdout + worker.stderr
    receipt = json.loads((work / "execution_receipt.json").read_text(encoding="utf-8"))
    assert receipt["state"] == "completed"
    assert receipt["release_id"] == t25_release.release_id
    assert receipt["release_digest"] == t25_release.release_digest
    assert list(run_root.iterdir()) == []  # no jobs DB from the release worker


def test_t25_local_release_identity_imports_without_remote_extra() -> None:
    code = (
        "import sys\n"
        "sys.modules['paramiko'] = None\n"
        "from acp.calculations.attempt_freeze import local_release_identity\n"
        "loaded = [name for name in sys.modules if name.startswith('acp.scheduler.remote')]\n"
        "assert loaded == [], loaded\n"
        f"identity = local_release_identity({str(_REPO_ROOT)!r})\n"
        "print(identity.release_id, identity.release_digest)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    release_id, digest = proc.stdout.split()
    assert len(release_id) == 16
    assert len(digest) == 64


# ---------------------------------------------------------------------------
# 9. 07-B completion service + ACP JobStore CAS adapter (todo 26)
# ---------------------------------------------------------------------------


class _T26Publication:
    """Publication double: idempotent published set + optional failure."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail
        self.published: set[tuple[object, ...]] = set()

    def publish(self, snapshot: AttemptSnapshot) -> dict[str, object]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("publication transport unavailable")
        self.published.add(snapshot.identity_key)
        return {"published": True}

    def is_published(self, snapshot: AttemptSnapshot) -> bool:
        return snapshot.identity_key in self.published


def _t26_record(
    work: Path,
    *,
    job_id: str = _JOB,
    attempt: int = 1,
    status: JobStatus = JobStatus.QUEUED,
) -> JobRecord:
    return JobRecord(
        id=job_id,
        spec=JobSpec(workflow="singlepoint", name="t26", input={"smiles": "CCO"}),
        status=status,
        work_dir=str(work),
        attempt=attempt,
    )


def _t26_env(job_id: str = _JOB, *, attempt: int = 1):
    base = _envelope(attempt=attempt)
    if job_id == _JOB:
        return base
    return replace(
        base,
        job_id=job_id,
        submission_id=expected_submission_id(job_id, attempt),
    )


def _t26_receipt(env, *, state: str = "completed"):
    return build_receipt(env, TaskExecutionOutcome(state=state, result_digest="sha256:t26"))


def test_t26_jobstore_adapter_advances_once_and_restart_is_a_noop(tmp_path: Path) -> None:
    work = tmp_path / "task"
    work.mkdir()
    env = _t26_env()
    write_frozen_attempt(work, _freeze_spec(envelope=env))
    store = JobStore(tmp_path / "jobs.db")
    store.create(_t26_record(work))
    receipt = _t26_receipt(env)
    receipt_path = work / "execution_receipt.json"
    write_receipt_atomic(receipt_path, receipt)

    publication = _T26Publication()
    service = build_attempt_completion_service(store, publication=publication)
    snapshot = FrozenGroupSnapshotReader(store).read(_JOB)
    assert snapshot is not None
    verdict = service.complete(snapshot, receipt_path=receipt_path, confirmed_exited=True)

    assert verdict.code == "published"
    assert verdict.cas_calls == 1
    assert publication.calls == 1
    stored = store.get(_JOB)
    assert stored is not None
    assert stored.status == JobStatus.COMPLETED
    assert stored.revision == 1
    assert stored.attempt == 1
    result = stored.result or {}
    assert result["publication_pending"] is True
    assert result["terminal_side_effects_done"] is False
    assert result["receipt_state"] == "completed"

    # Restart / duplicate delivery: terminal + already published -> no CAS.
    revision_before = stored.revision
    duplicate = service.complete(
        FrozenGroupSnapshotReader(store).read(_JOB),  # type: ignore[arg-type]
        receipt_path=receipt_path,
        confirmed_exited=True,
    )
    assert duplicate.code == ALREADY_PUBLISHED
    assert duplicate.cas_calls == 0
    assert store.get(_JOB).revision == revision_before  # type: ignore[union-attr]
    assert publication.calls == 1


def test_t26_crash_between_cas_and_publication_resumes_without_second_cas(
    tmp_path: Path,
) -> None:
    job_id = "job-t26-crash"
    work = tmp_path / "task-crash"
    work.mkdir()
    env = _t26_env(job_id)
    write_frozen_attempt(work, _freeze_spec(envelope=env))
    store = JobStore(tmp_path / "jobs.db")
    store.create(_t26_record(work, job_id=job_id))
    receipt_path = work / "execution_receipt.json"
    write_receipt_atomic(receipt_path, _t26_receipt(env))

    failing = _T26Publication(fail=True)
    service = build_attempt_completion_service(store, publication=failing)
    snapshot = FrozenGroupSnapshotReader(store).read(job_id)
    assert snapshot is not None
    failed = service.complete(snapshot, receipt_path=receipt_path, confirmed_exited=True)
    assert failed.code == "publication_failed"
    assert failed.cas_calls == 1
    assert failing.calls == 1
    after_cas = store.get(job_id)
    assert after_cas is not None
    assert after_cas.revision == 1
    assert (after_cas.result or {})["publication_pending"] is True

    # "Restart": the terminal CAS is not repeated; only publication resumes.
    recovered_pub = _T26Publication()
    restarted = build_attempt_completion_service(store, publication=recovered_pub)
    pending = FrozenGroupSnapshotReader(store).read(job_id)
    assert pending is not None
    resumed = restarted.resume_pending_publication(pending)
    assert resumed.code == "published"
    assert resumed.cas_calls == 0
    assert recovered_pub.calls == 1
    final = store.get(job_id)
    assert final is not None
    assert final.revision == 1  # no second CAS / no second QC-run transition
    assert final.status == JobStatus.COMPLETED


def test_t26_stale_attempt_receipt_leaves_the_job_untouched(tmp_path: Path) -> None:
    work = tmp_path / "task-stale"
    work.mkdir()
    current_env = _t26_env(attempt=2)
    write_frozen_attempt(work, _freeze_spec(envelope=current_env))
    store = JobStore(tmp_path / "jobs.db")
    store.create(_t26_record(work, attempt=2))
    stale_receipt = _t26_receipt(_envelope(attempt=1))

    publication = _T26Publication()
    service = build_attempt_completion_service(store, publication=publication)
    snapshot = FrozenGroupSnapshotReader(store).read(_JOB)
    assert snapshot is not None
    verdict = service.complete(snapshot, receipt=stale_receipt, confirmed_exited=True)

    assert verdict.code == "stale_attempt"
    assert verdict.cas_calls == 0
    assert publication.calls == 0
    unchanged = store.get(_JOB)
    assert unchanged is not None
    assert unchanged.status == JobStatus.QUEUED
    assert unchanged.revision == 0
    assert unchanged.attempt == 2
    assert unchanged.result is None


def test_t26_current_corrupt_after_exit_is_one_failure_cas_for_the_controller(
    tmp_path: Path,
) -> None:
    work = tmp_path / "task-corrupt"
    work.mkdir()
    env = _t26_env()
    write_frozen_attempt(work, _freeze_spec(envelope=env))
    store = JobStore(tmp_path / "jobs.db")
    store.create(_t26_record(work))
    publication = _T26Publication()
    service = build_attempt_completion_service(store, publication=publication)
    snapshot = FrozenGroupSnapshotReader(store).read(_JOB)
    assert snapshot is not None

    verdict = service.complete(
        snapshot,
        receipt_path=work / "execution_receipt.json",
        confirmed_exited=True,
    )
    assert verdict.code == CURRENT_CORRUPT_AFTER_EXIT
    assert verdict.failure is not None
    assert verdict.requires_controller_cas is True
    assert verdict.cas_calls == 0
    assert publication.calls == 0
    untouched = store.get(_JOB)
    assert untouched is not None
    assert untouched.status == JobStatus.QUEUED
    assert untouched.revision == 0

    # Task 27 consumes the returned fact and writes exactly one CAS failure.
    adapter = JobStoreAdvancePort(store)
    failed = adapter.advance(
        _JOB,
        expected_revision=0,
        expected_status=JobStatus.QUEUED,
        expected_attempt=1,
        status=JobStatus.FAILED,
        error=verdict.failure.code,
    )
    assert failed.status == JobStatus.FAILED
    assert failed.revision == 1
    with pytest.raises(JobStateConflictError):
        adapter.advance(
            _JOB,
            expected_revision=0,
            expected_status=JobStatus.QUEUED,
            expected_attempt=1,
            status=JobStatus.FAILED,
            error=verdict.failure.code,
        )


def test_t26_completion_service_is_not_a_second_job_writer() -> None:
    from acp_services.runtime import attempt_completion as completion

    source = Path(completion.__file__).read_text(encoding="utf-8")
    assert "sqlite3" not in source
    assert ".transition(" not in source
    assert not any(line.startswith(("from acp.", "import acp.")) for line in source.splitlines())
    assert "advance_job_state" in source


# ---------------------------------------------------------------------------
# 10. 07-B opt-in local singlepoint chain (todo 27)
# ---------------------------------------------------------------------------

_T27_XYZ = """3
water
O 0.000000 0.000000 0.000000
H 0.960000 0.000000 0.000000
H -0.240000 0.930000 0.000000
"""


def _t27_manager(
    tmp_path: Path,
    *,
    probe: str | None = "ok",
) -> tuple[JobManager, JobStore, JobRunner, Path]:
    run_root = tmp_path / "run_root"
    run_root.mkdir()
    store = JobStore(run_root / "acp_jobs.db")
    runner = JobRunner(python_executable=sys.executable)
    runner.worker_probe_executor = probe
    manager = JobManager(
        run_root,
        store=store,
        runner=runner,
        envelope_completion=True,
        poll_interval=5,
    )
    return manager, store, runner, run_root


def _t27_submit(
    manager: JobManager,
    tmp_path: Path,
    *,
    probe: str = "ok",
) -> JobRecord:
    """Submit through the real manager; freeze the group in the allocator.

    ``_allocate_work_dir`` runs after the manager generated the job id but
    before the worker command is built, so the frozen envelope can carry the
    real job id / submission derivation.
    """
    work = tmp_path / "task"

    def _allocate(spec: JobSpec, job_id: str) -> Path:
        work.mkdir(parents=True, exist_ok=True)
        (work / "input.xyz").write_text(_T27_XYZ, encoding="utf-8")
        env = _t26_env(job_id)
        write_frozen_attempt(work, _freeze_spec(envelope=env))
        return work

    manager._allocate_work_dir = _allocate  # type: ignore[method-assign]
    spec = JobSpec(
        workflow="singlepoint",
        name="t27",
        input={"smiles": "CCO"},
        method={},
    )
    return manager.submit(spec)


def _t27_wait(manager: JobManager, store: JobStore, job_id: str, *, timeout: float = 30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        record = store.get(job_id)
        if record is not None and record.status.is_terminal:
            return record
        manager._poll_job(job_id)
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach a terminal state in {timeout}s")


def _t27_events(work: Path) -> str:
    return (work / "WORK" / "00_RUNTIME" / "events.jsonl").read_text(encoding="utf-8")


@requires_orca
def test_t27_opt_in_singlepoint_runs_worker_advances_once_and_restarts_noop(
    tmp_path: Path,
) -> None:
    manager, store, _runner, run_root = _t27_manager(tmp_path)
    try:
        record = _t27_submit(manager, tmp_path)
        terminal = _t27_wait(manager, store, record.id)

        assert terminal.status == JobStatus.COMPLETED
        assert terminal.revision >= 1  # at least the one terminal CAS
        assert terminal.attempt == 1
        result = terminal.result or {}
        assert result["publication_pending"] is True
        assert result["receipt_state"] == "completed"
        work = Path(terminal.work_dir)
        receipt_path = work / "execution_receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt["state"] == "completed"
        events = _t27_events(work)
        assert "acp_services.worker_bootstrap" in events
        assert "acp.cli" not in events
        choice = chain.read_attempt_choice(work)
        assert choice is not None and choice.entry == chain.EXECUTION_ENTRY_ENVELOPE
        assert choice.attempt == 1

        # A duplicate/extra poll after the terminal CAS never re-executes.
        revision = terminal.revision
        manager._poll_job(record.id)
        after_duplicate = store.get(record.id)
        assert after_duplicate is not None
        assert after_duplicate.revision == revision
        assert _t27_events(work).count('"type": "process.starting"') == 1

        # Restart after the receipt: terminal row is not re-executed.
        manager.shutdown()
        manager = None  # type: ignore[assignment]
        restart_store = JobStore(run_root / "acp_jobs.db")
        restart_runner = JobRunner(python_executable=sys.executable)
        restart_runner.worker_probe_executor = "ok"
        restarted = JobManager(
            run_root,
            store=restart_store,
            runner=restart_runner,
            envelope_completion=True,
            poll_interval=5,
        )
        try:
            after_restart = restart_store.get(record.id)
            assert after_restart is not None
            assert after_restart.status == JobStatus.COMPLETED
            assert after_restart.revision == revision
            assert _t27_events(work).count('"type": "process.starting"') == 1
            assert receipt_path.is_file()
        finally:
            restarted.shutdown()
    finally:
        if manager is not None:
            manager.shutdown()


@requires_orca
def test_t27_failed_receipt_yields_failed_via_cas(tmp_path: Path) -> None:
    manager, store, _runner, _run_root = _t27_manager(tmp_path, probe="fail")
    try:
        record = _t27_submit(manager, tmp_path, probe="fail")
        terminal = _t27_wait(manager, store, record.id)
        assert terminal.status == JobStatus.FAILED
        assert terminal.revision >= 1
        result = terminal.result or {}
        assert result["receipt_state"] == "failed"
        assert result["publication_pending"] is True
        receipt = json.loads(
            (Path(terminal.work_dir) / "execution_receipt.json").read_text(encoding="utf-8")
        )
        assert receipt["state"] == "failed"
    finally:
        manager.shutdown()


@pytest.mark.parametrize("status", [JobStatus.RUNNING, JobStatus.PAUSED, JobStatus.CANCELLING])
def test_t27_stale_attempt_receipt_leaves_current_attempt_unchanged(
    tmp_path: Path, status: JobStatus
) -> None:
    manager, store, _runner, _run_root = _t27_manager(tmp_path)
    try:
        work = tmp_path / "task"
        work.mkdir()
        write_frozen_attempt(work, _freeze_spec(envelope=_t26_env(_JOB, attempt=2)))
        chain.write_attempt_choice(
            work,
            chain.AttemptExecutionChoice(
                attempt=2,
                workflow="singlepoint",
                entry=chain.EXECUTION_ENTRY_ENVELOPE,
                protocol=chain.EXECUTION_PROTOCOL_LOCAL,
                closure="integrated",
                release_id=_RELEASE,
            ),
        )
        write_receipt_atomic(
            work / "execution_receipt.json",
            _t26_receipt(_envelope(attempt=1)),
        )
        record = JobRecord(
            id=_JOB,
            spec=JobSpec(workflow="singlepoint", name="t27", input={"smiles": "CCO"}),
            status=status,
            work_dir=str(work),
            attempt=2,
        )
        store.create(record)
        manager.runner.poll = lambda _record: (True, 0)  # type: ignore[method-assign]
        work_events = work / "WORK" / "00_RUNTIME" / "events.jsonl"

        manager._poll_job(_JOB)

        unchanged = store.get(_JOB)
        assert unchanged is not None
        assert unchanged.status == status
        assert unchanged.revision == 0
        assert unchanged.attempt == 2
        assert unchanged.result is None
        if status != JobStatus.PAUSED:
            assert "job.completion_receipt_rejected" in work_events.read_text(encoding="utf-8")
    finally:
        manager.shutdown()


def test_t27_current_corrupt_after_exit_is_one_failure_cas(tmp_path: Path) -> None:
    manager, store, _runner, _run_root = _t27_manager(tmp_path)
    try:
        work = tmp_path / "task"
        work.mkdir()
        write_frozen_attempt(work, _freeze_spec(envelope=_t26_env(_JOB)))
        chain.write_attempt_choice(
            work,
            chain.AttemptExecutionChoice(
                attempt=1,
                workflow="singlepoint",
                entry=chain.EXECUTION_ENTRY_ENVELOPE,
                protocol=chain.EXECUTION_PROTOCOL_LOCAL,
                closure="integrated",
                release_id=_RELEASE,
            ),
        )
        record = JobRecord(
            id=_JOB,
            spec=JobSpec(workflow="singlepoint", name="t27", input={"smiles": "CCO"}),
            status=JobStatus.RUNNING,
            work_dir=str(work),
        )
        store.create(record)
        manager.runner.poll = lambda _record: (True, 1)  # type: ignore[method-assign]

        manager._poll_job(_JOB)

        failed = store.get(_JOB)
        assert failed is not None
        assert failed.status == JobStatus.FAILED
        assert failed.revision >= 1
        assert failed.exit_code == 1
        assert "current_corrupt_after_exit" in (failed.error or "")
        result = failed.result or {}
        assert result["completion_failure"]["code"] == "current_corrupt_after_exit"
        events = (work / "WORK" / "00_RUNTIME" / "events.jsonl").read_text(encoding="utf-8")
        assert "job.completion_failure" in events

        # Exactly one failure CAS: a repeated poll is terminal no-op.
        revision = failed.revision
        manager._poll_job(_JOB)
        again = store.get(_JOB)
        assert again is not None and again.revision == revision
        assert events.count("job.completion_failure") == 1
    finally:
        manager.shutdown()


# ---------------------------------------------------------------------------
# 11. 07-B retryable publication without recomputation (todo 28)
# ---------------------------------------------------------------------------


class _T28Result:
    """Minimal TaskResult-shaped object for the worker task_result codec."""

    def __init__(self, *, status: str = "completed") -> None:
        self._status = status

    def to_dict(self) -> dict[str, object]:
        return {
            "task": "singlepoint",
            "status": self._status,
            "energy_hartree": -76.0,
            "symbols": ["O", "H", "H"],
            "coordinates": [
                [0.0, 0.0, 0.0],
                [0.96, 0.0, 0.0],
                [-0.24, 0.93, 0.0],
            ],
        }


class _T28Reader:
    def __init__(self, snapshot: AttemptSnapshot) -> None:
        self.snapshot = snapshot

    def read(self, job_id: str) -> AttemptSnapshot | None:
        return self.snapshot if self.snapshot.job_id == job_id else None


def _t28_decision(*, partial: bool = False) -> CompletionDecision:
    if partial:
        return CompletionDecision(
            row="c",
            receipt_state="completed",
            exit_code=0,
            job_status="completed",
            normal_reuse=False,
            publish=True,
            publish_policy="partial",
            partial_result=True,
            qualification="partial_result",
            task="singlepoint",
            status="completed",
            complete=False,
        )
    return CompletionDecision(
        row="b",
        receipt_state="completed",
        exit_code=0,
        job_status="completed",
        normal_reuse=True,
        publish=True,
        publish_policy="normal",
        partial_result=False,
        qualification="complete",
        task="singlepoint",
        status="completed",
        complete=True,
        required_facts=("coordinates", "symbols_aligned"),
    )


def _t28_attempt(tmp_path: Path, *, partial: bool = False) -> tuple[Path, AttemptSnapshot, str]:
    work = tmp_path / "task"
    work.mkdir()
    env = _t26_env(_JOB)
    write_frozen_attempt(work, _freeze_spec(envelope=env))
    decision = _t28_decision(partial=partial)
    result = _T28Result()
    task_result_digest = write_task_result(work, decision, result)
    document = read_task_result(work)
    assert document is not None
    receipt = build_receipt(
        env,
        TaskExecutionOutcome(
            state=decision.receipt_state,
            result_digest=str(document["result_digest"]),
            evidence_digests={"task_result_digest": task_result_digest},
        ),
    )
    write_receipt_atomic(work / "execution_receipt.json", receipt)
    snapshot = AttemptSnapshot(
        job_id=env.job_id,
        attempt=env.attempt,
        submission_id=env.submission_id,
        release_id=env.release_id,
        owner=env.owner,
        fencing_token=env.fencing_token,
        revision=1,
        status="running",
        release_digest=env.release_digest,
        work_dir=str(work),
        result={},
        envelope=env,
    )
    return work, snapshot, task_result_digest


def test_t28_adapter_publishes_bound_task_result_idempotently(tmp_path: Path) -> None:
    work, snapshot, task_result_digest = _t28_attempt(tmp_path)
    adapter = AttemptResultPublication(workflow="singlepoint")

    report = adapter.publish(snapshot)

    result_dir = work / "RESULT"
    assert report.workflow == "singlepoint"
    assert report.manifest.status == "completed"
    assert report.partial is False
    assert (result_dir / "scientific_result.json").is_file()
    assert (result_dir / "result_manifest.json").is_file()
    assert (result_dir / "publication_state.json").is_file()
    assert (result_dir / "task_result.json").is_file()
    manifest = ResultManifest.read(result_dir)
    assert manifest.task_id == snapshot.job_id
    assert manifest.workflow == "singlepoint"
    assert manifest.status == "completed"
    # R01: publication carries the scientific products derived from the
    # persisted result body (energy + final structure), not only the
    # qualification file.
    assert [product.path for product in manifest.products] == [
        "task_result.json",
        "energy/singlepoint_energy.json",
        "structures/singlepoint_final.xyz",
    ]
    energy_product = next(
        product for product in manifest.products if product.kind.value == "energy_report"
    )
    assert energy_product.metadata["energy_hartree"] == -76.0
    record = load_scientific_result(result_dir)
    assert record is not None
    assert record.kind == "singlepoint"
    assert record.summary["task_result_digest"] == task_result_digest
    assert record.summary["plan_identity"] == snapshot.envelope.plan_identity
    state = load_publication_state(result_dir)
    assert state is not None and state.complete is True
    assert state.result_id == adapter.result_id(snapshot)
    assert adapter.is_published(snapshot) is True

    again = adapter.publish(snapshot)
    assert again.result_id == report.result_id
    assert [product.path for product in ResultManifest.read(result_dir).products] == [
        "task_result.json",
        "energy/singlepoint_energy.json",
        "structures/singlepoint_final.xyz",
    ]


def test_t28_publish_failure_keeps_scientific_record_then_retries(
    tmp_path: Path,
) -> None:
    work, snapshot, _digest = _t28_attempt(tmp_path)
    calls = {"count": 0}

    def publisher(result_dir: Path, manifest: ResultManifest) -> Path:
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("publication transport unavailable")
        return manifest.write(result_dir)

    adapter = AttemptResultPublication(workflow="singlepoint", publisher=publisher)
    with pytest.raises(OSError):
        adapter.publish(snapshot)

    result_dir = work / "RESULT"
    assert load_scientific_result(result_dir) is not None  # durable record survives
    assert not (result_dir / "result_manifest.json").exists()
    state = load_publication_state(result_dir)
    assert state is None or state.complete is False
    # No executor/worker process is involved in publication retry (QC count 0).
    assert not (work / "WORK" / "00_RUNTIME" / "events.jsonl").exists() or (
        "process.starting" not in _t27_events(work)
    )

    report = adapter.publish(snapshot)
    assert calls["count"] == 2
    assert report.manifest.status == "completed"
    assert (result_dir / "result_manifest.json").is_file()
    state = load_publication_state(result_dir)
    assert state is not None and state.complete is True


def test_t28_partial_result_publishes_only_as_partial(tmp_path: Path) -> None:
    work, snapshot, _digest = _t28_attempt(tmp_path, partial=True)
    adapter = AttemptResultPublication(workflow="singlepoint")

    report = adapter.publish(snapshot)

    assert report.partial is True
    assert report.manifest.status == "partial"
    assert report.manifest.status != "completed"
    assert len(report.manifest.products) == 1
    product = report.manifest.products[0]
    assert product.metadata["partial_result"] is True
    assert product.metadata["normal_reuse"] is False
    assert product.metadata["product_role"] == "partial"
    record = load_scientific_result(work / "RESULT")
    assert record is not None
    assert record.summary["partial_result"] is True
    assert record.summary["normal_reuse"] is False


def test_t28_tampered_or_missing_task_result_is_rejected(tmp_path: Path) -> None:
    work, snapshot, _digest = _t28_attempt(tmp_path)
    task_result = work / "WORK" / "00_RUNTIME" / "task_result.json"
    adapter = AttemptResultPublication(workflow="singlepoint")

    payload = json.loads(task_result.read_text(encoding="utf-8"))
    payload["status"] = "failed"
    task_result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PublicationIntegrityError, match="task_result"):
        adapter.publish(snapshot)
    assert not (work / "RESULT" / "result_manifest.json").exists()

    task_result.unlink()
    with pytest.raises(PublicationIntegrityError, match="task_result"):
        adapter.publish(snapshot)
    assert not (work / "RESULT" / "result_manifest.json").exists()


def test_t28_superseded_attempt_never_publishes_into_rerun_directory(
    tmp_path: Path,
) -> None:
    work, snapshot, _digest = _t28_attempt(tmp_path)
    newer = replace(
        snapshot,
        attempt=2,
        submission_id=expected_submission_id(_JOB, 2),
        owner="worker-2",
        fencing_token="fence-2",
        revision=2,
    )
    adapter = AttemptResultPublication(workflow="singlepoint", snapshot_reader=_T28Reader(newer))

    with pytest.raises(PublicationSupersededError):
        adapter.publish(snapshot)
    assert not (work / "RESULT" / "result_manifest.json").exists()


def test_t28_manager_restart_resumes_pending_publication_without_qc_or_cas(
    tmp_path: Path,
) -> None:
    work, snapshot, _digest = _t28_attempt(tmp_path)
    run_root = tmp_path / "run_root"
    run_root.mkdir()
    store = JobStore(run_root / "acp_jobs.db")
    record = JobRecord(
        id=_JOB,
        spec=JobSpec(workflow="singlepoint", name="t28", input={"smiles": "CCO"}),
        status=JobStatus.COMPLETED,
        work_dir=str(work),
        attempt=1,
        result={"publication_pending": True, "terminal_side_effects_done": True},
    )
    store.create(record)
    calls = {"count": 0}

    def publisher(result_dir: Path, manifest: ResultManifest) -> Path:
        calls["count"] += 1
        return manifest.write(result_dir)

    adapter = AttemptResultPublication(workflow="singlepoint", publisher=publisher)
    manager = JobManager(
        run_root,
        store=store,
        runner=JobRunner(python_executable=sys.executable),
        envelope_completion=True,
        attempt_publication=adapter,
        poll_interval=5,
    )
    try:
        # Startup sweep continues the pending publication (no worker/CAS).
        assert (work / "RESULT" / "result_manifest.json").is_file()
        assert adapter.is_published(snapshot) is True
        assert calls["count"] == 1
        after_startup = store.get(_JOB)
        assert after_startup is not None
        assert after_startup.revision == 0
        assert after_startup.status == JobStatus.COMPLETED

        # A later reconcile confirms the already-published fact without a
        # second publisher call, second CAS, or QC run.
        manager._reconcile_once()
        assert calls["count"] == 1
        assert store.get(_JOB).revision == 0  # type: ignore[union-attr]
        assert "process.starting" not in _t27_events(work)

        # A terminal no-op must not skip an unwritten manifest: deleting the
        # manifest (state marker still complete) makes the next recovery
        # re-publish through the same named path, without QC or a job CAS.
        (work / "RESULT" / "result_manifest.json").unlink()
        manager._reconcile_once()
        assert (work / "RESULT" / "result_manifest.json").is_file()
        assert calls["count"] == 2
        assert store.get(_JOB).revision == 0  # type: ignore[union-attr]
    finally:
        manager.shutdown()


# ---------------------------------------------------------------------------
# 12. 07-D same-attempt protocol + archive discipline (todo 32)
# ---------------------------------------------------------------------------


def _t32_work(tmp_path: Path, *, attempt: int = 1) -> Path:
    work = tmp_path / "task"
    work.mkdir(parents=True)
    env = _t26_env(_JOB, attempt=attempt)
    write_frozen_attempt(work, _freeze_spec(envelope=env))
    chain.write_attempt_choice(
        work,
        chain.AttemptExecutionChoice(
            attempt=attempt,
            workflow="singlepoint",
            entry=chain.EXECUTION_ENTRY_ENVELOPE,
            protocol=chain.EXECUTION_PROTOCOL_LOCAL,
            closure="integrated",
            release_id=_RELEASE,
        ),
    )
    return work


def test_t32_same_attempt_reuses_choice_group_and_claim_without_archive(
    tmp_path: Path,
) -> None:
    work = _t32_work(tmp_path)
    runtime = work.joinpath(*chain.RUNTIME_PROTOCOL_DIR)
    (runtime / af.ATTEMPT_LOCK_FILENAME).write_text("{}", encoding="utf-8")
    group_before = chain.load_frozen_group(work, expected_attempt=1)
    record = _record("singlepoint", attempt=1)
    runner = JobRunner(python_executable=sys.executable)

    first = runner._resolve_attempt_choice(record, work)
    second = runner._resolve_attempt_choice(record, work)

    assert first == second
    assert first.entry == chain.EXECUTION_ENTRY_ENVELOPE
    assert chain.load_frozen_group(work, expected_attempt=1).manifest.identity_key == (
        group_before.manifest.identity_key
    )
    assert (runtime / af.ATTEMPT_LOCK_FILENAME).is_file()
    assert not (work / "WORK" / "00_RUNTIME" / "attempts").exists()
    assert chain.read_attempt_choice(work) == first


def test_t32_continue_archives_all_per_attempt_files_after_stop(
    tmp_path: Path,
) -> None:
    manager, store, _runner, _run_root = _t27_manager(tmp_path)
    try:
        work = _t32_work(tmp_path)
        runtime = work.joinpath(*chain.RUNTIME_PROTOCOL_DIR)
        (runtime / af.ATTEMPT_LOCK_FILENAME).write_text("{}", encoding="utf-8")
        (runtime / "run.lock").write_text("{}", encoding="utf-8")
        (work / ".exit_code").write_text("1", encoding="utf-8")
        (work / "state.json").write_text("{}", encoding="utf-8")
        (work / "checkpoint.json").write_text("{}", encoding="utf-8")
        (work / "RESULT").mkdir()
        (work / "RESULT" / "result_manifest.json").write_text("{}", encoding="utf-8")
        write_receipt_atomic(
            work / "execution_receipt.json",
            _t26_receipt(_t26_env(_JOB)),
        )
        (runtime / "task_result.json").write_text("{}", encoding="utf-8")
        record = JobRecord(
            id=_JOB,
            spec=JobSpec(workflow="mechanism", name="t32", input={"smiles": "CCO"}),
            status=JobStatus.FAILED,
            work_dir=str(work),
            attempt=1,
        )
        store.create(record)
        submitted: list[str] = []
        manager._execute_submission = lambda job_id: submitted.append(job_id)  # type: ignore[method-assign]

        continued = manager.continue_job(_JOB)

        assert continued.attempt == 2
        assert continued.status == JobStatus.QUEUED
        archive = work / "WORK" / "00_RUNTIME" / "attempts" / "1"
        for rel in (
            "execution_receipt.json",
            ".exit_code",
            "state.json",
            "WORK/00_RUNTIME/execution_protocol.json",
            "WORK/00_RUNTIME/frozen/envelope.json",
            "WORK/00_RUNTIME/frozen/plan.json",
            "WORK/00_RUNTIME/frozen/frozen_group.json",
            "WORK/00_RUNTIME/attempt.lock",
            "WORK/00_RUNTIME/task_result.json",
            "WORK/00_RUNTIME/run.lock",
        ):
            assert (archive / rel).is_file(), rel
        assert not (runtime / "execution_protocol.json").exists()
        assert not (runtime / "frozen").exists()
        assert not (runtime / af.ATTEMPT_LOCK_FILENAME).exists()
        assert not (work / "execution_receipt.json").exists()
        # Science stays in place on continue (adopted by the new attempt).
        assert (work / "checkpoint.json").is_file()
        assert (work / "RESULT" / "result_manifest.json").is_file()
        assert not (archive / "RESULT").exists()
    finally:
        manager.shutdown()


def test_t32_envelope_attempt_mismatch_is_typed(tmp_path: Path) -> None:
    work = _t32_work(tmp_path, attempt=1)

    with pytest.raises(chain.FrozenAttemptMismatchError, match="stale envelope"):
        chain.load_frozen_group(work, expected_attempt=2)
    assert chain.frozen_artifacts_present(work, expected_attempt=2) is False


def test_t32_missing_or_corrupt_artifacts_after_choice_never_fall_back_to_legacy(
    tmp_path: Path,
) -> None:
    runner = JobRunner(python_executable=sys.executable)
    record = _record("singlepoint", attempt=1)

    missing = _t32_work(tmp_path / "missing")
    (missing / "WORK" / "00_RUNTIME" / "frozen" / "plan.json").unlink()
    with pytest.raises(FileNotFoundError):
        runner._build_execution_cmd(record, missing, "CCO", {})

    corrupt = _t32_work(tmp_path / "corrupt")
    protocol = corrupt.joinpath(*chain.RUNTIME_PROTOCOL_DIR, chain.ATTEMPT_PROTOCOL_FILENAME)
    protocol.write_text("{not-json", encoding="utf-8")
    with pytest.raises(chain.AttemptProtocolError):
        chain.read_attempt_choice(corrupt)
    with pytest.raises(chain.AttemptProtocolError):
        runner._build_execution_cmd(record, corrupt, "CCO", {})


def test_t32_completed_job_is_rerun_not_continue(tmp_path: Path) -> None:
    manager, store, _runner, _run_root = _t27_manager(tmp_path)
    try:
        work = tmp_path / "task"
        work.mkdir()
        record = JobRecord(
            id=_JOB,
            spec=JobSpec(workflow="mechanism", name="t32-done", input={"smiles": "CCO"}),
            status=JobStatus.COMPLETED,
            work_dir=str(work),
            attempt=1,
        )
        store.create(record)
        with pytest.raises(ValueError, match="FAILED or CANCELLED"):
            manager.continue_job(_JOB)
        assert store.get(_JOB).status == JobStatus.COMPLETED  # type: ignore[union-attr]
    finally:
        manager.shutdown()


# ---------------------------------------------------------------------------
# 13. 07-C singlepoint default activation + builder registry (todo 29)
# ---------------------------------------------------------------------------


def test_t29_default_envelope_scope_is_singlepoint_only() -> None:
    # M2-04d adds the envelope-only recipe workflow to the default freeze scope;
    # the ordinary singlepoint default is unchanged and the C01 catalog closure
    # stays exactly the eight (``RECIPE_WORKFLOWS`` is not a catalog workflow).
    assert chain.DEFAULT_ENVELOPE_WORKFLOWS == frozenset({"singlepoint", "xtb_optimize"}) | chain.RECIPE_WORKFLOWS
    assert chain.ENABLED_WORKFLOWS == _C01_EIGHT | chain.RECIPE_WORKFLOWS
    assert set(acp_freeze.FREEZE_BUILDERS) == {
        "singlepoint",
        "optimize",
        "frequency",
        "scan",
        "irc",
        "casscf",
        "xtb_optimize",
        "OrcaGradient",
        "BoundedRecipe",  # M2-04d envelope-only recipe builder
    }


@requires_orca
def test_t29_ordinary_singlepoint_submit_uses_the_frozen_chain(tmp_path: Path) -> None:
    run_root = tmp_path / "run_root"
    store = JobStore(run_root / "acp_jobs.db")
    runner = JobRunner(python_executable=sys.executable)
    runner.worker_probe_executor = "ok"
    manager = JobManager(run_root, store=store, runner=runner, poll_interval=5)
    try:
        record = manager.submit(
            JobSpec(
                workflow="singlepoint",
                name="t29",
                input={"smiles": "CCO"},
                method={},
            )
        )
        terminal = _t27_wait(manager, store, record.id, timeout=60)

        assert terminal.status == JobStatus.COMPLETED
        work = Path(terminal.work_dir)
        group = chain.load_frozen_group(work, expected_attempt=1)
        assert group.manifest.workflow == "singlepoint"
        choice = chain.read_attempt_choice(work)
        assert choice is not None and choice.entry == chain.EXECUTION_ENTRY_ENVELOPE
        assert (work / "execution_receipt.json").is_file()
        assert (work / "RESULT" / "result_manifest.json").is_file()
        events = (work / "WORK" / "00_RUNTIME" / "events.jsonl").read_text(encoding="utf-8")
        assert "attempt.frozen" in events
        assert "acp_services.worker_bootstrap" in events
        assert "acp.cli" not in events
        assert events.count('"type": "process.starting"') == 1
        assert store.get(record.id).status == JobStatus.COMPLETED  # type: ignore[union-attr]
    finally:
        manager.shutdown()


def test_t29_non_default_workflow_stays_on_the_registered_path(tmp_path: Path) -> None:
    work = tmp_path / "task"
    work.mkdir()
    record = JobRecord(
        id="job-t29-optimize",
        spec=JobSpec(
            workflow="optimize",
            name="t29-optimize",
            input={"smiles": "CCO"},
            method={},
        ),
        work_dir=str(work),
    )
    runner = JobRunner(python_executable=sys.executable)

    cmd = runner._build_execution_cmd(record, work, "CCO", {})

    assert "acp.cli" in " ".join(cmd)
    assert chain.WORKER_MODULE not in " ".join(cmd)
    assert not chain.frozen_group_dir(work).exists()
    assert chain.read_attempt_choice(work) is not None


# ---------------------------------------------------------------------------
# 14. 07-C frozen chain over the remote LSF path (todo 30)
# ---------------------------------------------------------------------------


def _t30_harness(tmp_path: Path):
    pytest.importorskip("paramiko")
    from tests.test_remote_submission_protocol import _RunnerHarness

    return _RunnerHarness(str(tmp_path))


def _t30_record(tmp_path: Path, *, job_id: str = "job-t30") -> tuple[object, Path]:
    work = tmp_path / "runs" / "singlepoint"
    work.mkdir(parents=True)
    (work / "input.xyz").write_text(_T27_XYZ, encoding="utf-8")
    record = JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow="singlepoint",
            name="t30",
            input={"smiles": "CCO"},
            method={},
        ),
        status=JobStatus.QUEUED,
        work_dir=str(work),
    )
    return record, work


def test_t30_synced_release_includes_services_and_matches_local_identity() -> None:
    remote = build_release_manifest(_REPO_ROOT)
    local = acp_freeze.local_release_identity(_REPO_ROOT)
    synced = {path.relative_to(_REPO_ROOT).as_posix() for path in build_sync_file_list(_REPO_ROOT)}
    assert "src/acp_services/worker_bootstrap.py" in synced
    assert remote.release_id == local.release_id
    assert set(remote.files) == set(local.files)
    assert (
        remote.files["src/acp_services/worker_bootstrap.py"]["sha256"]
        == (local.files["src/acp_services/worker_bootstrap.py"]["sha256"])
    )


def test_t30_remote_singlepoint_freezes_before_choice_and_publishes_manifest_last(
    tmp_path: Path, t25_release: acp_freeze.LocalRelease, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.scheduler.remote import ssh as ssh_mod

    harness = _t30_harness(tmp_path)
    record, work = _t30_record(tmp_path)
    monkeypatch.setenv("ACP_REMOTE_ALLOW_UNVERSIONED", "1")
    uploads: list[str] = []
    renames: list[tuple[str, str]] = []
    original_upload = harness.runner._stager.upload_file
    original_rename = harness.runner._stager.rename_remote
    original_resolve = harness.runner._resolve_remote_choice

    def upload(node, local_path, remote_path):
        uploads.append(str(remote_path))
        return original_upload(node, local_path, remote_path)

    def rename(node, src, dst):
        renames.append((str(src), str(dst)))
        return original_rename(node, src, dst)

    def resolve(rec, wd):
        assert chain.frozen_artifacts_present(wd, expected_attempt=rec.attempt)
        return original_resolve(rec, wd)

    harness.runner._stager.upload_file = upload  # type: ignore[method-assign]
    harness.runner._stager.rename_remote = rename  # type: ignore[method-assign]
    harness.runner._resolve_remote_choice = resolve  # type: ignore[method-assign]
    harness.runner._ensure_code_release = (  # type: ignore[method-assign]
        lambda node, event_log, rec: t25_release.release_id
    )
    try:
        with __import__("unittest").mock.patch.object(
            ssh_mod, "_create_client", side_effect=lambda n, timeout=30: harness.client
        ):
            lsf_id = harness.runner.submit_remote(record, JobEventLog(work / "events.jsonl"))
        assert lsf_id and harness.bsub_calls == 1
        choice = chain.read_attempt_choice(work)
        assert choice is not None and choice.entry == chain.EXECUTION_ENTRY_ENVELOPE
        assert choice.release_id == t25_release.release_id
        group = chain.load_frozen_group(work, expected_attempt=1)
        assert group.manifest.release_id == t25_release.release_id
        assert group.manifest.release_digest == t25_release.release_digest
        # The flat group manifest is the final publish rename.
        assert renames[-1][1].endswith("/" + chain.FROZEN_GROUP_MANIFEST_FILENAME)
        flat_manifest = renames[-1][1]
        remote_root = flat_manifest.rsplit("/", 1)[0]
        for name in (
            chain.ENVELOPE_FILENAME,
            chain.PLAN_FILENAME,
            chain.FROZEN_GROUP_MANIFEST_FILENAME,
        ):
            assert f"{remote_root}/{name}" in harness.sftp.files
        script = harness.sftp.files[f"{remote_root}/submit.lsf"].decode("utf-8")
        assert "acp_services.worker_bootstrap" in script
        assert "--require-frozen-group" in script
        events = JobEventLog(work / "events.jsonl").read_all()
        types = [event.get("type") for event in events]
        assert "remote.attempt_frozen" in types
        assert "remote.frozen_group_uploaded" in types
    finally:
        harness.close()


def test_t30_missing_or_tampered_remote_group_is_rejected_before_bsub(
    tmp_path: Path, t25_release: acp_freeze.LocalRelease
) -> None:
    from acp.scheduler.remote import ssh as ssh_mod

    harness = _t30_harness(tmp_path)
    record, work = _t30_record(tmp_path)
    record.attempt = 1
    group = acp_freeze.freeze_singlepoint_attempt(record, work, work / "input.xyz", t25_release)
    assert group.plan_path.is_file()
    group.plan_path.write_text("{tampered", encoding="utf-8")
    chain.write_attempt_choice(
        work,
        chain.AttemptExecutionChoice(
            attempt=1,
            workflow="singlepoint",
            entry=chain.EXECUTION_ENTRY_ENVELOPE,
            protocol=chain.EXECUTION_PROTOCOL_LSF,
            closure="integrated",
            release_id=t25_release.release_id,
        ),
    )
    harness.runner._ensure_code_release = (  # type: ignore[method-assign]
        lambda node, event_log, rec: t25_release.release_id
    )
    try:
        with __import__("unittest").mock.patch.object(
            ssh_mod, "_create_client", side_effect=lambda n, timeout=30: harness.client
        ):
            with pytest.raises(Exception, match="frozen group|missing/corrupt"):
                harness.runner.submit_remote(record, JobEventLog(work / "events.jsonl"))
        assert harness.bsub_calls == 0
    finally:
        harness.close()


def test_t30_interrupted_group_upload_leaves_no_manifest(
    tmp_path: Path, t25_release: acp_freeze.LocalRelease
) -> None:
    harness = _t30_harness(tmp_path)
    record, work = _t30_record(tmp_path)
    acp_freeze.freeze_singlepoint_attempt(record, work, work / "input.xyz", t25_release)
    chain.write_attempt_choice(
        work,
        chain.AttemptExecutionChoice(
            attempt=1,
            workflow="singlepoint",
            entry=chain.EXECUTION_ENTRY_ENVELOPE,
            protocol=chain.EXECUTION_PROTOCOL_LSF,
            closure="integrated",
            release_id=t25_release.release_id,
        ),
    )
    calls = {"n": 0}
    original = harness.runner._stager.upload_file

    def interrupted(node, local_path, remote_path):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("simulated truncated upload")
        return original(node, local_path, remote_path)

    harness.runner._stager.upload_file = interrupted  # type: ignore[method-assign]
    try:
        with pytest.raises(Exception):
            harness.runner._upload_frozen_envelope(
                harness.node,
                "/scratch/test/acp_jobs/interrupted",
                work,
                JobEventLog(work / "events.jsonl"),
                record,
            )
        flat_manifest = "/scratch/test/acp_jobs/interrupted/frozen_group.json"
        assert flat_manifest not in harness.sftp.files
    finally:
        harness.close()


def test_t30_remote_worker_receipt_and_task_result_are_fetched(
    tmp_path: Path,
) -> None:
    import posixpath as _posixpath

    from acp.scheduler.remote import ssh as ssh_mod

    harness = _t30_harness(tmp_path)
    record, work = _t30_record(tmp_path)
    import os

    os.environ["ACP_REMOTE_ALLOW_UNVERSIONED"] = "1"
    try:
        with __import__("unittest").mock.patch.object(
            ssh_mod, "_create_client", side_effect=lambda n, timeout=30: harness.client
        ):
            harness.runner.submit_remote(record, JobEventLog(work / "events.jsonl"))
        remote_dir = (record.result or {}).get(
            "remote_dir"
        ) or "/scratch/test/acp_jobs/t30_singlepoint"
        record.result = {
            **(record.result or {}),
            "node": "compute-01",
            "remote_dir": remote_dir,
        }
        harness.sftp.files[_posixpath.join(remote_dir, "execution_receipt.json")] = b'{"r":1}'
        harness.sftp.files[
            _posixpath.join(remote_dir, "WORK", "00_RUNTIME", "task_result.json")
        ] = b'{"t":1}'

        harness.runner._fetch_worker_artifacts(
            record, JobEventLog(work / "events.jsonl"), harness.node, remote_dir
        )

        assert (work / "execution_receipt.json").read_bytes() == b'{"r":1}'
        assert (work / "WORK" / "00_RUNTIME" / "task_result.json").read_bytes() == b'{"t":1}'
        events = JobEventLog(work / "events.jsonl").read_all()
        fetched = [e for e in events if e.get("type") == "remote.worker_artifacts_fetched"]
        assert fetched and fetched[-1].get("mechanism") == "RemoteStructureCache.fetch"
    finally:
        harness.close()


def test_t30_remote_receipt_completion_cas_and_publication(tmp_path: Path) -> None:
    work, _snapshot, _digest = _t28_attempt(tmp_path)
    chain.write_attempt_choice(
        work,
        chain.AttemptExecutionChoice(
            attempt=1,
            workflow="singlepoint",
            entry=chain.EXECUTION_ENTRY_ENVELOPE,
            protocol=chain.EXECUTION_PROTOCOL_LSF,
            closure="integrated",
            release_id=_RELEASE,
        ),
    )
    run_root = tmp_path / "run_root"
    run_root.mkdir()
    store = JobStore(run_root / "acp_jobs.db")

    class _RemoteStub:
        def poll_remote(self, record, event_log, cancel_event):
            return RemotePollObservation(
                terminal=True,
                exit_code=0,
                final_state={"result": dict(record.result or {})},
            )

        def apply_terminal_side_effects(self, record, event_log, stage_events=()):
            return None

    manager = JobManager(
        run_root,
        store=store,
        runner=JobRunner(python_executable=sys.executable),
        poll_interval=5,
    )
    manager.remote_runner = _RemoteStub()  # type: ignore[assignment]
    record = JobRecord(
        id=_JOB,
        spec=JobSpec(workflow="singlepoint", name="t30", input={"smiles": "CCO"}),
        status=JobStatus.RUNNING,
        work_dir=str(work),
        result={
            "node": "compute-01",
            "remote_dir": "/scratch/test/acp_jobs/t30",
            "remote": {
                "node": "compute-01",
                "relative": "t30",
                "submit_state": "submitted",
            },
        },
    )
    store.create(record)
    try:
        manager._poll_job(_JOB)
        advanced = store.get(_JOB)
        assert advanced is not None
        assert advanced.status == JobStatus.COMPLETED
        assert advanced.revision >= 1
        assert (work / "RESULT" / "result_manifest.json").is_file()
    finally:
        manager.shutdown()
