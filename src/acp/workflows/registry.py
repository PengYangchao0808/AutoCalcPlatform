"""Workflow registry: single source of truth for ACP workflow metadata.

The registry decouples API introspection from the scheduler's supported-workflow
set, allowing each workflow to declare its own label, description, and required
binaries.  The API consumes this registry instead of maintaining a static list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from acp.scheduler.jobs import SUPPORTED_WORKFLOWS


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
    "conformer": WorkflowRegistryEntry(
        name="conformer",
        label="Conformer Search",
        description="CREST conformer search → DFT refinement → single-point → thermo.",
        requires_binaries=["crest", "orca"],
    ),
    "nmr": WorkflowRegistryEntry(
        name="nmr",
        label="NMR",
        description="Conformer selection → GIAO shielding → Boltzmann-averaged report.",
        requires_binaries=["orca"],
    ),
    "benchmark": WorkflowRegistryEntry(
        name="benchmark",
        label="Benchmark",
        description="Run multiple conformer protocols and compare their results.",
        requires_binaries=["crest", "orca"],
    ),
    "mechanism": WorkflowRegistryEntry(
        name="mechanism",
        label="Mechanism / TS",
        description="TS search + optimization + IRC validation + energy barrier analysis.",
        requires_binaries=["orca"],
    ),
}


def list_workflow_entries() -> list[WorkflowRegistryEntry]:
    """Return metadata entries for every scheduler-supported workflow.

    Entries are returned in the registry's canonical order (fake first, then
    real QC workflows).  Only workflows that are also in ``SUPPORTED_WORKFLOWS``
    are included, so newly added workflows appear automatically once they are
    registered and supported.
    """
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
