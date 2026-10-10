"""
Resource Utilities
==================

Utilities for managing computational resources (memory, CPU, executables).
Extracted from RPH.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


_MEMORY_PATTERN = re.compile(
    r"((?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(TB|GB|MB|T|G|M)?", re.IGNORECASE
)
_MEMORY_FACTORS = {"MB": 1, "GB": 1024, "TB": 1024 * 1024}


def _memory_parts(value: str | int | float, default_unit: str) -> tuple[float, str]:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"Invalid memory specification: {value!r}")
    unit = default_unit.upper()
    if unit not in _MEMORY_FACTORS:
        raise ValueError(f"Invalid memory unit: {default_unit!r}")
    if isinstance(value, str):
        match = _MEMORY_PATTERN.fullmatch(re.sub(r"\s+", "", value))
        if match is None:
            raise ValueError(f"Cannot parse memory string: {value!r}")
        amount = float(match[1])
        if match[2]:
            unit = match[2].upper()
            if len(unit) == 1:
                unit += "B"
    else:
        amount = float(value)
    total = amount * _MEMORY_FACTORS[unit]
    if not math.isfinite(total) or total <= 0:
        raise ValueError(f"Memory must be positive and finite: {value!r}")
    return amount, unit


def parse_memory_mb(value: str | int | float, *, default_unit: str = "GB") -> float:
    """Parse total memory in MB; callers declare their unitless-value contract."""
    amount, unit = _memory_parts(value, default_unit)
    return amount * _MEMORY_FACTORS[unit]


def normalize_memory(value: str | int | float, *, default_unit: str = "GB") -> str:
    """Return a validated, unit-suffixed memory value, preserving the chosen unit."""
    amount, unit = _memory_parts(value, default_unit)
    return f"{str(amount).removesuffix('.0')}{unit}"


def mem_to_mb(mem_str: str | int | float) -> int:
    """
    Convert a memory specification to megabytes.

    Args:
        mem_str: Memory string like "16GB", "4096MB", or "1TB". Bare
            numeric values are interpreted as GB to match the Workbench's
            default unit.

    Returns:
        Memory in MB
    """
    total = int(parse_memory_mb(mem_str))
    if total < 1:
        raise ValueError("Memory must be at least 1 MB")
    return total


def mb_to_mem_str(mb: int) -> str:
    """
    Convert megabytes to memory string.

    Args:
        mb: Memory in MB

    Returns:
        Memory string like "16GB"
    """
    if mb >= 1024 and mb % 1024 == 0:
        return f"{mb // 1024}GB"
    return f"{mb}MB"


def _validate_orca_budget(mem_mb: int, nproc: int, safety_factor: float) -> None:
    if isinstance(nproc, bool) or not isinstance(nproc, int) or nproc <= 0:
        raise ValueError("nproc must be a positive integer")
    if isinstance(safety_factor, bool) or not 0 < safety_factor <= 1:
        raise ValueError("orca_maxcore_safety must be in (0, 1]")
    if not math.isfinite(mem_mb) or mem_mb <= 0:
        raise ValueError("Total memory must be positive and finite")


def calc_orca_maxcore(mem_mb: int, nproc: int, safety_factor: float = 0.8) -> int:
    """
    Calculate ORCA maxcore parameter.

    Args:
        mem_mb: Total memory in MB
        nproc: Number of processes
        safety_factor: Safety factor (default 0.8)

    Returns:
        Maxcore value per process in MB
    """
    _validate_orca_budget(mem_mb, nproc, safety_factor)
    result = int(mem_mb * safety_factor / nproc)
    if result < 1:
        raise ValueError("Memory budget is too small for ORCA: maxcore would be below 1 MB")
    return result


def resolve_orca_maxcore(
    mem_mb: int, nproc: int, safety_factor: float = 0.8, maxcore: int | None = None
) -> int:
    """Resolve ORCA's per-process MB, rejecting pins that exceed the total budget."""
    _validate_orca_budget(mem_mb, nproc, safety_factor)
    if maxcore is None:
        return calc_orca_maxcore(mem_mb, nproc, safety_factor)
    if isinstance(maxcore, bool) or not isinstance(maxcore, int) or maxcore <= 0:
        raise ValueError("ORCA maxcore must be a positive integer in MB per process")
    if maxcore * nproc > mem_mb:
        raise ValueError(
            f"Memory budget ({mem_mb} MB) cannot cover nproc * maxcore "
            f"({nproc} * {maxcore} = {nproc * maxcore} MB)"
        )
    return maxcore


def find_executable(program_name: str, fallback_paths: Optional[list] = None) -> Tuple[Optional[Path], str]:
    """
    Find executable in system PATH or fallback locations.

    Args:
        program_name: Name of executable
        fallback_paths: List of fallback paths to check

    Returns:
        Tuple of (Path to executable, source)
        - Path or None if not found
        - source: 'PATH', 'FALLBACK', or 'NOT_FOUND'
    """
    exe = shutil.which(program_name)
    if exe:
        return Path(exe), 'PATH'

    if fallback_paths:
        for path_str in fallback_paths:
            path = Path(path_str)
            if path.is_file() and os.access(path, os.X_OK):
                return path.resolve(), 'FALLBACK'
            if path.is_dir():
                exe_path = path / program_name
                if exe_path.is_file() and os.access(exe_path, os.X_OK):
                    return exe_path.resolve(), 'FALLBACK'

    return None, 'NOT_FOUND'


def resolve_executable_config(config: Dict[str, Any], program_key: str) -> Tuple[Path, Dict[str, Any]]:
    """
    Resolve executable path from configuration.

    Args:
        config: Configuration dictionary
        program_key: Key for executable (e.g., 'gaussian', 'orca')

    Returns:
        Tuple of (executable_path, resolved_config)
    """
    executables = config.get('executables', {})
    prog_config = executables.get(program_key, {})

    prog_path = prog_config.get('path', program_key)
    exe = Path(prog_path)

    if not exe.is_absolute():
        exe, _ = find_executable(prog_path)

    fallback_paths = prog_config.get('fallback_paths', [])
    if not exe or not exe.exists():
        exe, source = find_executable(prog_path, fallback_paths)
        if exe:
            logger.info(f"Using fallback {program_key}: {exe} ({source})")

    return exe, prog_config


def get_system_resources() -> Dict[str, int]:
    """
    Get available system resources.

    Returns:
        Dictionary with 'nproc' and 'mem_mb' keys
    """
    import multiprocessing

    nproc = multiprocessing.cpu_count()

    mem_mb = 16000
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    parts = line.split()
                    if len(parts) >= 2:
                        mem_kb = int(parts[1])
                        mem_mb = mem_kb // 1024
                        break
    except Exception:
        pass

    return {
        'nproc': nproc,
        'mem_mb': mem_mb
    }


def format_resource_str(nproc: int, mem_mb: int) -> str:
    """
    Format resources as string.

    Args:
        nproc: Number of processors
        mem_mb: Memory in MB

    Returns:
        Formatted string like "16 cores, 32GB"
    """
    mem_str = mb_to_mem_str(mem_mb)
    return f"{nproc} cores, {mem_str}"


class ResourceManager:
    """
    Manages computational resources for QC calculations.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize resource manager.

        Args:
            config: Configuration dictionary with 'resources' section
        """
        self.config = config
        resources = config.get('resources', {})

        from cccp.config import _get_default_config

        defaults = _get_default_config()['resources']
        self.nproc = resources.get('nproc', defaults['nproc'])
        self.mem_str = resources.get('mem', defaults['mem'])
        self.mem_mb = 0 if not self.mem_str or self.mem_str == '0' else mem_to_mb(self.mem_str)

        self._resolve_from_system()

    def _resolve_from_system(self):
        """Resolve resources from system if not specified."""
        if self.nproc <= 0:
            self.nproc = get_system_resources()['nproc']

        if not self.mem_str or self.mem_str == '0':
            self.mem_mb = get_system_resources()['mem_mb']
            self.mem_str = mb_to_mem_str(self.mem_mb)

    def get_orca_params(self) -> Dict[str, Any]:
        """Get ORCA-specific resource parameters."""
        safety = self.config.get('resources', {}).get('orca_maxcore_safety', 0.8)
        return {
            'nprocs': self.nproc,
            'maxcore': resolve_orca_maxcore(
                self.mem_mb, self.nproc, safety,
                self.config.get('executables', {}).get('orca', {}).get('maxcore'),
            )
        }

    def get_crest_params(self) -> Dict[str, Any]:
        """Get CREST-specific resource parameters."""
        return {
            'threads': self.nproc
        }

    def get_xtb_params(self) -> Dict[str, Any]:
        """Get xTB-specific resource parameters."""
        return {
            'parallel': self.nproc
        }
