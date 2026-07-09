"""
Core State
==========

Generic workflow state persistence and append-only event logging.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)

JsonObject = dict[str, object]
StageState = dict[str, object]


def _utc_now() -> str:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class EventLog:
    """Append-only event log stored as JSON Lines."""

    path: Path

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: Mapping[str, object]) -> None:
        """Append a single event record."""
        record = dict(event)
        record.setdefault("timestamp", _utc_now())

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            _ = handle.write(json.dumps(record, default=str) + "\n")


class WorkflowState:
    """Generic stage-based workflow state management."""

    STATE_FILE: str = "state.json"
    work_dir: Path
    job_name: str
    state_file: Path
    state: JsonObject

    def __init__(self, work_dir: Path | str = Path("/tmp"), job_name: str = "workflow"):
        self.work_dir = Path(work_dir)
        self.job_name = job_name
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.work_dir / self.STATE_FILE
        self.state = {}

    @property
    def completed_stages(self) -> list[str]:
        """Return completed stage names in insertion order."""
        return [
            name
            for name, stage in self._stages().items()
            if stage.get("status") == "completed"
        ]

    @property
    def failed_stages(self) -> dict[str, str]:
        """Return failed stages mapped to their recorded error message."""
        return {
            name: str(stage.get("error", ""))
            for name, stage in self._stages().items()
            if stage.get("status") == "failed"
        }

    def load(self) -> JsonObject | None:
        """Load state from disk if present."""
        if not self.state_file.exists():
            return None

        with self.state_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("Workflow state file must contain a JSON object")
        self.state = cast(JsonObject, payload)
        return self.state

    def _stages(self) -> dict[str, StageState]:
        """Return the mutable stages mapping."""
        stages = self.state.get("stages")
        if isinstance(stages, dict):
            return cast(dict[str, StageState], stages)

        stages = {}
        self.state["stages"] = stages
        return cast(dict[str, StageState], stages)

    def _stage(self, name: str) -> StageState:
        """Return the state entry for a named stage."""
        stages = self._stages()
        stage = stages.get(name)
        if isinstance(stage, dict):
            return cast(StageState, stage)

        stage = {}
        stages[name] = stage
        return cast(StageState, stage)

    def save(self) -> None:
        """Persist the current state atomically."""
        self.work_dir.mkdir(parents=True, exist_ok=True)

        tmp = tempfile.NamedTemporaryFile(
            dir=str(self.work_dir),
            suffix=".tmp",
            delete=False,
            mode="w",
            encoding="utf-8",
        )
        try:
            json.dump(self.state, tmp, indent=2)
            tmp.close()
            os.replace(tmp.name, str(self.state_file))
        except Exception as exc:
            logger.warning(f"Failed to save state: {exc}")
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def initialize(self, input_source: str = "") -> None:
        """Initialize a fresh workflow state file."""
        self.state = {
            "version": "1.0",
            "job_name": self.job_name,
            "status": "running",
            "input_source": input_source,
            "stages": {},
            "current_stage": None,
            "created_at": _utc_now(),
            "completed_at": None,
        }
        self.save()

    def set_stage(self, name: str) -> None:
        """Mark a stage as running."""
        stage = self._stage(name)
        now = _utc_now()
        stage.setdefault("started_at", now)
        stage.update({"status": "running", "updated_at": now})
        self.state["current_stage"] = name
        self.save()

    def complete_stage(self, name: str, result: Mapping[str, object] | None = None) -> None:
        """Mark a stage as completed and optionally store result data."""
        stage = self._stage(name)
        now = _utc_now()
        stage.update({"status": "completed", "completed_at": now, "updated_at": now})
        if result is not None:
            stage["result"] = dict(result)
        self.state["current_stage"] = name
        self.save()

    def fail_stage(self, name: str, error: str = "") -> None:
        """Mark a stage as failed."""
        stage = self._stage(name)
        now = _utc_now()
        stage.update(
            {
                "status": "failed",
                "error": error,
                "completed_at": now,
                "updated_at": now,
            }
        )
        self.state["current_stage"] = name
        self.save()

    def is_stage_completed(self, name: str) -> bool:
        """Return whether a stage has completed successfully."""
        stage = self._stages().get(name, {})
        return stage.get("status") == "completed"

    def get_stage_result(self, name: str) -> JsonObject | None:
        """Return stored stage results, if any."""
        result = self._stages().get(name, {}).get("result")
        if isinstance(result, dict):
            return cast(JsonObject, result)
        return None

    def mark_completed(self) -> None:
        """Mark the workflow as completed."""
        self.state["status"] = "completed"
        self.state["completed_at"] = _utc_now()
        self.save()

    def get_summary(self) -> JsonObject:
        """Return a compact workflow status summary."""
        return {
            "job_name": self.job_name,
            "status": self.state.get("status"),
            "current_stage": self.state.get("current_stage"),
            "completed": self.state.get("completed_at") is not None,
            "created_at": self.state.get("created_at"),
            "completed_at": self.state.get("completed_at"),
            "stages": list(self._stages().keys()),
        }

    def clear(self) -> None:
        """Remove on-disk state and reset the in-memory state."""
        if self.state_file.exists():
            self.state_file.unlink()
        self.state = {}


__all__ = ["EventLog", "WorkflowState"]
