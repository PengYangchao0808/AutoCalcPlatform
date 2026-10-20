"""B06-2 (todo 31) — frozen-worker + controller receipt-gate contract tests.

Covers the acceptance clauses:

1. worker AST ban — the worker execution boundary (``worker_bootstrap`` + the
   static ``acp_services`` import closure of ``worker``/``execution_chain``)
   imports no ``acp``, no ``sqlite3``/jobs-store, and contains no ``attempt``
   increment / status write;
2. receipt validation matrix — stale attempt / stale submission / stale release /
   wrong owner / wrong fencing / digest mismatch / forged submission rejected;
3. the controller advances only after eligibility (CAS port called exactly once
   on acceptance, never on rejection; idempotent re-delivery cannot advance twice);
4. the worker opens no jobs DB file.

The negative scanner snippets are asserted too, so the guard itself is tested.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import io
import json
import re
import sqlite3
import subprocess
import sys
import tokenize
from pathlib import Path
from types import SimpleNamespace

import pytest

from acp_services.contracts.envelope import (
    CURRENT_ENVELOPE_VERSION,
    ExecutionEnvelope,
    ExecutionReceipt,
    StorageBinding,
    TaskContextSerializationError,
    expected_submission_id,
)
from acp_services.contracts.injection import JobStateAdvancePort, StepRequirementPort
from acp_services.runtime.plan_contracts import CalculationResult, StepKind
from acp_services.runtime.receipt_identity import compare_receipt_identity
from acp_services.runtime.receipts import (
    ReceiptRejectedError,
    advance_job_state,
    verify_receipt,
)
from acp_services.runtime.step_requirements import (
    MISSING_REQUIREMENT,
    PriorStep,
    evaluate_prerequisite,
    is_partial_result,
)
from acp_services.runtime.worker import (
    JOB_STATUS_BY_RECEIPT_STATE,
    QUALIFICATION_COMPLETE,
    QUALIFICATION_INCOMPLETE,
    QUALIFICATION_PARTIAL,
    QUALIFICATION_TASK_FAILED,
    TASK_COMPLETION_AUTHORITIES,
    DeclaredPlanVerifier,
    DependencyNotSatisfiedError,
    FrozenPlan,
    FrozenPlanIdentity,
    FrozenStep,
    FrozenWorker,
    PlanVerificationError,
    PlanVerificationPort,
    ReceiptConflictError,
    TaskExecutionOutcome,
    TaskExecutorPort,
    TaskResultIntegrityError,
    build_receipt,
    decide_task_result,
    job_status_for_receipt,
    outcome_from_task_result,
    read_receipt,
    read_task_result,
    read_task_result_qualification,
    receipt_exit_code,
    task_result_path,
    write_receipt_atomic,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVICES_ROOT = _REPO_ROOT / "src" / "acp_services"

#: Worker execution boundary: the subprocess entry point, the frozen-execution
#: entry selector, and every ``acp_services`` module reachable from them by
#: static import.  Only these modules run (or decide what runs) in the worker
#: process; the rest of the Services package is controller-side and owns its
#: own ledgers (``admission``/``quota`` SQLite reservations, the ``isolation``
#: deny-list literal), so the "no acp / no jobs DB" independence probe is
#: scoped to this closure rather than ``runtime/**``.
_WORKER_BOUNDARY_ROOTS = (
    _SERVICES_ROOT / "worker_bootstrap.py",
    _SERVICES_ROOT / "runtime" / "worker.py",
    _SERVICES_ROOT / "runtime" / "execution_chain.py",
)

_JOB = "job-1"
_PLAN_DIGEST = "v2:plan-digest"
_PLAN_IDENTITY = "v2:plan-identity"
_STEP_IDENTITY = "v2:step-0"


# ---------------------------------------------------------------------------
# fixtures / fakes
# ---------------------------------------------------------------------------


def _envelope(**overrides: object) -> ExecutionEnvelope:
    kwargs: dict[str, object] = {
        "envelope_version": CURRENT_ENVELOPE_VERSION,
        "job_id": _JOB,
        "attempt": 1,
        "submission_id": expected_submission_id(_JOB, 1),
        "release_id": "rel-1",
        "release_digest": "reldig-1",
        "owner": "worker-1",
        "fencing_token": "fence-1",
        "plan_digest": _PLAN_DIGEST,
        "plan_identity": _PLAN_IDENTITY,
        "step_identities": (_STEP_IDENTITY,),
    }
    kwargs.update(overrides)
    return ExecutionEnvelope(**kwargs)  # type: ignore[arg-type]


def _plan(envelope: ExecutionEnvelope, **overrides: object) -> FrozenPlan:
    kwargs: dict[str, object] = {
        "payload": {"task": "optimize"},
        "steps": (FrozenStep("optimize"),),
        "item_symbols": ("C", "O"),
        "identity": FrozenPlanIdentity(
            envelope.plan_digest, envelope.plan_identity, envelope.step_identities
        ),
    }
    kwargs.update(overrides)
    return FrozenPlan(**kwargs)  # type: ignore[arg-type]


class _FakeExecutor:
    def __init__(self, outcome: TaskExecutionOutcome | None = None) -> None:
        self.calls = 0
        self.outcome = outcome or TaskExecutionOutcome(
            state="completed", result_digest="sha256:result"
        )

    def execute(self, scope: object) -> TaskExecutionOutcome:
        self.calls += 1
        return self.outcome


class _Outcome:
    def __init__(self, satisfied: bool, reason: str = "") -> None:
        self.satisfied = satisfied
        self.reason = reason


class _FakeRequirements:
    def __init__(self, satisfied: bool = True, reason: str = "") -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.satisfied = satisfied
        self.reason = reason

    def evaluate_prerequisite(self, prior: object, kind: object, item_symbols: object) -> _Outcome:
        self.calls.append((str(kind), tuple(item_symbols)))  # type: ignore[arg-type]
        return _Outcome(self.satisfied, self.reason)


class _FakeAdvancePort:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def advance(
        self,
        job_id: str,
        *,
        expected_revision: int,
        expected_status: object = None,
        expected_attempt: int | None = None,
        **fields: object,
    ) -> dict[str, object]:
        self.calls.append((job_id, expected_revision, expected_status, expected_attempt, fields))
        return {"job_id": job_id, "revision": expected_revision + 1}


def _worker(
    *,
    executor: _FakeExecutor | None = None,
    requirements: _FakeRequirements | None = None,
) -> FrozenWorker:
    return FrozenWorker(
        executor=executor or _FakeExecutor(),
        step_requirements=requirements or _FakeRequirements(),
    )


def _verify(env: ExecutionEnvelope, receipt: ExecutionReceipt, **overrides: object) -> object:
    kwargs: dict[str, object] = {
        "current_attempt": receipt.attempt,
        "current_submission_id": receipt.submission_id,
        "current_release_id": receipt.release_id,
        "owner_token": receipt.owner,
        "fencing_token": receipt.fencing_token,
    }
    kwargs.update(overrides)
    return verify_receipt(receipt, env, **kwargs)  # type: ignore[arg-type]


def _receipt(env: ExecutionEnvelope, **overrides: object) -> ExecutionReceipt:
    base = build_receipt(
        env,
        TaskExecutionOutcome(state="completed", result_digest="sha256:result"),
        written_at="2026-10-18T00:00:00+00:00",
    )
    return dataclasses.replace(base, **overrides) if overrides else base


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_happy_path_executes_writes_receipt_and_advances_once(tmp_path: Path) -> None:
    env = _envelope()
    plan = _plan(env)
    executor = _FakeExecutor()
    requirements = _FakeRequirements()
    worker = _worker(executor=executor, requirements=requirements)
    receipt_path = tmp_path / "receipt.json"

    receipt = worker.execute(
        env, plan, receipt_path=receipt_path, work_dir=tmp_path, prior_steps=()
    )

    assert executor.calls == 1
    assert requirements.calls == [("optimize", ("C", "O"))]
    assert receipt.state == "completed"
    assert receipt.job_id == _JOB and receipt.attempt == 1
    assert receipt.submission_id == env.submission_id
    assert receipt.release_id == env.release_id
    assert receipt.plan_identity == _PLAN_IDENTITY
    # atomic write: file present, no leftover temp file
    assert receipt_path.is_file()
    assert not list(tmp_path.glob(f".{receipt_path.name}.*"))
    assert read_receipt(receipt_path) == receipt

    verdict = _verify(env, receipt)
    assert verdict.accepted is True  # type: ignore[attr-defined]

    port = _FakeAdvancePort()
    advance_job_state(
        port,
        env,
        receipt,
        current_attempt=1,
        current_submission_id=env.submission_id,
        current_release_id=env.release_id,
        owner_token=env.owner,
        fencing_token=env.fencing_token,
        expected_revision=7,
        expected_status="running",
        status="completed",
    )
    assert len(port.calls) == 1
    job_id, revision, status, attempt, fields = port.calls[0]
    assert (job_id, revision, status, attempt) == (_JOB, 7, "running", 1)
    assert fields == {"status": "completed"}


def test_plan_identity_mismatch_blocks_before_execution(tmp_path: Path) -> None:
    env = _envelope()
    tampered = _plan(env, identity=FrozenPlanIdentity("v2:other", "v2:other", ("v2:other",)))
    executor = _FakeExecutor()
    worker = _worker(executor=executor)
    with pytest.raises(PlanVerificationError):
        worker.execute(env, tampered, receipt_path=tmp_path / "r.json", work_dir=tmp_path)
    assert executor.calls == 0
    assert not (tmp_path / "r.json").exists()


def test_unsatisfied_dependency_blocks_execution(tmp_path: Path) -> None:
    env = _envelope()
    plan = _plan(env)
    executor = _FakeExecutor()
    requirements = _FakeRequirements(satisfied=False, reason="missing_requirement")
    worker = _worker(executor=executor, requirements=requirements)
    with pytest.raises(DependencyNotSatisfiedError) as excinfo:
        worker.execute(env, plan, receipt_path=tmp_path / "r.json", work_dir=tmp_path)
    assert excinfo.value.reason == "missing_requirement"
    assert executor.calls == 0


# ---------------------------------------------------------------------------
# rejection matrix
# ---------------------------------------------------------------------------

_REJECT_CASES = [
    ("stale_attempt", {"current_attempt": 2}, "stale_attempt"),
    ("stale_submission", {"current_submission_id": "sub_other"}, "stale_submission"),
    ("stale_release", {"current_release_id": "rel-2"}, "stale_release"),
    ("wrong_owner", {"owner_token": "someone-else"}, "wrong_owner"),
    ("wrong_fencing", {"fencing_token": "fence-2"}, "wrong_fencing"),
]


@pytest.mark.parametrize("name,overrides,code", _REJECT_CASES)
def test_verify_receipt_rejects_stale_identity(
    name: str, overrides: dict[str, object], code: str
) -> None:
    env = _envelope()
    receipt = build_receipt(env, TaskExecutionOutcome(state="completed"))
    verdict = _verify(env, receipt, **overrides)
    assert verdict.accepted is False  # type: ignore[attr-defined]
    assert verdict.code == code  # type: ignore[attr-defined]


def test_verify_receipt_rejects_forged_submission() -> None:
    env = _envelope()
    receipt = _receipt(env, submission_id="sub_forged")
    verdict = _verify(env, receipt, current_submission_id="sub_forged")
    assert verdict.accepted is False  # type: ignore[attr-defined]
    assert verdict.code == "forged_submission"  # type: ignore[attr-defined]


def test_verify_receipt_rejects_digest_mismatch() -> None:
    env = _envelope()
    receipt = _receipt(env, plan_identity="v2:tampered")
    verdict = _verify(env, receipt)
    assert verdict.accepted is False  # type: ignore[attr-defined]
    assert verdict.code == "digest_mismatch"  # type: ignore[attr-defined]


def test_verify_receipt_rejects_job_mismatch() -> None:
    env = _envelope()
    other = _envelope(job_id="job-2", submission_id=expected_submission_id("job-2", 1))
    receipt = build_receipt(env, TaskExecutionOutcome(state="completed"))
    verdict = _verify(other, receipt)
    assert verdict.accepted is False  # type: ignore[attr-defined]
    assert verdict.code == "job_mismatch"  # type: ignore[attr-defined]


def test_forged_old_submission_receipt_is_rejected_and_state_unchanged() -> None:
    """QA failure scenario: forge an old submission -> reject, port untouched."""
    env = _envelope()
    receipt = _receipt(env, submission_id="sub_old_attempt")
    port = _FakeAdvancePort()
    with pytest.raises(ReceiptRejectedError) as excinfo:
        _advance(port, env, receipt)
    assert excinfo.value.verdict.code == "forged_submission"
    assert port.calls == []


@pytest.mark.parametrize("name,overrides,code", _REJECT_CASES)
def test_advance_not_called_when_receipt_rejected(
    name: str, overrides: dict[str, object], code: str
) -> None:
    env = _envelope()
    receipt = build_receipt(env, TaskExecutionOutcome(state="completed"))
    port = _FakeAdvancePort()
    with pytest.raises(ReceiptRejectedError) as excinfo:
        _advance(port, env, receipt, **overrides)
    assert excinfo.value.verdict.code == code
    assert port.calls == []


def _advance(
    port: _FakeAdvancePort, env: ExecutionEnvelope, receipt: ExecutionReceipt, **overrides: object
) -> object:
    kwargs: dict[str, object] = {
        "current_attempt": receipt.attempt,
        "current_submission_id": receipt.submission_id,
        "current_release_id": receipt.release_id,
        "owner_token": receipt.owner,
        "fencing_token": receipt.fencing_token,
        "expected_revision": 3,
        "expected_status": "running",
        "status": "completed",
    }
    kwargs.update(overrides)
    return advance_job_state(port, env, receipt, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


def test_idempotent_redelivery_does_not_reexecute_or_advance_twice(tmp_path: Path) -> None:
    env = _envelope()
    plan = _plan(env)
    executor = _FakeExecutor()
    worker = _worker(executor=executor)
    receipt_path = tmp_path / "receipt.json"

    first = worker.execute(env, plan, receipt_path=receipt_path, work_dir=tmp_path)
    second = worker.execute(env, plan, receipt_path=receipt_path, work_dir=tmp_path)

    assert executor.calls == 1, "re-delivery must not re-execute"
    assert second == first

    port = _FakeAdvancePort()
    _advance(port, env, first)
    assert len(port.calls) == 1

    # The job has moved on (attempt advanced); the consumed receipt is now stale,
    # so a re-delivered receipt cannot advance a second time.
    with pytest.raises(ReceiptRejectedError) as excinfo:
        _advance(port, env, first, current_attempt=2)
    assert excinfo.value.verdict.code == "stale_attempt"
    assert len(port.calls) == 1


def test_conflicting_attempt_receipt_is_not_overwritten(tmp_path: Path) -> None:
    env1 = _envelope(attempt=1)
    envelope2 = _envelope(
        attempt=2,
        submission_id=expected_submission_id(_JOB, 2),
    )
    executor = _FakeExecutor()
    worker = _worker(executor=executor)
    receipt_path = tmp_path / "receipt.json"

    worker.execute(env1, _plan(env1), receipt_path=receipt_path, work_dir=tmp_path)
    with pytest.raises(ReceiptConflictError):
        worker.execute(envelope2, _plan(envelope2), receipt_path=receipt_path, work_dir=tmp_path)

    assert executor.calls == 1
    assert read_receipt(receipt_path).attempt == 1  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# D02 — full frozen receipt identity is validated before any reuse
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _import_closure(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_receipt_identity_import_graph_is_acyclic_in_both_orders() -> None:
    runtime_root = _SERVICES_ROOT / "runtime"
    receipts_imports = _import_closure(runtime_root / "receipts.py")
    identity_imports = _import_closure(runtime_root / "receipt_identity.py")

    assert not {m for m in receipts_imports if m.startswith("acp_services.runtime.worker")}, (
        "receipts.py must not import worker (D02: controller -> execution reverse dependency)"
    )
    forbidden = ("acp_services.runtime.worker", "acp_services.runtime.receipts")
    assert not {m for m in identity_imports if m.startswith(forbidden)}, (
        "the identity predicate must stay dependency-free (no worker/receipts import)"
    )

    orders = (
        (
            "acp_services.runtime.receipt_identity",
            "acp_services.runtime.receipts",
            "acp_services.runtime.worker",
        ),
        (
            "acp_services.runtime.worker",
            "acp_services.runtime.receipt_identity",
            "acp_services.runtime.receipts",
        ),
    )
    for order in orders:
        code = "; ".join(f"import {module}" for module in order)
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
    independent_worker = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, acp_services.worker_bootstrap; "
            "assert 'acp_services.runtime.receipt_identity' in sys.modules",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert independent_worker.returncode == 0, independent_worker.stderr


_IDENTITY_MUTATIONS = [
    ("attempt", {"attempt": 2}, "attempt"),
    ("submission", {"submission_id": "sub_other_attempt"}, "submission_id"),
    ("release_id", {"release_id": "rel-2"}, "release_id"),
    ("release_digest", {"release_digest": "reldig-2"}, "release_digest"),
    ("plan_identity", {"plan_identity": "v2:plan-other"}, "plan_identity"),
    ("step_identities", {"step_identities": ("v2:step-other",)}, "step_identities"),
    ("owner", {"owner": "worker-2"}, "owner"),
    ("fencing_token", {"fencing_token": "fence-2"}, "fencing_token"),
]


@pytest.mark.parametrize(
    "name,overrides,code",
    _IDENTITY_MUTATIONS,
    ids=[case[0] for case in _IDENTITY_MUTATIONS],
)
def test_single_field_identity_mutation_is_a_typed_conflict(
    tmp_path: Path, name: str, overrides: dict[str, object], code: str
) -> None:
    env = _envelope()
    mutated = _envelope(**overrides)
    executor = _FakeExecutor()
    worker = _worker(executor=executor)
    receipt_path = tmp_path / "receipt.json"

    original = worker.execute(env, _plan(env), receipt_path=receipt_path, work_dir=tmp_path)
    before = _sha256(receipt_path)

    with pytest.raises(ReceiptConflictError) as excinfo:
        worker.execute(mutated, _plan(mutated), receipt_path=receipt_path, work_dir=tmp_path)

    comparison = excinfo.value.comparison
    assert comparison is not None
    assert comparison.codes == (code,), f"exactly one field drifted, got {comparison.codes}"
    assert _sha256(receipt_path) == before, "a conflicting receipt must stay byte-identical"
    assert read_receipt(receipt_path) == original
    assert executor.calls == 1, "a conflicting receipt must never reach the executor"


def test_receipt_data_binding_identity_mutation_is_a_typed_conflict(tmp_path: Path) -> None:
    env = _envelope(
        data_bindings=(
            StorageBinding(binding_id="bind-1", artifact_digests={"geom.xyz": "sha256:aaa"}),
        )
    )
    executor = _FakeExecutor()
    worker = _worker(executor=executor)
    receipt_path = tmp_path / "receipt.json"

    original = worker.execute(env, _plan(env), receipt_path=receipt_path, work_dir=tmp_path)
    tampered = dataclasses.replace(
        original,
        data_bindings=(
            StorageBinding(binding_id="bind-1", artifact_digests={"geom.xyz": "sha256:bbb"}),
        ),
    )
    write_receipt_atomic(receipt_path, tampered)
    before = _sha256(receipt_path)

    with pytest.raises(ReceiptConflictError) as excinfo:
        worker.execute(env, _plan(env), receipt_path=receipt_path, work_dir=tmp_path)

    assert excinfo.value.comparison is not None
    assert "data_bindings" in excinfo.value.comparison.codes
    assert _sha256(receipt_path) == before
    assert read_receipt(receipt_path) == tampered
    assert executor.calls == 1


def test_predicate_reports_every_divergent_field(tmp_path: Path) -> None:
    env = _envelope()
    receipt = build_receipt(env, TaskExecutionOutcome(state="completed"))
    drifted = dataclasses.replace(
        receipt,
        receipt_version=0,
        owner="worker-2",
        fencing_token="fence-2",
        step_identities=("v2:step-other",),
    )
    comparison = compare_receipt_identity(drifted, env)
    assert set(comparison.codes) >= {
        "receipt_version",
        "owner",
        "fencing_token",
        "step_identities",
    }


def test_attempt_2_envelope_with_attempt_1_completed_receipt_is_a_typed_conflict(
    tmp_path: Path,
) -> None:
    env1 = _envelope(attempt=1)
    env2 = _envelope(attempt=2, submission_id=expected_submission_id(_JOB, 2))
    executor = _FakeExecutor()
    worker = _worker(executor=executor)
    receipt_path = tmp_path / "receipt.json"
    worker.execute(env1, _plan(env1), receipt_path=receipt_path, work_dir=tmp_path)
    before = _sha256(receipt_path)

    with pytest.raises(ReceiptConflictError) as excinfo:
        worker.execute(env2, _plan(env2), receipt_path=receipt_path, work_dir=tmp_path)

    assert excinfo.value.comparison is not None
    assert "attempt" in excinfo.value.comparison.codes
    assert _sha256(receipt_path) == before
    assert read_receipt(receipt_path).attempt == 1  # type: ignore[union-attr]
    assert executor.calls == 1


def test_same_attempt_with_different_release_is_a_typed_conflict(tmp_path: Path) -> None:
    env = _envelope()
    other_release = _envelope(release_id="release-b")
    executor = _FakeExecutor()
    worker = _worker(executor=executor)
    receipt_path = tmp_path / "receipt.json"
    worker.execute(env, _plan(env), receipt_path=receipt_path, work_dir=tmp_path)
    before = _sha256(receipt_path)

    with pytest.raises(ReceiptConflictError) as excinfo:
        worker.execute(
            other_release, _plan(other_release), receipt_path=receipt_path, work_dir=tmp_path
        )

    assert excinfo.value.comparison is not None
    assert excinfo.value.comparison.codes == ("release_id",)
    assert _sha256(receipt_path) == before
    assert executor.calls == 1


def test_tampered_plan_body_is_rejected_before_receipt_reuse(tmp_path: Path) -> None:
    env = _envelope()
    executor = _FakeExecutor()
    worker = _worker(executor=executor)
    receipt_path = tmp_path / "receipt.json"
    worker.execute(env, _plan(env), receipt_path=receipt_path, work_dir=tmp_path)
    before = _sha256(receipt_path)

    tampered = _plan(
        env,
        payload={"task": "singlepoint"},
        identity=FrozenPlanIdentity("v2:tampered", env.plan_identity, env.step_identities),
    )
    with pytest.raises(PlanVerificationError):
        worker.execute(env, tampered, receipt_path=receipt_path, work_dir=tmp_path)

    assert _sha256(receipt_path) == before
    assert executor.calls == 1


def test_prerequisite_gate_runs_before_receipt_reuse(tmp_path: Path) -> None:
    env = _envelope()
    executor = _FakeExecutor()
    receipt_path = tmp_path / "receipt.json"
    _worker(executor=executor).execute(
        env, _plan(env), receipt_path=receipt_path, work_dir=tmp_path
    )
    before = _sha256(receipt_path)

    blocked = _FakeRequirements(satisfied=False, reason="missing_requirement")
    with pytest.raises(DependencyNotSatisfiedError) as excinfo:
        _worker(executor=executor, requirements=blocked).execute(
            env, _plan(env), receipt_path=receipt_path, work_dir=tmp_path
        )

    assert excinfo.value.reason == "missing_requirement"
    assert _sha256(receipt_path) == before
    assert executor.calls == 1


def test_full_identity_reuse_returns_existing_receipt_without_second_execution(
    tmp_path: Path,
) -> None:
    env = _envelope()
    executor = _FakeExecutor()
    worker = _worker(executor=executor)
    receipt_path = tmp_path / "receipt.json"

    first = worker.execute(env, _plan(env), receipt_path=receipt_path, work_dir=tmp_path)
    before = _sha256(receipt_path)
    second = worker.execute(env, _plan(env), receipt_path=receipt_path, work_dir=tmp_path)

    assert second == first
    assert executor.calls == 1
    assert _sha256(receipt_path) == before


# ---------------------------------------------------------------------------
# envelope guard (delegated): TaskContext / callback never serialized
# ---------------------------------------------------------------------------


def test_envelope_rejects_task_context_and_callback_fields() -> None:
    env = _envelope()
    payload = env.to_dict()
    payload["task_context"] = {"workdir": "/tmp"}
    with pytest.raises(TaskContextSerializationError):
        ExecutionEnvelope.from_dict(payload)

    payload2 = env.to_dict()
    payload2["progress_callback"] = lambda: None
    with pytest.raises(TaskContextSerializationError):
        ExecutionEnvelope.from_dict(payload2)


# ---------------------------------------------------------------------------
# port shape
# ---------------------------------------------------------------------------


def test_seams_are_runtime_checkable_protocols() -> None:
    assert isinstance(_FakeExecutor(), TaskExecutorPort)
    assert isinstance(_FakeRequirements(), StepRequirementPort)
    assert isinstance(_FakeAdvancePort(), JobStateAdvancePort)
    assert isinstance(DeclaredPlanVerifier(), PlanVerificationPort)


# ---------------------------------------------------------------------------
# no DB opened
# ---------------------------------------------------------------------------


def test_worker_execute_never_opens_a_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[object] = []

    def _guard(*args: object, **kwargs: object) -> object:
        opened.append((args, kwargs))
        raise AssertionError("worker must not open a database")

    monkeypatch.setattr(sqlite3, "connect", _guard)
    env = _envelope()
    worker = _worker()
    receipt = worker.execute(env, _plan(env), receipt_path=tmp_path / "r.json", work_dir=tmp_path)

    assert receipt.state == "completed"
    assert opened == []
    assert not list(tmp_path.glob("*.db"))


# ---------------------------------------------------------------------------
# AST guard: runtime package bans
# ---------------------------------------------------------------------------


def _docstring_node_ids(tree: ast.AST) -> set[int]:
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


def _code_only(source: str) -> str:
    tokens: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE):
                continue
            tokens.append(tok.string)
    except tokenize.TokenError:  # pragma: no cover
        return source
    return " ".join(tokens)


_RAW_BANS = (
    re.compile(r"(?<![\w.])import\s+acp(?![_\w])"),
    re.compile(r"(?<![\w.])from\s+acp(?![_\w])"),
    re.compile(r"sqlite3\s*\.\s*connect"),
    re.compile(r"import\s+sqlite3"),
    re.compile(r"\bJobStore\b"),
    re.compile(r"\battempt\s*\+=\s*1\b"),
    re.compile(r"\battempt\s*=\s*attempt\s*\+\s*1\b"),
    # Bare ``attempt + 1`` is intentionally NOT a raw ban: the AST visitor
    # above detects a real counter advance (``Name``/``AugAssign``) while a
    # wire validation like ``self.attempt + 1`` must stay legal.
    re.compile(r"\.transition\s*\("),
    re.compile(r"\.update_progress\s*\("),
    re.compile(r"\.requeue_with_spec\s*\("),
)

# DB-name / SQL-write markers live in string literals, so they are checked on
# non-docstring string constants (raw text stripping would otherwise hide them).
_DB_STRING_BANS = (
    re.compile(r"acp_jobs\.db|\bjobs\.db\b"),
    re.compile(r"UPDATE\s+jobs\b", re.IGNORECASE),
    re.compile(r"INSERT\s+INTO\s+jobs\b", re.IGNORECASE),
    re.compile(r"DELETE\s+FROM\s+jobs\b", re.IGNORECASE),
)


def scan_runtime_source(source: str) -> list[str]:
    violations: list[str] = []
    tree = ast.parse(source)
    doc_ids = _docstring_node_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "acp" or alias.name.startswith("acp."):
                    violations.append(f"import {alias.name}")
                if alias.name == "sqlite3":
                    violations.append("import sqlite3")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "acp" or module.startswith("acp."):
                violations.append(f"from {module} import ...")
            if module == "sqlite3":
                violations.append("from sqlite3 import ...")
        elif isinstance(node, ast.Name) and node.id == "JobStore":
            violations.append("name JobStore")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            if isinstance(node.left, ast.Name) and node.left.id == "attempt":
                if isinstance(node.right, ast.Constant) and node.right.value == 1:
                    violations.append("attempt + 1")
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "attempt":
                violations.append("attempt += ...")
        elif isinstance(node, ast.Call):
            func = node.func
            attr = func.attr if isinstance(func, ast.Attribute) else None
            if attr in {"transition", "update_progress", "requeue_with_spec"}:
                violations.append(f".{attr}(...)")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in doc_ids:
                continue
            for pattern in _DB_STRING_BANS:
                if pattern.search(node.value):
                    violations.append(f"db-write string {node.value!r}")

    code = _code_only(source)
    for pattern in _RAW_BANS:
        match = pattern.search(code)
        if match:
            violations.append(f"raw-code match {match.group(0)!r}")
    return violations


def _module_path(module: str) -> Path | None:
    """Resolve a dotted ``acp_services`` module to its source file, if any."""
    if module != "acp_services" and not module.startswith("acp_services."):
        return None
    base = _REPO_ROOT / "src"
    parts = module.split(".")
    file_path = base.joinpath(*parts).with_suffix(".py")
    if file_path.is_file():
        return file_path
    package_init = base.joinpath(*parts) / "__init__.py"
    return package_init if package_init.is_file() else None


def worker_boundary_modules() -> list[Path]:
    """Worker-boundary roots plus their static ``acp_services`` import closure."""
    seen: dict[Path, None] = {}
    queue: list[Path] = list(_WORKER_BOUNDARY_ROOTS)
    while queue:
        path = queue.pop(0)
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen[resolved] = None
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                dependency = _module_path(module)
                if dependency is not None and dependency.resolve() not in seen:
                    queue.append(dependency)
    return sorted(seen)


def test_worker_execution_boundary_has_no_acp_or_db_access() -> None:
    boundary = worker_boundary_modules()
    boundary_names = {path.name for path in boundary}
    assert {"worker_bootstrap.py", "worker.py", "execution_chain.py"} <= boundary_names
    offenders: dict[str, list[str]] = {}
    for path in boundary:
        findings = scan_runtime_source(path.read_text(encoding="utf-8"))
        if findings:
            offenders[str(path.relative_to(_REPO_ROOT))] = findings
    assert not offenders, f"worker boundary guard violations: {offenders}"


@pytest.mark.parametrize(
    "snippet",
    [
        "import acp\n",
        "import acp.scheduler.store\n",
        "from acp.scheduler import store\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import acp\n",
        "import sqlite3\nsqlite3.connect('acp_jobs.db')\n",
        "from sqlite3 import connect\n",
        "class JobStore:\n    pass\n",
        "def f(attempt):\n    attempt += 1\n",
        "def f(attempt):\n    attempt = attempt + 1\n",
        "def f(store):\n    store.transition('j', expected_revision=0)\n",
        "def f(conn):\n    conn.execute('UPDATE jobs SET status=1')\n",
        "def f():\n    return open('acp_jobs.db', 'w')\n",
    ],
)
def test_guard_scanner_catches_banned_patterns(snippet: str) -> None:
    assert scan_runtime_source(snippet), f"scanner missed: {snippet!r}"


def test_guard_scanner_allows_docstring_and_services_imports() -> None:
    source = (
        '"""This mentions acp.scheduler.store and JobStore.transition benignly."""\n'
        "from acp_services.runtime.worker import FrozenWorker\n"
        "def f(attempt, port):\n"
        '    """attempt is decremented; acp is never imported."""\n'
        "    return port.advance(attempt)\n"
    )
    assert scan_runtime_source(source) == []


_WORKER_MODULE_INJECTIONS = (
    "import acp\n",
    "def _injected(attempt):\n    attempt += 1\n",
    "def _injected(store):\n    store.transition('j')\n",
    "def _injected(conn):\n    conn.execute('UPDATE jobs SET status=1')\n",
)


@pytest.mark.parametrize("module_name", ["worker_bootstrap.py", "worker.py", "execution_chain.py"])
@pytest.mark.parametrize("injection", _WORKER_MODULE_INJECTIONS)
def test_guard_scanner_catches_violations_injected_into_worker_modules(
    module_name: str, injection: str
) -> None:
    boundary = {path.name: path for path in worker_boundary_modules()}
    path = boundary[module_name]
    source = path.read_text(encoding="utf-8")
    assert scan_runtime_source(source) == [], f"baseline not clean: {path}"
    assert scan_runtime_source(source + "\n" + injection), (
        f"scanner missed injected violation in {path}: {injection!r}"
    )


# ---------------------------------------------------------------------------
# CAS semantics preserved (isolated DB — controller side, not the worker)
# ---------------------------------------------------------------------------


def test_controller_advance_preserves_store_cas_semantics(tmp_path: Path) -> None:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.scheduler.store import JobStateConflictError, JobStore

    store = JobStore(tmp_path / "isolated.db")
    store.create(JobRecord(id=_JOB, spec=JobSpec(workflow="optimize")))

    class _StoreAdvance:
        def advance(
            self,
            job_id,
            *,
            expected_revision,
            expected_status=None,
            expected_attempt=None,
            **fields,
        ):
            return store.transition(
                job_id,
                expected_status=expected_status,
                expected_revision=expected_revision,
                expected_attempt=expected_attempt,
                **fields,
            )

    env = _envelope()
    receipt = build_receipt(env, TaskExecutionOutcome(state="completed"))
    record = store.get(_JOB)
    assert record is not None
    port = _StoreAdvance()

    advanced = advance_job_state(
        port,
        env,
        receipt,
        current_attempt=1,
        current_submission_id=env.submission_id,
        current_release_id=env.release_id,
        owner_token=env.owner,
        fencing_token=env.fencing_token,
        expected_revision=record.revision,
        expected_status="queued",
        status="completed",
    )
    assert advanced.status == JobStatus.COMPLETED

    # A stale expectation is rejected by the underlying CAS guard, unchanged.
    with pytest.raises(JobStateConflictError):
        advance_job_state(
            port,
            env,
            receipt,
            current_attempt=1,
            current_submission_id=env.submission_id,
            current_release_id=env.release_id,
            owner_token=env.owner,
            fencing_token=env.fencing_token,
            expected_revision=record.revision,
            expected_status="queued",
            status="completed",
        )


# ---------------------------------------------------------------------------
# D01 — authoritative TaskResult status and the frozen truth table
# ---------------------------------------------------------------------------


def _result(task: str = "singlepoint", **overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "task": task,
        "status": "completed",
        "complete": True,
        "errors": (),
        "energy_hartree": None,
        "coordinates": None,
        "symbols": None,
        "frequencies": (),
        "converged": None,
        "payload": None,
        "metadata": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _irc_partial_result(
    directions: tuple[str, ...] = ("forward",),
    requested: tuple[str, ...] = ("forward", "reverse"),
    *,
    complete: bool = False,
) -> SimpleNamespace:
    entries = tuple(
        SimpleNamespace(
            direction=direction,
            success=True,
            coordinates=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
            trajectory_ref=None,
        )
        for direction in directions
    )
    return _result(
        "irc",
        complete=complete,
        coordinates=((0.0, 0.0, 0.0),),
        symbols=("C", "O"),
        payload=SimpleNamespace(directions=entries),
        metadata={"directions": list(requested)},
    )


def _valid_result(kind: str) -> SimpleNamespace:
    """One result whose required facts the CCCP authority would accept."""
    if kind == "singlepoint":
        return _result(kind, energy_hartree=-76.0)
    if kind == "optimize":
        return _result(kind, coordinates=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)), symbols=("C", "O"))
    if kind == "frequency":
        return _result(kind, payload=SimpleNamespace(n_imaginary=0))
    if kind == "scan":
        frames = (SimpleNamespace(success=True, geometry_ref=object()),)
        return _result(
            kind,
            payload=SimpleNamespace(frames=frames),
            metadata={"frame_count": 1, "successful_frame_count": 1},
        )
    if kind == "irc":
        return _irc_partial_result(
            ("forward", "reverse"),
            ("forward", "reverse"),
            complete=True,
        )
    if kind == "casscf":
        return _result(kind, converged=True, payload=SimpleNamespace(active_space="(2,2)"))
    if kind == "thermochemistry":
        return _result(
            kind,
            payload=SimpleNamespace(gibbs_hartree=-1.0, enthalpy_hartree=-1.0),
        )
    if kind == "conformer_search":
        return _result(kind, payload=SimpleNamespace(conformer_count=2, ensemble_ref=object()))
    if kind == "md_sampling":
        return _result(kind, payload=SimpleNamespace(n_frames=3, trajectory_ref=object()))
    if kind == "clustering":
        return _result(
            kind, payload=SimpleNamespace(assignments=(object(),), clustered_ref=object())
        )
    if kind == "censo_refine":
        return _result(
            kind, payload=SimpleNamespace(records=(object(),), refined_ensemble_ref=object())
        )
    if kind == "nmr_shielding":
        return _result(kind, payload=SimpleNamespace(shieldings={0: object()}))
    if kind == "xtb_path_search":
        return _result(
            kind,
            payload=SimpleNamespace(frames=(object(),), trajectory_ref=object(), end_frame_index=0),
        )
    if kind == "orca_gradient":
        return _result(
            kind,
            symbols=("C",),
            payload=SimpleNamespace(gradients=((0.0, 0.0, 0.0),)),
        )
    if kind == "xtb_constrained_opt":
        return _result(kind, payload=SimpleNamespace(energy_effective_hartree=-1.0))
    raise AssertionError(f"no valid-result factory for {kind!r}")


_ROW_A = {
    "row": "a",
    "receipt_state": "failed",
    "exit_code": 1,
    "job_status": "failed",
    "normal_reuse": False,
    "publish": False,
    "publish_policy": "none",
    "partial_result": False,
    "qualification": QUALIFICATION_TASK_FAILED,
}
_ROW_B = {
    "row": "b",
    "receipt_state": "completed",
    "exit_code": 0,
    "job_status": "completed",
    "normal_reuse": True,
    "publish": True,
    "publish_policy": "normal",
    "partial_result": False,
    "qualification": QUALIFICATION_COMPLETE,
}
_ROW_C = {
    "row": "c",
    "receipt_state": "completed",
    "exit_code": 0,
    "job_status": "completed",
    "normal_reuse": False,
    "publish": True,
    "publish_policy": "partial",
    "partial_result": True,
    "qualification": QUALIFICATION_PARTIAL,
}
_ROW_D = {
    "row": "d",
    "receipt_state": "failed",
    "exit_code": 1,
    "job_status": "failed",
    "normal_reuse": False,
    "publish": False,
    "publish_policy": "none",
    "partial_result": False,
    "qualification": QUALIFICATION_INCOMPLETE,
}
_ROW_E = {
    "row": "e",
    "receipt_state": "failed",
    "exit_code": 1,
    "job_status": "failed",
    "normal_reuse": False,
    "publish": False,
    "publish_policy": "none",
    "partial_result": False,
    "qualification": QUALIFICATION_INCOMPLETE,
}


def _assert_row(decision, expected: dict[str, object]) -> None:
    for key, value in expected.items():
        assert getattr(decision, key) == value, f"{key}: {getattr(decision, key)!r} != {value!r}"


_D01_CASES = [
    ("a_failed", _result(status="failed", complete=False, errors=("boom",)), _ROW_A),
    (
        "a_cancelled",
        _result(status="cancelled", complete=False),
        {**_ROW_A, "receipt_state": "cancelled", "job_status": "cancelled"},
    ),
    (
        "a_blocked",
        _result(status="blocked", complete=False),
        {**_ROW_A, "receipt_state": "blocked"},
    ),
    ("b_complete", _result("singlepoint", energy_hartree=-76.0), _ROW_B),
    ("c_partial", _irc_partial_result(), _ROW_C),
    ("d_partial_missing_fact", _irc_partial_result(directions=()), _ROW_D),
    (
        "d_complete_true_missing_fact_outranked",
        _result("singlepoint", complete=True, energy_hartree=None),
        _ROW_D,
    ),
    (
        "d_complete_declaration_missing",
        _result("singlepoint", complete=None, energy_hartree=-76.0),
        _ROW_D,
    ),
    ("e_unknown_status", _result("singlepoint", status="finished"), _ROW_E),
    ("e_unknown_task_kind", _result("not_a_task", complete=False), _ROW_E),
]


@pytest.mark.parametrize("name,result,expected", _D01_CASES, ids=[c[0] for c in _D01_CASES])
def test_d01_truth_table_rows(
    name: str, result: SimpleNamespace, expected: dict[str, object]
) -> None:
    decision = decide_task_result(result)
    _assert_row(decision, expected)

    env = _envelope()
    outcome = outcome_from_task_result(result)
    receipt = build_receipt(env, outcome)
    assert receipt.state == expected["receipt_state"]
    assert receipt_exit_code(receipt) == expected["exit_code"]
    assert job_status_for_receipt(receipt) == expected["job_status"]
    assert receipt.evidence_digests["truth_table_row"] == expected["row"]
    assert receipt.evidence_digests["partial_result"] == (
        "true" if expected["partial_result"] else "false"
    )
    assert receipt.evidence_digests["normal_reuse"] == (
        "true" if expected["normal_reuse"] else "false"
    )
    assert receipt.evidence_digests["publish"] == ("true" if expected["publish"] else "false")
    assert receipt.evidence_digests["publish_policy"] == expected["publish_policy"]
    if expected["qualification"] == QUALIFICATION_INCOMPLETE:
        codes = [entry.code if hasattr(entry, "code") else str(entry) for entry in receipt.errors]
        assert QUALIFICATION_INCOMPLETE in codes


def test_d01_failed_row_preserves_cause_and_never_upgrades() -> None:
    decision = decide_task_result(_result(status="failed", complete=False, errors=("boom",)))
    assert decision.receipt_state == "failed"
    assert decision.errors == ("boom",)


def test_d01_missing_fact_outranks_complete_true() -> None:
    decision = decide_task_result(_result("singlepoint", complete=True, energy_hartree=None))
    assert decision.row == "d"
    assert decision.status == "completed"
    assert decision.complete is True
    assert decision.missing_facts == ("energy_hartree",)
    assert decision.receipt_state == "failed"


def test_d01_old_complete_false_upgrade_counterexample(monkeypatch) -> None:
    import acp_services.runtime.worker as worker_module

    def _legacy_receipt_state(result: SimpleNamespace) -> str:
        status = str(getattr(result, "status", "completed"))
        state = status if status in ("completed", "failed", "cancelled", "blocked") else "failed"
        if state == status and getattr(result, "complete", True) is False:
            state = "completed"
        return state

    failed = _result(status="failed", complete=False, errors=("boom",))
    assert _legacy_receipt_state(failed) == "completed", "counterexample must reproduce the bug"

    monkeypatch.setattr(
        worker_module,
        "decide_task_result",
        lambda result: dataclasses.replace(
            decide_task_result(result), receipt_state=_legacy_receipt_state(result)
        ),
    )
    with pytest.raises(AssertionError):
        _assert_row(worker_module.decide_task_result(failed), _ROW_A)


_CORE_KINDS = {
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "casscf",
}
_P2_KINDS = {
    "conformer_search",
    "md_sampling",
    "clustering",
    "censo_refine",
    "nmr_shielding",
    "xtb_path_search",
    "orca_gradient",
    "xtb_constrained_opt",
}


def test_completion_authority_matrix_covers_every_registered_kind() -> None:
    expected = _CORE_KINDS | _P2_KINDS | {"thermochemistry"}
    assert set(TASK_COMPLETION_AUTHORITIES) == expected
    for kind in sorted(expected):
        authority = TASK_COMPLETION_AUTHORITIES[kind]
        assert authority.kind == kind
        assert authority.source
        assert authority.required_facts
        assert authority.partial_policy
        decision = decide_task_result(_valid_result(kind))
        assert decision.row == "b", f"{kind}: {decision.missing_facts}"
        assert decision.required_facts == tuple(fact.name for fact in authority.required_facts)
        assert decision.missing_facts == ()


def test_completion_authority_missing_kind_fails_closed() -> None:
    decision = decide_task_result(_result("unregistered_kind", energy_hartree=-1.0))
    assert decision.row == "e"
    assert decision.qualification == QUALIFICATION_INCOMPLETE
    assert decision.receipt_state == "failed"
    assert decision.normal_reuse is False
    assert decision.publish is False


def test_job_status_map_covers_every_receipt_state() -> None:
    assert JOB_STATUS_BY_RECEIPT_STATE == {
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
        "blocked": "failed",
    }


# ---------------------------------------------------------------------------
# D01 — task_result.json persistence, digest binding, round-trip
# ---------------------------------------------------------------------------


def test_partial_qualification_round_trips_through_receipt_and_controller(
    tmp_path: Path,
) -> None:
    result = _irc_partial_result()
    outcome = outcome_from_task_result(result, work_dir=tmp_path)
    document = read_task_result(tmp_path)
    qualification = read_task_result_qualification(tmp_path)
    assert document is not None and qualification is not None
    assert document["partial_result"] is True
    assert document["normal_reuse"] is False
    assert document["publish"] is True
    assert document["publish_policy"] == "partial"
    assert document["truth_table_row"] == "c"
    assert document["required_facts"] == ["irc_directions"]
    assert document["missing_required_facts"] == []
    assert qualification.content_digest == document["content_digest"]
    assert outcome.evidence_digests["task_result_digest"] == document["content_digest"]

    env = _envelope()
    receipt = build_receipt(env, outcome)
    write_receipt_atomic(tmp_path / "receipt.json", receipt)
    reloaded = read_receipt(tmp_path / "receipt.json")
    assert reloaded is not None
    assert reloaded.evidence_digests["partial_result"] == "true"
    assert reloaded.evidence_digests["normal_reuse"] == "false"
    assert reloaded.evidence_digests["publish_policy"] == "partial"
    assert reloaded.evidence_digests["task_result_digest"] == document["content_digest"]
    assert receipt_exit_code(reloaded) == 0
    assert job_status_for_receipt(reloaded) == "completed"
    assert _verify(env, reloaded).accepted is True  # type: ignore[attr-defined]

    port = _FakeAdvancePort()
    advance_job_state(
        port,
        env,
        reloaded,
        current_attempt=reloaded.attempt,
        current_submission_id=reloaded.submission_id,
        current_release_id=reloaded.release_id,
        owner_token=reloaded.owner,
        fencing_token=reloaded.fencing_token,
        expected_revision=4,
        expected_status="running",
        status=job_status_for_receipt(reloaded),
    )
    assert len(port.calls) == 1
    assert port.calls[0][4] == {"status": "completed"}
    assert not list((tmp_path / "WORK" / "00_RUNTIME").glob(".*.tmp"))


def test_task_result_digest_binding_rejects_tampering(tmp_path: Path) -> None:
    outcome_from_task_result(_irc_partial_result(), work_dir=tmp_path)
    path = task_result_path(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["normal_reuse"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TaskResultIntegrityError):
        read_task_result(tmp_path)
    with pytest.raises(TaskResultIntegrityError):
        read_task_result_qualification(tmp_path)


def test_task_result_absent_is_none(tmp_path: Path) -> None:
    assert read_task_result(tmp_path) is None
    assert read_task_result_qualification(tmp_path) is None


class _TaskResultExecutor:
    def __init__(self, result: SimpleNamespace) -> None:
        self.calls = 0
        self.result = result

    def execute(self, scope: object) -> TaskExecutionOutcome:
        self.calls += 1
        work_dir = getattr(scope, "work_dir")
        return outcome_from_task_result(self.result, work_dir=work_dir)


def test_partial_redelivery_is_idempotent_and_keeps_the_fact(tmp_path: Path) -> None:
    env = _envelope()
    plan = _plan(env)
    executor = _TaskResultExecutor(_irc_partial_result())
    worker = _worker(executor=executor)  # type: ignore[arg-type]
    receipt_path = tmp_path / "receipt.json"

    first = worker.execute(env, plan, receipt_path=receipt_path, work_dir=tmp_path)
    second = worker.execute(env, plan, receipt_path=receipt_path, work_dir=tmp_path)

    assert executor.calls == 1
    assert second == first
    assert first.evidence_digests["truth_table_row"] == "c"
    assert first.evidence_digests["partial_result"] == "true"
    assert first.evidence_digests["normal_reuse"] == "false"
    assert read_task_result_qualification(tmp_path) is not None


# ---------------------------------------------------------------------------
# D01 — prerequisite + recovery consumers reject completed-partial reuse
# ---------------------------------------------------------------------------


def test_completed_partial_never_satisfies_geometry_prerequisite() -> None:
    partial = CalculationResult(
        status="completed",
        coords=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)],
        metadata={"partial_result": True, "normal_reuse": False},
    )
    assert is_partial_result(partial) is True
    outcome = evaluate_prerequisite(
        [PriorStep(StepKind.OPTIMIZE, "completed", partial)],
        StepKind.FREQUENCY,
        ("C", "O"),
    )
    assert outcome.satisfied is False
    assert outcome.reason == MISSING_REQUIREMENT

    flagged = PriorStep(
        StepKind.OPTIMIZE,
        "completed",
        CalculationResult(status="completed", coords=[(0.0, 0.0, 0.0)]),
        partial_result=True,
    )
    outcome2 = evaluate_prerequisite([flagged], StepKind.SINGLEPOINT, ("C",))
    assert outcome2.satisfied is False
    assert outcome2.reason == MISSING_REQUIREMENT


def test_completed_partial_never_satisfies_thermochemistry_upstream() -> None:
    partial_freq = CalculationResult(
        status="completed",
        frequencies=[100.0],
        metadata={"normal_reuse": False},
    )
    complete_sp = CalculationResult(status="completed", energy=-76.0)
    outcome = evaluate_prerequisite(
        [
            PriorStep(StepKind.FREQUENCY, "completed", partial_freq),
            PriorStep(StepKind.SINGLEPOINT, "completed", complete_sp),
        ],
        StepKind.THERMOCHEMISTRY,
        ("C", "O"),
    )
    assert outcome.satisfied is False
    assert outcome.reason == MISSING_REQUIREMENT


def test_executor_adoption_refuses_the_persisted_partial_fact(tmp_path: Path) -> None:
    from acp.calculations.executor import CalculationPlanExecutor

    step_dir = tmp_path / "WORK" / "05_SP"
    step_dir.mkdir(parents=True)
    (step_dir / "step_result.json").write_text(
        json.dumps({"schema_version": 1, "status": "completed"}), encoding="utf-8"
    )
    outcome_from_task_result(_irc_partial_result(), work_dir=tmp_path)

    executor = CalculationPlanExecutor()
    adoption = executor._adopt_step_result(
        idx=0,
        step_identity=None,
        science_root=tmp_path,
        task_root=tmp_path,
        rel="WORK/05_SP/step_result.json",
        enabled=True,
        was_completed=False,
        kind=StepKind.SINGLEPOINT,
    )

    assert adoption.result is None
    assert adoption.reason == "partial_result_not_reusable"
    assert adoption.integrity_failed is True


def test_executor_adoption_refuses_an_embedded_partial_step_result(tmp_path: Path) -> None:
    from acp.calculations.executor import CalculationPlanExecutor

    step_dir = tmp_path / "WORK" / "05_SP"
    step_dir.mkdir(parents=True)
    (step_dir / "step_result.json").write_text(
        json.dumps({"schema_version": 1, "partial_result": True}), encoding="utf-8"
    )

    adoption = CalculationPlanExecutor()._adopt_step_result(
        idx=0,
        step_identity=None,
        science_root=tmp_path,
        task_root=tmp_path,
        rel="WORK/05_SP/step_result.json",
        enabled=True,
        was_completed=False,
        kind=StepKind.SINGLEPOINT,
    )

    assert adoption.result is None
    assert adoption.reason == "partial_result_not_reusable"
    assert adoption.integrity_failed is True
