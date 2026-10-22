"""Registry-gated explicit analysis capabilities (B06 shell; A/B12 register).

The frozen application API (``runtime/application-api.v1.json``) makes
``POST /api/v1/jobs/{job_id}/analyze`` explicit-only and registry-gated.  This
module is the registry seam: until a science owner registers an analysis
handler, the route returns ``unsupported_capability`` and runs nothing.

Registration is deliberately explicit and process-local in P0:

* A registers the *semantics* (which analysis id exists, its version and its
  parameter contract) through B18/B12's integration work;
* B12 owns the real artifact/read lifecycle that backs a handler.

The module is stdlib-only and imports no :mod:`acp` code.  It is not a task
state machine and it never starts a Controller, a worker or a task.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ANALYSIS_CAPABILITY_IDS",
    "AnalysisCapability",
    "AnalysisCapabilityRegistry",
    "DEFAULT_ANALYSIS_REGISTRY",
    "register_analysis_capability",
]

#: The frozen science ids from the application contract.
ANALYSIS_CAPABILITY_IDS: tuple[str, ...] = ("vibration.compute", "vibration.analyze")

#: A handler receives the normalised request and returns a JSON-safe mapping.
AnalysisHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class AnalysisCapability:
    """One registered analysis capability.

    Attributes:
        analysis_id: Frozen science id (``vibration.analyze``).
        version: Capability contract version.
        owner: Registering science/platform owner (``A``/``B12``).
        handler: Optional execution handler.  When absent the capability is
            *declared* but not executable; the route still returns
            ``unsupported_capability`` rather than fabricating a result.
        metadata: Opaque registration metadata (never interpreted here).
    """

    analysis_id: str
    version: str
    owner: str
    handler: AnalysisHandler | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "version": self.version,
            "owner": self.owner,
            "executable": self.handler is not None,
            "metadata": dict(self.metadata),
        }


class AnalysisCapabilityRegistry:
    """Thread-safe explicit capability registry (empty by construction)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._capabilities: dict[str, AnalysisCapability] = {}

    def register(self, capability: AnalysisCapability) -> AnalysisCapability:
        if not capability.analysis_id:
            raise ValueError("analysis_id must be non-empty")
        with self._lock:
            self._capabilities[capability.analysis_id] = capability
            return capability

    def unregister(self, analysis_id: str) -> None:
        with self._lock:
            self._capabilities.pop(str(analysis_id), None)

    def get(self, analysis_id: str) -> AnalysisCapability | None:
        with self._lock:
            return self._capabilities.get(str(analysis_id))

    def ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._capabilities))

    def describe(self) -> tuple[Mapping[str, Any], ...]:
        with self._lock:
            return tuple(self._capabilities[key].describe() for key in sorted(self._capabilities))

    def clear(self) -> None:
        with self._lock:
            self._capabilities.clear()


#: Process-local default registry.  B06 ships it **empty**; A/B12 register.
DEFAULT_ANALYSIS_REGISTRY = AnalysisCapabilityRegistry()


def register_analysis_capability(capability: AnalysisCapability) -> AnalysisCapability:
    """Register a capability on the process-local default registry."""
    return DEFAULT_ANALYSIS_REGISTRY.register(capability)
