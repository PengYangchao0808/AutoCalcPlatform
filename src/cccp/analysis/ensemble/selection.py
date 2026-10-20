"""Boltzmann weights / cumulative selection / ensemble summary (T24).

Verbatim relocation of the pure Boltzmann selection + ensemble summary science
from ``acp.workflows.energy_shared`` into the CCCP analysis domain.  The values,
constants, and threshold semantics are unchanged.  The historical ACP path
re-exports the same objects.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from cccp.analysis.ensemble.thermo import (
    EnsembleThermoSummary,
    ensemble_total_gibbs,
    ensemble_total_gibbs_from_values,
    s_mix_cal_per_mol_kelvin,
    s_mix_kcal_per_mol_kelvin,
    t_s_mix_kcal_per_mol,
)
from cccp.utils.constants import HARTREE_TO_KCAL, KELVIN_TO_HARTREE

logger = logging.getLogger(__name__)

_K_B_HARTREE_PER_KELVIN = KELVIN_TO_HARTREE


def boltzmann_weights(gibbs_values: list[float], temperature_k: float) -> list[float]:
    if not gibbs_values:
        return []
    kt = _K_B_HARTREE_PER_KELVIN * temperature_k
    g_min = min(gibbs_values)
    raw = [float(np.exp(-(g - g_min) / kt)) for g in gibbs_values]
    total = sum(raw)
    if total <= 0:
        return [0.0 for _ in raw]
    return [w / total for w in raw]


def select_cumulative_boltzmann(
    records: list[Any],
    temperature_k: float,
    threshold: float,
) -> list[Any]:
    """Select the lowest-gtot conformers up to a cumulative Boltzmann cutoff.

    Records (``.gtot``-bearing, e.g. :class:`CensoConformerRecord`) are
    sorted by ascending ``gtot``; conformers are accumulated until their
    cumulative Boltzmann population reaches ``threshold`` (the conformer
    crossing the threshold is included). Rank1 is always selected.
    """
    if not records:
        return []
    ordered = sorted(records, key=lambda r: r.gtot)
    weights = boltzmann_weights([r.gtot for r in ordered], temperature_k)
    selected: list[Any] = []
    cumulative = 0.0
    for rec, weight in zip(ordered, weights):
        selected.append(rec)
        cumulative += weight
        if cumulative >= threshold:
            break
    return selected


def build_ensemble_summary(
    candidates: list[dict[str, Any]],
    dft_weights: list[float],
    temperature_k: float,
    external_weights: dict[str, float] | None = None,
    external_total_gibbs: float | None = None,
    external_total_gibbs_censo: float | None = None,
    population_weights: dict[str, float] | None = None,
    external_table_source: str = "censo",
) -> EnsembleThermoSummary:
    """Assemble the ensemble total-Gibbs summary for an output batch.

    Workflow 1 (no external table): the DFT table of the selected
    candidates drives p₁ and S_mix; ``population_coverage`` is the
    cumulative weight of the selected set inside the full screening table
    (``population_weights``), disclosing the ≥99% truncation.

    Workflow 2 (external table): the complete CENSO/xTB table drives p₁
    and S_mix while G₁ is the fine rank1 Gibbs value (or the CENSO gtot on
    cheap paths); the coverage is 1.0 by construction of the table.
    """
    if external_weights is not None:
        method = "censo_table_rank1" if external_table_source == "censo" else "xtb_table_rank1"
        p1 = 0.0
        for cand in candidates:
            p1 = external_weights.get(str(cand.get("source", "")), 0.0)
            if p1 > 0.0:
                break
        if p1 <= 0.0 and external_weights:
            p1 = max(external_weights.values())
        rank1_source = str(candidates[0].get("source", "")) if candidates else ""
        rank1_gibbs = candidates[0].get("gibbs") if candidates else None
        rank1_gibbs = float(rank1_gibbs) if rank1_gibbs is not None else 0.0
        total = (
            external_total_gibbs
            if external_total_gibbs is not None
            else ensemble_total_gibbs(rank1_gibbs, p1, temperature_k)
        )
        rows: list[dict[str, Any]] = []
        ordered_ext = sorted(external_weights.items(), key=lambda kv: (-float(kv[1]), str(kv[0])))
        for row_rank, (conf_id, w) in enumerate(ordered_ext, start=1):
            g = rank1_gibbs if conf_id == rank1_source else None
            rows.append(
                {
                    "conf_id": conf_id,
                    "rank": row_rank,
                    "gibbs_hartree": g,
                    "delta_gibbs_kcal_mol": None,
                    "weight": round(float(w), 6),
                }
            )
        weights_values = list(external_weights.values())
        coverage = 1.0
    else:
        method = "dft_table"
        gibbs_values = [
            c["gibbs"] if c.get("gibbs") is not None else c.get("energy", 0.0) for c in candidates
        ]
        rank1_gibbs = gibbs_values[0] if gibbs_values else 0.0
        total = ensemble_total_gibbs_from_values(gibbs_values, temperature_k)
        p1 = dft_weights[0] if dft_weights else 0.0
        rows = [
            {
                "conf_id": c.get("source", f"conf{c.get('index', i)}"),
                "rank": i + 1,
                "gibbs_hartree": c.get("gibbs"),
                "delta_gibbs_kcal_mol": (
                    (float(c["gibbs"]) - float(rank1_gibbs)) * HARTREE_TO_KCAL
                    if c.get("gibbs") is not None and rank1_gibbs is not None
                    else None
                ),
                "weight": round(float(w), 6),
            }
            for i, (c, w) in enumerate(zip(candidates, dft_weights))
        ]
        weights_values = dft_weights
        coverage = 1.0
        if population_weights:
            selected = sum(
                population_weights.get(str(c.get("source", "")), 0.0) for c in candidates
            )
            if selected > 0.0:
                coverage = round(min(selected, 1.0), 6)

    return EnsembleThermoSummary(
        method=method,
        temperature_k=temperature_k,
        total_gibbs_hartree=total,
        total_gibbs_kcal_mol=total * HARTREE_TO_KCAL,
        rank1_gibbs_hartree=rank1_gibbs,
        rank1_weight=round(p1, 6),
        mixing_entropy_kcal_per_mol_kelvin=s_mix_kcal_per_mol_kelvin(weights_values),
        mixing_entropy_cal_per_mol_kelvin=s_mix_cal_per_mol_kelvin(weights_values),
        t_s_mix_kcal_per_mol=t_s_mix_kcal_per_mol(weights_values, temperature_k),
        population_coverage=coverage,
        conformers=rows,
        censo_reference_gibbs_hartree=external_total_gibbs_censo,
        censo_reference_gibbs_kcal_mol=(
            external_total_gibbs_censo * HARTREE_TO_KCAL
            if external_total_gibbs_censo is not None
            else None
        ),
    )


__all__ = [
    "boltzmann_weights",
    "build_ensemble_summary",
    "select_cumulative_boltzmann",
]
