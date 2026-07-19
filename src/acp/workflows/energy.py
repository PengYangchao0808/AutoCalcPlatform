"""Conformer energy workflow: CREST → CENSO screening → ensemble refinement.

Implements the ``acp run energy`` workflow (censo-light / censo-default /
censo-zero presets).  The refinement set is the group of lowest-free-energy
conformers whose cumulative Boltzmann population exceeds the configured
threshold (``censo.refinement_threshold``, default 0.99 — v15 semantics,
superseding the v10 rank1-only output for censo-light/censo-zero).  Default
path (opt enabled) refines each selected conformer via the ACP standard
handoff — ORCA opt → ORCA freq (same method/basis) → high-level SP →
Shermo — enforcing the opt/freq consistency rule.  The ``--no-opt`` cheap
path delegates refinement to CENSO itself (xTB geometry + xTB SPH mRRHO)
and applies the same cumulative-population selection to the refined
records.  ``censo-default`` keeps CENSO's native Part3 funnel (0.99
population cutoff) followed by same-level freq + Shermo re-ranking.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

import numpy as np

from acp.backends.censo_backend import CensoBackend, CensoRunResult
from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.core.state import WorkflowState
from acp.core.workflow import WorkflowResult
from acp.io.structures import InputFormat, StructureReader
from acp.workflows._helpers import sanitize_job_name
from acp.workflows.ensemble import (
    _is_file_input,
    _is_multiframe_xyz,
    _resolve_crest_ewin,
    _resolve_solvent_config,
    _xtb_passthrough_result,
)
from conformer_search.config import load_config
from conformer_search.qc.interfaces.crest import CRESTInterface
from conformer_search.qc.interfaces.orca import ORCAInterface
from conformer_search.qc.runners import run_shermo
from conformer_search.utils.file_io import write_xyz

logger = logging.getLogger(__name__)

_K_B_HARTREE_PER_KELVIN = 3.166811563e-6

_ENERGY_PRESETS = ("censo-light", "censo-default", "censo-zero")

_DEFAULT_OPT_FUNCTIONAL = "r2SCAN-3c"
_DEFAULT_SP_FUNCTIONAL = "wB97M-V"
_DEFAULT_SP_BASIS = "def2-TZVPP"

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


def _base_route_extras(level: dict[str, Any]) -> list[str]:
    """Build ORCA route keywords from a level's advanced fields.

    Whitelisted fields only (§10.1): dispersion, ri_approximation,
    aux_basis, grid, scf_convergence. Values outside the catalog enums are
    passed through verbatim in upper case where conventional.
    """
    extras: list[str] = []

    dispersion = str(level.get("dispersion") or "").strip()
    if dispersion and dispersion.lower() != "none":
        extras.append(dispersion.upper())

    ri = str(level.get("ri_approximation") or "").strip()
    if ri and ri.lower() != "none":
        extras.append(ri.upper())

    aux_basis = str(level.get("aux_basis") or "").strip()
    if aux_basis:
        extras.append(aux_basis)

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


def _resolve_levels(
    cfg: dict[str, Any],
    levels: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge config defaults with user ``--levels`` overrides.

    Consumes the full §10.1 field sets: ``dft_opt`` (functional, basis,
    dispersion, solvent_model, solvent, grid, scf_convergence,
    opt_convergence, max_steps), ``refinement_sp`` (functional, basis,
    aux_basis, dispersion, ri_approximation, solvent_model, solvent, grid,
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


def _run_rank1_handoff(
    cfg: dict[str, Any],
    coordinates: np.ndarray,
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

    orca = ORCAInterface(
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
    freq_result = orca.frequency(
        opt_coords,
        opt_symbols,
        charge=charge,
        multiplicity=multiplicity,
        output_dir=work_dir,
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
            sp_orca = ORCAInterface(
                cfg,
                method=resolved["sp_method"],
                basis=resolved["sp_basis"],
                solvent=sp_solvent,
                solvent_model=sp_solvent_model if sp_solvent else "none",
            )
        sp_result = sp_orca.single_point(
            opt_coords,
            opt_symbols,
            charge=charge,
            multiplicity=multiplicity,
            output_dir=work_dir,
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

    logger.info("  [shermo] thermodynamic correction")
    shermo_bin = cfg.get("executables", {}).get("shermo", {}).get("path", "Shermo")
    shermo_result = run_shermo(
        freq_output=freq_result.log_file,
        sp_energy=sp_energy,
        output_dir=work_dir,
        shermo_bin=shermo_bin,
        output_file=work_dir / f"conf_{index:03d}_Shermo.sum",
        temperature_k=resolved["temperature_k"],
        pressure_atm=resolved["pressure_atm"],
        scl_zpe=resolved["scl_zpe"],
        ilowfreq=resolved["ilowfreq"],
        imagreal=resolved["imagreal"],
        conc=resolved["conc"],
    )
    if not shermo_result:
        raise RuntimeError("Shermo thermochemistry failed for rank1 conformer")

    g_sum = shermo_result.get("g_sum")
    g_conc = shermo_result.get("g_conc")
    gibbs = g_conc if g_conc is not None else g_sum

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
# Output writers (format identical to existing finalDFT products)
# ---------------------------------------------------------------------------


def _boltzmann_weights(gibbs_values: list[float], temperature_k: float) -> list[float]:
    if not gibbs_values:
        return []
    kt = _K_B_HARTREE_PER_KELVIN * temperature_k
    g_min = min(gibbs_values)
    raw = [float(np.exp(-(g - g_min) / kt)) for g in gibbs_values]
    total = sum(raw)
    if total <= 0:
        return [0.0 for _ in raw]
    return [w / total for w in raw]


def _select_cumulative_boltzmann(
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
    weights = _boltzmann_weights([r.gtot for r in ordered], temperature_k)
    selected: list[Any] = []
    cumulative = 0.0
    for rec, weight in zip(ordered, weights):
        selected.append(rec)
        cumulative += weight
        if cumulative >= threshold:
            break
    return selected


def _write_final_outputs(
    candidates: list[dict[str, Any]],
    mol_dir: Path,
    mol_name: str,
    temperature_k: float,
) -> dict[str, str]:
    """Write finalDFT/all_conformers.xyz + conformer_thermo.csv + global_min.xyz.

    File formats match the legacy engine's ``_finalize_results`` products
    field-for-field so downstream consumers (NMR, benchmark, frontend) work
    without modification.
    """
    final_dft_dir = mol_dir / "finalDFT"
    final_dft_dir.mkdir(parents=True, exist_ok=True)

    candidates = sorted(
        candidates,
        key=lambda c: c["gibbs"] if c.get("gibbs") is not None else float("inf"),
    )
    gibbs_values = [
        c["gibbs"] if c.get("gibbs") is not None else c.get("energy", 0.0) for c in candidates
    ]
    weights = _boltzmann_weights([float(g) for g in gibbs_values], temperature_k)

    for rank, (cand, weight) in enumerate(zip(candidates, weights), start=1):
        cand["rank"] = rank
        cand["weight"] = weight

    ensemble_xyz = final_dft_dir / "all_conformers.xyz"
    with open(ensemble_xyz, "w") as f:
        for c in candidates:
            e_fmt = f"{c['energy']:.6f}" if c.get("energy") is not None else "N/A"
            f.write(f"{len(c['symbols'])}\n")
            f.write(
                f"Conformer {c['index']}, E={e_fmt}, Rank={c['rank']}, Weight={c['weight']:.4f}\n"
            )
            for sym, coord in zip(c["symbols"], c["coordinates"]):
                f.write(f"{sym:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}\n")

    thermo_csv = final_dft_dir / "conformer_thermo.csv"
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

    global_min = candidates[0]
    global_min_xyz = mol_dir / f"{mol_name}_global_min.xyz"
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

    return {
        "all_conformers_xyz": str(ensemble_xyz),
        "thermo_csv": str(thermo_csv),
        "global_min_xyz": str(global_min_xyz),
    }


def _write_screening_ranking(result: CensoRunResult, mol_dir: Path) -> str:
    """Write the auxiliary screening ranking table (ΔGconf,rel estimation)."""
    ensemble_dir = mol_dir / "ensemble"
    ensemble_dir.mkdir(parents=True, exist_ok=True)
    csv_path = ensemble_dir / "screening_ranking.csv"

    weights = result.boltzmann_weights()
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["conf_id", "energy_hartree", "gsolv", "grrho", "gtot", "boltzmann_weight"])
        for rec in result.records:
            writer.writerow(
                [
                    rec.conf_id,
                    f"{rec.energy:.10f}",
                    f"{rec.gsolv:.10f}",
                    f"{rec.grrho:.10f}",
                    f"{rec.gtot:.10f}",
                    f"{weights.get(rec.conf_id, 0.0):.6f}",
                ]
            )
    return str(csv_path)


def _build_result_ensemble(
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


# ---------------------------------------------------------------------------
# Rank1 extraction helpers
# ---------------------------------------------------------------------------


def _censo_record_to_candidate(rec: Any, index: int = 0) -> dict[str, Any]:
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
# Main entry point
# ---------------------------------------------------------------------------


def run_conformer_energy(
    input_source: str,
    output_dir: str | Path = "./energy_output",
    preset: str = "censo-light",
    config: dict[str, Any] | None = None,
    name: str | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    solvent: str | None = None,
    nproc: int | None = None,
    no_opt: bool = False,
    levels: dict[str, Any] | None = None,
    ewin: float | None = None,
) -> WorkflowResult:
    """Run the conformer energy workflow (cumulative-Boltzmann ensemble, v15).

    Args:
        input_source: SMILES string or path to XYZ file (multi-frame XYZ
            skips CREST and is treated as an external ensemble).
        output_dir: Output directory.
        preset: censo-light (default) / censo-default / censo-zero.
        config: Optional configuration dict.
        name: Molecule name.
        charge: Molecular charge.
        multiplicity: Spin multiplicity.
        solvent: Solvent name or None for gas phase.
        nproc: Number of processors.
        no_opt: Disable the high-accuracy geometry optimization of the
            selected conformers (cheap RSH//xTB path). No effect for
            censo-default.
        levels: Method level overrides (censo / dft_opt / refinement_sp /
            screening_sp / thermo / refinement_threshold), field names
            identical to the catalog.
        ewin: CREST energy window in kcal/mol. Priority: this argument >
            ``levels.censo.ewin`` > ``censo.ewin`` config > 6.0.

    Returns:
        WorkflowResult whose ensemble contains the lowest-free-energy
        conformers up to the cumulative Boltzmann population threshold
        (``censo.refinement_threshold``, default 0.99) — for all presets.
    """
    preset = (preset or "censo-light").lower()
    if preset not in _ENERGY_PRESETS:
        return WorkflowResult(
            status="failed",
            ensemble=StructureEnsemble(records=[]),
            stages_completed=[],
            error=f"Unknown preset '{preset}'. Allowed: {', '.join(_ENERGY_PRESETS)}",
        )

    cfg = load_config(overrides=config) if config is not None else load_config()

    opt_enabled = not no_opt
    if not cfg.get("censo", {}).get("optimization", {}).get("enabled", True):
        opt_enabled = False
    if preset == "censo-default":
        opt_enabled = True  # Part2 is always on for censo-default

    resolved = _resolve_levels(cfg, levels)

    # Resolve to an absolute path: QC interfaces run subprocesses with
    # cwd=<stage dir> while passing input paths as given, so relative
    # output roots (e.g. remote jobs with --output ".") would break.
    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    try:
        reader = StructureReader()
        input_format = reader.detect_format(input_source)
        structure = reader.read(
            input_source,
            charge=charge,
            multiplicity=multiplicity,
            name=name,
        )
    except Exception as exc:
        logger.exception("Failed to read input: %s", exc)
        return WorkflowResult(
            status="failed",
            ensemble=StructureEnsemble(records=[]),
            stages_completed=[],
            error=str(exc),
        )

    safe_name = sanitize_job_name(structure.id)
    if structure.coordinates is None or len(structure.coordinates) == 0:
        return WorkflowResult(
            status="failed",
            ensemble=StructureEnsemble(records=[]),
            stages_completed=["embed"],
            error="Input embedding produced no 3D coordinates",
        )
    structure = Structure(
        id=safe_name,
        charge=structure.charge,
        multiplicity=structure.multiplicity,
        symbols=structure.symbols,
        coordinates=structure.coordinates,
        metadata=structure.metadata,
    )

    state = WorkflowState(output_root / safe_name, safe_name)
    state.initialize(
        input_source=input_source,
        stage_names=["crest", "censo", "dft_handoff", "finalize", "conformer_energy"],
    )

    mol_dir = output_root / safe_name

    # Solvent priority: CLI --solvent > levels (UI wizard fields) > YAML.
    effective_solvent_arg = solvent if solvent is not None else resolved["levels_solvent"]
    censo_solvent, solvent_model = _resolve_solvent_config(cfg, effective_solvent_arg)
    if censo_solvent and resolved["levels_solvent_model"]:
        solvent_model = resolved["levels_solvent_model"]
    _solvent_model = solvent_model if solvent_model else "none"
    if censo_solvent and _solvent_model == "none":
        _solvent_model = "smd"

    safe_nproc: int | None = None
    if nproc is not None and nproc > 0:
        safe_nproc = nproc

    stages_completed: list[str] = ["embed"]
    crest_skipped = False
    screening_ranking_csv: str | None = None

    try:
        # ---- Stage: CREST search (or external ensemble) -------------------
        is_file = input_format not in (InputFormat.SMILES,) and _is_file_input(input_source)
        state.set_stage("crest")
        if is_file and _is_multiframe_xyz(Path(input_source)):
            logger.info("Multi-frame XYZ input detected — skipping CREST")
            crest_ensemble_xyz = Path(input_source).resolve()
            state.complete_stage("crest", {"status": "skipped", "reason": "multi-frame XYZ input"})
            crest_skipped = True
        else:
            crest_dir = mol_dir / "crest"
            crest_dir.mkdir(parents=True, exist_ok=True)

            crest_cfg = cfg.get("executables", {}).get("crest", {})
            crest_iface = CRESTInterface(
                config=cfg,
                gfn_level=crest_cfg.get("gfn_level", 2),
                solvent=censo_solvent,
                solvent_model=_solvent_model,
            )

            coords = (
                np.asarray(structure.coordinates)
                if structure.coordinates is not None
                else np.empty((0, 3))
            )
            crest_ewin = _resolve_crest_ewin(
                cfg,
                ewin if ewin is not None else resolved["crest_ewin_level"],
            )
            logger.info("CREST energy window: %.2f kcal/mol", crest_ewin)
            crest_result = crest_iface.run_conformer_search(
                coordinates=coords,
                symbols=list(structure.symbols),
                output_dir=crest_dir,
                output_name=safe_name,
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                energy_window=crest_ewin,
            )
            if not crest_result.success:
                msg = crest_result.error_message or "CREST search failed with unknown error"
                raise RuntimeError(f"CREST search failed: {msg}")

            state.complete_stage("crest", {"status": "completed"})

            crest_ensemble_xyz = crest_dir / "crest_conformers.xyz"
            if not crest_ensemble_xyz.exists():
                alt = list(crest_dir.glob("*conformer*.xyz")) + list(
                    crest_dir.glob("*ensemble*.xyz")
                )
                if alt:
                    crest_ensemble_xyz = alt[0]
                else:
                    raise FileNotFoundError(f"CREST output not found in {crest_dir}")

        stages_completed.append("crest")

        temperature_k = float(resolved["temperature_k"])
        threshold = float(resolved["refinement_threshold"])
        candidates: list[dict[str, Any]]

        def _handoff_selected(selected: list[Any]) -> list[dict[str, Any]]:
            """Run the ACP handoff on each selected conformer (v15 ensemble).

            Individual failures beyond rank1 are logged and skipped so a
            single bad conformer does not discard the rest of the ensemble;
            an empty result raises.
            """
            results: list[dict[str, Any]] = []
            for i, rec in enumerate(selected):
                handoff_dir = mol_dir / "finalDFT" / f"conf_{i:03d}"
                try:
                    cand = _run_rank1_handoff(
                        cfg,
                        np.asarray(rec.coordinates),
                        list(rec.symbols),
                        structure.charge,
                        structure.multiplicity,
                        handoff_dir,
                        resolved,
                        censo_solvent,
                        _solvent_model,
                        index=i,
                        source=rec.conf_id,
                    )
                except RuntimeError as exc:
                    if i == 0:
                        raise
                    logger.warning(
                        "DFT handoff failed for %s (%s) — dropping this "
                        "conformer from the ensemble",
                        rec.conf_id,
                        exc,
                    )
                    continue
                results.append(cand)
            if not results:
                raise RuntimeError("All conformer handoffs failed")
            return results

        # ---- Preset dispatch ----------------------------------------------
        if preset == "censo-zero" and opt_enabled:
            # xTB-ranked ensemble → cumulative Boltzmann selection → ACP
            # handoff for each survivor (no CENSO CLI involved)
            passthrough = _xtb_passthrough_result(crest_ensemble_xyz, temperature_k)
            selected = _select_cumulative_boltzmann(
                passthrough.records,
                temperature_k,
                threshold,
            )
            logger.info(
                "censo-zero (opt on): %d/%d conformers within %.0f%% cumulative "
                "Boltzmann (xTB) → ACP handoff",
                len(selected),
                len(passthrough.records),
                threshold * 100,
            )
            screening_ranking_csv = _write_screening_ranking(passthrough, mol_dir)
            state.set_stage("dft_handoff")
            candidates = _handoff_selected(selected)
            state.complete_stage("dft_handoff", {"status": "completed"})
            stages_completed.append("dft_handoff")

        else:
            # All remaining paths invoke CENSO
            censo_dir = mol_dir / "censo"
            censo_dir.mkdir(parents=True, exist_ok=True)
            backend = CensoBackend(cfg)

            part_overrides: dict[str, dict[str, Any]] = {}
            if resolved["screening_overrides"]:
                part_overrides["screening"] = resolved["screening_overrides"]
            if resolved["refinement_overrides"]:
                part_overrides["refinement"] = resolved["refinement_overrides"]
            if abs(threshold - 0.99) > 1e-9:
                part_overrides.setdefault("refinement", {})["threshold"] = threshold

            part_templates: dict[str, list[str]] = {}
            if resolved["screening_template_lines"]:
                part_templates["screening"] = resolved["screening_template_lines"]
            if resolved["refinement_template_lines"]:
                part_templates["refinement"] = resolved["refinement_template_lines"]

            if preset == "censo-light" and opt_enabled:
                # CENSO -P -S screens rank1 → ACP handoff. Refinement runs
                # ACP-side here, so only screening overrides/templates may
                # reach the CENSO rcfile (CENSO validates all sections).
                censo_overrides = {k: v for k, v in part_overrides.items() if k == "screening"}
                censo_templates = {k: v for k, v in part_templates.items() if k == "screening"}
                state.set_stage("censo")
                censo_result = backend.refine_ensemble(
                    crest_ensemble_xyz,
                    censo_dir,
                    preset=preset,
                    charge=structure.charge,
                    multiplicity=structure.multiplicity,
                    temperature=temperature_k,
                    solvent=censo_solvent,
                    solvent_model=_solvent_model,
                    nproc=safe_nproc,
                    part_overrides=censo_overrides or None,
                    part_templates=censo_templates or None,
                )
                state.complete_stage(
                    "censo",
                    {"status": "completed", "n_records": len(censo_result.records)},
                )
                stages_completed.append("censo")
                if not censo_result.records:
                    raise RuntimeError("CENSO screening produced no conformer records")

                screening_ranking_csv = _write_screening_ranking(censo_result, mol_dir)

                selected = _select_cumulative_boltzmann(
                    censo_result.records,
                    temperature_k,
                    threshold,
                )
                logger.info(
                    "censo-light (opt on): %d/%d conformers within %.0f%% "
                    "cumulative Boltzmann (screening gtot) → ACP handoff",
                    len(selected),
                    len(censo_result.records),
                    threshold * 100,
                )
                state.set_stage("dft_handoff")
                candidates = _handoff_selected(selected)
                state.complete_stage("dft_handoff", {"status": "completed"})
                stages_completed.append("dft_handoff")

            elif preset in ("censo-light", "censo-zero"):
                # Cheap path (--no-opt): CENSO handles refinement itself.
                # censo-zero preselects the cumulative-population set at the
                # xTB level and restricts CENSO to those frames (-n N).
                logger.info("%s (opt off): CENSO refinement cheap path", preset)
                nconf: int | None = None
                if preset == "censo-zero":
                    passthrough = _xtb_passthrough_result(
                        crest_ensemble_xyz,
                        temperature_k,
                    )
                    preselected = _select_cumulative_boltzmann(
                        passthrough.records,
                        temperature_k,
                        threshold,
                    )
                    nconf = max(1, len(preselected))
                    logger.info(
                        "censo-zero preselection: %d/%d frames within %.0f%% "
                        "cumulative Boltzmann (xTB)",
                        nconf,
                        len(passthrough.records),
                        threshold * 100,
                    )
                state.set_stage("censo")
                censo_result = backend.refine_ensemble(
                    crest_ensemble_xyz,
                    censo_dir,
                    preset=preset,
                    charge=structure.charge,
                    multiplicity=structure.multiplicity,
                    temperature=temperature_k,
                    solvent=censo_solvent,
                    solvent_model=_solvent_model,
                    nproc=safe_nproc,
                    include_refinement=(preset == "censo-light"),
                    nconf=nconf,
                    part_overrides=part_overrides or None,
                    part_templates=part_templates or None,
                )
                state.complete_stage(
                    "censo",
                    {"status": "completed", "n_records": len(censo_result.records)},
                )
                stages_completed.append("censo")
                if not censo_result.records:
                    raise RuntimeError("CENSO refinement produced no conformer records")

                screening_ranking_csv = _write_screening_ranking(censo_result, mol_dir)

                selected = _select_cumulative_boltzmann(
                    censo_result.records,
                    temperature_k,
                    threshold,
                )
                logger.info(
                    "%s (opt off): %d/%d refined conformers within %.0f%% "
                    "cumulative Boltzmann (gtot)",
                    preset,
                    len(selected),
                    len(censo_result.records),
                    threshold * 100,
                )
                candidates = [
                    _censo_record_to_candidate(rec, index=i) for i, rec in enumerate(selected)
                ]

            else:
                # censo-default: full Part0–Part3 + same-level freq + Shermo
                logger.info("censo-default: full CENSO Part0–Part3 funnel")
                state.set_stage("censo")
                censo_result = backend.refine_ensemble(
                    crest_ensemble_xyz,
                    censo_dir,
                    preset=preset,
                    charge=structure.charge,
                    multiplicity=structure.multiplicity,
                    temperature=temperature_k,
                    solvent=censo_solvent,
                    solvent_model=_solvent_model,
                    nproc=safe_nproc,
                    part_overrides=part_overrides or None,
                    part_templates=part_templates or None,
                )
                state.complete_stage(
                    "censo",
                    {"status": "completed", "n_records": len(censo_result.records)},
                )
                stages_completed.append("censo")
                if not censo_result.records:
                    raise RuntimeError("CENSO refinement produced no conformer records")

                state.set_stage("dft_handoff")
                candidates = []
                for i, rec in enumerate(censo_result.records):
                    handoff_dir = mol_dir / "finalDFT" / f"conf_{i:03d}"
                    try:
                        cand = _run_rank1_handoff(
                            cfg,
                            np.asarray(rec.coordinates),
                            list(rec.symbols),
                            structure.charge,
                            structure.multiplicity,
                            handoff_dir,
                            resolved,
                            censo_solvent,
                            _solvent_model,
                            index=i,
                            source=rec.conf_id,
                            sp_energy_precomputed=rec.energy,
                            skip_opt_sp=True,
                        )
                    except RuntimeError as exc:
                        logger.warning(
                            "Same-level freq+Shermo failed for %s (%s) — "
                            "falling back to CENSO gtot",
                            rec.conf_id,
                            exc,
                        )
                        cand = _censo_record_to_candidate(rec, index=i)
                    candidates.append(cand)
                state.complete_stage("dft_handoff", {"status": "completed"})
                stages_completed.append("dft_handoff")

        # ---- Finalization ---------------------------------------------------
        state.set_stage("finalize")
        outputs = _write_final_outputs(candidates, mol_dir, safe_name, temperature_k)
        ensemble = _build_result_ensemble(candidates, structure)
        state.complete_stage("finalize", {"n_conformers": len(candidates)})
        stages_completed.append("finalize")

    except Exception as exc:
        logger.exception("Conformer energy workflow failed: %s", exc)
        state.fail_stage("conformer_energy", str(exc))
        return WorkflowResult(
            status="failed",
            ensemble=StructureEnsemble(records=[]),
            stages_completed=list(stages_completed),
            error=str(exc),
        )

    state.mark_completed()
    metadata: dict[str, Any] = {
        "preset": preset,
        "opt_enabled": opt_enabled,
        "n_conformers": len(candidates),
        "refinement_threshold": float(resolved["refinement_threshold"]),
        "crest_ewin": _resolve_crest_ewin(
            cfg,
            ewin if ewin is not None else resolved["crest_ewin_level"],
        ),
        "crest_skipped": crest_skipped,
        **outputs,
    }
    if screening_ranking_csv:
        metadata["screening_ranking_csv"] = screening_ranking_csv

    return WorkflowResult(
        status="completed",
        ensemble=ensemble,
        stages_completed=stages_completed,
        metadata=metadata,
    )


__all__ = ["run_conformer_energy"]
