# pyright: reportMissingTypeStubs=false
"""Workflow implementations.

Public symbols are exposed lazily via :pep:`562` ``__getattr__`` so that
importing a single workflow (e.g. ``from acp.workflows.conformer import ...``)
does **not** pull in the others.  This keeps each workflow's third-party
dependencies decoupled — a conformer job no longer requires the NMR report
dependencies (``openpyxl``) to be importable, which matters on slimmed-down
remote compute nodes.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "BENCHMARK_LEVELS",
    "BenchmarkRunner",
    "WorkflowRegistryEntry",
    "boltzmann_weight_ensemble",
    "get_mechanism_stages",
    "get_nmr_stages",
    "get_protocol_stages",
    "get_workflow_entry",
    "list_workflow_entries",
    "register_workflow",
    "run_conformer_energy",
    "run_conformer_search",
    "run_ensemble_generation",
    "run_mechanism_analysis",
    "run_nmr_calculation",
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
    "BENCHMARK_LEVELS": "acp.workflows.benchmark",
    "BenchmarkRunner": "acp.workflows.benchmark",
    "boltzmann_weight_ensemble": "acp.workflows.conformer",
    "get_protocol_stages": "acp.workflows.conformer",
    "run_conformer_search": "acp.workflows.conformer",
    "get_mechanism_stages": "acp.workflows.mechanism",
    "run_mechanism_analysis": "acp.workflows.mechanism",
    "get_nmr_stages": "acp.workflows.nmr",
    "run_nmr_calculation": "acp.workflows.nmr",
    "run_singlepoint": "acp.workflows.simple",
    "run_optimize": "acp.workflows.simple",
    "run_frequency": "acp.workflows.simple",
    "run_optfreq": "acp.workflows.simple",
    "run_optfreqsp": "acp.workflows.simple",
    "run_ensemble_generation": "acp.workflows.ensemble",
    "run_conformer_energy": "acp.workflows.energy",
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
