"""
Utility Modules
==============

Common utilities for ConformerSearch.
"""

from conformer_search.utils.constants import (
    HARTREE_TO_KCAL,
    KCAL_TO_HARTREE,
    HARTREE_TO_KJ,
    BOHR_TO_ANGSTROM,
    ANGSTROM_TO_BOHR,
    ELEMENT_MASS,
    ATOMIC_NUMBER,
)

from conformer_search.utils.file_io import (
    read_xyz,
    write_xyz,
    read_xyz_multiframe,
    write_xyz_multiframe,
    read_gjf,
    write_gjf,
    read_xyz_with_energy,
    ensure_dir,
)

from conformer_search.utils.geometry_tools import (
    GeometryUtils,
    LogParser,
)

from conformer_search.utils.resource_utils import (
    mem_to_mb,
    mb_to_mem_str,
    calc_orca_maxcore,
    find_executable,
    resolve_executable_config,
    ResourceManager,
)

from conformer_search.utils.keyword_translator import KeywordTranslator

from conformer_search.utils.solvent_map import (
    gaussian_pcm_keyword,
    orca_smd_solvent,
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
    "write_gjf",
    "read_xyz_with_energy",
    "ensure_dir",
    "GeometryUtils",
    "LogParser",
    "mem_to_mb",
    "mb_to_mem_str",
    "calc_orca_maxcore",
    "find_executable",
    "resolve_executable_config",
    "ResourceManager",
    "KeywordTranslator",
    "gaussian_pcm_keyword",
    "orca_smd_solvent",
    "SOLVENT_ALIASES",
]
