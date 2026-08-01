# pyright: reportMissingTypeStubs=false
"""Workflow implementations.

Public symbols are exposed lazily via :pep:`562` ``__getattr__`` so that
importing a single workflow (e.g. ``from acp.workflows.energy import ...``)
does **not** pull in the others.  This keeps each workflow's third-party
dependencies decoupled and ``import acp.workflows`` cheap and side-effect
free.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "WorkflowRegistryEntry",
    "get_mechanism_stages",
    "get_workflow_entry",
    "list_workflow_entries",
    "register_workflow",
    "run_conformer_energy",
    "run_ensemble_generation",
    "run_mechanism_analysis",
    "run_md_replicas",
    "run_xtbmd_censo_energy",
    "run_singlepoint",
    "run_optimize",
    "run_frequency",
    "run_optfreq",
    "run_optfreqsp",
]

# Maps each public name to the submodule that defines it.  The submodule is
# imported on first access only, keeping ``import acp.workflows`` cheap and
# side-effect free.
_LAZY_SOURCES: dict[str, str] = {
    "get_mechanism_stages": "acp.workflows.mechanism",
    "run_mechanism_analysis": "acp.workflows.mechanism",
    "run_md_replicas": "acp.workflows.xtbmd_md",
    "run_singlepoint": "acp.workflows.simple",
    "run_optimize": "acp.workflows.simple",
    "run_frequency": "acp.workflows.simple",
    "run_optfreq": "acp.workflows.simple",
    "run_optfreqsp": "acp.workflows.simple",
    "run_ensemble_generation": "acp.workflows.ensemble",
    "run_conformer_energy": "acp.workflows.energy",
    "run_xtbmd_censo_energy": "acp.workflows.xtbmd_censo_energy",
    "WorkflowRegistryEntry": "acp.workflows.registry",
    "get_workflow_entry": "acp.workflows.registry",
    "list_workflow_entries": "acp.workflows.registry",
    "register_workflow": "acp.workflows.registry",
}


def __getattr__(name: str) -> Any:
    source = _LAZY_SOURCES.get(name)
    if source is None:
        raise AttributeError(f"module 'acp.workflows' has no attribute {name!r}")
    import importlib

    module = importlib.import_module(source)
    value = getattr(module, name)
    globals()[name] = value  # cache for subsequent lookups
    return value


def __dir__() -> list[str]:
    return sorted(list(globals()) + __all__)
