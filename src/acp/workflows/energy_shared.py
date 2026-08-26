"""Shared energy-workflow helpers (E4 extraction).

Public home of the helpers that the ``energy`` and ``xtbmd_censo_energy``
workflows share: levels resolution, the ACP standard DFT handoff, Boltzmann
selection, final-output writers, the censo-zero xTB passthrough, and the
solvent / CREST-ewin resolvers.  These were originally private functions in
``energy.py`` / ``ensemble.py``; they are imported from here by both
workflows (and kept as private-name aliases in the origin modules so existing
importers keep working, behaviour unchanged).

The module has no dependency on ``acp.workflows.energy`` or
``acp.workflows.ensemble`` (no import cycle): it only imports shared lower
layers (backends, core models, ensemble_thermo, cccp interfaces).
"""

from __future__ import annotations

import json
import logging
import re
from importlib import import_module
from pathlib import Path
from typing import Any

import numpy as np

from acp.backends.censo_backend import CensoConformerRecord, CensoRunResult
from acp.backends.registry import get_backend
from acp.chem.composition import normalize_recalc_hess
from acp.core.models import HARTREE_TO_KCAL, Structure, StructureEnsemble, StructureRecord
from acp.storage.layout import TaskStorage
from acp.storage.manifest import ResultManifest
from acp.workflows._helpers import write_result_summary
from acp.workflows.ensemble_thermo import (
    EnsembleThermoSummary,
    ensemble_total_gibbs,
    ensemble_total_gibbs_from_values,
    s_mix_cal_per_mol_kelvin,
    s_mix_kcal_per_mol_kelvin,
    t_s_mix_kcal_per_mol,
)
from cccp.qc.runners import run_shermo
from cccp.utils.file_io import read_xyz_multiframe, write_xyz

logger = logging.getLogger(__name__)

_K_B_HARTREE_PER_KELVIN = 3.166811563e-6

_DEFAULT_OPT_FUNCTIONAL = "r2SCAN-3c"
_DEFAULT_SP_FUNCTIONAL = "wB97M-V"
_DEFAULT_SP_BASIS = "def2-TZVPP"


def _handoff_stage_dir(conf_dir: Path, stage: str, engine: str) -> Path:
    """Sibling stage dir for the same conformer (design §6: 04_FREQ/05_SP/06_THERMO).

    *conf_dir* is ``<mol>/WORK/03_OPT/<engine>/conf_NNN``; the sibling keeps
    the conf index but moves to its own stage root. Falls back to *conf_dir*
    itself when the shape does not match, so non-scheduler callers keep a
    working (if undivided) layout.
    """
    parts = conf_dir.parts
    if len(parts) >= 4 and parts[-4] == "WORK":
        work_root = conf_dir.parents[2]
        target = work_root / stage / engine / conf_dir.name
        target.mkdir(parents=True, exist_ok=True)
        return target
    return conf_dir


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


def write_v2_manifest(
    mol_dir: Path,
    workflow: str,
    products: list[dict[str, Any]],
) -> None:
    """Write ``RESULT/result_manifest.json`` (v2 §8) with RESULT-relative paths."""
    manifest = ResultManifest(task_id="", workflow=workflow, status="completed")
    for item in products:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        manifest.add_product(
            id=str(item.get("id") or item["path"]),
            label=str(item.get("label") or item["path"]),
            path=str(item["path"]),
            kind=str(item.get("kind") or "file"),
        )
    manifest.write(TaskStorage(mol_dir).result_dir())


# Route-keyword maps for advanced level fields (catalog enum → ORCA keyword).
# "Fine" maps to ORCA's default DEFGRID2 and "Normal" to the default SCF
# convergence — both are omitted so default inputs stay byte-identical.
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

#: First-float matcher for xTB title energies (censo-zero passthrough).
_FLOAT_RE = re.compile(r"[-+]?\d+\.\d+")


def _base_route_extras(level: dict[str, Any]) -> list[str]:
    """Build ORCA route keywords from a level's advanced fields.

    Whitelisted fields only (§10.1): dispersion, ri_approximation,
    aux_j_basis, aux_c_basis, grid, scf_convergence. Values outside the catalog enums are
    passed through verbatim in upper case where conventional.
    """
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
    """Extract an explicit per-level solvent override.

    Returns ``(solvent, model)`` only when the level explicitly sets a
    non-empty solvent with a model other than ``none``; otherwise
    ``(None, None)`` so the workflow-global solvent applies.
    """
    model = str(level.get("solvent_model") or "").strip().lower()
    solvent = str(level.get("solvent") or "").strip()
    if not solvent or model in ("", "none"):
        return None, None
    return solvent, model


# ---------------------------------------------------------------------------
# Levels resolution (dft_opt / refinement_sp / screening_sp / thermo)
# ---------------------------------------------------------------------------


def resolve_levels(
    cfg: dict[str, Any],
    levels: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge config defaults with user ``--levels`` overrides.

    Consumes the full §10.1 field sets: ``dft_opt`` (functional, basis,
    dispersion, solvent_model, solvent, grid, scf_convergence,
    opt_convergence, max_steps), ``refinement_sp`` (functional, basis,
    aux_j_basis, aux_c_basis, dispersion, ri_approximation, solvent_model, solvent, grid,
    scf_convergence), ``screening_sp`` (same set, applied to the CENSO
    screening part) and ``thermo`` (temperature, pressure, scale_factor).

    Advanced ORCA fields are converted to route keywords for the ACP
    handoff; for CENSO-executed parts they become template lines injected
    via the per-run HOME mechanism (§6.4).
    """
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

    # Hessian policy for the opt stage (plan §10.2): route through the
    # shared normaliser so invalid values surface here rather than
    # silently falling through to ORCA. ``None`` ⇒ follow config.
    try:
        opt_recalc_hess = normalize_recalc_hess(dft_opt.get("recalc_hess"))
    except ValueError as exc:
        raise ValueError(f"dft_opt.recalc_hess: {exc}") from exc

    sp_method = refinement_sp.get("functional") or censo_cfg.get(
        "refinement_func", _DEFAULT_SP_FUNCTIONAL
    )
    sp_basis = refinement_sp.get("basis") or censo_cfg.get("refinement_basis", _DEFAULT_SP_BASIS)

    # --- ACP handoff route extras (opt / freq / SP) ------------------------
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

    # --- CENSO rcfile overrides + advanced-field templates ------------------
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

    # Workflow-global solvent fallback derived from levels (UI wizard path):
    # refinement_sp takes precedence over dft_opt.
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
# ACP standard handoff (opt → freq → SP → Shermo), consistency-enforced
# ---------------------------------------------------------------------------


def run_rank1_handoff(
    cfg: dict[str, Any],
    coordinates: np.ndarray[Any, Any],
    symbols: list[str],
    charge: int,
    multiplicity: int,
    work_dir: Path,
    resolved: dict[str, Any],
    solvent: str | None,
    solvent_model: str,
    index: int = 0,
    source: str = "rank1",
    sp_energy_precomputed: float | None = None,
    skip_opt_sp: bool = False,
) -> dict[str, Any]:
    """Run the ACP standard DFT handoff on one conformer.

    Enforces the v7 consistency rule: the frequency calculation uses the
    same method/basis as the geometry optimization (ORCA freq + Shermo).

    Args:
        skip_opt_sp: When True (censo-default survivors), skip the opt and
            SP steps and only run freq + Shermo on the given geometry with
            ``sp_energy_precomputed`` as electronic energy.

    Returns:
        Candidate record dict (index/coordinates/symbols/energy/gibbs/...).
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    opt_solvent = resolved.get("opt_solvent") or solvent
    opt_solvent_model = (
        resolved.get("opt_solvent_model")
        if resolved.get("opt_solvent")
        else (solvent_model if solvent else "none")
    ) or "none"

    orca: Any = get_backend("orca")(
        cfg,
        method=resolved["opt_method"],
        basis=resolved["opt_basis"] or "def2-TZVPP",
        solvent=opt_solvent,
        solvent_model=opt_solvent_model if opt_solvent else "none",
    )

    if skip_opt_sp:
        opt_coords = coordinates
        opt_symbols = symbols
        sp_energy = sp_energy_precomputed
        opt_log = None
        sp_log = None
        logger.info("  [freq] same-level frequency on pre-optimized geometry")
    else:
        logger.info("  [opt] geometry optimization (%s)", resolved["opt_method"])

        opt_result = orca.optimize(
            coordinates,
            symbols,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=work_dir,
            output_name=f"conf_{index:03d}_opt",
            method=resolved["opt_method"],
            basis=resolved["opt_basis"],
            route_extras=resolved.get("opt_route_extras"),
            geom_maxiter=resolved.get("opt_geom_maxiter"),
            recalc_hess=resolved.get("opt_recalc_hess"),
        )
        if not opt_result.success:
            raise RuntimeError(f"rank1 geometry optimization failed: {opt_result.error_message}")
        opt_coords = opt_result.coordinates
        opt_symbols = opt_result.symbols or symbols
        opt_log = str(opt_result.log_file) if opt_result.log_file else None
        sp_energy = None
        sp_log = None

    # Frequency at the SAME method/basis as the optimization (v7 rule)
    logger.info(
        "  [freq] frequency calculation (%s, same level as opt)",
        resolved["opt_method"],
    )
    freq_dir = _handoff_stage_dir(work_dir, "04_FREQ", "ORCA")
    freq_result = orca.frequency(
        opt_coords,
        opt_symbols,
        charge=charge,
        multiplicity=multiplicity,
        output_dir=freq_dir,
        output_name=f"conf_{index:03d}_freq",
        method=resolved["opt_method"],
        basis=resolved["opt_basis"],
        route_extras=resolved.get("opt_freq_route_extras"),
    )
    if not freq_result.success:
        raise RuntimeError(f"rank1 frequency calculation failed: {freq_result.error_message}")

    if not skip_opt_sp:
        logger.info(
            "  [sp] high-level single point (%s/%s)",
            resolved["sp_method"],
            resolved["sp_basis"],
        )
        sp_solvent = resolved.get("sp_solvent") or solvent
        sp_solvent_model = (
            resolved.get("sp_solvent_model")
            if resolved.get("sp_solvent")
            else (solvent_model if solvent else "none")
        ) or "none"
        if (sp_solvent, sp_solvent_model if sp_solvent else "none") == (
            opt_solvent,
            opt_solvent_model if opt_solvent else "none",
        ):
            sp_orca = orca
        else:
            sp_orca: Any = get_backend("orca")(
                cfg,
                method=resolved["sp_method"],
                basis=resolved["sp_basis"],
                solvent=sp_solvent,
                solvent_model=sp_solvent_model if sp_solvent else "none",
            )
        sp_dir = _handoff_stage_dir(work_dir, "05_SP", "ORCA")
        sp_result = sp_orca.single_point(
            opt_coords,
            opt_symbols,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=sp_dir,
            output_name=f"conf_{index:03d}_sp",
            method=resolved["sp_method"],
            basis=resolved["sp_basis"],
            route_extras=resolved.get("sp_route_extras"),
        )
        if not sp_result.success:
            raise RuntimeError(f"rank1 single-point failed: {sp_result.error_message}")
        sp_energy = sp_result.energy
        sp_log = str(sp_result.log_file) if sp_result.log_file else None

    if sp_energy is None:
        raise RuntimeError("No electronic energy available for Shermo handoff")

    thermo_dir = _handoff_stage_dir(work_dir, "06_THERMO", "Shermo")
    logger.info("  [thermo] thermodynamic correction")
    thermo_module = import_module("acp.mechanism.providers.thermo")
    thermo_provider: Any = thermo_module.get_thermochemistry_provider(cfg, runner=run_shermo)
    standard_state: str = thermo_module.resolve_standard_state(cfg)
    compute_details = getattr(thermo_provider, "compute_details", None)
    if callable(compute_details):
        thermo_result: Any = compute_details(
            sp_energy=sp_energy,
            freq_log=freq_result.log_file,
            ensemble=None,
            temperature=resolved["temperature_k"],
            standard_state=standard_state,
            output_dir=thermo_dir,
            output_file=thermo_dir / f"conf_{index:03d}_Shermo.sum",
            pressure_atm=resolved["pressure_atm"],
            scl_zpe=resolved["scl_zpe"],
            ilowfreq=resolved["ilowfreq"],
            imagreal=resolved["imagreal"],
            conc=resolved["conc"],
        )
    else:
        thermo_result = thermo_provider.compute(
            sp_energy=sp_energy,
            freq_log=freq_result.log_file,
            ensemble=None,
            temperature=resolved["temperature_k"],
            standard_state=standard_state,
        )
    shermo_result = thermo_module.thermochemistry_result_to_legacy_dict(thermo_result)
    if thermo_result.gibbs_hartree is None:
        raise RuntimeError("Shermo thermochemistry failed for rank1 conformer")

    g_sum = shermo_result.get("g_sum")
    g_conc = shermo_result.get("g_conc")
    gibbs = thermo_result.gibbs_hartree

    return {
        "index": index,
        "coordinates": np.asarray(opt_coords),
        "symbols": list(opt_symbols),
        "energy": sp_energy,
        "gibbs": gibbs,
        "gibbs_correction": g_sum,
        "h_correction": shermo_result.get("h_sum"),
        "u_correction": shermo_result.get("u_sum"),
        "s_total": shermo_result.get("s_total"),
        "g_conc": g_conc,
        "source": source,
        "opt_log": opt_log,
        "sp_log": sp_log,
    }


# ---------------------------------------------------------------------------
# Boltzmann selection
# ---------------------------------------------------------------------------


def boltzmann_weights(gibbs_values: list[float], temperature_k: float) -> list[float]:
    if not gibbs_values:
        return []
    kt = _K_B_HARTREE_PER_KELVIN * temperature_k
    g_min = min(gibbs_values)
    raw = [float(np.exp(-(g - g_min) / kt)) for g in gibbs_values]
    total = sum(raw)
    if total <= 0:
        return [0.0 for _ in raw]
    return [w / total for w in raw]


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
    if not records:
        return []
    ordered = sorted(records, key=lambda r: r.gtot)
    weights = boltzmann_weights([r.gtot for r in ordered], temperature_k)
    selected: list[Any] = []
    cumulative = 0.0
    for rec, weight in zip(ordered, weights):
        selected.append(rec)
        cumulative += weight
        if cumulative >= threshold:
            break
    return selected


# ---------------------------------------------------------------------------
# Ensemble total-Gibbs summary + final output writers
# ---------------------------------------------------------------------------


def build_ensemble_summary(
    candidates: list[dict[str, Any]],
    dft_weights: list[float],
    temperature_k: float,
    external_weights: dict[str, float] | None = None,
    external_total_gibbs: float | None = None,
    external_total_gibbs_censo: float | None = None,
    population_weights: dict[str, float] | None = None,
    external_table_source: str = "censo",
) -> EnsembleThermoSummary:
    """Assemble the ensemble total-Gibbs summary for an output batch.

    Workflow 1 (no external table): the DFT table of the selected
    candidates drives p₁ and S_mix; ``population_coverage`` is the
    cumulative weight of the selected set inside the full screening table
    (``population_weights``), disclosing the ≥99% truncation.

    Workflow 2 (external table): the complete CENSO/xTB table drives p₁
    and S_mix while G₁ is the fine rank1 Gibbs value (or the CENSO gtot on
    cheap paths); the coverage is 1.0 by construction of the table.
    """
    if external_weights is not None:
        method = "censo_table_rank1" if external_table_source == "censo" else "xtb_table_rank1"
        p1 = 0.0
        for cand in candidates:
            p1 = external_weights.get(str(cand.get("source", "")), 0.0)
            if p1 > 0.0:
                break
        if p1 <= 0.0 and external_weights:
            p1 = max(external_weights.values())
        rank1_source = str(candidates[0].get("source", "")) if candidates else ""
        rank1_gibbs = candidates[0].get("gibbs") if candidates else None
        rank1_gibbs = float(rank1_gibbs) if rank1_gibbs is not None else 0.0
        total = (
            external_total_gibbs
            if external_total_gibbs is not None
            else ensemble_total_gibbs(rank1_gibbs, p1, temperature_k)
        )
        rows: list[dict[str, Any]] = []
        for conf_id, w in sorted(external_weights.items()):
            g = rank1_gibbs if conf_id == rank1_source else None
            rows.append(
                {
                    "conf_id": conf_id,
                    "gibbs_hartree": g,
                    "delta_gibbs_kcal_mol": None,
                    "weight": round(float(w), 6),
                }
            )
        weights_values = list(external_weights.values())
        coverage = 1.0
    else:
        method = "dft_table"
        gibbs_values = [
            c["gibbs"] if c.get("gibbs") is not None else c.get("energy", 0.0) for c in candidates
        ]
        rank1_gibbs = gibbs_values[0] if gibbs_values else 0.0
        total = ensemble_total_gibbs_from_values(gibbs_values, temperature_k)
        p1 = dft_weights[0] if dft_weights else 0.0
        rows = [
            {
                "conf_id": c.get("source", f"conf{c.get('index', i)}"),
                "gibbs_hartree": c.get("gibbs"),
                "delta_gibbs_kcal_mol": (
                    (float(c["gibbs"]) - float(rank1_gibbs)) * HARTREE_TO_KCAL
                    if c.get("gibbs") is not None and rank1_gibbs is not None
                    else None
                ),
                "weight": round(float(w), 6),
            }
            for i, (c, w) in enumerate(zip(candidates, dft_weights))
        ]
        weights_values = dft_weights
        coverage = 1.0
        if population_weights:
            selected = sum(
                population_weights.get(str(c.get("source", "")), 0.0) for c in candidates
            )
            if selected > 0.0:
                coverage = round(min(selected, 1.0), 6)

    return EnsembleThermoSummary(
        method=method,
        temperature_k=temperature_k,
        total_gibbs_hartree=total,
        total_gibbs_kcal_mol=total * HARTREE_TO_KCAL,
        rank1_gibbs_hartree=rank1_gibbs,
        rank1_weight=round(p1, 6),
        mixing_entropy_kcal_per_mol_kelvin=s_mix_kcal_per_mol_kelvin(weights_values),
        mixing_entropy_cal_per_mol_kelvin=s_mix_cal_per_mol_kelvin(weights_values),
        t_s_mix_kcal_per_mol=t_s_mix_kcal_per_mol(weights_values, temperature_k),
        population_coverage=coverage,
        conformers=rows,
        censo_reference_gibbs_hartree=external_total_gibbs_censo,
        censo_reference_gibbs_kcal_mol=(
            external_total_gibbs_censo * HARTREE_TO_KCAL
            if external_total_gibbs_censo is not None
            else None
        ),
    )


def write_final_outputs(
    candidates: list[dict[str, Any]],
    mol_dir: Path,
    mol_name: str,
    temperature_k: float,
    external_weights: dict[str, float] | None = None,
    external_total_gibbs: float | None = None,
    external_total_gibbs_censo: float | None = None,
    population_weights: dict[str, float] | None = None,
    external_table_source: str = "censo",
) -> dict[str, Any]:
    """Write categorized final results under ``RESULT/``.

    File formats match the legacy engine's ``_finalize_results`` products
    field-for-field so downstream consumers (NMR, benchmark, frontend) work
    without modification.  New scheduler tasks store the products in
    ``RESULT/structures`` / ``RESULT/energies`` / ``RESULT/ensembles``.  The
    legacy ``finalDFT/`` directory is read-only compatibility for historical
    tasks and is no longer created here.  When *external_weights* is given
    (workflow 2), the Boltzmann table is written to ``RESULT/ensembles``;
    the thermo CSV keeps its legacy header and receives a TOTAL row.

    Args:
        external_weights: Full-ensemble weight table (conf_id → p) driving
            workflow 2 (CENSO or xTB table).
        external_total_gibbs: Pre-computed G_total (fine G₁ + kT·ln p₁).
        external_total_gibbs_censo: CENSO-level reference total for the
            workflow-2 cross-check (gtot₁ + kT·ln p₁).
        population_weights: Full screening-table weights used to disclose
            the cumulative population coverage of the workflow-1 DFT table.
        external_table_source: ``"censo"`` (default) or ``"xtb"`` for the
            workflow-2 weight-table provenance.
    """
    result_structures = v2_result_category(mol_dir, "structures")
    result_energies = v2_result_category(mol_dir, "energies")
    result_ensembles = v2_result_category(mol_dir, "ensembles")

    candidates = sorted(
        candidates,
        key=lambda c: c["gibbs"] if c.get("gibbs") is not None else float("inf"),
    )
    gibbs_values = [
        c["gibbs"] if c.get("gibbs") is not None else c.get("energy", 0.0) for c in candidates
    ]
    weights = boltzmann_weights([float(g) for g in gibbs_values], temperature_k)

    for rank, (cand, weight) in enumerate(zip(candidates, weights), start=1):
        cand["rank"] = rank
        cand["weight"] = weight

    summary = build_ensemble_summary(
        candidates,
        weights,
        temperature_k,
        external_weights=external_weights,
        external_total_gibbs=external_total_gibbs,
        external_total_gibbs_censo=external_total_gibbs_censo,
        population_weights=population_weights,
        external_table_source=external_table_source,
    )
    thermo_json = result_energies / "ensemble_thermo.json"
    summary.write_json(thermo_json)

    ensemble_xyz = result_structures / "all_conformers.xyz"
    with open(ensemble_xyz, "w") as f:
        for c in candidates:
            e_fmt = f"{c['energy']:.6f}" if c.get("energy") is not None else "N/A"
            f.write(f"{len(c['symbols'])}\n")
            f.write(
                f"Conformer {c['index']}, E={e_fmt}, Rank={c['rank']}, Weight={c['weight']:.4f}\n"
            )
            for sym, coord in zip(c["symbols"], c["coordinates"]):
                f.write(f"{sym:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")

    thermo_csv = result_energies / "conformer_thermo.csv"
    with open(thermo_csv, "w") as f:
        f.write(
            "index,rank,energy_hartree,gibbs_correction,gibbs_hartree,"
            "h_correction,u_correction,s_total,g_conc,weight,source\n"
        )
        for c in candidates:
            f.write(f"{c['index']},{c['rank']},")
            f.write(
                f"{c['energy']:.10f},"
                if c.get("energy") is not None and c["energy"] != float("inf")
                else "N/A,"
            )
            for key in (
                "gibbs_correction",
                "gibbs",
                "h_correction",
                "u_correction",
                "s_total",
                "g_conc",
            ):
                val = c.get(key)
                f.write(f"{val:.10f}," if val is not None else ",")
            f.write(f"{c['weight']:.6f},")
            f.write(f"{c.get('source', 'unknown')}\n")
        # TOTAL summary row appended inside the write block — column header
        # set stays untouched so existing parsers keep working (G_total
        # lands in the gibbs_hartree column, source marker "ensemble_total").
        f.write(
            ",".join(
                [
                    "TOTAL",
                    "",
                    "",
                    "",
                    f"{summary.total_gibbs_hartree:.10f}",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "ensemble_total",
                ]
            )
            + "\n"
        )

    boltzmann_table_json: str | None = None
    if external_weights is not None:
        bt_path = result_ensembles / "boltzmann_table.json"
        bt_path.write_text(
            json.dumps(
                {
                    "temperature_k": temperature_k,
                    "source": "censo" if external_table_source == "censo" else "xtb",
                    "weights": {str(k): float(v) for k, v in sorted(external_weights.items())},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        boltzmann_table_json = str(bt_path)

    global_min = candidates[0]
    global_min_xyz = result_structures / f"{mol_name}_global_min.xyz"
    gm_energy = (
        global_min.get("g_conc")
        if global_min.get("g_conc") is not None
        else (
            global_min.get("gibbs")
            if global_min.get("gibbs") is not None
            else global_min.get("energy")
        )
    )
    write_xyz(
        global_min_xyz,
        global_min["coordinates"],
        global_min["symbols"],
        title=f"Global minimum for {mol_name}",
        energy=gm_energy if gm_energy is not None else float("inf"),
        comment=f"Rank {global_min['rank']}, Weight {global_min['weight']:.4f}",
    )

    outputs: dict[str, Any] = {
        "all_conformers_xyz": str(ensemble_xyz),
        "thermo_csv": str(thermo_csv),
        "global_min_xyz": str(global_min_xyz),
        "total_gibbs_hartree": summary.total_gibbs_hartree,
        "total_gibbs_kcal_mol": summary.total_gibbs_kcal_mol,
        "ensemble_thermo_json": str(thermo_json),
    }
    if summary.censo_reference_gibbs_hartree is not None:
        outputs["total_gibbs_censo_hartree"] = summary.censo_reference_gibbs_hartree
    if boltzmann_table_json is not None:
        outputs["boltzmann_table_json"] = boltzmann_table_json

    result_summary_products = [
        {
            "label": "Ranked conformers (XYZ)",
            "path": "RESULT/structures/all_conformers.xyz",
            "kind": "xyz",
        },
        {
            "label": "Ensemble thermo (G_total)",
            "path": "RESULT/energies/ensemble_thermo.json",
            "kind": "report",
        },
        {
            "label": "Conformer thermo table (CSV)",
            "path": "RESULT/energies/conformer_thermo.csv",
            "kind": "table",
        },
        {
            "label": "Global minimum structure",
            "path": f"RESULT/structures/{mol_name}_global_min.xyz",
            "kind": "xyz",
            "role": "final_stable_structure",
        },
    ]
    write_result_summary(mol_dir, workflow="energy", products=result_summary_products)
    write_v2_manifest(
        mol_dir,
        "energy",
        [
            {
                "id": "all_conformers",
                "label": "Ranked conformers (XYZ)",
                "path": "structures/all_conformers.xyz",
                "kind": "structure",
            },
            {
                "id": "ensemble_thermo",
                "label": "Ensemble thermo (G_total)",
                "path": "energies/ensemble_thermo.json",
                "kind": "energy_report",
            },
            {
                "id": "conformer_thermo_csv",
                "label": "Conformer thermo table (CSV)",
                "path": "energies/conformer_thermo.csv",
                "kind": "file",
            },
            {
                "id": "global_min",
                "label": "Global minimum structure",
                "path": f"structures/{mol_name}_global_min.xyz",
                "kind": "structure",
            },
        ],
    )
    return outputs


def build_result_ensemble(
    candidates: list[dict[str, Any]],
    structure: Structure,
) -> StructureEnsemble:
    records: list[StructureRecord] = []
    for c in candidates:
        conf_struct = Structure(
            id=f"{structure.id}_conf{c['index']:03d}",
            charge=structure.charge,
            multiplicity=structure.multiplicity,
            symbols=c["symbols"],
            coordinates=c["coordinates"].tolist()
            if isinstance(c["coordinates"], np.ndarray)
            else c["coordinates"],
            metadata={"rank": c.get("rank"), "source": c.get("source")},
        )
        records.append(
            StructureRecord(
                structure=conf_struct,
                energy_hartree=c.get("energy"),
                free_energy_hartree=c.get("gibbs"),
                weight=c.get("weight"),
                properties={
                    "gibbs_correction": c.get("gibbs_correction"),
                    "g_conc": c.get("g_conc"),
                },
            )
        )
    return StructureEnsemble(records=records)


def censo_record_to_candidate(rec: Any, index: int = 0) -> dict[str, Any]:
    """Convert a CensoConformerRecord (cheap path) into a candidate dict.

    On the ``--no-opt`` path the CENSO gtot already is the final free energy
    (E_SP + δGsolv + G_mRRHO at xTB level).
    """
    return {
        "index": index,
        "coordinates": np.asarray(rec.coordinates),
        "symbols": list(rec.symbols),
        "energy": rec.energy,
        "gibbs": rec.gtot,
        "gibbs_correction": rec.grrho,
        "h_correction": None,
        "u_correction": None,
        "s_total": None,
        "g_conc": None,
        "source": rec.conf_id,
    }


# ---------------------------------------------------------------------------
# xTB passthrough (censo-zero) + solvent / ewin resolvers (ex-ensemble.py)
# ---------------------------------------------------------------------------


def xtb_passthrough_result(
    ensemble_xyz: Path,
    temperature: float,
) -> CensoRunResult:
    """Build a CensoRunResult directly from an xTB-ranked ensemble (censo-zero).

    The censo-zero preset does not invoke CENSO (§7): the ensemble is
    exported as-is, sorted by the xTB energies parsed from the frame title
    lines. ``gsolv``/``grrho`` are zero — the ``gtot`` equals the xTB
    electronic energy.
    """
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


def resolve_solvent_config(
    cfg: dict[str, Any],
    user_solvent: str | None,
) -> tuple[str | None, str]:
    censo_solvent = (
        user_solvent if user_solvent is not None else cfg.get("censo", {}).get("solvent")
    )
    preopt_model = (
        cfg.get("theory", {}).get("preoptimization", {}).get("solvent_model") or ""
    ).lower()
    censo_model = (cfg.get("censo", {}).get("solvent_model") or "").lower()

    if preopt_model and preopt_model != "none":
        solvent_model = preopt_model
    elif censo_model and censo_model != "none":
        solvent_model = censo_model
    else:
        solvent_model = "none"

    return censo_solvent, solvent_model


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


__all__ = [
    "boltzmann_weights",
    "build_ensemble_summary",
    "build_result_ensemble",
    "censo_record_to_candidate",
    "resolve_crest_ewin",
    "resolve_levels",
    "resolve_solvent_config",
    "run_rank1_handoff",
    "select_cumulative_boltzmann",
    "write_final_outputs",
    "xtb_passthrough_result",
]
