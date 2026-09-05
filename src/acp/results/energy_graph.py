"""Normalized energy-graph projections for the ACP workbench.

The frontend energy workspace should not need to understand individual
workflow manifests.  This module converts the currently supported result
products into one small, stable projection containing axes, series, nodes,
annotations, and geometry references.
"""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportUnusedCallResult=false, reportImplicitStringConcatenation=false, reportUnnecessaryIsInstance=false, reportOptionalMemberAccess=false, reportArgumentType=false, reportIndexIssue=false
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HARTREE_TO_KCAL = 627.5094740631

__all__ = [
    "build_conformer_energy_graph",
    "build_energy_graph_from_job",
    "build_mechanism_energy_graph",
    "build_optimization_energy_graph",
    "build_pes_energy_graph",
    "build_s2_energy_graph",
    "build_unavailable_energy_graph",
]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _sanitize_json(value: Any) -> Any:
    """Recursively replace non-finite floats (NaN/±Inf) with ``None``.

    Scan frames persist ``float("nan")`` when coordinate measurement fails
    (``calculations/pes/scan.py``).  ``json.dump`` writes those as bare
    ``NaN``/``Infinity`` tokens which violate strict JSON, and FastAPI's
    encoder rejects them with ``ValueError: Out of range float values are
    not JSON compliant``.  Every projection handed to the API passes
    through this guard so historical manifests with persisted NaN still
    render instead of returning HTTP 500.
    """
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, dict):
        return {key: _sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json(item) for item in value]
    return value


def _aligned(values: Any, size: int) -> list[float | None]:
    raw = list(values) if isinstance(values, (list, tuple)) else []
    result = [_number(value) for value in raw[:size]]
    return result + [None] * max(0, size - len(result))


def _relative_hartree(values: list[float | None]) -> list[float | None]:
    finite = [value for value in values if value is not None]
    if not finite:
        return [None] * len(values)
    reference = min(finite)
    return [None if value is None else (value - reference) * HARTREE_TO_KCAL for value in values]


def _revision(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:16]


def _coordinate_axis(payload: dict[str, Any]) -> dict[str, str]:
    coordinate = (payload.get("protocol") or {}).get("coordinate") or {}
    kind = str(coordinate.get("kind") or "coordinate")
    unit = str(coordinate.get("unit") or ("angstrom" if kind == "distance" else "degree"))
    display_unit = (
        "Å" if unit in {"angstrom", "A", "Å"} else "°" if unit in {"degree", "deg", "°"} else unit
    )
    labels = {
        "distance": "扫描距离",
        "angle": "扫描角度",
        "dihedral": "扫描二面角",
    }
    return {"label": labels.get(kind, "扫描坐标"), "unit": display_unit}


def _candidate_label(candidate: dict[str, Any], index: int) -> str:
    kind = str(candidate.get("kind") or "candidate").lower()
    prefix = (
        "TS"
        if kind in {"ts", "transition_state"}
        else "INT"
        if kind in {"intermediate", "int"}
        else "候选"
    )
    candidate_id = str(candidate.get("candidate_id") or "")
    suffix = candidate_id.rsplit("_", 1)[-1] if candidate_id else str(index + 1).zfill(2)
    try:
        suffix = str(int(suffix)).zfill(2)
    except ValueError:
        suffix = suffix[-8:] or str(index + 1).zfill(2)
    return f"{prefix}-{suffix}"


def _s2_series(frames: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    size = len(frames)
    relative = _aligned(profile.get("relative_energies_kcal_mol"), size)
    scan = [_number(frame.get("scan_energy_hartree")) for frame in frames]
    single_point = [_number(frame.get("single_point_energy_hartree")) for frame in frames]
    convergence = [1.0 if bool(frame.get("optimization_converged")) else 0.0 for frame in frames]
    series = [
        {
            "id": "relative_energy",
            "label": "相对能量",
            "unit": "kcal/mol",
            "axis": "left",
            "values": relative,
            "source": "energy_profile.relative_energies_kcal_mol",
        },
        {
            "id": "scan_energy",
            "label": "扫描能量",
            "unit": "Eh",
            "axis": "left",
            "values": scan,
            "source": "scan.frames.scan_energy_hartree",
        },
        {
            "id": "single_point_energy",
            "label": "单点能量",
            "unit": "Eh",
            "axis": "left",
            "values": single_point,
            "source": "scan.frames.single_point_energy_hartree",
        },
        {
            "id": "convergence",
            "label": "收敛状态",
            "unit": "0/1",
            "axis": "status",
            "values": convergence,
            "source": "scan.frames.optimization_converged",
        },
    ]
    return [item for item in series if any(value is not None for value in item["values"])]


def build_s2_energy_graph(
    job_id: str,
    payload: dict[str, Any],
    *,
    s2_candidates: list[dict[str, Any]] | None = None,
    s2_review_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the energy workspace projection for an S2 scan manifest.

    ``s2_candidates`` / ``s2_review_state`` carry the persisted
    editable-candidate state (``s2_candidate_manifest.json`` rows and the
    saved ``s2_review.json``).  When absent the projection falls back to
    the manifest's algorithm recommendations shown as unsaved initial
    markings.
    """
    original_schema = str(payload.get("schema_version") or "")
    if not (isinstance(payload.get("scan"), dict) and "energy_profile" in payload):
        from acp.results.pes_profile import normalize_pes_profile

        payload = normalize_pes_profile(payload)
    scan = payload.get("scan") or {}
    frames = [frame for frame in (scan.get("frames") or []) if isinstance(frame, dict)]
    profile = payload.get("energy_profile") or {}
    series = _s2_series(frames, profile)
    by_id = {item["id"]: item for item in series}
    default_series = (
        "single_point_energy"
        if any(
            value is not None for value in by_id.get("single_point_energy", {}).get("values", [])
        )
        else "relative_energy"
    )
    default_values = by_id.get(default_series, {}).get("values", [])
    axis = _coordinate_axis(payload)
    nodes: list[dict[str, Any]] = []
    for position, frame in enumerate(frames):
        # Plot the prescribed coordinate (monotone by construction); actuals
        # live in metadata — off-path frames plotted as x fold into zig-zags.
        x = _number(frame.get("target_coordinate"))
        if x is None:
            x = _number(frame.get("actual_coordinate"))
        status = "converged" if bool(frame.get("optimization_converged")) else "failed"
        if str(frame.get("single_point_status") or "").lower() == "failed":
            status = "failed"
        nodes.append(
            {
                "id": f"frame_{int(frame.get('index', position))}",
                "label": f"Frame {int(frame.get('index', position)) + 1}",
                "type": "frame",
                "frame_index": int(frame.get("index", position)),
                "x": x,
                "energy": default_values[position] if position < len(default_values) else None,
                "status": status,
                "geometry_ref": str(frame.get("geometry_path") or ""),
                "metadata": {
                    "target_coordinate": _number(frame.get("target_coordinate")),
                    "actual_coordinate": _number(frame.get("actual_coordinate")),
                    "target_coordinates": _sanitize_json(frame.get("target_coordinates") or {}),
                    "actual_coordinates": _sanitize_json(frame.get("actual_coordinates") or {}),
                    "scan_energy_hartree": _number(frame.get("scan_energy_hartree")),
                    "single_point_energy_hartree": _number(
                        frame.get("single_point_energy_hartree")
                    ),
                    "single_point_status": frame.get("single_point_status"),
                },
            }
        )

    node_by_frame = {node["frame_index"]: node for node in nodes}
    recommendations = payload.get("recommendations") or {}
    recommendation_by_id: dict[str, dict[str, Any]] = {}
    for group_key in ("ts", "intermediates"):
        for candidate in recommendations.get(group_key) or []:
            if isinstance(candidate, dict) and candidate.get("candidate_id"):
                row = dict(candidate)
                row["recommended_type"] = "ts" if group_key == "ts" else "intermediate"
                recommendation_by_id[str(candidate["candidate_id"])] = row

    saved_rows: dict[str, dict[str, Any]] = {}
    for row in s2_candidates or []:
        if isinstance(row, dict) and row.get("candidate_id"):
            saved_rows[str(row["candidate_id"])] = dict(row)
    review_state = s2_review_state or {}
    saved_any = bool(saved_rows) or str(review_state.get("status") or "") == "confirmed"

    annotations: list[dict[str, Any]] = []
    merged_ids = list(recommendation_by_id) + [
        cid for cid in saved_rows if cid not in recommendation_by_id
    ]
    for position, candidate_id in enumerate(merged_ids):
        recommendation = recommendation_by_id.get(candidate_id)
        saved = saved_rows.get(candidate_id)
        if saved is not None:
            frame_index = int(saved.get("frame_index") or 0)
            active = bool(saved.get("active", True))
            marker_type = str(saved.get("role") or "ts")
            selection_source = str(saved.get("selection_source") or "manual")
        else:
            frame_index = int(recommendation.get("frame_index") or 0)
            active = True
            marker_type = str(recommendation.get("recommended_type") or "ts")
            selection_source = "algorithm"
        recommended_type = (
            str(recommendation.get("recommended_type"))
            if recommendation is not None
            else (saved or {}).get("recommended_role")
        )
        node = node_by_frame.get(frame_index)
        if node is None:
            continue
        label_source = recommendation or saved or {}
        annotations.append(
            {
                "id": candidate_id,
                "candidate_id": candidate_id,
                "type": marker_type,
                "label": _candidate_label(label_source, position),
                "frame_index": frame_index,
                "x": node["x"],
                "y": node["energy"],
                "status": node["status"],
                "geometry_ref": node["geometry_ref"],
                "selected": active,
                "active": active,
                "saved": saved_any and saved is not None,
                "recommended_type": recommended_type,
                "selection_source": selection_source,
                "confidence": (recommendation or saved or {}).get("confidence"),
                "reason": (recommendation or saved or {}).get("reason"),
            }
        )

    finite_nodes = [node for node in nodes if node.get("energy") is not None]
    if finite_nodes:
        minimum = min(finite_nodes, key=lambda node: float(node["energy"]))
        annotations.append(
            {
                "id": f"minimum_{minimum['frame_index']}",
                "type": "minimum",
                "label": "最低能量",
                "frame_index": minimum["frame_index"],
                "x": minimum["x"],
                "y": minimum["energy"],
                "status": minimum["status"],
                "geometry_ref": minimum["geometry_ref"],
                "selected": False,
            }
        )
    for node in nodes:
        if node["status"] == "failed":
            annotations.append(
                {
                    "id": f"failed_{node['frame_index']}",
                    "type": "failed",
                    "label": "未收敛",
                    "frame_index": node["frame_index"],
                    "x": node["x"],
                    "y": node["energy"],
                    "status": "failed",
                    "geometry_ref": node["geometry_ref"],
                    "selected": False,
                }
            )

    quality = scan.get("quality") or {}
    complete = bool(
        quality.get("scan_complete", payload.get("status") in {"ready_for_review", "completed"})
    )
    source = str(
        payload.get("_source_path")
        or (
            "RESULT/pes_search/pes_profile.json"
            if original_schema == "pes_profile_v2"
            else "RESULT/mechanism/s2_path_manifest.json"
        )
    )
    title = "PESsearch 扫描能量" if original_schema == "pes_profile_v2" else "S2 扫描能量"
    projection: dict[str, Any] = _sanitize_json(
        {
            "job_id": job_id,
            "view_type": "scan",
            "title": title,
            "status": str(payload.get("status") or "unknown"),
            "complete": complete,
            "revision": _revision(payload),
            "default_series": default_series,
            "available_views": ["scan"],
            "x_axis": axis,
            "series": series,
            "nodes": nodes,
            "edges": [],
            "annotations": annotations,
            "source": source,
            "provenance": payload.get("provenance") or {},
            "metadata": {
                "energy_source": profile.get("energy_source"),
                "sp_incomplete": bool(profile.get("sp_incomplete")),
                "frame_count": len(frames),
                "constraints_satisfied": bool(quality.get("constraints_satisfied", True)),
                "max_constraint_residual": _number(quality.get("max_constraint_residual")),
                "constraint_tolerance": _number(quality.get("constraint_tolerance")),
                "coordinate": (payload.get("protocol") or {}).get("coordinate") or {},
                "coordinates": payload.get("coordinates") or [],
                "selection": payload.get("selection") or {},
                "review": {
                    "status": str(review_state.get("status") or "pending"),
                    "decided_at": review_state.get("decided_at"),
                    "revision": review_state.get("revision"),
                    "saved": saved_any,
                    "active_candidates": sum(
                        1
                        for item in annotations
                        if item["type"] in {"ts", "intermediate"} and item["active"]
                    ),
                },
            },
        }
    )
    return projection


def build_pes_energy_graph(
    job_id: str,
    payload: dict[str, Any],
    *,
    s2_candidates: list[dict[str, Any]] | None = None,
    s2_review_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the PESsearch scan projection.

    ``build_s2_energy_graph`` remains as the compatibility name for older
    callers and historical mechanism payloads.
    """
    return build_s2_energy_graph(
        job_id,
        payload,
        s2_candidates=s2_candidates,
        s2_review_state=s2_review_state,
    )


def build_optimization_energy_graph(
    job_id: str,
    work_dir: Path,
    item_id: str | None = None,
) -> dict[str, Any] | None:
    """Build a live or completed optimization projection."""
    trajectory_path, trajectory = _find_optimization_trajectory(work_dir, item_id)
    if trajectory_path is None or trajectory is None:
        return None

    raw_cycles = trajectory.get("cycles")
    has_cycle_payload = isinstance(raw_cycles, list)
    cycles: list[dict[str, Any]] = []
    if has_cycle_payload:
        for index, raw_cycle in enumerate(raw_cycles or []):
            if not isinstance(raw_cycle, dict):
                continue
            cycle_number = _number(raw_cycle.get("cycle"))
            cycles.append(
                {
                    **raw_cycle,
                    "cycle": int(cycle_number) if cycle_number is not None else index + 1,
                    "energy_hartree": _number(
                        raw_cycle.get("energy_hartree", raw_cycle.get("energy"))
                    ),
                }
            )
    else:
        scf_values = trajectory.get("scf_energies")
        gradient_values = trajectory.get("gradients_rms")
        count = max(
            len(scf_values) if isinstance(scf_values, (list, tuple)) else 0,
            len(gradient_values) if isinstance(gradient_values, (list, tuple)) else 0,
        )
        scf = _aligned(scf_values, count)
        gradients = _aligned(gradient_values, count)
        cycles = [
            {
                "cycle": index + 1,
                "energy_hartree": scf[index],
                "rms_gradient": gradients[index],
            }
            for index in range(count)
        ]
    if not cycles:
        return None

    source = _relative_path(trajectory_path, work_dir)
    energy_values = [_number(cycle.get("energy_hartree")) for cycle in cycles]
    relative = _relative_from_first(energy_values)
    delta = [
        None
        if index == 0 or energy_values[index] is None or energy_values[index - 1] is None
        else (energy_values[index] - energy_values[index - 1]) * HARTREE_TO_KCAL
        for index in range(len(energy_values))
    ]
    x_values = [_number(cycle.get("cycle")) or index + 1 for index, cycle in enumerate(cycles)]

    series_specs = [
        ("relative_energy", "相对初始能量", "kcal/mol", "left", relative),
        ("scf_energy", "SCF 能量", "Eh", "left", energy_values),
        ("rms_gradient", "RMS 梯度", "Eh/Bohr", "right", _cycle_values(cycles, "rms_gradient")),
        ("max_gradient", "最大梯度", "Eh/Bohr", "right", _cycle_values(cycles, "max_gradient")),
        ("rms_displacement", "RMS 位移", "Å", "right", _cycle_values(cycles, "rms_displacement")),
        ("max_displacement", "最大位移", "Å", "right", _cycle_values(cycles, "max_displacement")),
    ]
    if has_cycle_payload:
        series_specs.insert(1, ("delta_energy", "步间能量变化", "kcal/mol", "left", delta))
    series = [
        {
            "id": series_id,
            "label": label,
            "unit": unit,
            "axis": axis,
            "values": values,
            "x_values": x_values,
            "source": source,
        }
        for series_id, label, unit, axis, values in series_specs
        if any(value is not None for value in values)
    ]

    raw_status = str(trajectory.get("status") or "").lower()
    complete = bool(trajectory.get("converged")) or raw_status in {"complete", "completed"}
    status = (
        "completed"
        if complete
        else raw_status
        if raw_status in {"running", "failed"}
        else "partial"
    )
    last_index = len(cycles) - 1
    nodes = []
    for index, cycle in enumerate(cycles):
        cycle_status = str(cycle.get("status") or "")
        if index == last_index and complete:
            cycle_status = "converged"
        elif index == last_index and status == "running":
            cycle_status = "running"
        elif not cycle_status:
            cycle_status = "completed"
        node_metadata = {
            "cycle": x_values[index],
            "scf_energy_hartree": energy_values[index],
            "delta_energy_kcal_mol": delta[index],
            "rms_gradient": _number(cycle.get("rms_gradient")),
            "max_gradient": _number(cycle.get("max_gradient")),
            "rms_displacement": _number(cycle.get("rms_displacement")),
            "max_displacement": _number(cycle.get("max_displacement")),
            "scf_iterations": cycle.get("scf_iterations"),
        }
        nodes.append(
            {
                "id": f"cycle_{index}",
                "label": f"Cycle {int(x_values[index])}",
                "type": "optimization_cycle",
                "frame_index": index,
                "x": x_values[index],
                "energy": relative[index],
                "status": cycle_status,
                "geometry_ref": str(cycle.get("geometry_ref") or ""),
                "metadata": node_metadata,
            }
        )

    metadata = {
        "n_cycles": len(nodes),
        "current_cycle": trajectory.get("current_cycle") or x_values[-1],
        "item_id": trajectory.get("item_id") or item_id or "",
        "live": not complete,
        "thresholds": dict(trajectory.get("thresholds") or {}),
        "last_cycle": nodes[-1]["metadata"],
    }
    projection: dict[str, Any] = _sanitize_json(
        {
            "job_id": job_id,
            "view_type": "optimization",
            "title": "几何结构优化",
            "status": status,
            "complete": complete,
            "revision": _revision(trajectory),
            "default_series": "relative_energy",
            "available_views": ["optimization"],
            "x_axis": {"label": "优化周期", "unit": "cycle"},
            "series": series,
            "nodes": nodes,
            "edges": [],
            "annotations": [],
            "source": source,
            "provenance": {"capture": trajectory.get("source", "ORCA output")},
            "metadata": metadata,
        }
    )
    return projection


def _relative_from_first(values: list[float | None]) -> list[float | None]:
    """Return energy relative to the first available cycle, not the minimum."""
    reference = next((value for value in values if value is not None), None)
    if reference is None:
        return [None] * len(values)
    return [None if value is None else (value - reference) * HARTREE_TO_KCAL for value in values]


def _cycle_values(cycles: list[dict[str, Any]], key: str) -> list[float | None]:
    return [_number(cycle.get(key)) for cycle in cycles]


def _relative_path(path: Path, work_dir: Path) -> str:
    try:
        return path.resolve().relative_to(work_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix().replace("\\", "/")


def _find_optimization_trajectory(
    work_dir: Path,
    item_id: str | None,
) -> tuple[Path | None, dict[str, Any] | None]:
    """Find the newest valid live/result trajectory."""
    live_stage_root = work_dir / "WORK" / "03_OPT"
    batch_root = live_stage_root / "batch"
    result_root = work_dir / "RESULT" / "trajectories"
    if item_id:
        safe_id = Path(str(item_id)).name
        roots = [
            live_stage_root / "optimization_trajectory.json",
            *live_stage_root.glob("rescue_*/optimization_trajectory.json"),
            batch_root / safe_id / "optimize" / "optimization_trajectory.json",
            *batch_root.glob(f"{safe_id}/optimize/rescue_*/optimization_trajectory.json"),
            result_root / "optimization.json",
            result_root / safe_id / "optimization.json",
            result_root / f"{safe_id}_optimization.json",
        ]
    else:
        roots = [
            live_stage_root / "optimization_trajectory.json",
            *live_stage_root.glob("rescue_*/optimization_trajectory.json"),
            *batch_root.glob("*/optimize/optimization_trajectory.json"),
            *batch_root.glob("*/optimize/rescue_*/optimization_trajectory.json"),
            result_root / "optimization.json",
            *result_root.glob("*/optimization.json"),
            *result_root.glob("*_optimization.json"),
        ]
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in dict.fromkeys(roots):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            candidates.append((path, payload))
    if not candidates:
        return None, None
    running = [item for item in candidates if str(item[1].get("status") or "").lower() == "running"]
    pool = running or candidates
    selected = max(pool, key=lambda item: item[0].stat().st_mtime_ns)
    return selected


def build_mechanism_energy_graph(job_id: str, report: dict[str, Any]) -> dict[str, Any] | None:
    """Build a reaction-path graph from ``mechanism_profile`` JSON data."""
    profile = report.get("mechanism_profile") if isinstance(report, dict) else None
    routes = profile.get("routes") if isinstance(profile, dict) else None
    if not isinstance(routes, list) or not routes:
        return None
    series: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    for route_index, route in enumerate(routes):
        if not isinstance(route, dict):
            continue
        methods = route.get("methods") if isinstance(route.get("methods"), dict) else {}
        for method_index, (method, points) in enumerate(methods.items()):
            if not isinstance(points, list):
                continue
            ordered = [point for point in points if isinstance(point, dict)]
            energies = [_number(point.get("energy_hartree")) for point in ordered]
            relative = _relative_hartree(energies)
            series.append(
                {
                    "id": f"route_{route_index}_{method_index}",
                    "label": f"{route.get('route_id') or 'route'} · {method}",
                    "unit": "kcal/mol",
                    "axis": "left",
                    "values": relative,
                    "x_values": [_number(point.get("progress")) for point in ordered],
                    "source": "RESULT/mechanism/mechanism_profile.json",
                }
            )
            if method_index == 0:
                for point_index, point in enumerate(ordered):
                    nodes.append(
                        {
                            "id": str(
                                point.get("point_id") or f"route_{route_index}_point_{point_index}"
                            ),
                            "label": str(point.get("point_id") or f"Point {point_index + 1}"),
                            "type": "reaction_point",
                            "frame_index": point_index,
                            "x": _number(point.get("progress")) or float(point_index),
                            "energy": relative[point_index]
                            if point_index < len(relative)
                            else None,
                            "status": str(route.get("status") or "unknown"),
                            "geometry_ref": "",
                            "metadata": {"route_id": route.get("route_id"), "method": method},
                        }
                    )
        for point in route.get("refined_stationary_points") or []:
            if not isinstance(point, dict):
                continue
            match = next((node for node in nodes if node["id"] == str(point.get("point_id"))), None)
            if match is not None:
                annotations.append(
                    {
                        "id": str(point.get("point_id")),
                        "type": "ts"
                        if str(point.get("role") or point.get("kind") or "").lower()
                        in {"ts", "transition_state"}
                        else "intermediate",
                        "label": str(point.get("point_id")),
                        "frame_index": match["frame_index"],
                        "x": match["x"],
                        "y": match["energy"],
                        "status": "canonical" if point.get("canonical") else "candidate",
                        "geometry_ref": "",
                        "selected": bool(point.get("canonical")),
                    }
                )
    if not series:
        return None
    return {
        "job_id": job_id,
        "view_type": "reaction_path",
        "title": "反应路径能量图",
        "status": "completed",
        "complete": True,
        "revision": _revision(report),
        "default_series": series[0]["id"],
        "available_views": ["reaction_path"],
        "x_axis": {"label": "反应进程", "unit": "progress"},
        "series": series,
        "nodes": nodes,
        "edges": [],
        "annotations": annotations,
        "source": "RESULT/mechanism/mechanism_profile.json",
        "provenance": report.get("provenance") or {},
        "metadata": {"route_count": len(routes)},
    }


def _read_json_payload(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _read_legacy_csv(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                first_cell = row.get("index")
                if isinstance(first_cell, str) and first_cell.strip() == "TOTAL":
                    continue
                rows.append(
                    {
                        key: value
                        for key, value in row.items()
                        if isinstance(key, str) and isinstance(value, str)
                    }
                )
    except (OSError, csv.Error, ValueError):
        return []
    return rows


def _result_geometry_refs(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("products"), list):
        return {}
    refs: dict[str, str] = {}
    for product in payload["products"]:
        if not isinstance(product, dict) or product.get("kind") != "structure":
            continue
        path = product.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        relative_path = path.replace("\\", "/").lstrip("/")
        geometry_ref = (
            relative_path if relative_path.startswith("RESULT/") else f"RESULT/{relative_path}"
        )
        keys = (product.get("id"), product.get("label"), Path(relative_path).stem)
        for key in keys:
            if isinstance(key, str) and key.strip():
                normalized = key.strip()
                refs[normalized] = geometry_ref
                refs[normalized.casefold()] = geometry_ref
    return refs


def _normalise_confsearch_records(
    payload: dict[str, Any], geometry_refs: dict[str, str]
) -> list[dict[str, Any]]:
    entries = payload.get("conformers")
    if not isinstance(entries, list):
        return []
    records: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        gibbs = _number(entry.get("free_energy_hartree"))
        relative = _number(entry.get("relative_energy_kcal"))
        if gibbs is None and relative is None:
            continue
        conf_id = str(entry.get("conf_id") or f"CONF{index + 1}")
        rank_value = _number(entry.get("rank"))
        rank = int(rank_value) if rank_value is not None and rank_value > 0 else index + 1
        geometry = entry.get("geometry")
        if isinstance(geometry, str) and geometry.strip():
            geometry_path = geometry.replace("\\", "/").lstrip("/")
            geometry_ref = f"RESULT/confsearch/{geometry_path}"
        else:
            geometry_ref = geometry_refs.get(conf_id) or geometry_refs.get(conf_id.casefold(), "")
        records.append(
            {
                "conf_id": conf_id,
                "gibbs_hartree": gibbs,
                "energy_hartree": _number(entry.get("energy_hartree")),
                "relative_gibbs": relative,
                "weight": _number(entry.get("boltzmann_weight")),
                "rank": rank,
                "geometry_ref": geometry_ref,
            }
        )
    records.sort(key=lambda record: int(record["rank"]))
    return records


def _normalise_legacy_records(
    ensemble_payload: Any, csv_rows: list[dict[str, str]]
) -> list[dict[str, Any]]:
    entries = ensemble_payload.get("conformers") if isinstance(ensemble_payload, dict) else []
    if not isinstance(entries, list):
        entries = []
    records: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        conf_id = str(entry.get("conf_id") or f"CONF{index + 1}")
        record = {
            "conf_id": conf_id,
            "gibbs_hartree": _number(entry.get("gibbs_hartree")),
            "energy_hartree": None,
            "relative_gibbs": _number(entry.get("delta_gibbs_kcal_mol")),
            "weight": _number(entry.get("weight")),
            "rank": _number(entry.get("rank")),
            "_position": index,
        }
        records.append(record)
        by_id.setdefault(conf_id, record)

    for csv_index, row in enumerate(csv_rows):
        conf_id = str(row.get("source") or "").strip()
        if not conf_id:
            continue
        record = by_id.get(conf_id)
        if record is None:
            record = {
                "conf_id": conf_id,
                "gibbs_hartree": None,
                "energy_hartree": None,
                "relative_gibbs": None,
                "weight": None,
                "rank": None,
                "_position": len(records) + csv_index,
            }
            records.append(record)
            by_id[conf_id] = record
        for field, csv_key in (
            ("gibbs_hartree", "gibbs_hartree"),
            ("energy_hartree", "energy_hartree"),
            ("weight", "weight"),
        ):
            value = _number(row.get(csv_key))
            if value is not None:
                record[field] = value
        rank = _number(row.get("rank"))
        if rank is not None:
            record["rank"] = rank

    records = [
        record
        for record in records
        if record.get("gibbs_hartree") is not None or record.get("relative_gibbs") is not None
    ]
    for index, record in enumerate(records):
        rank = _number(record.get("rank"))
        record["rank"] = int(rank) if rank is not None and rank > 0 else index + 1
    records.sort(key=lambda record: (int(record["rank"]), int(record["_position"])))
    for record in records:
        record.pop("_position", None)
    return records


@dataclass(frozen=True, slots=True)
class _ConformerGraphInput:
    records: list[dict[str, Any]]
    source: str
    revision_payload: Any
    result_status: str


def _build_conformer_graph(
    job_id: str,
    graph_input: _ConformerGraphInput,
) -> dict[str, Any]:
    records = graph_input.records
    source = graph_input.source
    revision_payload = graph_input.revision_payload
    result_status = graph_input.result_status
    size = len(records)
    gibbs = _aligned([record.get("gibbs_hartree") for record in records], size)
    relative = _aligned([record.get("relative_gibbs") for record in records], size)
    fallback_relative = _relative_hartree(gibbs)
    relative = [
        value if value is not None else fallback_relative[index]
        for index, value in enumerate(relative)
    ]
    absolute = _aligned([record.get("energy_hartree") for record in records], size)
    weights = _aligned([record.get("weight") for record in records], size)
    series: list[dict[str, Any]] = []
    for series_id, label, unit, axis, values in (
        ("relative_gibbs", "相对 Gibbs 能量", "kcal/mol", "left", relative),
        ("gibbs_energy", "Gibbs 能量", "Eh", "left", gibbs),
        ("absolute_energy", "绝对能量", "Eh", "left", absolute),
        ("boltzmann_weight", "Boltzmann 权重", "", "right", weights),
    ):
        if any(value is not None for value in values):
            series.append(
                {
                    "id": series_id,
                    "label": label,
                    "unit": unit,
                    "axis": axis,
                    "values": values,
                    "source": source,
                }
            )
    by_id = {item["id"]: item for item in series}
    default_values = by_id.get("relative_gibbs", {}).get("values", [])
    nodes: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        rank = int(record["rank"])
        nodes.append(
            {
                "id": f"conf_{rank}",
                "label": str(record["conf_id"]),
                "type": "conformer",
                "frame_index": rank - 1,
                "x": rank,
                "energy": default_values[index] if index < len(default_values) else None,
                "status": "completed",
                "geometry_ref": str(record.get("geometry_ref") or ""),
                "metadata": {
                    "gibbs_hartree": gibbs[index],
                    "energy_hartree": absolute[index],
                    "weight": weights[index],
                    "rank": rank,
                },
            }
        )

    gibbs_indices = [index for index, value in enumerate(gibbs) if value is not None]
    if gibbs_indices:
        minimum_index = min(gibbs_indices, key=lambda index: gibbs[index] or 0.0)
    else:
        relative_indices = [index for index, value in enumerate(relative) if value is not None]
        minimum_index = min(relative_indices, key=lambda index: relative[index] or 0.0)
    minimum = nodes[minimum_index]
    annotations = [
        {
            "id": f"minimum_{minimum['frame_index']}",
            "type": "minimum",
            "label": "最低能量",
            "frame_index": minimum["frame_index"],
            "x": minimum["x"],
            "y": minimum["energy"],
            "status": minimum["status"],
            "geometry_ref": minimum["geometry_ref"],
            "selected": False,
        }
    ]
    metadata: dict[str, Any] = {"conformer_count": len(nodes)}
    if result_status:
        metadata["result_status"] = result_status
    return {
        "job_id": job_id,
        "view_type": "conformer",
        "title": "构象能量分布",
        "status": "completed",
        "complete": True,
        "revision": _revision(revision_payload),
        "default_series": "relative_gibbs",
        "available_views": ["conformer"],
        "x_axis": {"label": "构象排名", "unit": "rank"},
        "series": series,
        "nodes": nodes,
        "edges": [],
        "annotations": annotations,
        "source": source,
        "provenance": {},
        "metadata": metadata,
    }


def build_conformer_energy_graph(job_id: str, work_dir: Path) -> dict[str, Any] | None:
    """Build a conformer energy graph from current or retired result files."""
    result_manifest = _read_json_payload(work_dir / "RESULT" / "result_manifest.json")
    geometry_refs = _result_geometry_refs(result_manifest)
    result_status = (
        str(result_manifest.get("status") or "") if isinstance(result_manifest, dict) else ""
    )

    confsearch_path = work_dir / "RESULT" / "confsearch" / "confsearch_manifest.json"
    confsearch_payload = _read_json_payload(confsearch_path)
    if isinstance(confsearch_payload, dict):
        records = _normalise_confsearch_records(confsearch_payload, geometry_refs)
        if records:
            source = str(confsearch_path.relative_to(work_dir)).replace("\\", "/")
            return _build_conformer_graph(
                job_id,
                _ConformerGraphInput(records, source, confsearch_payload, result_status),
            )

    ensemble_path = work_dir / "RESULT" / "energies" / "ensemble_thermo.json"
    csv_path = work_dir / "RESULT" / "energies" / "conformer_thermo.csv"
    ensemble_payload = _read_json_payload(ensemble_path)
    csv_rows = _read_legacy_csv(csv_path)
    records = _normalise_legacy_records(ensemble_payload, csv_rows)
    if not records:
        return None
    for record in records:
        conf_id = str(record["conf_id"])
        record["geometry_ref"] = geometry_refs.get(conf_id) or geometry_refs.get(
            conf_id.casefold(), ""
        )
    has_json_conformers = isinstance(ensemble_payload, dict) and isinstance(
        ensemble_payload.get("conformers"), list
    )
    if has_json_conformers:
        source_path = ensemble_path
        revision_payload = ensemble_payload
    else:
        source_path = csv_path
        revision_payload = csv_rows
    source = str(source_path.relative_to(work_dir)).replace("\\", "/")
    return _build_conformer_graph(
        job_id,
        _ConformerGraphInput(records, source, revision_payload, result_status),
    )


def build_unavailable_energy_graph(job_id: str, *, workflow: str, reason: str) -> dict[str, Any]:
    """Build the explicit projection returned when a job has no graph data."""
    return {
        "job_id": job_id,
        "view_type": "unsupported",
        "title": "能量图不可用",
        "status": "unavailable",
        "complete": False,
        "revision": "",
        "default_series": "",
        "available_views": [],
        "x_axis": {},
        "series": [],
        "nodes": [],
        "edges": [],
        "annotations": [],
        "source": "",
        "provenance": {},
        "metadata": {"reason": reason, "workflow": workflow},
    }


def build_energy_graph_from_job(
    job_id: str,
    *,
    workflow: str,
    method: dict[str, Any] | None,
    work_dir: Path,
    s2_payload: dict[str, Any] | None = None,
    mechanism_report: dict[str, Any] | None = None,
    s2_candidates: list[dict[str, Any]] | None = None,
    s2_review_state: dict[str, Any] | None = None,
    item_id: str | None = None,
) -> dict[str, Any]:
    """Select the first supported energy projection for a scheduler job.

    The projection is sanitized for strict JSON compliance (non-finite
    floats become ``None``) so the FastAPI encoder never rejects the
    response with ``Out of range float values are not JSON compliant``.
    """
    projection: dict[str, Any] = _sanitize_json(
        _build_energy_graph_projection(
            job_id,
            workflow=workflow,
            method=method,
            work_dir=work_dir,
            s2_payload=s2_payload,
            mechanism_report=mechanism_report,
            s2_candidates=s2_candidates,
            s2_review_state=s2_review_state,
            item_id=item_id,
        )
    )
    return projection


def _build_energy_graph_projection(
    job_id: str,
    *,
    workflow: str,
    method: dict[str, Any] | None,
    work_dir: Path,
    s2_payload: dict[str, Any] | None = None,
    mechanism_report: dict[str, Any] | None = None,
    s2_candidates: list[dict[str, Any]] | None = None,
    s2_review_state: dict[str, Any] | None = None,
    item_id: str | None = None,
) -> dict[str, Any]:
    """Dispatch to the workflow-specific projection builder."""
    if workflow == "PESsearch" and str((method or {}).get("mode") or "") == "bond_length_scan":
        return build_pes_energy_graph(
            job_id,
            s2_payload or {},
            s2_candidates=s2_candidates,
            s2_review_state=s2_review_state,
        )
    if workflow == "mechanism" and mechanism_report:
        result = build_mechanism_energy_graph(job_id, mechanism_report)
        if result is not None:
            return result
        return build_unavailable_energy_graph(
            job_id, workflow=workflow, reason="energy_data_missing"
        )
    if workflow in {"optimize", "optfreq", "optfreqsp", "xtb-opt", "simple", "BatchOptimize"}:
        result = build_optimization_energy_graph(job_id, work_dir, item_id=item_id)
        if result is not None:
            return result
        return build_unavailable_energy_graph(
            job_id, workflow=workflow, reason="energy_data_missing"
        )
    if workflow in {"energy", "ensemble", "Confsearch", "xtbmd_censo_energy"}:
        result = build_conformer_energy_graph(job_id, work_dir)
        if result is not None:
            return result
        return build_unavailable_energy_graph(
            job_id, workflow=workflow, reason="energy_data_missing"
        )
    return build_unavailable_energy_graph(
        job_id, workflow=workflow, reason="workflow_has_no_energy_graph"
    )
