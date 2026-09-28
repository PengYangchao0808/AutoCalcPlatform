"""Shared helper functions for confsearch protocol runners.

Extracted from ``__init__.py`` to break the circular import between
``__init__.py`` and the individual protocol modules (``censo_crest.py``,
``xtb_crest.py``, ``xtb_md.py``, ``xtbmd_censo.py``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from ..contracts import ConfsearchRequest, ProtocolOutcome

logger = logging.getLogger(__name__)


def coords_list(coordinates: Any) -> list[list[float]]:
    """Normalize a coordinate block to plain nested lists."""
    return np.asarray(coordinates, dtype=float).tolist()


def records_from_ensemble_result(result: Any) -> list[dict[str, Any]]:
    """Convert a ``WorkflowResult.ensemble`` (StructureEnsemble) into rows."""
    records: list[dict[str, Any]] = []
    ensemble = getattr(result, "ensemble", None)
    for record in getattr(ensemble, "records", []) or []:
        structure = record.structure
        # Join key for boltzmann_table.json (plan decision D12/Metis B1):
        # manifest conf_NNNN ids are renumbered and must NEVER be used to
        # join the screening table — metadata["source"] is the original key.
        source_id = structure.metadata.get("source") or structure.metadata.get("conf_id")
        records.append(
            {
                "conf_id": str(structure.metadata.get("conf_id") or structure.id),
                "source_conf_id": str(source_id) if source_id is not None else None,
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


# ── weight provenance per refinement policy (plan todo 5 / D1, D13) ────


def apply_screening_weight_table(
    outcome: ProtocolOutcome,
    policy: str,
    *,
    logger_warn: Callable[[str], None] | None = None,
) -> None:
    """Apply the delegated screening weight table for ``rank1`` runs.

    For ``policy == "rank1"`` the authoritative weights are the screening
    table written by the delegated workflow
    (``RESULT/ensembles/boltzmann_table.json``, whose path is exposed under
    ``workflow_metadata["boltzmann_table_json"]``): rank1 keeps ONE refined
    entry whose Boltzmann weight is the table ``p₁`` — never the recomputed
    1.0 (decision D1).  The file shape is ``{"temperature_k": float,
    "source": "censo"|"xtb", "weights": {source_conf_id: float}}``
    (``energy_shared.write_final_outputs``).

    A missing, unreadable, or malformed table degrades the run: warning +
    ``weight_source="computed"``, ``weight_method="computed"``,
    ``population_coverage=None`` — the job still completes (D13).  Never
    raises.  Non-rank1 policies are a no-op (their table must not be read).
    """
    if policy != "rank1":
        return
    warn = logger_warn or (lambda message: logger.warning("%s", message))
    table_path = outcome.workflow_metadata.get("boltzmann_table_json")
    table: dict[str, float] | None = None
    source = "censo"
    temperature_k: float | None = None
    if table_path:
        payload: Any = None
        try:
            payload = json.loads(Path(str(table_path)).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            warn(
                f"Screening weight table unreadable at {table_path!r} — "
                f"degrading rank1 weights to 'computed' ({exc})"
            )
        weights = payload.get("weights") if isinstance(payload, dict) else None
        if isinstance(weights, dict) and weights:
            try:
                table = {str(key): float(value) for key, value in weights.items()}
            except (TypeError, ValueError):
                table = None
        if table is None:
            warn(
                f"Screening weight table at {table_path!r} has no usable "
                "'weights' dict — degrading rank1 weights to 'computed'"
            )
        else:
            file_source = payload.get("source")
            if isinstance(file_source, str) and file_source:
                source = file_source
            try:
                temperature_k = float(payload["temperature_k"])
            except (KeyError, TypeError, ValueError):
                temperature_k = None
    else:
        warn(
            "No screening weight table (workflow_metadata['boltzmann_table_json']) "
            "available — degrading rank1 weights to 'computed'"
        )
    if table is None:
        outcome.weight_source = "computed"
        outcome.weight_method = "computed"
        outcome.population_coverage = None
        return
    outcome.weight_table = table
    outcome.weight_source = source
    outcome.weight_method = f"{source}_table_rank1"
    outcome.population_coverage = 1.0
    if temperature_k is not None:
        outcome.temperature_k = temperature_k


def apply_dft_provenance(outcome: ProtocolOutcome, metadata: dict[str, Any]) -> None:
    """``cumulative-99`` / ``all`` weights come from the refined DFT table.

    The delegated workflow already computed the DFT-refined Boltzmann
    weights and disclosed the cumulative ``population_coverage``; carry
    both verbatim from its metadata (defaults mirror the historical
    recomputed behaviour when the metadata is sparse).
    """
    outcome.weight_source = "dft"
    outcome.weight_method = str(metadata.get("weight_method") or "dft_table")
    coverage = metadata.get("population_coverage")
    outcome.population_coverage = float(coverage) if isinstance(coverage, (int, float)) else None


def apply_screen_provenance(outcome: ProtocolOutcome, metadata: dict[str, Any]) -> None:
    """``screen`` weights come from the screening ensemble itself.

    Reads the delegated ensemble metadata (``weight_source``/``weight_method``
    /``population_coverage``) with CENSO-table defaults — the screening
    table IS the final table for this policy.
    """
    outcome.weight_source = str(metadata.get("weight_source") or "censo")
    outcome.weight_method = str(metadata.get("weight_method") or "censo_table")
    coverage = metadata.get("population_coverage")
    outcome.population_coverage = float(coverage) if isinstance(coverage, (int, float)) else 1.0


def apply_xtb_provenance(outcome: ProtocolOutcome) -> None:
    """Pure-xTB protocols: the xTB Boltzmann table IS the final table."""
    outcome.weight_source = "xtb"
    outcome.weight_method = "xtb_table"
    outcome.population_coverage = 1.0


# ── temperature forwarding (D6 / Metis M2) ─────────────────────────────


def _forwarded_temperature(request: ConfsearchRequest) -> float | None:
    """Temperature to forward to the delegated workflow, or ``None``.

    Forwarding applies only when ``request.temperature`` is explicitly set
    AND ``levels.thermo.temperature`` does not already pin it — explicit
    levels win, ``request`` is never mutated.
    """
    if request.temperature is None:
        return None
    levels_thermo = (request.levels or {}).get("thermo")
    if isinstance(levels_thermo, dict) and levels_thermo.get("temperature") is not None:
        return None
    return float(request.temperature)


def levels_with_thermo_temperature(request: ConfsearchRequest) -> dict[str, Any] | None:
    """Copy of ``request.levels`` with ``thermo.temperature`` merged in.

    Returns ``request.levels`` unchanged when no forwarding applies; the
    caller passes the copy to the delegated ``run_*`` call — ``request``
    is never mutated.
    """
    temperature = _forwarded_temperature(request)
    if temperature is None:
        return request.levels
    merged = dict(request.levels or {})
    thermo = dict(merged.get("thermo") or {})
    thermo["temperature"] = temperature
    merged["thermo"] = thermo
    return merged


def config_with_censo_temperature(request: ConfsearchRequest) -> dict[str, Any] | None:
    """Config-channel temperature copy for ``run_ensemble_generation`` callers.

    ``run_ensemble_generation`` has no ``levels`` parameter; its temperature
    is resolved from ``cfg["censo"]["temperature"]`` (censo-zero xTB
    passthrough and the CENSO call).  Returns a non-mutating copy of
    ``request.config`` with that key set, or ``request.config`` unchanged
    when no forwarding applies.
    """
    temperature = _forwarded_temperature(request)
    if temperature is None:
        return request.config
    merged = dict(request.config or {})
    censo = dict(merged.get("censo") or {})
    censo["temperature"] = temperature
    merged["censo"] = censo
    return merged
