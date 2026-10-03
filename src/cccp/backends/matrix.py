"""Declarative backend capability matrix (dependency-free).

This module is the single authority for capability *declarations* and the
neutral :class:`BackendCapabilityStatus` vocabulary.  It imports nothing
beyond the standard library so both ``cccp.backends.registry`` (selection)
and ``cccp.backends.capabilities`` (introspection) can depend on it without
a ``registry <-> capabilities`` import cycle.

Declaration semantics: **declared == implemented**.  ``AVAILABLE`` means
the backend really implements the capability; ``STUBBED`` marks methods
that exist only to raise ``NotImplementedError``; ``NOT_IMPLEMENTED`` marks
gaps.  Binary presence is NOT a declaration concern — runtime per-capability
probes (e.g. ``is_isostat_available`` / ``is_shermo_available``) judge it
and surface ``BackendUnavailableError`` when a tool is missing.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "CAPABILITY_ALIASES",
    "CAPABILITY_MATRIX",
    "BackendCapabilityStatus",
    "normalize_capability_name",
]


class BackendCapabilityStatus(str, Enum):
    """Declared status for a backend capability."""

    AVAILABLE = "available"
    STUBBED = "stubbed"
    NOT_IMPLEMENTED = "not_implemented"
    MISSING_BINARY = "missing_binary"


#: Caller-facing capability aliases folded onto canonical matrix keys.
CAPABILITY_ALIASES: dict[str, str] = {
    "optimization": "geometry_optimization",
    "optimizer": "geometry_optimization",
    "geometry_optimization": "geometry_optimization",
    "constrained_optimize": "constrained_optimization",
    "constrained_optimization": "constrained_optimization",
    "single_point": "single_point",
    "sp": "single_point",
    "frequency": "frequency",
    "freq": "frequency",
    "conformer_search": "conformer_search",
    "search": "conformer_search",
    "clustering": "clustering",
    "cluster": "clustering",
    "thermochemistry": "thermochemistry",
    "thermo": "thermochemistry",
    "enso_thermo": "mrrho_thermochemistry",
    "mrrho_thermo": "mrrho_thermochemistry",
    "mrrho_thermochemistry": "mrrho_thermochemistry",
    "nmr_shielding": "nmr_shielding",
    "nmr": "nmr_shielding",
    "relaxed_scan": "relaxed_scan",
    "path_search": "relaxed_scan",
    "scan": "relaxed_scan",
    "transition_state": "transition_state",
    "ts": "transition_state",
    "irc": "irc",
}

CAPABILITY_MATRIX: dict[str, dict[str, BackendCapabilityStatus]] = {
    "censo": {
        "geometry_optimization": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "constrained_optimization": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "single_point": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "frequency": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "conformer_search": BackendCapabilityStatus.AVAILABLE,
        "clustering": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "mrrho_thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nmr_shielding": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "relaxed_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "transition_state": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "irc": BackendCapabilityStatus.NOT_IMPLEMENTED,
    },
    "orca": {
        "geometry_optimization": BackendCapabilityStatus.AVAILABLE,
        "constrained_optimization": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "single_point": BackendCapabilityStatus.AVAILABLE,
        "frequency": BackendCapabilityStatus.AVAILABLE,
        "conformer_search": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "clustering": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "mrrho_thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nmr_shielding": BackendCapabilityStatus.AVAILABLE,
        "relaxed_scan": BackendCapabilityStatus.AVAILABLE,
        "transition_state": BackendCapabilityStatus.AVAILABLE,
        "irc": BackendCapabilityStatus.AVAILABLE,
    },
    "crest": {
        # Declaration = implemented: optimize/single_point exist only as
        # NotImplementedError stubs (delta D2), frequency is absent (D4).
        "geometry_optimization": BackendCapabilityStatus.STUBBED,
        "constrained_optimization": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "single_point": BackendCapabilityStatus.STUBBED,
        "frequency": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "conformer_search": BackendCapabilityStatus.AVAILABLE,
        "clustering": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "mrrho_thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nmr_shielding": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "relaxed_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "transition_state": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "irc": BackendCapabilityStatus.NOT_IMPLEMENTED,
    },
    "xtb": {
        "geometry_optimization": BackendCapabilityStatus.AVAILABLE,
        "constrained_optimization": BackendCapabilityStatus.AVAILABLE,
        "single_point": BackendCapabilityStatus.AVAILABLE,
        "frequency": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "conformer_search": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "clustering": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "mrrho_thermochemistry": BackendCapabilityStatus.AVAILABLE,
        "nmr_shielding": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "relaxed_scan": BackendCapabilityStatus.AVAILABLE,
        "transition_state": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "irc": BackendCapabilityStatus.NOT_IMPLEMENTED,
    },
    "external": {
        "geometry_optimization": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "constrained_optimization": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "single_point": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "frequency": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "conformer_search": BackendCapabilityStatus.NOT_IMPLEMENTED,
        # Declaration = implemented (delta D3): binary presence is judged
        # at runtime by is_isostat_available / is_shermo_available.
        "clustering": BackendCapabilityStatus.AVAILABLE,
        "thermochemistry": BackendCapabilityStatus.AVAILABLE,
        "mrrho_thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nmr_shielding": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "relaxed_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "transition_state": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "irc": BackendCapabilityStatus.NOT_IMPLEMENTED,
    },
    "molclus": {
        "geometry_optimization": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "constrained_optimization": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "single_point": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "frequency": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "conformer_search": BackendCapabilityStatus.AVAILABLE,
        "clustering": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "mrrho_thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nmr_shielding": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "relaxed_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "transition_state": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "irc": BackendCapabilityStatus.NOT_IMPLEMENTED,
    },
    "isostat": {
        "geometry_optimization": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "constrained_optimization": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "single_point": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "frequency": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "conformer_search": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "clustering": BackendCapabilityStatus.AVAILABLE,
        "thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "mrrho_thermochemistry": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nmr_shielding": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "relaxed_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "transition_state": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "irc": BackendCapabilityStatus.NOT_IMPLEMENTED,
    },
}


def normalize_capability_name(capability: str) -> str:
    """Fold a caller-facing capability name onto its canonical matrix key.

    Raises:
        ValueError: If the capability name is unknown.
    """
    key = capability.lower()
    if key not in CAPABILITY_ALIASES:
        known = ", ".join(sorted(CAPABILITY_ALIASES))
        raise ValueError(f"Unknown capability: {capability}. Known: {known}")
    return CAPABILITY_ALIASES[key]
