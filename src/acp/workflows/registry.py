"""Workflow registry: single source of truth for ACP workflow metadata.

The registry decouples API introspection from the scheduler's supported-workflow
set, allowing each workflow to declare its own label, description, and required
binaries.  The API consumes this registry instead of maintaining a static list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkflowRegistryEntry:
    """Metadata describing one supported ACP workflow.

    Attributes:
        name: Machine-readable workflow identifier (matches ``SUPPORTED_WORKFLOWS``).
        label: Human-readable short name.
        description: One-sentence explanation of what the workflow does.
        requires_binaries: QC programs this workflow normally requires.
    """

    name: str
    label: str
    description: str = ""
    requires_binaries: list[str] = field(default_factory=list)


_WORKFLOW_REGISTRY: dict[str, WorkflowRegistryEntry] = {
    "fake": WorkflowRegistryEntry(
        name="fake",
        label="Fake (demo)",
        description="Built-in no-op workflow; no external binaries required.",
        requires_binaries=[],
    ),
    "Confsearch": WorkflowRegistryEntry(
        name="Confsearch",
        label="Conformer Search",
        description=(
            "Unified conformer search + energies via protocol "
            "(xtb-crest / xtb-md / censo-crest / xtbmd-censo)."
        ),
        requires_binaries=["crest", "censo", "orca"],
    ),
    "PESsearch": WorkflowRegistryEntry(
        name="PESsearch",
        label="PES Search",
        description=(
            "Reaction path search + TS/intermediate guesses from a Confsearch "
            "manifest (mechanism stage S2)."
        ),
        requires_binaries=["orca", "xtb"],
    ),
    "BatchOptimize": WorkflowRegistryEntry(
        name="BatchOptimize",
        label="Batch Optimization",
        description=(
            "Batch structure optimization with optional frequency, single-point, "
            "and thermochemistry steps."
        ),
        requires_binaries=["orca", "shermo"],
    ),
    "nmr": WorkflowRegistryEntry(
        name="nmr",
        label="NMR + DP4/DP5",
        description=(
            "CREST+CENSO conformers → mPW1PW91/6-311G(d) GIAO NMR → "
            "Boltzmann averaging + DP4/DP5 stereochemistry assignment."
        ),
        requires_binaries=["crest", "censo", "orca"],
    ),
    "singlepoint": WorkflowRegistryEntry(
        name="singlepoint",
        label="Single Point Energy",
        description="ORCA single-point energy calculation at current geometry.",
        requires_binaries=["orca"],
    ),
    "optimize": WorkflowRegistryEntry(
        name="optimize",
        label="Geometry Optimization",
        description="ORCA geometry optimization.",
        requires_binaries=["orca"],
    ),
    "frequency": WorkflowRegistryEntry(
        name="frequency",
        label="Frequency Calculation",
        description="ORCA vibrational frequency calculation.",
        requires_binaries=["orca"],
    ),
    "scan": WorkflowRegistryEntry(
        name="scan",
        label="Relaxed Scan",
        description="Relax an internal coordinate at each scan point with ORCA.",
        requires_binaries=["orca"],
    ),
    "irc": WorkflowRegistryEntry(
        name="irc",
        label="Intrinsic Reaction Coordinate",
        description="Run an independent IRC from a transition-state structure.",
        requires_binaries=["orca"],
    ),
    "xtb_optimize": WorkflowRegistryEntry(
        name="xtb_optimize",
        label="xTB Optimization",
        description="Fast semi-empirical geometry optimization with xTB (GFN-xTB).",
        requires_binaries=["xtb"],
    ),
}


def list_workflow_entries() -> list[WorkflowRegistryEntry]:
    """Return metadata entries for every scheduler-supported workflow.

    Entries are returned in the registry's canonical order (fake first, then
    real QC workflows).  Only workflows that are also in ``SUPPORTED_WORKFLOWS``
    are included, so newly added workflows appear automatically once they are
    registered and supported.
    """
    from acp.scheduler.jobs import SUPPORTED_WORKFLOWS

    return [_WORKFLOW_REGISTRY[name] for name in _WORKFLOW_REGISTRY if name in SUPPORTED_WORKFLOWS]


def get_workflow_entry(name: str) -> WorkflowRegistryEntry | None:
    """Return metadata for a single workflow, or ``None`` if unknown."""
    return _WORKFLOW_REGISTRY.get(name)


def register_workflow(entry: WorkflowRegistryEntry) -> None:
    """Register (or override) a workflow entry.

    Primarily intended for tests and optional workflow plugins. Core workflows
    are pre-registered at import time.
    """
    _WORKFLOW_REGISTRY[entry.name] = entry


def workflow_to_dict(entry: WorkflowRegistryEntry) -> dict[str, Any]:
    """Serialize a registry entry to a plain dict for API responses."""
    return {
        "name": entry.name,
        "label": entry.label,
        "description": entry.description,
        "requires_binaries": list(entry.requires_binaries),
    }


__all__ = [
    "WorkflowRegistryEntry",
    "get_workflow_entry",
    "list_workflow_entries",
    "register_workflow",
    "workflow_to_dict",
]
