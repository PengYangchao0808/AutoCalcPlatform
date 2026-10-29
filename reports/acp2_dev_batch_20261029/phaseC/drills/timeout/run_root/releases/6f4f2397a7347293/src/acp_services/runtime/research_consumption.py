"""Research-client recovery metadata over the existing proposal CAS authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from acp_services.runtime.research_service import (
    ResearchOperationConflictError,
    ResearchOperationError,
    ResearchOperations,
)
from acp_services.workspace.command_identity import CommandIdentity

__all__ = [
    "validate_client_attempt",
    "checkpoint_client_state",
    "consumer_budget_usage",
    "client_task_reference",
]


def validate_client_attempt(task: Mapping[str, Any], current: Any, project: str) -> None:
    """A client checkpoint/read may never adopt another project or attempt."""
    job_id = str(task.get("job_id") or task.get("task_id") or "")
    if (
        current is None
        or str(current.id) != job_id
        or current.project_id != project
        or task.get("project_id") != project
        or isinstance(task.get("attempt"), bool)
        or current.attempt != task.get("attempt")
    ):
        raise ResearchOperationConflictError(
            "fenced", "client reference is not the current project attempt"
        )


def client_task_reference(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate recovery payload structure before resolving any task."""
    kind = entry.get("kind")
    value = entry.get("value")
    if kind not in {"task_ref", "report", "checkpoint"} or not isinstance(value, Mapping):
        raise ResearchOperationError("input_invalid", "unknown or malformed client checkpoint")
    handle = value.get("handle", value)
    if not isinstance(handle, Mapping):
        raise ResearchOperationError("input_invalid", "checkpoint handle must be an object")
    task = value if kind == "task_ref" else handle.get("task_ref")
    if not isinstance(task, Mapping):
        raise ResearchOperationError("input_invalid", "client checkpoint requires a TaskRef")
    return task


def checkpoint_client_state(
    *,
    operations: ResearchOperations,
    command: CommandIdentity,
    proposal_ref: str,
    proposal: Mapping[str, Any],
    current_task: Any,
    entry: Mapping[str, Any],
    expected_revision: int,
    plan: Mapping[str, Any],
    session_seed: str,
) -> dict[str, Any]:
    """CAS one client's recovery metadata; never writes worker checkpoints.

    A task-bound entry is checked against the Controller's attempt observation.
    Proposal CAS fences concurrent clients; execution rights remain worker-owned.
    """
    kind = str(entry.get("kind"))
    value = entry.get("value")
    task = client_task_reference(entry)
    validate_client_attempt(task, current_task, str(command.project_id or ""))
    document = dict(proposal)
    state = dict(document.get("rph_state") or {})
    state[str(kind)] = dict(value)
    document["rph_state"] = state
    return operations.proposal_update_cas(
        command=command,
        proposal_ref=proposal_ref,
        expected_revision=expected_revision,
        proposal=document,
        plan=plan,
        session_seed=session_seed,
    )


def consumer_budget_usage(projection: Any, model: Mapping[str, Any]) -> dict[str, Any]:
    """Project measured ledger counters; missing elapsed metrics stay explicit."""
    counters = {} if projection is None else projection.consumed
    rows = model["receipt_rows"]

    def tokens(field: str) -> int:
        return sum(int((row.get("usage") or {}).get(field) or 0) for row in rows)

    # Zero in an unmeasured field is a lower bound, not a measured zero. Clients
    # must retain their consumed wall/queue budget when restoring (max rule).
    return {
        "source": "platform-receipt",
        "used": {
            "model_requests": model["model_requests"],
            "input_tokens": tokens("input_tokens"),
            "output_tokens": tokens("output_tokens"),
            "task_refs": int(counters.get("task_ref", 0)),
            "recipe_steps": int(counters.get("recipe_step", 0)),
            "qc_invocations": int(counters.get("qc_invocation", 0)),
            "wall_seconds": 0,
            "queue_seconds": 0,
        },
        "unmeasured": ["wall_seconds", "queue_seconds"]
        + [
            field
            for field in ("input_tokens", "output_tokens")
            if any((row.get("usage") or {}).get(field) is None for row in rows)
        ],
        "receipts": [str(row["receipt_id"]) for row in rows],
        "limitations": [
            "wall/queue values are lower bounds; per-attempt deadlines apply",
            "cumulative tokens/core-hours are not hard enforced",
        ],
    }
