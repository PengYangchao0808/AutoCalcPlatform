"""Atomic persistence for the calculation task checkpoint."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .contracts import Checkpoint, JsonValue

__all__ = ["Checkpoint", "CheckpointMismatchError", "load_checkpoint", "write_checkpoint"]

CHECKPOINT_FILENAME: Final = "checkpoint.json"


@dataclass(frozen=True, slots=True)
class CheckpointMismatchError(Exception):
    """Raised when a checkpoint belongs to a different calculation plan."""

    checkpoint_path: Path
    expected_fingerprint: str
    actual_fingerprint: str

    def __str__(self) -> str:
        return (
            f"checkpoint fingerprint mismatch at {self.checkpoint_path}: "
            f"expected {self.expected_fingerprint!r}, got {self.actual_fingerprint!r}"
        )


def _checkpoint_path(directory: Path | str) -> Path:
    return Path(directory) / CHECKPOINT_FILENAME


def _checkpoint_payload(checkpoint: Checkpoint) -> dict[str, JsonValue]:
    return {
        "task_id": checkpoint.task_id,
        "workflow": checkpoint.workflow,
        "plan_fingerprint": checkpoint.plan_fingerprint,
        "step_states": checkpoint.step_states,
        "items_state": checkpoint.items_state,
        "attempts": checkpoint.attempts,
    }


def _checkpoint_from_payload(payload: JsonValue) -> Checkpoint | None:
    if not isinstance(payload, dict):
        return None

    task_id = payload.get("task_id")
    workflow = payload.get("workflow")
    plan_fingerprint = payload.get("plan_fingerprint")
    step_states = payload.get("step_states")
    items_state = payload.get("items_state")
    attempts = payload.get("attempts")

    if not isinstance(task_id, str) or not isinstance(workflow, str):
        return None
    if not isinstance(plan_fingerprint, str):
        return None
    if not isinstance(step_states, list) or not isinstance(items_state, dict):
        return None
    if not isinstance(attempts, int) or isinstance(attempts, bool):
        return None

    return Checkpoint(
        task_id=task_id,
        workflow=workflow,
        plan_fingerprint=plan_fingerprint,
        step_states=step_states,
        items_state=items_state,
        attempts=attempts,
    )


def write_checkpoint(directory: Path | str, checkpoint: Checkpoint) -> None:
    """Atomically write ``checkpoint.json`` into the runtime directory."""
    path = _checkpoint_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(
        json.dumps(_checkpoint_payload(checkpoint), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def load_checkpoint(directory: Path | str, expected_fingerprint: str) -> Checkpoint | None:
    """Load a checkpoint, rejecting stale plans and rebuilding corrupt state.

    A missing, unreadable, or malformed checkpoint returns ``None``. A valid
    checkpoint with a different plan fingerprint raises ``CheckpointMismatchError``
    so stale calculation state cannot be reused silently.
    """
    path = _checkpoint_path(directory)
    try:
        payload: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    checkpoint = _checkpoint_from_payload(payload)
    if checkpoint is None:
        return None
    if checkpoint.plan_fingerprint != expected_fingerprint:
        raise CheckpointMismatchError(
            checkpoint_path=path,
            expected_fingerprint=expected_fingerprint,
            actual_fingerprint=checkpoint.plan_fingerprint,
        )
    return checkpoint
