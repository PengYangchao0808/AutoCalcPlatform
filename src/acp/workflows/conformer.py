"""Conformer search workflow: ACP wrapper delegating to the legacy engine.

The authoritative :class:`conformer_search.core.engine.ConformerEngine` owns the
complete protocol pipeline (CREST → ISOSTAT → DFT handoff → Shermo) for every
supported protocol (ext / full / lite / zero / benchmark). ACP wraps it to
produce :class:`WorkflowResult` artifacts and a stage plan for display, without
re-implementing QC logic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from acp.catalog import convert_method_levels_to_protocol_levels
from acp.core.models import Structure, StructureEnsemble, StructureRecord, zip_strict
from acp.core.state import WorkflowState
from acp.core.workflow import Stage, WorkflowContext, WorkflowResult
from acp.io.structures import InputFormat as ACPInputFormat
from acp.io.structures import StructureReader
from acp.workflows._helpers import sanitize_job_name
from conformer_search.config import load_config
from conformer_search.core.engine import ConformerEngine
from conformer_search.core.protocols import resolve_protocol_spec, validate_protocol_methods
from conformer_search.io.input_handler import InputFormat as LegacyInputFormat
from conformer_search.io.input_handler import MolecularInput
from conformer_search.utils.file_io import read_xyz_multiframe

logger = logging.getLogger(__name__)

_ALL_CONFORMERS_XYZ = "all_conformers.xyz"


def boltzmann_weight_ensemble(
    ensemble: StructureEnsemble,
    temperature: float = 298.15,
) -> None:
    """Populate Boltzmann weights on an ensemble using free energy when available."""
    if not ensemble.records:
        return

    energies = [record.free_energy_hartree or record.energy_hartree for record in ensemble.records]
    valid_energies = [energy for energy in energies if energy is not None]
    if not valid_energies:
        return

    min_energy = min(valid_energies)
    gas_constant_hartree = 8.314462618 / 2625500.0

    weights: list[float] = []
    for energy in energies:
        if energy is None:
            weights.append(0.0)
            continue
        weights.append(float(np.exp(-(energy - min_energy) / (gas_constant_hartree * temperature))))

    total = sum(weights)
    if total <= 0.0:
        equal_weight = 1.0 / len(ensemble.records)
        for record in ensemble.records:
            record.weight = equal_weight
        return

    for record, weight in zip_strict(ensemble.records, weights):
        record.weight = weight / total


def _resolve_protocol_name(config: dict[str, Any], protocol: str) -> str:
    """Resolve the user-facing protocol name, honoring the legacy default alias."""
    requested = (protocol or "ext").strip().lower() or "ext"
    if requested == "default":
        configured_default = config.get("protocols", {}).get("default", "ext")
        if isinstance(configured_default, str) and configured_default.strip():
            requested = configured_default.strip().lower()
        else:
            requested = "ext"
    return requested


def _to_legacy_input_format(input_format: ACPInputFormat) -> LegacyInputFormat:
    """Map ACP input format enums back to the legacy conformer_search enum."""
    mapping = {
        ACPInputFormat.SMILES: LegacyInputFormat.SMILES,
        ACPInputFormat.XYZ: LegacyInputFormat.XYZ,
        ACPInputFormat.GJF: LegacyInputFormat.GJF,
        ACPInputFormat.LOG: LegacyInputFormat.LOG,
        ACPInputFormat.OUT: LegacyInputFormat.OUT,
    }
    return mapping[input_format]


def _structure_to_molecular_input(
    structure: Structure,
    *,
    input_source: str,
    input_format: ACPInputFormat,
) -> MolecularInput:
    """Rebuild the legacy MolecularInput object expected by ConformerEngine."""
    if structure.coordinates is None:
        raise ValueError("Conformer workflow requires input coordinates")

    metadata = dict(structure.metadata)
    if input_format is ACPInputFormat.SMILES:
        metadata.setdefault("smiles", input_source)

    source_path = None
    if input_format is not ACPInputFormat.SMILES:
        source_path = Path(input_source)

    return MolecularInput(
        name=structure.id,
        coordinates=np.array(structure.coordinates, copy=True),
        symbols=list(structure.symbols),
        charge=structure.charge,
        multiplicity=structure.multiplicity,
        source_format=_to_legacy_input_format(input_format),
        source_path=source_path,
        metadata=metadata,
    )


def _display_stage(ctx: WorkflowContext | None, data: Any, **params: Any) -> Any:
    """No-op stage callable used only to populate the informational stage plan."""
    return data


# Stage names mirror the engine's coarse pipeline so the scheduler and CLI can
# render progress without coupling to engine internals.
_PROTOCOL_STAGE_FLAGS: list[tuple[str, str]] = [
    ("embed_smiles", "_always"),
    ("crest_search", "enable_crest"),
    ("isostat_cluster", "enable_clustering"),
    ("dft_optimize", "enable_optimization"),
    ("frequency", "enable_frequency"),
    ("single_point", "enable_single_point"),
    ("shermo_thermo", "enable_shermo"),
]


def get_protocol_stages(
    name: str,
    config: dict[str, Any] | None = None,
    levels: dict[str, Any] | None = None,
) -> list[Stage]:
    """Return an informational stage list for a conformer protocol.

    Stages are derived from the resolved :class:`ProtocolSpec` capability flags
    and are used for display/tracking only; the authoritative engine executes
    the real pipeline monolithically inside :meth:`ConformerEngine.run`.
    """
    levels = convert_method_levels_to_protocol_levels(levels) if levels else None
    cfg = load_config(overrides=config) if config is not None else load_config()
    protocol_name = _resolve_protocol_name(cfg, name)
    spec = resolve_protocol_spec(cfg, protocol_name, levels=levels)

    stages: list[Stage] = []
    for stage_name, flag in _PROTOCOL_STAGE_FLAGS:
        if flag == "_always" or getattr(spec, flag, False):
            stages.append(Stage(stage_name, _display_stage))
    if not stages:
        stages.append(Stage("embed_smiles", _display_stage))
    return stages


def _build_ensemble_from_engine(
    engine: ConformerEngine,
    structure: Structure,
    candidates_meta: list[dict[str, Any]],
    *,
    charge: int,
    multiplicity: int,
) -> StructureEnsemble:
    """Rebuild an ACP ensemble from the engine's written multi-frame XYZ.

    Coordinates are read from ``finalDFT/all_conformers.xyz``; per-conformer
    energies/weights come from the engine metadata (same ordering).
    """
    ensemble_file = engine.final_dft_dir / _ALL_CONFORMERS_XYZ
    records: list[StructureRecord] = []

    if ensemble_file.exists():
        try:
            all_coords, symbols = read_xyz_multiframe(ensemble_file)
        except Exception as exc:
            logger.warning("Failed to read %s: %s", ensemble_file, exc)
            symbols = list(structure.symbols)
            all_coords = np.empty((0, 3))
    else:
        symbols = list(structure.symbols)
        all_coords = np.empty((0, 3))

    n_atoms = len(symbols)
    if n_atoms > 0 and all_coords.shape[0] > 0:
        frames = all_coords.reshape(-1, n_atoms, 3)
    else:
        frames = np.empty((0, n_atoms, 3))

    for idx, frame in enumerate(frames):
        meta = candidates_meta[idx] if idx < len(candidates_meta) else {}
        conf_struct = Structure(
            id=f"{structure.id}_conf{idx:03d}",
            charge=charge,
            multiplicity=multiplicity,
            symbols=symbols,
            coordinates=frame,
            metadata={"rank": meta.get("rank", idx), "source_file": meta.get("source_file")},
        )
        records.append(
            StructureRecord(
                structure=conf_struct,
                energy_hartree=meta.get("energy"),
                free_energy_hartree=meta.get("g_conc") or meta.get("gibbs_energy"),
                weight=meta.get("weight", 0.0),
                properties={k: v for k, v in meta.items() if k not in {"source_file"}},
            )
        )

    ensemble = StructureEnsemble(records=records)
    boltzmann_weight_ensemble(ensemble)
    return ensemble


def run_conformer_search(
    input_source: str,
    output_dir: str | Path = "./conformer_output",
    protocol: str = "ext",
    config: dict[str, Any] | None = None,
    name: str | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    levels: dict[str, Any] | None = None,
) -> WorkflowResult:
    """Run the conformer search workflow via the legacy ConformerEngine.

    Delegates the entire protocol pipeline (CREST → ISOSTAT → DFT → Shermo) to
    :meth:`ConformerEngine.run`, then reconstructs an ACP ensemble from the
    engine's ``all_conformers.xyz`` output for downstream consumers (e.g. NMR).
    """
    levels = convert_method_levels_to_protocol_levels(levels) if levels else None

    cfg = load_config(overrides=config) if config is not None else load_config()
    protocol_name = _resolve_protocol_name(cfg, protocol)

    # Validate protocol methods before running.
    is_valid, errors = validate_protocol_methods(cfg, protocol_name, levels)
    if not is_valid:
        logger.error("Protocol validation failed: %s", "; ".join(errors))
        return WorkflowResult(
            status="failed",
            ensemble=StructureEnsemble(records=[]),
            stages_completed=[],
            error=f"Protocol validation failed: {'; '.join(errors)}",
        )

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    reader = StructureReader()
    input_format = reader.detect_format(input_source)
    structure = reader.read(
        input_source,
        charge=charge,
        multiplicity=multiplicity,
        name=name,
    )
    if name:
        safe_name = sanitize_job_name(name)
        structure = Structure(
            id=safe_name,
            charge=structure.charge,
            multiplicity=structure.multiplicity,
            symbols=structure.symbols,
            coordinates=structure.coordinates,
            metadata=structure.metadata,
        )

    molecular_input = _structure_to_molecular_input(
        structure,
        input_source=input_source,
        input_format=input_format,
    )

    state = WorkflowState(output_root / structure.id, structure.id)
    state.initialize(input_source=input_source, stage_names=[])

    stages_completed = [
        stage.name for stage in get_protocol_stages(protocol_name, config=cfg, levels=levels)
    ]

    try:
        engine = ConformerEngine(
            config=cfg,
            work_dir=output_root,
            molecule_name=structure.id,
            protocol=protocol_name,
            levels=levels,
        )
        global_min_xyz, global_min_energy, metadata = engine.run(molecular_input)
    except Exception as exc:
        logger.exception("Conformer search failed: %s", exc)
        state.fail_stage("engine_run", str(exc))
        return WorkflowResult(
            status="failed",
            ensemble=StructureEnsemble(records=[]),
            stages_completed=[],
            error=str(exc),
        )

    candidates_meta = (metadata or {}).get("candidates", [])
    ensemble = _build_ensemble_from_engine(
        engine,
        structure,
        candidates_meta,
        charge=molecular_input.charge,
        multiplicity=molecular_input.multiplicity,
    )

    state.mark_completed()

    result_metadata: dict[str, Any] = {
        "protocol": protocol_name,
        "global_min_xyz": str(global_min_xyz) if global_min_xyz is not None else None,
        "global_min_energy": global_min_energy,
        "n_conformers": len(ensemble.records),
    }
    result_metadata.update(metadata or {})

    return WorkflowResult(
        status="completed",
        ensemble=ensemble,
        stages_completed=stages_completed,
        metadata=result_metadata,
    )


__all__ = [
    "boltzmann_weight_ensemble",
    "get_protocol_stages",
    "run_conformer_search",
]
