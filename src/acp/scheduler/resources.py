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
    # ``cores`` is the MCP-facing alias; precedence stays nproc > ncores >
    # cores and an explicit value is validated even when it is zero.  Zero on a
    # lower-priority alias means "unset" (historical payloads send both), but a
    # non-zero disagreement is a real conflict: admission and argv must agree.
    core_fields = [
        (name, resources[name])
        for name in ("nproc", "ncores", "cores")
        if resources.get(name) is not None
    ]
    nproc = core_fields[0][1] if core_fields else None
    effective = [(name, value) for name, value in core_fields if value != 0]
    if len(effective) > 1:
        distinct: set[float | str] = set()
        for _name, value in effective:
            try:
                distinct.add(float(value))
            except (TypeError, ValueError):
                distinct.add(str(value))
        if len(distinct) > 1:
            raise ValueError("resources.nproc/ncores/cores disagree")
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
