"""Mechanism-study report writers and S4 promotion-policy helpers."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from importlib import import_module
from pathlib import Path
from typing import Any, Literal

from acp.core.models import HARTREE_TO_KCAL

from ._helpers import opt_float as _opt_float
from ._helpers import write_json_atomic as _write_json_atomic
from .models import MechanismStudy, PathResult, ReactionNetwork, StableState, StationaryPoint

logger = logging.getLogger(__name__)

PromotionPolicy = Literal["all_confirmed", "rate_relevant", "user_selected"]

_REPORT_FILENAMES = {
    "reaction_network": "reaction_network.json",
    "mechanism_profile": "mechanism_profile.json",
    "stationary_points": "stationary_points.json",
    "quality_gates": "quality_gates.json",
    "provenance": "provenance.json",
}

_DEFAULT_RATE_THRESHOLDS = {
    "low_energy_window_kcal": 5.0,
    "low_barrier_window_kcal": 5.0,
    "competing_barrier_window_kcal": 2.0,
}


def write_study_reports(study_dir: Path) -> dict[str, Path]:
    """Write the five publication-grade JSON reports for a mechanism study."""

    root = Path(study_dir)
    study, study_notes = _load_study(root)
    network, network_notes = _load_network(root, study)
    route_payloads, route_notes = _collect_route_payloads(root, study)
    refinement_manifests, refinement_notes = _collect_refinement_manifests(root, study)
    canonical_point_ids = _canonical_point_ids(refinement_manifests)
    quality_payload, quality_notes = _load_quality_gates(root, study)

    outputs = {
        "reaction_network": root / _REPORT_FILENAMES["reaction_network"],
        "mechanism_profile": root / _REPORT_FILENAMES["mechanism_profile"],
        "stationary_points": root / _REPORT_FILENAMES["stationary_points"],
        "quality_gates": root / _REPORT_FILENAMES["quality_gates"],
        "provenance": root / _REPORT_FILENAMES["provenance"],
    }

    _write_json_atomic(
        outputs["reaction_network"],
        _build_reaction_network_report(study, network, study_notes + network_notes),
    )
    _write_json_atomic(
        outputs["mechanism_profile"],
        _build_mechanism_profile_report(
            study,
            route_payloads,
            refinement_manifests,
            canonical_point_ids,
            study_notes + route_notes + refinement_notes,
        ),
    )
    _write_json_atomic(
        outputs["stationary_points"],
        _build_stationary_points_report(
            study,
            canonical_point_ids,
            study_notes + refinement_notes,
        ),
    )
    _write_json_atomic(
        outputs["quality_gates"],
        _normalize_quality_gates_payload(quality_payload, quality_notes),
    )
    _write_json_atomic(
        outputs["provenance"],
        _build_provenance_report(
            study,
            route_payloads,
            refinement_manifests,
            study_notes + route_notes + refinement_notes,
        ),
    )
    return outputs


def select_s4_candidates(
    study: MechanismStudy | Mapping[str, Any],
    policy: PromotionPolicy,
    user_selection: Sequence[str] | None = None,
    *,
    thresholds: Mapping[str, float] | None = None,
) -> list[str]:
    """Select confirmed TS candidates for S4 promotion.

    ``rate_relevant`` favors confirmed TS points attached to low-energy states,
    with low barriers, and retains competing TSs within a configurable barrier
    window from the best competitor on the same source state.
    """

    study_obj = _coerce_study(study)
    ts_points = {
        point.point_id: point for point in study_obj.stationary_points if point.kind == "ts"
    }
    if policy == "user_selected":
        return _unique_in_order(
            point_id for point_id in user_selection or [] if point_id in ts_points
        )

    confirmed_ts_ids = [
        point_id for point_id, point in ts_points.items() if _is_confirmed_ts(point, study_obj)
    ]
    if policy == "all_confirmed":
        return confirmed_ts_ids

    limits = dict(_DEFAULT_RATE_THRESHOLDS)
    if thresholds is not None:
        for key, value in thresholds.items():
            limits[str(key)] = float(value)

    state_energies = {
        state.state_id: _canonical_state_energy(state) for state in study_obj.stable_states
    }
    finite_state_energies = [energy for energy in state_energies.values() if energy is not None]
    global_min = min(finite_state_energies) if finite_state_energies else None

    relevant_edges = [
        edge
        for edge in study_obj.elementary_steps
        if edge.status == "confirmed" and edge.ts_id in ts_points and edge.ts_id in confirmed_ts_ids
    ]
    if not relevant_edges:
        return []

    edge_metrics = {edge.step_id: _edge_barrier_metric(edge) for edge in relevant_edges}
    finite_barriers = [metric for metric in edge_metrics.values() if metric is not None]
    best_barrier = min(finite_barriers) if finite_barriers else None

    by_source: dict[str, list[Any]] = defaultdict(list)
    for edge in relevant_edges:
        by_source[edge.source_state_id].append(edge)

    selected: list[str] = []
    for edge in relevant_edges:
        metric = edge_metrics.get(edge.step_id)
        low_barrier = (
            metric is not None
            and best_barrier is not None
            and metric <= best_barrier + limits["low_barrier_window_kcal"]
        )
        low_energy = _edge_touches_low_energy_state(
            edge,
            state_energies,
            global_min,
            limits["low_energy_window_kcal"],
        )
        competing = _is_competing_ts(
            edge,
            by_source[edge.source_state_id],
            edge_metrics,
            limits["competing_barrier_window_kcal"],
        )
        if (low_energy and low_barrier) or competing:
            selected.append(edge.ts_id)
    if not selected and confirmed_ts_ids:
        ordered = sorted(
            confirmed_ts_ids,
            key=lambda point_id: (
                _edge_barrier_metric_for_ts(point_id, relevant_edges),
                point_id,
            ),
        )
        selected.append(ordered[0])
    return _unique_in_order(selected)


def _load_study(root: Path) -> tuple[MechanismStudy, list[str]]:
    path = root / "study.json"
    payload = _read_json_object(path)
    if payload is None:
        return MechanismStudy(study_id=root.name, study_dir=str(root)), [
            "study.json missing; using empty study"
        ]
    study = MechanismStudy.from_dict(payload)
    study.study_dir = str(root)
    return study, []


def _load_network(root: Path, study: MechanismStudy) -> tuple[ReactionNetwork, list[str]]:
    path = root / "network.json"
    payload = _read_json_object(path)
    if payload is None:
        return study.network, ["network.json missing; using study.network"]
    return ReactionNetwork.from_dict(payload), []


def _collect_route_payloads(
    root: Path,
    study: MechanismStudy,
) -> tuple[list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    route_payloads: dict[str, dict[str, Any]] = {}
    routes_dir = root / "routes"
    if routes_dir.exists():
        for manifest_path in sorted(routes_dir.glob("*/path_manifest.json")):
            manifest_payload = _read_json_object(manifest_path)
            if manifest_payload is None:
                continue
            key = _route_payload_key(manifest_payload)
            route_payloads[key] = manifest_payload
    else:
        notes.append("routes/ directory missing; using study metadata path_results when available")

    route_statuses = study.metadata.get("route_statuses")
    route_status_payload = route_statuses if isinstance(route_statuses, dict) else {}
    path_results = study.metadata.get("path_results")
    if isinstance(path_results, dict):
        for exploration_key, result_data in path_results.items():
            if not isinstance(result_data, dict):
                continue
            source_state_id, route_id = _split_exploration_key(str(exploration_key))
            key = f"{source_state_id}::{route_id}"
            if key in route_payloads:
                continue
            payload: dict[str, Any] = {
                "source_state_id": source_state_id,
                "route_id": route_id,
                "path_result": result_data,
            }
            route_record = route_status_payload.get(key)
            if isinstance(route_record, dict):
                payload["status"] = route_record.get("status")
                payload["fingerprint"] = route_record.get("fingerprint")
            route = next(
                (candidate for candidate in study.routes if candidate.route_id == route_id),
                None,
            )
            if route is not None:
                payload["route"] = route.to_dict()
            route_payloads[key] = payload
    if not route_payloads:
        notes.append("No route manifests or path_results available")
    return [route_payloads[key] for key in sorted(route_payloads)], notes


def _collect_refinement_manifests(
    root: Path,
    study: MechanismStudy,
) -> tuple[list[Any], list[str]]:
    notes: list[str] = []
    manifests: dict[str, Any] = {}
    manifest_cls = import_module("acp.mechanism.providers.contracts").RefinementManifest
    refinements_dir = root / "refinements"
    if refinements_dir.exists():
        for manifest_path in sorted(refinements_dir.glob("*/refinement_manifest.json")):
            payload = _read_json_object(manifest_path)
            if payload is None:
                continue
            manifest = manifest_cls.from_dict(payload)
            manifests[manifest.manifest_id] = manifest
    else:
        notes.append(
            "refinements/ directory missing; using study metadata refinement_manifests "
            "when available"
        )
    metadata_manifests = study.metadata.get("refinement_manifests")
    if isinstance(metadata_manifests, dict):
        for manifest_id, payload in metadata_manifests.items():
            if manifest_id in manifests or not isinstance(payload, dict):
                continue
            manifests[manifest_id] = manifest_cls.from_dict(payload)
    if not manifests:
        notes.append("No refinement manifests available")
    return [manifests[key] for key in sorted(manifests)], notes


def _load_quality_gates(root: Path, study: MechanismStudy) -> tuple[dict[str, Any], list[str]]:
    path = root / "quality_gates.json"
    payload = _read_json_object(path)
    if payload is not None:
        return payload, []
    if study.quality_gates:
        return {
            "quality_gates": [gate.to_dict() for gate in study.quality_gates],
        }, ["quality_gates.json missing; using gates embedded in study.json"]
    return {"quality_gates": []}, ["quality_gates.json missing; no gate records available"]


def _build_reaction_network_report(
    study: MechanismStudy,
    network: ReactionNetwork,
    notes: list[str],
) -> dict[str, Any]:
    state_map = {state.state_id: state for state in study.stable_states}
    nodes = []
    for node in sorted(network.nodes.values(), key=lambda item: item.state_id):
        state = state_map.get(node.state_id)
        nodes.append(
            {
                "state_id": node.state_id,
                "role": state.role if state is not None else None,
                "charge": node.charge,
                "multiplicity": node.multiplicity,
                "identity_fingerprint": node.identity_fingerprint,
                "canonical_energy_hartree": _canonical_state_energy(
                    state,
                    ensemble_fallback=node.ensemble,
                ),
            }
        )
    edges = []
    for edge in network.edges:
        edges.append(
            {
                "step_id": edge.step_id,
                "source": edge.source_state_id,
                "sink": edge.sink_state_id,
                "ts_id": edge.ts_id,
                "barrier_forward": edge.barrier_forward,
                "barrier_reverse": edge.barrier_reverse,
                "fidelity": edge.fidelity,
                "status": edge.status,
                "path_strategy": edge.path_strategy,
            }
        )
    return {
        "study_id": study.study_id,
        "nodes": nodes,
        "edges": edges,
        "notes": notes,
    }


def _build_mechanism_profile_report(
    study: MechanismStudy,
    route_payloads: Sequence[dict[str, Any]],
    refinement_manifests: Sequence[Any],
    canonical_point_ids: set[str],
    notes: list[str],
) -> dict[str, Any]:
    stationary_by_route: dict[str | None, list[StationaryPoint]] = defaultdict(list)
    for point in study.stationary_points:
        stationary_by_route[point.route_id].append(point)

    refinement_by_route: dict[str | None, list[Any]] = defaultdict(list)
    for manifest in refinement_manifests:
        route_id = (
            manifest.canonical_winner.route_id if manifest.canonical_winner is not None else None
        )
        refinement_by_route[route_id].append(manifest)

    routes = []
    if not route_payloads:
        notes = list(notes) + ["No path manifests available for mechanism_profile.json"]
    for payload in route_payloads:
        path_result = _path_result_from_payload(payload)
        route_data = payload.get("route") if isinstance(payload.get("route"), dict) else None
        route_id = str(payload.get("route_id") or (route_data or {}).get("route_id") or "")
        methods: dict[str, list[dict[str, Any]]] = defaultdict(list)
        arc_lengths: list[dict[str, Any]] = []
        if path_result is not None:
            for point in sorted(path_result.points, key=lambda item: item.progress):
                arc_lengths.append(
                    {
                        "point_id": point.point_id,
                        "progress": point.progress,
                        "arc_length": point.arc_length,
                    }
                )
                for method, energy in point.energies_hartree.items():
                    methods[str(method)].append(
                        {
                            "point_id": point.point_id,
                            "progress": point.progress,
                            "arc_length": point.arc_length,
                            "energy_hartree": energy,
                        }
                    )
        refined_stationary = []
        for point in stationary_by_route.get(route_id, []):
            refined_stationary.append(
                {
                    "point_id": point.point_id,
                    "role": point.role,
                    "kind": point.kind,
                    "energy_hartree": point.energy_hartree,
                    "canonical": point.point_id in canonical_point_ids,
                    "fidelity": point.metadata.get("fidelity"),
                }
            )
        for manifest in refinement_by_route.get(route_id, []):
            if manifest.canonical_winner is None:
                continue
            point = manifest.canonical_winner
            if any(existing["point_id"] == point.point_id for existing in refined_stationary):
                continue
            refined_stationary.append(
                {
                    "point_id": point.point_id,
                    "role": point.role,
                    "kind": point.kind,
                    "energy_hartree": point.energy_hartree,
                    "canonical": True,
                    "fidelity": manifest.fidelity or point.metadata.get("fidelity"),
                }
            )
        routes.append(
            {
                "source_state_id": payload.get("source_state_id"),
                "route_id": route_id,
                "status": payload.get("status"),
                "fingerprint": payload.get("fingerprint"),
                "path_strategy": (route_data or {}).get("path_strategy")
                or (path_result.strategy if path_result is not None else None),
                "fidelity": (route_data or {}).get("fidelity"),
                "selected_ts_id": path_result.selected_ts_id if path_result is not None else None,
                "selected_int_id": path_result.selected_int_id if path_result is not None else None,
                "methods": dict(methods),
                "arc_lengths": arc_lengths,
                "refined_stationary_points": refined_stationary,
            }
        )
    return {
        "study_id": study.study_id,
        "routes": routes,
        "notes": notes,
    }


def _build_stationary_points_report(
    study: MechanismStudy,
    canonical_point_ids: set[str],
    notes: list[str],
) -> dict[str, Any]:
    stationary_points = []
    for point in study.stationary_points:
        fidelity = point.metadata.get("fidelity")
        stationary_points.append(
            {
                "point_id": point.point_id,
                "role": point.role,
                "kind": point.kind,
                "state_id": point.state_id,
                "route_id": point.route_id,
                "charge": point.charge,
                "multiplicity": point.multiplicity,
                "geometry_artifact": point.geometry.to_dict(),
                "energies_per_fidelity": (
                    {str(fidelity or "unknown"): point.energy_hartree}
                    if point.energy_hartree is not None
                    else {}
                ),
                "frequencies_summary": {
                    "imaginary_count": (
                        point.identity.imaginary_count if point.identity is not None else None
                    ),
                    "imaginary_frequency_cm1": (
                        point.identity.imaginary_frequency_cm1
                        if point.identity is not None
                        else None
                    ),
                    "validation_count": (
                        len(point.validation.identities) if point.validation is not None else 0
                    ),
                },
                "identity_evidence": {
                    "identity": point.identity.to_dict() if point.identity is not None else None,
                    "validation": (
                        point.validation.to_dict() if point.validation is not None else None
                    ),
                },
                "canonical": (
                    point.point_id in canonical_point_ids or bool(point.metadata.get("canonical"))
                ),
                "confirmed": _is_confirmed_point(point),
                "provenance": point.provenance.to_dict() if point.provenance is not None else None,
            }
        )
    if not stationary_points:
        notes = list(notes) + ["No stationary points available"]
    return {
        "study_id": study.study_id,
        "stationary_points": stationary_points,
        "notes": notes,
    }


def _normalize_quality_gates_payload(payload: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    for gate in payload.get("quality_gates") or []:
        if not isinstance(gate, dict):
            continue
        normalized.append(
            {
                "gate_id": gate.get("gate_id"),
                "status": gate.get("status"),
                "evidence": gate.get("evidence") if isinstance(gate.get("evidence"), dict) else {},
                "thresholds": (
                    gate.get("thresholds") if isinstance(gate.get("thresholds"), dict) else {}
                ),
                "missing_evidence": list(gate.get("missing_evidence") or []),
                "suggested_action": gate.get("suggested_action"),
            }
        )
    return {
        "quality_gates": normalized,
        "notes": notes,
    }


def _build_provenance_report(
    study: MechanismStudy,
    route_payloads: Sequence[dict[str, Any]],
    refinement_manifests: Sequence[Any],
    notes: list[str],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for payload in _iter_provenance_payloads(study, route_payloads, refinement_manifests):
        normalized = _normalize_provenance(payload)
        if normalized is None:
            continue
        signature = tuple(str(normalized.get(key) or "") for key in _provenance_fields())
        if signature in seen:
            continue
        seen.add(signature)
        records.append(normalized)
    return {
        "study_id": study.study_id,
        "study_input_signature": study.metadata.get("input_signature"),
        "records": records,
        "count": len(records),
        "notes": notes,
    }


def _iter_provenance_payloads(
    study: MechanismStudy,
    route_payloads: Sequence[dict[str, Any]],
    refinement_manifests: Sequence[Any],
) -> Iterable[dict[str, Any]]:
    for state in study.stable_states:
        if state.provenance is not None:
            yield state.provenance.to_dict()
    for point in study.stationary_points:
        if point.provenance is not None:
            yield point.provenance.to_dict()
    for payload in route_payloads:
        path_result = _path_result_from_payload(payload)
        if path_result is None:
            continue
        for point in path_result.points:
            if point.provenance is not None:
                yield point.provenance.to_dict()
    for manifest in refinement_manifests:
        if (
            manifest.canonical_winner is not None
            and manifest.canonical_winner.provenance is not None
        ):
            yield manifest.canonical_winner.provenance.to_dict()
        for attempt in manifest.attempts:
            if (
                attempt.stationary_point is not None
                and attempt.stationary_point.provenance is not None
            ):
                yield attempt.stationary_point.provenance.to_dict()


def _canonical_point_ids(manifests: Sequence[Any]) -> set[str]:
    return {
        manifest.canonical_winner.point_id
        for manifest in manifests
        if manifest.canonical_winner is not None
    }


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _route_payload_key(payload: Mapping[str, Any]) -> str:
    source_state_id = str(payload.get("source_state_id") or "")
    route_id = str(payload.get("route_id") or "")
    return f"{source_state_id}::{route_id}"


def _split_exploration_key(key: str) -> tuple[str, str]:
    if "::" not in key:
        return "", key
    source_state_id, route_id = key.split("::", 1)
    return source_state_id, route_id


def _path_result_from_payload(payload: Mapping[str, Any]) -> PathResult | None:
    path_result_data = payload.get("path_result")
    if not isinstance(path_result_data, dict):
        return None
    return PathResult.from_dict(dict(path_result_data))


def _coerce_study(study: MechanismStudy | Mapping[str, Any]) -> MechanismStudy:
    if isinstance(study, MechanismStudy):
        return study
    return MechanismStudy.from_dict(dict(study))


def _canonical_state_energy(
    state: StableState | None,
    *,
    ensemble_fallback: Any | None = None,
) -> float | None:
    if state is not None:
        if state.ensemble is not None:
            minimum = state.ensemble.global_minimum()
            if minimum is not None:
                return (
                    minimum.free_energy_hartree
                    if minimum.free_energy_hartree is not None
                    else minimum.energy_hartree
                )
        energy = _opt_float(state.metadata.get("canonical_energy_hartree"))
        if energy is not None:
            return energy
    if ensemble_fallback is not None:
        minimum = ensemble_fallback.global_minimum()
        if minimum is not None:
            return (
                minimum.free_energy_hartree
                if minimum.free_energy_hartree is not None
                else minimum.energy_hartree
            )
    return None


def _is_confirmed_point(point: StationaryPoint) -> bool:
    if bool(point.metadata.get("confirmed")):
        return True
    if point.kind == "ts" and point.identity is not None:
        return bool(point.identity.valid)
    return False


def _is_confirmed_ts(point: StationaryPoint, study: MechanismStudy) -> bool:
    if point.kind != "ts":
        return False
    if _is_confirmed_point(point):
        return True
    return any(
        edge.ts_id == point.point_id and edge.status == "confirmed"
        for edge in study.elementary_steps
    )


def _edge_barrier_metric(edge: Any) -> float | None:
    barriers = [
        value for value in (edge.barrier_forward, edge.barrier_reverse) if value is not None
    ]
    if not barriers:
        return None
    return min(float(value) for value in barriers)


def _edge_barrier_metric_for_ts(point_id: str, edges: Sequence[Any]) -> float:
    metrics = [_edge_barrier_metric(edge) for edge in edges if edge.ts_id == point_id]
    finite = [metric for metric in metrics if metric is not None]
    if not finite:
        return float("inf")
    return min(finite)


def _edge_touches_low_energy_state(
    edge: Any,
    state_energies: Mapping[str, float | None],
    global_min: float | None,
    threshold_kcal: float,
) -> bool:
    if global_min is None:
        return False
    for state_id in (edge.source_state_id, edge.sink_state_id):
        energy = state_energies.get(state_id)
        if energy is None:
            continue
        if (energy - global_min) * HARTREE_TO_KCAL <= threshold_kcal:
            return True
    return False


def _is_competing_ts(
    edge: Any,
    sibling_edges: Sequence[Any],
    edge_metrics: Mapping[str, float | None],
    threshold_kcal: float,
) -> bool:
    sibling_metrics = [
        edge_metrics.get(sibling.step_id)
        for sibling in sibling_edges
        if sibling.ts_id != edge.ts_id
    ]
    finite = [metric for metric in sibling_metrics if metric is not None]
    current = edge_metrics.get(edge.step_id)
    if current is None or not finite:
        return False
    return current <= min(finite) + threshold_kcal


def _normalize_provenance(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    if not all(field in payload for field in _provenance_fields()):
        return None
    return {field: str(payload.get(field) or "") for field in _provenance_fields()}


def _provenance_fields() -> tuple[str, ...]:
    return (
        "provider",
        "provider_version",
        "provider_commit",
        "strategy",
        "strategy_version",
        "profile_id",
        "schema_version",
        "input_signature",
    )


def _unique_in_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


__all__ = ["select_s4_candidates", "write_study_reports"]
