"""Helpers migrated from ``energy_shared.py`` (todo 48).

``v2_stage_dir``, ``resolve_crest_ewin``, ``resolve_levels``, and
``xtb_passthrough_result`` are the active helpers consumed by
``confsearch/protocols/xtb_md.py``.  Moved here verbatim so
``energy_shared.py`` can be deleted.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import numpy as np

from acp.storage.layout import TaskStorage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# v2 stage / result helpers
# ---------------------------------------------------------------------------


def v2_stage_dir(mol_dir: Path, stage: str, engine: str | None = None) -> Path:
    """Resolve (and create) ``WORK/<stage>/<engine>`` under *mol_dir* (v2 layout)."""
    storage = TaskStorage(mol_dir)
    path = storage.stage_dir(stage, engine)
    path.mkdir(parents=True, exist_ok=True)
    return path


def v2_result_category(mol_dir: Path, category: str) -> Path:
    """Resolve (and create) ``RESULT/<category>`` under *mol_dir* (v2 layout)."""
    storage = TaskStorage(mol_dir)
    path = storage.result_category_dir(category)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# CREST energy-window resolver
# ---------------------------------------------------------------------------


def resolve_crest_ewin(
    cfg: dict[str, Any],
    ewin: float | None,
) -> float:
    """Resolve the CREST energy window: explicit arg > censo.ewin > 6.0."""
    if ewin is not None and ewin > 0:
        return float(ewin)
    raw = cfg.get("censo", {}).get("ewin", 6.0)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 6.0
    return value if value > 0 else 6.0


# ---------------------------------------------------------------------------
# Levels resolution (dft_opt / refinement_sp / screening_sp / thermo)
# ---------------------------------------------------------------------------

_DEFAULT_OPT_FUNCTIONAL = "r2SCAN-3c"
_DEFAULT_SP_FUNCTIONAL = "wB97M-V"
_DEFAULT_SP_BASIS = "def2-TZVPP"

_GRID_ROUTE_MAP = {
    "sg1": "DEFGRID1",
    "ultrafine": "DEFGRID3",
    "superfine": "DEFGRID3",
}
_SCF_ROUTE_MAP = {
    "tight": "TightSCF",
    "verytight": "VeryTightSCF",
}
_OPT_CONV_ROUTE_MAP = {
    "loose": "LooseOpt",
    "tight": "TightOpt",
    "verytight": "VeryTightOpt",
}

_FLOAT_RE = re.compile(r"[-+]?\d+\.\d+")


def _base_route_extras(level: dict[str, Any]) -> list[str]:
    """Build ORCA route keywords from a level's advanced fields."""
    extras: list[str] = []
    dispersion = str(level.get("dispersion") or "").strip()
    if dispersion and dispersion.lower() != "none":
        extras.append(dispersion.upper())
    ri = str(level.get("ri_approximation") or "").strip()
    if ri and ri.lower() != "none":
        extras.append(ri.upper())
    aux_j_basis = str(level.get("aux_j_basis") or "").strip()
    if aux_j_basis:
        extras.append(aux_j_basis)
    aux_c_basis = str(level.get("aux_c_basis") or "").strip()
    if aux_c_basis:
        extras.append(aux_c_basis)
    grid_kw = _GRID_ROUTE_MAP.get(str(level.get("grid") or "").strip().lower())
    if grid_kw:
        extras.append(grid_kw)
    scf_kw = _SCF_ROUTE_MAP.get(str(level.get("scf_convergence") or "").strip().lower())
    if scf_kw:
        extras.append(scf_kw)
    return extras


def _solvent_from_level(level: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract an explicit per-level solvent override."""
    model = str(level.get("solvent_model") or "").strip().lower()
    solvent = str(level.get("solvent") or "").strip()
    if not solvent or model in ("", "none"):
        return None, None
    return solvent, model


def resolve_levels(
    cfg: dict[str, Any],
    levels: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge config defaults with user ``--levels`` overrides."""
    from acp.chem.composition import normalize_recalc_hess

    levels = levels or {}
    censo_cfg = cfg.get("censo", {})
    opt_cfg = censo_cfg.get("optimization", {})
    thermo_cfg = cfg.get("thermo", {})

    dft_opt = levels.get("dft_opt", {}) or {}
    refinement_sp = levels.get("refinement_sp", {}) or {}
    screening_sp = levels.get("screening_sp", {}) or {}
    thermo = levels.get("thermo", {}) or {}
    censo_level = levels.get("censo", {}) or {}

    opt_method = dft_opt.get("functional") or opt_cfg.get("functional", _DEFAULT_OPT_FUNCTIONAL)
    opt_basis = dft_opt.get("basis")

    try:
        opt_recalc_hess = normalize_recalc_hess(dft_opt.get("recalc_hess"))
    except ValueError as exc:
        raise ValueError(f"dft_opt.recalc_hess: {exc}") from exc

    sp_method = refinement_sp.get("functional") or censo_cfg.get(
        "refinement_func", _DEFAULT_SP_FUNCTIONAL
    )
    sp_basis = refinement_sp.get("basis") or censo_cfg.get("refinement_basis", _DEFAULT_SP_BASIS)

    opt_base_extras = _base_route_extras(dft_opt)
    opt_route_extras = list(opt_base_extras)
    opt_conv_kw = _OPT_CONV_ROUTE_MAP.get(str(dft_opt.get("opt_convergence") or "").strip().lower())
    if opt_conv_kw:
        opt_route_extras.append(opt_conv_kw)

    opt_geom_maxiter: int | None = None
    max_steps = dft_opt.get("max_steps")
    if isinstance(max_steps, (int, float)) and int(max_steps) > 0:
        opt_geom_maxiter = int(max_steps)

    sp_route_extras = _base_route_extras(refinement_sp)

    opt_solvent, opt_solvent_model = _solvent_from_level(dft_opt)
    sp_solvent, sp_solvent_model = _solvent_from_level(refinement_sp)

    screening_overrides: dict[str, Any] = {}
    if screening_sp.get("functional"):
        screening_overrides["func"] = str(screening_sp["functional"]).lower()
    if screening_sp.get("basis"):
        screening_overrides["basis"] = str(screening_sp["basis"]).lower()

    refinement_overrides: dict[str, Any] = {}
    if refinement_sp.get("functional"):
        refinement_overrides["func"] = str(refinement_sp["functional"]).lower()
    if refinement_sp.get("basis"):
        refinement_overrides["basis"] = str(refinement_sp["basis"]).lower()

    screening_extras = _base_route_extras(screening_sp)
    screening_template_lines = ["! " + " ".join(screening_extras)] if screening_extras else []
    refinement_template_lines = ["! " + " ".join(sp_route_extras)] if sp_route_extras else []

    levels_solvent = sp_solvent or opt_solvent
    levels_solvent_model = sp_solvent_model or opt_solvent_model

    try:
        refinement_threshold = float(
            levels.get(
                "refinement_threshold",
                censo_cfg.get("refinement_threshold", 0.99),
            )
        )
    except (TypeError, ValueError):
        refinement_threshold = 0.99
    if not 0.0 < refinement_threshold <= 1.0:
        logger.warning(
            "refinement_threshold %.4f outside (0, 1] — falling back to 0.99",
            refinement_threshold,
        )
        refinement_threshold = 0.99

    crest_ewin_level: float | None = None
    raw_ewin = censo_level.get("ewin")
    if raw_ewin is not None:
        try:
            candidate_ewin = float(raw_ewin)
        except (TypeError, ValueError):
            candidate_ewin = None
        if candidate_ewin is not None and candidate_ewin > 0:
            crest_ewin_level = candidate_ewin

    return {
        "opt_method": opt_method,
        "opt_basis": opt_basis,
        "opt_route_extras": opt_route_extras,
        "opt_freq_route_extras": opt_base_extras,
        "opt_geom_maxiter": opt_geom_maxiter,
        "opt_recalc_hess": opt_recalc_hess,
        "opt_solvent": opt_solvent,
        "opt_solvent_model": opt_solvent_model,
        "sp_method": sp_method,
        "sp_basis": sp_basis,
        "sp_route_extras": sp_route_extras,
        "sp_solvent": sp_solvent,
        "sp_solvent_model": sp_solvent_model,
        "screening_overrides": screening_overrides,
        "refinement_overrides": refinement_overrides,
        "screening_template_lines": screening_template_lines,
        "refinement_template_lines": refinement_template_lines,
        "levels_solvent": levels_solvent,
        "levels_solvent_model": levels_solvent_model,
        "refinement_threshold": refinement_threshold,
        "crest_ewin_level": crest_ewin_level,
        "temperature_k": thermo.get("temperature", thermo_cfg.get("temperature_k", 298.15)),
        "pressure_atm": thermo.get("pressure", thermo_cfg.get("pressure_atm", 1.0)),
        "scl_zpe": thermo.get("scale_factor", thermo_cfg.get("scl_zpe", 0.9905)),
        "ilowfreq": thermo_cfg.get("shermo_ilowfreq", 2),
        "imagreal": thermo_cfg.get("shermo_imagreal", 0),
        "conc": thermo_cfg.get("shermo_conc"),
    }


# ---------------------------------------------------------------------------
# Cumulative Boltzmann selection
# ---------------------------------------------------------------------------


def select_cumulative_boltzmann(
    records: list[Any],
    temperature_k: float,
    threshold: float,
) -> list[Any]:
    """Select the lowest-gtot conformers up to a cumulative Boltzmann cutoff.

    Records (``.gtot``-bearing, e.g. :class:`CensoConformerRecord`) are
    sorted by ascending ``gtot``; conformers are accumulated until their
    cumulative Boltzmann population reaches ``threshold`` (the conformer
    crossing the threshold is included). Rank1 is always selected.
    """
    from .boltzmann import boltzmann_weights as _bw

    if not records:
        return []
    ordered = sorted(records, key=lambda r: r.gtot)
    weights = _bw([r.gtot for r in ordered], temperature_k)
    selected: list[Any] = []
    cumulative = 0.0
    for rec, weight in zip(ordered, weights):
        selected.append(rec)
        cumulative += float(weight) if weight is not None else 0.0
        if cumulative >= threshold:
            break
    return selected


# ---------------------------------------------------------------------------
# xTB passthrough (censo-zero)
# ---------------------------------------------------------------------------


def xtb_passthrough_result(
    ensemble_xyz: Path,
    temperature: float,
) -> Any:
    """Build a CensoRunResult directly from an xTB-ranked ensemble (censo-zero).

    The censo-zero preset does not invoke CENSO: the ensemble is exported
    as-is, sorted by the xTB energies parsed from the frame title lines.
    """
    from acp.backends.censo_backend import CensoConformerRecord, CensoRunResult
    from cccp.utils.file_io import read_xyz_multiframe

    all_coords, symbols = read_xyz_multiframe(ensemble_xyz)
    n_atoms = len(symbols)
    if n_atoms == 0:
        raise ValueError(f"No atoms found in ensemble: {ensemble_xyz}")
    n_frames = len(all_coords) // n_atoms

    energies: list[float] = []
    with open(ensemble_xyz, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    idx = 0
    while idx < len(lines) and len(energies) < n_frames:
        try:
            frame_atoms = int(lines[idx].strip())
        except ValueError:
            break
        title = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
        match = _FLOAT_RE.search(title)
        energies.append(float(match.group()) if match else 0.0)
        idx += frame_atoms + 2

    if len(energies) < n_frames:
        logger.warning(
            "Parsed %d/%d title energies from %s — missing values set to 0.0",
            len(energies),
            n_frames,
            ensemble_xyz,
        )
        energies.extend([0.0] * (n_frames - len(energies)))

    records = []
    for i in range(n_frames):
        start = i * n_atoms
        records.append(
            CensoConformerRecord(
                conf_id=f"CONF{i + 1}",
                frame_index=i,
                energy=energies[i],
                gsolv=0.0,
                grrho=0.0,
                gtot=energies[i],
                coordinates=np.array(all_coords[start : start + n_atoms], dtype=float),
                symbols=list(symbols),
            )
        )

    result = CensoRunResult(
        preset="censo-zero",
        records=records,
        final_part="crest_passthrough",
        work_dir=ensemble_xyz.parent,
        temperature=temperature,
    )
    result.sort_by_gtot()
    return result


__all__ = [
    "resolve_crest_ewin",
    "resolve_levels",
    "v2_result_category",
    "v2_stage_dir",
    "xtb_passthrough_result",
]
