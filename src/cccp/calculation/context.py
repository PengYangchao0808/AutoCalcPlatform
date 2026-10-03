"""Runtime task context: resolved config, work directory, progress sink.

``TaskRequest`` is the serializable intent; :class:`TaskContext` is the
runtime companion passed to ``run_*(request, *, context=None)``.  The
runtime rules implemented/documented here are authoritative in
``docs/ACP_CCCP_Task_API_DevDoc.md`` §TaskContext:

* R1  ``context=None`` acquisition (config deferred; workdir/output root).
* R2  relative input basis (``input_base``).
* R6  progress-callback thread safety and failure isolation.
* R8  cooperative cancellation checks between pending units.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from cccp.calculation.contracts import JsonValue
from cccp.calculation.progress import ProgressEvent, TaskProgressSink
from cccp.calculation.requests import TaskRequest

logger = logging.getLogger(__name__)


@dataclass
class TaskContext:
    """Runtime companion of a :class:`~cccp.calculation.requests.TaskRequest`.

    Never serialised.  ``config`` is the already-resolved cccp config
    mapping (``None`` = the execution layer loads it at call time);
    ``workdir`` is the task work directory; ``input_base`` is the base for
    relative input paths (R2, defaults to the process CWD at execution
    start); ``timeout_s`` bounds the WHOLE task including rescue attempts
    (R7); ``cancelled`` is polled between pending units (R8).
    """

    config: Mapping[str, JsonValue] | None = None
    workdir: Path | None = None
    input_base: Path | None = None
    progress: TaskProgressSink | None = None
    timeout_s: float | None = None
    cancelled: Callable[[], bool] | None = None
    _progress_errors: list[str] = field(default_factory=list, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def emit_progress(self, event: ProgressEvent) -> bool:
        """Emit one progress event with failure isolation (R6).

        Progress-callback failures are recorded (see
        :meth:`progress_errors`) and logged; they are NEVER converted into
        scientific failures and never propagate out of task code.  Internal
        programming errors elsewhere keep propagating as diagnosable
        exceptions.

        Returns:
            True when the event was delivered (or no sink is attached);
            False when the sink raised.
        """
        sink = self.progress
        if sink is None:
            return True
        with self._lock:
            try:
                sink.emit(event)
            except Exception as exc:  # noqa: BLE001 — callback isolation (R6)
                label = f"{event.kind.value}:{event.stage or '-'}"
                self._progress_errors.append(f"{label}: {type(exc).__name__}: {exc}")
                logger.warning("progress sink failure (%s): %s", label, exc)
                return False
        return True

    def progress_errors(self) -> tuple[str, ...]:
        """Return recorded progress-callback failures (stable strings)."""
        with self._lock:
            return tuple(self._progress_errors)

    def is_cancelled(self) -> bool:
        """Return True when the caller requested cancellation (R8)."""
        if self.cancelled is None:
            return False
        return bool(self.cancelled())

    def input_root(self) -> Path:
        """Return the base directory for relative input paths (R2)."""
        return Path(self.input_base) if self.input_base is not None else Path.cwd()


def resolve_context(request: TaskRequest, context: TaskContext | None = None) -> TaskContext:
    """Return the effective context for one task call (rule R1).

    ``context=None`` acquires defaults: config is deferred to the
    execution layer (``config=None``), the work directory is
    ``request.output_dir`` when set (the artifact root), and the relative
    input basis is the process CWD.
    """
    if context is not None:
        return context
    workdir = request.output_dir if request.output_dir is not None else Path.cwd()
    return TaskContext(workdir=workdir, input_base=Path.cwd())


__all__ = [
    "TaskContext",
    "resolve_context",
]
