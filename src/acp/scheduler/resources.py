"""Resolve scheduler resource intent once for local and remote execution."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from acp.scheduler.jobs import JobSpec
from cccp.config import load_config
from cccp.utils.resource_utils import normalize_memory

_CORE_KEYS: tuple[str, ...] = ("nproc", "ncores", "cores")


def _core_int(name: str, value: Any) -> int:
    """Parse one core value exactly like the execution path will."""
    if isinstance(value, bool):
        raise ValueError(f"resources.{name} must be a positive integer")
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            raise ValueError(f"resources.{name} must be a positive integer") from None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            raise ValueError(f"resources.{name} must be a positive integer")
        return int(value)
    raise ValueError(f"resources.{name} must be a positive integer")


def _resolve_cores(resources: Mapping[str, Any]) -> int | None:
    """Validate aliases and return the effective cores (``None`` = unset)."""
    parsed: dict[str, int] = {}
    for name in _CORE_KEYS:
        if name not in resources or resources[name] is None:
            continue
        number = _core_int(name, resources[name])
        if number < 0:
            raise ValueError(f"resources.{name} must be a positive integer")
        parsed[name] = number
    non_zero = {value for value in parsed.values() if value != 0}
    if len(non_zero) > 1:
        raise ValueError("resources.nproc/ncores/cores disagree")
    chosen = next((parsed[name] for name in _CORE_KEYS if name in parsed), None)
    if chosen == 0:
        raise ValueError("resources.nproc must be a positive integer")
    return chosen


def validate_resource_request(resources: Mapping[str, Any]) -> None:
    """Pure resource validation shared by preflight and submit.

    Aliases follow ``nproc > ncores > cores``; a lower-priority ``0`` means
    "unset" but every present value must parse exactly as the execution layer
    will parse the chosen one (bools, ``"4.0"``, ``"1e999"``, negatives and
    non-integer floats are refused).

    Raises:
        ValueError: Any value the execution resolver would later refuse, or a
            non-zero alias conflict.
    """
    _resolve_cores(resources)


def resolve_job_resources(spec: JobSpec) -> dict[str, Any]:
    """Resolve total memory and cores, keeping scheduler metadata intact.

    JobSpec/CLI unitless memory is GB. Persisted jobs carry an explicit
    suffix, so compute-node defaults cannot change their memory allocation.
    The cccp TaskResources API retains its separate unitless MB contract.
    """
    resources = dict(spec.resources)
    nproc = _resolve_cores(resources)
    memory = resources.get("mem", resources.get("memory_gb"))
    if nproc is None or memory is None:
        config = load_config(config_path=Path(spec.config_path) if spec.config_path else None)
        defaults = config["resources"]
        if nproc is None:
            nproc = defaults["nproc"]
        if memory is None:
            memory = defaults["mem"]
    cores = _core_int("nproc", nproc)
    if cores <= 0:
        raise ValueError("resources.nproc must be a positive integer")
    resources["nproc"] = cores
    resources["mem"] = normalize_memory(memory)
    return resources


def with_job_resources(spec: JobSpec) -> JobSpec:
    """Return a detached spec with explicit, validated execution resources."""
    return replace(spec, resources=resolve_job_resources(spec))


__all__ = ["resolve_job_resources", "validate_resource_request", "with_job_resources"]
