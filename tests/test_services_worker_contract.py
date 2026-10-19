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
import io
import re
import sqlite3
import tokenize
from pathlib import Path

import pytest

from acp_services.contracts.envelope import (
    CURRENT_ENVELOPE_VERSION,
    ExecutionEnvelope,
    ExecutionReceipt,
    TaskContextSerializationError,
    expected_submission_id,
)
from acp_services.contracts.injection import JobStateAdvancePort, StepRequirementPort
from acp_services.runtime.receipts import (
    ReceiptRejectedError,
    advance_job_state,
    verify_receipt,
)
from acp_services.runtime.worker import (
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
    build_receipt,
    read_receipt,
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
