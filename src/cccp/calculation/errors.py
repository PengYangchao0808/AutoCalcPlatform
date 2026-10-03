"""Neutral, task-agnostic error types for the calculation layer.

Deliberately dependency-free: importing this module must never pull in
task-execution modules, QC interfaces, or backend registration (it is the
one shared seam every station may import, including ``acp.backends``).
Todo 13 extends this surface without redefining the types below.

Each error keeps the historical exception category as a base so existing
``except ValueError`` / ``except LookupError`` / ``except RuntimeError``
call sites keep working while callers migrate to the typed errors.
"""

from __future__ import annotations

__all__ = [
    "BackendUnavailableError",
    "CalculationError",
    "ProgressCallbackError",
    "TaskCancelledError",
    "TaskInputError",
    "UnsupportedCapabilityError",
]


class CalculationError(Exception):
    """Base class for neutral calculation-layer errors."""


class TaskInputError(CalculationError, ValueError):
    """Invalid request input, rejected before any computation runs."""


class UnsupportedCapabilityError(CalculationError, LookupError):
    """Requested capability is unknown or has no implemented backend.

    Raised as a pre-launch structured rejection so stubs or unimplemented
    capabilities can never be selected or dispatched.
    """


class BackendUnavailableError(CalculationError, RuntimeError):
    """A declared-implemented capability lacks its runtime binary.

    Declaration no longer encodes binary presence; per-capability runtime
    probes (e.g. ``is_isostat_available`` / ``is_shermo_available``) judge
    absence and surface it through this error.
    """


class TaskCancelledError(CalculationError):
    """Cooperative cancellation of a pending unit (batch entry, rescue step).

    Task-level results surface cancellation as ``status="failed"`` with
    ``error_kind="cancelled"``; this exception is for executor/batch code
    that must abort a pending entry before it starts (R8).
    """


class ProgressCallbackError(CalculationError):
    """A progress-callback failure, distinguished from scientific failure.

    Progress-callback failures never turn a scientific success into a
    failure: ``TaskContext.emit_progress`` isolates and records them (R6).
    This type is the stable classification for callers that want to
    re-raise or report them explicitly.
    """
