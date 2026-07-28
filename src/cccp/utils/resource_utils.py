"""
Resource Utilities
==================

Utilities for managing computational resources (memory, CPU, executables).
Extracted from RPH.

Author: QCcalc Team
"""

import os
import re
import shutil
from pathlib import Path
from typing import Dict, Optional, Tuple, Any
import logging

logger = logging.getLogger(__name__)


def mem_to_mb(mem_str: str) -> int:
    """
    Convert memory string to megabytes.

    Args:
        mem_str: Memory string like "16GB", "4096MB", "32G"

    Returns:
        Memory in MB
    """
    if not mem_str:
        return 4000

    mem_str = str(mem_str).strip().upper()

    gb_match = re.match(r'^(\d+(?:\.\d+)?)\s*GB?$', mem_str)
    if gb_match:
        return int(float(gb_match.group(1)) * 1024)

    mb_match = re.match(r'^(\d+(?:\.\d+)?)\s*MB?$', mem_str)
    if mb_match:
        return int(float(mb_match.group(1)))

    num_match = re.match(r'^(\d+(?:\.\d+)?)$', mem_str)
    if num_match:
        return int(float(num_match.group(1)))

    raise ValueError(f"Cannot parse memory string: {mem_str}")


def mb_to_mem_str(mb: int) -> str:
    """
    Convert megabytes to memory string.

    Args:
        mb: Memory in MB

    Returns:
        Memory string like "16GB"
    """
    if mb >= 1024:
        return f"{mb // 1024}GB"
    return f"{mb}MB"


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
    return int(mem_mb * safety_factor / nproc)


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

        self.nproc = resources.get('nproc', 16)
        self.mem_str = resources.get('mem', '32GB')
        self.mem_mb = mem_to_mb(self.mem_str)

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
            'maxcore': calc_orca_maxcore(self.mem_mb, self.nproc, safety)
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
