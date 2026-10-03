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
