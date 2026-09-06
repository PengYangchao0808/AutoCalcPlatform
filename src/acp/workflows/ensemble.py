"""Ensemble generation workflow: CREST → CENSO prescreening+screening.

Produces a sorted, Boltzmann-weighted conformer ensemble using CENSO's
B97-3c GGA SP ranking (``censo-light`` preset) as the default protocol.
The ``censo-zero`` preset bypasses CENSO entirely and exports the CREST
ensemble sorted by the xTB energies parsed from the title lines (§7).
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from acp.backends.censo_backend import (
    CensoBackend,
    CensoRunResult,
)
from acp.backends.registry import get_backend
from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.core.state import WorkflowState
from acp.core.workflow import WorkflowResult
from acp.io.structures import InputFormat, StructureReader
from acp.workflows._helpers import resolve_task_output_root, sanitize_job_name
from acp.workflows.energy_shared import (
    resolve_crest_ewin as _resolve_crest_ewin,
)
from acp.workflows.energy_shared import (
    resolve_solvent_config as _resolve_solvent_config,
)
from acp.workflows.energy_shared import (
    v2_stage_dir as _v2_stage_dir,
)
from acp.workflows.energy_shared import (
    xtb_passthrough_result as _xtb_passthrough_result,
)
from cccp.config import load_config
from cccp.utils.file_io import read_xyz_multiframe, write_xyz, write_xyz_multiframe

logger = logging.getLogger(__name__)


def _is_multiframe_xyz(path: Path) -> bool:
    if not path.exists() or path.suffix.lower() not in (".xyz",):
        return False
    try:
        coords, _ = read_xyz_multiframe(path)
        n_atom_lines = coords.shape[0]
        if n_atom_lines == 0:
            return False
        with open(path) as f:
            first_line = f.readline().strip()
        try:
            atoms_per_frame = int(first_line)
        except ValueError:
            return False
        if atoms_per_frame <= 0:
            return False
        return n_atom_lines > atoms_per_frame
    except (OSError, ValueError, RuntimeError):
        return False


def _is_file_input(input_source: str) -> bool:
    try:
        path = Path(input_source)
        return path.exists() and path.is_file()
    except OSError:
        return False


def _build_ensemble_from_censo(
    result: CensoRunResult,
    structure: Structure,
) -> StructureEnsemble:
    records: list[StructureRecord] = []
    weights = result.boltzmann_weights()

    for rec in result.records:
        conf_struct = Structure(
            id=f"{structure.id}_{rec.conf_id.lower()}",
            charge=structure.charge,
            multiplicity=structure.multiplicity,
            symbols=rec.symbols,
            coordinates=rec.coordinates,
            metadata={"conf_id": rec.conf_id, "frame_index": rec.frame_index},
        )
        records.append(
            StructureRecord(
                structure=conf_struct,
                energy_hartree=rec.energy,
                free_energy_hartree=rec.gtot,
                weight=weights.get(rec.conf_id, 0.0),
                properties={
                    "gsolv": rec.gsolv,
                    "grrho": rec.grrho,
                    "gtot": rec.gtot,
                },
            )
        )

    records.sort(key=lambda r: r.free_energy_hartree if r.free_energy_hartree is not None else 0.0)
    return StructureEnsemble(records=records)


def _write_ensemble_outputs(
    ensemble: StructureEnsemble,
    output_dir: Path,
    raw_result: CensoRunResult,
) -> None:
    ensembles_dir = output_dir / "RESULT" / "ensembles"
    ensembles_dir.mkdir(parents=True, exist_ok=True)
    base = ensembles_dir / "ensemble"

    symbols: list[str] = []
    all_coords_list: list[np.ndarray] = []
    xyz_titles: list[str] = []

    for i, rec in enumerate(ensemble.records):
        s = rec.structure
        if not symbols:
            symbols = s.symbols
        gtot = rec.free_energy_hartree or 0.0
        weight = rec.weight or 0.0
        title = f"conf{i:03d} gtot={gtot:.8f} weight={weight:.6f}"
        xyz_titles.append(title)
        coords = (
            np.asarray(s.coordinates) if s.coordinates is not None else np.zeros((len(symbols), 3))
        )
        all_coords_list.append(coords)

    if all_coords_list:
        all_coords = np.vstack(all_coords_list)
        write_xyz_multiframe(
            base.with_suffix(".xyz"),
            all_coords,
            symbols,
            titles=xyz_titles,
        )

    json_data: list[dict[str, Any]] = []
    for rec in ensemble.records:
        json_data.append(
            {
                "conf_id": rec.structure.metadata.get("conf_id", ""),
                "frame_index": rec.structure.metadata.get("frame_index", 0),
                "energy_hartree": rec.energy_hartree,
                "gsolv": rec.properties.get("gsolv"),
                "grrho": rec.properties.get("grrho"),
                "gtot": rec.free_energy_hartree,
                "boltzmann_weight": rec.weight,
            }
        )
    json_path = base.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {
                "temperature": raw_result.temperature,
                "preset": raw_result.preset,
                "n_conformers": len(ensemble.records),
                "conformers": json_data,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    csv_path = base.with_suffix(".csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["conf_id", "energy_hartree", "gsolv", "grrho", "gtot", "boltzmann_weight"])
        for item in json_data:
            writer.writerow(
                [
                    item["conf_id"],
                    f"{item['energy_hartree']:.10f}" if item["energy_hartree"] is not None else "",
                    f"{item.get('gsolv'):.10f}" if item.get("gsolv") is not None else "",
                    f"{item.get('grrho'):.10f}" if item.get("grrho") is not None else "",
                    f"{item.get('gtot'):.10f}" if item.get("gtot") is not None else "",
                    f"{item['boltzmann_weight']:.6f}"
                    if item["boltzmann_weight"] is not None
                    else "",
                ]
            )

    logger.info("Ensemble written to %s.{xyz,json,csv}", base)


def run_ensemble_generation(
    input_source: str,
    output_dir: str | Path = "./ensemble_output",
    preset: str = "censo-light",
    config: dict[str, Any] | None = None,
    name: str | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    solvent: str | None = None,
    nproc: int | None = None,
    keep_all: bool | None = None,
    ewin: float | None = None,
) -> WorkflowResult:
    """Run ensemble generation: SMILES/XYZ → CREST → CENSO → sorted ensemble.

    Args:
        input_source: SMILES string or path to XYZ file.
        output_dir: Output directory.
        preset: CENSO preset (censo-light/censo-default/censo-zero);
            censo-zero exports the CREST ensemble directly (no CENSO call).
        config: Optional configuration dict.
        name: Molecule name.
        charge: Molecular charge.
        multiplicity: Spin multiplicity.
        solvent: Solvent name or None for gas phase.
        nproc: Number of processors.
        keep_all: Pass ``--keep-all`` to CENSO so the ensemble is not
            truncated at part thresholds (default: ``censo.keep_all`` config,
            False).
        ewin: CREST energy window in kcal/mol (default: ``censo.ewin``
            config, 6.0 — literature RTCONF55-16K value).

    Returns:
        WorkflowResult with conformer ensemble.
    """
    cfg = load_config(overrides=config) if config is not None else load_config()

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
    structure = Structure(
        id=safe_name,
        charge=structure.charge,
        multiplicity=structure.multiplicity,
        symbols=structure.symbols,
        coordinates=structure.coordinates,
        metadata=structure.metadata,
    )

    mol_dir = resolve_task_output_root(output_root, safe_name)
    state = WorkflowState(mol_dir, safe_name)
    state.initialize(
        input_source=input_source,
        stage_names=["crest", "censo", "ensemble_generation"],
    )

    censo_solvent, solvent_model = _resolve_solvent_config(cfg, solvent)

    if censo_solvent and solvent_model == "none":
        logger.info(
            "Solvent '%s' specified without a solvent model — "
            "defaulting to SMD for CENSO and CREST/xTB.",
            censo_solvent,
        )
        solvent_model = "smd"

    safe_nproc: int | None = None
    if nproc is not None and nproc > 0:
        safe_nproc = nproc

    crest_ewin = _resolve_crest_ewin(cfg, ewin)

    stages_completed: list[str] = ["embed"]
    crest_skipped = False

    try:
        is_file = input_format not in (InputFormat.SMILES,) and _is_file_input(input_source)
        state.set_stage("crest")
        if is_file and _is_multiframe_xyz(Path(input_source)):
            logger.info("Multi-frame XYZ input detected — skipping CREST")
            crest_ensemble_xyz = Path(input_source).resolve()
            state.complete_stage("crest", {"status": "skipped", "reason": "multi-frame XYZ input"})
            crest_skipped = True
        else:
            crest_dir = _v2_stage_dir(mol_dir, "02_SEARCH", "CREST")
            crest_dir.mkdir(parents=True, exist_ok=True)

            crest_cfg = cfg.get("executables", {}).get("crest", {})
            crest_backend = get_backend("crest")(
                config=cfg,
                gfn_level=crest_cfg.get("gfn_level", 2),
                solvent=censo_solvent,
                solvent_model=solvent_model,
            )

            coords = (
                np.asarray(structure.coordinates)
                if structure.coordinates is not None
                else np.empty((0, 3))
            )
            logger.info("CREST energy window: %.2f kcal/mol", crest_ewin)
            crest_input_xyz = crest_dir / f"{safe_name}.xyz"
            write_xyz(
                crest_input_xyz,
                coords,
                list(structure.symbols),
                title=f"CREST input for {safe_name}",
            )
            crest_ensemble_xyz = crest_backend.search(
                initial_xyz=crest_input_xyz,
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                output_dir=crest_dir,
                energy_window=crest_ewin,
            )

            state.complete_stage("crest", {"status": "completed"})

        stages_completed.append("crest")

        if (preset or "").lower() == "censo-zero":
            # §7: ensemble + censo-zero does NOT invoke CENSO — the CREST
            # ensemble is exported directly, sorted by xTB title energies.
            logger.info("censo-zero: CREST xTB passthrough (no CENSO call)")
            temperature = cfg.get("censo", {}).get("temperature", 298.15)
            result = _xtb_passthrough_result(crest_ensemble_xyz, temperature)
            state.set_stage("censo")
            state.complete_stage(
                "censo",
                {
                    "status": "skipped",
                    "reason": "censo-zero passthrough",
                    "n_records": len(result.records),
                },
            )
        else:
            censo_dir = _v2_stage_dir(mol_dir, "02_SEARCH", "CENSO")
            censo_dir.mkdir(parents=True, exist_ok=True)

            backend = CensoBackend(cfg)
            state.set_stage("censo")
            result = backend.refine_ensemble(
                crest_ensemble_xyz,
                censo_dir,
                preset=preset,
                charge=structure.charge,
                multiplicity=structure.multiplicity,
                solvent=censo_solvent,
                solvent_model=solvent_model,
                nproc=safe_nproc,
                keep_all=keep_all,
            )
            state.complete_stage("censo", {"status": "completed", "n_records": len(result.records)})
        stages_completed.append("censo")

        ensemble = _build_ensemble_from_censo(result, structure)
        _write_ensemble_outputs(ensemble, mol_dir, result)
        stages_completed.append("finalize")

    except Exception as exc:
        logger.exception("Ensemble generation failed: %s", exc)
        state.fail_stage("ensemble_generation", str(exc))
        return WorkflowResult(
            status="failed",
            ensemble=StructureEnsemble(records=[]),
            stages_completed=[],
            error=str(exc),
        )

    state.mark_completed()
    return WorkflowResult(
        status="completed",
        ensemble=ensemble,
        stages_completed=stages_completed,
        metadata={
            "preset": preset,
            "n_conformers": len(ensemble.records),
            "crest_ewin": crest_ewin,
            "ensemble_xyz": str(mol_dir / "RESULT" / "ensembles" / "ensemble.xyz"),
            "ensemble_json": str(mol_dir / "RESULT" / "ensembles" / "ensemble.json"),
            "ensemble_csv": str(mol_dir / "RESULT" / "ensembles" / "ensemble.csv"),
            "crest_skipped": crest_skipped,
        },
    )


__all__ = ["run_ensemble_generation"]
