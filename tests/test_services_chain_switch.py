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
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
    StorageBinding,
    expected_submission_id,
)
from acp_services.runtime import attempt_freeze as af
from acp_services.runtime import execution_chain as chain
from acp_services.runtime.attempt_freeze import (
    AttemptFreezeError,
    AttemptFreezeSpec,
    AttemptLockClaim,
    AttemptLockExecutionRight,
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
from acp_services.runtime.worker import FrozenStep, TaskExecutionOutcome, build_receipt
from acp_services.worker_bootstrap import (
    assemble_worker,
    freeze_frozen_plan,
    frozen_plan_body_digest,
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
    receipt_path = work / "execution_receipt.json"
    unbound = json.loads(receipt_path.read_text(encoding="utf-8"))
    # The pre-existing unbound-assembly diagnostic — NOT an authoritative receipt.
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

    assert right.acquire(envelope).held is True  # verifiably dead -> checked steal
    assert right.verify(envelope).held is True
    assert right.release(envelope) is True


def test_claim_recycled_pid_is_detected_as_dead(tmp_path: Path) -> None:
    work = tmp_path / "task"
    envelope = _envelope("singlepoint")
    lock_path = attempt_lock_path(work)
    # Same pid, wrong /proc start-time: the recorded process ended and the pid
    # was recycled — kill(0) alone would lie.
    _write_foreign_claim(lock_path, pid=os.getpid(), pid_starttime="999999999")

    verdict = AttemptLockExecutionRight(lock_path).acquire(envelope)
    assert verdict.held is True


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
    work = tmp_path / "task"
    work.mkdir()
    group = write_frozen_attempt(work, _freeze_spec("singlepoint"))
    run_root = tmp_path / "empty_run_root"
    run_root.mkdir()

    base_argv = [
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
    ]
    procs = [
        subprocess.Popen(
            base_argv,
            cwd=str(_REPO_ROOT),
            env=_child_env(run_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outs = [proc.communicate(timeout=120) for proc in procs]
    assert sorted(proc.returncode for proc in procs) == [0, 3], outs

    receipt_path = work / "execution_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["state"] == "completed"
    refusal_doc = json.loads(
        receipt_path.with_name(receipt_path.name + ".refused.json").read_text(encoding="utf-8")
    )
    assert refusal_doc["code"] == "execution_right_not_held"
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
