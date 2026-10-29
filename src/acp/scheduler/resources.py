"""Resolve scheduler resource intent once for local and remote execution."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from acp.scheduler.jobs import JobSpec
from cccp.config import load_config
from cccp.utils.resource_utils import normalize_memory


_CORE_KEYS: tuple[str, ...] = ("nproc", "ncores", "cores")


def validate_resource_request(resources: Mapping[str, Any]) -> None:
    """Pure resource validation shared by preflight and submit.

    Aliases follow the historical precedence ``nproc > ncores > cores``; a
    lower-priority ``0`` means "unset" but any boolean or non-integer value is
    rejected wherever it appears.  Two non-zero aliases that disagree are a
    conflict: admission and argv must describe the same request.

    Raises:
        ValueError: A boolean/non-numeric/non-integer core value, or a
            non-zero alias conflict.
    """
    numeric: dict[str, float] = {}
    for name in _CORE_KEYS:
        if name not in resources or resources[name] is None:
            continue
        value = resources[name]
        if isinstance(value, bool):
            raise ValueError(f"resources.{name} must be a positive integer")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"resources.{name} must be a positive integer") from None
        if number != int(number):
            raise ValueError(f"resources.{name} must be a positive integer")
        numeric[name] = number
    non_zero = {value for value in numeric.values() if value != 0}
    if len(non_zero) > 1:
        raise ValueError("resources.nproc/ncores/cores disagree")


def resolve_job_resources(spec: JobSpec) -> dict[str, Any]:
    """Resolve total memory and cores, keeping scheduler metadata intact.

    JobSpec/CLI unitless memory is GB. Persisted jobs carry an explicit
    suffix, so compute-node defaults cannot change their memory allocation.
    The cccp TaskResources API retains its separate unitless MB contract.
    """
    resources = dict(spec.resources)
    validate_resource_request(resources)
    nproc = next(
        (resources[name] for name in _CORE_KEYS if resources.get(name) is not None),
        None,
    )
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


__all__ = ["resolve_job_resources", "validate_resource_request", "with_job_resources"]
