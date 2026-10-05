"""Atomic persistence for the calculation task checkpoint.

Identity schema contract (D06, plan todo 10):

* ``Checkpoint.identity_schema`` defaults to ``1``; legacy files without
  the field read as ``1``.
* ``load_checkpoint(directory, expected_fingerprint, *, allow_legacy_fingerprint=False)``
  is a single, deterministic behaviour — it NEVER raises
  ``CheckpointMismatchError``:

  - schema ``2`` → v2 fingerprint compare; mismatch → ``None`` + logger
    event ``identity_fingerprint_mismatch`` (``execute`` then treats it as
    "no whole-plan reuse" and continues per-step adoption).
  - schema ``1`` + ``allow_legacy_fingerprint=False`` (executor path) →
    ``None`` + logger event ``identity_unverifiable_legacy`` (conservative
    recompute; legacy fingerprints alone never authorize reuse).
  - schema ``1`` + ``allow_legacy_fingerprint=True`` (batch path ONLY) →
    compare the stored ``plan_fingerprint``: match → checkpoint returned
    (cache-hit / resume preserved), mismatch → ``None`` + event.
  - unknown schema → ``None`` + event ``identity_unknown_schema``.

``identity_unverifiable_legacy`` is a logger event — there is no event_log
sink for it.  ``CheckpointMismatchError`` is retained for API compatibility
only; no code path raises it anymore.  Writers: the executor and
``acp.workflows.irc`` write ``identity_schema=2``; the batch engine keeps
``identity_schema=1``; ``tsmode`` uses its own ``tsmode_checkpoint.json``
(outside this contract).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .contracts import Checkpoint, JsonValue

__all__ = ["Checkpoint", "CheckpointMismatchError", "load_checkpoint", "write_checkpoint"]

logger = logging.getLogger(__name__)

CHECKPOINT_FILENAME: Final = "checkpoint.json"
_LEGACY_IDENTITY_SCHEMA: Final = 1


@dataclass(frozen=True, slots=True)
class CheckpointMismatchError(Exception):
    """Retained for API compatibility — no load path raises it anymore.

    A fingerprint mismatch now yields ``None`` plus a logger event so stale
    state can never crash a resume; callers recompute conservatively.
    """

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
        "identity_schema": checkpoint.identity_schema,
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
    # Missing identity_schema reads as 1 (legacy checkpoint files).
    raw_schema = payload.get("identity_schema", _LEGACY_IDENTITY_SCHEMA)

    if not isinstance(task_id, str) or not isinstance(workflow, str):
        return None
    if not isinstance(plan_fingerprint, str):
        return None
    if not isinstance(step_states, list) or not isinstance(items_state, dict):
        return None
    if not isinstance(attempts, int) or isinstance(attempts, bool):
        return None
    if not isinstance(raw_schema, int) or isinstance(raw_schema, bool):
        return None

    return Checkpoint(
        task_id=task_id,
        workflow=workflow,
        plan_fingerprint=plan_fingerprint,
        step_states=step_states,
        items_state=items_state,
        attempts=attempts,
        identity_schema=raw_schema,
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


def load_checkpoint(
    directory: Path | str,
    expected_fingerprint: str,
    *,
    allow_legacy_fingerprint: bool = False,
) -> Checkpoint | None:
    """Load a checkpoint under the D06 identity contract.

    A missing, unreadable, malformed, legacy-unverifiable, unknown-schema or
    fingerprint-mismatched checkpoint returns ``None`` (conservative
    recompute) together with a logger event — this function never raises
    ``CheckpointMismatchError``.

    Args:
        directory: Runtime directory holding ``checkpoint.json``.
        expected_fingerprint: The caller's expected plan fingerprint
            (v2 identity for schema=2 writers).
        allow_legacy_fingerprint: Batch-path compatibility switch only;
            permits schema=1 checkpoints whose stored ``plan_fingerprint``
            equals *expected_fingerprint* to be returned.

    Returns:
        The checkpoint when verifiable against *expected_fingerprint*,
        otherwise ``None``.
    """
    path = _checkpoint_path(directory)
    try:
        payload: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    checkpoint = _checkpoint_from_payload(payload)
    if checkpoint is None:
        return None

    schema = checkpoint.identity_schema
    if schema == 2:
        if checkpoint.plan_fingerprint == expected_fingerprint:
            return checkpoint
        logger.info(
            "identity_fingerprint_mismatch: checkpoint %s carries %r, expected %r — "
            "no whole-plan reuse (conservative recompute)",
            path,
            checkpoint.plan_fingerprint,
            expected_fingerprint,
        )
        return None

    if schema == _LEGACY_IDENTITY_SCHEMA:
        if not allow_legacy_fingerprint:
            logger.info(
                "identity_unverifiable_legacy: checkpoint %s has no v2 identity binding — "
                "conservative recompute",
                path,
            )
            return None
        if checkpoint.plan_fingerprint == expected_fingerprint:
            return checkpoint
        logger.info(
            "identity_unverifiable_legacy: stored fingerprint %r does not match %r — "
            "legacy checkpoint cannot be verified (conservative recompute)",
            checkpoint.plan_fingerprint,
            expected_fingerprint,
        )
        return None

    logger.info(
        "identity_unknown_schema: checkpoint %s uses identity_schema=%s — conservative recompute",
        path,
        schema,
    )
    return None
