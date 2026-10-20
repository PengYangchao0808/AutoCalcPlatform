"""Self-sufficient worker composition root (B06-6 / todo 78).

The worker is launched by an :class:`~acp_services.contracts.envelope.ExecutionEnvelope`
in a **new process** (local subprocess or remote LSF node). Until this slice the
worker's two remaining seams — the plan verifier and the step-requirement gate —
were built by the legacy ``acp`` assembly and injected. This module is the
composition root that replaces that inheritance: given **only** frozen data
(the envelope JSON + the frozen plan JSON + an optional storage binding + the
read-only task artifacts under ``work_dir``), it constructs every dependency the
:class:`~acp_services.runtime.worker.FrozenWorker` needs, then runs one attempt
and writes one atomic receipt.

What this module deliberately is **not**:

* it imports no ``acp`` module (the Services dependency direction is one-way);
* it never opens, reads or writes the jobs database and never resolves a
  run root — the worker independence probe runs with an empty ``ACP_RUN_ROOT``
  and must create no DB file;
* it never advances job state, increments ``attempt`` or writes job status —
  that is the controller's job (``acp_services.runtime.receipts``);
* it launches no second task state machine: it reuses the single
  :class:`FrozenWorker` + Services-native prerequisite table
  (``acp_services.runtime.step_requirements``) + the ``cccp.calculation.run_*``
  task core.

Frozen-plan wire contract
-------------------------

The frozen plan is the JSON body the controller dispatched for *this* attempt.
Its ``identity.plan_digest`` is the **body digest** recomputed here by
:func:`frozen_plan_body_digest` (a content-free ``identity_fingerprint`` over
``payload`` + ``steps`` + ``item_symbols``); the opaque two-layer science
identity (``plan_identity`` / ``step_identities``) is carried verbatim and
cross-checked against the envelope by :class:`FrozenWorker`.  Recomputing the
body digest is what makes the composition root's plan verification genuinely
Services-native (no injected verifier) — a tampered plan body fails before any
task runs.  A controller freezing a plan for dispatch builds it with
:func:`freeze_frozen_plan` / :func:`frozen_plan_to_dict`.

Remote entry
------------

``src/acp/scheduler/remote/script_gen.py::build_remote_worker_command`` emits
``python -m acp_services.worker_bootstrap ...`` so a remote node runs the synced
Services consumer, never a hop through the legacy ``acp.cli``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from acp_services.contracts.envelope import (
    EnvelopeError,
    ExecutionEnvelope,
    ExecutionError,
    ExecutionReceipt,
    StorageBinding,
)
from acp_services.runtime.plan_contracts import CalculationResult, StepKind
from acp_services.runtime.step_requirements import (
    SATISFIED,
    PriorStep,
    RequirementOutcome,
    evaluate_prerequisite,
    is_partial_result,
)
from acp_services.runtime.worker import (
    NORMAL_REUSE_METADATA_KEY,
    PARTIAL_RESULT_METADATA_KEY,
    CccpTaskExecutor,
    DependencyNotSatisfiedError,
    FrozenPlan,
    FrozenPlanIdentity,
    FrozenStep,
    FrozenWorker,
    PlanVerificationError,
    ReceiptConflictError,
    TaskExecutionOutcome,
    TaskExecutorPort,
    WorkerError,
    build_receipt,
    decision_for_failure,
    outcome_from_decision,
    outcome_from_task_result,
    read_receipt,
    receipt_exit_code,
    write_receipt_atomic,
)
from cccp.analysis.identity import identity_fingerprint

__all__ = [
    "AssemblyError",
    "FrozenEnvelopeDecodeError",
    "FrozenPlanDecodeError",
    "FrozenPlanVerifier",
    "ServicesStepRequirements",
    "StorageBindingDecodeError",
    "WorkerBootstrapError",
    "WorkerRuntime",
    "assemble_worker",
    "freeze_frozen_plan",
    "frozen_plan_body_digest",
    "frozen_plan_from_dict",
    "frozen_plan_to_dict",
    "load_frozen_envelope",
    "load_frozen_plan",
    "load_storage_binding",
    "load_and_assemble",
    "main",
]

#: Entrypoints this composition root is allowed to consume.  A frozen envelope
#: declaring anything else was meant for a different worker role.
_WORKER_ENTRYPOINTS: frozenset[str] = frozenset({"worker.execute", "worker.bootstrap"})

_DEFAULT_RECEIPT_NAME = "execution_receipt.json"


# ── errors ──────────────────────────────────────────────────────────────────


class WorkerBootstrapError(Exception):
    """Base class for composition-root contract violations."""


class AssemblyError(WorkerBootstrapError):
    """Frozen data is missing, unreadable or internally inconsistent."""


class FrozenEnvelopeDecodeError(AssemblyError):
    """The frozen envelope JSON could not be decoded."""


class FrozenPlanDecodeError(AssemblyError):
    """The frozen plan JSON could not be decoded."""


class StorageBindingDecodeError(AssemblyError):
    """The frozen storage-binding JSON could not be decoded."""


# ── frozen-plan wire codec ──────────────────────────────────────────────────


def frozen_plan_body_digest(plan: FrozenPlan) -> str:
    """Recompute the content-free body digest of a frozen plan.

    Covers exactly the dispatched body (``payload`` + ordered ``steps`` +
    ``item_symbols``) and excludes the opaque declared science identity — the
    digest is the tamper anchor the composition root recomputes, never trusts.
    """
    return identity_fingerprint(
        {
            "scope": "frozen-plan",
            "payload": dict(plan.payload),
            "steps": [{"kind": step.kind, "payload": dict(step.payload)} for step in plan.steps],
            "item_symbols": list(plan.item_symbols),
        }
    )


def freeze_frozen_plan(
    *,
    payload: Mapping[str, Any] | None = None,
    steps: Sequence[FrozenStep] = (),
    item_symbols: Sequence[str] = (),
    plan_identity: str = "",
    step_identities: Sequence[str] = (),
) -> FrozenPlan:
    """Build a frozen plan whose declared ``plan_digest`` is its body digest.

    This is the controller-side constructor: the body digest is computed over
    the body only, so it is stable and independent of the opaque science
    identity supplied by the planner.
    """
    body = FrozenPlan(
        payload=dict(payload or {}),
        steps=tuple(steps),
        item_symbols=tuple(item_symbols),
        identity=None,
    )
    identity = FrozenPlanIdentity(
        plan_digest=frozen_plan_body_digest(body),
        plan_identity=plan_identity,
        step_identities=tuple(step_identities),
    )
    return replace(body, identity=identity)


def frozen_plan_to_dict(plan: FrozenPlan) -> dict[str, Any]:
    """Serialise *plan* to its frozen wire form (JSON-safe)."""
    identity: dict[str, Any] | None = None
    if plan.identity is not None:
        identity = {
            "plan_digest": plan.identity.plan_digest,
            "plan_identity": plan.identity.plan_identity,
            "step_identities": list(plan.identity.step_identities),
        }
    return {
        "payload": dict(plan.payload),
        "steps": [{"kind": step.kind, "payload": dict(step.payload)} for step in plan.steps],
        "item_symbols": list(plan.item_symbols),
        "identity": identity,
    }


def _as_mapping(value: Any, *, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FrozenPlanDecodeError(f"{what} must be a mapping, got {type(value).__name__}")
    return value


def frozen_plan_from_dict(payload: Mapping[str, Any]) -> FrozenPlan:
    """Decode a frozen plan wire body (strict; the composition root owns it)."""
    if not isinstance(payload, Mapping):
        raise FrozenPlanDecodeError("frozen plan payload must be a mapping")

    raw_steps = payload.get("steps") or ()
    if isinstance(raw_steps, (str, bytes)) or not isinstance(raw_steps, (list, tuple)):
        raise FrozenPlanDecodeError("frozen plan steps must be a list")
    steps: list[FrozenStep] = []
    for index, raw_step in enumerate(raw_steps):
        entry = _as_mapping(raw_step, what=f"steps[{index}]")
        kind = entry.get("kind")
        if not isinstance(kind, str) or not kind:
            raise FrozenPlanDecodeError(f"steps[{index}].kind must be a non-empty string")
        raw_body = entry.get("payload") or {}
        steps.append(
            FrozenStep(
                kind=kind, payload=dict(_as_mapping(raw_body, what=f"steps[{index}].payload"))
            )
        )

    raw_symbols = payload.get("item_symbols") or ()
    if isinstance(raw_symbols, (str, bytes)) or not isinstance(raw_symbols, (list, tuple)):
        raise FrozenPlanDecodeError("frozen plan item_symbols must be a list")
    item_symbols = tuple(str(symbol) for symbol in raw_symbols)

    identity: FrozenPlanIdentity | None = None
    raw_identity = payload.get("identity")
    if raw_identity is not None:
        ident = _as_mapping(raw_identity, what="identity")
        raw_step_ids = ident.get("step_identities") or ()
        if isinstance(raw_step_ids, (str, bytes)) or not isinstance(raw_step_ids, (list, tuple)):
            raise FrozenPlanDecodeError("identity.step_identities must be a list")
        identity = FrozenPlanIdentity(
            plan_digest=str(ident.get("plan_digest") or ""),
            plan_identity=str(ident.get("plan_identity") or ""),
            step_identities=tuple(str(entry) for entry in raw_step_ids),
        )

    raw_body = payload.get("payload") or {}
    return FrozenPlan(
        payload=dict(_as_mapping(raw_body, what="payload")),
        steps=tuple(steps),
        item_symbols=item_symbols,
        identity=identity,
    )


# ── Services-native plan verification ───────────────────────────────────────


@dataclass(frozen=True)
class FrozenPlanVerifier:
    """Recompute-backed :class:`PlanVerificationPort` (no injected verifier).

    Recomputes the frozen-plan body digest and requires it to equal the plan's
    declared ``plan_digest``; on success the declared two-layer identity is
    returned so :class:`FrozenWorker` can bind it to the envelope.  A tampered
    or re-serialised body fails here, before any task executes.
    """

    def verify(self, plan: FrozenPlan) -> FrozenPlanIdentity:
        if plan.identity is None:
            raise PlanVerificationError(
                "frozen plan carries no declared identity; the composition root "
                "requires the frozen two-layer identity (freeze_frozen_plan)"
            )
        recomputed = frozen_plan_body_digest(plan)
        if plan.identity.plan_digest != recomputed:
            raise PlanVerificationError(
                f"frozen plan body digest {recomputed!r} != declared plan_digest "
                f"{plan.identity.plan_digest!r} (tampered or frozen under another codec)"
            )
        return plan.identity


# ── Services-native step-requirement gate ───────────────────────────────────


def _entry_is_partial(entry: Any, result: CalculationResult | None) -> bool:
    if isinstance(entry, Mapping):
        if entry.get(PARTIAL_RESULT_METADATA_KEY) is True:
            return True
        if entry.get(NORMAL_REUSE_METADATA_KEY) is False:
            return True
    elif getattr(entry, PARTIAL_RESULT_METADATA_KEY, None) is True:
        return True
    return is_partial_result(result)


def _coerce_prior_steps(prior: Sequence[Any] | None) -> tuple[PriorStep, ...]:
    """Best-effort decode of upstream step observations into :class:`PriorStep`."""
    if not prior:
        return ()
    decoded: list[PriorStep] = []
    for entry in prior:
        if isinstance(entry, PriorStep):
            decoded.append(entry)
            continue
        if isinstance(entry, Mapping):
            raw_kind = entry.get("kind")
            status = str(entry.get("status") or "")
            raw_result = entry.get("result")
            result = (
                CalculationResult.from_step_result_dict(raw_result)
                if isinstance(raw_result, Mapping)
                else None
            )
        else:
            raw_kind = getattr(entry, "kind", None)
            status = str(getattr(entry, "status", "") or "")
            result = getattr(entry, "result", None)
        try:
            kind = raw_kind if isinstance(raw_kind, StepKind) else StepKind(str(raw_kind))
        except (TypeError, ValueError):
            continue
        decoded.append(
            PriorStep(
                kind=kind,
                status=status,
                result=result,
                partial_result=_entry_is_partial(entry, result),
            )
        )
    return tuple(decoded)


@dataclass(frozen=True)
class ServicesStepRequirements:
    """Expose the Services-native prerequisite table as :class:`StepRequirementPort`.

    The frozen-plan step ``kind`` is a wire string; it is coerced onto
    :class:`StepKind` and delegated to
    :func:`acp_services.runtime.step_requirements.evaluate_prerequisite` — the
    single implementation migrated in todo 77.  A kind with no registered
    requirement is unconditionally satisfied (mirrors the table semantics).
    """

    def evaluate_prerequisite(
        self,
        prior: Any,
        kind: Any,
        item_symbols: Any,
    ) -> RequirementOutcome:
        try:
            step_kind = kind if isinstance(kind, StepKind) else StepKind(str(kind))
        except (TypeError, ValueError):
            return SATISFIED
        symbols = tuple(str(symbol) for symbol in (item_symbols or ()))
        return evaluate_prerequisite(_coerce_prior_steps(prior), step_kind, symbols)


# ── probe executor (worker-independence probe only; never a default) ─────────


@dataclass(frozen=True)
class _ProbeExecutor:
    """Deterministic executor for the independence probe / remote mock chain.

    Runs no scientific binary; ``state`` selects one exact D01 truth-table row
    (``completed``=b, ``failed``=a, ``partial``=c, ``partial-missing``=d) and the
    synthetic result is fed through the same ``outcome_from_task_result`` the
    CCCP executor uses.  Selected only through the explicit ``--probe-executor``
    flag; the production default remains :class:`CccpTaskExecutor`.
    """

    state: str = "completed"

    def _result(self) -> Any:
        coordinates = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        base = {
            "task": "optimize",
            "symbols": ("C", "O"),
            "payload": None,
            "metadata": {},
        }
        if self.state == "failed":
            return SimpleNamespace(
                **base,
                status="failed",
                complete=False,
                coordinates=None,
                errors=(
                    ExecutionError(
                        code="probe_failure",
                        message="probe executor was asked to fail",
                        retryable=False,
                    ),
                ),
            )
        if self.state == "partial":
            return SimpleNamespace(
                **base,
                status="completed",
                complete=False,
                coordinates=coordinates,
                errors=(),
            )
        if self.state == "partial-missing":
            return SimpleNamespace(
                **base,
                status="completed",
                complete=False,
                coordinates=None,
                errors=(),
            )
        return SimpleNamespace(
            **base,
            status="completed",
            complete=True,
            coordinates=coordinates,
            errors=(),
        )

    def execute(self, scope: Any) -> TaskExecutionOutcome:
        return outcome_from_task_result(self._result(), work_dir=scope.work_dir)


# ── frozen-data loaders ─────────────────────────────────────────────────────


def load_frozen_envelope(path: Path | str) -> ExecutionEnvelope:
    """Decode a frozen envelope JSON file (typed error on any failure)."""
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FrozenEnvelopeDecodeError(f"cannot read frozen envelope {target}: {exc}") from exc
    try:
        return ExecutionEnvelope.from_dict(payload)
    except EnvelopeError as exc:
        raise FrozenEnvelopeDecodeError(f"invalid frozen envelope {target}: {exc}") from exc


def load_frozen_plan(path: Path | str) -> FrozenPlan:
    """Decode a frozen plan JSON file (typed error on any failure)."""
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FrozenPlanDecodeError(f"cannot read frozen plan {target}: {exc}") from exc
    return frozen_plan_from_dict(payload)


def load_storage_binding(path: Path | str) -> StorageBinding:
    """Decode a frozen storage-binding JSON file (typed error on any failure)."""
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StorageBindingDecodeError(f"cannot read storage binding {target}: {exc}") from exc
    try:
        return StorageBinding.from_dict(payload)
    except EnvelopeError as exc:
        raise StorageBindingDecodeError(f"invalid storage binding {target}: {exc}") from exc


# ── the assembled runtime ───────────────────────────────────────────────────


def _error_from_worker(exc: WorkerError) -> ExecutionError:
    if isinstance(exc, DependencyNotSatisfiedError):
        reason = getattr(exc, "reason", "") or str(exc)
        return ExecutionError(code="dependency_not_satisfied", message=reason, retryable=False)
    if isinstance(exc, PlanVerificationError):
        return ExecutionError(code="plan_verification_failed", message=str(exc), retryable=False)
    if isinstance(exc, ReceiptConflictError):
        return ExecutionError(code="receipt_conflict", message=str(exc), retryable=False)
    return ExecutionError(code="worker_error", message=str(exc), retryable=False)


@dataclass(frozen=True)
class WorkerRuntime:
    """Frozen data assembled into a ready-to-run worker (one attempt).

    Constructed by :func:`assemble_worker` / :func:`load_and_assemble`; carries
    no store, no database handle and no mutable job state.  :meth:`run`
    executes once and always returns a receipt — a thrown worker/executor
    failure is converted into a **structured failed receipt** with typed
    :class:`ExecutionError` entries, never a crash trace.
    """

    envelope: ExecutionEnvelope
    plan: FrozenPlan
    work_dir: Path
    receipt_path: Path
    storage_binding: StorageBinding | None
    worker: FrozenWorker

    def run(
        self, *, prior_steps: Sequence[Any] = (), item_symbols: Sequence[str] | None = None
    ) -> ExecutionReceipt:
        """Execute one attempt; return the (success or structured-failure) receipt."""
        try:
            return self.worker.execute(
                self.envelope,
                self.plan,
                receipt_path=self.receipt_path,
                work_dir=self.work_dir,
                storage_binding=self.storage_binding,
                prior_steps=prior_steps,
                item_symbols=item_symbols,
            )
        except WorkerError as exc:
            return self._write_failure_receipt(_error_from_worker(exc))
        except Exception as exc:  # noqa: BLE001 — executor failure must become a typed receipt
            return self._write_failure_receipt(
                ExecutionError(
                    code="executor_error",
                    message=f"{type(exc).__name__}: {exc}",
                    retryable=False,
                )
            )

    def _write_failure_receipt(self, error: ExecutionError) -> ExecutionReceipt:
        existing = read_receipt(self.receipt_path)
        if existing is not None:
            return existing
        outcome = outcome_from_decision(decision_for_failure(error), work_dir=self.work_dir)
        receipt = build_receipt(self.envelope, outcome)
        write_receipt_atomic(self.receipt_path, receipt)
        return receipt


def _select_storage_binding(
    envelope: ExecutionEnvelope, binding: StorageBinding | None
) -> StorageBinding | None:
    if binding is not None:
        if envelope.data_bindings and binding.binding_id not in {
            entry.binding_id for entry in envelope.data_bindings
        }:
            raise AssemblyError(
                f"storage binding {binding.binding_id!r} is not declared by the frozen envelope"
            )
        return binding
    return envelope.data_bindings[0] if envelope.data_bindings else None


def assemble_worker(
    *,
    envelope: ExecutionEnvelope,
    plan: FrozenPlan,
    work_dir: Path | str,
    receipt_path: Path | str | None = None,
    storage_binding: StorageBinding | None = None,
    executor: TaskExecutorPort | None = None,
) -> WorkerRuntime:
    """Build the Services-native worker from frozen objects (no injection)."""
    if envelope.entrypoint not in _WORKER_ENTRYPOINTS:
        raise AssemblyError(
            f"envelope entrypoint {envelope.entrypoint!r} is not a worker entrypoint "
            f"{sorted(_WORKER_ENTRYPOINTS)}"
        )
    resolved_work_dir = Path(work_dir)
    resolved_receipt = (
        Path(receipt_path)
        if receipt_path is not None
        else resolved_work_dir / _DEFAULT_RECEIPT_NAME
    )
    worker = FrozenWorker(
        executor=executor if executor is not None else CccpTaskExecutor(),
        step_requirements=ServicesStepRequirements(),
        plan_verifier=FrozenPlanVerifier(),
    )
    return WorkerRuntime(
        envelope=envelope,
        plan=plan,
        work_dir=resolved_work_dir,
        receipt_path=resolved_receipt,
        storage_binding=_select_storage_binding(envelope, storage_binding),
        worker=worker,
    )


def load_and_assemble(
    *,
    envelope_path: Path | str,
    plan_path: Path | str,
    work_dir: Path | str,
    receipt_path: Path | str | None = None,
    binding_path: Path | str | None = None,
    executor: TaskExecutorPort | None = None,
) -> WorkerRuntime:
    """Read frozen files from disk and assemble the worker (new-process path)."""
    envelope = load_frozen_envelope(envelope_path)
    plan = load_frozen_plan(plan_path)
    binding = load_storage_binding(binding_path) if binding_path is not None else None
    return assemble_worker(
        envelope=envelope,
        plan=plan,
        work_dir=work_dir,
        receipt_path=receipt_path,
        storage_binding=binding,
        executor=executor,
    )


# ── process entry ───────────────────────────────────────────────────────────


def _write_assembly_failure(
    envelope: ExecutionEnvelope,
    receipt_path: Path,
    error: ExecutionError,
    *,
    work_dir: Path | None = None,
) -> ExecutionReceipt:
    existing = read_receipt(receipt_path)
    if existing is not None:
        return existing
    outcome = outcome_from_decision(decision_for_failure(error), work_dir=work_dir)
    receipt = build_receipt(envelope, outcome)
    write_receipt_atomic(receipt_path, receipt)
    return receipt


def _write_unbound_failure(receipt_path: Path, code: str, message: str) -> None:
    """Structured failure document when the envelope itself cannot be decoded."""
    document = {
        "schema": "worker_assembly_error",
        "state": "failed",
        "errors": [{"code": code, "message": message, "retryable": False}],
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Worker process entrypoint (``python -m acp_services.worker_bootstrap``)."""
    parser = argparse.ArgumentParser(
        prog="acp_services.worker_bootstrap",
        description="Assemble and run one frozen-envelope worker attempt from frozen data only.",
    )
    parser.add_argument("--envelope", required=True, help="frozen ExecutionEnvelope JSON path")
    parser.add_argument("--plan", required=True, help="frozen plan JSON path")
    parser.add_argument(
        "--work-dir", required=True, help="task work dir holding read-only artifacts"
    )
    parser.add_argument(
        "--receipt",
        default=None,
        help="receipt output path (default: <work-dir>/execution_receipt.json)",
    )
    parser.add_argument("--binding", default=None, help="optional StorageBinding JSON path")
    parser.add_argument(
        "--probe-executor",
        choices=("ok", "fail", "partial", "partial-missing"),
        default=None,
        help="worker-independence probe only: run a deterministic executor instead of cccp",
    )
    args = parser.parse_args(argv)

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = Path(args.receipt) if args.receipt else work_dir / _DEFAULT_RECEIPT_NAME

    try:
        envelope = load_frozen_envelope(args.envelope)
    except AssemblyError as exc:
        _write_unbound_failure(receipt_path, "envelope_unreadable", str(exc))
        print(json.dumps({"state": "failed", "code": "envelope_unreadable", "message": str(exc)}))
        return 2

    try:
        plan = load_frozen_plan(args.plan)
        binding = load_storage_binding(args.binding) if args.binding else None
        probe_state = {
            "ok": "completed",
            "fail": "failed",
            "partial": "partial",
            "partial-missing": "partial-missing",
        }
        executor: TaskExecutorPort | None = (
            _ProbeExecutor(state=probe_state[str(args.probe_executor)])
            if args.probe_executor
            else None
        )
        runtime = assemble_worker(
            envelope=envelope,
            plan=plan,
            work_dir=work_dir,
            receipt_path=receipt_path,
            storage_binding=binding,
            executor=executor,
        )
    except AssemblyError as exc:
        receipt = _write_assembly_failure(
            envelope,
            receipt_path,
            ExecutionError(code="assembly_failed", message=str(exc), retryable=False),
            work_dir=work_dir,
        )
        print(json.dumps(receipt.to_dict(), sort_keys=True))
        return 1

    receipt = runtime.run()
    print(json.dumps(receipt.to_dict(), sort_keys=True))
    return receipt_exit_code(receipt)


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
