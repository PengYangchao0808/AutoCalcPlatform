"""One application-level science discovery provider (M2-02, extended by M2-05).

``GET /api/capabilities`` (the MCP discovery command) and
``GET /api/v2/science/capabilities`` used to answer from two different places:
the v2 read surface returned A's full capability catalog (and cached it on
``app.state``), while the application discovery command hard-coded
``capability_descriptors: None``.  A client could therefore never discover the
science capabilities through the interface it actually speaks.

This module is the single provider both surfaces call.  It merges the three
capability sources the platform actually has, and adds none:

* **built-in** — ``cccp.calculation.capabilities.builtin_catalog()``;
* **domain** — ``domain_catalog()`` (NMR/thermochemistry);
* **authorised third-party plugins** — the enabled, byte-verified
  pure-analysis capabilities of the installed-plugin enablement ledger
  (:class:`acp_services.runtime.science_plugins.SciencePluginRegistry`).

Both catalog documents are built **once per application** and cached on
``app.state``.  The cache is keyed by an *enablement signature*
(``generation`` + ledger file signature) that the enable/disable path bumps, so
a plugin enabled by another process is picked up by the next discovery request
without probing the executable seam again.

The analysis-capability projection
(:func:`acp_services.runtime.analysis_capabilities.capability_projection`) is
computed from the **injected** application registry
(``app.state.analysis_registry``), never from the module-level default.  A
capability that the ledger reports as enabled but that this process did not
freeze into its registry is projected as ``registered`` — declared, and
honestly not executable here — never as executable and never as absent.

Everything here is read-only: no task, no prepare, no transfer, no reservation,
no Controller/subprocess start, and no plugin import (the ledger is metadata
only; a plugin module is imported only when its capability is invoked).

The module keeps no state of its own and never imports the scheduler.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from acp_services.runtime.analysis_capabilities import (
    AnalysisCapabilityRegistry,
    capability_projection,
)
from acp_services.runtime.science_plugins import (
    PluginEnablement,
    SciencePluginRegistry,
)

__all__ = [
    "ScienceCapabilityCatalog",
    "analysis_capabilities",
    "analysis_registry",
    "capability_descriptors",
    "plugin_capabilities",
    "plugin_registry",
    "science_catalog",
]

#: App-state attribute holding the cached catalog document pair.
_CATALOG_ATTR = "science_capability_catalog"
#: App-state attribute holding the injected application analysis registry.
_REGISTRY_ATTR = "analysis_registry"
#: App-state attribute holding the installed-plugin enablement ledger.
_PLUGIN_REGISTRY_ATTR = "science_plugin_registry"
#: Serialises the catalog build across concurrent requests.
_CATALOG_LOCK = threading.Lock()

#: Signature of an application without any plugin ledger (never scans).
_NO_PLUGIN_SIGNATURE = (0, "absent")


@dataclass(frozen=True)
class ScienceCapabilityCatalog:
    """The merged capability documents for one application process.

    Attributes:
        descriptors: The A-owned descriptor document with the enabled
            third-party plugin capability descriptors appended to its
            ``capabilities`` list (built-in entries are unchanged).
        domain: ``domain_catalog().to_dict()`` — the domain (NMR/thermochemistry)
            catalog.
        plugins: The appended third-party descriptor entries (the same objects
            that appear in ``descriptors["capabilities"]``).
        signature: The enablement signature the catalog was built from.
        generation: The ledger generation at build time.
        etag: ``sha256:`` over the signature and the plugin descriptor digests.
    """

    descriptors: Mapping[str, Any]
    domain: Mapping[str, Any]
    plugins: tuple[dict[str, Any], ...] = ()
    signature: tuple[int, str] = _NO_PLUGIN_SIGNATURE
    generation: int = 0
    etag: str = ""

    def descriptor_entries(self) -> tuple[dict[str, Any], ...]:
        """Return the descriptor objects forwarded by discovery (merged)."""
        entries = self.descriptors.get("capabilities")
        if not isinstance(entries, (list, tuple)):
            return ()
        return tuple(dict(entry) for entry in entries if isinstance(entry, Mapping))


def plugin_registry(state: Any) -> SciencePluginRegistry | None:
    """Return the installed-plugin enablement ledger (``None`` when absent)."""
    registry = getattr(state, _PLUGIN_REGISTRY_ATTR, None)
    return registry if isinstance(registry, SciencePluginRegistry) else None


def plugin_capabilities(state: Any) -> tuple[PluginEnablement, ...]:
    """Return the enabled, byte-verified third-party capabilities."""
    registry = plugin_registry(state)
    if registry is None:
        return ()
    return registry.authorized_capabilities()


def _enablement_signature(registry: SciencePluginRegistry | None) -> tuple[int, str]:
    if registry is None:
        return _NO_PLUGIN_SIGNATURE
    return (registry.generation(), registry.ledger_signature())


def _merge_plugin_entries(
    descriptors: Mapping[str, Any],
    plugins: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    """Return the descriptor document with plugin entries appended.

    The built-in entries are forwarded unchanged (a copy of the list, the same
    entry objects): the platform never rewrites A's catalog objects, it only
    adds platform-owned plugin descriptors after them.
    """
    merged = dict(descriptors)
    entries = merged.get("capabilities")
    builtin = list(entries) if isinstance(entries, (list, tuple)) else []
    merged["capabilities"] = [*builtin, *plugins]
    return merged


def _plugin_descriptor_entries(
    capabilities: tuple[PluginEnablement, ...],
) -> tuple[dict[str, Any], ...]:
    """Project each enabled capability descriptor with its frozen provenance."""
    entries: list[dict[str, Any]] = []
    for record in capabilities:
        entry = dict(record.descriptor)
        entry["capability_source"] = "science_plugin"
        entry["enablement"] = {
            "enabled": record.enabled,
            "enabled_by": record.enabled_by,
            "enabled_at": record.enabled_at,
        }
        entry["descriptor_digest"] = record.descriptor_digest
        entry["distribution_digest"] = dict(record.distribution_digest)
        entries.append(entry)
    return tuple(entries)


def _etag(signature: tuple[int, str], plugins: tuple[dict[str, Any], ...]) -> str:
    hasher = hashlib.sha256()
    hasher.update(f"{signature[0]}\x00{signature[1]}\x00".encode())
    for entry in plugins:
        hasher.update(str(entry.get("descriptor_digest", "")).encode())
        hasher.update(b"\x00")
    return "sha256:" + hasher.hexdigest()


def _injected_legacy(cached: Any, legacy_descriptors: Any) -> bool:
    """Return whether the legacy attributes were injected from outside.

    This provider writes the merged documents back onto the same two
    attributes for the v2 router's back-compat path.  Those write-backs are
    *this* provider's own build result, never an injected fixture, so they must
    not be mistaken for one on the next cache invalidation: an enablement change
    would otherwise return the previous build's merged descriptors with the
    plugin entries dropped.
    """
    if isinstance(cached, ScienceCapabilityCatalog):
        return legacy_descriptors is not cached.descriptors
    return True


def science_catalog(state: Any) -> ScienceCapabilityCatalog:
    """Return the per-application catalog, probing the executable seam once.

    The cache lives on ``app.state`` so every router of one application shares
    the same documents.  The build is guarded by a process lock, so two
    concurrent first requests cannot each run ``builtin_catalog()``.  A change
    of the enablement signature (an enable/disable in this or another process)
    invalidates the cache; nothing else does.

    Back-compat: the v2 router used to cache the two documents under
    ``science_capability_descriptors``/``science_capability_domain`` (and tests
    inject them to avoid executable probing).  Those attributes are honoured
    when present, so there is exactly one provider and no second probe; an
    injected legacy document deliberately carries no plugin entries (a test
    fixture is not an enablement fact).
    """
    registry = plugin_registry(state)
    signature = _enablement_signature(registry)
    cached = getattr(state, _CATALOG_ATTR, None)
    if isinstance(cached, ScienceCapabilityCatalog) and cached.signature == signature:
        return cached
    legacy_descriptors = getattr(state, "science_capability_descriptors", None)
    legacy_domain = getattr(state, "science_capability_domain", None)
    if (
        _injected_legacy(cached, legacy_descriptors)
        and isinstance(legacy_descriptors, Mapping)
        and isinstance(legacy_domain, Mapping)
    ):
        catalog = ScienceCapabilityCatalog(
            descriptors=legacy_descriptors,
            domain=legacy_domain,
            plugins=(),
            signature=signature,
            generation=signature[0],
            etag=_etag(signature, ()),
        )
        state.science_capability_catalog = catalog
        return catalog
    with _CATALOG_LOCK:
        registry = plugin_registry(state)
        signature = _enablement_signature(registry)
        cached = getattr(state, _CATALOG_ATTR, None)
        if isinstance(cached, ScienceCapabilityCatalog) and cached.signature == signature:
            return cached
        # Lazy import: a monkeypatched ``builtin_catalog`` (test/probe seam) must
        # be honoured, and importing the catalog must not pull in task modules.
        from cccp.calculation.capabilities import builtin_catalog, domain_catalog

        plugins = _plugin_descriptor_entries(
            registry.authorized_capabilities() if registry is not None else ()
        )
        descriptors = _merge_plugin_entries(builtin_catalog().to_dict(), plugins)
        catalog = ScienceCapabilityCatalog(
            descriptors=descriptors,
            domain=domain_catalog().to_dict(),
            plugins=plugins,
            signature=signature,
            generation=signature[0],
            etag=_etag(signature, plugins),
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

    One entry per frozen science id plus one per registered third-party
    capability.  A registered capability without a handler stays ``registered``
    (declared, still refused); only a handler makes it ``executable``.  A
    capability the enablement ledger reports as enabled but that this process
    did not freeze is also ``registered`` — never executable.  The projection
    starts nothing and reads no artifact.
    """
    registry = analysis_registry(state)
    projection = capability_projection(
        registry if registry is not None else AnalysisCapabilityRegistry()
    )
    known = {entry["analysis_id"] for entry in projection}
    pending: list[dict[str, Any]] = []
    for record in plugin_capabilities(state):
        if record.capability_id in known:
            continue
        pending.append(
            {
                "analysis_id": record.capability_id,
                "state": "registered",
                "registered": True,
                "executable": False,
                "version": record.version,
                "owner": f"plugin:{record.plugin_name}",
            }
        )
    return (*projection, *pending)


def capability_descriptors(state: Any) -> tuple[dict[str, Any], ...]:
    """Return the capability descriptors forwarded verbatim by discovery.

    These are the catalog objects (never a platform-state annotation): a
    descriptor says what the capability *is*, while
    :func:`analysis_capabilities` says what this process can *execute*.  Since
    M2-05 the built-in entries are followed by the authorised third-party
    plugin descriptors, so the two discovery surfaces answer from one merged
    provider.
    """
    return science_catalog(state).descriptor_entries()
