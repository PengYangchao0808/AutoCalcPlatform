"""Mechanism analysis workflow — reaction-path pipeline.

Stage-based workflow implementing the ACP mechanism research module. The
pipeline drives user-defined reaction coordinates (distance / angle /
dihedral), searches the path with a pluggable strategy (guided-scan /
rph-reverse / direct-ts), refines TS candidates at the selected fidelity
(s3 / s4), validates them via imaginary-mode + reaction-coordinate overlap,
runs IRC endpoint discovery, and compiles the energy profile.

The generic coordinate primitives live in ``cccp.qc.interfaces.constraints``;
the route/path/candidate models and strategy/preset/rescue submodules live in
``acp.mechanism``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from acp.core.models import HARTREE_TO_KCAL, Structure, StructureEnsemble, StructureRecord
from acp.core.state import WorkflowState
from acp.core.workflow import Stage, WorkflowContext, WorkflowResult, WorkflowRunner, WorkflowSpec
from acp.io.structures import StructureReader
from acp.mechanism.candidates import select_primary_ts
from acp.mechanism.models import (
    MechanismInput,
    MechanismRoute,
    PathResult,
    TsIdentity,
    TsValidation,
)
from acp.mechanism.presets import (
    FidelityProfile,
    resolve_fidelity,
    resolve_fidelity_profile,
)
from acp.mechanism.rescue import build_rescue_plan
from acp.mechanism.strategies import resolve_path_strategy
from cccp.config import load_config
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan

logger = logging.getLogger(__name__)

STAGE_PREPARE = "prepare_reaction"
STAGE_REACTANT_OPT = "reactant_optimize"
STAGE_PRODUCT_OPT = "product_optimize"
STAGE_PATH_SEARCH = "path_search"
STAGE_CANDIDATE_REFINE = "candidate_refine"
STAGE_TS_OPTIMIZE = "ts_optimize"
STAGE_TS_VALIDATE = "ts_validate"
STAGE_IRC_VALIDATE = "irc_validate"
STAGE_ENERGY_ANALYSIS = "energy_analysis"

_MECHANISM_METADATA_KEY = "mechanism"
_MOLECULE_NAME_KEY = "mechanism_molecule_name"
_ROUTE_KEY = "mechanism_routes"
_FIDELITY_OVERRIDES_KEY = "mechanism_fidelity_overrides"

# Preserve legacy stage aliases so existing state files / UI plans stay readable.
LEGACY_STAGE_ALIASES: dict[str, str] = {
    "ts_guess": STAGE_PATH_SEARCH,
    "irc_forward": STAGE_IRC_VALIDATE,
    "irc_reverse": STAGE_IRC_VALIDATE,
}


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


def _update_record_coordinates(
    record: StructureRecord,
    coordinates: NDArray[np.float64] | None,
    energy_hartree: float | None = None,
) -> StructureRecord:
    """Return a copy of *record* with updated coordinates / energy.

    ``Structure`` is frozen, so records are replaced (never mutated) when a
    stage produces a new geometry.
    """
    if coordinates is None:
        return record
    new_structure = Structure(
        id=record.id,
        charge=record.charge,
        multiplicity=record.multiplicity,
        symbols=list(record.symbols),
        coordinates=coordinates,
        metadata=dict(record.structure.metadata),
    )
    return StructureRecord(
        structure=new_structure,
        energy_hartree=energy_hartree if energy_hartree is not None else record.energy_hartree,
        free_energy_hartree=record.free_energy_hartree,
        weight=record.weight,
        properties=dict(record.properties),
        files=dict(record.files),
    )


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


def _replace_record(data: StructureEnsemble, record: StructureRecord) -> None:
    """Replace *record* in the ensemble by id (frozen Structure semantics)."""
    for i, existing in enumerate(data.records):
        if existing.id == record.id:
            data.records[i] = record
            return
    data.records.append(record)


def _update_record_free_energy(
    record: StructureRecord,
    free_energy_hartree: float,
    *,
    thermochemistry: dict[str, Any] | None = None,
) -> StructureRecord:
    """Return a copy of *record* with updated thermochemistry metadata."""

    properties = dict(record.properties)
    if thermochemistry is not None:
        properties["thermochemistry"] = thermochemistry
    return StructureRecord(
        structure=record.structure,
        energy_hartree=record.energy_hartree,
        free_energy_hartree=free_energy_hartree,
        weight=record.weight,
        properties=properties,
        files=dict(record.files),
    )


def _record_freq_log(record: StructureRecord) -> Path | None:
    for key in ("freq_log", "frequency_log", "log_file"):
        path = record.files.get(key)
        if path is not None:
            return Path(path)
    return None


def _maybe_apply_record_thermochemistry(
    ctx: WorkflowContext,
    record: StructureRecord,
    temperature: float,
) -> tuple[StructureRecord, str | None]:
    """Populate ``free_energy_hartree`` when a frequency log is available."""

    if record.free_energy_hartree is not None or record.energy_hartree is None:
        return record, None
    freq_log = _record_freq_log(record)
    if freq_log is None:
        return record, None
    thermo_module = import_module("acp.mechanism.providers.thermo")
    provider = thermo_module.get_thermochemistry_provider(cast(dict[str, Any], ctx.config))
    thermo_result = provider.compute(
        sp_energy=record.energy_hartree,
        freq_log=freq_log,
        ensemble=None,
        temperature=temperature,
        standard_state=thermo_module.resolve_standard_state(cast(dict[str, Any], ctx.config)),
    )
    if thermo_result.gibbs_hartree is None:
        return record, type(provider).__name__
    updated = _update_record_free_energy(
        record,
        thermo_result.gibbs_hartree,
        thermochemistry=thermo_result.to_dict(),
    )
    return updated, type(provider).__name__


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


def _backend(ctx: WorkflowContext, name: str) -> Any:
    from acp.backends.registry import get_backend

    if name not in ctx.backends:
        cfg = cast(dict[str, Any], ctx.config)
        backend_cls = get_backend(name)
        ctx.backends[name] = backend_cls(cfg)
    return ctx.backends[name]


def _store_route(ctx: WorkflowContext, data: StructureEnsemble, route: MechanismRoute) -> None:
    routes = _mechanism_metadata(data).setdefault(_ROUTE_KEY, [])
    if not isinstance(routes, list):
        routes = []
        _mechanism_metadata(data)[_ROUTE_KEY] = routes
    routes.append(route.to_dict())


def _resolve_stage_fidelity(
    ctx: WorkflowContext,
    strategy: str,
    fidelity: str,
) -> FidelityProfile:
    profile = resolve_fidelity_profile(strategy, resolve_fidelity(fidelity))
    overrides = ctx.params.get(_FIDELITY_OVERRIDES_KEY)
    if not isinstance(overrides, dict):
        return profile

    replace_kwargs: dict[str, int] = {}
    scan_points = overrides.get("scan_points")
    if isinstance(scan_points, int):
        replace_kwargs["scan_points"] = scan_points
    irc_points = overrides.get("irc_points")
    if isinstance(irc_points, int):
        replace_kwargs["irc_points"] = irc_points

    if not replace_kwargs:
        return profile
    return replace(profile, **replace_kwargs)


def _route_from_input(data: StructureEnsemble, m_input: MechanismInput) -> MechanismRoute:
    """Build the (first) route from the mechanism input, falling back to a
    direct-ts route when only a TS guess was supplied and no plan exists."""
    if m_input.routes:
        return m_input.routes[0]
    if m_input.ts_guess is not None:
        ts_record = _find_record_by_role(data, "ts_guess")
        return MechanismRoute(
            route_id="route-1",
            coordinate_plan=ReactionCoordinatePlan(
                coordinates=(
                    CoordinateSpec(
                        id="rc1",
                        kind="distance",
                        atoms=(0, 1),
                        start=1.5,
                        end=1.5,
                    ),
                ),
                points=2,
            ),
            path_strategy="direct-ts",
            fidelity="s3",
            ts_guess_id=ts_record.id if ts_record is not None else None,
        )
    raise ValueError(
        "MechanismInput requires at least one route with a coordinate plan "
        "(or a ts_guess for direct-ts)"
    )


# ── Stages ────────────────────────────────────────────────────────────────


def stage_prepare_reaction(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Tag role records (reactant/product/ts_guess) from the input payload."""
    _start_stage(ctx, STAGE_PREPARE)
    m_input = ctx.params.get("mechanism_input")
    if isinstance(m_input, MechanismInput):
        if m_input.reactant is not None and _find_record_by_role(data, "reactant") is None:
            _ = _ensure_role_record(data, "reactant")
        if m_input.product is not None and _find_record_by_role(data, "product") is None:
            _ = _ensure_role_record(data, "product")
        if m_input.ts_guess is not None and _find_record_by_role(data, "ts_guess") is None:
            _ = _ensure_role_record(data, "ts_guess")
    return _finish_stage(
        ctx,
        data,
        STAGE_PREPARE,
        {"action": "role_records_tagged", "n_records": len(data.records)},
    )


def stage_reactant_optimize(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Optimize the reactant geometry with the ORCA backend."""
    _start_stage(ctx, STAGE_REACTANT_OPT)
    reactant = _base_record(data)
    coordinates = _copy_coordinates(reactant.coordinates)
    symbols = list(reactant.symbols)

    if coordinates is None:
        raise ValueError("reactant has no coordinates to optimize")

    backend = _backend(ctx, "orca")
    fidelity = _resolve_stage_fidelity(ctx, "guided-scan", str(params.get("fidelity")))
    stage_dir = Path(str(ctx.work_dir)) / "stages" / STAGE_REACTANT_OPT
    result = backend.optimize(
        coordinates,
        symbols,
        charge=int(reactant.charge or 0),
        multiplicity=int(reactant.multiplicity or 1),
        output_dir=stage_dir,
        method=fidelity.ts_method,
        basis=fidelity.ts_basis,
        solvent=fidelity.solvent,
        solvent_model=fidelity.solvent_model,
    )
    if result.success and result.coordinates is not None:
        reactant = _update_record_coordinates(
            reactant,
            _copy_coordinates(result.coordinates),
            energy_hartree=result.energy,
        )
        _replace_record(data, reactant)

    return _finish_stage(
        ctx,
        data,
        STAGE_REACTANT_OPT,
        {
            "record_id": reactant.id,
            "success": result.success,
            "energy_hartree": result.energy,
            "output_dir": str(stage_dir),
        },
    )


def stage_product_optimize(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Optimize the product geometry (skipped when no product is given)."""
    _start_stage(ctx, STAGE_PRODUCT_OPT)
    product = _find_record_by_role(data, "product")
    if product is None:
        return _finish_stage(
            ctx,
            data,
            STAGE_PRODUCT_OPT,
            {"action": "product_not_supplied", "skipped": True},
        )

    coordinates = _copy_coordinates(product.coordinates)
    symbols = list(product.symbols)
    if coordinates is None:
        return _finish_stage(
            ctx,
            data,
            STAGE_PRODUCT_OPT,
            {"action": "no_coordinates", "skipped": True},
        )

    backend = _backend(ctx, "orca")
    fidelity = _resolve_stage_fidelity(ctx, "guided-scan", str(params.get("fidelity")))
    stage_dir = Path(str(ctx.work_dir)) / "stages" / STAGE_PRODUCT_OPT
    result = backend.optimize(
        coordinates,
        symbols,
        charge=int(product.charge or 0),
        multiplicity=int(product.multiplicity or 1),
        output_dir=stage_dir,
        method=fidelity.ts_method,
        basis=fidelity.ts_basis,
        solvent=fidelity.solvent,
        solvent_model=fidelity.solvent_model,
    )
    if result.success and result.coordinates is not None:
        product = _update_record_coordinates(
            product,
            _copy_coordinates(result.coordinates),
            energy_hartree=result.energy,
        )
        _replace_record(data, product)

    return _finish_stage(
        ctx,
        data,
        STAGE_PRODUCT_OPT,
        {
            "record_id": product.id,
            "success": result.success,
            "energy_hartree": result.energy,
        },
    )


def stage_path_search(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Run the route's path-search strategy (guided-scan / rph-reverse /
    direct-ts) and persist the :class:`PathResult` into the ensemble."""
    _start_stage(ctx, STAGE_PATH_SEARCH)

    m_input = ctx.params.get("mechanism_input")
    if not isinstance(m_input, MechanismInput):
        raise ValueError("mechanism_input missing from workflow params")

    route = _route_from_input(data, m_input)
    _store_route(ctx, data, route)

    reactant = _base_record(data)
    coordinates = _copy_coordinates(reactant.coordinates)
    symbols = list(reactant.symbols)
    if coordinates is None:
        raise ValueError("reactant has no coordinates for path search")

    if route.path_strategy == "rph-reverse":
        product = _find_record_by_role(data, "product")
        if product is not None and product.coordinates is not None:
            coordinates = _copy_coordinates(product.coordinates)
        route.product_id = product.id if product is not None else None

    fidelity = _resolve_stage_fidelity(ctx, route.path_strategy, route.fidelity)
    strategy_fn = resolve_path_strategy(route.path_strategy)
    scan_dir = Path(str(ctx.work_dir)) / "stages" / STAGE_PATH_SEARCH

    path_result = strategy_fn(
        route,
        coordinates=coordinates,
        symbols=symbols,
        charge=int(reactant.charge or 0),
        multiplicity=int(reactant.multiplicity or 1),
        scan_dir=scan_dir,
        backend=_backend(ctx, "xtb"),
        fidelity=fidelity,
    )

    _mechanism_metadata(data)["path_result"] = path_result.to_dict()
    return _finish_stage(
        ctx,
        data,
        STAGE_PATH_SEARCH,
        {
            "strategy": path_result.strategy,
            "route_id": path_result.route_id,
            "n_points": len(path_result.points),
            "n_candidates": len(path_result.candidates),
            "selected_ts_id": path_result.selected_ts_id,
            "selected_int_id": path_result.selected_int_id,
        },
    )


def stage_candidate_refine(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Refine the selected TS seed(s) with a cheap TS optimization.

    Attempts the primary TS seed first; on failure consults the rescue
    matrix and re-runs with rescue keyword overrides.
    """
    _start_stage(ctx, STAGE_CANDIDATE_REFINE)

    path_dict = _mechanism_metadata(data).get("path_result")
    if not isinstance(path_dict, dict):
        return _finish_stage(
            ctx,
            data,
            STAGE_CANDIDATE_REFINE,
            {"action": "no_path_result", "skipped": True},
        )

    path_result = PathResult(
        points=[],
        candidates=[],
        strategy=str(path_dict.get("strategy") or "guided-scan"),
        route_id=str(path_dict.get("route_id") or "route-1"),
        selected_ts_id=path_dict.get("selected_ts_id"),
        selected_int_id=path_dict.get("selected_int_id"),
    )
    for point_dict in path_dict.get("points", []):
        from acp.mechanism.models import PathPoint

        path_result.points.append(
            PathPoint(
                point_id=str(point_dict.get("point_id") or ""),
                progress=float(point_dict.get("progress") or 0.0),
                coordinate_values=dict(point_dict.get("coordinate_values") or {}),
                energies_hartree=dict(point_dict.get("energies_hartree") or {}),
                geometry=(
                    np.asarray(point_dict["geometry"], dtype=float)
                    if point_dict.get("geometry") is not None
                    else None
                ),
            )
        )
    for cand_dict in path_dict.get("candidates", []):
        from acp.mechanism.models import PathCandidate

        path_result.candidates.append(
            PathCandidate(
                candidate_id=str(cand_dict.get("candidate_id") or ""),
                kind=cast(Any, cand_dict.get("kind") or "ts_seed"),
                point_id=str(cand_dict.get("point_id") or ""),
                reason=str(cand_dict.get("reason") or ""),
                progress=float(cand_dict.get("progress") or 0.0),
                score=cand_dict.get("score"),
            )
        )

    primary_ts = select_primary_ts(path_result)
    if primary_ts is None:
        return _finish_stage(
            ctx,
            data,
            STAGE_CANDIDATE_REFINE,
            {"action": "no_ts_seed", "skipped": True},
        )

    ts_point = path_result.point_by_id(primary_ts.point_id)
    if ts_point is None or ts_point.geometry is None:
        return _finish_stage(
            ctx,
            data,
            STAGE_CANDIDATE_REFINE,
            {"action": "no_ts_geometry", "skipped": True},
        )

    ts_record = _ensure_role_record(data, "transition_state")
    ts_record = _update_record_coordinates(ts_record, _copy_coordinates(ts_point.geometry))
    _replace_record(data, ts_record)

    fidelity = _resolve_stage_fidelity(ctx, "guided-scan", str(params.get("fidelity")))
    backend = _backend(ctx, "orca")
    stage_dir = Path(str(ctx.work_dir)) / "stages" / STAGE_CANDIDATE_REFINE
    kwargs: dict[str, object] = {
        "charge": int(ts_record.charge or 0),
        "multiplicity": int(ts_record.multiplicity or 1),
        "output_dir": stage_dir,
        **fidelity.ts_kwargs(),
    }
    result = backend.transition_state_opt(
        _copy_coordinates(ts_point.geometry),
        list(ts_record.symbols),
        **kwargs,
    )

    if not result.success:
        rescue_plan = build_rescue_plan("geometry_not_converged", "ts")
        if not rescue_plan.terminal and rescue_plan.actions:
            from acp.mechanism.rescue import apply_rescue_kwargs

            action = rescue_plan.actions[0]
            rescue_kwargs = apply_rescue_kwargs(action, kwargs)
            rescue_dir = stage_dir / "rescue"
            result = backend.transition_state_opt(
                _copy_coordinates(ts_point.geometry),
                list(ts_record.symbols),
                **{**rescue_kwargs, "output_dir": rescue_dir},
            )

    if result.success and result.coordinates is not None:
        ts_record = _update_record_coordinates(
            ts_record,
            _copy_coordinates(result.coordinates),
            energy_hartree=result.energy_hartree,
        )
        _replace_record(data, ts_record)

    return _finish_stage(
        ctx,
        data,
        STAGE_CANDIDATE_REFINE,
        {
            "record_id": ts_record.id,
            "candidate_id": primary_ts.candidate_id,
            "success": result.success,
            "energy_hartree": result.energy_hartree,
            "imaginary_frequencies": list(getattr(result, "imaginary_frequencies", [])),
        },
    )


def stage_ts_optimize(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Full-fidelity TS optimization of the refined candidate."""
    _start_stage(ctx, STAGE_TS_OPTIMIZE)
    ts_record = _ensure_role_record(data, "transition_state")
    coordinates = _copy_coordinates(ts_record.coordinates)
    if coordinates is None:
        raise ValueError("transition_state record has no coordinates")

    fidelity = _resolve_stage_fidelity(ctx, "guided-scan", str(params.get("fidelity")))
    backend = _backend(ctx, "orca")
    stage_dir = Path(str(ctx.work_dir)) / "stages" / STAGE_TS_OPTIMIZE
    result = backend.transition_state_opt(
        coordinates,
        list(ts_record.symbols),
        charge=int(ts_record.charge or 0),
        multiplicity=int(ts_record.multiplicity or 1),
        output_dir=stage_dir,
        **fidelity.ts_kwargs(),
    )

    if result.success and result.coordinates is not None:
        ts_record = _update_record_coordinates(
            ts_record,
            _copy_coordinates(result.coordinates),
            energy_hartree=result.energy_hartree,
        )
        _replace_record(data, ts_record)

    return _finish_stage(
        ctx,
        data,
        STAGE_TS_OPTIMIZE,
        {
            "record_id": ts_record.id,
            "success": result.success,
            "energy_hartree": result.energy_hartree,
            "imaginary_frequencies": list(getattr(result, "imaginary_frequencies", [])),
            "frequencies": list(getattr(result, "all_frequencies", [])),
        },
    )


def stage_ts_validate(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Validate the TS via imaginary-mode count + reaction-coordinate overlap.

    Produces a :class:`TsValidation`; only a validated TS proceeds to IRC.
    """
    _start_stage(ctx, STAGE_TS_VALIDATE)
    ts_record = _ensure_role_record(data, "transition_state")
    stage_result = _stage_results(data).get(STAGE_TS_OPTIMIZE, {})
    raw_frequencies = stage_result.get("frequencies")
    frequencies: list[float] = []
    if isinstance(raw_frequencies, (list, tuple)):
        frequencies = [float(f) for f in raw_frequencies]

    identity = _identity_from_frequencies(frequencies)

    _ = TsValidation(identities=[identity], selected_candidate_id=ts_record.id)
    _mechanism_metadata(data)["ts_validation"] = {
        "valid": identity.valid,
        "imaginary_count": identity.imaginary_count,
        "imaginary_frequency_cm1": identity.imaginary_frequency_cm1,
        "messages": identity.messages,
    }
    return _finish_stage(
        ctx,
        data,
        STAGE_TS_VALIDATE,
        {
            "record_id": ts_record.id,
            "valid": identity.valid,
            "imaginary_count": identity.imaginary_count,
            "imaginary_frequency_cm1": identity.imaginary_frequency_cm1,
            "messages": identity.messages,
        },
    )


def _identity_from_frequencies(frequencies: list[float]) -> TsIdentity:
    from acp.mechanism.identity import classify_ts_identity

    return classify_ts_identity([f for f in frequencies if f < 0.0], topology_sane=True)


def stage_irc_validate(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """IRC endpoint discovery from the validated TS (forward + reverse)."""
    _start_stage(ctx, STAGE_IRC_VALIDATE)
    ts_record = _ensure_role_record(data, "transition_state")
    validation = _mechanism_metadata(data).get("ts_validation")
    if isinstance(validation, dict) and not validation.get("valid", False):
        return _finish_stage(
            ctx,
            data,
            STAGE_IRC_VALIDATE,
            {"action": "ts_not_validated", "skipped": True},
        )

    coordinates = _copy_coordinates(ts_record.coordinates)
    if coordinates is None:
        return _finish_stage(
            ctx,
            data,
            STAGE_IRC_VALIDATE,
            {"action": "no_coordinates", "skipped": True},
        )

    fidelity = _resolve_stage_fidelity(ctx, "guided-scan", str(params.get("fidelity")))
    backend = _backend(ctx, "orca")
    stage_dir = Path(str(ctx.work_dir)) / "stages" / STAGE_IRC_VALIDATE
    result = backend.irc(
        coordinates,
        list(ts_record.symbols),
        charge=int(ts_record.charge or 0),
        multiplicity=int(ts_record.multiplicity or 1),
        output_dir=stage_dir,
        method=fidelity.freq_method,
        basis=fidelity.freq_basis,
        direction="both",
        max_iter=fidelity.irc_points,
    )

    forward = int(getattr(result, "forward_points", 0) or 0)
    reverse = int(getattr(result, "reverse_points", 0) or 0)
    endpoints_raw = getattr(result, "endpoints", None)
    endpoints: dict[str, object] = dict(endpoints_raw) if isinstance(endpoints_raw, dict) else {}

    return _finish_stage(
        ctx,
        data,
        STAGE_IRC_VALIDATE,
        {
            "record_id": ts_record.id,
            "success": result.success,
            "forward_points": forward,
            "reverse_points": reverse,
            "endpoints": {k: str(v) for k, v in endpoints.items()},
            "connectivity": "reactant <-> product" if (forward and reverse) else "incomplete",
        },
    )


def stage_energy_analysis(
    ctx: WorkflowContext,
    data: StructureEnsemble,
    **params: object,
) -> StructureEnsemble:
    """Compile the energy profile (barriers in kcal/mol)."""
    _start_stage(ctx, STAGE_ENERGY_ANALYSIS)
    reactant = _base_record(data)
    product = _find_record_by_role(data, "product")
    transition_state = _find_record_by_role(data, "transition_state")

    if transition_state is None:
        raise ValueError("No transition_state record available for energy analysis")

    thermo_provider_name: str | None = None
    reactant, provider_name = _maybe_apply_record_thermochemistry(ctx, reactant, data.temperature)
    if provider_name is not None:
        _replace_record(data, reactant)
        thermo_provider_name = provider_name
    if product is not None:
        product, provider_name = _maybe_apply_record_thermochemistry(ctx, product, data.temperature)
        if provider_name is not None:
            _replace_record(data, product)
            thermo_provider_name = provider_name
    transition_state, provider_name = _maybe_apply_record_thermochemistry(
        ctx,
        transition_state,
        data.temperature,
    )
    if provider_name is not None:
        _replace_record(data, transition_state)
        thermo_provider_name = provider_name

    reactant_reference = (
        reactant.free_energy_hartree
        if reactant.free_energy_hartree is not None
        else reactant.energy_hartree
    )
    product_reference = (
        product.free_energy_hartree
        if product is not None and product.free_energy_hartree is not None
        else (product.energy_hartree if product is not None else None)
    )
    transition_state_reference = (
        transition_state.free_energy_hartree
        if transition_state.free_energy_hartree is not None
        else transition_state.energy_hartree
    )

    profile = {
        "reactant_id": reactant.id,
        "product_id": product.id if product is not None else None,
        "transition_state_id": transition_state.id,
        "reactant_energy_hartree": reactant_reference,
        "product_energy_hartree": product_reference,
        "transition_state_energy_hartree": transition_state_reference,
        "forward_barrier_kcal_mol": _energy_delta_kcal(
            reactant_reference,
            transition_state_reference,
        ),
        "reverse_barrier_kcal_mol": _energy_delta_kcal(
            product_reference,
            transition_state_reference,
        ),
        "reaction_energy_kcal_mol": _energy_delta_kcal(
            reactant_reference,
            product_reference,
        ),
        "energy_source": (
            "free_energy_hartree"
            if any(
                value is not None
                for value in (
                    reactant.free_energy_hartree,
                    transition_state.free_energy_hartree,
                    product.free_energy_hartree if product is not None else None,
                )
            )
            else "energy_hartree"
        ),
        "thermochemistry_provider": thermo_provider_name,
    }
    data.metadata["energy_profile"] = profile
    _mechanism_metadata(data)["energy_profile"] = profile

    return _finish_stage(
        ctx,
        data,
        STAGE_ENERGY_ANALYSIS,
        {"action": "energy_analysis", "profile": profile},
    )


def get_mechanism_stages() -> list[Stage]:
    """Return the stage list for mechanism analysis."""
    return [
        Stage(STAGE_PREPARE, stage_prepare_reaction),
        Stage(STAGE_REACTANT_OPT, stage_reactant_optimize),
        Stage(STAGE_PRODUCT_OPT, stage_product_optimize),
        Stage(STAGE_PATH_SEARCH, stage_path_search),
        Stage(STAGE_CANDIDATE_REFINE, stage_candidate_refine),
        Stage(STAGE_TS_OPTIMIZE, stage_ts_optimize),
        Stage(STAGE_TS_VALIDATE, stage_ts_validate),
        Stage(STAGE_IRC_VALIDATE, stage_irc_validate),
        Stage(STAGE_ENERGY_ANALYSIS, stage_energy_analysis),
    ]


def run_mechanism_analysis(
    input_source: str,
    output_dir: str | Path = "./mechanism_output",
    config: dict[str, Any] | None = None,
    name: str | None = None,
    charge: int | None = None,
    multiplicity: int | None = None,
    *,
    product_source: str | None = None,
    ts_guess_source: str | None = None,
    routes: list[dict[str, Any]] | None = None,
    strategy: str | None = None,
    fidelity: str | None = None,
    scan_points: int | None = None,
    irc_points: int | None = None,
) -> WorkflowResult:
    """Run mechanism analysis: prepare → opt → path search → TS → IRC → energy.

    Args:
        input_source: Reactant SMILES or input file path.
        output_dir: Output directory.
        config: Optional config overrides.
        name: Molecule name override.
        charge / multiplicity: Reactant charge / spin multiplicity.
        product_source: Optional product SMILES / file path.
        ts_guess_source: Optional TS-guess SMILES / file path.
        routes: Optional list of route dicts (coordinate plans).
        strategy: Path strategy override (guided-scan / rph-reverse / direct-ts).
        fidelity: Fidelity override (s3 / s4).
        scan_points: Optional relaxed-scan frame override.
        irc_points: Optional IRC MaxIter override.
    """
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

    routes_parsed: list[MechanismRoute] = []
    for route_dict in routes or []:
        routes_parsed.append(MechanismRoute.from_dict(route_dict))

    m_input = MechanismInput(
        reactant=input_source,
        product=product_source,
        ts_guess=ts_guess_source,
        routes=routes_parsed,
        charge=charge,
        multiplicity=multiplicity,
    )

    context = WorkflowContext(
        work_dir=output_root,
        state=state,
        config=cfg,
        backends={},
        params={
            _MOLECULE_NAME_KEY: structure.id,
            "mechanism_input": m_input,
            "strategy": strategy
            or (routes_parsed[0].path_strategy if routes_parsed else "guided-scan"),
            "fidelity": fidelity or (routes_parsed[0].fidelity if routes_parsed else "s3"),
            _FIDELITY_OVERRIDES_KEY: {
                "scan_points": scan_points,
                "irc_points": irc_points,
            },
        },
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
        "mechanism": _mechanism_metadata(result.ensemble) if result.ensemble is not None else {},
    }
    return result


__all__ = [
    "LEGACY_STAGE_ALIASES",
    "STAGE_CANDIDATE_REFINE",
    "STAGE_ENERGY_ANALYSIS",
    "STAGE_IRC_VALIDATE",
    "STAGE_PATH_SEARCH",
    "STAGE_PREPARE",
    "STAGE_PRODUCT_OPT",
    "STAGE_REACTANT_OPT",
    "STAGE_TS_OPTIMIZE",
    "STAGE_TS_VALIDATE",
    "get_mechanism_stages",
    "run_mechanism_analysis",
    "stage_candidate_refine",
    "stage_energy_analysis",
    "stage_irc_validate",
    "stage_path_search",
    "stage_prepare_reaction",
    "stage_product_optimize",
    "stage_reactant_optimize",
    "stage_ts_optimize",
    "stage_ts_validate",
]
