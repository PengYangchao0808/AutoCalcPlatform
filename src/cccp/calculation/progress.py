"""Scientific progress events for the cccp calculation layer.

The progress interface carries SCIENTIFIC events only (stage lifecycle +
scientific metrics with stable names/units).  UI presentation fields
(``label_key``/``priority``/display ordering) are ACP concerns: ACP
converts these events into LiveMetric display metrics and CCCP never
carries them.  See ``docs/ACP_CCCP_Task_API_DevDoc.md`` §Progress.

Author: QCcalc Team
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class ProgressEventKind(str, Enum):
    """Kind of one scientific progress event."""

    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    METRIC = "metric"
    MESSAGE = "message"


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One scientific progress event.

    ``metric`` uses stable scientific names (``energy_hartree``, ``cycle``,
    ``s2``, ``scan_point``, ...) with ``unit``; ``index`` optionally carries
    the original scientific record number (frame/point/attempt) and follows
    the record-identity rules (never renumbered).
    """

    kind: ProgressEventKind
    stage: str = ""
    metric: str | None = None
    value: float | None = None
    unit: str | None = None
    message: str | None = None
    index: int | None = None


@runtime_checkable
class TaskProgressSink(Protocol):
    """Structural protocol for progress consumers (ACP ProgressReporter-like).

    Implementations may be called from multiple threads; task code
    serialises calls through :class:`cccp.calculation.context.TaskContext`.
    """

    def emit(self, event: ProgressEvent) -> None:
        """Consume one scientific progress event."""


__all__ = [
    "ProgressEvent",
    "ProgressEventKind",
    "TaskProgressSink",
]
