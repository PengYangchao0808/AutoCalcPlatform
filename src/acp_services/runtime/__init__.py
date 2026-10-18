"""ACP Services runtime package — frozen-envelope worker + controller (B06).

Re-exports only (ANTI #5): the frozen-plan worker lives in
:mod:`acp_services.runtime.worker` and the controller-side receipt gate in
:mod:`acp_services.runtime.receipts`. This package never imports ``acp``.
"""

from __future__ import annotations

from acp_services.runtime.receipts import (
    ReceiptRejectedError,
    ReceiptVerdict,
    advance_job_state,
    verify_receipt,
)
from acp_services.runtime.worker import (
    CccpTaskExecutor,
    DeclaredPlanVerifier,
    DependencyNotSatisfiedError,
    ExecutionScope,
    FrozenPlan,
    FrozenPlanIdentity,
    FrozenStep,
    FrozenWorker,
    PlanVerificationError,
    PlanVerificationPort,
    ReceiptConflictError,
    TaskExecutionOutcome,
    TaskExecutorPort,
    WorkerError,
    build_receipt,
    read_receipt,
    write_receipt_atomic,
)

__all__ = [
    "CccpTaskExecutor",
    "DeclaredPlanVerifier",
    "DependencyNotSatisfiedError",
    "ExecutionScope",
    "FrozenPlan",
    "FrozenPlanIdentity",
    "FrozenStep",
    "FrozenWorker",
    "PlanVerificationError",
    "PlanVerificationPort",
    "ReceiptConflictError",
    "ReceiptRejectedError",
    "ReceiptVerdict",
    "TaskExecutionOutcome",
    "TaskExecutorPort",
    "WorkerError",
    "advance_job_state",
    "build_receipt",
    "read_receipt",
    "verify_receipt",
    "write_receipt_atomic",
]
