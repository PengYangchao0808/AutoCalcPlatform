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
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
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
from acp_services.runtime.attempt_freeze import (
    DEFAULT_LEASE_SECONDS,
    AttemptLockExecutionRight,
    attempt_lock_path,
)
from acp_services.runtime.attempt_recovery import CgroupAttemptRecovery
from acp_services.runtime.execution_chain import (
    FROZEN_GROUP_MANIFEST_FILENAME,
    FrozenGroupError,
    inspect_frozen_group_dir,
)
from acp_services.runtime.execution_right import (
    AlwaysHeldExecutionRight,
    ExecutionRightPort,
    ExecutionRightVerdict,
    WorkerExecutionResult,
    WorkerRefusal,
)
from acp_services.runtime.plan_contracts import CalculationResult, StepKind
from acp_services.runtime.receipt_identity import (
    ReceiptIdentity,
    ReceiptIdentityComparison,
    compare_receipt_identity,
)
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
    frozen_plan_body_digest,
    frozen_plan_to_dict,
    outcome_from_decision,
    outcome_from_task_result,
    read_receipt,
    receipt_exit_code,
    write_receipt_atomic,
)

__all__ = [
    "AssemblyError",
    "FrozenEnvelopeDecodeError",
    "FrozenPlanDecodeError",
    "ExecutionRightPort",
    "FrozenPlanVerifier",
    "ServicesStepRequirements",
    "StorageBindingDecodeError",
    "WorkerBootstrapError",
    "WorkerRefusal",
    "WorkerRuntime",
    "assemble_worker",
    "freeze_frozen_plan",
    "frozen_plan_body_digest",
    "frozen_plan_from_dict",
    "frozen_plan_to_dict",
    "load_and_assemble",
    "load_frozen_envelope",
    "load_frozen_plan",
    "load_storage_binding",
    "main",
    "validate_frozen_group_inputs",
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
#
# ``frozen_plan_body_digest`` / ``frozen_plan_to_dict`` live in
# ``acp_services.runtime.worker`` (07-A / todo 24) so the frozen-attempt
# producer shares the exact digest/codec; they stay importable from here.


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
        return outcome_from_task_result(self._result())


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


def validate_frozen_group_inputs(
    envelope_path: Path | str,
    plan_path: Path | str,
    binding_path: Path | str | None = None,
    *,
    require_manifest: bool = False,
) -> None:
    """Reject a partial/mixed frozen group before anything executes (07-A).

    When ``frozen_group.json`` sits beside the envelope, the manifest is the
    group's commit marker: every member hash is re-verified here and the
    paths this worker consumes must be exactly the group's members.  Without
    a manifest (direct/remote flat hand-off) the loose files keep the
    historical verification path (plan digest + envelope identity inside the
    worker).
    """
    envelope_file = Path(envelope_path)
    if not (envelope_file.parent / FROZEN_GROUP_MANIFEST_FILENAME).is_file():
        if require_manifest:
            raise AssemblyError(
                "frozen group manifest required for this entry; refusing loose member files"
            )
        return
    try:
        group = inspect_frozen_group_dir(envelope_file.parent)
    except FrozenGroupError as exc:
        raise AssemblyError(f"frozen input group rejected: {exc}") from exc
    if envelope_file != group.envelope_path or Path(plan_path) != group.plan_path:
        raise AssemblyError("envelope/plan paths are not the frozen group's members")
    if binding_path is None:
        if group.binding_path is not None:
            raise AssemblyError("frozen group declares binding.json but no --binding was supplied")
    elif group.binding_path is None:
        raise AssemblyError("--binding supplied but the frozen group declares no binding member")
    elif Path(binding_path) != group.binding_path:
        raise AssemblyError("--binding path is not the frozen group's binding member")


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


# ── receipt conflict + refusal diagnostics ──────────────────────────────────


@dataclass(frozen=True)
class _ReceiptConflict:
    """Why an existing receipt cannot be reused (identity mismatch or corruption)."""

    code: str
    message: str = ""
    comparison: ReceiptIdentityComparison | None = None
    existing: ExecutionReceipt | None = None


def _write_json_document_atomic(path: Path, document: Mapping[str, Any]) -> None:
    """Atomic (temp + ``os.replace``) JSON diagnostic write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(document, sort_keys=True, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _conflict_path(receipt_path: Path) -> Path:
    return receipt_path.with_name(receipt_path.name + ".conflict.json")


def _refusal_path(receipt_path: Path) -> Path:
    return receipt_path.with_name(receipt_path.name + ".refused.json")


def _inspect_existing_receipt(
    envelope: ExecutionEnvelope, receipt_path: Path
) -> tuple[ExecutionReceipt | None, _ReceiptConflict | None]:
    """Return ``(reusable receipt, conflict)`` for the canonical receipt path.

    A readable receipt is reused only when its **full** frozen identity matches
    the current envelope (task-6 predicate).  Corrupt JSON is a typed conflict,
    never an exception escaping the handler.
    """
    try:
        existing = read_receipt(receipt_path)
    except (ReceiptConflictError, EnvelopeError) as exc:
        # Unreadable JSON or a structurally invalid receipt document: both are
        # evidence-preserving typed conflicts, never an escaping traceback.
        return None, _ReceiptConflict(code="unreadable_receipt", message=str(exc))
    if existing is None:
        return None, None
    comparison = compare_receipt_identity(existing, envelope)
    if comparison.matches:
        return existing, None
    return None, _ReceiptConflict(
        code="receipt_identity_mismatch",
        message=(
            f"existing receipt at {receipt_path} does not carry the current "
            f"envelope identity ({comparison.describe()})"
        ),
        comparison=comparison,
        existing=existing,
    )


def _write_conflict_document(
    *,
    envelope: ExecutionEnvelope,
    receipt_path: Path,
    conflict: _ReceiptConflict,
    current_receipt: ExecutionReceipt | None = None,
) -> None:
    """Write ``<receipt>.conflict.json`` next to (never over) the original."""
    current_identity = ReceiptIdentity.from_envelope(envelope)
    existing_identity = (
        ReceiptIdentity.from_receipt(conflict.existing) if conflict.existing is not None else None
    )
    document: dict[str, Any] = {
        "schema": "worker_receipt_conflict",
        "state": "conflict",
        "code": conflict.code,
        "message": conflict.message,
        "receipt_path": str(receipt_path),
        # The frozen attempt this worker was asked to execute.
        "current_identity": current_identity.to_dict(),
        # A receipt for the current attempt must carry exactly this identity.
        "expected_identity": current_identity.to_dict(),
        # The preserved evidence already on disk (``None`` when unreadable).
        "existing_identity": existing_identity.to_dict() if existing_identity else None,
        "conflict_field": conflict.comparison.code if conflict.comparison else "",
        "conflicts": (
            [entry.to_dict() for entry in conflict.comparison.conflicts]
            if conflict.comparison is not None
            else []
        ),
        "existing_receipt_sha256": _file_sha256(receipt_path),
        "current_receipt": current_receipt.to_dict() if current_receipt is not None else None,
    }
    _write_json_document_atomic(_conflict_path(receipt_path), document)


def _build_failure_receipt(
    envelope: ExecutionEnvelope, error: ExecutionError, *, work_dir: Path | None
) -> ExecutionReceipt:
    outcome = outcome_from_decision(decision_for_failure(error), work_dir=work_dir)
    return build_receipt(envelope, outcome)


def _write_refusal(
    envelope: ExecutionEnvelope, receipt_path: Path, verdict: ExecutionRightVerdict
) -> WorkerRefusal:
    refusal = WorkerRefusal(envelope=envelope, verdict=verdict, receipt_path=str(receipt_path))
    _write_json_document_atomic(_refusal_path(receipt_path), refusal.to_dict())
    return refusal


class _ExecutionRightLostError(Exception):
    """Raised at the authoritative-write seam when the claim is no longer held."""

    def __init__(self, verdict: ExecutionRightVerdict) -> None:
        super().__init__(verdict.detail or "execution right lost")
        self.verdict = verdict


@dataclass(frozen=True)
class WorkerRuntime:
    """Frozen data assembled into a ready-to-run worker (one attempt).

    Constructed by :func:`assemble_worker` / :func:`load_and_assemble`; carries
    no store, no database handle and no mutable job state.  :meth:`run`
    executes once and returns an authoritative receipt — a thrown
    worker/executor failure is converted into a **structured failed receipt**
    with typed :class:`ExecutionError` entries, never a crash trace.

    A non-holder of the injected :class:`ExecutionRightPort` (competition loser,
    duplicate delivery, lost lease) gets a :class:`WorkerRefusal` instead: no
    authoritative receipt/``task_result`` is written and the canonical receipt
    path is never touched.
    """

    envelope: ExecutionEnvelope
    plan: FrozenPlan
    work_dir: Path
    receipt_path: Path
    storage_binding: StorageBinding | None
    worker: FrozenWorker
    execution_right: ExecutionRightPort = field(default_factory=AlwaysHeldExecutionRight)
    #: Optional control-intent poll injected into the executor (M2-04c).  A DAG
    #: attempt polled ``cancel`` stops before the next step dispatch.
    control_poll: Callable[[], str] | None = None

    def run(
        self, *, prior_steps: Sequence[Any] = (), item_symbols: Sequence[str] | None = None
    ) -> WorkerExecutionResult:
        """Execute one attempt; return a receipt or a non-authoritative refusal."""
        acquired = self.execution_right.acquire(self.envelope)
        if not acquired.held:
            return _write_refusal(self.envelope, self.receipt_path, acquired)
        try:
            return self.worker.execute(
                self.envelope,
                self.plan,
                receipt_path=self.receipt_path,
                work_dir=self.work_dir,
                storage_binding=self.storage_binding,
                prior_steps=prior_steps,
                item_symbols=item_symbols,
                before_write=self._require_execution_right,
                execution_right=self.execution_right,
                control_poll=self.control_poll,
            )
        except _ExecutionRightLostError as exc:
            return _write_refusal(self.envelope, self.receipt_path, exc.verdict)
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

    def _require_execution_right(self) -> None:
        """Write seam: raise ``_ExecutionRightLostError`` when the claim is gone."""
        verdict = self.execution_right.verify(self.envelope)
        if not verdict.held:
            raise _ExecutionRightLostError(verdict)

    def _write_failure_receipt(self, error: ExecutionError) -> WorkerExecutionResult:
        # Re-check the right at the write seam: a worker that lost it must not
        # publish an authoritative failure.
        verdict = self.execution_right.verify(self.envelope)
        if not verdict.held:
            return _write_refusal(self.envelope, self.receipt_path, verdict)
        existing, conflict = _inspect_existing_receipt(self.envelope, self.receipt_path)
        if existing is not None:
            return existing
        if conflict is not None:
            # Preserve the original bytes; publish the current-attempt failed
            # receipt in the conflict document and return it for the controller.
            receipt = _build_failure_receipt(self.envelope, error, work_dir=None)
            _write_conflict_document(
                envelope=self.envelope,
                receipt_path=self.receipt_path,
                conflict=conflict,
                current_receipt=receipt,
            )
            return receipt
        receipt = _build_failure_receipt(self.envelope, error, work_dir=self.work_dir)
        write_receipt_atomic(self.receipt_path, receipt)
        return receipt


def _plan_carries_bounded_dag(plan: FrozenPlan) -> bool:
    """True when the frozen plan declares a bounded recipe DAG (M2-04d)."""
    return "bounded_step_dag" in dict(plan.payload or {})


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
    execution_right: ExecutionRightPort | None = None,
    control_poll: Callable[[], str] | None = None,
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
    resolved_executor = executor if executor is not None else CccpTaskExecutor()
    if _plan_carries_bounded_dag(plan):
        from acp_services.runtime.step_dag_worker import StepDagTaskExecutor

        if executor is None:
            resolved_executor = StepDagTaskExecutor()
    worker = FrozenWorker(
        executor=resolved_executor,
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
        execution_right=(
            execution_right if execution_right is not None else AlwaysHeldExecutionRight()
        ),
        control_poll=control_poll,
    )


def load_and_assemble(
    *,
    envelope_path: Path | str,
    plan_path: Path | str,
    work_dir: Path | str,
    receipt_path: Path | str | None = None,
    binding_path: Path | str | None = None,
    executor: TaskExecutorPort | None = None,
    execution_right: ExecutionRightPort | None = None,
    control_poll: Callable[[], str] | None = None,
) -> WorkerRuntime:
    """Read frozen files from disk and assemble the worker (new-process path)."""
    validate_frozen_group_inputs(envelope_path, plan_path, binding_path)
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
        execution_right=execution_right,
        control_poll=control_poll,
    )


# ── process entry ───────────────────────────────────────────────────────────


def _write_assembly_failure(
    envelope: ExecutionEnvelope,
    receipt_path: Path,
    error: ExecutionError,
    *,
    work_dir: Path | None = None,
    execution_right: ExecutionRightPort | None = None,
) -> WorkerExecutionResult:
    """Assembly failure receipt — never silently reusing another identity's receipt."""
    port = execution_right if execution_right is not None else AlwaysHeldExecutionRight()
    verdict = port.verify(envelope)
    if not verdict.held:
        return _write_refusal(envelope, receipt_path, verdict)
    existing, conflict = _inspect_existing_receipt(envelope, receipt_path)
    if existing is not None:
        return existing
    if conflict is not None:
        receipt = _build_failure_receipt(envelope, error, work_dir=None)
        _write_conflict_document(
            envelope=envelope,
            receipt_path=receipt_path,
            conflict=conflict,
            current_receipt=receipt,
        )
        return receipt
    receipt = _build_failure_receipt(envelope, error, work_dir=work_dir)
    write_receipt_atomic(receipt_path, receipt)
    return receipt


def _unbound_diagnostic_path(receipt_path: Path) -> Path:
    return receipt_path.with_name(receipt_path.name + ".unbound.json")


def _write_unbound_failure(receipt_path: Path, code: str, message: str) -> Path:
    """Structured failure document when the envelope itself cannot be decoded.

    R04: an assembly error raised before any identity can be bound (and before
    the execution right could possibly be acquired) must never touch the
    canonical receipt path — a pre-existing authoritative receipt (including a
    completed one) stays byte-identical.  The diagnostic lands beside it as
    ``<receipt>.unbound.json`` and on stderr.
    """
    document = {
        "schema": "worker_assembly_error",
        "state": "failed",
        "errors": [{"code": code, "message": message, "retryable": False}],
        "receipt_path": str(receipt_path),
    }
    diagnostic = _unbound_diagnostic_path(receipt_path)
    diagnostic.parent.mkdir(parents=True, exist_ok=True)
    _write_json_document_atomic(diagnostic, document)
    print(json.dumps({"state": "failed", "code": code, "message": message}))
    return diagnostic


def _result_exit_code(result: WorkerExecutionResult) -> int:
    """Exit code for a worker result: refusal code, else the receipt row code."""
    if isinstance(result, WorkerRefusal):
        return result.exit_code
    return receipt_exit_code(result)


def main(
    argv: Sequence[str] | None = None,
    *,
    execution_right: ExecutionRightPort | None = None,
) -> int:
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
        "--require-frozen-group",
        action="store_true",
        help=(
            "require the complete frozen-group manifest beside the envelope (remote entry); "
            "loose envelope/plan files are rejected"
        ),
    )
    parser.add_argument(
        "--attempt-lock",
        nargs="?",
        const="",
        default=None,
        help=(
            "atomically acquire the exclusive execution right (attempt.lock, default "
            "WORK/00_RUNTIME/attempt.lock or the given path) before invoking the executor; "
            "a competition loser writes only a refusal diagnostic"
        ),
    )
    parser.add_argument(
        "--lease-seconds",
        type=float,
        default=DEFAULT_LEASE_SECONDS,
        help="claim lease seconds for --attempt-lock",
    )
    parser.add_argument(
        "--probe-executor",
        choices=("ok", "fail", "partial", "partial-missing"),
        default=None,
        help="worker-independence probe only: run a deterministic executor instead of cccp",
    )
    parser.add_argument(
        "--execution-cgroup-root",
        default=os.environ.get("ACP_EXECUTION_CGROUP_ROOT"),
        help="delegated cgroup v2 root for checked local recovery; requires --attempt-lock",
    )
    args = parser.parse_args(argv)
    if args.execution_cgroup_root and args.attempt_lock is None:
        parser.error("--execution-cgroup-root requires --attempt-lock")

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = Path(args.receipt) if args.receipt else work_dir / _DEFAULT_RECEIPT_NAME

    try:
        validate_frozen_group_inputs(
            args.envelope,
            args.plan,
            args.binding,
            require_manifest=bool(args.require_frozen_group),
        )
    except AssemblyError as exc:
        _write_unbound_failure(receipt_path, "frozen_group_invalid", str(exc))
        return 2

    lock_path = Path(args.attempt_lock) if args.attempt_lock else attempt_lock_path(work_dir)
    recovery = CgroupAttemptRecovery(
        Path(args.execution_cgroup_root) if args.execution_cgroup_root else None
    )
    right_port = (
        execution_right
        if execution_right is not None
        else (
            AttemptLockExecutionRight(
                lock_path=lock_path,
                lease_seconds=args.lease_seconds,
                recovery_check=recovery,
                execution_scope=recovery if recovery.root is not None else None,
            )
            if args.attempt_lock is not None
            else AlwaysHeldExecutionRight()
        )
    )

    try:
        envelope = load_frozen_envelope(args.envelope)
    except AssemblyError as exc:
        _write_unbound_failure(receipt_path, "envelope_unreadable", str(exc))
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
            execution_right=right_port,
        )
    except AssemblyError as exc:
        result = _write_assembly_failure(
            envelope,
            receipt_path,
            ExecutionError(code="assembly_failed", message=str(exc), retryable=False),
            work_dir=work_dir,
            execution_right=right_port,
        )
        print(json.dumps(result.to_dict(), sort_keys=True))
        return _result_exit_code(result)

    result = runtime.run()
    print(json.dumps(result.to_dict(), sort_keys=True))
    return _result_exit_code(result)


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
