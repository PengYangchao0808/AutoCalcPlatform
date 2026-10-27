"""One application-level science discovery provider (M2-02).

``GET /api/capabilities`` (the MCP discovery command) and
``GET /api/v2/science/capabilities`` used to answer from two different places:
the v2 read surface returned A's full capability catalog (and cached it on
``app.state``), while the application discovery command hard-coded
``capability_descriptors: None``.  A client could therefore never discover the
science capabilities through the interface it actually speaks.

This module is the single provider both surfaces call:

* the built-in and domain catalog documents are built **once per application**
  and cached on ``app.state`` (``builtin_catalog()``/``domain_catalog()`` probe
  the executable seam lazily, so a second build is a second probe);
* the analysis-capability projection
  (:func:`acp_services.runtime.analysis_capabilities.capability_projection`) is
  computed from the **injected** application registry
  (``app.state.analysis_registry``), never from the module-level default;
* everything here is read-only: no task, no prepare, no transfer, no
  reservation and no Controller/subprocess start.

The module keeps no state of its own and never imports the scheduler.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from acp_services.runtime.analysis_capabilities import (
    AnalysisCapabilityRegistry,
    capability_projection,
)

__all__ = [
    "ScienceCapabilityCatalog",
    "analysis_capabilities",
    "analysis_registry",
    "capability_descriptors",
    "science_catalog",
]

#: App-state attribute holding the cached catalog document pair.
_CATALOG_ATTR = "science_capability_catalog"
#: App-state attribute holding the injected application analysis registry.
_REGISTRY_ATTR = "analysis_registry"
#: Serialises the first (and only) catalog build across concurrent requests.
_CATALOG_LOCK = threading.Lock()


@dataclass(frozen=True)
class ScienceCapabilityCatalog:
    """The two A-owned catalog documents for one application process.

    Attributes:
        descriptors: ``cccp.calculation.capabilities.builtin_catalog().to_dict()``
            — the four seeded capabilities (``electronic.energy``,
            ``geometry.optimize``, ``vibration.analyze``, ``vibration.compute``).
        domain: ``domain_catalog().to_dict()`` — the domain (NMR/thermochemistry)
            catalog.
    """

    descriptors: Mapping[str, Any]
    domain: Mapping[str, Any]

    def descriptor_entries(self) -> tuple[dict[str, Any], ...]:
        """Return the A-owned descriptor objects forwarded by discovery."""
        entries = self.descriptors.get("capabilities")
        if not isinstance(entries, (list, tuple)):
            return ()
        return tuple(dict(entry) for entry in entries if isinstance(entry, Mapping))


def science_catalog(state: Any) -> ScienceCapabilityCatalog:
    """Return the per-application catalog, probing the executable seam once.

    The cache lives on ``app.state`` so every router of one application shares
    the same documents.  The build is guarded by a process lock, so two
    concurrent first requests cannot each run ``builtin_catalog()``.

    Back-compat: the v2 router used to cache the two documents under
    ``science_capability_descriptors``/``science_capability_domain`` (and tests
    inject them to avoid executable probing).  Those attributes are honoured
    when present, so there is exactly one provider and no second probe.
    """
    cached = getattr(state, _CATALOG_ATTR, None)
    if isinstance(cached, ScienceCapabilityCatalog):
        return cached
    legacy_descriptors = getattr(state, "science_capability_descriptors", None)
    legacy_domain = getattr(state, "science_capability_domain", None)
    if isinstance(legacy_descriptors, Mapping) and isinstance(legacy_domain, Mapping):
        catalog = ScienceCapabilityCatalog(
            descriptors=legacy_descriptors,
            domain=legacy_domain,
        )
        state.science_capability_catalog = catalog
        return catalog
    with _CATALOG_LOCK:
        cached = getattr(state, _CATALOG_ATTR, None)
        if isinstance(cached, ScienceCapabilityCatalog):
            return cached
        # Lazy import: a monkeypatched ``builtin_catalog`` (test/probe seam) must
        # be honoured, and importing the catalog must not pull in task modules.
        from cccp.calculation.capabilities import builtin_catalog, domain_catalog

        catalog = ScienceCapabilityCatalog(
            descriptors=builtin_catalog().to_dict(),
            domain=domain_catalog().to_dict(),
        )
        state.science_capability_catalog = catalog
        # Back-compat: the v2 router cached the two documents under separate
        # attributes before this provider existed.
        state.science_capability_descriptors = catalog.descriptors
        state.science_capability_domain = catalog.domain
        return catalog


def analysis_registry(state: Any) -> AnalysisCapabilityRegistry | None:
    """Return the injected application analysis registry (``None`` when absent).

    The registry is built by the acp composition root and stored on
    ``app.state`` at lifespan startup.  ``None`` means "this process never wired
    analysis", which is a typed refusal at the analyze command and an
    all-``unavailable`` projection for discovery — never a fallback to the
    module-level default registry.
    """
    registry = getattr(state, _REGISTRY_ATTR, None)
    return registry if isinstance(registry, AnalysisCapabilityRegistry) else None


def analysis_capabilities(state: Any) -> tuple[dict[str, Any], ...]:
    """Project the frozen ``registered/executable/unavailable`` states.

    One entry per frozen science id.  A registered capability without a handler
    stays ``registered`` (declared, still refused); only a handler makes it
    ``executable``.  The projection starts nothing and reads no artifact.
    """
    registry = analysis_registry(state)
    if registry is None:
        return capability_projection(AnalysisCapabilityRegistry())
    return capability_projection(registry)


def capability_descriptors(state: Any) -> tuple[dict[str, Any], ...]:
    """Return the A-owned capability descriptors forwarded verbatim by discovery.

    These are the catalog objects (never a platform-state annotation): a
    descriptor says what the capability *is*, while
    :func:`analysis_capabilities` says what this process can *execute*.

    M2-02 forwards the builtin catalog (the four seeded capabilities, including
    ``vibration.analyze``/``vibration.compute``).  Merging the domain catalog and
    authorised third-party plugins into this one provider is M2-05; the v2 read
    surface already returns both documents unchanged.
    """
    return science_catalog(state).descriptor_entries()
