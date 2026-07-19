"""Mechanism analysis workflow."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from acp.core.models import HARTREE_TO_KCAL, Structure, StructureEnsemble, StructureRecord
from acp.core.state import WorkflowState
from acp.core.workflow import Stage, WorkflowContext, WorkflowResult, WorkflowRunner, WorkflowSpec
from acp.io.structures import StructureReader
from conformer_search.config import load_config

logger = logging.getLogger(__name__)

STAGE_REACTANT_OPT = "reactant_optimize"
STAGE_PRODUCT_OPT = "product_optimize"
STAGE_TS_GUESS = "ts_guess"
STAGE_TS_OPTIMIZE = "ts_optimize"
STAGE_IRC_FORWARD = "irc_forward"
STAGE_IRC_REVERSE = "irc_reverse"
STAGE_ENERGY_ANALYSIS = "energy_analysis"

_MECHANISM_METADATA_KEY = "mechanism"
_MOLECULE_NAME_KEY = "mechanism_molecule_name"


def _mechanism_metadata(data: StructureEnsemble) -> dict[str, Any]:
    metadata = data.metadata.get(_MECHANISM_METADATA_KEY)
    if isinstance(metadata, dict):
        return cast(dict[str, Any], metadata)

    mechanism_metadata: dict[str, Any] = {}
    data.metadata[_MECHANISM_METADATA_KEY] = mechanism_metadata
    return mechanism_metadata


def _stage_results(data: StructureEnsemble) -> dict[str, dict[str, object]]:
    mechanism_metadata = _mechanism_metadata(data)
    results = mechanism_metadata.get("stage_results")
    if isinstance(results, dict):
        return cast(dict[str, dict[str, object]], results)

    stage_results: dict[str, dict[str, object]] = {}
    mechanism_metadata["stage_results"] = stage_results
    return stage_results


def _find_record_by_role(data: StructureEnsemble, role: str) -> StructureRecord | None:
    for record in data.records:
        if record.properties.get("mechanism_role") == role:
            return record
    return None


def _copy_coordinates(
    coordinates: Sequence[Sequence[float]] | NDArray[np.float64] | None,
) -> NDArray[np.float64] | None:
    if coordinates is None:
        return None
    return np.asarray(coordinates, dtype=float).copy()


def _base_record(data: StructureEnsemble) -> StructureRecord:
    reactant = _find_record_by_role(data, "reactant")
    if reactant is not None:
        return reactant
    if not data.records:
        raise ValueError("Mechanism workflow requires an initial structure record")

    _ = data.records[0].properties.setdefault("mechanism_role", "reactant")
    return data.records[0]


def _clone_record(source: StructureRecord, role: str) -> StructureRecord:
    structure = Structure(
        id=f"{source.id}_{role}",
        charge=source.charge,
        multiplicity=source.multiplicity,
        symbols=list(source.symbols),
        coordinates=_copy_coordinates(source.coordinates),
        metadata={
            **source.metadata,
            "mechanism_role": role,
            "mechanism_origin": source.id,
        },
    )
    return StructureRecord(
        structure=structure,
        energy_hartree=source.energy_hartree,
        free_energy_hartree=source.free_energy_hartree,
        weight=source.weight,
        properties={
            **source.properties,
            "mechanism_role": role,
            "mechanism_origin": source.id,
        },
        files=dict(source.files),
    )


def _ensure_role_record(
    data: StructureEnsemble,
    role: str,
    *,
    template: StructureRecord | None = None,
) -> StructureRecord:
    record = _find_record_by_role(data, role)
    if record is not None:
        return record

    source = _base_record(data) if template is None else template
    record = _clone_record(source, role)
    data.records.append(record)
    return record


def _start_stage(ctx: WorkflowContext, stage_name: str) -> None:
    logger.info("Mechanism stage started: %s", stage_name)


def _finish_stage(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    stage_name: str,
    result: Mapping[str, object],
) -> StructureEnsemble:
    _stage_results(data)[stage_name] = dict(result)
    _mechanism_metadata(data)["last_stage"] = stage_name
    ctx.state.complete_stage(stage_name, result=result)
    return data


def _energy_delta_kcal(reference: float | None, target: float | None) -> float | None:
    if reference is None or target is None:
        return None
    return (target - reference) * HARTREE_TO_KCAL


def stage_reactant_optimize(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Optimize the reactant geometry structurally for the mechanism workflow."""
    _ = params
    _start_stage(ctx, STAGE_REACTANT_OPT)
    reactant = _base_record(data)
    reactant.properties["geometry_status"] = "optimized_placeholder"

    result = {
        "record_id": reactant.id,
        "action": "reactant_geometry_optimization_placeholder",
    }
    return _finish_stage(ctx, data, STAGE_REACTANT_OPT, result)


def stage_product_optimize(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Optimize the product geometry structurally for the mechanism workflow."""
    _ = params
    _start_stage(ctx, STAGE_PRODUCT_OPT)
    product = _ensure_role_record(data, "product")
    product.properties["geometry_status"] = "optimized_placeholder"

    result = {
        "record_id": product.id,
        "action": "product_geometry_optimization_placeholder",
    }
    return _finish_stage(ctx, data, STAGE_PRODUCT_OPT, result)


def stage_ts_guess(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Build a structural TS guess placeholder."""
    _ = params
    _start_stage(ctx, STAGE_TS_GUESS)
    reactant = _base_record(data)
    product = _ensure_role_record(data, "product")
    ts_guess = _ensure_role_record(data, "ts_guess", template=reactant)
    ts_guess.properties["ts_guess_method"] = "linear_interpolation_placeholder"

    result = {
        "record_id": ts_guess.id,
        "action": "ts_guess_placeholder",
        "reactant_id": reactant.id,
        "product_id": product.id,
        "strategy": "linear_interpolation_or_qst2_style",
    }
    return _finish_stage(ctx, data, STAGE_TS_GUESS, result)


def stage_ts_optimize(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Perform a structural TS optimization placeholder."""
    _ = params
    _start_stage(ctx, STAGE_TS_OPTIMIZE)
    ts_guess = _ensure_role_record(data, "ts_guess")
    ts_record = _ensure_role_record(data, "transition_state", template=ts_guess)
    ts_record.properties["optimization_keywords"] = ["Opt=TS", "CalcFC"]
    ts_record.properties["geometry_status"] = "optimized_placeholder"

    result = {
        "record_id": ts_record.id,
        "action": "ts_optimization_placeholder",
        "keywords": "Opt=TS CalcFC",
    }
    return _finish_stage(ctx, data, STAGE_TS_OPTIMIZE, result)


def stage_irc_forward(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Record a structural IRC-forward validation placeholder."""
    _ = params
    _start_stage(ctx, STAGE_IRC_FORWARD)
    transition_state = _ensure_role_record(data, "transition_state")
    product = _ensure_role_record(data, "product")

    result = {
        "record_id": transition_state.id,
        "action": "irc_forward_placeholder",
        "target_id": product.id,
    }
    return _finish_stage(ctx, data, STAGE_IRC_FORWARD, result)


def stage_irc_reverse(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Record a structural IRC-reverse validation placeholder."""
    _ = params
    _start_stage(ctx, STAGE_IRC_REVERSE)
    transition_state = _ensure_role_record(data, "transition_state")
    reactant = _base_record(data)

    result = {
        "record_id": transition_state.id,
        "action": "irc_reverse_placeholder",
        "target_id": reactant.id,
    }
    return _finish_stage(ctx, data, STAGE_IRC_REVERSE, result)


def stage_energy_analysis(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Compile a structural energy-profile placeholder."""
    _ = params
    _start_stage(ctx, STAGE_ENERGY_ANALYSIS)
    reactant = _base_record(data)
    product = _ensure_role_record(data, "product")
    transition_state = _ensure_role_record(data, "transition_state")

    profile = {
        "reactant_id": reactant.id,
        "product_id": product.id,
        "transition_state_id": transition_state.id,
        "reactant_energy_hartree": reactant.energy_hartree,
        "product_energy_hartree": product.energy_hartree,
        "transition_state_energy_hartree": transition_state.energy_hartree,
        "forward_barrier_kcal_mol": _energy_delta_kcal(
            reactant.energy_hartree,
            transition_state.energy_hartree,
        ),
        "reverse_barrier_kcal_mol": _energy_delta_kcal(
            product.energy_hartree,
            transition_state.energy_hartree,
        ),
        "reaction_energy_kcal_mol": _energy_delta_kcal(
            reactant.energy_hartree,
            product.energy_hartree,
        ),
    }
    data.metadata["energy_profile"] = profile
    _mechanism_metadata(data)["energy_profile"] = profile

    result = {
        "action": "energy_analysis_placeholder",
        "profile": profile,
    }
    return _finish_stage(ctx, data, STAGE_ENERGY_ANALYSIS, result)


def get_mechanism_stages() -> list[Stage]:
    """Return the stage list for mechanism analysis."""
    return [
        Stage(STAGE_REACTANT_OPT, stage_reactant_optimize),
        Stage(STAGE_PRODUCT_OPT, stage_product_optimize),
        Stage(STAGE_TS_GUESS, stage_ts_guess),
        Stage(STAGE_TS_OPTIMIZE, stage_ts_optimize),
        Stage(STAGE_IRC_FORWARD, stage_irc_forward),
        Stage(STAGE_IRC_REVERSE, stage_irc_reverse),
        Stage(STAGE_ENERGY_ANALYSIS, stage_energy_analysis),
    ]


def run_mechanism_analysis(
    input_source: str,
    output_dir: str | Path = "./mechanism_output",
    config: dict[str, Any] | None = None,
    name: str | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
) -> WorkflowResult:
    """Run mechanism analysis: reactant/product optimization → TS → IRC → energy profile."""
    cfg = load_config(overrides=config) if config is not None else load_config()
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    structure = StructureReader().read(
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
            coordinates=_copy_coordinates(structure.coordinates),
            metadata=structure.metadata,
        )

    ensemble = StructureEnsemble(records=[StructureRecord(structure=structure)])
    state = WorkflowState(output_root / structure.id, structure.id)

    context = WorkflowContext(
        work_dir=output_root,
        state=state,
        config=cfg,
        backends={},
        params={_MOLECULE_NAME_KEY: structure.id},
        input_source=input_source,
    )
    spec = WorkflowSpec(name="mechanism", stages=get_mechanism_stages())

    runner = WorkflowRunner(context)
    result = runner.run(spec, initial_data=ensemble)
    if result.status != "completed":
        return result

    state.mark_completed()
    result.metadata = {
        "workflow": "mechanism",
        "molecule_name": structure.id,
        "energy_profile": result.ensemble.metadata.get("energy_profile")
        if result.ensemble is not None
        else None,
        "n_structures": len(result.ensemble.records) if result.ensemble is not None else 0,
        "stage_results": _stage_results(result.ensemble) if result.ensemble is not None else {},
    }
    return result


__all__ = [
    "STAGE_ENERGY_ANALYSIS",
    "STAGE_IRC_FORWARD",
    "STAGE_IRC_REVERSE",
    "STAGE_PRODUCT_OPT",
    "STAGE_REACTANT_OPT",
    "STAGE_TS_GUESS",
    "STAGE_TS_OPTIMIZE",
    "get_mechanism_stages",
    "run_mechanism_analysis",
    "stage_energy_analysis",
    "stage_irc_forward",
    "stage_irc_reverse",
    "stage_product_optimize",
    "stage_reactant_optimize",
    "stage_ts_guess",
    "stage_ts_optimize",
]
