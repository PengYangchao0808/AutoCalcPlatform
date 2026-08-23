"""v2 RESULT view layer for mechanism studies (design doc §7.5, §8).

The checkpoint-frozen ``mechanism_study/<study_id>/`` subtree is read-only
input here; this module projects a read-only v2 ``RESULT/`` layer next to
it (``RESULT/mechanism/``, ``RESULT/structures/``, ``RESULT/reports/`` +
``RESULT/result_manifest.json``) without moving or renaming any frozen file.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from acp.storage.layout import TaskStorage
from acp.storage.manifest import ResultManifest

from .layout import MechanismStudyLayout, find_study_layout

logger = logging.getLogger(__name__)

__all__ = ["write_v2_result_layer"]


def _read_json(path: Path) -> dict[str, Any] | None:
    """Best-effort JSON read; ``None`` on missing/unreadable/corrupt files."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _collect_routes(layout: MechanismStudyLayout) -> list[dict[str, Any]]:
    """Summarise ``routes/<src>__<route>/path_manifest.json`` entries."""
    routes_dir = layout.routes_root
    summaries: list[dict[str, Any]] = []
    if not routes_dir.is_dir():
        return summaries
    for manifest_path in sorted(routes_dir.glob("*/path_manifest.json")):
        payload = _read_json(manifest_path)
        if payload is None:
            continue
        summaries.append(
            {
                "route_id": str(payload.get("route_id") or manifest_path.parent.name),
                "source_state_id": payload.get("source_state_id"),
                "target_state_id": payload.get("target_state_id"),
                "fidelity": payload.get("fidelity"),
                "status": payload.get("status", "completed"),
                "source": "path_manifest",
            }
        )
    return summaries


def _collect_refinements(layout: MechanismStudyLayout) -> list[dict[str, Any]]:
    """Summarise ``refinements/<manifest_id>/refinement_manifest.json`` entries."""
    refinements_dir = layout.refinements_root
    summaries: list[dict[str, Any]] = []
    if not refinements_dir.is_dir():
        return summaries
    for manifest_path in sorted(refinements_dir.glob("*/refinement_manifest.json")):
        payload = _read_json(manifest_path)
        if payload is None:
            continue
        summaries.append(
            {
                "manifest_id": str(payload.get("manifest_id") or manifest_path.parent.name),
                "route_id": payload.get("route_id"),
                "fidelity": payload.get("fidelity"),
                "status": payload.get("status", "completed"),
            }
        )
    return summaries


def _collect_irc(layout: MechanismStudyLayout) -> dict[str, Any]:
    """Best-effort IRC validation summary from ``sr/irc/*`` outputs."""
    irc_root = layout.endpoint_root / "irc"
    if not irc_root.is_dir():
        return {"validated": False, "note": "no IRC data"}
    point_ids = sorted(p.name for p in irc_root.iterdir() if p.is_dir())
    return {
        "validated": bool(point_ids),
        "n_irc_points": len(point_ids),
        "point_ids": point_ids,
    }


def _copy_structures(layout: MechanismStudyLayout, result_structures: Path) -> list[str]:
    """Copy stable-state inputs + canonical TS structures into RESULT/structures.

    Returns the list of copied file names (best-effort; missing files skipped).
    """
    copied: list[str] = []
    inputs_dir = layout.inputs_root
    if inputs_dir.is_dir():
        for xyz in sorted(inputs_dir.glob("*.xyz")):
            dest = result_structures / xyz.name
            try:
                shutil.copy2(xyz, dest)
                copied.append(xyz.name)
            except OSError:
                logger.warning("Could not copy structure %s", xyz)
    for ts_xyz in sorted(layout.refinements_root.glob("*/canonical.xyz")):
        dest = result_structures / ts_xyz.name
        try:
            shutil.copy2(ts_xyz, dest)
            copied.append(ts_xyz.name)
        except OSError:
            logger.warning("Could not copy canonical TS %s", ts_xyz)
    return copied


def write_v2_result_layer(task_root: Path | str) -> Path | None:
    """Write the v2 ``RESULT/`` layer for a completed mechanism study.

    Locates the study via :func:`find_study_layout` (v2 WORK tree first,
    legacy ``mechanism_study/`` fallback) and writes
    ``RESULT/{mechanism,structures,reports}/`` plus
    ``RESULT/result_manifest.json`` under *task_root* (the job work dir).
    Never mutates the study subtree.  Returns the manifest path, or
    ``None`` when the study has no usable data.
    """
    task = Path(task_root)
    layout = find_study_layout(task)
    if layout is None:
        logger.debug("write_v2_result_layer: no study found under %s", task)
        return None

    study_payload = _read_json(layout.study_json)
    network_payload = _read_json(layout.network_json)
    if study_payload is None and network_payload is None:
        return None

    storage = TaskStorage(task)
    storage.ensure_layout(
        stages=["02_SEARCH", "03_OPT", "07_PATH", "08_ANALYSIS"],
        categories=["mechanism", "structures", "energies", "reports"],
    )
    mech_dir = storage.result_category_dir("mechanism")
    struct_dir = storage.result_category_dir("structures")
    energy_dir = storage.result_category_dir("energies")

    routes = _collect_routes(layout)
    refinements = _collect_refinements(layout)
    irc = _collect_irc(layout)

    def _w(name: str, payload: dict[str, Any]) -> None:
        (mech_dir / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )

    _w(
        "reaction_network.json",
        {
            "study_id": str(study_payload.get("study_id") or layout.study_id),
            "reactant_id": study_payload.get("reactant_id"),
            "product_id": study_payload.get("product_id"),
            "status": str(study_payload.get("status") or "completed"),
            "quality": study_payload.get("quality"),
            "n_stable_states": len(study_payload.get("stable_states") or []),
            "nodes": (network_payload or {}).get("nodes", []),
            "edges": (network_payload or {}).get("edges", []),
        },
    )
    _w(
        "route_summary.json",
        {
            "study_id": str(study_payload.get("study_id") or layout.study_id),
            "n_routes": len(routes),
            "routes": routes,
        },
    )
    _w(
        "ts_summary.json",
        {
            "study_id": str(study_payload.get("study_id") or layout.study_id),
            "n_refinements": len(refinements),
            "refinements": refinements,
        },
    )
    _w("irc_validation.json", irc)

    copied = _copy_structures(layout, struct_dir)

    energy_profile: dict[str, Any] = {"routes": [], "note": "per-route energies not extracted"}
    (energy_dir / "energy_profile.json").write_text(
        json.dumps(energy_profile, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )

    summary = {
        "study_id": str(study_payload.get("study_id") or layout.study_id),
        "status": str(study_payload.get("status") or "completed"),
        "quality": study_payload.get("quality"),
        "n_routes": len(routes),
        "n_ts": len(refinements),
        "n_structures_copied": len(copied),
        "irc": irc,
    }
    (storage.result_dir() / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )

    manifest = ResultManifest(
        task_id="",
        workflow="mechanism",
        status=str(summary["status"]),
    )
    manifest.add_product(
        "reaction_network", "Reaction network", "mechanism/reaction_network.json", "report"
    )
    manifest.add_product("route_summary", "Route summary", "mechanism/route_summary.json", "report")
    manifest.add_product("ts_summary", "TS summary", "mechanism/ts_summary.json", "report")
    for name in copied:
        manifest.add_product(f"structure_{name}", name, f"structures/{name}", "structure")
    manifest.write(storage.result_dir())
    return storage.result_manifest_json()
