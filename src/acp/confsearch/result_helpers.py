# pyright: basic
"""Pure result transformations for the Confsearch engine."""

from __future__ import annotations

import logging
import math
from typing import Any

from .contracts import (
    PURE_XTB_PROTOCOLS,
    ConformerEntry,
    ConfsearchRequest,
    ProtocolOutcome,
)
from .shared.boltzmann import boltzmann_weights, relative_energies_kcal

logger = logging.getLogger(__name__)

_WEIGHT_MATCH_TOL = 1e-9
"""Absolute tolerance for rank1 weight == table-value checks (plan T2)."""


def sorted_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return records ranked by free energy (stable on ties).

    ``build_entries`` assigns ``conf_NNNN`` ids in this order, so geometry
    writers must pair entries against the same ranking.
    """

    def sort_key(record: dict[str, Any]) -> float:
        value = record.get("free_energy_hartree")
        if value is None:
            value = record.get("energy_hartree")
        return float(value) if value is not None else float("inf")

    return sorted(records, key=sort_key)


def _table_weights(
    records: list[dict[str, Any]],
    weight_table: dict[str, float],
) -> list[float] | None:
    """Look up every record's weight in the screening table.

    Returns ``None`` (and logs a warning) when ANY record's
    ``source_conf_id`` misses the table or holds a non-finite value — the
    caller then falls back to recomputed Boltzmann weights for ALL entries.
    """
    weights: list[float] = []
    for record in records:
        source_id = record.get("source_conf_id")
        value = weight_table.get(str(source_id)) if source_id is not None else None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            logger.warning(
                "weight_table lookup failed for source_conf_id=%r — "
                "recomputing Boltzmann weights for all %d entries",
                source_id,
                len(records),
            )
            return None
        weights.append(float(value))
    return weights


def build_entries(outcome: ProtocolOutcome) -> list[ConformerEntry]:
    """Rank protocol records by free energy and calculate their weights.

    When ``outcome.weight_table`` is present, weights are table lookups by
    ``source_conf_id``; if ANY lookup fails, ALL entries fall back to the
    recomputed Boltzmann weight with ``weight_source="computed"``.
    """
    records = sorted_records(outcome.records)

    energies = [
        record.get("free_energy_hartree") or record.get("energy_hartree") for record in records
    ]
    weights = boltzmann_weights(energies, outcome.temperature_k)
    relative = relative_energies_kcal(energies)

    table_weights: list[float] | None = None
    fallback_computed = False
    if outcome.weight_table:
        table_weights = _table_weights(records, outcome.weight_table)
        fallback_computed = table_weights is None

    entries: list[ConformerEntry] = []
    for index, record in enumerate(records):
        raw_source_id = record.get("source_conf_id")
        source_conf_id = str(raw_source_id) if raw_source_id is not None else None
        if table_weights is not None:
            weight: float = table_weights[index]
            weight_source = outcome.weight_source
        else:
            weight = weights[index] if weights[index] is not None else 0.0
            weight_source = "computed" if fallback_computed else outcome.weight_source
        refined = bool(
            source_conf_id is not None
            and outcome.refined_conf_ids
            and source_conf_id in outcome.refined_conf_ids
        )
        entry = ConformerEntry(
            conf_id=f"conf_{index + 1:04d}",
            geometry="",
            energy_hartree=record.get("energy_hartree"),
            free_energy_hartree=record.get("free_energy_hartree"),
            relative_energy_kcal=relative[index],
            boltzmann_weight=weight,
            rank=index + 1,
            source_conf_id=source_conf_id,
            weight_source=weight_source,
            refined=refined,
        )
        entries.append(entry)
    return entries


def refinement_block(
    request: ConfsearchRequest,
    outcome: ProtocolOutcome,
    selected: list[str],
) -> dict[str, Any]:
    """Build the refinement section of the Confsearch manifest."""
    completed = bool(outcome.refined_conf_ids) if selected else True
    if request.protocol in PURE_XTB_PROTOCOLS:
        completed = True  # nothing to refine — protocol energies are final
    artifacts: list[str] = []
    for key in (
        "thermo_csv",
        "boltzmann_table_json",
        "ensemble_thermo_json",
        "global_min_xyz",
    ):
        value = outcome.workflow_metadata.get(key)
        if isinstance(value, str):
            artifacts.append(value)
    return {
        "policy": request.refinement_policy,
        "completed": completed,
        "refined_conf_ids": list(outcome.refined_conf_ids),
        "selected_conformers": list(selected),
        "artifacts": artifacts,
    }


def _boltzmann_weights_valid(
    entries: list[ConformerEntry],
    outcome: ProtocolOutcome,
    policy: str,
) -> bool:
    """Policy-aware weight check.

    rank1 + table: every weight finite in (0, 1] and equal to its table
    value (tol 1e-9).  rank1 degraded — ``weight_source == "computed"`` on
    the outcome or on any entry (partial table miss fallback), or no table
    at all: only the physical range 0 < w <= 1.  Every other policy keeps
    the historical sum≈1 check.
    """
    if policy != "rank1":
        weight_sum = sum(entry.boltzmann_weight or 0.0 for entry in entries)
        return abs(weight_sum - 1.0) < 1e-3
    degraded = (
        outcome.weight_source == "computed"
        or not outcome.weight_table
        or any(entry.weight_source == "computed" for entry in entries)
    )
    if degraded:
        return all(
            entry.boltzmann_weight is not None
            and math.isfinite(entry.boltzmann_weight)
            and 0.0 < entry.boltzmann_weight <= 1.0
            for entry in entries
        )
    for entry in entries:
        weight = entry.boltzmann_weight
        if weight is None or not math.isfinite(weight) or not 0.0 < weight <= 1.0:
            return False
        if entry.source_conf_id is None:
            return False
        expected = outcome.weight_table.get(entry.source_conf_id)
        if (
            isinstance(expected, bool)
            or not isinstance(expected, (int, float))
            or not math.isfinite(float(expected))
        ):
            return False
        if abs(weight - float(expected)) > _WEIGHT_MATCH_TOL:
            return False
    return True


def quality_gates(
    entries: list[ConformerEntry],
    outcome: ProtocolOutcome,
    selected: list[str],
    *,
    protocol: str = "",
    policy: str = "",
) -> dict[str, Any]:
    """Build the Confsearch G1 quality-gate payload.

    ``policy`` selects the ``boltzmann_weights_valid`` mode (see
    :func:`_boltzmann_weights_valid`); the outcome's ``weight_source`` is
    reported additively under ``gates["weight_source"]``.
    """
    relative_valid = all(
        entry.relative_energy_kcal is None or entry.relative_energy_kcal >= -1e-6
        for entry in entries
    )
    ranked = [entry.rank for entry in entries]
    pure_xtb_refinement = protocol in PURE_XTB_PROTOCOLS
    gates: dict[str, Any] = {
        "input_valid": True,
        "at_least_one_conformer": len(entries) > 0,
        "dedup_completed": bool(outcome.sampling.get("method")),
        "energy_ranking_valid": relative_valid and ranked == list(range(1, len(entries) + 1)),
        "boltzmann_weights_valid": _boltzmann_weights_valid(entries, outcome, policy),
        "refinement_consistent": (
            not selected or pure_xtb_refinement or bool(outcome.refined_conf_ids)
        ),
    }
    gates["G1"] = "PASS" if all(gates.values()) else "FAIL"
    gates["weight_source"] = outcome.weight_source
    return gates


__all__ = ["build_entries", "quality_gates", "refinement_block", "sorted_records"]
