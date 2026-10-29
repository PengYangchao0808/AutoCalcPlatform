"""CCCP retired-engine ensemble closure (T24).

Pure-algorithm home for the retired Confsearch-engine ensemble science:
levels resolution, solvent/ewin resolvers, Boltzmann weights + cumulative
selection, ensemble assembly/frame ordering, ensemble total-Gibbs thermos, and
input classification.  Re-exports only (no implementation) per repo ANTI #5.
The historical ACP paths re-export the same objects.
"""

from __future__ import annotations

from cccp.analysis.ensemble.assembly import (
    _build_ensemble_from_censo,
    _ensemble_xyz_frames,
    build_result_ensemble,
    censo_record_to_candidate,
)
from cccp.analysis.ensemble.inputs import (
    _is_file_input,
    _is_multiframe_xyz,
)
from cccp.analysis.ensemble.levels import (
    _base_route_extras,
    _solvent_from_level,
    conformer_tag,
    resolve_levels,
)
from cccp.analysis.ensemble.selection import (
    boltzmann_weights,
    build_ensemble_summary,
    select_cumulative_boltzmann,
)
from cccp.analysis.ensemble.solvent import (
    SAMPLING_DEFAULT_SOLVENT_MODEL,
    SAMPLING_SOLVENT_CONFIG_KEY,
    SamplingSolventReceipt,
    StageSolventModels,
    resolve_crest_ewin,
    resolve_sampling_solvent,
    resolve_solvent_config,
    resolve_stage_solvent_models,
)
from cccp.analysis.ensemble.thermo import (
    EnsembleThermoSummary,
    ensemble_total_gibbs,
    ensemble_total_gibbs_from_values,
    mixing_entropy,
    s_mix_cal_per_mol_kelvin,
    s_mix_kcal_per_mol_kelvin,
    t_s_mix_kcal_per_mol,
)

__all__ = [
    "EnsembleThermoSummary",
    "SAMPLING_DEFAULT_SOLVENT_MODEL",
    "SAMPLING_SOLVENT_CONFIG_KEY",
    "SamplingSolventReceipt",
    "StageSolventModels",
    "_base_route_extras",
    "_build_ensemble_from_censo",
    "_ensemble_xyz_frames",
    "_is_file_input",
    "_is_multiframe_xyz",
    "_solvent_from_level",
    "boltzmann_weights",
    "build_ensemble_summary",
    "build_result_ensemble",
    "censo_record_to_candidate",
    "conformer_tag",
    "ensemble_total_gibbs",
    "ensemble_total_gibbs_from_values",
    "mixing_entropy",
    "resolve_crest_ewin",
    "resolve_levels",
    "resolve_sampling_solvent",
    "resolve_solvent_config",
    "resolve_stage_solvent_models",
    "s_mix_cal_per_mol_kelvin",
    "s_mix_kcal_per_mol_kelvin",
    "select_cumulative_boltzmann",
    "t_s_mix_kcal_per_mol",
]
