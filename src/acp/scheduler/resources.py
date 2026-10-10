"""Resolve scheduler resource intent once for local and remote execution."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from acp.scheduler.jobs import JobSpec
from cccp.config import load_config
from cccp.utils.resource_utils import normalize_memory


def resolve_job_resources(spec: JobSpec) -> dict[str, Any]:
    """Resolve total memory and cores, keeping scheduler metadata intact.

    JobSpec/CLI unitless memory is GB. Persisted jobs carry an explicit
    suffix, so compute-node defaults cannot change their memory allocation.
    The cccp TaskResources API retains its separate unitless MB contract.
    """
    resources = dict(spec.resources)
    nproc = resources.get("nproc", resources.get("ncores"))
    memory = resources.get("mem", resources.get("memory_gb"))
    if nproc is None or memory is None:
        config = load_config(config_path=Path(spec.config_path) if spec.config_path else None)
        defaults = config["resources"]
        if nproc is None:
            nproc = defaults["nproc"]
        if memory is None:
            memory = defaults["mem"]
    if isinstance(nproc, bool):
        raise ValueError("resources.nproc must be a positive integer")
    try:
        cores = int(nproc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("resources.nproc must be a positive integer") from exc
    if cores <= 0 or (isinstance(nproc, float) and nproc != cores):
        raise ValueError("resources.nproc must be a positive integer")
    resources["nproc"] = cores
    resources["mem"] = normalize_memory(memory)
    return resources


def with_job_resources(spec: JobSpec) -> JobSpec:
    """Return a detached spec with explicit, validated execution resources."""
    return replace(spec, resources=resolve_job_resources(spec))


__all__ = ["resolve_job_resources", "with_job_resources"]
