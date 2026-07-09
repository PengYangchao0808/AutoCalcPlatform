# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnusedCallResult=false, reportUnusedParameter=false, reportUnnecessaryIsInstance=false
"""NMR workflow integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from acp.backends import NMRCalculator, get_backend
from acp.core.models import StructureEnsemble, StructureRecord
from acp.core.state import WorkflowState
from acp.core.workflow import Stage, WorkflowContext, WorkflowResult, WorkflowRunner, WorkflowSpec
from acp.nmr.calibration import average_atom_results, calibrate_shifts, select_conformers
from acp.nmr.models import NMRConformerResult, NMRReport
from acp.nmr.parser import parse_nmr_output
from acp.reports import write_json_report, write_xlsx_report
from acp.workflows.conformer import boltzmann_weight_ensemble, run_conformer_search
from conformer_search.config import load_config

logger = logging.getLogger(__name__)

_BACKEND_KEY = "nmr_backend"
_BACKEND_NAME_KEY = "nmr_backend_name"
_JOB_NAME_KEY = "nmr_job_name"
_MOLECULE_NAME_KEY = "nmr_molecule_name"
_REPORT_KEY = "nmr_report_object"


def _normalize_backend_name(backend_name: str | None, config: dict[str, Any]) -> str:
    """Resolve the NMR backend name from explicit input or configuration."""
    theory_nmr = cast(dict[str, Any], config.get("theory", {}).get("nmr", {}))
    resolved = backend_name or theory_nmr.get("engine") or "gaussian"
    if not isinstance(resolved, str) or not resolved.strip():
        return "gaussian"
    return resolved.strip().lower()


def _sanitize_job_name(name: str) -> str:
    """Return a filesystem-safe job name."""
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in name.strip())
    return cleaned.strip("._") or "nmr"


def _derive_molecule_name(
    input_source: str,
    ensemble: StructureEnsemble,
    name: str | None,
) -> str:
    """Return a display name for the NMR workflow."""
    if isinstance(name, str) and name.strip():
        return name.strip()

    metadata_name = ensemble.metadata.get("molecule_name")
    if isinstance(metadata_name, str) and metadata_name.strip():
        return metadata_name.strip()

    if ensemble.records:
        structure_name = ensemble.records[0].metadata.get("structure_id")
        if isinstance(structure_name, str) and structure_name.strip():
            return structure_name.strip()

    input_path = Path(input_source)
    if input_path.suffix:
        return input_path.stem or "molecule"
    return input_source.strip() or "molecule"


def _nmr_config(ctx: WorkflowContext) -> dict[str, Any]:
    """Return the top-level NMR configuration mapping."""
    config = ctx.config.get("nmr", {})
    if not isinstance(config, dict):
        raise ValueError("Workflow configuration is missing the 'nmr' section")
    return cast(dict[str, Any], config)


def _nmr_theory_config(ctx: WorkflowContext) -> dict[str, Any]:
    """Return the NMR theory configuration mapping."""
    theory = ctx.config.get("theory", {})
    if not isinstance(theory, dict):
        raise ValueError("Workflow configuration is missing the 'theory' section")

    theory_nmr = theory.get("nmr", {})
    if not isinstance(theory_nmr, dict):
        raise ValueError("Workflow configuration is missing the 'theory.nmr' section")
    return cast(dict[str, Any], theory_nmr)


def _molecule_name_from_context(ctx: WorkflowContext) -> str:
    """Return the display molecule name from workflow context."""
    name = ctx.params.get(_MOLECULE_NAME_KEY)
    if not isinstance(name, str) or not name:
        raise ValueError("Workflow context is missing the NMR molecule name")
    return name


def _job_name_from_context(ctx: WorkflowContext) -> str:
    """Return the filesystem-safe job name from workflow context."""
    name = ctx.params.get(_JOB_NAME_KEY)
    if not isinstance(name, str) or not name:
        raise ValueError("Workflow context is missing the NMR job name")
    return name


def _backend_name_from_context(ctx: WorkflowContext) -> str:
    """Return the backend name for NMR execution."""
    backend_name = ctx.params.get(_BACKEND_NAME_KEY)
    if not isinstance(backend_name, str) or not backend_name:
        raise ValueError("Workflow context is missing the NMR backend name")
    return backend_name


def _report_from_context(ctx: WorkflowContext) -> NMRReport:
    """Return the cached NMR report from workflow context."""
    report = ctx.params.get(_REPORT_KEY)
    if not isinstance(report, NMRReport):
        raise ValueError("Workflow context is missing the NMR report")
    return report


def _nmr_result_from_record(record: StructureRecord) -> NMRConformerResult:
    """Return the NMR result stored on a structure record."""
    result = record.properties.get("nmr")
    if not isinstance(result, NMRConformerResult):
        raise ValueError(f"Structure record '{record.id}' is missing NMR results")
    return result


def _ensure_backend(ctx: WorkflowContext) -> NMRCalculator:
    """Instantiate and cache the configured NMR backend."""
    cached_backend = ctx.backends.get(_BACKEND_KEY)
    if cached_backend is not None:
        if not isinstance(cached_backend, NMRCalculator):
            raise TypeError("Cached backend does not support the NMR capability")
        return cached_backend

    backend_name = _backend_name_from_context(ctx)
    backend = get_backend(backend_name)(ctx.config)
    if not isinstance(backend, NMRCalculator):
        raise TypeError(f"Backend '{backend_name}' does not implement the NMR capability")

    ctx.backends[_BACKEND_KEY] = backend
    return backend


def _apply_nmr_config_overrides(
    config: dict[str, Any],
    *,
    backend_name: str | None = None,
    references: dict[str, float] | None = None,
    temperature: float | None = None,
    energy_window_kcal: float | None = None,
    max_conformers: int | None = None,
) -> dict[str, Any]:
    """Apply NMR-specific runtime overrides onto a loaded configuration."""
    theory = cast(dict[str, Any], config.setdefault("theory", {}))
    theory_nmr = cast(dict[str, Any], theory.setdefault("nmr", {}))
    nmr_config = cast(dict[str, Any], config.setdefault("nmr", {}))

    if backend_name is not None:
        theory_nmr["engine"] = backend_name.strip().lower()
    if temperature is not None:
        nmr_config["temperature_k"] = float(temperature)
    if energy_window_kcal is not None:
        nmr_config["energy_window_kcal"] = float(energy_window_kcal)
    if max_conformers is not None:
        nmr_config["max_conformers"] = int(max_conformers)
    if references is not None:
        merged_references = dict(cast(dict[str, float | None], nmr_config.get("references", {})))
        merged_references.update({str(nucleus): float(value) for nucleus, value in references.items()})
        nmr_config["references"] = merged_references

    return config


def stage_select_conformers(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Select the conformers used for the downstream NMR calculation."""
    if not data.records:
        raise ValueError("NMR workflow requires a conformer ensemble")

    temperature = float(params.get("temperature", _nmr_config(ctx).get("temperature_k", 298.15)))
    energy_window_kcal = float(
        params.get("energy_window_kcal", _nmr_config(ctx).get("energy_window_kcal", 3.0))
    )
    max_conformers = int(params.get("max_conformers", _nmr_config(ctx).get("max_conformers", 10)))

    selected_records = select_conformers(
        data,
        energy_window_kcal=energy_window_kcal,
        max_conformers=max_conformers,
    )
    if not selected_records:
        raise ValueError("No conformers were selected for NMR evaluation")

    selected_ensemble = StructureEnsemble(
        records=list(selected_records),
        data=list(data.data),
        temperature=temperature,
        metadata={
            **data.metadata,
            "molecule_name": _molecule_name_from_context(ctx),
            "nmr_energy_window_kcal": energy_window_kcal,
            "nmr_max_conformers": max_conformers,
            "nmr_selected_conformers": [record.id for record in selected_records],
            "nmr_total_conformers": len(data.records),
        },
    )
    boltzmann_weight_ensemble(selected_ensemble, temperature=temperature)
    return selected_ensemble


def stage_run_nmr_giao(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Run the backend NMR shielding calculation for each selected conformer."""
    backend = _ensure_backend(ctx)
    theory_nmr = _nmr_theory_config(ctx)

    for record in data.records:
        if record.coordinates is None:
            raise ValueError(f"Conformer '{record.id}' is missing coordinates for NMR calculation")

        conformer_dir = ctx.work_dir / "calculations" / record.id
        conformer_dir.mkdir(parents=True, exist_ok=True)
        backend_kwargs = {
            key: theory_nmr.get(key)
            for key in ("method", "basis", "dispersion", "solvent", "solvent_model")
            if theory_nmr.get(key) is not None
        }
        qc_result = backend.nmr_shielding(
            record.coordinates,
            list(record.symbols),
            charge=record.charge,
            multiplicity=record.multiplicity,
            output_dir=conformer_dir,
            output_name="nmr",
            **backend_kwargs,
        )
        if not qc_result.success:
            error_message = qc_result.error_message or "Unknown NMR backend failure"
            raise RuntimeError(f"NMR calculation failed for conformer '{record.id}': {error_message}")

        log_file = qc_result.log_file or qc_result.output_file
        if log_file is None:
            raise RuntimeError(f"NMR backend did not produce a log file for conformer '{record.id}'")

        record.files["nmr_input"] = qc_result.output_file or Path(log_file)
        record.files["nmr_log"] = Path(log_file)
        record.properties["nmr"] = NMRConformerResult(
            record_id=record.id,
            energy_hartree=record.energy_hartree,
            free_energy_hartree=record.free_energy_hartree,
            weight=record.weight,
            log_file=Path(log_file),
        )

    return data


def stage_parse_shieldings(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Parse NMR shielding tensors from backend log files."""
    backend_name = _backend_name_from_context(ctx)

    for record in data.records:
        conformer_result = _nmr_result_from_record(record)
        if not conformer_result.log_file.exists():
            raise FileNotFoundError(
                f"NMR log file for conformer '{record.id}' does not exist: {conformer_result.log_file}"
            )
        conformer_result.shieldings = parse_nmr_output(
            backend_name,
            conformer_result.log_file,
            expected_symbols=record.symbols,
        )

    return data


def stage_calibrate_shifts(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Convert parsed shieldings into referenced chemical shifts."""
    references = cast(dict[str, float | None], _nmr_config(ctx).get("references", {}))
    if not isinstance(references, dict):
        raise ValueError("Workflow configuration is missing NMR references")

    for record in data.records:
        conformer_result = _nmr_result_from_record(record)
        if not conformer_result.shieldings:
            raise ValueError(f"No parsed shieldings available for conformer '{record.id}'")
        conformer_result.shifts = calibrate_shifts(conformer_result.shieldings, references)

    return data


def stage_average_shifts(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Boltzmann-average the conformer chemical shifts."""
    temperature = float(params.get("temperature", _nmr_config(ctx).get("temperature_k", 298.15)))
    conformer_results = [_nmr_result_from_record(record) for record in data.records]
    averaged_atoms = average_atom_results(conformer_results, temperature=temperature)
    theory_nmr = _nmr_theory_config(ctx)
    references = cast(dict[str, float | None], _nmr_config(ctx).get("references", {}))

    ctx.params[_REPORT_KEY] = NMRReport(
        molecule_name=_molecule_name_from_context(ctx),
        backend=_backend_name_from_context(ctx),
        method=cast(str | None, theory_nmr.get("method")),
        basis=cast(str | None, theory_nmr.get("basis")),
        temperature_k=temperature,
        references=references,
        conformers=conformer_results,
        averaged_atoms=averaged_atoms,
        metadata={
            "conformer_protocol": ctx.params.get("conformer_protocol"),
            "selected_conformers": [record.id for record in data.records],
            "n_selected_conformers": len(data.records),
            "energy_window_kcal": data.metadata.get("nmr_energy_window_kcal"),
            "max_conformers": data.metadata.get("nmr_max_conformers"),
        },
    )
    return data


def stage_write_report(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Write JSON and XLSX NMR reports to disk."""
    report = _report_from_context(ctx)
    job_name = _job_name_from_context(ctx)
    json_path = ctx.work_dir / f"{job_name}_nmr_report.json"
    xlsx_path = ctx.work_dir / f"{job_name}_nmr_report.xlsx"

    report.metadata["json_report"] = str(json_path)
    report.metadata["xlsx_report"] = str(xlsx_path)
    write_json_report(report, json_path)
    write_xlsx_report(report, xlsx_path)

    data.metadata["nmr_report"] = str(json_path)
    data.metadata["nmr_report_xlsx"] = str(xlsx_path)
    return data


def get_nmr_stages(config: dict[str, Any] | None = None) -> list[Stage]:
    """Return the ACP stage list for the NMR workflow."""
    cfg = load_config(overrides=config) if config is not None else load_config()
    nmr_config = cast(dict[str, Any], cfg.get("nmr", {}))
    temperature = float(nmr_config.get("temperature_k", 298.15))
    energy_window_kcal = float(nmr_config.get("energy_window_kcal", 3.0))
    max_conformers = int(nmr_config.get("max_conformers", 10))

    return [
        Stage(
            "select_conformers",
            stage_select_conformers,
            {
                "temperature": temperature,
                "energy_window_kcal": energy_window_kcal,
                "max_conformers": max_conformers,
            },
        ),
        Stage("run_nmr_giao", stage_run_nmr_giao),
        Stage("parse_shieldings", stage_parse_shieldings),
        Stage("calibrate_shifts", stage_calibrate_shifts),
        Stage("average_shifts", stage_average_shifts, {"temperature": temperature}),
        Stage("write_report", stage_write_report),
    ]


def run_nmr_calculation(
    input_source: str,
    output_dir: str | Path = "./nmr_output",
    conformer_protocol: str = "ext",
    config: dict[str, Any] | None = None,
    name: str | None = None,
    backend_name: str | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    references: dict[str, float] | None = None,
    temperature: float | None = None,
    energy_window_kcal: float | None = None,
    max_conformers: int | None = None,
    ensemble: StructureEnsemble | None = None,
) -> WorkflowResult:
    """Run the integrated ACP NMR workflow."""
    cfg = load_config(overrides=config) if config is not None else load_config()
    cfg = _apply_nmr_config_overrides(
        cfg,
        backend_name=backend_name,
        references=references,
        temperature=temperature,
        energy_window_kcal=energy_window_kcal,
        max_conformers=max_conformers,
    )

    resolved_backend = _normalize_backend_name(backend_name, cfg)
    if resolved_backend == "orca":
        raise NotImplementedError(
            "ORCA NMR is not implemented yet. Use Gaussian for NMR shielding calculations."
        )

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    if ensemble is None:
        conformer_result = run_conformer_search(
            input_source,
            output_dir=output_root / "conformer",
            protocol=conformer_protocol,
            config=cfg,
            name=name,
            charge=charge,
            multiplicity=multiplicity,
        )
        if conformer_result.status != "completed":
            return WorkflowResult(
                status="failed",
                ensemble=conformer_result.ensemble,
                stages_completed=[],
                error=f"Conformer search failed: {conformer_result.error or 'unknown error'}",
            )
        if conformer_result.ensemble is None:
            return WorkflowResult(
                status="failed",
                stages_completed=[],
                error="Conformer workflow completed without an ensemble result",
            )
        working_ensemble = conformer_result.ensemble
    else:
        working_ensemble = ensemble

    molecule_name = _derive_molecule_name(input_source, working_ensemble, name)
    job_name = _sanitize_job_name(molecule_name)
    _ = working_ensemble.metadata.setdefault("molecule_name", molecule_name)

    state = WorkflowState(output_root / job_name, job_name)
    state.initialize(input_source=input_source)

    context = WorkflowContext(
        work_dir=output_root,
        state=state,
        config=cfg,
        backends={},
        params={
            _MOLECULE_NAME_KEY: molecule_name,
            _JOB_NAME_KEY: job_name,
            _BACKEND_NAME_KEY: _normalize_backend_name(backend_name, cfg),
            "conformer_protocol": conformer_protocol,
        },
    )

    spec = WorkflowSpec(name="nmr", stages=get_nmr_stages(config=cfg))
    runner = WorkflowRunner(context)
    result = runner.run(spec, initial_data=working_ensemble)
    if result.status != "completed":
        return result

    state.mark_completed()
    result.metadata = {
        "backend": _backend_name_from_context(context),
        "conformer_protocol": conformer_protocol,
        "nmr_report": result.ensemble.metadata.get("nmr_report") if result.ensemble is not None else None,
        "nmr_report_xlsx": result.ensemble.metadata.get("nmr_report_xlsx") if result.ensemble is not None else None,
        "selected_conformers": len(result.ensemble.records) if result.ensemble is not None else 0,
    }
    return result


__all__ = [
    "get_nmr_stages",
    "run_nmr_calculation",
    "stage_average_shifts",
    "stage_calibrate_shifts",
    "stage_parse_shieldings",
    "stage_run_nmr_giao",
    "stage_select_conformers",
    "stage_write_report",
]
