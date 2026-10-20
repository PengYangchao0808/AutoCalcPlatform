"""B06-6 (todo 78) — self-sufficient worker composition root.

Acceptance clauses:

1. a **new process** assembles the worker from frozen data alone (frozen
   envelope JSON + frozen plan JSON + StorageBinding + a tmp work dir) and runs
   one attempt with an injected fake executor — no parent-process object, no
   jobs DB;
2. success **and** structured failure receipts are verified (executor failure,
   plan-body tamper, unreadable envelope) — typed ``ExecutionError`` entries,
   never a crash trace;
3. the worker path imports no ``acp`` module (AST + new-process import probe);
4. the jobs DB is never opened (SQLite guard in the child + empty
   ``ACP_RUN_ROOT`` stays empty);
5. the remote entry (``build_remote_worker_command``) points at the Services
   worker bootstrap — never a hop through the legacy CLI — and the generated
   remote chain runs end to end under mock (probe executor).

The controller/store equivalence checks live in ``test_services_worker_contract``
and use an isolated DB; the "no jobs DB" probe here is bounded at the worker.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from acp_services.contracts.envelope import (
    CURRENT_ENVELOPE_VERSION,
    ExecutionEnvelope,
    StorageBinding,
    expected_submission_id,
)
from acp_services.runtime.execution_right import (
    ExecutionRightState,
    ExecutionRightVerdict,
    WorkerRefusal,
)
from acp_services.runtime.receipts import advance_job_from_worker_result
from acp_services.runtime.worker import (
    FrozenStep,
    PlanVerificationError,
    TaskExecutionOutcome,
    build_receipt,
    read_task_result,
    read_task_result_qualification,
    receipt_exit_code,
    task_result_path,
    write_receipt_atomic,
)
from acp_services.worker_bootstrap import (
    AssemblyError,
    FrozenPlanVerifier,
    ServicesStepRequirements,
    StorageBindingDecodeError,
    WorkerRuntime,
    assemble_worker,
    freeze_frozen_plan,
    frozen_plan_body_digest,
    frozen_plan_from_dict,
    frozen_plan_to_dict,
    load_and_assemble,
    load_storage_binding,
    main,
)
from tests.test_acp_services_isolation import scan_for_acp_imports

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKER_MODULE = _REPO_ROOT / "src" / "acp_services" / "worker_bootstrap.py"

_JOB = "job-78"
_OWNER = "worker-78"
_FENCE = "fence-78"
_RELEASE = "rel-78"
_RELEASE_DIGEST = "reldig-78"
_PLAN_IDENTITY = "v2:science-identity"
_STEP_IDENTITY = "v2:step-0"
_BINDING_ID = "sb_78"


# ---------------------------------------------------------------------------
# frozen-data fixtures
# ---------------------------------------------------------------------------


class _FakeExecutor:
    """Deterministic in-test executor (no scientific binary)."""

    def __init__(self, outcome: TaskExecutionOutcome | None = None) -> None:
        self.calls = 0
        self.outcome = outcome or TaskExecutionOutcome(
            state="completed", result_digest="sha256:fake-result"
        )

    def execute(self, scope: object) -> TaskExecutionOutcome:
        self.calls += 1
        return self.outcome


def _binding() -> StorageBinding:
    return StorageBinding(
        binding_id=_BINDING_ID,
        tenant_id="tenant-1",
        project_id="proj-1",
        endpoint="local",
        relative_path="proj-1/task-78",
        artifact_digests={"input.xyz": "sha256:artifact"},
    )


def _plan():
    return freeze_frozen_plan(
        payload={"task": "optimize", "charge": 0, "multiplicity": 1},
        steps=(FrozenStep(kind="optimize", payload={"task": "optimize"}),),
        item_symbols=("C", "O"),
        plan_identity=_PLAN_IDENTITY,
        step_identities=(_STEP_IDENTITY,),
    )


def _envelope(
    plan, binding: StorageBinding | None = None, **overrides: object
) -> ExecutionEnvelope:
    kwargs: dict[str, object] = {
        "envelope_version": CURRENT_ENVELOPE_VERSION,
        "job_id": _JOB,
        "attempt": 1,
        "submission_id": expected_submission_id(_JOB, 1),
        "release_id": _RELEASE,
        "release_digest": _RELEASE_DIGEST,
        "owner": _OWNER,
        "fencing_token": _FENCE,
        "plan_digest": plan.identity.plan_digest,
        "plan_identity": plan.identity.plan_identity,
        "step_identities": plan.identity.step_identities,
        "entrypoint": "worker.bootstrap",
        "data_bindings": (binding or _binding(),),
    }
    kwargs.update(overrides)
    return ExecutionEnvelope(**kwargs)  # type: ignore[arg-type]


def _write_frozen(tmp_path: Path, *, tamper_plan: bool = False) -> dict[str, str]:
    binding = _binding()
    plan = _plan()
    envelope = _envelope(plan, binding)
    plan_body = frozen_plan_to_dict(plan)
    if tamper_plan:
        plan_body["payload"]["task"] = "scan"  # body no longer matches declared digest

    env_path = tmp_path / "envelope.json"
    plan_path = tmp_path / "plan.json"
    bind_path = tmp_path / "binding.json"
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    env_path.write_text(json.dumps(envelope.to_dict()), encoding="utf-8")
    plan_path.write_text(json.dumps(plan_body), encoding="utf-8")
    bind_path.write_text(json.dumps(binding.to_dict()), encoding="utf-8")
    return {
        "envelope": str(env_path),
        "plan": str(plan_path),
        "binding": str(bind_path),
        "work_dir": str(work_dir),
        "receipt": str(work_dir / "execution_receipt.json"),
    }


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


def _run(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


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


def _code_surface(source: str) -> str:
    """Code identifiers, import names and non-docstring string constants."""
    tree = ast.parse(source)
    doc_ids = _docstring_ids(tree)
    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            parts.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts.append(node.module or "")
        elif isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts.append(node.attr)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in doc_ids
        ):
            parts.append(node.value)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# codec + Services-native seams (in-process contract)
# ---------------------------------------------------------------------------


def test_composition_root_seams_satisfy_ports() -> None:
    from acp_services.contracts.injection import StepRequirementPort
    from acp_services.runtime.worker import PlanVerificationPort, TaskExecutorPort

    assert isinstance(FrozenPlanVerifier(), PlanVerificationPort)
    assert isinstance(ServicesStepRequirements(), StepRequirementPort)
    assert isinstance(_FakeExecutor(), TaskExecutorPort)


def test_frozen_plan_codec_round_trips_body_and_identity() -> None:
    plan = _plan()
    decoded = frozen_plan_from_dict(frozen_plan_to_dict(plan))
    assert decoded.payload == dict(plan.payload)
    assert tuple((s.kind, dict(s.payload)) for s in decoded.steps) == (
        ("optimize", {"task": "optimize"}),
    )
    assert decoded.item_symbols == ("C", "O")
    assert decoded.identity is not None
    assert decoded.identity.plan_digest == frozen_plan_body_digest(plan)
    assert decoded.identity.plan_identity == _PLAN_IDENTITY
    assert decoded.identity.step_identities == (_STEP_IDENTITY,)


def test_frozen_plan_verifier_recomputes_body_digest() -> None:
    plan = _plan()
    identity = FrozenPlanVerifier().verify(plan)
    assert identity.plan_digest == frozen_plan_body_digest(plan)
    assert identity == plan.identity

    # A tampered body is rejected by the recompute before any execution.
    tampered = frozen_plan_from_dict({**frozen_plan_to_dict(plan), "payload": {"task": "scan"}})
    with pytest.raises(PlanVerificationError):
        FrozenPlanVerifier().verify(tampered)


def test_services_step_requirements_coerces_unknown_kind_to_satisfied() -> None:
    requirements = ServicesStepRequirements()
    # 'optimize' has no registered prerequisite -> satisfied.
    assert requirements.evaluate_prerequisite((), "optimize", ("C", "O")).satisfied is True
    # A kind outside StepKind is also treated as satisfied (mirrors the table).
    assert requirements.evaluate_prerequisite((), "irc", ()).satisfied is True


def test_assemble_worker_rejects_binding_not_declared_by_envelope() -> None:
    plan = _plan()
    envelope = _envelope(plan)
    foreign = StorageBinding(binding_id="sb_other", relative_path="proj/other")
    with pytest.raises(AssemblyError):
        assemble_worker(
            envelope=envelope,
            plan=plan,
            work_dir="/tmp/unused",
            receipt_path="/tmp/unused/r.json",
            storage_binding=foreign,
        )


def test_assemble_worker_rejects_non_worker_entrypoint() -> None:
    plan = _plan()
    envelope = _envelope(plan, entrypoint="worker.reconcile")
    with pytest.raises(AssemblyError):
        assemble_worker(envelope=envelope, plan=plan, work_dir="/tmp/unused")


def test_load_storage_binding_invalid_payload_is_typed() -> None:
    with pytest.raises(StorageBindingDecodeError) as excinfo:
        load_storage_binding("/nonexistent/binding.json")
    assert "cannot read storage binding" in str(excinfo.value)


# ---------------------------------------------------------------------------
# new-process assembly (fake executor; no acp, no DB)
# ---------------------------------------------------------------------------

_CHILD_SCRIPT = """
import json
import sqlite3
import sys

_calls = []


def _guard(*args, **kwargs):
    _calls.append(args)
    raise AssertionError("worker opened a database")


sqlite3.connect = _guard

from acp_services.runtime.worker import TaskExecutionOutcome
from acp_services.worker_bootstrap import load_and_assemble


class _Fake:
    def __init__(self):
        self.calls = 0

    def execute(self, scope):
        self.calls += 1
        return TaskExecutionOutcome(state="completed", result_digest="sha256:fake-result")


runtime = load_and_assemble(
    envelope_path={envelope!r},
    plan_path={plan!r},
    work_dir={work_dir!r},
    receipt_path={receipt!r},
    binding_path={binding!r},
    executor=_Fake(),
)
receipt = runtime.run()
acp_modules = sorted(m for m in sys.modules if m == "acp" or m.startswith("acp."))
print(json.dumps({{
    "receipt": receipt.to_dict(),
    "acp_modules": acp_modules,
    "sqlite_calls": len(_calls),
}}))
"""


def test_new_process_assembles_from_frozen_data_without_acp_or_db(tmp_path: Path) -> None:
    frozen = _write_frozen(tmp_path)
    run_root = tmp_path / "empty_run_root"
    run_root.mkdir()
    script = _CHILD_SCRIPT.format(**frozen)
    result = _run([sys.executable, "-c", script], _child_env(run_root))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    receipt = payload["receipt"]
    assert receipt["state"] == "completed"
    assert receipt["job_id"] == _JOB
    assert receipt["submission_id"] == expected_submission_id(_JOB, 1)
    assert receipt["plan_identity"] == _PLAN_IDENTITY
    assert payload["acp_modules"] == [], f"acp leaked into the worker: {payload['acp_modules']}"
    assert payload["sqlite_calls"] == 0
    # the receipt is the only artifact; the run root stays empty (no jobs DB)
    assert Path(frozen["receipt"]).is_file()
    assert list(run_root.rglob("*")) == []


# ---------------------------------------------------------------------------
# process entry — success + structured failure receipts
# ---------------------------------------------------------------------------


def test_main_probe_success_writes_completed_receipt(tmp_path: Path) -> None:
    frozen = _write_frozen(tmp_path)
    run_root = tmp_path / "rr_ok"
    run_root.mkdir()
    cmd = [
        sys.executable,
        "-m",
        "acp_services.worker_bootstrap",
        "--envelope",
        frozen["envelope"],
        "--plan",
        frozen["plan"],
        "--work-dir",
        frozen["work_dir"],
        "--receipt",
        frozen["receipt"],
        "--binding",
        frozen["binding"],
        "--probe-executor",
        "ok",
    ]
    result = _run(cmd, _child_env(run_root))
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    assert receipt["state"] == "completed"
    assert "Traceback" not in result.stderr
    assert list(run_root.rglob("*")) == []


def test_main_probe_failure_writes_structured_failed_receipt(tmp_path: Path) -> None:
    frozen = _write_frozen(tmp_path)
    run_root = tmp_path / "rr_fail"
    run_root.mkdir()
    cmd = [
        sys.executable,
        "-m",
        "acp_services.worker_bootstrap",
        "--envelope",
        frozen["envelope"],
        "--plan",
        frozen["plan"],
        "--work-dir",
        frozen["work_dir"],
        "--receipt",
        frozen["receipt"],
        "--binding",
        frozen["binding"],
        "--probe-executor",
        "fail",
    ]
    result = _run(cmd, _child_env(run_root))
    assert result.returncode == 1, result.stderr
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    assert receipt["state"] == "failed"
    assert receipt["errors"][0]["code"] == "probe_failure"
    assert "Traceback" not in result.stderr
    assert list(run_root.rglob("*")) == []


def test_main_tampered_plan_body_yields_typed_failure_receipt(tmp_path: Path) -> None:
    frozen = _write_frozen(tmp_path, tamper_plan=True)
    run_root = tmp_path / "rr_tamper"
    run_root.mkdir()
    cmd = [
        sys.executable,
        "-m",
        "acp_services.worker_bootstrap",
        "--envelope",
        frozen["envelope"],
        "--plan",
        frozen["plan"],
        "--work-dir",
        frozen["work_dir"],
        "--receipt",
        frozen["receipt"],
        "--binding",
        frozen["binding"],
        "--probe-executor",
        "ok",
    ]
    result = _run(cmd, _child_env(run_root))
    assert result.returncode == 1, result.stderr
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    assert receipt["state"] == "failed"
    assert receipt["errors"][0]["code"] == "plan_verification_failed"
    assert "Traceback" not in result.stderr


def test_main_unreadable_envelope_writes_assembly_error_document(tmp_path: Path) -> None:
    frozen = _write_frozen(tmp_path)
    bad_envelope = tmp_path / "bad_envelope.json"
    bad_envelope.write_text("{not json", encoding="utf-8")
    receipt = tmp_path / "work" / "assembly_receipt.json"
    cmd = [
        sys.executable,
        "-m",
        "acp_services.worker_bootstrap",
        "--envelope",
        str(bad_envelope),
        "--plan",
        frozen["plan"],
        "--work-dir",
        frozen["work_dir"],
        "--receipt",
        str(receipt),
    ]
    result = _run(cmd, _child_env(tmp_path / "rr_bad"))
    assert result.returncode == 2, result.stderr
    document = json.loads(receipt.read_text(encoding="utf-8"))
    assert document["schema"] == "worker_assembly_error"
    assert document["state"] == "failed"
    assert document["errors"][0]["code"] == "envelope_unreadable"
    assert "Traceback" not in result.stderr


# ---------------------------------------------------------------------------
# remote entry: Services bootstrap, never the legacy CLI
# ---------------------------------------------------------------------------


def test_remote_worker_command_targets_services_bootstrap() -> None:
    from acp.scheduler.remote.script_gen import build_remote_worker_command

    cmd = build_remote_worker_command(
        envelope_path="/remote/env.json",
        plan_path="/remote/plan.json",
        work_dir="/remote/work",
        receipt_path="/remote/work/receipt.json",
        binding_path="/remote/binding.json",
        python_executable="python3.11",
    )
    assert cmd[:3] == ["python3.11", "-m", "acp_services.worker_bootstrap"]
    assert "acp.cli" not in " ".join(cmd)
    assert "--envelope" in cmd and "--plan" in cmd


def test_remote_mock_chain_runs_generated_worker_command(tmp_path: Path) -> None:
    from acp.scheduler.remote.script_gen import build_remote_worker_command

    frozen = _write_frozen(tmp_path)
    run_root = tmp_path / "rr_remote"
    run_root.mkdir()
    cmd = build_remote_worker_command(
        envelope_path=frozen["envelope"],
        plan_path=frozen["plan"],
        work_dir=frozen["work_dir"],
        receipt_path=frozen["receipt"],
        binding_path=frozen["binding"],
        python_executable=sys.executable,
        probe_executor="ok",
    )
    result = _run(cmd, _child_env(run_root))
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    assert receipt["state"] == "completed"
    assert list(run_root.rglob("*")) == []


# ---------------------------------------------------------------------------
# static isolation: no acp import, no DB / run-root access on the worker path
# ---------------------------------------------------------------------------


def test_worker_bootstrap_has_no_acp_import() -> None:
    source = _WORKER_MODULE.read_text(encoding="utf-8")
    assert scan_for_acp_imports(source) == []
    assert "acp.cli" not in _code_surface(source)


def test_worker_bootstrap_never_touches_jobs_db_or_run_root() -> None:
    surface = _code_surface(_WORKER_MODULE.read_text(encoding="utf-8"))
    for marker in (
        "sqlite3",
        "acp_jobs.db",
        "jobs.db",
        "ACP_RUN_ROOT",
        "resolve_run_root",
        "JobStore",
    ):
        assert marker not in surface, f"worker path references {marker!r}"


def test_worker_runtime_never_invokes_executor_twice(tmp_path: Path) -> None:
    frozen = _write_frozen(tmp_path)
    executor = _FakeExecutor()
    runtime: WorkerRuntime = load_and_assemble(
        envelope_path=frozen["envelope"],
        plan_path=frozen["plan"],
        work_dir=frozen["work_dir"],
        receipt_path=frozen["receipt"],
        binding_path=frozen["binding"],
        executor=executor,
    )
    first = runtime.run()
    second = runtime.run()
    assert executor.calls == 1, "re-delivery must not re-execute"
    assert second == first


# ---------------------------------------------------------------------------
# D01 — real worker subprocesses: the frozen status/exit/job/reuse matrix
# ---------------------------------------------------------------------------


def _run_probe(tmp_path: Path, mode: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    frozen = _write_frozen(tmp_path)
    run_root = tmp_path / f"rr_{mode.replace('-', '_')}"
    run_root.mkdir()
    cmd = [
        sys.executable,
        "-m",
        "acp_services.worker_bootstrap",
        "--envelope",
        frozen["envelope"],
        "--plan",
        frozen["plan"],
        "--work-dir",
        frozen["work_dir"],
        "--receipt",
        frozen["receipt"],
        "--binding",
        frozen["binding"],
        "--probe-executor",
        mode,
    ]
    result = _run(cmd, _child_env(run_root))
    assert "Traceback" not in result.stderr
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    return result, receipt


def test_d01_subprocess_failed_row_a(tmp_path: Path) -> None:
    result, receipt = _run_probe(tmp_path, "fail")
    assert result.returncode == 1
    assert receipt["state"] == "failed"
    assert receipt["errors"][0]["code"] == "probe_failure"
    evidence = receipt["evidence_digests"]
    assert evidence["truth_table_row"] == "a"
    assert evidence["normal_reuse"] == "false"
    assert evidence["publish"] == "false"
    assert evidence["publish_policy"] == "none"
    assert evidence["job_status"] == "failed"
    assert evidence["exit_code"] == "1"
    document = read_task_result(tmp_path / "work")
    assert document is not None
    assert document["status"] == "failed"
    assert document["qualification"] == "task_failed"
    assert evidence["task_result_digest"] == document["content_digest"]


def test_d01_subprocess_partial_missing_fact_row_d(tmp_path: Path) -> None:
    result, receipt = _run_probe(tmp_path, "partial-missing")
    assert result.returncode == 1
    assert receipt["state"] == "failed"
    evidence = receipt["evidence_digests"]
    assert evidence["truth_table_row"] == "d"
    assert evidence["qualification"] == "incomplete_result"
    assert evidence["normal_reuse"] == "false"
    assert evidence["publish"] == "false"
    assert evidence["job_status"] == "failed"
    assert evidence["exit_code"] == "1"
    assert "coordinates" in evidence["missing_required_facts"]
    codes = [entry["code"] if isinstance(entry, dict) else entry for entry in receipt["errors"]]
    assert "incomplete_result" in codes

    document = read_task_result(tmp_path / "work")
    assert document is not None
    assert document["truth_table_row"] == "d"
    assert document["missing_required_facts"]


def test_d01_subprocess_partial_satisfied_row_c(tmp_path: Path) -> None:
    result, receipt = _run_probe(tmp_path, "partial")
    assert result.returncode == 0
    assert receipt["state"] == "completed"
    evidence = receipt["evidence_digests"]
    assert evidence["truth_table_row"] == "c"
    assert evidence["qualification"] == "partial_result"
    assert evidence["partial_result"] == "true"
    assert evidence["normal_reuse"] == "false"
    assert evidence["publish"] == "true"
    assert evidence["publish_policy"] == "partial"
    assert evidence["job_status"] == "completed"
    assert evidence["exit_code"] == "0"

    qualification = read_task_result_qualification(tmp_path / "work")
    assert qualification is not None
    assert qualification.partial_result is True
    assert qualification.normal_reuse is False
    assert qualification.publish is True
    assert qualification.publish_policy == "partial"
    assert evidence["task_result_digest"] == qualification.content_digest
    assert task_result_path(tmp_path / "work").is_file()


def test_d01_subprocess_complete_row_b(tmp_path: Path) -> None:
    result, receipt = _run_probe(tmp_path, "ok")
    assert result.returncode == 0
    assert receipt["state"] == "completed"
    evidence = receipt["evidence_digests"]
    assert evidence["truth_table_row"] == "b"
    assert evidence["normal_reuse"] == "true"
    assert evidence["publish_policy"] == "normal"
    assert evidence["job_status"] == "completed"
    assert evidence["exit_code"] == "0"


# ---------------------------------------------------------------------------
# D02 — conflicting failure-path receipts + injected execution right
# ---------------------------------------------------------------------------


class _RaisingExecutor:
    """Executor that fails hard (drives ``WorkerRuntime.run``'s failure seam)."""

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, scope: object) -> TaskExecutionOutcome:
        self.calls += 1
        raise RuntimeError("executor exploded")


class _FakeExecutionRight:
    """Configurable execution-right port (task 24 supplies the persistent one)."""

    def __init__(
        self,
        *,
        acquire: ExecutionRightState = ExecutionRightState.HELD,
        verify: ExecutionRightState | None = None,
    ) -> None:
        self.acquire_verdict = ExecutionRightVerdict(acquire)
        self.verify_verdict = ExecutionRightVerdict(
            acquire if verify is None else verify
        )
        self.calls: list[str] = []

    def acquire(self, envelope: object) -> ExecutionRightVerdict:
        self.calls.append("acquire")
        return self.acquire_verdict

    def verify(self, envelope: object) -> ExecutionRightVerdict:
        self.calls.append("verify")
        return self.verify_verdict


class _RecordingAdvancePort:
    """Minimal ``JobStateAdvancePort`` stand-in that records CAS attempts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def advance(self, job_id: str, **kwargs: object) -> str:
        self.calls.append((job_id, dict(kwargs)))
        return "advanced"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_foreign_receipt(receipt_path: Path, *, attempt: int = 2) -> None:
    foreign = _envelope(
        _plan(), attempt=attempt, submission_id=expected_submission_id(_JOB, attempt)
    )
    write_receipt_atomic(
        receipt_path,
        build_receipt(
            foreign, TaskExecutionOutcome(state="completed", result_digest="sha256:old")
        ),
    )


def _main_args(frozen: dict[str, str], probe: str = "ok") -> list[str]:
    return [
        "--envelope",
        frozen["envelope"],
        "--plan",
        frozen["plan"],
        "--work-dir",
        frozen["work_dir"],
        "--receipt",
        frozen["receipt"],
        "--binding",
        frozen["binding"],
        "--probe-executor",
        probe,
    ]


def test_holder_conflicting_receipt_preserves_bytes_and_writes_conflict_doc(
    tmp_path: Path,
) -> None:
    frozen = _write_frozen(tmp_path)
    receipt_path = Path(frozen["receipt"])
    _seed_foreign_receipt(receipt_path)
    sha_before = _sha256(receipt_path)

    executor = _RaisingExecutor()
    runtime = assemble_worker(
        envelope=_envelope(_plan()),
        plan=_plan(),
        work_dir=frozen["work_dir"],
        receipt_path=receipt_path,
        storage_binding=_binding(),
        executor=executor,
    )
    result = runtime.run()

    assert not isinstance(result, WorkerRefusal)
    assert result.attempt == 1
    assert result.state == "failed"
    assert result.errors[0].code == "receipt_conflict"  # type: ignore[union-attr]
    assert receipt_exit_code(result) == 1
    assert executor.calls == 0, "a conflicting receipt must be resolved before execution"
    # the original receipt is evidence: bytes are untouched
    assert _sha256(receipt_path) == sha_before

    conflict_path = receipt_path.with_name(receipt_path.name + ".conflict.json")
    document = json.loads(conflict_path.read_text(encoding="utf-8"))
    assert document["schema"] == "worker_receipt_conflict"
    assert document["code"] == "receipt_identity_mismatch"
    assert document["conflict_field"] == "attempt"
    assert document["current_identity"]["attempt"] == 1
    assert document["expected_identity"]["attempt"] == 1
    assert document["existing_identity"]["attempt"] == 2
    assert document["existing_receipt_sha256"] == sha_before
    assert document["current_receipt"]["attempt"] == 1
    assert document["current_receipt"]["state"] == "failed"
    # no authoritative task_result for a preserved conflict
    assert not task_result_path(frozen["work_dir"]).exists()


def test_main_holder_conflicting_receipt_exits_one_and_preserves_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    frozen = _write_frozen(tmp_path)
    receipt_path = Path(frozen["receipt"])
    _seed_foreign_receipt(receipt_path)
    sha_before = _sha256(receipt_path)

    code = main(_main_args(frozen, probe="ok"))

    assert code == 1
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["attempt"] == 1
    assert printed["state"] == "failed"
    assert printed["errors"][0]["code"] == "receipt_conflict"
    assert _sha256(receipt_path) == sha_before
    assert receipt_path.with_name(receipt_path.name + ".conflict.json").is_file()


def test_loser_not_held_writes_only_refusal_document_and_exits_three(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    frozen = _write_frozen(tmp_path)
    receipt_path = Path(frozen["receipt"])
    right = _FakeExecutionRight(acquire=ExecutionRightState.NOT_HELD)

    code = main(_main_args(frozen, probe="fail"), execution_right=right)

    assert code == 3, "a non-holder refusal is a distinct non-zero retry code"
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["schema"] == "worker_execution_refusal"
    assert printed["code"] == "execution_right_not_held"
    assert printed["state"] == "wait"
    assert printed["authoritative"] is False
    assert printed["receipt"] is None
    assert printed["current_identity"]["attempt"] == 1
    # no authoritative receipt/task_result; only the separate diagnostic document
    assert not receipt_path.exists()
    assert not task_result_path(frozen["work_dir"]).exists()
    assert sorted(p.name for p in Path(frozen["work_dir"]).iterdir()) == [
        receipt_path.name + ".refused.json"
    ]


def test_lost_right_at_failure_seam_writes_no_authoritative_receipt(tmp_path: Path) -> None:
    frozen = _write_frozen(tmp_path)
    receipt_path = Path(frozen["receipt"])
    right = _FakeExecutionRight(
        acquire=ExecutionRightState.HELD, verify=ExecutionRightState.LOST
    )
    runtime = assemble_worker(
        envelope=_envelope(_plan()),
        plan=_plan(),
        work_dir=frozen["work_dir"],
        receipt_path=receipt_path,
        storage_binding=_binding(),
        executor=_RaisingExecutor(),
        execution_right=right,
    )

    result = runtime.run()

    assert isinstance(result, WorkerRefusal)
    assert result.state == "retry"
    assert result.code == "execution_right_lost"
    assert result.exit_code == 3
    assert right.calls == ["acquire", "verify"]
    assert not receipt_path.exists()
    assert not task_result_path(frozen["work_dir"]).exists()
    document = json.loads(
        receipt_path.with_name(receipt_path.name + ".refused.json").read_text(
            encoding="utf-8"
        )
    )
    assert document["code"] == "execution_right_lost"
    assert document["receipt"] is None


def test_unreadable_receipt_is_typed_conflict_not_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    frozen = _write_frozen(tmp_path)
    receipt_path = Path(frozen["receipt"])
    receipt_path.write_text("{not json", encoding="utf-8")
    sha_before = _sha256(receipt_path)

    code = main(_main_args(frozen, probe="ok"))

    assert code == 1
    output = capsys.readouterr()
    assert "Traceback" not in output.err
    printed = json.loads(output.out.strip().splitlines()[-1])
    assert printed["attempt"] == 1
    assert printed["state"] == "failed"
    assert _sha256(receipt_path) == sha_before
    document = json.loads(
        receipt_path.with_name(receipt_path.name + ".conflict.json").read_text(
            encoding="utf-8"
        )
    )
    assert document["code"] == "unreadable_receipt"
    assert document["existing_identity"] is None
    assert document["existing_receipt_sha256"] == sha_before


def test_duplicate_failure_delivery_same_identity_is_idempotent_without_conflict(
    tmp_path: Path,
) -> None:
    frozen = _write_frozen(tmp_path)
    receipt_path = Path(frozen["receipt"])
    executor = _FakeExecutor(
        outcome=TaskExecutionOutcome(state="failed", result_digest="sha256:failed")
    )
    runtime = assemble_worker(
        envelope=_envelope(_plan()),
        plan=_plan(),
        work_dir=frozen["work_dir"],
        receipt_path=receipt_path,
        storage_binding=_binding(),
        executor=executor,
    )

    first = runtime.run()
    second = runtime.run()

    assert first == second
    assert executor.calls == 1
    assert first.state == "failed"
    assert not receipt_path.with_name(receipt_path.name + ".conflict.json").exists()


def test_refusal_never_triggers_job_failure_cas_but_receipt_does(tmp_path: Path) -> None:
    frozen = _write_frozen(tmp_path)
    envelope = _envelope(_plan())
    refusal_runtime = assemble_worker(
        envelope=envelope,
        plan=_plan(),
        work_dir=frozen["work_dir"],
        receipt_path=Path(frozen["receipt"]),
        storage_binding=_binding(),
        executor=_FakeExecutor(),
        execution_right=_FakeExecutionRight(acquire=ExecutionRightState.NOT_HELD),
    )
    refusal = refusal_runtime.run()
    assert isinstance(refusal, WorkerRefusal)

    kwargs = {
        "current_attempt": 1,
        "current_submission_id": expected_submission_id(_JOB, 1),
        "current_release_id": _RELEASE,
        "owner_token": _OWNER,
        "fencing_token": _FENCE,
        "expected_revision": 1,
        "status": "failed",
    }
    port = _RecordingAdvancePort()
    consumed = advance_job_from_worker_result(port, envelope, refusal, **kwargs)
    assert consumed is None
    assert port.calls == [], "a non-holder refusal must not trigger a job CAS"

    # positive control: the holder's authoritative failed receipt does CAS once
    holder_frozen = _write_frozen(tmp_path / "holder")
    holder_runtime = assemble_worker(
        envelope=envelope,
        plan=_plan(),
        work_dir=holder_frozen["work_dir"],
        receipt_path=Path(holder_frozen["receipt"]),
        storage_binding=_binding(),
        executor=_RaisingExecutor(),
    )
    failed_receipt = holder_runtime.run()
    assert not isinstance(failed_receipt, WorkerRefusal)
    applied = advance_job_from_worker_result(port, envelope, failed_receipt, **kwargs)
    assert applied == "advanced"
    assert len(port.calls) == 1
    assert port.calls[0][1]["status"] == "failed"


def test_assembly_failure_with_conflicting_receipt_preserves_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    frozen = _write_frozen(tmp_path)
    receipt_path = Path(frozen["receipt"])
    _seed_foreign_receipt(receipt_path)
    sha_before = _sha256(receipt_path)
    undeclared = StorageBinding(
        binding_id="sb_undeclared",
        tenant_id="tenant-1",
        project_id="proj-1",
        endpoint="local",
        relative_path="proj-1/other",
        artifact_digests={"input.xyz": "sha256:other"},
    )
    undeclared_path = tmp_path / "undeclared_binding.json"
    undeclared_path.write_text(json.dumps(undeclared.to_dict()), encoding="utf-8")
    args = _main_args(frozen, probe="ok")
    args[args.index("--binding") + 1] = str(undeclared_path)

    code = main(args)

    assert code == 1
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["state"] == "failed"
    assert printed["errors"][0]["code"] == "assembly_failed"
    assert printed["attempt"] == 1
    assert _sha256(receipt_path) == sha_before
    document = json.loads(
        receipt_path.with_name(receipt_path.name + ".conflict.json").read_text(
            encoding="utf-8"
        )
    )
    assert document["schema"] == "worker_receipt_conflict"
    assert document["code"] == "receipt_identity_mismatch"
    assert document["existing_identity"]["attempt"] == 2


def test_structurally_invalid_receipt_is_typed_conflict(tmp_path: Path) -> None:
    frozen = _write_frozen(tmp_path)
    receipt_path = Path(frozen["receipt"])
    receipt_path.write_text(
        json.dumps({"receipt_version": 1, "state": "not-a-receipt-state"}),
        encoding="utf-8",
    )
    sha_before = _sha256(receipt_path)

    result = assemble_worker(
        envelope=_envelope(_plan()),
        plan=_plan(),
        work_dir=frozen["work_dir"],
        receipt_path=receipt_path,
        storage_binding=_binding(),
        executor=_FakeExecutor(),
    ).run()

    assert not isinstance(result, WorkerRefusal)
    assert result.attempt == 1
    assert result.state == "failed"
    assert _sha256(receipt_path) == sha_before
    document = json.loads(
        receipt_path.with_name(receipt_path.name + ".conflict.json").read_text(
            encoding="utf-8"
        )
    )
    assert document["code"] == "unreadable_receipt"
    assert document["existing_identity"] is None
