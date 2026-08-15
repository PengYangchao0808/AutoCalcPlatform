# pyright: reportMissingImports=false, reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnannotatedClassAttribute=false, reportImplicitOverride=false, reportUnusedCallResult=false, reportUnnecessaryCast=false
"""ReactionProfileHunter provider adapters for the mechanism study layer.

DEPRECATED as the production path since the native engine internalization:
``NativeCensoLiteProvider`` / ``NativeReversePebStrategy`` /
``NativeRefinementProvider`` are the default study engines. These adapters
remain available behind ``config['mechanism']['provider_backend'] = 'rph'``
solely for scientific parity comparison runs against the external
ReactionProfileHunter checkout.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np

from acp.core.models import Structure, StructureEnsemble, StructureRecord

from .._helpers import opt_float as _opt_float
from ..models import (
    ArtifactRef,
    PathCandidate,
    PathPoint,
    PathResult,
    Provenance,
    SeedCandidate,
    StableState,
    StationaryPoint,
    StationaryPointRequest,
    ThermoCorrection,
    TsIdentity,
    TsValidation,
)
from ..presets import (
    RPH_CENSO_LITE_MODE,
    RPH_PROFILE_IDS,
    XTB_FAST_MODE,
    rph_profile_id,
)
from ..presets import (
    FidelityProfile as AcpFidelityProfile,
)
from .contracts import (
    EnsembleProvider,
    PathSearchStrategy,
    RefinementAttempt,
    RefinementManifest,
    RefinementProvider,
)

logger = logging.getLogger(__name__)

DEFAULT_RPH_REPO_PATH = Path(
    "/mnt/e/Calculations/Common_Script/Auto_Calc_Platform/ReactionProfileHunter"
)
DEFAULT_RPH_WORK_ROOT = Path("/tmp/opencode/acp_rph")
RPH_PROVIDER_NAME = "rph"
RPH_PROVIDER_COMMIT = "3abbaecdd0b3c8cad6c4106c6e3ea07b6071e437"
RPH_REVERSE_STRATEGY_ID = "rph-reverse"
RPH_REFINEMENT_SCHEMA_VERSION = "refinement_manifest_v1"
RPH_SELECTION_ALGORITHM = "endpoint_knee_shift_midpoint_v1"
RPH_ADAPTER_SCHEMA_VERSION = "acp_rph_adapter_v1"

_RPH_TOP_LEVEL_KEYS = (
    "executables",
    "resources",
    "thermo",
    "theory",
    "refinement",
    "step1",
    "step2",
    "step3",
    "step4",
    "ui",
    "run",
)


class RPHUnavailableError(RuntimeError):
    """Raised when the local ReactionProfileHunter checkout cannot be imported."""


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _sha256_text(payload: str) -> str:
    return _sha256_bytes(payload.encode("utf-8"))


def _json_hash(payload: Any) -> str:
    return _sha256_text(json.dumps(payload, sort_keys=True, default=str))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _first_float(mapping: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _opt_float(mapping.get(key))
        if value is not None:
            return value
    return None


def _normalize_repo_path(path: Path | str) -> Path:
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve(strict=False)
    except OSError:
        return candidate.absolute()


def _insert_repo_on_sys_path(repo_path: Path) -> None:
    normalized = str(_normalize_repo_path(repo_path))
    if normalized not in sys.path:
        sys.path.insert(0, normalized)


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = copy.deepcopy(dict(base))
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, Mapping):
            merged[key] = _deep_merge(cast(Mapping[str, Any], merged[key]), value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _config_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _nested_config_value(config: Mapping[str, Any] | None, *path: str) -> Any:
    current: Any = config or {}
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def resolve_rph_repo_path(
    rph_path: Path | str | None = None,
    config: Mapping[str, Any] | None = None,
) -> Path:
    """Resolve the local ReactionProfileHunter checkout path.

    Resolution order:
      1. explicit constructor argument
      2. config keys (`rph.path`, `config['rph']['path']`, mechanism-scoped variants)
      3. env var `ACP_RPH_PATH`
      4. documented sibling checkout fallback
    """

    if rph_path is not None:
        return _normalize_repo_path(rph_path)

    candidates = (
        _nested_config_value(config, "rph", "path"),
        _nested_config_value(config, "mechanism", "rph", "path"),
        _nested_config_value(config, "mechanism", "providers", "rph", "path"),
        (config or {}).get("rph.path"),
        (config or {}).get("mechanism.rph.path"),
        os.environ.get("ACP_RPH_PATH"),
        DEFAULT_RPH_REPO_PATH,
    )
    for candidate in candidates:
        if candidate is None:
            continue
        candidate_text = str(candidate).strip()
        if candidate_text:
            return _normalize_repo_path(candidate_text)
    return _normalize_repo_path(DEFAULT_RPH_REPO_PATH)


def _unavailable_error(module_name: str, repo_path: Path, _exc: Exception) -> RPHUnavailableError:
    remediation = (
        f"ReactionProfileHunter import failed for {module_name!r}. "
        f"Expected checkout: {repo_path}. "
        "Pass rph_path=..., set ACP_RPH_PATH, or provide config['rph']['path']."
    )
    if not repo_path.exists():
        remediation += " The resolved checkout path does not exist."
    return RPHUnavailableError(remediation)


def _import_rph_module(module_name: str, repo_path: Path | str) -> Any:
    resolved_repo = _normalize_repo_path(repo_path)
    _insert_repo_on_sys_path(resolved_repo)
    try:
        return importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - exact exception type is environment-dependent
        raise _unavailable_error(module_name, resolved_repo, exc) from exc


@cache
def _cached_rph_version(repo_path: str) -> str:
    version_module = _import_rph_module("rph_core.version", repo_path)
    return str(getattr(version_module, "__version__", "unknown"))


def rph_version(
    rph_path: Path | str | None = None,
    config: Mapping[str, Any] | None = None,
) -> str:
    """Return the imported ReactionProfileHunter version lazily and cache it."""

    repo_path = resolve_rph_repo_path(rph_path, config)
    return _cached_rph_version(str(repo_path))


def _base_rph_config() -> dict[str, Any]:
    return {
        "schema_version": "rph_v4_config_v1",
        "executables": {
            "gaussian": {
                "path": "",
                "root": "",
                "scratch_dir": "/tmp/g16scratch",
                "use_wrapper": False,
                "wrapper_path": "",
            },
            "orca": {
                "path": "",
                "ld_library_path": "",
                "mpi_bin_dir": "",
                "mpi_lib_dir": "",
                "version": "6.1.1",
                "versions": {
                    "5.0.4": {
                        "path": "",
                        "ld_library_path": "",
                        "mpi_bin_dir": "",
                        "mpi_lib_dir": "",
                    },
                    "6.1.1": {
                        "path": "",
                        "ld_library_path": "",
                        "mpi_bin_dir": "",
                        "mpi_lib_dir": "",
                    },
                },
            },
            "crest": {"path": ""},
            "xtb": {"path": "", "fallback_paths": []},
            "discovery": {"enabled": True, "fail_on_invalid_explicit": True},
        },
        "resources": {
            "mem": "32GB",
            "nproc": 16,
            "orca_maxcore_safety": 0.65,
            "orca_math_threads_per_rank": 1,
        },
        "thermo": {"temperature_k": 298.15, "pressure_atm": 1.0},
        "theory": {
            "s3_low_level": {
                "optimization": {
                    "engine": "orca",
                    "task": "opt",
                    "method": "B97-3c",
                    "basis": "",
                    "aux_basis": "",
                    "solvent": "acetone",
                    "solvent_model": "CPCM",
                    "route_minimum": "Opt",
                    "route_ts": "OptTS",
                    "ts_trust_radius": 0.15,
                    "route_extras": "",
                    "max_cycles": 60,
                    "max_cycles_intermediate": 60,
                    "max_cycles_ts": 60,
                    "timeout": 864000,
                    "warmup": {
                        "enabled": True,
                        "roles": ["intermediate", "ts"],
                        "convergence": "loose",
                        "max_cycles": {"intermediate": 40, "ts": 50},
                        "accept_partial_geometry": True,
                    },
                    "frequency": {
                        "enabled_for_minima": True,
                        "enabled_for_ts": True,
                        "task": "freq",
                        "imaginary_cutoff_cm1": -50.0,
                        "require_exactly_one": True,
                        "soft_mode_window_cm1": [-50.0, -10.0],
                    },
                },
                "single_point": {
                    "engine": "orca",
                    "task": "sp",
                    "method": "r2SCAN-3c",
                    "basis": "",
                    "aux_basis": "",
                    "solvent": "acetone",
                    "solvent_model": "CPCM",
                    "grid": "DefGrid3",
                    "timeout": 864000,
                },
            },
            "s4_high_precision": {
                "optimization": {
                    "engine": "orca",
                    "task": "opt",
                    "method": "M062X",
                    "basis": "def2-SVP",
                    "aux_basis": "def2/J",
                    "solvent": "acetone",
                    "solvent_model": "CPCM",
                    "route_minimum": "Opt",
                    "route_ts": "OptTS",
                    "route_extras": "",
                    "grid": "DefGrid3",
                    "scf": "TightSCF",
                    "max_cycles": 200,
                    "max_cycles_ts": 200,
                    "timeout": 864000,
                    "frequency": {
                        "enabled_for_minima": True,
                        "enabled_for_ts": True,
                        "task": "freq",
                        "imaginary_cutoff_cm1": -50.0,
                        "require_exactly_one": True,
                        "soft_mode_window_cm1": [-50.0, -10.0],
                    },
                },
                "single_point": {
                    "engine": "orca",
                    "task": "sp",
                    "method": "wB97M-V",
                    "basis": "def2-TZVPP",
                    "aux_basis": "def2/J",
                    "solvent": "acetone",
                    "solvent_model": "CPCM",
                    "timeout": 864000,
                },
            },
        },
        "refinement": {
            "engine_schema": "refinement_engine_v1",
            "common": {
                "workflow": {
                    "warmup": {
                        "enabled_roles": ["intermediate", "ts"],
                        "constraint_mode": "freeze_forming_bonds_at_input_distance",
                        "fallback_to_input_geometry": True,
                        "accept_partial_geometry": True,
                    },
                    "initial_hessian": {
                        "precursor": "model",
                        "product": "model",
                        "intermediate": "calculate",
                        "ts": "calculate",
                    },
                    "frequency_always_independent": True,
                    "continue_on_structure_failure": False,
                },
                "rescue": {
                    "live_monitor": {
                        "enabled": False,
                        "poll_seconds": 20,
                        "min_cycles": 12,
                        "window_cycles": 16,
                        "min_energy_reversals": 4,
                        "energy_change_floor_hartree": 1.0e-5,
                        "min_gradient_improvement_fraction": 0.15,
                        "step_tolerance_ratio": 4.0,
                        "min_excessive_steps": 6,
                    },
                    "ts_rescue": {"enabled": False},
                    "ts_nonconvergence_rescue": {"enabled": False},
                    "int_rescue": {"enabled": False},
                    "int_nonconvergence_rescue": {"enabled": False},
                    "matrix": {"enabled": True},
                    "methods": {
                        "fresh_hessian_restart": {"max_cycles": 30, "recalc_hessian": 5},
                        "fresh_hessian_mode_monitor": {"max_cycles": 30, "recalc_hessian": 5},
                        "ts_mode_directed": {
                            "trust": 0.15,
                            "max_cycles": 12,
                            "mode_min_overlap": 0.35,
                            "mode_min_overlap_margin": 0.08,
                        },
                        "mode_displacement": {
                            "max_cycles": 30,
                            "displacement_step_angstrom": 0.30,
                        },
                        "saddle_break": {
                            "max_cycles": 30,
                            "displacement_step_angstrom": 0.30,
                        },
                        "calcall_opt": {"max_cycles": 30, "recalc_hessian": 1},
                        "irc_midpoint_recovery": {
                            "max_cycles": 30,
                            "irc_max_iter": 5,
                            "irc_direction": "both",
                            "shoulder_energy_window_kcal_mol": 2.0,
                        },
                    },
                },
                "irc": {
                    "enabled": True,
                    "direction": "both",
                    "max_iter": 30,
                    "init_hessian": "read",
                },
                "identity": {
                    "use_topology": True,
                    "use_mapped_rmsd": True,
                    "use_reaction_progress": True,
                    "validate_all_roles": True,
                    "int_identity_v2": {
                        "classification_version": "int_identity_v2",
                        "imaginary_cutoff_cm1": -10.0,
                        "product_rmsd_collapse_ang": 0.30,
                        "formed_bond_max_ang": 1.75,
                        "dipolar_open_bond_min_ang": 2.40,
                        "dipolar_open_bond_max_ang": 4.60,
                        "ts_rmsd_merged_ang": 0.40,
                        "ts_energy_merged_kcal": 0.5,
                        "ts_energy_degenerate_kcal": 0.3,
                        "int_below_ts_min_kcal": 0.2,
                        "int_above_product_min_kcal": 2.0,
                        "above_ts_invalid_kcal": 0.5,
                        "product_like_energy_max_kcal": 2.0,
                    },
                },
                "thermochemistry": {
                    "temperature_k": 247.55,
                    "temperature_K": 247.55,
                    "standard_state": "1M",
                    "qrrho": True,
                    "ensemble_correction_source": "s1",
                },
                "output": {"manifest_schema": RPH_REFINEMENT_SCHEMA_VERSION},
            },
            "s3": {
                "fidelity": "low",
                "profile_id": RPH_PROFILE_IDS["s3"],
                "warmup": {"max_cycles": {"intermediate": 40, "ts": 50}},
                "geometry": {
                    "method": "B97-3c",
                    "basis": "",
                    "route_minimum": "Opt",
                    "route_ts": "OptTS",
                    "ts_trust_radius": 0.15,
                    "max_cycles_minimum": 60,
                    "max_cycles_intermediate": 60,
                    "max_cycles_ts": 60,
                    "timeout": 864000,
                },
                "frequency": {"method": "B97-3c", "basis": ""},
                "single_point": {
                    "method": "r2SCAN-3c",
                    "basis": "",
                    "grid": "DefGrid3",
                    "timeout": 864000,
                },
                "solvent": {"solvent": "acetone", "solvent_model": "CPCM"},
                "resources": {
                    "cores_per_worker": 16,
                    "memory_gb_per_worker": 32,
                    "max_workers": 1,
                },
            },
            "s4": {
                "fidelity": "high",
                "profile_id": RPH_PROFILE_IDS["s4"],
                "warmup": {"max_cycles": {"intermediate": 4, "ts": 6}},
                "geometry": {
                    "method": "M062X",
                    "basis": "def2-SVP",
                    "aux_basis": "def2/J",
                    "grid": "DefGrid3",
                    "scf": "TightSCF",
                    "route_minimum": "Opt",
                    "route_ts": "OptTS",
                    "max_cycles_minimum": 200,
                    "max_cycles_intermediate": 200,
                    "max_cycles_ts": 200,
                    "timeout": 864000,
                },
                "frequency": {"method": "M062X", "basis": "def2-SVP"},
                "single_point": {
                    "method": "wB97M-V",
                    "basis": "def2-TZVPP",
                    "aux_basis": "def2/J",
                    "timeout": 864000,
                },
                "solvent": {"solvent": "acetone", "solvent_model": "CPCM"},
                "resources": {
                    "cores_per_worker": 32,
                    "memory_gb_per_worker": 64,
                    "max_workers": 1,
                },
            },
        },
        "step1": {
            "protocol": "censo_lite",
            "allowed_protocols": ["censo_lite"],
            "censo_lite": {
                "crest": {
                    "engine": "crest",
                    "gfn_level": 2,
                    "nproc": "auto",
                    "search_mode": "imtd_gc",
                    "energy_window_kcal": 6.0,
                    "solvent": "acetone",
                    "additional_flags": "",
                    "timeout": 21600,
                },
                "ranking": {
                    "engine": "orca",
                    "task": "sp",
                    "method": "B97-3c",
                    "basis": "",
                    "aux_basis": "",
                    "solvent": "acetone",
                    "solvent_model": "CPCM",
                    "route_extras": "",
                    "timeout": 864000,
                    "nproc": 1,
                    "maxcore": 2000,
                    "charge": 0,
                    "multiplicity": 1,
                },
                "b97_3c": {
                    "failure_policy": "strict",
                    "parallel_jobs": 16,
                    "cores_per_job": 1,
                    "max_parallel_jobs": 16,
                },
                "prefilter": {
                    "xtb_window_kcal": None,
                    "max_candidates_soft": None,
                    "extra_deduplication": True,
                },
                "xtb_thermo": {
                    "enabled": True,
                    "failure_policy": "defer",
                    "gfn_level": 2,
                    "temperature_k": 298.15,
                    "parallel_jobs": "auto",
                    "cores_per_job": 1,
                    "max_parallel_jobs": 16,
                },
                "selection": {
                    "final_window_kcal": 2.5,
                    "min_keep": 3,
                    "max_keep": 10,
                },
                "deduplication": {
                    "heavy_atom_rmsd_prefilter_A": 0.25,
                    "torsion_bin_deg": 20.0,
                    "torsion_rmsd_deg": 25.0,
                },
            },
            "fast_sp_profiles": {},
        },
        "step2": {
            "method": "orca_gfn2_relaxed_scan",
            "scan": {
                "topology_guard_enabled": True,
                "scan_start_distance": 3.4,
                "scan_end_distance": 1.2,
                "scan_steps": "auto",
                "coarse_step_A": 0.20,
                "scan_policy": "policy_c",
                "scan_force_constant": 0.05,
                "terminate_after_consecutive_off_path": 2,
                "reclassify_isolated_topology_suspects": True,
                "isolated_topology_max_energy_jump_kcal": 5.0,
                "isolated_topology_max_reaction_core_rmsd_A": 0.15,
                "anchor_detection": {
                    "persistent_drift_points": 2,
                    "plateau_onset": {
                        "slope_ratio_to_ts": 0.25,
                        "slope_change_ratio_to_ts": 0.15,
                        "absolute_slope_ceiling_kcal_mol_A": 12.0,
                        "minimum_consecutive_intervals": 2,
                    },
                },
                "endpoint_extension": {
                    "enabled": True,
                    "increment_A": 0.25,
                    "maximum_coordinate_A": 4.20,
                    "min_dissociation_side_span_A": 0.50,
                    "min_valid_post_ts_points": 5,
                    "max_extensions": 3,
                },
                "selection": {
                    "preferred_energy_source": "b973c",
                    "ts_min_prominence_kcal_mol": 0.40,
                    "valid_corridor_weak_peak_min_prominence_kcal_mol": 0.10,
                    "valid_corridor_weak_peak_min_barrier_kcal_mol": 3.0,
                    "ts_min_reactant_barrier_kcal_mol": 3.0,
                    "max_nonreactive_scaffold_rmsd_A": 0.75,
                    "minimum_clean_frames_after_knee": 2,
                    "require_intermediate": False,
                    "full_endpoint_min_clean_frames_from_boundary": 3,
                    "ts_min_clean_neighbors": 3,
                    "ts_seed_reactant_backoff_A": 0.20,
                    "int_min_basin_prominence_kcal_mol": 0.50,
                    "int_plateau_fallback_enabled": True,
                    "int_plateau_min_consecutive_frames": 3,
                    "int_plateau_min_ts_separation_A": 0.10,
                    "int_plateau_energy_window_kcal_mol": 2.0,
                    "int_plateau_barrier_fraction": 0.25,
                    "int_plateau_max_slope_kcal_mol_A": 40.0,
                    "endpoint_exclusion_frames": 2,
                    "min_reaction_progress": 0.10,
                    "min_valid_neighbor_window": 1,
                    "allow_monotonic_shoulder": True,
                    "shoulder_max_abs_slope_kcal_mol_per_A": 20.0,
                    "shoulder_min_curvature_signal": 0.05,
                    "allow_shared_search_seed": True,
                    "endpoint": {"guard_frames": 1, "min_valid_frames": 3},
                    "knee": {
                        "enabled": True,
                        "smoothing_window": 5,
                        "minimum_left_support_frames": 2,
                        "minimum_right_support_frames": 2,
                        "minimum_curvature_signal": 0.05,
                        "minimum_slope_change_kcal_mol_per_A": 0.0,
                    },
                    "ts_seed": {
                        "right_shift": {
                            "enabled": True,
                            "base_A": 0.15,
                            "span_fraction": 0.10,
                            "min_A": 0.05,
                            "max_A": 0.40,
                            "override_A": None,
                        }
                    },
                    "int_seed": {"mode": "ts_to_effective_endpoint_midpoint"},
                    "int_plateau": {
                        "min_consecutive_frames": 3,
                        "energy_window_kcal_mol": 2.0,
                        "min_ts_separation_A": 0.10,
                        "max_slope_kcal_mol_A": 40.0,
                    },
                    "admission": {
                        "require_barrier_for_search_seed": False,
                        "require_scaffold_for_search_seed": False,
                    },
                },
            },
            "orca_gfn2_scan": {
                "enabled": True,
                "relaxed_scan": {
                    "stretch_end_A": 3.40,
                    "points": 17,
                    "single_coordinate_use_scants": True,
                    "solvent": "acetone",
                    "solvent_model": "ALPB",
                    "nproc": 4,
                    "maxcore": 1600,
                },
            },
            "rescue": {
                "enabled": False,
                "relaxed_scan": {
                    "stretch_end_A": 3.40,
                    "points": 17,
                    "single_coordinate_use_scants": True,
                    "nproc": 4,
                    "maxcore": 1600,
                    "ts_min_prominence_kcal_mol": 0.40,
                    "ts_min_reactant_barrier_kcal_mol": 3.0,
                    "max_nonreactive_rmsd_A": 0.75,
                },
            },
            "scan_charge_spin": {"charge": 0, "multiplicity": 1},
            "energy_refinement": {
                "enabled": True,
                "engine": "orca",
                "task": "sp",
                "method": "B97-3c",
                "basis": "",
                "aux_basis": "",
                "solvent": "acetone",
                "solvent_model": "CPCM",
                "route_extras": "",
                "parallel_jobs": 4,
                "cores_per_job": 1,
                "memory_per_job": "2GB",
                "timeout": 864000,
            },
        },
        "step3": {
            "enabled": True,
            "continue_on_structure_failure": True,
            "scheduling": {
                "enabled": True,
                "max_workers": 1,
                "nproc_per_job": 16,
                "memory_per_job": "32GB",
                "priority_roles": ["ts", "intermediate", "precursor", "product"],
                "max_concurrent_by_role": {
                    "ts": 1,
                    "intermediate": 1,
                    "precursor": 2,
                    "product": 2,
                },
            },
        },
        "step4": {
            "enabled": True,
            "continue_on_structure_failure": True,
            "scheduling": {
                "enabled": True,
                "max_workers": 1,
                "nproc_per_job": 16,
                "memory_per_job": "32GB",
                "priority_roles": ["ts", "intermediate", "precursor", "product"],
                "max_concurrent_by_role": {
                    "ts": 1,
                    "intermediate": 1,
                    "precursor": 2,
                    "product": 2,
                },
            },
        },
        "ui": {
            "mode": "auto",
            "heartbeat_seconds": 30,
            "non_tty_heartbeat_seconds": 60,
            "top_population_count": 5,
        },
        "run": {
            "resume": True,
            "resume_policy": "strict",
            "checkpoint": {"enabled": True},
            "output_root": "./Output",
        },
    }


def _rph_section_overrides(config: Mapping[str, Any] | None) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    root = config or {}
    for key in _RPH_TOP_LEVEL_KEYS:
        value = root.get(key)
        if isinstance(value, Mapping):
            overrides[key] = copy.deepcopy(dict(value))
    for section in (
        _nested_config_value(root, "rph"),
        _nested_config_value(root, "mechanism", "rph"),
        _nested_config_value(root, "mechanism", "providers", "rph"),
        root.get("rph.config"),
        root.get("mechanism.rph.config"),
    ):
        mapping = _config_mapping(section)
        if not mapping:
            continue
        if "config" in mapping and isinstance(mapping["config"], Mapping):
            overrides = _deep_merge(overrides, cast(Mapping[str, Any], mapping["config"]))
            continue
        filtered = {
            key: copy.deepcopy(value)
            for key, value in mapping.items()
            if key not in {"path", "work_root", "config", "run_id"}
        }
        if filtered:
            overrides = _deep_merge(overrides, filtered)
    return overrides


def _normalize_fidelity_name(fidelity: AcpFidelityProfile | str | None) -> str | None:
    if fidelity is None:
        return None
    raw = fidelity.name if isinstance(fidelity, AcpFidelityProfile) else str(fidelity)
    normalized = raw.strip().lower()
    if normalized in RPH_PROFILE_IDS:
        return normalized
    for fidelity_name, profile_id in RPH_PROFILE_IDS.items():
        if raw == profile_id:
            return fidelity_name
    return normalized or None


def _apply_fidelity_overrides(
    config: dict[str, Any],
    fidelity: AcpFidelityProfile | str | None,
) -> dict[str, Any]:
    normalized = _normalize_fidelity_name(fidelity)
    if normalized not in RPH_PROFILE_IDS:
        return config
    if isinstance(fidelity, AcpFidelityProfile):
        stage_key = cast(str, normalized)
        refinement_block = cast(dict[str, Any], config["refinement"][stage_key])
        theory_key = "s3_low_level" if stage_key == "s3" else "s4_high_precision"
        theory_block = cast(dict[str, Any], config["theory"][theory_key])
        geometry_block = cast(dict[str, Any], refinement_block.setdefault("geometry", {}))
        frequency_block = cast(dict[str, Any], refinement_block.setdefault("frequency", {}))
        sp_block = cast(dict[str, Any], refinement_block.setdefault("single_point", {}))
        solvent_block = cast(dict[str, Any], refinement_block.setdefault("solvent", {}))
        geometry_block.update(
            {
                "method": fidelity.ts_method,
                "basis": fidelity.ts_basis,
                "ts_trust_radius": fidelity.ts_trust_radius,
            }
        )
        if fidelity.ts_grid is not None:
            geometry_block["grid"] = fidelity.ts_grid
        if fidelity.ts_scf is not None:
            geometry_block["scf"] = fidelity.ts_scf
        frequency_block.update({"method": fidelity.freq_method, "basis": fidelity.freq_basis})
        sp_block.update({"method": fidelity.sp_method, "basis": fidelity.sp_basis})
        if fidelity.sp_aux_j is not None:
            sp_block["aux_basis"] = fidelity.sp_aux_j
        solvent_block.update(
            {"solvent": fidelity.solvent or "acetone", "solvent_model": fidelity.solvent_model}
        )
        cast(dict[str, Any], theory_block.setdefault("optimization", {})).update(
            {
                "method": fidelity.ts_method,
                "basis": fidelity.ts_basis,
                "solvent": fidelity.solvent or "acetone",
                "solvent_model": fidelity.solvent_model,
            }
        )
        cast(dict[str, Any], theory_block.setdefault("single_point", {})).update(
            {
                "method": fidelity.sp_method,
                "basis": fidelity.sp_basis,
                "solvent": fidelity.solvent or "acetone",
                "solvent_model": fidelity.solvent_model,
            }
        )
    return config


def default_rph_config(
    config: Mapping[str, Any] | None = None,
    fidelity: AcpFidelityProfile | str | None = None,
) -> dict[str, Any]:
    """Return a self-contained RPH config dict with ACP-friendly defaults."""

    merged = _deep_merge(_base_rph_config(), _rph_section_overrides(config))
    merged = _apply_fidelity_overrides(merged, fidelity)
    merged["refinement"]["s3"]["profile_id"] = RPH_PROFILE_IDS["s3"]
    merged["refinement"]["s4"]["profile_id"] = RPH_PROFILE_IDS["s4"]
    return merged


def _fidelity_stage(fidelity: AcpFidelityProfile | str) -> str:
    normalized = _normalize_fidelity_name(fidelity)
    if normalized == "s3":
        return "S3"
    if normalized == "s4":
        return "S4"
    raise ValueError(f"Unsupported ACP fidelity for RPH adapter: {fidelity!r}")


def _fidelity_name(fidelity: AcpFidelityProfile | str) -> str:
    normalized = _normalize_fidelity_name(fidelity)
    if normalized in {"s3", "s4"}:
        return cast(str, normalized)
    raise ValueError(f"Unsupported ACP fidelity for RPH adapter: {fidelity!r}")


def _raw_coordinates_from_state(state: StableState) -> tuple[list[str], list[list[float]]]:
    if state.ensemble is not None:
        minimum = state.ensemble.global_minimum()
        if minimum is not None and minimum.coordinates is not None:
            coordinates = [[float(value) for value in row] for row in minimum.coordinates]
            return list(minimum.symbols), coordinates
    symbols = [str(symbol) for symbol in state.metadata.get("symbols") or []]
    coordinates_raw = state.metadata.get("coordinates") or []
    coordinates = [
        [float(value) for value in cast(Sequence[float], row)]
        for row in cast(Sequence[Sequence[float]], coordinates_raw)
    ]
    if symbols and coordinates:
        return symbols, coordinates
    raise ValueError(f"No resolvable geometry payload for stable state {state.state_id!r}")


def _write_xyz(path: Path, symbols: Sequence[str], coordinates: Sequence[Sequence[float]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [str(len(symbols)), path.stem]
    for symbol, xyz in zip(symbols, coordinates, strict=True):
        x, y, z = xyz
        lines.append(f"{symbol} {float(x):.10f} {float(y):.10f} {float(z):.10f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _read_xyz(path: Path) -> tuple[list[str], list[list[float]]]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError(f"XYZ file is too short: {path}")
    try:
        atom_count = int(lines[0])
    except ValueError as exc:
        raise ValueError(f"Invalid XYZ atom count in {path}") from exc
    atom_lines = lines[2 : 2 + atom_count]
    if len(atom_lines) != atom_count:
        raise ValueError(f"XYZ atom count mismatch in {path}")
    symbols: list[str] = []
    coordinates: list[list[float]] = []
    for line in atom_lines:
        parts = line.split()
        if len(parts) < 4:
            raise ValueError(f"Invalid XYZ atom line in {path}: {line!r}")
        symbols.append(parts[0])
        coordinates.append([float(parts[1]), float(parts[2]), float(parts[3])])
    return symbols, coordinates


def _resolve_geometry_file(
    artifact: ArtifactRef,
    fallback_symbols: Sequence[str] | None,
    fallback_coordinates: Sequence[Sequence[float]] | None,
    destination: Path,
) -> Path:
    source_path = Path(str(artifact.path))
    if source_path.is_file():
        return source_path
    if fallback_symbols is None or fallback_coordinates is None:
        raise FileNotFoundError(
            f"Geometry artifact is not a file and no fallback coordinates exist: {artifact.path}"
        )
    return _write_xyz(destination, fallback_symbols, fallback_coordinates)


def _artifact_ref(path: Path | str, kind: str) -> ArtifactRef:
    candidate = Path(str(path))
    if candidate.is_file():
        checksum = _file_sha256(candidate)
        resolved_path = str(candidate)
    else:
        resolved_path = str(path)
        checksum = _sha256_text(resolved_path)
    return ArtifactRef(path=resolved_path, sha256=checksum, kind=kind)


def _resolve_ensemble_input(stable_state: StableState) -> str:
    for key in ("smiles", "input_smiles", "input", "source_input", "structure_input"):
        value = stable_state.metadata.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return str(stable_state.canonical_geometry.path)


def _forming_bonds_from_plan(coordinate_plan: Any) -> list[tuple[int, int]]:
    bonds: list[tuple[int, int]] = []
    for spec in getattr(coordinate_plan, "coordinates", ()):
        if str(getattr(spec, "kind", "")).lower() != "distance":
            continue
        atoms = tuple(int(atom) for atom in getattr(spec, "atoms", ())[:2])
        if len(atoms) != 2:
            continue
        pair = (min(atoms), max(atoms))
        if pair not in bonds:
            bonds.append(pair)
    return bonds


def _request_seed_state(request: StationaryPointRequest) -> str:
    if request.kind == "ts" or request.role == "transition_state":
        return "ts_search_seed"
    return "stable_minimum_seed"


def _request_parent_structure(request: StationaryPointRequest) -> dict[str, Any] | None:
    if request.parent_state_id is None and request.route_id is None:
        return None
    return {
        "parent_state_id": request.parent_state_id,
        "route_id": request.route_id,
        "provenance": request.provenance.to_dict(),
    }


def _thermo_fields(
    correction: ThermoCorrection | None,
) -> tuple[str | None, dict[str, Any] | None, str | None, float | None]:
    if correction is None:
        return None, None, None, None
    metadata = dict(correction.metadata)
    manifest_ref = metadata.get("s1_manifest")
    thermo_payload = metadata.get("s1_ensemble_thermodynamics")
    if not isinstance(thermo_payload, Mapping):
        thermo_payload = correction.to_dict()
    status = str(metadata.get("s1_thermochemistry_status") or "available")
    return (
        str(manifest_ref) if manifest_ref is not None else None,
        dict(cast(Mapping[str, Any], thermo_payload)),
        status,
        correction.ensemble_delta_g_hartree,
    )


def build_rph_structure_request_payload(
    request: StationaryPointRequest,
    *,
    input_xyz: Path | str,
    fallback_xyz: Path | str | None = None,
    atom_mapping_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build the dict payload consumed by ``RefinementEngine.run``.

    Args:
        request: ACP stationary-point request.
        input_xyz: Materialized input geometry path.
        fallback_xyz: Optional fallback geometry path.
        atom_mapping_path: Optional materialized atom-mapping JSON path.

    Returns:
        A dict matching RPH's ``StructureRequest`` coercion contract.
    """

    role_map = {
        "reactant": "precursor",
        "product": "product",
        "intermediate": "intermediate",
        "transition_state": "ts",
    }
    s1_manifest, s1_thermo, s1_status, s1_correction = _thermo_fields(request.ensemble_correction)
    return {
        "id": request.id,
        "role": role_map[request.role],
        "kind": request.kind,
        "input_xyz": str(input_xyz),
        "forming_bonds": [list(pair) for pair in _forming_bonds_from_plan(request.coordinate_plan)],
        "fallback_xyz": str(fallback_xyz) if fallback_xyz is not None else None,
        "original_seed_xyz": str(input_xyz),
        "source_stage": request.source_stage,
        "seed_state": _request_seed_state(request),
        "charge": request.charge,
        "multiplicity": request.multiplicity,
        "atom_mapping": str(atom_mapping_path) if atom_mapping_path is not None else None,
        "parent_structure": _request_parent_structure(request),
        "structure_id": request.id,
        "variant_id": request.route_id or request.parent_state_id or request.id,
        "branch_id": request.route_id,
        "pathway_id": request.route_id,
        "parent_structure_id": request.parent_state_id,
        "mapping_audit": request.provenance.input_signature,
        "mapping_required": request.atom_mapping is not None,
        "s1_manifest": s1_manifest,
        "s1_ensemble_thermodynamics": s1_thermo,
        "s1_thermochemistry_status": s1_status,
        "ensemble_thermochemistry_correction_hartree": s1_correction,
    }


def _manifest_file_hash(manifest_path: Path, payload: Mapping[str, Any]) -> str:
    return _file_sha256(manifest_path) if manifest_path.is_file() else _json_hash(dict(payload))


def _stationary_role(raw_role: str, raw_kind: str) -> str:
    normalized_role = str(raw_role).strip().lower()
    normalized_kind = str(raw_kind).strip().lower()
    if normalized_role == "precursor":
        return "reactant"
    if normalized_role == "ts" or normalized_kind == "ts":
        return "transition_state"
    if normalized_role in {"product", "intermediate"}:
        return normalized_role
    return "intermediate"


def _stationary_role_literal(
    raw_role: str,
    raw_kind: str,
) -> Literal["reactant", "product", "intermediate", "transition_state"]:
    return cast(
        Literal["reactant", "product", "intermediate", "transition_state"],
        _stationary_role(raw_role, raw_kind),
    )


def _stationary_kind_literal(raw_kind: str) -> Literal["minimum", "ts"]:
    return "ts" if raw_kind == "ts" else "minimum"


def _ts_identity_from_payload(structure_payload: Mapping[str, Any]) -> TsIdentity | None:
    raw_kind = str(structure_payload.get("kind") or "")
    raw_role = str(structure_payload.get("role") or "")
    if raw_kind != "ts" and raw_role != "ts":
        return None
    frequencies = list(structure_payload.get("canonical_imaginary_frequencies_cm1") or [])
    if not frequencies:
        frequencies = list(structure_payload.get("imaginary_frequencies_cm1") or [])
    ts_classification = dict(structure_payload.get("ts_classification") or {})
    stationary_class = str(ts_classification.get("stationary_point_class") or "")
    valid = stationary_class in {"valid_target_ts", "soft_target_ts"}
    return TsIdentity(
        imaginary_count=len(frequencies),
        imaginary_frequency_cm1=_opt_float(frequencies[0]) if frequencies else None,
        mode_match_score=_first_float(
            ts_classification,
            "mode_match_score",
            "mode_alignment_score",
        ),
        topology_sane=valid,
        valid=valid,
        messages=[stationary_class] if stationary_class else [],
    )


def _stationary_point_from_structure_payload(
    structure_payload: Mapping[str, Any],
    *,
    profile_id: str,
    provider_version: str,
    manifest_path: Path,
    input_signature: str,
) -> StationaryPoint | None:
    canonical_xyz = structure_payload.get("canonical_xyz")
    if canonical_xyz is None:
        return None
    geometry_artifact = _artifact_ref(Path(str(canonical_xyz)), "stationary_point_geometry")
    raw_role = str(structure_payload.get("role") or "intermediate")
    raw_kind = str(structure_payload.get("kind") or "minimum")
    identity = _ts_identity_from_payload(structure_payload)
    validation = (
        TsValidation(
            identities=[identity],
            selected_candidate_id=str(structure_payload.get("id") or ""),
        )
        if identity is not None
        else None
    )
    artifacts = [geometry_artifact]
    for key, kind in (
        ("opt_output", "refinement_opt_output"),
        ("canonical_frequency_output", "refinement_freq_output"),
        ("sp_output", "refinement_sp_output"),
    ):
        value = structure_payload.get(key)
        if value:
            artifacts.append(_artifact_ref(Path(str(value)), kind))
    return StationaryPoint(
        point_id=str(structure_payload.get("id") or structure_payload.get("structure_id") or ""),
        role=_stationary_role_literal(raw_role, raw_kind),
        kind=_stationary_kind_literal(raw_kind),
        geometry=geometry_artifact,
        charge=int(structure_payload.get("charge") or 0),
        multiplicity=int(structure_payload.get("multiplicity") or 1),
        state_id=str(structure_payload.get("parent_structure_id") or "") or None,
        route_id=(
            str(structure_payload.get("pathway_id") or structure_payload.get("branch_id") or "")
            or None
        ),
        energy_hartree=_first_float(
            structure_payload,
            "sp_energy_hartree",
            "canonical_frequency_energy_hartree",
            "opt_energy_hartree",
        ),
        identity=identity,
        validation=validation,
        provenance=Provenance(
            provider=RPH_PROVIDER_NAME,
            provider_version=provider_version,
            provider_commit=RPH_PROVIDER_COMMIT,
            strategy=f"rph-{profile_id}",
            strategy_version=provider_version,
            profile_id=profile_id,
            schema_version=str(
                structure_payload.get("schema_version") or RPH_REFINEMENT_SCHEMA_VERSION
            ),
            input_signature=input_signature,
        ),
        artifacts=artifacts,
        metadata={
            "status": structure_payload.get("status"),
            "thermochemistry": copy.deepcopy(structure_payload.get("thermochemistry") or {}),
            "manifest_path": str(manifest_path),
            "canonical_frequency_status": structure_payload.get("canonical_frequency_status"),
            "sp_status": structure_payload.get("sp_status"),
        },
    )


def convert_rph_refinement_manifest(
    manifest_payload: Mapping[str, Any],
    *,
    manifest_path: Path,
    fidelity: AcpFidelityProfile | str,
    provider_version: str,
    input_signature: str | None = None,
) -> RefinementManifest:
    """Convert an RPH ``refinement_manifest_v1`` payload into ACP form."""

    fidelity_name = _fidelity_name(fidelity)
    profile_id = rph_profile_id(fidelity_name)
    signature = input_signature or _json_hash(dict(manifest_payload))
    structures_payload = [
        dict(row)
        for row in cast(
            Sequence[Mapping[str, Any]],
            manifest_payload.get("structures") or [],
        )
    ]
    attempts: list[RefinementAttempt] = []
    successful_points: list[StationaryPoint] = []
    structure_summaries: list[dict[str, Any]] = []
    for structure_payload in structures_payload:
        stationary_point = _stationary_point_from_structure_payload(
            structure_payload,
            profile_id=profile_id,
            provider_version=provider_version,
            manifest_path=manifest_path,
            input_signature=signature,
        )
        success = (
            stationary_point is not None
            and str(structure_payload.get("status") or "").lower() == "complete"
        )
        if stationary_point is not None:
            successful_points.append(stationary_point)
        attempts.append(
            RefinementAttempt(
                request_id=str(
                    structure_payload.get("id") or structure_payload.get("structure_id") or ""
                ),
                status="success" if success else "failed",
                stationary_point=stationary_point,
                evidence={
                    "status": structure_payload.get("status"),
                    "sp_status": structure_payload.get("sp_status"),
                    "canonical_frequency_status": structure_payload.get(
                        "canonical_frequency_status"
                    ),
                },
            )
        )
        structure_summaries.append(
            {
                "id": str(
                    structure_payload.get("id") or structure_payload.get("structure_id") or ""
                ),
                "status": structure_payload.get("status"),
                "canonical_xyz": structure_payload.get("canonical_xyz"),
                "sp_energy_hartree": structure_payload.get("sp_energy_hartree"),
                "thermochemistry": copy.deepcopy(structure_payload.get("thermochemistry") or {}),
            }
        )
    canonical_winner = next(
        (point for point in successful_points if point.kind == "ts"),
        successful_points[0] if successful_points else None,
    )
    manifest_id = str(
        manifest_payload.get("run_id") or manifest_path.parent.name or manifest_path.stem
    )
    return RefinementManifest(
        manifest_id=manifest_id,
        canonical_winner=canonical_winner,
        attempts=attempts,
        manifest_hash=_manifest_file_hash(manifest_path, manifest_payload),
        fidelity=fidelity_name,
        metadata={
            "stage": manifest_payload.get("stage"),
            "rph_fidelity": manifest_payload.get("fidelity"),
            "profile_id": manifest_payload.get("profile_id"),
            "schema_version": manifest_payload.get("schema_version"),
            "run_id": manifest_payload.get("run_id"),
            "summary": copy.deepcopy(manifest_payload.get("summary") or {}),
            "provenance": copy.deepcopy(manifest_payload.get("provenance") or {}),
            "structures": structure_summaries,
            "manifest_path": str(manifest_path),
        },
    )


def _point_id_by_frame(payload: Mapping[str, Any]) -> dict[int, str]:
    composite_profile = _config_mapping(payload.get("composite_profile"))
    composite_points = cast(Sequence[Mapping[str, Any]], composite_profile.get("points") or [])
    mapping: dict[int, str] = {}
    for index, point in enumerate(composite_points):
        mapping[index] = str(point.get("point_id") or f"p{index:03d}")
    frames = cast(Sequence[Any], payload.get("frames") or [])
    for index in range(len(frames)):
        mapping.setdefault(index, f"p{index:03d}")
    return mapping


def seed_selection_to_seed_candidates(
    selection: Any,
    *,
    point_ids_by_frame: Mapping[int, str],
    selection_mode: str = RPH_SELECTION_ALGORITHM,
) -> list[SeedCandidate]:
    """Convert one RPH ``SeedSelection`` into ACP ``SeedCandidate`` rows."""

    candidates: list[SeedCandidate] = []
    ts_seed = cast(Mapping[str, Any] | None, getattr(selection, "ts_search_seed", None))
    int_seed = cast(Mapping[str, Any] | None, getattr(selection, "int_search_seed", None))
    if ts_seed is not None:
        frame_index = int(ts_seed.get("frame_index") or 0)
        point_id = point_ids_by_frame.get(frame_index, f"p{frame_index:03d}")
        candidates.append(
            SeedCandidate(
                id=f"ts_seed_{point_id}",
                kind="ts_seed",
                geometry=_artifact_ref(Path(str(ts_seed.get("xyz"))), "ts_seed_geometry"),
                rank=1,
                selection_mode=selection_mode,
                confidence=str(ts_seed.get("confidence") or "medium"),
                evidence={
                    "frame_index": frame_index,
                    "point_id": point_id,
                    "seed": dict(ts_seed),
                    "seed_evidence": getattr(selection, "seed_evidence", None),
                },
                stationary_point_claimed=False,
            )
        )
    if int_seed is not None:
        frame_index = int(int_seed.get("frame_index") or 0)
        point_id = point_ids_by_frame.get(frame_index, f"p{frame_index:03d}")
        candidates.append(
            SeedCandidate(
                id=f"int_seed_{point_id}",
                kind="intermediate_seed",
                geometry=_artifact_ref(
                    Path(str(int_seed.get("xyz"))),
                    "intermediate_seed_geometry",
                ),
                rank=1,
                selection_mode=selection_mode,
                confidence="medium" if not bool(int_seed.get("shared_with_ts")) else "low",
                evidence={
                    "frame_index": frame_index,
                    "point_id": point_id,
                    "seed": dict(int_seed),
                    "seed_evidence": getattr(selection, "seed_evidence", None),
                    "shared_with_ts": bool(int_seed.get("shared_with_ts", False)),
                    "has_independent_int": bool(getattr(selection, "has_independent_int", False)),
                },
                stationary_point_claimed=False,
            )
        )
    return candidates


def _selection_to_path_candidates(
    selection: Any,
    *,
    point_ids_by_frame: Mapping[int, str],
    progress_by_frame: Mapping[int, float],
) -> list[PathCandidate]:
    candidates: list[PathCandidate] = []
    ts_seed = cast(Mapping[str, Any] | None, getattr(selection, "ts_search_seed", None))
    if ts_seed is not None:
        frame_index = int(ts_seed.get("frame_index") or 0)
        point_id = point_ids_by_frame.get(frame_index, f"p{frame_index:03d}")
        candidates.append(
            PathCandidate(
                candidate_id=f"ts_candidate_{point_id}",
                kind="ts_seed",
                point_id=point_id,
                reason=RPH_SELECTION_ALGORITHM,
                progress=float(progress_by_frame.get(frame_index, 0.0)),
                score=None,
            )
        )
    int_seed = cast(Mapping[str, Any] | None, getattr(selection, "int_search_seed", None))
    if int_seed is not None:
        frame_index = int(int_seed.get("frame_index") or 0)
        point_id = point_ids_by_frame.get(frame_index, f"p{frame_index:03d}")
        candidates.append(
            PathCandidate(
                candidate_id=f"int_candidate_{point_id}",
                kind="intermediate_seed",
                point_id=point_id,
                reason=RPH_SELECTION_ALGORITHM,
                progress=float(progress_by_frame.get(frame_index, 0.0)),
                score=None,
            )
        )
    return candidates


def _build_path_profile_from_payload(
    payload: Mapping[str, Any],
    *,
    product_xyz: Path,
    forming_bonds: Sequence[tuple[int, int]],
    path_profile_module: Any,
) -> Any:
    frames = [Path(str(frame)) for frame in cast(Sequence[Any], payload.get("frames") or [])]
    if not frames:
        raise ValueError("RPH S2 payload does not contain frame paths")
    selection_source = str(payload.get("selection_source") or "orca_relaxed_scan")
    energy_curves = _config_mapping(payload.get("energy_curves"))
    trajectory_quality = _config_mapping(payload.get("trajectory_quality"))
    source_provenance = {
        "selection_source": selection_source,
        "scan_profile_schema": payload.get("profile_schema_version"),
        "generation_method": payload.get("generation_method"),
    }
    if selection_source == "orca_relaxed_scan":
        b973c_curve = _config_mapping(energy_curves.get("b973c"))
        energies = cast(
            Sequence[Any],
            b973c_curve.get("energies_hartree") or payload.get("energies_hartree") or [],
        )
        return path_profile_module.build_orca_scan_profile(
            frames=frames,
            energies_hartree=energies,
            forming_bonds=forming_bonds,
            product_xyz=product_xyz,
            energy_source=str(b973c_curve.get("method") or "B97-3c"),
            source_provenance=source_provenance,
        )
    xtb_curve = _config_mapping(energy_curves.get("xtb") or energy_curves.get("gfn2"))
    energies = cast(
        Sequence[Any],
        xtb_curve.get("energies_hartree")
        or payload.get("xtb_energies_hartree")
        or payload.get("energies_hartree")
        or [],
    )
    return path_profile_module.build_xtb_path_profile(
        frame_paths=frames,
        energies_hartree=energies,
        forming_bonds=forming_bonds,
        product_xyz=product_xyz,
        off_path_indices=cast(
            Sequence[int],
            trajectory_quality.get("excluded_frames")
            or trajectory_quality.get("off_path_indices")
            or [],
        ),
        source_provenance=source_provenance,
    )


def _coordinate_labels(coordinate_plan: Any, frame: Any) -> list[str]:
    coords = list(getattr(frame, "reaction_coordinates", ()) or ())
    plan_coordinates = list(getattr(coordinate_plan, "coordinates", ()) or ())
    if len(plan_coordinates) == len(coords) and plan_coordinates:
        return [
            str(getattr(spec, "id", f"rc{index + 1}"))
            for index, spec in enumerate(plan_coordinates)
        ]
    return [f"rc{index + 1}" for index in range(len(coords))]


def _energies_for_frame(
    payload: Mapping[str, Any],
    frame_index: int,
    fallback_energy: float | None,
) -> dict[str, float | None]:
    energy_curves = _config_mapping(payload.get("energy_curves"))
    energies: dict[str, float | None] = {}
    for key in ("xtb", "gfn2", "b973c"):
        curve = _config_mapping(energy_curves.get(key))
        values = cast(Sequence[Any], curve.get("energies_hartree") or [])
        if frame_index < len(values):
            energies["b97-3c" if key == "b973c" else key] = _opt_float(values[frame_index])
    if fallback_energy is not None and "b97-3c" not in energies:
        energies["b97-3c"] = fallback_energy
    return energies


def _path_points_from_profile(
    payload: Mapping[str, Any],
    path_profile: Any,
    *,
    coordinate_plan: Any,
    provenance: Provenance,
) -> list[PathPoint]:
    point_ids = _point_id_by_frame(payload)
    points: list[PathPoint] = []
    for frame in cast(Sequence[Any], getattr(path_profile, "frames", ())):
        labels = _coordinate_labels(coordinate_plan, frame)
        coordinate_values = {
            label: float(value)
            for label, value in zip(labels, getattr(frame, "reaction_coordinates", ()), strict=True)
        }
        _, coordinates = _read_xyz(Path(str(frame.xyz)))
        frame_index = int(frame.frame_index)
        points.append(
            PathPoint(
                point_id=point_ids.get(frame_index, f"p{frame_index:03d}"),
                progress=float(frame.progress),
                coordinate_values=coordinate_values,
                reaction_coordinates=coordinate_values,
                geometry=np.asarray(coordinates, dtype=float),
                energies_hartree=_energies_for_frame(
                    payload,
                    frame_index,
                    _opt_float(frame.energy_hartree),
                ),
                topology={"valid": bool(frame.topology_valid), "reason": frame.topology_reason},
                frame_index=frame_index,
                arc_length=float(frame.progress),
                topology_valid=bool(frame.topology_valid),
                diagnostics={
                    "rmsd_to_product": frame.rmsd_to_product,
                    "neighbor_rmsd": frame.neighbor_rmsd,
                    "gradient_proxy": frame.gradient_proxy,
                    "curvature_proxy": frame.curvature_proxy,
                    "source": frame.source,
                },
                provenance=provenance,
            )
        )
    return points


def _rph_g2_policy(
    *,
    path_profile: Any,
    payload: Mapping[str, Any],
    selection: Any,
) -> dict[str, Any]:
    b973c_curve = _config_mapping(_config_mapping(payload.get("energy_curves")).get("b973c"))
    b973c_values = cast(Sequence[Any], b973c_curve.get("energies_hartree") or [])
    b973c_full_coverage = bool(b973c_values) and all(
        _opt_float(value) is not None for value in b973c_values
    )
    endpoint_evidence = cast(
        Mapping[str, Any] | None,
        getattr(selection, "endpoint_evidence", None),
    )
    knee_evidence = cast(
        Mapping[str, Any] | None,
        getattr(selection, "knee_evidence", None),
    )
    return {
        "require_profile_complete": True,
        "require_b97_full_coverage": True,
        "require_valid_corridor": bool(getattr(path_profile, "topology_valid_intervals", ())),
        "require_effective_endpoint": bool(
            endpoint_evidence and endpoint_evidence.get("effective_endpoint_index") is not None
        ),
        "require_knee": bool(knee_evidence and knee_evidence.get("frame_index") is not None),
        "require_ts_seed": getattr(selection, "ts_search_seed", None) is not None,
        "profile_complete": bool(getattr(path_profile, "complete", False)),
        "b97_full_coverage": b973c_full_coverage,
        "selector": RPH_SELECTION_ALGORITHM,
    }


def _request_fingerprint(requests: Sequence[StationaryPointRequest], fidelity: str) -> str:
    payload = {
        "fidelity": fidelity,
        "requests": [request.to_dict() for request in requests],
    }
    return _json_hash(payload)


class _RPHAdapterBase:
    """Shared lazy-import/runtime helpers for RPH-backed providers."""

    def __init__(
        self,
        *,
        rph_path: Path | str | None = None,
        config: Mapping[str, Any] | None = None,
        work_root: Path | str | None = None,
        event_callback: Any = None,
        run_id: str | None = None,
    ) -> None:
        self.adapter_config = copy.deepcopy(dict(config or {}))
        self.rph_repo_path = resolve_rph_repo_path(rph_path, self.adapter_config)
        _insert_repo_on_sys_path(self.rph_repo_path)
        self.work_root = _normalize_repo_path(work_root or DEFAULT_RPH_WORK_ROOT)
        self.event_callback = event_callback
        self.run_id = run_id

    def _import(self, module_name: str) -> Any:
        return _import_rph_module(module_name, self.rph_repo_path)

    def _rph_config(self, fidelity: AcpFidelityProfile | str | None = None) -> dict[str, Any]:
        return default_rph_config(self.adapter_config, fidelity)

    def _rph_version(self) -> str:
        return rph_version(self.rph_repo_path)


class RPHEnsembleProvider(_RPHAdapterBase, EnsembleProvider):
    """S1 ensemble provider backed by ``CensoLiteEngine``."""

    def generate(self, stable_state: StableState, profile: Any) -> StructureEnsemble:
        censo_module = self._import("rph_core.steps.conformer_search.censo_lite")
        mode = str(profile or RPH_CENSO_LITE_MODE)
        if mode not in {RPH_CENSO_LITE_MODE, XTB_FAST_MODE}:
            logger.debug(
                "RPHEnsembleProvider received unsupported S1 mode %r; using censo-lite",
                mode,
            )
        input_value = _resolve_ensemble_input(stable_state)
        provider_version = self._rph_version()
        input_signature = _sha256_text(input_value)
        run_dir = self.work_root / "s1" / stable_state.state_id
        run_dir.mkdir(parents=True, exist_ok=True)
        engine = censo_module.CensoLiteEngine(
            copy.deepcopy(self._rph_config()),
            run_dir,
            stable_state.state_id,
            event_callback=self.event_callback,
            run_id=self.run_id,
        )
        result = engine.run(str(input_value))
        manifest_path = Path(str(result.get("manifest")))
        manifest_payload = dict(
            result.get("data") or json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        base_dir = manifest_path.parent
        provenance = Provenance(
            provider=RPH_PROVIDER_NAME,
            provider_version=provider_version,
            provider_commit=RPH_PROVIDER_COMMIT,
            strategy=RPH_CENSO_LITE_MODE,
            strategy_version=provider_version,
            profile_id=RPH_CENSO_LITE_MODE,
            schema_version=str(
                manifest_payload.get("schema_version") or "s1_censo_light_ranking_v4"
            ),
            input_signature=input_signature,
        )
        records: list[StructureRecord] = []
        for rank, candidate in enumerate(
            cast(
                Sequence[Mapping[str, Any]],
                manifest_payload.get("candidates") or [],
            ),
            start=1,
        ):
            xyz_ref = candidate.get("xyz")
            if xyz_ref is None:
                continue
            xyz_path = Path(str(xyz_ref))
            if not xyz_path.is_absolute():
                xyz_path = base_dir / xyz_path
            symbols, coordinates = _read_xyz(xyz_path)
            candidate_id = str(candidate.get("id") or f"{stable_state.state_id}_conf_{rank:04d}")
            structure = Structure(
                id=candidate_id,
                charge=stable_state.charge,
                multiplicity=stable_state.multiplicity,
                symbols=symbols,
                coordinates=coordinates,
                metadata={
                    "state_id": stable_state.state_id,
                    "candidate_id": candidate_id,
                    "provider": RPH_PROVIDER_NAME,
                },
            )
            weight = _first_float(candidate, "boltzmann_population", "population", "weight")
            records.append(
                StructureRecord(
                    structure=structure,
                    energy_hartree=_first_float(
                        candidate,
                        "electronic_energy_hartree",
                        "energy_hartree",
                        "s1_score_hartree",
                    ),
                    free_energy_hartree=_first_float(
                        candidate,
                        "gibbs_free_energy_hartree",
                        "free_energy_hartree",
                        "s1_score_hartree",
                    ),
                    weight=weight,
                    properties={
                        "rank": rank,
                        "candidate_id": candidate_id,
                        "boltzmann_population": weight,
                        "degeneracy": candidate.get("degeneracy"),
                        "relative_energy_kcal": candidate.get("relative_energy_kcal"),
                        "relative_free_energy_kcal": candidate.get("relative_free_energy_kcal"),
                        "xtb_mrrho_thermal_correction_hartree": candidate.get(
                            "xtb_mrrho_thermal_correction_hartree"
                        ),
                        "provenance": provenance.to_dict(),
                    },
                    files={"source": xyz_path},
                )
            )
        return StructureEnsemble(
            records=records,
            metadata={
                "provider": RPH_PROVIDER_NAME,
                "requested_profile": mode,
                "profile_id": RPH_CENSO_LITE_MODE,
                "manifest_path": str(manifest_path),
                "selected_id": manifest_payload.get("selected"),
                "selected_xyz": result.get("selected_xyz"),
                "ensemble_thermodynamics": copy.deepcopy(
                    manifest_payload.get("ensemble_thermodynamics") or {}
                ),
                "provenance": provenance.to_dict(),
            },
        )


class RPHPathSearchStrategy(_RPHAdapterBase, PathSearchStrategy):
    """S2 path-search strategy backed by the canonical RPH reverse PEB stack."""

    def search(
        self,
        source_state: StableState,
        target_state: StableState | None,
        coordinate_plan: Any,
        profile: Any,
    ) -> PathResult:
        if target_state is None:
            raise ValueError("RPH reverse path search requires a target/product state")
        peb_module = self._import("rph_core.steps.step2_retro.peb_scanner")
        path_selector_module = self._import("rph_core.steps.step2_retro.path_selector")
        path_profile_module = self._import("rph_core.steps.step2_retro.path_profile")
        provider_version = self._rph_version()
        forming_bonds = _forming_bonds_from_plan(coordinate_plan)
        if not forming_bonds:
            raise ValueError("RPH reverse path search requires at least one distance coordinate")
        run_dir = self.work_root / "s2" / f"{source_state.state_id}__{target_state.state_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        target_symbols, target_coordinates = _raw_coordinates_from_state(target_state)
        product_xyz = _resolve_geometry_file(
            target_state.canonical_geometry,
            target_symbols,
            target_coordinates,
            run_dir / "product.xyz",
        )
        scanner = peb_module.PEBScanner(
            copy.deepcopy(self._rph_config()),
            molecule_name=f"{source_state.state_id}__{target_state.state_id}",
            event_callback=self.event_callback,
        )
        raw_result = scanner.run(product_xyz, run_dir, forming_bonds)
        profile_path = Path(str(raw_result[4]))
        payload = dict(
            getattr(scanner, "last_profile_payload", None)
            or json.loads(profile_path.read_text(encoding="utf-8"))
        )
        path_profile = _build_path_profile_from_payload(
            payload,
            product_xyz=product_xyz,
            forming_bonds=forming_bonds,
            path_profile_module=path_profile_module,
        )
        selection_policy = path_selector_module.policy_from_config(
            _nested_config_value(self._rph_config(), "step2", "scan", "selection") or {},
            _nested_config_value(self._rph_config(), "step2", "rescue", "relaxed_scan") or {},
        )
        selection = path_selector_module.select_path_seeds(path_profile, selection_policy)
        provenance = Provenance(
            provider=RPH_PROVIDER_NAME,
            provider_version=provider_version,
            provider_commit=RPH_PROVIDER_COMMIT,
            strategy=RPH_REVERSE_STRATEGY_ID,
            strategy_version=provider_version,
            profile_id=str(profile or RPH_REVERSE_STRATEGY_ID),
            schema_version=str(payload.get("profile_schema_version") or RPH_ADAPTER_SCHEMA_VERSION),
            input_signature=_json_hash(
                {
                    "source_state": source_state.to_dict(),
                    "target_state": target_state.to_dict(),
                    "coordinate_plan": getattr(
                        coordinate_plan,
                        "to_dict",
                        lambda: str(coordinate_plan),
                    )(),
                }
            ),
        )
        points = _path_points_from_profile(
            payload,
            path_profile,
            coordinate_plan=coordinate_plan,
            provenance=provenance,
        )
        point_ids = {int(point.frame_index or 0): point.point_id for point in points}
        progress_by_frame = {int(point.frame_index or 0): float(point.progress) for point in points}
        seed_candidates = seed_selection_to_seed_candidates(selection, point_ids_by_frame=point_ids)
        path_candidates = _selection_to_path_candidates(
            selection,
            point_ids_by_frame=point_ids,
            progress_by_frame=progress_by_frame,
        )
        gate_policy = _rph_g2_policy(
            path_profile=path_profile,
            payload=payload,
            selection=selection,
        )
        geometry_sha256 = {
            point.point_id: _artifact_ref(
                Path(
                    str(
                        cast(
                            Sequence[Any],
                            payload.get("frames") or [],
                        )[int(point.frame_index or 0)]
                    )
                ),
                "path_frame_geometry",
            ).sha256
            for point in points
        }
        selected_ts_id = next(
            (
                candidate.candidate_id
                for candidate in path_candidates
                if candidate.kind == "ts_seed"
            ),
            None,
        )
        selected_int_id = next(
            (
                candidate.candidate_id
                for candidate in path_candidates
                if candidate.kind == "intermediate_seed"
            ),
            None,
        )
        return PathResult(
            points=points,
            candidates=path_candidates,
            strategy=RPH_REVERSE_STRATEGY_ID,
            route_id=str(
                source_state.metadata.get("route_id")
                or target_state.metadata.get("route_id")
                or "rph-route"
            ),
            selected_ts_id=selected_ts_id,
            selected_int_id=selected_int_id,
            metadata={
                "provider": RPH_PROVIDER_NAME,
                "selection_source": payload.get("selection_source"),
                "selection_diagnostics": copy.deepcopy(getattr(selection, "diagnostics", {})),
                "gate_policies": {"G2": gate_policy},
                "wiring": (
                    "Used rph_core.steps.step2_retro.PEBScanner.run for coarse "
                    "reverse PEB + PATH + xTB/B97 coverage, then rebuilt "
                    "PathProfile and replayed "
                    "rph_core.steps.step2_retro.path_selector.select_path_seeds "
                    "to preserve RPH selector semantics inside ACP."
                ),
            },
            seed_candidates=seed_candidates,
            strategy_id=RPH_REVERSE_STRATEGY_ID,
            strategy_version=provider_version,
            complete=bool(
                getattr(path_profile, "complete", False) and gate_policy["b97_full_coverage"]
            ),
            endpoint_evidence=dict(
                copy.deepcopy(
                    cast(Mapping[str, Any] | None, getattr(selection, "endpoint_evidence", None))
                    or {}
                )
            ),
            topology_segments=[
                {"start": int(start), "end": int(end), "valid": True}
                for start, end in cast(
                    Sequence[tuple[int, int]],
                    getattr(path_profile, "topology_valid_intervals", ()),
                )
            ],
            artifacts={
                "scan_profile": str(profile_path),
                "product_xyz": str(product_xyz),
                "geometry_sha256_by_point": geometry_sha256,
            },
        )


class RPHRefinementProvider(_RPHAdapterBase, RefinementProvider):
    """S3/S4 refinement provider backed by RPH's ``RefinementEngine``."""

    def __init__(
        self,
        *,
        rph_path: Path | str | None = None,
        config: Mapping[str, Any] | None = None,
        work_root: Path | str | None = None,
        event_callback: Any = None,
        run_id: str | None = None,
        resume_incomplete: bool = False,
        structure_ids: Sequence[str] | None = None,
        rescue_only: bool = False,
        parent_manifest_paths: Mapping[str, Path] | None = None,
    ) -> None:
        super().__init__(
            rph_path=rph_path,
            config=config,
            work_root=work_root,
            event_callback=event_callback,
            run_id=run_id,
        )
        self.resume_incomplete = resume_incomplete
        self.structure_ids = list(structure_ids) if structure_ids is not None else None
        self.rescue_only = rescue_only
        self.parent_manifest_paths = dict(parent_manifest_paths or {})

    def refine(
        self,
        requests: list[StationaryPointRequest],
        fidelity: AcpFidelityProfile | str,
    ) -> RefinementManifest:
        refinement_module = self._import("rph_core.steps.refinement")
        manifest_io_module = self._import("rph_core.steps.refinement.manifest_io")
        stage = _fidelity_stage(fidelity)
        fidelity_name = _fidelity_name(fidelity)
        config = self._rph_config(fidelity)
        profile = refinement_module.FidelityProfile.from_config(config, stage)
        engine = refinement_module.RefinementEngine(
            config=copy.deepcopy(config),
            profile=profile,
            run_id=self.run_id,
            parent_manifest_paths=self.parent_manifest_paths or None,
        )
        materialized_requests: list[dict[str, Any]] = []
        for request in requests:
            request_dir = self.work_root / stage.lower() / request.id
            request_dir.mkdir(parents=True, exist_ok=True)
            input_xyz = _resolve_geometry_file(
                request.input_geometry,
                None,
                None,
                request_dir / "input.xyz",
            )
            fallback_xyz: Path | None = None
            if request.fallback_geometries:
                fallback_xyz = _resolve_geometry_file(
                    request.fallback_geometries[0],
                    None,
                    None,
                    request_dir / "fallback.xyz",
                )
            atom_mapping_path: Path | None = None
            if request.atom_mapping is not None:
                atom_mapping_path = request_dir / "atom_mapping.json"
                atom_mapping_path.write_text(
                    json.dumps(request.atom_mapping.to_dict(), indent=2, sort_keys=True),
                    encoding="utf-8",
                )
            materialized_requests.append(
                build_rph_structure_request_payload(
                    request,
                    input_xyz=input_xyz,
                    fallback_xyz=fallback_xyz,
                    atom_mapping_path=atom_mapping_path,
                )
            )
        run_fingerprint = _request_fingerprint(requests, fidelity_name)
        output_dir = self.work_root / stage.lower() / run_fingerprint.replace(":", "_")
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = Path(
            str(
                engine.run(
                    materialized_requests,
                    output_dir,
                    event_callback=self.event_callback,
                    resume_incomplete=self.resume_incomplete,
                    structure_ids=self.structure_ids,
                    rescue_only=self.rescue_only,
                )
            )
        )
        manifest_payload = dict(manifest_io_module.read_refinement_manifest(manifest_path))
        return convert_rph_refinement_manifest(
            manifest_payload,
            manifest_path=manifest_path,
            fidelity=fidelity_name,
            provider_version=self._rph_version(),
            input_signature=run_fingerprint,
        )


__all__ = [
    "DEFAULT_RPH_REPO_PATH",
    "DEFAULT_RPH_WORK_ROOT",
    "RPHEnsembleProvider",
    "RPHPathSearchStrategy",
    "RPHRefinementProvider",
    "RPHUnavailableError",
    "build_rph_structure_request_payload",
    "convert_rph_refinement_manifest",
    "default_rph_config",
    "resolve_rph_repo_path",
    "rph_version",
    "seed_selection_to_seed_candidates",
]
