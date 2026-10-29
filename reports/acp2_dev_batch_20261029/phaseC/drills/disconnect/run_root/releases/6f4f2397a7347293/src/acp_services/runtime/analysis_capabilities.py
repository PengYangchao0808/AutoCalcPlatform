"""Registry-gated explicit analysis capabilities (B06 shell; A/B12/M2-02 register).

The frozen application API (``runtime/application-api.v1.json``) makes
``POST /api/v1/jobs/{job_id}/analyze`` explicit-only and registry-gated.  This
module is the registry seam: a registered capability with a handler is
``executable``; one without is ``registered`` (declared, still refused); an id
that is not registered is ``unavailable``.  Nothing else is a state, and the
route runs only the executable set.

Production wiring (M2-02)
-------------------------
The **application-level registry is injected by the acp composition root**
(:func:`acp.services_analysis_bootstrap.build_analysis_registry
<acp.services_analysis_bootstrap.build_analysis_registry>`) onto
``app.state.analysis_registry``; the HTTP route and the discovery projection read
that instance.  The module-level :data:`DEFAULT_ANALYSIS_REGISTRY` is retained for
backwards compatibility and unit tests only — it is **never** the production
authority.

* A registers the *semantics* (which analysis id exists, its version and its
  parameter contract);
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
    "ANALYSIS_CAPABILITY_STATES",
    "AnalysisCapability",
    "AnalysisCapabilityRegistry",
    "DEFAULT_ANALYSIS_REGISTRY",
    "capability_projection",
    "register_analysis_capability",
]

#: The frozen science ids from the application contract.
ANALYSIS_CAPABILITY_IDS: tuple[str, ...] = ("vibration.compute", "vibration.analyze")

#: The frozen capability-descriptor states (M1-01b).  There is deliberately no
#: "available"/"ready" state: a descriptor says whether the capability is
#: registered, executable or unavailable, and ``analyze`` runs only the
#: executable set.
ANALYSIS_CAPABILITY_STATES: tuple[str, ...] = ("registered", "executable", "unavailable")


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

    @property
    def state(self) -> str:
        """The frozen descriptor state of this registration."""
        return "executable" if self.handler is not None else "registered"

    def describe(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "version": self.version,
            "owner": self.owner,
            "state": self.state,
            "registered": True,
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


#: Process-local default registry.  B06 shipped it **empty**; since M2-02 the
#: production registry is built by the acp composition root
#: (``acp.services_analysis_bootstrap``) and injected onto ``app.state``.  This
#: object exists for back-compat and unit tests only — no route may treat it as
#: the application authority.
DEFAULT_ANALYSIS_REGISTRY = AnalysisCapabilityRegistry()


def register_analysis_capability(capability: AnalysisCapability) -> AnalysisCapability:
    """Register a capability on the process-local default registry.

    Test/compat helper only: production code must receive the injected registry
    from the composition root instead of mutating this global.
    """
    return DEFAULT_ANALYSIS_REGISTRY.register(capability)


def capability_projection(
    registry: AnalysisCapabilityRegistry | None = None,
) -> tuple[dict[str, Any], ...]:
    """Project the frozen ``registered/executable/unavailable`` descriptor states.

    One entry per frozen science id, never a "ready/available" state: an id that
    is not registered is ``unavailable`` (so ``analyze`` refuses it with
    ``unsupported_capability``), a registered id without a handler is
    ``registered`` (declared, still refused), and only a handler makes it
    ``executable``.  The projection is read-only and starts nothing.

    A registry may hold capabilities beyond the frozen built-in ids (M2-05: an
    enabled third-party pure-analysis capability registered by the composition
    root).  Those entries are projected with exactly the same six keys and the
    same three states — the extra ids never change what a built-in id means.
    """
    table = registry if registry is not None else DEFAULT_ANALYSIS_REGISTRY
    entries: list[dict[str, Any]] = []
    for analysis_id in ANALYSIS_CAPABILITY_IDS:
        capability = table.get(analysis_id)
        if capability is None:
            entries.append(_unavailable_entry(analysis_id))
            continue
        entries.append(_registered_entry(capability))
    for analysis_id in table.ids():
        if analysis_id in ANALYSIS_CAPABILITY_IDS:
            continue
        capability = table.get(analysis_id)
        if capability is not None:
            entries.append(_registered_entry(capability))
    return tuple(entries)


def _unavailable_entry(analysis_id: str) -> dict[str, Any]:
    return {
        "analysis_id": analysis_id,
        "state": "unavailable",
        "registered": False,
        "executable": False,
        "version": None,
        "owner": None,
    }


def _registered_entry(capability: AnalysisCapability) -> dict[str, Any]:
    return {
        "analysis_id": capability.analysis_id,
        "state": capability.state,
        "registered": True,
        "executable": capability.handler is not None,
        "version": capability.version,
        "owner": capability.owner,
    }

