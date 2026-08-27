"""Simple workflow adapters backed by calculation plans."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.calculations import (
    CalculationPlan,
    CalculationPlanExecutor,
    CalculationRequest,
    CalculationResult,
    CalculationStep,
    StepKind,
    StructureArtifact,
)
from acp.calculations.plans import build_simple_plan
from acp.calculations.primitives.scan import run_scan
from acp.core.models import HARTREE_TO_KCAL
from acp.core.utils import ensure_unique_dir
from acp.core.workflow import WorkflowResult
from acp.io.structures import StructureReader
from acp.workflows._helpers import resolve_task_output_root, sanitize_job_name
from cccp.config import load_config

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".xyz", ".gjf", ".com", ".inp"}
_DEFAULT_SCALE_FACTOR = 0.9905


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
    coordinates: NDArray[np.float64] = np.asarray(structure.coordinates, dtype=np.float64)
    return coordinates, list(structure.symbols), structure.charge, structure.multiplicity


def _write_energy_json(output_dir: Path, energy: float | None, unit: str = "Hartree") -> None:
    data = {"energy": energy, "unit": unit}
    (output_dir / "energy.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_frequencies_txt(output_dir: Path, frequencies: list[float]) -> None:
    lines = [f"{index + 1:6d}  {frequency:12.4f}" for index, frequency in enumerate(frequencies)]
    (output_dir / "frequencies.txt").write_text("\n".join(lines), encoding="utf-8")


def _write_optimized_xyz(
    output_dir: Path,
    coordinates: NDArray[np.float64],
    symbols: list[str],
) -> None:
    lines = [str(len(symbols)), "Optimized geometry"]
    for symbol, coordinate in zip(symbols, coordinates, strict=True):
        lines.append(
            f"{symbol:2s} {coordinate[0]:15.10f} {coordinate[1]:15.10f} {coordinate[2]:15.10f}"
        )
    (output_dir / "optimized.xyz").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_thermo_json(output_dir: Path, thermo: Mapping[str, Any], sp_energy: float) -> None:
    g_sum = thermo.get("g_sum", 0.0)
    u_sum = thermo.get("u_sum", 0.0)
    data = {
        "sp_energy_hartree": sp_energy,
        "free_energy_hartree": g_sum,
        "free_energy_kcal_mol": g_sum * HARTREE_TO_KCAL,
        "thermal_correction_u_hartree": u_sum - sp_energy if "u_sum" in thermo else 0.0,
        "total_enthalpy_hartree": thermo.get("h_sum", 0.0),
        "total_gibbs_hartree": g_sum,
        "entropy": thermo.get("s_total", 0.0),
        "temperature_k": thermo.get("_temperature", 298.15),
        "pressure_atm": thermo.get("_pressure", 1.0),
        "success": bool(thermo),
    }
    (output_dir / "thermo.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


_SCHEDULER_MARKERS: set[str] = {
    "submit.lsf",
    ".exit_code",
    "events.jsonl",
    "job.json",
    "stdout.log",
    "stderr.log",
    "mechanism_config.json",
    "metrics.json",
    "WORK",
    "RESULT",
    "input.xyz",
    "task.json",
    "input_source.json",
}


def _resolve_output_dir(output_dir: str | Path) -> Path:
    base = Path(output_dir).resolve()
    if base.is_dir():
        contents = {path.name for path in base.iterdir()}
        if not contents or contents <= _SCHEDULER_MARKERS:
            base.mkdir(parents=True, exist_ok=True)
            return base
    return ensure_unique_dir(output_dir)


def _calc_subdir(
    output_root: Path, name: str | None, input_source: str, workflow_name: str
) -> Path:
    if name:
        safe_name = sanitize_job_name(name)
    else:
        stem = Path(input_source).stem
        safe_name = sanitize_job_name(workflow_name if stem in ("", "input") else stem)
    calc_dir = resolve_task_output_root(output_root, safe_name)
    calc_dir.mkdir(parents=True, exist_ok=True)
    return calc_dir


_GFN_DISPLAY_TO_INT: dict[str, int] = {
    "GFN0-xTB": 0,
    "GFN1-xTB": 1,
    "GFN2-xTB": 2,
    "0": 0,
    "1": 1,
    "2": 2,
}


def _normalize_gfn(val: Any) -> int | None:
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        return _GFN_DISPLAY_TO_INT.get(val.strip())
    return None


def _build_method_kwargs(raw_kwargs: Mapping[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    ri_approx = raw_kwargs.get("ri_approximation")
    dispersion = raw_kwargs.get("dispersion")
    extras = raw_kwargs.get("route_extras")
    route_extras: list[str] = []
    if isinstance(extras, str) and extras.strip():
        route_extras = [value.strip() for value in extras.split(",") if value.strip()]
    elif isinstance(extras, list):
        route_extras = [str(value) for value in extras]
    if ri_approx and ri_approx not in ("none", ""):
        route_extras.append(str(ri_approx))
    if dispersion and str(dispersion).strip().lower() not in ("", "none"):
        route_extras.append(str(dispersion).strip().upper())
    if route_extras:
        kwargs["route_extras"] = route_extras

    for key, value in raw_kwargs.items():
        if key in ("route_extras", "ri_approximation", "dispersion", "opt_convergence", "method"):
            continue
        if key == "geom_maxiter":
            if value is not None:
                kwargs[key] = value
            continue
        if value is None or value == "" or value == "none":
            continue
        kwargs[key] = value

    raw_solvent_model = raw_kwargs.get("solvent_model")
    if raw_solvent_model is None or str(raw_solvent_model).strip().lower() in ("", "none"):
        kwargs.setdefault("solvent_model", "none")
        kwargs.setdefault("solvent", None)
    return kwargs


def _build_xtb_method_kwargs(raw_kwargs: Mapping[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for key in ("opt_level", "solvent", "solvent_model", "max_steps"):
        value = raw_kwargs.get(key)
        if value is None or value == "" or value == "none":
            continue
        kwargs[key] = value
    raw_solvent_model = raw_kwargs.get("solvent_model")
    if raw_solvent_model is None or str(raw_solvent_model).strip().lower() in ("", "none"):
        kwargs.setdefault("solvent_model", "none")
        kwargs.setdefault("solvent", None)
    return kwargs


@dataclass(frozen=True, slots=True)
class _AdapterContext:
    input_source: str
    config: dict[str, Any]
    coordinates: NDArray[np.float64]
    symbols: list[str]
    charge: int
    multiplicity: int
    artifact: StructureArtifact


@dataclass(frozen=True, slots=True)
class _RequestDefinition:
    workflow: str
    backend: str
    method_kwargs: Mapping[str, Any]


def _context(
    input_source: str,
    config: dict[str, Any] | None,
    charge: int | None,
    multiplicity: int | None,
    name: str | None,
) -> _AdapterContext:
    cfg = load_config(overrides=config) if config else load_config()
    input_path = _check_input(input_source)
    coordinates, symbols, effective_charge, effective_multiplicity = _read_input(
        input_source, charge, multiplicity, name
    )
    return _AdapterContext(
        input_source=input_source,
        config=cfg,
        coordinates=coordinates,
        symbols=symbols,
        charge=effective_charge,
        multiplicity=effective_multiplicity,
        artifact=StructureArtifact(path=input_path, elements=symbols, source="simple"),
    )


def _orca_default_method(config: Mapping[str, Any]) -> str:
    theory = config.get("theory", {})
    if isinstance(theory, dict):
        optimization = theory.get("optimization", {})
        if isinstance(optimization, dict):
            method = optimization.get("method")
            if method:
                return str(method)
    return "r2SCAN-3c"


def _build_request(context: _AdapterContext, definition: _RequestDefinition) -> CalculationRequest:
    raw_kwargs = dict(definition.method_kwargs)
    if definition.backend == "xtb":
        resources = _build_xtb_method_kwargs(raw_kwargs)
        gfn_level = _normalize_gfn(raw_kwargs.get("gfn"))
        if gfn_level is not None:
            resources["gfn_level"] = gfn_level
        method = str(raw_kwargs.get("method") or "")
    else:
        resources = _build_method_kwargs(raw_kwargs)
        method = str(raw_kwargs.get("method") or _orca_default_method(context.config))

    resources.update(
        {
            "backend": definition.backend,
            "config": context.config,
            "coordinates": context.coordinates.tolist(),
            "symbols": list(context.symbols),
            "charge": context.charge,
            "multiplicity": context.multiplicity,
        }
    )
    return CalculationRequest(
        input_artifact=context.artifact,
        method=method,
        resources=resources,
        workflow=definition.workflow,
        profile="default",
    )


def _step_spec(request: CalculationRequest) -> dict[str, Any]:
    spec = dict(request.resources)
    spec["method"] = request.method
    return spec


def _build_plan(
    first_kind: StepKind,
    requests: Sequence[CalculationRequest],
    kinds: Sequence[StepKind],
) -> CalculationPlan:
    base_plan = build_simple_plan(first_kind, requests[0])
    first_step = next(step for step in base_plan.steps if isinstance(step, CalculationStep))
    steps: list[CalculationStep | Mapping[str, Any]] = [
        CalculationStep(
            kind=first_step.kind,
            mode=first_step.mode,
            spec=_step_spec(requests[0]),
        )
    ]
    steps.extend(
        CalculationStep(kind=kind, spec=_step_spec(request))
        for kind, request in zip(kinds[1:], requests[1:], strict=True)
    )
    return CalculationPlan(
        workflow=base_plan.workflow,
        profile=base_plan.profile,
        items=base_plan.items,
        steps=steps,
    )


def _workflow_result(execution: Any, calc_dir: Path) -> WorkflowResult:
    metadata: dict[str, Any] = {"output_dir": str(calc_dir)}
    completed_stages: list[str] = []
    for step_state in execution.step_states:
        if step_state.status in {"completed", "skipped"}:
            completed_stages.append(step_state.kind.value)
        result: CalculationResult | None = step_state.result
        if result is None:
            continue
        if step_state.kind is StepKind.OPTIMIZE:
            metadata["energy"] = result.energy
            metadata["converged"] = result.coords is not None and result.status == "completed"
        elif step_state.kind is StepKind.FREQUENCY:
            metadata["n_frequencies"] = len(result.frequencies)
            metadata["has_frequencies"] = bool(result.frequencies)
        elif step_state.kind is StepKind.SINGLEPOINT:
            metadata["sp_energy"] = result.energy
        elif step_state.kind is StepKind.THERMOCHEMISTRY:
            metadata["thermo_success"] = result.status == "completed"
            free_energy = result.metadata.get("gibbs_hartree")
            if free_energy is None:
                free_energy = result.metadata.get("free_energy_hartree")
            metadata["free_energy_hartree"] = free_energy

    errors = "; ".join(execution.errors) if execution.errors else None
    return WorkflowResult(
        status=execution.status,
        stages_completed=completed_stages,
        error=errors,
        metadata=metadata,
    )


def _execute(plan: CalculationPlan, calc_dir: Path) -> WorkflowResult:
    execution = CalculationPlanExecutor().execute(plan, calc_dir)
    return _workflow_result(execution, calc_dir)


def run_singlepoint(
    input_source: str,
    output_dir: str | Path = "./singlepoint_output",
    config: dict[str, Any] | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    method_kwargs: dict[str, Any] | None = None,
) -> WorkflowResult:
    context = _context(input_source, config, charge, multiplicity, name)
    calc_dir = _calc_subdir(_resolve_output_dir(output_dir), name, input_source, "singlepoint")
    request = _build_request(
        context,
        _RequestDefinition("singlepoint", "orca", method_kwargs or {}),
    )
    return _execute(_build_plan(StepKind.SINGLEPOINT, [request], [StepKind.SINGLEPOINT]), calc_dir)


def run_optimize(
    input_source: str,
    output_dir: str | Path = "./optimize_output",
    config: dict[str, Any] | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    method_kwargs: dict[str, Any] | None = None,
) -> WorkflowResult:
    context = _context(input_source, config, charge, multiplicity, name)
    calc_dir = _calc_subdir(_resolve_output_dir(output_dir), name, input_source, "optimize")
    request = _build_request(
        context,
        _RequestDefinition("optimize", "orca", method_kwargs or {}),
    )
    return _execute(_build_plan(StepKind.OPTIMIZE, [request], [StepKind.OPTIMIZE]), calc_dir)


def run_xtb_optimize(
    input_source: str,
    output_dir: str | Path = "./xtb_optimize_output",
    config: dict[str, Any] | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    method_kwargs: dict[str, Any] | None = None,
) -> WorkflowResult:
    context = _context(input_source, config, charge, multiplicity, name)
    calc_dir = _calc_subdir(_resolve_output_dir(output_dir), name, input_source, "xtb_optimize")
    request = _build_request(
        context,
        _RequestDefinition("xtb_optimize", "xtb", method_kwargs or {}),
    )
    return _execute(_build_plan(StepKind.OPTIMIZE, [request], [StepKind.OPTIMIZE]), calc_dir)


def run_frequency(
    input_source: str,
    output_dir: str | Path = "./frequency_output",
    config: dict[str, Any] | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    method_kwargs: dict[str, Any] | None = None,
) -> WorkflowResult:
    context = _context(input_source, config, charge, multiplicity, name)
    calc_dir = _calc_subdir(_resolve_output_dir(output_dir), name, input_source, "frequency")
    request = _build_request(
        context,
        _RequestDefinition("frequency", "orca", method_kwargs or {}),
    )
    return _execute(_build_plan(StepKind.FREQUENCY, [request], [StepKind.FREQUENCY]), calc_dir)


def run_optfreq(
    input_source: str,
    output_dir: str | Path = "./optfreq_output",
    config: dict[str, Any] | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    name: str | None = None,
    method_kwargs: dict[str, Any] | None = None,
) -> WorkflowResult:
    context = _context(input_source, config, charge, multiplicity, name)
    calc_dir = _calc_subdir(_resolve_output_dir(output_dir), name, input_source, "optfreq")
    request = _build_request(context, _RequestDefinition("optfreq", "orca", method_kwargs or {}))
    plan = _build_plan(
        StepKind.OPTIMIZE,
        [request, request],
        [StepKind.OPTIMIZE, StepKind.FREQUENCY],
    )
    return _execute(plan, calc_dir)


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
    context = _context(input_source, config, charge, multiplicity, name)
    calc_dir = _calc_subdir(_resolve_output_dir(output_dir), name, input_source, "optfreqsp")
    opt_request = _build_request(
        context,
        _RequestDefinition("optfreqsp", "orca", optfreq_kwargs or {}),
    )
    sp_request = _build_request(
        context,
        _RequestDefinition("optfreqsp", "orca", sp_kwargs or {}),
    )
    thermo_options = dict(thermo_kwargs or {})
    thermo_options.setdefault("temperature", 298.15)
    thermo_options.setdefault("pressure", 1.0)
    thermo_options.setdefault("scale_factor", _DEFAULT_SCALE_FACTOR)
    thermo_options.setdefault("standard_state", "1atm")
    thermo_request = _build_request(
        context,
        _RequestDefinition("optfreqsp", "orca", thermo_options),
    )
    plan = _build_plan(
        StepKind.OPTIMIZE,
        [opt_request, opt_request, sp_request, thermo_request],
        [
            StepKind.OPTIMIZE,
            StepKind.FREQUENCY,
            StepKind.SINGLEPOINT,
            StepKind.THERMOCHEMISTRY,
        ],
    )
    return _execute(plan, calc_dir)


__all__ = [
    "run_singlepoint",
    "run_optimize",
    "run_frequency",
    "run_scan",
    "run_xtb_optimize",
    "run_optfreq",
    "run_optfreqsp",
]
