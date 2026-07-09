"""Conformer search workflow: stage-based ACP wrapper over the legacy engine."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import numpy as np

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.core.state import WorkflowState
from acp.core.workflow import Stage, WorkflowContext, WorkflowResult, WorkflowRunner, WorkflowSpec
from acp.io.structures import InputFormat as ACPInputFormat, StructureReader
from conformer_search.config import load_config
from conformer_search.core.candidates import CandidateSet, candidate_set_from_paths
from conformer_search.core.engine import ConformerEngine
from conformer_search.core.protocols import ProtocolSpec, resolve_protocol_spec
from conformer_search.core.spec_adapter import (
    resolve_any_protocol,
    stages_from_workflow_spec,
)
from conformer_search.core.specs import ConformerWorkflowSpec
from conformer_search.ensemble.candidate_set import FunnelRecordSet
from conformer_search.io.input_handler import InputFormat as LegacyInputFormat, MolecularInput
from conformer_search.recipes.adapter import candidate_set_from_funnel_records
from conformer_search.recipes.censo_parts import (
    KEY_FINAL_E,
    KEY_FINAL_G,
    KEY_LOWCOST,
    KEY_R2SCAN,
    KEY_XTB,
    run_part0,
    run_part1,
    run_part2,
    run_part3,
)

logger = logging.getLogger(__name__)

_ENGINE_KEY = "conformer_engine"
_MOLECULAR_INPUT_KEY = "molecular_input"
_PROTOCOL_SPEC_KEY = "protocol_spec"
_WORKFLOW_SPEC_KEY = "workflow_spec"
_MOLECULE_NAME_KEY = "molecule_name"
_INITIAL_XYZ_KEY = "initial_xyz"
_ENSEMBLE_XYZ_KEY = "ensemble_xyz"
_CANDIDATE_PATHS_KEY = "candidate_paths"
_FUNNEL_RECORDS_KEY = "funnel_records"
_CANDIDATE_SET_KEY = "candidate_set"
_LEGACY_STATE_INITIALIZED_KEY = "legacy_state_initialized"


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

    for record, weight in zip(ensemble.records, weights, strict=True):
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


def _resolve_protocol_spec_from_workflow(
    config: dict[str, Any],
    workflow_spec: ConformerWorkflowSpec,
) -> ProtocolSpec:
    """Resolve a ProtocolSpec for a given workflow specification."""
    return resolve_protocol_spec(config, workflow_spec.name.strip().lower())


def _protocol_spec_from_context(ctx: WorkflowContext) -> ProtocolSpec:
    """Return the resolved legacy protocol spec from workflow context."""
    protocol_spec = ctx.params.get(_PROTOCOL_SPEC_KEY)
    if not isinstance(protocol_spec, ProtocolSpec):
        raise ValueError("Workflow context is missing a resolved protocol specification")
    return protocol_spec


def _molecular_input_from_context(ctx: WorkflowContext) -> MolecularInput:
    """Return the cached legacy MolecularInput from workflow context."""
    molecular_input = ctx.params.get(_MOLECULAR_INPUT_KEY)
    if not isinstance(molecular_input, MolecularInput):
        raise ValueError("Workflow context is missing parsed molecular input")
    return molecular_input


def _workflow_spec_from_context(ctx: WorkflowContext) -> ConformerWorkflowSpec:
    """Return the resolved composable workflow spec from workflow context."""
    workflow_spec = ctx.params.get(_WORKFLOW_SPEC_KEY)
    if not isinstance(workflow_spec, ConformerWorkflowSpec):
        raise ValueError("Workflow context is missing a composable workflow specification")
    return workflow_spec


def _initial_xyz_from_context(ctx: WorkflowContext) -> Path:
    """Return the cached initial XYZ path from workflow context."""
    initial_xyz = ctx.params.get(_INITIAL_XYZ_KEY)
    if not isinstance(initial_xyz, Path):
        raise ValueError("Workflow context is missing the initial XYZ path")
    return initial_xyz


def _candidate_paths_from_context(ctx: WorkflowContext) -> list[Path]:
    """Return cached candidate paths from workflow context."""
    candidate_paths = ctx.params.get(_CANDIDATE_PATHS_KEY)
    if not isinstance(candidate_paths, list) or not all(isinstance(path, Path) for path in candidate_paths):
        raise ValueError("Workflow context is missing candidate XYZ paths")
    return cast(list[Path], candidate_paths)


def _candidate_set_from_context(ctx: WorkflowContext) -> CandidateSet:
    """Return cached candidate set from workflow context."""
    candidate_set = ctx.params.get(_CANDIDATE_SET_KEY)
    if not isinstance(candidate_set, CandidateSet):
        raise ValueError("Workflow context is missing a finalized candidate set")
    return candidate_set


def _funnel_records_from_context(ctx: WorkflowContext) -> FunnelRecordSet:
    """Return cached funnel records from workflow context."""
    records = ctx.params.get(_FUNNEL_RECORDS_KEY)
    if not isinstance(records, FunnelRecordSet):
        raise ValueError("Workflow context is missing funnel records")
    return records


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


def _ensure_engine(ctx: WorkflowContext, data: StructureEnsemble) -> ConformerEngine:
    """Create and cache the legacy ConformerEngine for this workflow run."""
    cached_engine = ctx.backends.get(_ENGINE_KEY)
    if isinstance(cached_engine, ConformerEngine):
        return cached_engine

    molecule_name = ctx.params.get(_MOLECULE_NAME_KEY)
    if not isinstance(molecule_name, str) or not molecule_name:
        raise ValueError("Workflow context is missing molecule name")

    protocol_spec = _protocol_spec_from_context(ctx)
    engine = ConformerEngine(
        config=ctx.config,
        work_dir=ctx.work_dir,
        molecule_name=molecule_name,
        protocol=protocol_spec.name,
        protocol_spec=protocol_spec,
    )

    if data.records:
        engine._current_charge = data.records[0].charge
        engine._current_multiplicity = data.records[0].multiplicity

    ctx.backends[_ENGINE_KEY] = engine
    return engine


def _initialize_legacy_state(engine: ConformerEngine, ctx: WorkflowContext) -> None:
    """Mirror the legacy run/state initialization performed by ConformerEngine.run()."""
    if ctx.params.get(_LEGACY_STATE_INITIALIZED_KEY):
        return

    molecular_input = _molecular_input_from_context(ctx)
    engine.state_manager.start_run(
        smiles=molecular_input.metadata.get("smiles", "unknown"),
        two_stage_enabled=engine.protocol_spec.two_stage_enabled,
    )
    engine.state_manager.set_protocol_signature(
        protocol=engine.protocol_spec.name,
        funnel_signature={
            "search_mode": engine.protocol_spec.funnel_policy.search_mode,
            "two_stage": engine.protocol_spec.two_stage_enabled,
            "ngeom_default": engine.protocol_spec.ngeom_default,
        },
    )
    ctx.params[_LEGACY_STATE_INITIALIZED_KEY] = True


def _candidate_set_to_ensemble(candidate_set: CandidateSet) -> StructureEnsemble:
    """Convert a legacy candidate set into the ACP ensemble model."""
    ensemble = candidate_set.to_structure_ensemble()
    ensemble.sort_by_energy()
    return ensemble


def _funnel_source_paths_from_context(ctx: WorkflowContext) -> list[Path]:
    """Return candidate paths for CENSO stages, falling back to the initial XYZ."""
    candidate_paths = ctx.params.get(_CANDIDATE_PATHS_KEY)
    if isinstance(candidate_paths, list) and all(isinstance(path, Path) for path in candidate_paths):
        return cast(list[Path], candidate_paths)
    return [_initial_xyz_from_context(ctx)]


def _ensure_funnel_records(ctx: WorkflowContext, data: StructureEnsemble) -> FunnelRecordSet:
    """Create and cache initial funnel records for CENSO-style workflows."""
    cached_records = ctx.params.get(_FUNNEL_RECORDS_KEY)
    if isinstance(cached_records, FunnelRecordSet):
        return cached_records

    engine = _ensure_engine(ctx, data)
    records = engine._build_censo_funnel_records(_funnel_source_paths_from_context(ctx))
    ctx.params[_FUNNEL_RECORDS_KEY] = records
    return records


def _store_censo_records(ctx: WorkflowContext, records: FunnelRecordSet) -> StructureEnsemble:
    """Persist funnel records and mirror them into the legacy candidate-set cache."""
    ctx.params[_FUNNEL_RECORDS_KEY] = records
    candidate_set = candidate_set_from_funnel_records(records)
    candidate_set.update_ranks()
    ctx.params[_CANDIDATE_SET_KEY] = candidate_set
    return _candidate_set_to_ensemble(candidate_set)


def _finalize_conformer_results(ctx: WorkflowContext) -> dict[str, Any]:
    """Delegate final output writing to the legacy finalizer for exact parity."""
    engine = _ensure_engine(ctx, StructureEnsemble())
    candidate_set = _candidate_set_from_context(ctx)
    return engine.finalize(candidate_set)


def stage_embed_smiles(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Write the initial RDKit/file-derived structure exactly as the legacy engine does."""
    if not data.records:
        raise ValueError("Conformer workflow requires an initial structure record")

    engine = _ensure_engine(ctx, data)
    molecular_input = _molecular_input_from_context(ctx)
    _initialize_legacy_state(engine, ctx)

    engine._current_charge = molecular_input.charge
    engine._current_multiplicity = molecular_input.multiplicity

    if molecular_input.source_format is LegacyInputFormat.SMILES:
        ctx.params[_INITIAL_XYZ_KEY] = engine._step_rdkit_embed(molecular_input)
    else:
        ctx.params[_INITIAL_XYZ_KEY] = engine._save_initial_structure(molecular_input)
    return data


def stage_search(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Run conformer search via the configured search backend."""
    engine = _ensure_engine(ctx, data)
    initial_xyz = _initial_xyz_from_context(ctx)

    backend = "crest"
    try:
        backend = _workflow_spec_from_context(ctx).search.backend
    except ValueError:
        pass

    if backend == "crest":
        ensemble_xyz = engine.run_crest(initial_xyz)
    elif backend == "molclus_xtb_md":
        try:
            from acp.backends.molclus_backend import MolclusBackend

            molclus = MolclusBackend(ctx.config)
            result = molclus.search(
                initial_xyz,
                charge=getattr(engine, "_current_charge", 0),
                multiplicity=getattr(engine, "_current_multiplicity", 1),
                output_dir=getattr(engine, "crest_dir", initial_xyz.parent),
            )
            if result.success and result.output_file is not None:
                ensemble_xyz = Path(result.output_file)
            else:
                logger.warning("Molclus search failed; falling back to CREST")
                ensemble_xyz = engine.run_crest(initial_xyz)
        except Exception as exc:
            logger.warning("Molclus backend unavailable (%s); falling back to CREST", exc)
            ensemble_xyz = engine.run_crest(initial_xyz)
    elif backend == "external_xyz":
        ensemble_xyz = initial_xyz
    else:
        ensemble_xyz = engine.run_crest(initial_xyz)

    ctx.params[_ENSEMBLE_XYZ_KEY] = ensemble_xyz
    return data


stage_crest_search = stage_search


def stage_isostat_cluster(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Run legacy ISOSTAT clustering, then split the clustered ensemble into candidate XYZs."""
    engine = _ensure_engine(ctx, data)

    ensemble_xyz = ctx.params.get(_ENSEMBLE_XYZ_KEY)
    if not isinstance(ensemble_xyz, Path):
        raise ValueError("Workflow context is missing the ensemble XYZ path")

    ctx.params[_CANDIDATE_PATHS_KEY] = engine.run_isostat(ensemble_xyz)
    candidate_set = candidate_set_from_paths(ctx.params[_CANDIDATE_PATHS_KEY])
    candidate_set.update_ranks()
    ctx.params[_CANDIDATE_SET_KEY] = candidate_set
    return _candidate_set_to_ensemble(candidate_set)


def stage_dft_optimize(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Run the legacy OPT→FREQ→SP→Shermo handoff for non-zero protocols."""
    mode = params.get("mode", "shared_all")
    if mode == "skip":
        return data

    engine = _ensure_engine(ctx, data)
    candidate_paths = _candidate_paths_from_context(ctx)
    if mode == "shared_default":
        candidate_paths = candidate_paths[:engine.protocol_spec.ngeom_default]

    candidate_set = engine.run_dft_handoff(candidate_paths)
    ctx.params[_CANDIDATE_SET_KEY] = candidate_set
    return _candidate_set_to_ensemble(candidate_set)


def stage_frequency(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Structural ACP stage; frequency work is executed inside the legacy handoff."""
    return data


def stage_single_point(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Run the legacy zero-protocol SP pipeline or act as a structural no-op otherwise."""
    mode = params.get("mode")
    if mode != "zero_protocol":
        return data

    engine = _ensure_engine(ctx, data)
    candidate_set = engine.run_zero_sp(_initial_xyz_from_context(ctx))
    ctx.params[_CANDIDATE_SET_KEY] = candidate_set
    return _candidate_set_to_ensemble(candidate_set)


def stage_shermo_thermo(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Structural ACP stage; Shermo work is executed inside the legacy handoff."""
    return data


def stage_censo_part0(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Run CENSO Part0 over the current funnel-record set."""
    engine = _ensure_engine(ctx, data)
    workflow_spec = _workflow_spec_from_context(ctx)
    records = _ensure_funnel_records(ctx, data)
    updated_records = run_part0(
        records,
        params.get("window_kcal"),
        work_dir=engine.work_dir,
        protocol=workflow_spec.name,
    )
    return _store_censo_records(ctx, updated_records)


def stage_censo_part1(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Run CENSO Part1 over the current funnel-record set."""
    engine = _ensure_engine(ctx, data)
    workflow_spec = _workflow_spec_from_context(ctx)
    records = _ensure_funnel_records(ctx, data)
    got_real = engine._run_dft_sp_for_records(records, KEY_LOWCOST, stage="low_cost_sp")
    if not got_real:
        engine._seed_censo_energy_key(records, KEY_LOWCOST, [KEY_XTB])
    updated_records = run_part1(
        records,
        params.get("window_kcal"),
        work_dir=engine.work_dir,
        protocol=workflow_spec.name,
    )
    return _store_censo_records(ctx, updated_records)


def stage_censo_part2(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Run CENSO Part2 over the current funnel-record set."""
    engine = _ensure_engine(ctx, data)
    workflow_spec = _workflow_spec_from_context(ctx)
    records = _ensure_funnel_records(ctx, data)
    got_real = engine._run_dft_opt_freq_for_records(records, KEY_R2SCAN, stage="optimization")
    if not got_real:
        engine._seed_censo_energy_key(records, KEY_R2SCAN, [KEY_LOWCOST, KEY_XTB])
    updated_records = run_part2(
        records,
        params.get("window_kcal"),
        work_dir=engine.work_dir,
        protocol=workflow_spec.name,
    )
    return _store_censo_records(ctx, updated_records)


def stage_censo_part3(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: Any,
) -> StructureEnsemble:
    """Run CENSO Part3 over the current funnel-record set."""
    engine = _ensure_engine(ctx, data)
    workflow_spec = _workflow_spec_from_context(ctx)
    records = _ensure_funnel_records(ctx, data)
    got_real = engine._run_dft_sp_for_records(records, KEY_FINAL_E, stage="final_sp")
    if not got_real:
        engine._seed_censo_energy_key(records, KEY_FINAL_E, [KEY_R2SCAN, KEY_LOWCOST, KEY_XTB])
    if workflow_spec.thermo.backend == "shermo":
        engine._apply_thermo_correction(
            records, KEY_FINAL_E, KEY_FINAL_G,
            temperature=workflow_spec.thermo.temperature,
        )
    else:
        engine._seed_censo_energy_key(records, KEY_FINAL_G, [KEY_FINAL_E, KEY_R2SCAN, KEY_LOWCOST, KEY_XTB])
    updated_records = run_part3(
        records,
        cutoff=params.get("cutoff"),
        temperature=params.get("temperature", 298.15),
        work_dir=engine.work_dir,
        protocol=workflow_spec.name,
    )
    return _store_censo_records(ctx, updated_records)


def _stage_from_workflow_name(
    stage_name: str,
    protocol_spec: ProtocolSpec,
    workflow_spec: ConformerWorkflowSpec,
) -> Stage:
    """Map canonical workflow-spec stage names to ACP stage wrappers."""
    if stage_name == "embed_smiles":
        return Stage("embed_smiles", stage_embed_smiles)
    if stage_name == "crest_search":
        return Stage(
            "crest_search",
            stage_search,
            {"two_stage": protocol_spec.two_stage_enabled},
        )
    if stage_name == "isostat_cluster":
        return Stage("isostat_cluster", stage_isostat_cluster)
    if stage_name == "single_point":
        return Stage("single_point", stage_single_point, {"mode": "zero_protocol"})
    if stage_name == "censo_part0":
        return Stage(
            "censo_part0",
            stage_censo_part0,
            {"window_kcal": workflow_spec.recipe.part0_window_kcal},
        )
    if stage_name == "censo_part1":
        return Stage(
            "censo_part1",
            stage_censo_part1,
            {"window_kcal": workflow_spec.recipe.part1_window_kcal},
        )
    if stage_name == "censo_part2":
        return Stage(
            "censo_part2",
            stage_censo_part2,
            {"window_kcal": workflow_spec.recipe.part2_window_kcal},
        )
    if stage_name == "censo_part3":
        return Stage(
            "censo_part3",
            stage_censo_part3,
            {
                "cutoff": workflow_spec.recipe.boltzmann_cutoff,
                "temperature": workflow_spec.thermo.temperature,
            },
        )
    raise ValueError(f"Unsupported workflow stage name: {stage_name}")


def get_protocol_stages(
    name: str,
    config: dict[str, Any] | None = None,
) -> list[Stage]:
    """Return the ACP stage list for a conformer protocol."""
    cfg = load_config(overrides=config) if config is not None else load_config()
    protocol_name = _resolve_protocol_name(cfg, name)
    workflow_spec = resolve_any_protocol(protocol_name, config=cfg)
    protocol_spec = _resolve_protocol_spec_from_workflow(cfg, workflow_spec)

    if workflow_spec.family == "reference":
        return [
            Stage("single_point", stage_single_point, {"mode": "zero_protocol"}),
        ]

    if workflow_spec.family == "ext":
        return [
            Stage("embed_smiles", stage_embed_smiles),
            Stage("crest_search", stage_search, {"two_stage": protocol_spec.two_stage_enabled}),
            Stage("isostat_cluster", stage_isostat_cluster),
        ]

    # CENSO family (default)
    stage_names = stages_from_workflow_spec(workflow_spec)
    return [
        _stage_from_workflow_name(stage_name, protocol_spec, workflow_spec)
        for stage_name in stage_names
    ]


def run_conformer_search(
    input_source: str,
    output_dir: str | Path = "./conformer_output",
    protocol: str = "ext",
    config: dict[str, Any] | None = None,
    name: str | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
) -> WorkflowResult:
    """Run the stage-based ACP conformer workflow and finalize legacy outputs."""
    cfg = load_config(overrides=config) if config is not None else load_config()
    protocol_name = _resolve_protocol_name(cfg, protocol)
    workflow_spec = resolve_any_protocol(protocol_name, config=cfg)
    protocol_spec = _resolve_protocol_spec_from_workflow(cfg, workflow_spec)

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    reader = StructureReader()
    input_format = reader.detect_format(input_source)
    structure = reader.read(
        input_source,
        charge=charge,
        multiplicity=multiplicity,
    )
    if name:
        structure = Structure(
            id=name,
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
    ensemble = StructureEnsemble(records=[StructureRecord(structure=structure)])
    initial_xyz = None
    if workflow_spec.name == "reference-sp" and molecular_input.source_path is not None:
        initial_xyz = molecular_input.source_path

    state = WorkflowState(output_root / structure.id, structure.id)
    state.initialize(input_source=input_source)

    params: dict[str, Any] = {
        _MOLECULE_NAME_KEY: structure.id,
        _MOLECULAR_INPUT_KEY: molecular_input,
        _PROTOCOL_SPEC_KEY: protocol_spec,
        _WORKFLOW_SPEC_KEY: workflow_spec,
    }
    if initial_xyz is not None:
        params[_INITIAL_XYZ_KEY] = initial_xyz

    context = WorkflowContext(
        work_dir=output_root,
        state=state,
        config=cfg,
        backends={},
        params=params,
    )

    spec = WorkflowSpec(
        name=f"conformer_{protocol_spec.name}",
        stages=get_protocol_stages(protocol_spec.name, config=cfg),
    )

    runner = WorkflowRunner(context)
    result = runner.run(spec, initial_data=ensemble)
    if result.status != "completed":
        return result

    try:
        final_result = _finalize_conformer_results(context)
    except Exception as exc:
        state.fail_stage("finalization", str(exc))
        return WorkflowResult(
            status="failed",
            ensemble=result.ensemble,
            stages_completed=result.stages_completed,
            error=str(exc),
        )

    state.mark_completed()
    result.metadata = {
        "global_min_xyz": str(final_result["global_min_xyz"]),
        "global_min_energy": final_result["global_min_energy"],
        "n_conformers": final_result["n_conformers"],
        **final_result["metadata"],
    }
    return result


__all__ = [
    "boltzmann_weight_ensemble",
    "get_protocol_stages",
    "run_conformer_search",
    "stage_search",
    "stage_crest_search",
    "stage_dft_optimize",
    "stage_embed_smiles",
    "stage_frequency",
    "stage_isostat_cluster",
    "stage_censo_part0",
    "stage_censo_part1",
    "stage_censo_part2",
    "stage_censo_part3",
    "stage_shermo_thermo",
    "stage_single_point",
]
