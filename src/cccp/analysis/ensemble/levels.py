"""Level/config resolution + conformer tagging (retired-engine closure, T24).

Verbatim relocation of the pure ``acp.workflows.energy_shared`` level science
into the CCCP analysis domain.  The historical ACP path re-exports the same
objects.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from cccp.qc.hessian_policy import normalize_recalc_hess
from cccp.qc.translation import render_censo_template_lines

logger = logging.getLogger(__name__)

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
    screening_template_lines = render_censo_template_lines(screening_extras)
    refinement_template_lines = render_censo_template_lines(sp_route_extras)

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


_TAG_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def conformer_tag(source: str | None, index: int) -> str:
    """Stable filesystem tag identifying one conformer across all artifacts.

    Derived from the screening ``conf_id`` (e.g. ``CONF12``) so the DFT stage
    directory, calculation file basenames, ``conformer_thermo.csv`` source
    column, xyz frame ``conf_id=`` markers and Boltzmann-table rows all carry
    the same name regardless of the (post-DFT) rank order.  Falls back to the
    legacy positional ``conf_NNN`` when no screening id is available.
    """
    cleaned = _TAG_UNSAFE_RE.sub("_", str(source).strip()) if source else ""
    return cleaned if cleaned and cleaned != "rank1" else f"conf_{index:03d}"

__all__ = [
    "_base_route_extras",
    "_solvent_from_level",
    "conformer_tag",
    "resolve_levels",
]
