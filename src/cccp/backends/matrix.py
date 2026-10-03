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
    "CAPABILITY_BACKEND_PRIORITY",
    "CAPABILITY_MATRIX",
    "TASK_CAPABILITY_MAP",
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
    "constrained_scan": "constrained_relaxed_scan",
    "constrained_relaxed_scan": "constrained_relaxed_scan",
    "rigid_scan": "rigid_scan",
    "transition_state": "transition_state",
    "ts": "transition_state",
    "irc": "irc",
    "casscf": "casscf",
    "nevpt2": "nevpt2",
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
        "constrained_relaxed_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "rigid_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "casscf": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nevpt2": BackendCapabilityStatus.NOT_IMPLEMENTED,
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
        "constrained_relaxed_scan": BackendCapabilityStatus.AVAILABLE,
        "rigid_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "casscf": BackendCapabilityStatus.AVAILABLE,
        "nevpt2": BackendCapabilityStatus.AVAILABLE,
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
        "constrained_relaxed_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "rigid_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "casscf": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nevpt2": BackendCapabilityStatus.NOT_IMPLEMENTED,
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
        "constrained_relaxed_scan": BackendCapabilityStatus.AVAILABLE,
        "rigid_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "casscf": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nevpt2": BackendCapabilityStatus.NOT_IMPLEMENTED,
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
        "constrained_relaxed_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "rigid_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "casscf": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nevpt2": BackendCapabilityStatus.NOT_IMPLEMENTED,
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
        "constrained_relaxed_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "rigid_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "casscf": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nevpt2": BackendCapabilityStatus.NOT_IMPLEMENTED,
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
        "constrained_relaxed_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "rigid_scan": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "casscf": BackendCapabilityStatus.NOT_IMPLEMENTED,
        "nevpt2": BackendCapabilityStatus.NOT_IMPLEMENTED,
    },
}


#: Task kind (the seven core kinds, keyed by ``TaskKind`` value) → the full
#: capability-name vocabulary that task may require.  The *concrete* required
#: capability per request is derived from the scientific options (structure
#: role, optimization mode, scan constraints, NEVPT2, …) by the two-step
#: selection module of the calculation package; this table is the
#: declaration-side mapping.  P2 tasks extend it in todo 24 (names and
#: ambiguity priority for GIAO/EnGrad/CENSO are deliberately NOT declared
#: here).
TASK_CAPABILITY_MAP: dict[str, tuple[str, ...]] = {
    "singlepoint": ("single_point",),
    "optimize": ("geometry_optimization", "transition_state", "constrained_optimization"),
    "frequency": ("frequency",),
    "scan": ("relaxed_scan", "constrained_relaxed_scan", "rigid_scan"),
    "irc": ("irc",),
    "casscf": ("casscf", "nevpt2"),
    "thermochemistry": ("thermochemistry",),
}


#: Deterministic backend priority per capability (first = preferred) used
#: when several backends declare the capability AVAILABLE and the request
#: names no explicit backend.  Only the orca/xtb ordering is pinned here;
#: ambiguity priority for P2 capabilities is left to todo 24.  A capability
#: without an entry falls back to the declaring backends in sorted name
#: order (still deterministic).
CAPABILITY_BACKEND_PRIORITY: dict[str, tuple[str, ...]] = {
    "single_point": ("orca", "xtb"),
    "geometry_optimization": ("orca", "xtb"),
    "constrained_optimization": ("xtb",),
    "transition_state": ("orca",),
    "frequency": ("orca",),
    "relaxed_scan": ("orca", "xtb"),
    "constrained_relaxed_scan": ("orca", "xtb"),
    "rigid_scan": (),
    "irc": ("orca",),
    "casscf": ("orca",),
    "nevpt2": ("orca",),
    "thermochemistry": ("external",),
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
