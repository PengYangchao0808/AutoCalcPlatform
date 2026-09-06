# pyright: basic
"""Pure result transformations for the Confsearch engine."""

from __future__ import annotations

from typing import Any

from .contracts import (
    PURE_XTB_PROTOCOLS,
    ConformerEntry,
    ConfsearchRequest,
    ProtocolOutcome,
)
from .shared.boltzmann import boltzmann_weights, relative_energies_kcal


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


def build_entries(outcome: ProtocolOutcome) -> list[ConformerEntry]:
    """Rank protocol records by free energy and calculate their weights."""
    records = sorted_records(outcome.records)

    energies = [
        record.get("free_energy_hartree") or record.get("energy_hartree") for record in records
    ]
    weights = boltzmann_weights(energies, outcome.temperature_k)
    relative = relative_energies_kcal(energies)

    entries: list[ConformerEntry] = []
    for index, record in enumerate(records):
        entry = ConformerEntry(
            conf_id=f"conf_{index + 1:04d}",
            geometry="",
            energy_hartree=record.get("energy_hartree"),
            free_energy_hartree=record.get("free_energy_hartree"),
            relative_energy_kcal=relative[index],
            boltzmann_weight=weights[index] if weights[index] is not None else 0.0,
            rank=index + 1,
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


def quality_gates(
    entries: list[ConformerEntry],
    outcome: ProtocolOutcome,
    selected: list[str],
    *,
    protocol: str = "",
) -> dict[str, Any]:
    """Build the Confsearch G1 quality-gate payload."""
    weight_sum = sum(entry.boltzmann_weight or 0.0 for entry in entries)
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
        "boltzmann_weights_valid": abs(weight_sum - 1.0) < 1e-3,
        "refinement_consistent": (
            not selected or pure_xtb_refinement or bool(outcome.refined_conf_ids)
        ),
    }
    gates["G1"] = "PASS" if all(gates.values()) else "FAIL"
    return gates


__all__ = ["build_entries", "quality_gates", "refinement_block", "sorted_records"]
