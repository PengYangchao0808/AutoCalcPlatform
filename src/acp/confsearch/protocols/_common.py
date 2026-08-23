"""Shared helper functions for confsearch protocol runners.

Extracted from ``__init__.py`` to break the circular import between
``__init__.py`` and the individual protocol modules (``censo_crest.py``,
``xtb_crest.py``, ``xtb_md.py``, ``xtbmd_censo.py``).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..contracts import ConfsearchRequest, ProtocolOutcome


def coords_list(coordinates: Any) -> list[list[float]]:
    """Normalize a coordinate block to plain nested lists."""
    return np.asarray(coordinates, dtype=float).tolist()


def records_from_ensemble_result(result: Any) -> list[dict[str, Any]]:
    """Convert a ``WorkflowResult.ensemble`` (StructureEnsemble) into rows."""
    records: list[dict[str, Any]] = []
    ensemble = getattr(result, "ensemble", None)
    for record in getattr(ensemble, "records", []) or []:
        structure = record.structure
        records.append(
            {
                "conf_id": str(structure.metadata.get("conf_id") or structure.id),
                "symbols": list(structure.symbols),
                "coordinates": (
                    coords_list(structure.coordinates)
                    if structure.coordinates is not None
                    else None
                ),
                "energy_hartree": record.energy_hartree,
                "free_energy_hartree": record.free_energy_hartree,
                "weight": record.weight,
                "properties": dict(record.properties or {}),
            }
        )
    return records


def refined_ids_from_metadata(metadata: dict[str, Any]) -> list[str]:
    """Best-effort extraction of refined conformer ids from workflow metadata."""
    for key in ("refined_conf_ids", "selected_conf_ids"):
        value = metadata.get(key)
        if isinstance(value, list) and value:
            return [str(item) for item in value]
    candidates = metadata.get("final_candidates")
    if isinstance(candidates, list) and candidates:
        ids: list[str] = []
        for item in candidates:
            if isinstance(item, dict) and item.get("conf_id"):
                ids.append(str(item["conf_id"]))
            elif isinstance(item, str):
                ids.append(item)
        if ids:
            return ids
    return []


def outcome_from_workflow_result(
    result: Any,
    *,
    sampling: dict[str, Any],
    temperature_k: float,
) -> ProtocolOutcome:
    """Normalize a completed delegated ``WorkflowResult``."""
    if result.status != "completed":
        raise RuntimeError(f"Delegated workflow failed: {result.error}")
    records = records_from_ensemble_result(result)
    if not records:
        raise RuntimeError("Delegated workflow produced no conformer records")
    return ProtocolOutcome(
        records=records,
        temperature_k=temperature_k,
        refined_conf_ids=refined_ids_from_metadata(result.metadata or {}),
        sampling=sampling,
        stages_completed=list(result.stages_completed or []),
        workflow_metadata=dict(result.metadata or {}),
    )


def require_completed(result: Any) -> None:
    if result.status != "completed":
        raise RuntimeError(f"Delegated workflow failed: {result.error}")


def threshold_from_levels(request: ConfsearchRequest, default: float = 0.99) -> float:
    """Resolve the cumulative-Boltzmann threshold from ``levels`` overrides."""
    levels = request.levels or {}
    value = levels.get("refinement_threshold")
    if isinstance(value, (int, float)) and 0 < float(value) <= 1.0:
        return float(value)
    return default
