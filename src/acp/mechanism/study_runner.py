# pyright: reportMissingImports=false, reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnannotatedClassAttribute=false, reportMissingTypeArgument=false, reportArgumentType=false, reportUnusedCallResult=false, reportImplicitStringConcatenation=false, reportUnusedVariable=false, reportUnusedParameter=false
"""Study-runner wiring for the contract-first mechanism orchestrator."""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from acp.core.models import Structure
from acp.io.structures import StructureReader
from acp.mechanism._helpers import distance as _distance
from acp.mechanism._helpers import mapping_pairs_from_occurrence as _mapping_pairs_from_occurrence
from acp.mechanism.endpoint import (
    EndpointMatchThresholds,
    connectivity_fingerprint,
    perceive_connectivity,
)
from acp.mechanism.models import (
    ArtifactRef,
    AtomIdentityMap,
    MechanismRoute,
    MechanismStudy,
    PathCandidate,
    PathPoint,
    PathResult,
    StableState,
)
from acp.mechanism.orchestrator import StudyOrchestrator
from acp.mechanism.presets import (
    FIDELITY_PROFILES,
    RPH_CENSO_LITE_MODE,
    XTB_FAST_MODE,
    resolve_fidelity,
    resolve_strategy,
)
from acp.mechanism.providers.guided_scan import GuidedScanPathStrategy
from acp.mechanism.providers.rph_adapter import (
    RPHEnsembleProvider,
    RPHPathSearchStrategy,
    RPHRefinementProvider,
    RPHUnavailableError,
    rph_version,
)
from acp.mechanism.providers.xtb_ensemble import XtbFastEnsembleProvider
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan

logger = logging.getLogger(__name__)


class DirectTsStrategy:
    """Thin contract-layer adapter for direct TS handoff."""

    def __init__(self) -> None:
        self.strategy_id = "direct-ts"
        self.strategy_version = "1.0"

    def search(
        self,
        source_state: StableState,
        target_state: StableState | None,
        coordinate_plan: ReactionCoordinatePlan,
        profile: Any,
    ) -> PathResult:
        coordinates, symbols = _path_geometry(source_state)
        point = PathPoint(
            point_id="p000",
            progress=0.5,
            coordinate_values=dict(coordinate_plan.coordinate_targets(0)),
            reaction_coordinates=dict(coordinate_plan.coordinate_targets(0)),
            geometry=coordinates,
        )
        candidate = PathCandidate(
            candidate_id="ts_candidate_01",
            kind="ts_seed",
            point_id=point.point_id,
            reason="user_supplied_ts_guess",
            progress=point.progress,
            score=0.0,
        )
        return PathResult(
            points=[point],
            candidates=[candidate],
            strategy=self.strategy_id,
            route_id=str(
                source_state.metadata.get("route_id") or f"{source_state.state_id}__direct_ts"
            ),
            selected_ts_id=candidate.candidate_id,
            metadata={
                "profile": getattr(profile, "name", str(profile)),
                "symbols": list(symbols),
            },
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            complete=True,
            endpoint_evidence={
                "source_state_id": source_state.state_id,
                "target_state_id": target_state.state_id if target_state is not None else None,
            },
        )


def build_study_providers(
    conformer_mode: str,
    strategy: str,
    fidelity: str,
    config: dict,
) -> dict[str, Any]:
    """Build the provider bundle required by :class:`StudyOrchestrator`."""

    resolved_strategy = resolve_strategy(strategy)
    resolved_fidelity = resolve_fidelity(fidelity)
    fidelity_profile = FIDELITY_PROFILES[resolved_fidelity]
    rph_available = _rph_is_available(config)
    resolved_conformer_mode = _resolve_conformer_mode(conformer_mode, rph_available)

    if resolved_conformer_mode == "censo-lite":
        if not rph_available:
            raise RuntimeError(
                "Mechanism study conformer_mode='censo-lite' requires a working "
                "ReactionProfileHunter checkout (set ACP_RPH_PATH or config['rph']['path'])."
            )
        ensemble_provider: Any = RPHEnsembleProvider(config=config)
        ensemble_profile: Any = RPH_CENSO_LITE_MODE
    else:
        ensemble_provider = XtbFastEnsembleProvider(config=config)
        ensemble_profile = XTB_FAST_MODE

    if resolved_strategy == "guided-scan":
        path_strategy: Any = GuidedScanPathStrategy(config=config)
    elif resolved_strategy == "rph-reverse":
        if not rph_available:
            raise RuntimeError(
                "Mechanism study strategy='rph-reverse' requires ReactionProfileHunter; "
                "configure ACP_RPH_PATH or config['rph']['path']."
            )
        path_strategy = RPHPathSearchStrategy(config=config)
    elif resolved_strategy == "direct-ts":
        path_strategy = DirectTsStrategy()
    else:
        raise ValueError(f"Unsupported mechanism study strategy: {strategy!r}")

    if not rph_available:
        raise RuntimeError(
            "Mechanism study refinement for fidelity "
            f"{resolved_fidelity!r} requires ReactionProfileHunter. "
            "No null/fake refinement path was requested, so the study cannot proceed."
        )
    refinement_provider = RPHRefinementProvider(config=config)

    return {
        "ensemble_provider": ensemble_provider,
        "path_strategy": path_strategy,
        "refinement_provider": refinement_provider,
        "resolved_conformer_mode": resolved_conformer_mode,
        "ensemble_profile": ensemble_profile,
        "low_fidelity_profile": fidelity_profile,
        "high_fidelity_profile": FIDELITY_PROFILES["s4"],
    }


def run_mechanism_study(
    input_source: str,
    output_dir: str | Path,
    config: dict[str, Any] | None,
    name: str | None,
    charge: int | None,
    multiplicity: int | None,
    *,
    product_source: str | None = None,
    ts_guess_source: str | None = None,
    routes: list[dict[str, Any]] | None = None,
    strategy: str | None = None,
    fidelity: str | None = None,
    scan_points: int | None = None,
    irc_points: int | None = None,
    study_id: str | None = None,
    conformer_mode: str = "auto",
    max_elementary_steps: int = 3,
    int_extension: bool = False,
    promotion_policy: str = "all_confirmed",
    auto_converge: bool = False,
) -> dict[str, Any]:
    """Build and run a mechanism study through :class:`StudyOrchestrator`."""

    cfg = dict(config or {})
    resolved_strategy = resolve_strategy(strategy)
    resolved_fidelity = resolve_fidelity(fidelity)
    study_name = study_id or _default_study_id(input_source, product_source)
    study_root = Path(output_dir)
    study_dir = _resolve_study_dir(study_root, study_name)

    reactant = _read_structure(input_source, charge=charge, multiplicity=multiplicity, name=name)
    product = (
        _read_structure(product_source, charge=charge, multiplicity=multiplicity, name=None)
        if product_source
        else None
    )
    ts_guess = (
        _read_structure(ts_guess_source, charge=charge, multiplicity=multiplicity, name=None)
        if ts_guess_source
        else None
    )

    thresholds = EndpointMatchThresholds()
    stable_states, atom_identity_map = _build_initial_states(
        study_dir=study_dir,
        reactant=reactant,
        reactant_source=input_source,
        product=product,
        product_source=product_source,
        thresholds=thresholds,
        ts_guess=ts_guess,
    )
    route_models = _build_routes(
        routes=routes,
        reactant=reactant,
        product=product,
        strategy=resolved_strategy,
        fidelity=resolved_fidelity,
        scan_points=scan_points,
        ts_guess_present=ts_guess is not None,
    )

    reactant_state = stable_states[0]
    product_state = next((state for state in stable_states if state.role == "product"), None)
    for index, route in enumerate(route_models, start=1):
        normalized_route = replace(
            route,
            route_id=route.route_id or f"route_{index}",
            reactant_id=route.reactant_id or reactant_state.state_id,
            product_id=(
                route.product_id
                if route.product_id is not None
                else (product_state.state_id if product_state is not None else None)
            ),
            ts_guess_id=route.ts_guess_id or ("ts_guess_01" if ts_guess is not None else None),
            path_strategy=resolved_strategy if strategy is not None else route.path_strategy,
            fidelity=resolved_fidelity if fidelity is not None else route.fidelity,
        )
        route_models[index - 1] = normalized_route

    study = MechanismStudy(
        study_id=study_name,
        reactant_id=reactant_state.state_id,
        product_id=product_state.state_id if product_state is not None else None,
        ts_guess_id="ts_guess_01" if ts_guess is not None else None,
        atom_identity_map=atom_identity_map,
        stable_states=stable_states,
        routes=route_models,
    )
    study.frontier.max_depth = max_elementary_steps if int_extension else 0
    study.metadata["study_runner"] = {
        "conformer_mode": conformer_mode,
        "strategy": resolved_strategy,
        "fidelity": resolved_fidelity,
        "scan_points": scan_points,
        "irc_points": irc_points,
        "study_id": study_name,
        "max_elementary_steps": max_elementary_steps,
        "int_extension": int_extension,
        "promotion_policy": promotion_policy,
        "auto_converge": auto_converge,
        "config": cfg,
    }

    providers = build_study_providers(conformer_mode, resolved_strategy, resolved_fidelity, cfg)
    low_fidelity_profile = providers["low_fidelity_profile"]
    if irc_points is not None:
        low_fidelity_profile = replace(low_fidelity_profile, irc_points=int(irc_points))
    endpoint_provider = _build_endpoint_provider(study_dir, cfg)

    orchestrator = StudyOrchestrator(
        study,
        study_root=study_root,
        ensemble_provider=providers["ensemble_provider"],
        path_strategy=providers["path_strategy"],
        refinement_provider=providers["refinement_provider"],
        endpoint_provider=endpoint_provider,
        ensemble_profile=providers["ensemble_profile"],
        low_fidelity_profile=low_fidelity_profile,
        high_fidelity_profile=providers["high_fidelity_profile"],
        max_elementary_steps=max_elementary_steps,
    )
    result = orchestrator.run()
    if auto_converge and result.status == "waiting":
        result = _auto_resume(orchestrator)
    return _study_summary(result)


def resume_mechanism_study(
    *,
    study_id: str,
    study_root: str | Path,
    decision_resolutions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resume a persisted mechanism study from its checkpoint bundle."""

    root = Path(study_root)
    study_dir = _resolve_study_dir(root, study_id)
    study_path = study_dir / "study.json"
    if not study_path.exists():
        raise FileNotFoundError(f"Mechanism study checkpoint not found: {study_path}")

    payload = json.loads(study_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Mechanism study JSON must be an object: {study_path}")
    study = MechanismStudy.from_dict(payload)
    runner_meta = study.metadata.get("study_runner")
    if not isinstance(runner_meta, dict):
        raise ValueError("Mechanism study metadata is missing the study_runner bundle")

    cfg = dict(runner_meta.get("config") or {})
    resolved_strategy = resolve_strategy(str(runner_meta.get("strategy") or "guided-scan"))
    resolved_fidelity = resolve_fidelity(str(runner_meta.get("fidelity") or "s3"))
    conformer_mode = str(runner_meta.get("conformer_mode") or "auto")
    providers = build_study_providers(conformer_mode, resolved_strategy, resolved_fidelity, cfg)
    low_fidelity_profile = providers["low_fidelity_profile"]
    irc_points = runner_meta.get("irc_points")
    if isinstance(irc_points, int):
        low_fidelity_profile = replace(low_fidelity_profile, irc_points=irc_points)

    orchestrator = StudyOrchestrator(
        study,
        study_root=root,
        ensemble_provider=providers["ensemble_provider"],
        path_strategy=providers["path_strategy"],
        refinement_provider=providers["refinement_provider"],
        endpoint_provider=_build_endpoint_provider(study_dir, cfg),
        ensemble_profile=providers["ensemble_profile"],
        low_fidelity_profile=low_fidelity_profile,
        high_fidelity_profile=providers["high_fidelity_profile"],
        max_elementary_steps=int(runner_meta.get("max_elementary_steps") or 3),
    )
    result = orchestrator.resume(dict(decision_resolutions or {}))
    if _as_bool(runner_meta.get("auto_converge")) and result.status == "waiting":
        result = _auto_resume(orchestrator)
    return _study_summary(result)


def _auto_resume(orchestrator: StudyOrchestrator) -> MechanismStudy:
    result = orchestrator.study
    for _ in range(max(1, int(orchestrator.max_elementary_steps) * 2)):
        waiting = [
            decision.id for decision in result.decision_points if decision.status == "waiting"
        ]
        if not waiting or result.status != "waiting":
            return result
        result = orchestrator.resume({decision_id: "continue" for decision_id in waiting})
        if result.status != "waiting":
            return result
    logger.warning(
        "Mechanism study %s remained in waiting state after auto-converge retries",
        result.study_id,
    )
    return result


def _build_endpoint_provider(study_dir: Path, config: dict[str, Any]) -> Any:
    from acp.mechanism.endpoint import DefaultEndpointProvider

    return DefaultEndpointProvider(
        backend=_build_orca_backend(config),
        work_root=study_dir / "sr",
    )


def _build_orca_backend(config: dict[str, Any]) -> Any | None:
    from acp.backends.registry import get_backend

    try:
        backend_cls = get_backend("orca")
    except KeyError:
        _ = importlib.import_module("acp.backends")
        backend_cls = get_backend("orca")
    return backend_cls(config)


def _rph_is_available(config: dict[str, Any]) -> bool:
    try:
        _ = rph_version(config=config)
    except RPHUnavailableError:
        return False
    return True


def _resolve_conformer_mode(mode: str, rph_available: bool) -> str:
    normalized = str(mode or "auto").strip().lower()
    if normalized == "auto":
        return "censo-lite" if rph_available else "xtb-fast"
    if normalized in {"censo-lite", "xtb-fast"}:
        return normalized
    raise ValueError(f"Unsupported conformer mode: {mode!r}")


def _build_initial_states(
    *,
    study_dir: Path,
    reactant: Structure,
    reactant_source: str,
    product: Structure | None,
    product_source: str | None,
    thresholds: EndpointMatchThresholds,
    ts_guess: Structure | None,
) -> tuple[list[StableState], AtomIdentityMap | None]:
    atom_identity_map = _build_atom_identity_map(reactant, product)
    stable_states = [
        _build_stable_state(
            role="reactant",
            state_id="state_reactant",
            structure=reactant,
            source_input=reactant_source,
            study_dir=study_dir,
            thresholds=thresholds,
            atom_mapping=_state_atom_mapping(reactant),
            ts_guess=ts_guess,
        )
    ]
    if product is not None:
        stable_states.append(
            _build_stable_state(
                role="product",
                state_id="state_product",
                structure=product,
                source_input=product_source or "",
                study_dir=study_dir,
                thresholds=thresholds,
                atom_mapping=_state_atom_mapping(product),
                ts_guess=None,
            )
        )
    return stable_states, atom_identity_map


def _build_stable_state(
    *,
    role: str,
    state_id: str,
    structure: Structure,
    source_input: str,
    study_dir: Path,
    thresholds: EndpointMatchThresholds,
    atom_mapping: dict[str, int] | None,
    ts_guess: Structure | None,
) -> StableState:
    coordinates = _require_coordinates(structure)
    geometry_path = study_dir / "inputs" / f"{state_id}.xyz"
    _write_xyz(geometry_path, structure, title=f"{role} input")
    fingerprint = _identity_fingerprint(structure, thresholds)
    metadata: dict[str, Any] = {
        "source_input": source_input,
        "input": source_input,
        "structure_id": structure.id,
        "symbols": list(structure.symbols),
        "coordinates": coordinates.tolist(),
        "atom_mapping": dict(atom_mapping or {}),
        "validated_minimum": role in {"reactant", "product"},
        "state_role": role,
    }
    if ts_guess is not None:
        metadata["ts_guess_symbols"] = list(ts_guess.symbols)
        metadata["ts_guess_coordinates"] = _require_coordinates(ts_guess).tolist()
    return StableState(
        state_id=state_id,
        role=role,
        canonical_geometry=_artifact_ref(geometry_path, kind="stable_state_geometry"),
        charge=structure.charge,
        multiplicity=structure.multiplicity,
        identity_fingerprint=fingerprint,
        metadata=metadata,
    )


def _build_atom_identity_map(
    reactant: Structure,
    product: Structure | None,
) -> AtomIdentityMap | None:
    reactant_mapping = _state_atom_mapping(reactant)
    if reactant_mapping is None:
        return None
    mapping: dict[str, dict[str, int]] = {"state_reactant": dict(reactant_mapping)}
    if product is not None:
        product_mapping = _state_atom_mapping(product)
        if product_mapping is not None:
            mapping["state_product"] = dict(product_mapping)
    return AtomIdentityMap(uid_to_structure_index=dict(reactant_mapping), mapping=mapping)


def _state_atom_mapping(structure: Structure) -> dict[str, int] | None:
    coordinates = structure.coordinates
    if coordinates is None:
        return None
    return {f"a{index + 1}": index for index in range(len(coordinates))}


def _build_routes(
    *,
    routes: list[dict[str, Any]] | None,
    reactant: Structure,
    product: Structure | None,
    strategy: str,
    fidelity: str,
    scan_points: int | None,
    ts_guess_present: bool,
) -> list[MechanismRoute]:
    if routes:
        parsed: list[MechanismRoute] = [
            route if isinstance(route, MechanismRoute) else MechanismRoute.from_dict(dict(route))
            for route in routes
        ]
        if scan_points is not None:
            parsed = [
                replace(
                    route,
                    coordinate_plan=replace(route.coordinate_plan, points=int(scan_points)),
                )
                for route in parsed
            ]
        return parsed

    if strategy == "direct-ts":
        return [
            MechanismRoute(
                route_id="route_1",
                coordinate_plan=_direct_ts_plan(reactant),
                path_strategy=strategy,
                fidelity=fidelity,
                ts_guess_id="ts_guess_01" if ts_guess_present else None,
            )
        ]

    if product is None:
        raise ValueError(
            "Mechanism study requires --product, --routes, or --ts-guess to define a route"
        )

    return [
        MechanismRoute(
            route_id="route_1",
            coordinate_plan=_infer_coordinate_plan(reactant, product, points=scan_points or 21),
            path_strategy=strategy,
            fidelity=fidelity,
        )
    ]


def _direct_ts_plan(structure: Structure) -> ReactionCoordinatePlan:
    coordinates = _require_coordinates(structure)
    if len(coordinates) < 2:
        raise ValueError("direct-ts requires at least two atoms to define a placeholder coordinate")
    distance = _distance(coordinates, 0, 1)
    return ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(
                id="rc1",
                kind="distance",
                atoms=(0, 1),
                start=distance,
                end=distance,
            ),
        ),
        points=2,
    )


def _infer_coordinate_plan(
    reactant: Structure,
    product: Structure,
    *,
    points: int,
) -> ReactionCoordinatePlan:
    reactant_coords = _require_coordinates(reactant)
    product_coords = _require_coordinates(product)
    if len(reactant.symbols) != len(product.symbols):
        raise ValueError(
            "Automatic mechanism-study route inference requires reactant/product atom "
            "counts to match"
        )
    if Counter(reactant.symbols) != Counter(product.symbols):
        raise ValueError(
            "Automatic mechanism-study route inference requires identical "
            "reactant/product compositions"
        )

    thresholds = EndpointMatchThresholds()
    reactant_edges = perceive_connectivity(reactant.symbols, reactant_coords, thresholds)
    product_edges_raw = perceive_connectivity(product.symbols, product_coords, thresholds)
    product_to_reactant = _mapping_pairs_from_occurrence(product.symbols, reactant.symbols)
    if product_to_reactant is None:
        raise ValueError(
            "Automatic mechanism-study route inference could not build an atom mapping"
        )
    product_edges = _map_edges_to_reactant(product_edges_raw, product_to_reactant)
    formed = sorted(product_edges - reactant_edges)
    broken = sorted(reactant_edges - product_edges)
    changed_pairs = formed + broken
    if not changed_pairs:
        changed_pairs = [_largest_distance_change_pair(reactant_coords, product_coords)]

    reactant_to_product = {
        reactant_index: product_index for product_index, reactant_index in product_to_reactant
    }
    specs: list[CoordinateSpec] = []
    for index, pair in enumerate(changed_pairs[:4], start=1):
        left, right = pair
        product_pair = (reactant_to_product[left], reactant_to_product[right])
        start = _distance(reactant_coords, left, right)
        end = _distance(product_coords, product_pair[0], product_pair[1])
        if abs(start - end) < 1.0e-4:
            end = end - 0.1 if pair in formed else end + 0.1
        specs.append(
            CoordinateSpec(
                id=f"rc{index}",
                kind="distance",
                atoms=pair,
                start=start,
                end=end,
            )
        )

    return ReactionCoordinatePlan(coordinates=tuple(specs), points=int(points))


def _map_edges_to_reactant(
    edges: set[tuple[int, int]],
    mapping_pairs: list[tuple[int, int]],
) -> set[tuple[int, int]]:
    mapping = {
        candidate_index: reference_index for candidate_index, reference_index in mapping_pairs
    }
    mapped: set[tuple[int, int]] = set()
    for left, right in edges:
        mapped_left = mapping[left]
        mapped_right = mapping[right]
        mapped.add((min(mapped_left, mapped_right), max(mapped_left, mapped_right)))
    return mapped


def _largest_distance_change_pair(
    reactant_coordinates: np.ndarray,
    product_coordinates: np.ndarray,
) -> tuple[int, int]:
    best_pair = (0, 1)
    best_delta = -1.0
    atom_count = len(reactant_coordinates)
    for left in range(atom_count):
        for right in range(left + 1, atom_count):
            delta = abs(
                _distance(reactant_coordinates, left, right)
                - _distance(product_coordinates, left, right)
            )
            if delta > best_delta:
                best_delta = delta
                best_pair = (left, right)
    return best_pair


def _path_geometry(state: StableState) -> tuple[np.ndarray, list[str]]:
    ts_guess_coordinates = state.metadata.get("ts_guess_coordinates")
    ts_guess_symbols = state.metadata.get("ts_guess_symbols")
    if ts_guess_coordinates is not None and ts_guess_symbols is not None:
        return np.asarray(ts_guess_coordinates, dtype=float), [
            str(symbol) for symbol in ts_guess_symbols
        ]
    if state.ensemble is not None:
        minimum = state.ensemble.global_minimum()
        if minimum is not None and minimum.coordinates is not None:
            return np.asarray(minimum.coordinates, dtype=float), list(minimum.symbols)
    coordinates = state.metadata.get("coordinates")
    symbols = state.metadata.get("symbols")
    if coordinates is None or symbols is None:
        raise ValueError(
            f"StableState {state.state_id!r} requires coordinates/symbols for path search"
        )
    return np.asarray(coordinates, dtype=float), [str(symbol) for symbol in symbols]


def _study_summary(study: MechanismStudy) -> dict[str, Any]:
    return {
        "study_id": study.study_id,
        "study_dir": study.study_dir,
        "status": study.status,
        "network_size": {
            "states": len(study.network.nodes),
            "elementary_steps": len(study.elementary_steps),
        },
        "gates_summary": {gate.gate_id: gate.status for gate in study.quality_gates},
        "pending_decisions": [
            decision.id for decision in study.decision_points if decision.status == "waiting"
        ],
    }


def write_review_payload(study_root: str | Path, summary: dict[str, Any]) -> Path:
    """Persist a waiting-study summary for the scheduler poller (exit-77 handoff).

    The JobManager reads this file when the study subprocess exits with
    :data:`acp.scheduler.jobs.EXIT_WAITING_REVIEW` and stores it under
    ``record.result["review_payload"]`` (mirrored into ``<work_dir>/job.json``).
    """
    root = Path(study_root)
    root.mkdir(parents=True, exist_ok=True)
    payload_path = root / "review_payload.json"
    payload_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return payload_path


def read_review_handoff(
    study_root: str | Path,
) -> tuple[str | None, dict[str, Any] | None]:
    """Read the scheduler job mirror for the resume handoff.

    The JobManager mirrors every job record to ``<work_dir>/job.json``. When a
    study paused at a review gate the record carries
    ``result.review_payload.study_id``; after a review action it additionally
    carries ``result.review_resolution.decisions`` (decision_id → resolution).

    Returns:
        ``(study_id, decisions)`` — both ``None`` when no handoff exists.
    """
    job_path = Path(study_root) / "job.json"
    if not job_path.exists():
        return None, None
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(job, dict):
        return None, None
    result = job.get("result")
    if not isinstance(result, dict):
        return None, None

    study_id: str | None = None
    review_payload = result.get("review_payload")
    if isinstance(review_payload, dict):
        raw = review_payload.get("study_id")
        if isinstance(raw, str) and raw:
            study_id = raw

    decisions: dict[str, Any] | None = None
    review_resolution = result.get("review_resolution")
    if isinstance(review_resolution, dict):
        raw = review_resolution.get("decisions")
        if isinstance(raw, dict) and raw:
            decisions = dict(raw)
    return study_id, decisions


def waiting_study_exists(study_root: str | Path, study_id: str) -> bool:
    """True when the persisted study checkpoint is paused at a review gate."""
    study_path = _resolve_study_dir(Path(study_root), study_id) / "study.json"
    if not study_path.exists():
        return False
    try:
        payload = json.loads(study_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "waiting"


def _read_structure(
    source: str,
    *,
    charge: int | None,
    multiplicity: int | None,
    name: str | None,
) -> Structure:
    structure = StructureReader().read(source, charge=charge, multiplicity=multiplicity)
    if not name:
        return structure
    return Structure(
        id=name,
        charge=structure.charge,
        multiplicity=structure.multiplicity,
        symbols=structure.symbols,
        coordinates=_require_coordinates(structure).copy(),
        metadata=dict(structure.metadata),
    )


def _identity_fingerprint(structure: Structure, thresholds: EndpointMatchThresholds) -> str:
    coordinates = _require_coordinates(structure)
    return _stable_hash(
        {
            "symbols": list(structure.symbols),
            "connectivity_fingerprint": connectivity_fingerprint(
                structure.symbols,
                coordinates,
                thresholds,
            ),
            "charge": structure.charge,
            "multiplicity": structure.multiplicity,
        }
    )


def _write_xyz(path: Path, structure: Structure, *, title: str) -> None:
    coordinates = _require_coordinates(structure)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [str(len(structure.symbols)), title]
    for symbol, row in zip(structure.symbols, coordinates):
        lines.append(f"{symbol} {row[0]:.10f} {row[1]:.10f} {row[2]:.10f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _artifact_ref(path: Path, *, kind: str) -> ArtifactRef:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return ArtifactRef(path=str(path), sha256=f"sha256:{digest}", kind=kind)


def _default_study_id(input_source: str, product_source: str | None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha256(f"{input_source}|{product_source or ''}".encode()).hexdigest()[:8]
    return f"study_{stamp}_{digest}"


def _resolve_study_dir(root: Path, study_id: str) -> Path:
    if root.name == study_id and root.parent.name == "mechanism_study":
        return root
    return root / "mechanism_study" / study_id


def _require_coordinates(structure: Structure) -> np.ndarray:
    if structure.coordinates is None:
        raise ValueError(f"Structure {structure.id!r} has no 3D coordinates")
    return np.asarray(structure.coordinates, dtype=float)


def _stable_hash(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


__all__ = [
    "DirectTsStrategy",
    "build_study_providers",
    "resume_mechanism_study",
    "run_mechanism_study",
]
