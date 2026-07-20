"""Simple ORCA workflows: singlepoint/optimize/frequency/optfreq/optfreqsp."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from acp.backends.orca import ORCABackend
from acp.core.models import HARTREE_TO_KCAL
from acp.core.state import WorkflowState
from acp.core.utils import ensure_unique_dir
from acp.core.workflow import WorkflowResult
from acp.io.structures import StructureReader
from conformer_search.config import load_config

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".xyz", ".gjf", ".com", ".inp"}


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


def _read_input(input_source: str, charge: int | None, multiplicity: int | None, name: str | None, output_dir: Path) -> tuple:
    reader = StructureReader()
    structure = reader.read(input_source, charge=charge, multiplicity=multiplicity, name=name)
    if structure.coordinates is None or structure.symbols is None:
        raise ValueError("Failed to extract coordinates/symbols from input")
    return structure.coordinates, list(structure.symbols), structure.charge, structure.multiplicity


def _write_energy_json(output_dir: Path, energy: float, unit: str = "Hartree") -> None:
    data = {"energy": energy, "unit": unit}
    (output_dir / "energy.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_thermo_json(output_dir: Path, thermo: dict[str, float], sp_energy: float) -> None:
    g_sum = thermo.get("g_sum", 0.0)
    free_energy = g_sum
    data = {
        "sp_energy_hartree": sp_energy,
        "free_energy_hartree": free_energy,
        "free_energy_kcal_mol": free_energy * HARTREE_TO_KCAL,
        "zpe_hartree": thermo.get("u_sum", 0.0) - sp_energy if "u_sum" in thermo else 0.0,
        "enthalpy_hartree": thermo.get("h_sum", 0.0),
        "gibbs_hartree": g_sum,
        "entropy": thermo.get("s_total"),
        "temperature_k": thermo.get("_temperature", 298.15),
        "pressure_atm": thermo.get("_pressure", 1.0),
        "success": bool(thermo),
    }
    (output_dir / "thermo.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_frequencies_txt(output_dir: Path, frequencies: list[float]) -> None:
    lines = [f"{i+1:6d}  {freq:12.4f}" for i, freq in enumerate(frequencies)]
    (output_dir / "frequencies.txt").write_text("\n".join(lines), encoding="utf-8")


def _write_optimized_xyz(output_dir: Path, coordinates, symbols) -> None:
    lines = [str(len(symbols)), "Optimized geometry"]
    for sym, coord in zip(symbols, coordinates):
        lines.append(f"{sym:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}")
    (output_dir / "optimized.xyz").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _find_shermo() -> bool:
    return shutil.which("shermo") is not None or shutil.which("Shermo") is not None


def _build_backend(config: dict[str, Any], method_kwargs: dict[str, Any]) -> ORCABackend:
    return ORCABackend(config=config, **method_kwargs)


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
    out = ensure_unique_dir(output_dir)
    _check_input(input_source)
    coords, symbols, chg, mult = _read_input(input_source, charge, multiplicity, name, out)

    backend = _build_backend(cfg, method_kwargs or {})
    result = backend.single_point(coords, symbols, charge=chg, multiplicity=mult, output_dir=out)

    if not result.success:
        return WorkflowResult(status="failed", error=result.error_message or "SP calculation failed")

    _write_energy_json(out, result.energy)
    return WorkflowResult(
        status="completed",
        metadata={"output_dir": str(out), "energy": result.energy, "unit": "Hartree"},
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
    out = ensure_unique_dir(output_dir)
    _check_input(input_source)
    coords, symbols, chg, mult = _read_input(input_source, charge, multiplicity, name, out)

    backend = _build_backend(cfg, method_kwargs or {})
    result = backend.optimize(coords, symbols, charge=chg, multiplicity=mult, output_dir=out)

    if not result.success:
        return WorkflowResult(status="failed", error=result.error_message or "Optimization failed")

    if result.coordinates is not None:
        _write_optimized_xyz(out, result.coordinates, result.symbols or symbols)
    _write_energy_json(out, result.energy)
    return WorkflowResult(
        status="completed",
        metadata={
            "output_dir": str(out),
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
    out = ensure_unique_dir(output_dir)
    _check_input(input_source)
    coords, symbols, chg, mult = _read_input(input_source, charge, multiplicity, name, out)

    backend = _build_backend(cfg, method_kwargs or {})
    result = backend.frequency(coords, symbols, charge=chg, multiplicity=mult, output_dir=out)

    if not result.success:
        return WorkflowResult(status="failed", error=result.error_message or "Frequency calculation failed")

    freqs = result.frequencies or []
    if freqs:
        _write_frequencies_txt(out, freqs)
    return WorkflowResult(
        status="completed",
        metadata={"output_dir": str(out), "n_frequencies": len(freqs), "has_frequencies": result.has_frequencies},
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
    out = ensure_unique_dir(output_dir)
    _check_input(input_source)
    coords, symbols, chg, mult = _read_input(input_source, charge, multiplicity, name, out)

    backend = _build_backend(cfg, method_kwargs or {})
    result = backend.opt_freq(coords, symbols, charge=chg, multiplicity=mult, output_dir=out)

    if not result.success:
        return WorkflowResult(status="failed", error=result.error_message or "Opt+Freq calculation failed")

    if result.coordinates is not None:
        _write_optimized_xyz(out, result.coordinates, result.symbols or symbols)
    freqs = result.frequencies or []
    if freqs:
        _write_frequencies_txt(out, freqs)
    if result.energy is not None:
        _write_energy_json(out, result.energy)
    return WorkflowResult(
        status="completed",
        metadata={
            "output_dir": str(out),
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
    if not _find_shermo():
        return WorkflowResult(status="failed", error="Shermo binary not found in PATH")

    cfg = load_config(overrides=config) if config else load_config()
    out = ensure_unique_dir(output_dir)
    _check_input(input_source)
    coords, symbols, chg, mult = _read_input(input_source, charge, multiplicity, name, out)

    backend = _build_backend(cfg, optfreq_kwargs or {})
    optfreq_result = backend.opt_freq(coords, symbols, charge=chg, multiplicity=mult, output_dir=out)
    if not optfreq_result.success:
        return WorkflowResult(status="failed", error=optfreq_result.error_message or "Opt+Freq stage failed")

    opt_coords = optfreq_result.coordinates if optfreq_result.coordinates is not None else coords
    freqs = optfreq_result.frequencies or []
    if optfreq_result.coordinates is not None:
        _write_optimized_xyz(out, optfreq_result.coordinates, optfreq_result.symbols or symbols)
    if freqs:
        _write_frequencies_txt(out, freqs)

    sp_backend = _build_backend(cfg, sp_kwargs or {})
    sp_result = sp_backend.single_point(opt_coords, symbols, charge=chg, multiplicity=mult, output_dir=out)
    if not sp_result.success:
        return WorkflowResult(status="failed", error=sp_result.error_message or "SP stage failed")

    sp_energy = sp_result.energy if sp_result.energy is not None else 0.0
    log_file = optfreq_result.log_file
    if log_file is None:
        return WorkflowResult(status="failed", error="ORCA log file path not available for Shermo")

    from acp.backends.external import run_shermo
    thermo = run_shermo(
        freq_output=log_file,
        sp_energy=sp_energy,
        output_dir=out,
        temperature_k=(thermo_kwargs or {}).get("temperature", 298.15),
        pressure_atm=(thermo_kwargs or {}).get("pressure", 1.0),
        scl_zpe=(thermo_kwargs or {}).get("scale_factor", 0.9905),
    )

    if thermo:
        thermo["_temperature"] = (thermo_kwargs or {}).get("temperature", 298.15)
        thermo["_pressure"] = (thermo_kwargs or {}).get("pressure", 1.0)

    context: dict[str, Any] = {
        "opt_coords": opt_coords,
        "opt_log_path": str(log_file) if log_file else None,
        "sp_energy": sp_energy,
        "frequencies": freqs,
        "thermo": thermo or {},
    }
    _write_thermo_json(out, thermo or {}, sp_energy)
    _write_energy_json(out, sp_energy)

    return WorkflowResult(
        status="completed",
        metadata={
            "output_dir": str(out),
            "sp_energy": sp_energy,
            "thermo_success": bool(thermo),
            "n_frequencies": len(freqs),
            "free_energy_hartree": (thermo or {}).get("g_sum"),
        },
        ensemble=None,
    )


__all__ = [
    "run_singlepoint",
    "run_optimize",
    "run_frequency",
    "run_optfreq",
    "run_optfreqsp",
]
