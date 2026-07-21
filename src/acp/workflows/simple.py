"""Simple workflows: singlepoint/optimize/frequency/optfreq/optfreqsp (ORCA) + xtb_optimize (xTB)."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.backends.orca import ORCABackend
from acp.backends.xtb import XTBBackend
from acp.core.models import HARTREE_TO_KCAL
from acp.core.state import WorkflowState
from acp.core.utils import ensure_unique_dir
from acp.core.workflow import WorkflowResult
from acp.io.structures import StructureReader
from acp.workflows._helpers import sanitize_job_name
from conformer_search.config import load_config

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".xyz", ".gjf", ".com", ".inp"}

_STAGE_NAMES: dict[str, list[str]] = {
    "singlepoint": ["single_point"],
    "optimize": ["optimize"],
    "frequency": ["frequency"],
    "optfreq": ["opt_freq"],
    "optfreqsp": ["opt_freq", "single_point", "shermo"],
    "xtb_optimize": ["xtb_optimize"],
}


def _check_input(input_source: str) -> Path:
    path = Path(input_source)
    if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported input format '{path.suffix}'. "
            f"Supported: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}"
        )
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {input_source}")
    return path


def _read_input(
    input_source: str,
    charge: int | None,
    multiplicity: int | None,
    name: str | None,
) -> tuple[NDArray[np.float64], list[str], int, int]:
    reader = StructureReader()
    structure = reader.read(input_source, charge=charge, multiplicity=multiplicity, name=name)
    if structure.coordinates is None or structure.symbols is None:
        raise ValueError("Failed to extract coordinates/symbols from input")
    return structure.coordinates, list(structure.symbols), structure.charge, structure.multiplicity


def _write_energy_json(output_dir: Path, energy: float | None, unit: str = "Hartree") -> None:
    data = {"energy": energy, "unit": unit}
    (output_dir / "energy.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_thermo_json(output_dir: Path, thermo: dict[str, float], sp_energy: float) -> None:
    g_sum = thermo.get("g_sum", 0.0)
    data = {
        "sp_energy_hartree": sp_energy,
        "free_energy_hartree": g_sum,
        "free_energy_kcal_mol": g_sum * HARTREE_TO_KCAL,
        "thermal_correction_u_hartree": thermo.get("u_sum", 0.0) - sp_energy if "u_sum" in thermo else 0.0,
        "total_enthalpy_hartree": thermo.get("h_sum", 0.0),
        "total_gibbs_hartree": g_sum,
        "entropy": thermo.get("s_total", 0.0),
        "temperature_k": thermo.get("_temperature", 298.15),
        "pressure_atm": thermo.get("_pressure", 1.0),
        "success": bool(thermo),
    }
    (output_dir / "thermo.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_frequencies_txt(output_dir: Path, frequencies: list[float]) -> None:
    lines = [f"{i+1:6d}  {freq:12.4f}" for i, freq in enumerate(frequencies)]
    (output_dir / "frequencies.txt").write_text("\n".join(lines), encoding="utf-8")


def _write_optimized_xyz(
    output_dir: Path,
    coordinates: NDArray[np.float64],
    symbols: list[str],
) -> None:
    lines = [str(len(symbols)), "Optimized geometry"]
    for sym, coord in zip(symbols, coordinates):
        lines.append(f"{sym:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}")
    (output_dir / "optimized.xyz").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _find_shermo(cfg: dict[str, Any] | None = None) -> str | None:
    """Return the path to the Shermo binary, or None if not found.

    Checks the configured executable path from the config first (matching
    ``energy.py``), then falls back to ``shutil.which`` for PATH lookup.
    Returns the resolved path as a string if found, or None.
    """
    if cfg:
        configured = cfg.get("executables", {}).get("shermo", {}).get("path", "")
        if configured and Path(configured).is_file():
            return configured
    if shutil.which("shermo"):
        return "shermo"
    if shutil.which("Shermo"):
        return "Shermo"
    return None


_SCHEDULER_MARKERS: set[str] = {"inputs", "submit.lsf", ".exit_code", "work", "results"}


def _resolve_output_dir(output_dir: str | Path) -> Path:
    """Resolve output directory.

    If the directory already exists and contains only scheduler/pre-runner
    artifacts (e.g. ``inputs/``, ``submit.lsf``, ``work/``, ``results/``)
    or is empty, reuse it directly.  Otherwise, ensure a unique path so that
    repeated CLI invocations never overwrite previous results.
    """
    base = Path(output_dir).resolve()
    if base.is_dir():
        contents = {p.name for p in base.iterdir()}
        if not contents or contents <= _SCHEDULER_MARKERS:
            base.mkdir(parents=True, exist_ok=True)
            return base
    return ensure_unique_dir(output_dir)


def _calc_subdir(output_root: Path, name: str | None, input_source: str, workflow_name: str) -> Path:
    """Create the per-molecule calculation subdirectory under *output_root*.

    Mirrors the ``output_root / safe_name`` pattern used by the
    ``energy`` and ``conformer`` workflows.  Falls back to *workflow_name*
    only when the input file stem is generic (e.g. scheduler materialised
    ``inputs/input.xyz``) and no explicit name was provided.
    """
    if name:
        safe_name = sanitize_job_name(name)
    else:
        stem = Path(input_source).stem
        if stem in ("", "input"):
            safe_name = sanitize_job_name(workflow_name)
        else:
            safe_name = sanitize_job_name(stem)
    calc_dir = output_root / safe_name
    calc_dir.mkdir(parents=True, exist_ok=True)
    return calc_dir


def _build_backend(config: dict[str, Any]) -> ORCABackend:
    """Create an ORCABackend without passing method_kwargs to constructor.

    Method kwargs (method, basis, dispersion, etc.) flow through the
    calculation method calls only, not the constructor, to avoid the
    silent-drop anti-pattern in the legacy QCInterfaceBase chain.
    """
    return ORCABackend(config=config)


def _init_state(work_dir: Path, workflow_name: str, input_source: str = "") -> WorkflowState:
    """Initialize scheduler-visible WorkflowState with stage declarations."""
    stage_names = _STAGE_NAMES.get(workflow_name, [])
    state = WorkflowState(work_dir=work_dir, job_name=workflow_name)
    state.initialize(input_source=input_source, stage_names=stage_names)
    return state


def _build_method_kwargs(raw_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Filter method kwargs: strip empty strings, convert ri_approximation to route_extras."""
    kwargs: dict[str, Any] = {}
    ri_approx = raw_kwargs.get("ri_approximation")
    route_extras: list[str] = []
    extras = raw_kwargs.get("route_extras")
    if isinstance(extras, str) and extras.strip():
        route_extras = [x.strip() for x in extras.split(",") if x.strip()]
    elif isinstance(extras, list):
        route_extras = [str(x) for x in extras]
    if ri_approx and ri_approx not in ("none", ""):
        route_extras.append(str(ri_approx))
    if route_extras:
        kwargs["route_extras"] = route_extras

    for key, val in raw_kwargs.items():
        if key in ("route_extras", "ri_approximation", "opt_convergence"):
            continue
        if key == "geom_maxiter":
            if val is not None:
                kwargs[key] = val
            continue
        if val is None or val == "" or val == "none":
            continue
        kwargs[key] = val

    # Gas-phase override: when solvent_model is "none" or absent in the raw
    # kwargs, the user explicitly chose gas phase. We must pass solvent=None
    # and solvent_model="none" through to the backend so that ORCABackend's
    # setdefault() does NOT fall back to theory.optimization.solvent from
    # the config (e.g. ~/.conformer_search.yaml may set solvent: methanol).
    raw_sm = raw_kwargs.get("solvent_model")
    if raw_sm is None or str(raw_sm).strip().lower() in ("", "none"):
        kwargs.setdefault("solvent_model", "none")
        kwargs.setdefault("solvent", None)

    return kwargs


_GFN_DISPLAY_TO_INT: dict[str, int] = {
    "GFN0-xTB": 0, "GFN1-xTB": 1, "GFN2-xTB": 2,
    "0": 0, "1": 1, "2": 2,
}


def _normalize_gfn(val: Any) -> int | None:
    """Map catalog display names (GFN2-xTB) or numeric strings to integer GFN level."""
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        return _GFN_DISPLAY_TO_INT.get(val.strip())
    return None


def _build_xtb_backend(config: dict[str, Any], gfn_level: int | None = None) -> XTBBackend:
    """Create an XTBBackend, optionally overriding the GFN level."""
    kwargs: dict[str, Any] = {}
    if gfn_level is not None:
        kwargs["gfn_level"] = gfn_level
    return XTBBackend(config=config, **kwargs)


def _build_xtb_method_kwargs(raw_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Filter xTB method kwargs: pass through opt_level, solvent, max_steps.

    Note: gfn is extracted separately and passed to the XTBBackend constructor,
    not through optimize() kwargs (XTBInterface reads gfn_level from __init__).
    """
    kwargs: dict[str, Any] = {}
    for key in ("opt_level", "solvent", "solvent_model", "max_steps"):
        val = raw_kwargs.get(key)
        if val is None or val == "" or val == "none":
            continue
        kwargs[key] = val

    raw_sm = raw_kwargs.get("solvent_model")
    if raw_sm is None or str(raw_sm).strip().lower() in ("", "none"):
        kwargs.setdefault("solvent_model", "none")
        kwargs.setdefault("solvent", None)

    return kwargs


# ---------------------------------------------------------------------------
# Workflow entry points
# ---------------------------------------------------------------------------


def run_singlepoint(
    input_source: str,
    output_dir: str | Path = "./singlepoint_output",
    config: dict[str, Any] | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    method_kwargs: dict[str, Any] | None = None,
) -> WorkflowResult:
    cfg = load_config(overrides=config) if config else load_config()
    out = _resolve_output_dir(output_dir)
    _check_input(input_source)
    coords, symbols, chg, mult = _read_input(input_source, charge, multiplicity, name)
    calc_dir = _calc_subdir(out, name, input_source, "singlepoint")
    state = _init_state(calc_dir, "singlepoint", input_source)

    kwargs = _build_method_kwargs(method_kwargs or {})
    backend = _build_backend(cfg)
    state.set_stage("single_point")
    result = backend.single_point(coords, symbols, charge=chg, multiplicity=mult, output_dir=calc_dir, **kwargs)
    if not result.success:
        state.fail_stage("single_point", result.error_message or "SP calculation failed")
        return WorkflowResult(status="failed", error=result.error_message or "SP calculation failed")

    if result.energy is None:
        state.fail_stage("single_point", "SP returned no energy")
        return WorkflowResult(status="failed", error="SP calculation returned no energy")
    state.complete_stage("single_point")
    _write_energy_json(calc_dir, result.energy)
    state.mark_completed()
    return WorkflowResult(
        status="completed",
        metadata={"output_dir": str(calc_dir), "energy": result.energy, "unit": "Hartree"},
    )


def run_optimize(
    input_source: str,
    output_dir: str | Path = "./optimize_output",
    config: dict[str, Any] | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    method_kwargs: dict[str, Any] | None = None,
) -> WorkflowResult:
    cfg = load_config(overrides=config) if config else load_config()
    out = _resolve_output_dir(output_dir)
    _check_input(input_source)
    coords, symbols, chg, mult = _read_input(input_source, charge, multiplicity, name)
    calc_dir = _calc_subdir(out, name, input_source, "optimize")
    state = _init_state(calc_dir, "optimize", input_source)

    kwargs = _build_method_kwargs(method_kwargs or {})
    backend = _build_backend(cfg)
    state.set_stage("optimize")
    result = backend.optimize(coords, symbols, charge=chg, multiplicity=mult, output_dir=calc_dir, **kwargs)
    if not result.success:
        state.fail_stage("optimize", result.error_message or "Optimization failed")
        return WorkflowResult(status="failed", error=result.error_message or "Optimization failed")

    state.complete_stage("optimize")
    if result.coordinates is not None:
        _write_optimized_xyz(calc_dir, result.coordinates, result.symbols or symbols)
    if result.energy is not None:
        _write_energy_json(calc_dir, result.energy)
    state.mark_completed()
    return WorkflowResult(
        status="completed",
        metadata={
            "output_dir": str(calc_dir),
            "energy": result.energy,
            "converged": result.converged,
        },
    )


def run_xtb_optimize(
    input_source: str,
    output_dir: str | Path = "./xtb_optimize_output",
    config: dict[str, Any] | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    method_kwargs: dict[str, Any] | None = None,
) -> WorkflowResult:
    cfg = load_config(overrides=config) if config else load_config()
    out = _resolve_output_dir(output_dir)
    _check_input(input_source)
    coords, symbols, chg, mult = _read_input(input_source, charge, multiplicity, name)
    calc_dir = _calc_subdir(out, name, input_source, "xtb_optimize")
    state = _init_state(calc_dir, "xtb_optimize", input_source)

    kwargs = _build_xtb_method_kwargs(method_kwargs or {})
    gfn_level = _normalize_gfn((method_kwargs or {}).get("gfn"))
    backend = _build_xtb_backend(cfg, gfn_level=gfn_level)
    state.set_stage("xtb_optimize")
    result = backend.optimize(coords, symbols, charge=chg, multiplicity=mult, output_dir=calc_dir, **kwargs)
    if not result.success:
        state.fail_stage("xtb_optimize", result.error_message or "xTB optimization failed")
        return WorkflowResult(status="failed", error=result.error_message or "xTB optimization failed")

    state.complete_stage("xtb_optimize")
    if result.coordinates is not None:
        _write_optimized_xyz(calc_dir, result.coordinates, result.symbols or symbols)
    if result.energy is not None:
        _write_energy_json(calc_dir, result.energy)
    state.mark_completed()
    return WorkflowResult(
        status="completed",
        metadata={
            "output_dir": str(calc_dir),
            "energy": result.energy,
            "converged": result.converged,
        },
    )


def run_frequency(
    input_source: str,
    output_dir: str | Path = "./frequency_output",
    config: dict[str, Any] | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    method_kwargs: dict[str, Any] | None = None,
) -> WorkflowResult:
    cfg = load_config(overrides=config) if config else load_config()
    out = _resolve_output_dir(output_dir)
    _check_input(input_source)
    coords, symbols, chg, mult = _read_input(input_source, charge, multiplicity, name)
    calc_dir = _calc_subdir(out, name, input_source, "frequency")
    state = _init_state(calc_dir, "frequency", input_source)

    kwargs = _build_method_kwargs(method_kwargs or {})
    backend = _build_backend(cfg)
    state.set_stage("frequency")
    result = backend.frequency(coords, symbols, charge=chg, multiplicity=mult, output_dir=calc_dir, **kwargs)
    if not result.success:
        state.fail_stage("frequency", result.error_message or "Frequency calculation failed")
        return WorkflowResult(status="failed", error=result.error_message or "Frequency calculation failed")

    state.complete_stage("frequency")
    freqs = result.frequencies or []
    if freqs:
        _write_frequencies_txt(calc_dir, freqs)
    state.mark_completed()
    return WorkflowResult(
        status="completed",
        metadata={"output_dir": str(calc_dir), "n_frequencies": len(freqs), "has_frequencies": result.has_frequencies},
    )


def run_optfreq(
    input_source: str,
    output_dir: str | Path = "./optfreq_output",
    config: dict[str, Any] | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    method_kwargs: dict[str, Any] | None = None,
) -> WorkflowResult:
    cfg = load_config(overrides=config) if config else load_config()
    out = _resolve_output_dir(output_dir)
    _check_input(input_source)
    coords, symbols, chg, mult = _read_input(input_source, charge, multiplicity, name)
    calc_dir = _calc_subdir(out, name, input_source, "optfreq")
    state = _init_state(calc_dir, "optfreq", input_source)

    kwargs = _build_method_kwargs(method_kwargs or {})
    backend = _build_backend(cfg)
    state.set_stage("opt_freq")
    result = backend.opt_freq(coords, symbols, charge=chg, multiplicity=mult, output_dir=calc_dir, **kwargs)
    if not result.success:
        state.fail_stage("opt_freq", result.error_message or "Opt+Freq calculation failed")
        return WorkflowResult(status="failed", error=result.error_message or "Opt+Freq calculation failed")

    state.complete_stage("opt_freq")
    if result.coordinates is not None:
        _write_optimized_xyz(calc_dir, result.coordinates, result.symbols or symbols)
    freqs = result.frequencies or []
    if freqs:
        _write_frequencies_txt(calc_dir, freqs)
    if result.energy is not None:
        _write_energy_json(calc_dir, result.energy)
    state.mark_completed()
    return WorkflowResult(
        status="completed",
        metadata={
            "output_dir": str(calc_dir),
            "energy": result.energy,
            "n_frequencies": len(freqs),
            "converged": result.converged,
        },
    )


def run_optfreqsp(
    input_source: str,
    output_dir: str | Path = "./optfreqsp_output",
    config: dict[str, Any] | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    optfreq_kwargs: dict[str, Any] | None = None,
    sp_kwargs: dict[str, Any] | None = None,
    thermo_kwargs: dict[str, Any] | None = None,
) -> WorkflowResult:
    cfg = load_config(overrides=config) if config else load_config()
    shermo_bin = _find_shermo(cfg)
    if not shermo_bin:
        return WorkflowResult(status="failed", error="Shermo binary not found")
    out = _resolve_output_dir(output_dir)
    _check_input(input_source)
    coords, symbols, chg, mult = _read_input(input_source, charge, multiplicity, name)
    calc_dir = _calc_subdir(out, name, input_source, "optfreqsp")
    state = _init_state(calc_dir, "optfreqsp", input_source)

    # --- Stage 1: Opt+Freq ---
    opt_kwargs = _build_method_kwargs(optfreq_kwargs or {})
    backend = _build_backend(cfg)
    state.set_stage("opt_freq")
    optfreq_result = backend.opt_freq(coords, symbols, charge=chg, multiplicity=mult, output_dir=calc_dir, **opt_kwargs)
    if not optfreq_result.success:
        state.fail_stage("opt_freq", optfreq_result.error_message or "Opt+Freq stage failed")
        return WorkflowResult(status="failed", error=optfreq_result.error_message or "Opt+Freq stage failed")
    state.complete_stage("opt_freq")

    opt_coords = optfreq_result.coordinates if optfreq_result.coordinates is not None else coords
    freqs = optfreq_result.frequencies or []
    if optfreq_result.coordinates is not None:
        _write_optimized_xyz(calc_dir, optfreq_result.coordinates, optfreq_result.symbols or symbols)
    if freqs:
        _write_frequencies_txt(calc_dir, freqs)

    # --- Stage 2: Single Point ---
    sp_flat = _build_method_kwargs(sp_kwargs or {})
    sp_backend = _build_backend(cfg)
    state.set_stage("single_point")
    sp_result = sp_backend.single_point(opt_coords, symbols, charge=chg, multiplicity=mult, output_dir=calc_dir, **sp_flat)
    if not sp_result.success:
        state.fail_stage("single_point", sp_result.error_message or "SP stage failed")
        return WorkflowResult(status="failed", error=sp_result.error_message or "SP stage failed")
    if sp_result.energy is None:
        state.fail_stage("single_point", "SP stage returned no energy")
        return WorkflowResult(status="failed", error="SP stage returned no energy")
    sp_energy = sp_result.energy
    state.complete_stage("single_point")

    log_file = optfreq_result.log_file
    if log_file is None:
        state.fail_stage("shermo", "ORCA log file path not available for Shermo")
        return WorkflowResult(status="failed", error="ORCA log file path not available for Shermo")
    if not log_file.exists():
        state.fail_stage("shermo", f"ORCA log file not found: {log_file}")
        return WorkflowResult(status="failed", error=f"ORCA log file not found: {log_file}")

    # --- Stage 3: Shermo ---
    th = thermo_kwargs or {}
    from acp.backends.external import run_shermo
    state.set_stage("shermo")
    thermo = run_shermo(
        freq_output=log_file,
        sp_energy=sp_energy,
        output_dir=calc_dir,
        shermo_bin=shermo_bin,
        temperature_k=th.get("temperature", 298.15),
        pressure_atm=th.get("pressure", 1.0),
        scl_zpe=th.get("scale_factor", 0.9905),
    )

    if thermo:
        thermo["_temperature"] = th.get("temperature", 298.15)
        thermo["_pressure"] = th.get("pressure", 1.0)
        state.complete_stage("shermo")
    else:
        state.fail_stage("shermo", "Shermo returned no output")

    _write_thermo_json(calc_dir, thermo or {}, sp_energy)
    _write_energy_json(calc_dir, sp_energy)
    state.mark_completed()

    return WorkflowResult(
        status="completed",
        metadata={
            "output_dir": str(calc_dir),
            "sp_energy": sp_energy,
            "thermo_success": bool(thermo),
            "n_frequencies": len(freqs),
            "free_energy_hartree": (thermo or {}).get("g_sum"),
        },
    )


__all__ = [
    "run_singlepoint",
    "run_optimize",
    "run_frequency",
    "run_optfreq",
    "run_optfreqsp",
]
