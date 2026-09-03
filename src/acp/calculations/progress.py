# pyright: basic, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportUnannotatedClassAttribute=false, reportUnusedParameter=false, reportUnusedVariable=false
"""Workflow progress reporter — writes state.json for scheduler observation."""

from __future__ import annotations

import copy
import html
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

_LIVE_METRIC_KINDS: Final[frozenset[str]] = frozenset(
    {"count", "iteration", "status", "text", "progress"}
)


def _iso_now() -> str:
    """Return current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True, kw_only=True)
class LiveMetric:
    """A normalized metric displayed while a calculation is running."""

    key: str
    label_key: str | None = None
    label: str | None = None
    value: str
    kind: str
    priority: int = 0
    detail: str | None = None


class ProgressReporter:
    """Writes state.json in the schema that JobRunner._observe_state reads.

    Thread-safe, throttled (min_interval between writes), atomic (os.replace).
    """

    def __init__(
        self,
        work_dir: Path,
        *,
        job_name: str = "",
        stages: list[str] | None = None,
        min_interval: float = 2.0,
    ) -> None:
        self._work_dir = Path(work_dir)
        self._job_name = job_name
        self._stage_names: list[str] = list(stages or [])
        self._min_interval = min_interval
        self._last_write: float = 0.0
        self._stages: dict[str, dict] = {}
        self._live_metrics: tuple[LiveMetric, ...] = ()
        self._current_stage: str | None = None
        self._status = "running"
        self._created_at = _iso_now()
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        for name in self._stage_names:
            self._stages[name] = {"status": "pending"}

    def initialize(self) -> None:
        """Write initial state.json with all stages pending."""
        self._write(force=True)

    @property
    def current_stage(self) -> str | None:
        """Return the currently running stage name, if any."""
        with self._lock:
            return self._current_stage

    def start_stage(self, name: str) -> None:
        """Mark a stage as running."""
        now = _iso_now()
        with self._lock:
            if name not in self._stages:
                self._stages[name] = {}
                self._stage_names.append(name)
            self._stages[name]["status"] = "running"
            self._stages[name]["started_at"] = now
            self._current_stage = name
        self._write(force=True)

    def update_stage(
        self,
        name: str,
        *,
        completed: int,
        total: int,
        detail: str | None = None,
    ) -> None:
        """Report sub-stage progress (e.g., scan point 17/40)."""
        with self._lock:
            if name not in self._stages:
                return
            progress = round(completed / max(total, 1), 3)
            self._stages[name]["progress"] = progress
            self._stages[name]["detail"] = detail or f"{completed}/{total}"
        self._write()  # throttled

    def complete_stage(self, name: str, result: dict | None = None) -> None:
        """Mark a stage as completed."""
        now = _iso_now()
        with self._lock:
            if name not in self._stages:
                self._stages[name] = {}
            self._stages[name]["status"] = "completed"
            self._stages[name]["completed_at"] = now
            if result:
                self._stages[name]["result"] = result
            self._current_stage = None
        self._write(force=True)

    def fail_stage(self, name: str, error: str) -> None:
        """Mark a stage as failed."""
        now = _iso_now()
        with self._lock:
            if name not in self._stages:
                self._stages[name] = {}
            self._stages[name]["status"] = "failed"
            self._stages[name]["completed_at"] = now
            self._stages[name]["error"] = error
            self._status = "failed"
        self._write(force=True)

    def complete(self, result: dict | None = None) -> None:
        """Mark the entire job as completed."""
        now = _iso_now()
        with self._lock:
            self._status = "completed"
            for name, info in self._stages.items():
                if info.get("status") not in ("completed", "skipped", "failed"):
                    info["status"] = "completed"
                    info["completed_at"] = now
            self._current_stage = None
        self._write(force=True)

    def fail(self, error: str) -> None:
        """Mark the entire job as failed."""
        with self._lock:
            self._status = "failed"
        self._write(force=True)

    def set_live_metrics(self, metrics: list[LiveMetric]) -> None:
        """Replace live metrics and persist them without throttling."""
        by_key: dict[str, LiveMetric] = {}
        for metric in metrics:
            if metric.kind not in _LIVE_METRIC_KINDS:
                continue
            by_key[metric.key] = LiveMetric(
                key=metric.key,
                label_key=metric.label_key,
                label=metric.label,
                value=html.escape(str(metric.value), quote=True)[:48],
                kind=metric.kind,
                priority=metric.priority,
                detail=metric.detail,
            )
        normalized = tuple(
            sorted(by_key.values(), key=lambda metric: (-metric.priority, metric.key))[:3]
        )
        with self._lock:
            self._live_metrics = normalized
        self._write(force=True)

    def _overall_progress(self) -> float:
        """Compute overall progress including sub-stage fraction."""
        with self._lock:
            return self._overall_progress_unlocked()

    def _overall_progress_unlocked(self) -> float:
        """Compute overall progress while the state lock is held."""
        total = max(len(self._stages), 1)
        done = sum(1 for s in self._stages.values() if s.get("status") in ("completed", "skipped"))
        current_frac = 0.0
        if self._current_stage and self._current_stage in self._stages:
            current_frac = self._stages[self._current_stage].get("progress", 0.0)
        return round((done + current_frac) / total, 3)

    def _stage_index(self) -> int | None:
        """1-based index of current stage."""
        with self._lock:
            return self._stage_index_unlocked()

    def _stage_index_unlocked(self) -> int | None:
        """Return the stage index while the state lock is held."""
        if self._current_stage and self._current_stage in self._stage_names:
            return self._stage_names.index(self._current_stage) + 1
        return None

    def _progress_state(self) -> str:
        """Return 'determinate' or 'indeterminate' for frontend progress bar."""
        with self._lock:
            return self._progress_state_unlocked()

    def _progress_state_unlocked(self) -> str:
        """Return progress state while the state lock is held."""
        if self._status != "running":
            return "determinate"
        current = self._current_stage
        if current and self._stages.get(current, {}).get("progress") is not None:
            return "determinate"
        return "indeterminate"

    def _write(self, *, force: bool = False) -> None:
        """Write state.json atomically, throttled to min_interval."""
        # The state lock only covers the in-memory snapshot. The write lock
        # serializes the shared temporary path without holding the state lock
        # during filesystem I/O.
        with self._write_lock:
            now = time.monotonic()
            with self._lock:
                if not force and (now - self._last_write) < self._min_interval:
                    return
                self._last_write = now
                current_stage = self._current_stage
                stages = copy.deepcopy(self._stages)
                data = {
                    "version": "1.0",
                    "job_name": self._job_name,
                    "status": self._status,
                    "current_stage": current_stage,
                    "overall_progress": self._overall_progress_unlocked(),
                    "stage_index": self._stage_index_unlocked(),
                    "stage_total": len(stages),
                    "stage_progress": (
                        stages[current_stage].get("progress")
                        if current_stage and current_stage in stages
                        else None
                    ),
                    "stage_detail": (
                        stages[current_stage].get("detail")
                        if current_stage and current_stage in stages
                        else None
                    ),
                    "progress_state": self._progress_state_unlocked(),
                    "created_at": self._created_at,
                    "updated_at": _iso_now(),
                    "stages": stages,
                }
                if self._live_metrics:
                    data["live_metrics"] = [
                        {
                            "key": metric.key,
                            "label_key": metric.label_key,
                            "label": metric.label,
                            "value": metric.value,
                            "kind": metric.kind,
                            "priority": metric.priority,
                            "detail": metric.detail,
                        }
                        for metric in self._live_metrics
                    ]

            state_path = self._work_dir / "state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, state_path)
        logger.debug(
            "ProgressReporter wrote %s (stage=%s, overall=%.3f)",
            state_path,
            data["current_stage"],
            data["overall_progress"],
        )


__all__ = ["LiveMetric", "ProgressReporter"]
