"""
Utility Modules
==============

Common utilities for ConformerSearch.
"""

from cccp.utils.constants import (
    ANGSTROM_TO_BOHR,
    ATOMIC_NUMBER,
    BOHR_TO_ANGSTROM,
    ELEMENT_MASS,
    GAS_CONSTANT_KCAL_PER_MOL_K,
    HARTREE_TO_KCAL,
    HARTREE_TO_KJ,
    KCAL_TO_HARTREE,
    KELVIN_TO_HARTREE,
)
from cccp.utils.file_io import (
    ensure_dir,
    read_energy_from_gaussian,
    read_gjf,
    read_json,
    read_xyz,
    read_xyz_multiframe,
    read_xyz_with_energy,
    write_json,
    write_xyz,
    write_xyz_multiframe,
)
from cccp.utils.geometry_tools import (
    GeometryUtils,
    LogParser,
)
from cccp.utils.resource_utils import (
    ResourceManager,
    calc_orca_maxcore,
    find_executable,
    mb_to_mem_str,
    mem_to_mb,
    resolve_executable_config,
)
from cccp.utils.solvent_map import (
    SOLVENT_ALIASES,
    orca_smd_solvent,
    xtb_solvent,
)

__all__ = [
    "HARTREE_TO_KCAL",
    "KCAL_TO_HARTREE",
    "HARTREE_TO_KJ",
    "BOHR_TO_ANGSTROM",
    "ANGSTROM_TO_BOHR",
    "KELVIN_TO_HARTREE",
    "GAS_CONSTANT_KCAL_PER_MOL_K",
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
