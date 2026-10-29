"""CCCP retired-engine xTB-MD closure (T24).

Pure-algorithm home for the retired ``xtbmd_censo_energy`` trajectory parsing,
geometry metrics, outcome classification, and GFN1 energy-window filter.
Re-exports only (no implementation) per repo ANTI #5.  The historical ACP path
re-exports the same objects.
"""

from __future__ import annotations

from cccp.analysis.xtbmd.energy_filter import (
    EnergyFilterResult,
    _filter_energy_window,
)
from cccp.analysis.xtbmd.trajectory import (
    _TITLE_ENERGY_RE,
    _aligned_rmsd,
    _classify_frame_result,
    _count_xyz_frames_strict,
    _histogram_divergence,
    _match_isomers_frame,
    _max_principal_moment,
    _parse_kcal_per_mol,
    _parse_title_energy_hartree,
    _radius_of_gyration,
    _read_trajectory,
    _uniform_subsample_indices,
)

__all__ = [
    "EnergyFilterResult",
    "_TITLE_ENERGY_RE",
    "_aligned_rmsd",
    "_classify_frame_result",
    "_count_xyz_frames_strict",
    "_filter_energy_window",
    "_histogram_divergence",
    "_match_isomers_frame",
    "_max_principal_moment",
    "_parse_kcal_per_mol",
    "_parse_title_energy_hartree",
    "_radius_of_gyration",
    "_read_trajectory",
    "_uniform_subsample_indices",
]
