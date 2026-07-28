"""
Utility Modules
==============

Common utilities for ConformerSearch.
"""

from cccp.utils.constants import (
    HARTREE_TO_KCAL,
    KCAL_TO_HARTREE,
    HARTREE_TO_KJ,
    BOHR_TO_ANGSTROM,
    ANGSTROM_TO_BOHR,
    ELEMENT_MASS,
    ATOMIC_NUMBER,
)

from cccp.utils.file_io import (
    read_xyz,
    write_xyz,
    read_xyz_multiframe,
    write_xyz_multiframe,
    read_gjf,
    read_energy_from_gaussian,
    read_xyz_with_energy,
    read_json,
    write_json,
    ensure_dir,
)

from cccp.utils.geometry_tools import (
    GeometryUtils,
    LogParser,
)

from cccp.utils.resource_utils import (
    mem_to_mb,
    mb_to_mem_str,
    calc_orca_maxcore,
    find_executable,
    resolve_executable_config,
    ResourceManager,
)

from cccp.utils.solvent_map import (
    orca_smd_solvent,
    xtb_solvent,
    SOLVENT_ALIASES,
)

__all__ = [
    "HARTREE_TO_KCAL",
    "KCAL_TO_HARTREE",
    "HARTREE_TO_KJ",
    "BOHR_TO_ANGSTROM",
    "ANGSTROM_TO_BOHR",
    "ELEMENT_MASS",
    "ATOMIC_NUMBER",
    "read_xyz",
    "write_xyz",
    "read_xyz_multiframe",
    "write_xyz_multiframe",
    "read_gjf",
    "read_energy_from_gaussian",
    "read_xyz_with_energy",
    "read_json",
    "write_json",
    "ensure_dir",
    "GeometryUtils",
    "LogParser",
    "mem_to_mb",
    "mb_to_mem_str",
    "calc_orca_maxcore",
    "find_executable",
    "resolve_executable_config",
    "ResourceManager",
    "orca_smd_solvent",
    "xtb_solvent",
    "SOLVENT_ALIASES",
]
