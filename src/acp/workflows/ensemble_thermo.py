"""Ensemble total Gibbs free energy helpers.

Implements the conformer-ensemble total free energy as a first-class
output of ``acp run energy``.  The mixing formula

    G_total = Σ_i p_i·G_i + RT·Σ_i p_i·ln p_i

is mathematically identical to the rank1 form

    G_total = G₁ + RT·ln p₁

so the total only depends on the lowest-free-energy conformer's G and its
Boltzmann weight.  Two weight tables are supported: the DFT table of the
selected (cumulative ≥99%) ensemble (workflow 1, ``dft_table``) and the
CENSO / xTB screening table combined with a single fine-DFT G₁ (workflow 2,
``censo_table_rank1`` / ``xtb_table_rank1``).  All internal units are
Hartree; kcal/mol conversions use :data:`acp.core.models.HARTREE_TO_KCAL`
(the single source of truth — do not re-define it here).

Boundary conditions: an empty weight table / p₁ ≤ 0 yields the rank1 Gibbs
value without a mixing correction (warning logged); p₁ > 1 is clamped to
1.0.  Callers are responsible for normalizing weights.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from acp.core.models import HARTREE_TO_KCAL

logger = logging.getLogger(__name__)

#: Boltzmann constant in Hartree per Kelvin (synced with energy.py:48).
_K_B_HARTREE_PER_KELVIN = 3.166811563e-6

#: Tolerance (Hartree) for the rank1-vs-full-mixing-formula cross-check.
_CROSS_CHECK_TOLERANCE_HARTREE = 1e-8


def mixing_entropy(weights: list[float]) -> float:
    """Return the mixing entropy −k_B·Σ p·ln p (Hartree/K per molecule).

    Zero / non-finite / negative weights contribute nothing (0·ln 0 ≡ 0).
    """
    total = 0.0
    for w in weights:
        try:
            w = float(w)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(w) or w <= 0.0:
            continue
        total -= w * math.log(w)
    return _K_B_HARTREE_PER_KELVIN * total


def s_mix_kcal_per_mol_kelvin(weights: list[float]) -> float:
    """Return the mixing entropy in kcal/(mol·K)."""
    return mixing_entropy(weights) * HARTREE_TO_KCAL


def s_mix_cal_per_mol_kelvin(weights: list[float]) -> float:
    """Return the mixing entropy in cal/(mol·K)."""
    return s_mix_kcal_per_mol_kelvin(weights) * 1000.0


def t_s_mix_kcal_per_mol(weights: list[float], temperature_k: float) -> float:
    """Return T·S_mix in kcal/mol (the entropy stabilization term)."""
    return s_mix_kcal_per_mol_kelvin(weights) * temperature_k


def ensemble_total_gibbs(rank1_gibbs: float, p1: float, temperature_k: float) -> float:
    """Return G_total = G₁ + k_B·T·ln p₁ (Hartree), rank1 form.

    If *p1* is missing / ≤ 0 the mixing correction is dropped (rank1 Gibbs
    returned unchanged) instead of clamping to an arbitrary tiny value —
    a clamped p₁ would inject a spurious ~16 kcal/mol correction.
    """
    if not math.isfinite(float(rank1_gibbs)):
        logger.warning("Non-finite rank1 Gibbs %r — returning it unchanged", rank1_gibbs)
        return rank1_gibbs
    p = float(p1)
    if not math.isfinite(p) or p <= 0.0:
        logger.warning("p1=%r not in (0, 1] — returning rank1 Gibbs without mixing correction", p1)
        return rank1_gibbs
    if p > 1.0:
        logger.warning("p1=%r > 1.0 — clamping to 1.0 (no mixing correction)", p1)
        p = 1.0
    return rank1_gibbs + _K_B_HARTREE_PER_KELVIN * temperature_k * math.log(p)


def ensemble_total_gibbs_from_values(gibbs_values: list[float], temperature_k: float) -> float:
    """Return G_total via the full mixing formula Σ p·G + RT·Σ p·ln p.

    The Boltzmann weights are recomputed from the given Gibbs values
    (renormalized).  Internally cross-checks against the rank1 form and
    raises :class:`RuntimeError` on disagreement (tolerance 1e-8 Ha) — the
    two forms are mathematically identical, so any mismatch indicates a
    bug, not a truncation effect.
    """
    values = [float(g) for g in gibbs_values if g is not None and math.isfinite(float(g))]
    if not values:
        raise ValueError("gibbs_values must contain at least one finite value")

    kt = _K_B_HARTREE_PER_KELVIN * temperature_k
    g_min = min(values)
    raw = [math.exp(-(g - g_min) / kt) for g in values]
    total = sum(raw)
    if total <= 0:
        raise ValueError("Boltzmann weights sum to zero — cannot compute ensemble total")
    weights = [w / total for w in raw]

    g_full = sum(w * g for w, g in zip(weights, values)) + kt * sum(
        w * math.log(w) for w in weights if w > 0.0
    )
    p1 = max(weights)
    g_rank1 = g_min + kt * math.log(p1)
    if abs(g_full - g_rank1) > _CROSS_CHECK_TOLERANCE_HARTREE:
        raise RuntimeError(
            f"Ensemble Gibbs cross-check failed: full-form {g_full:.12f} "
            f"vs rank1-form {g_rank1:.12f} Ha"
        )
    return g_full


@dataclass
class EnsembleThermoSummary:
    """Serializable summary of an ensemble total-Gibbs computation.

    Attributes:
        method: Weight-table flavour — ``dft_table`` (workflow 1) /
            ``censo_table_rank1`` / ``xtb_table_rank1`` (workflow 2).
        temperature_k: Ensemble temperature.
        total_gibbs_hartree: G_total in Hartree.
        total_gibbs_kcal_mol: G_total in kcal/mol.
        rank1_gibbs_hartree: Fine G₁ (Hartree).
        rank1_weight: Boltzmann weight p₁ of rank1.
        mixing_entropy_kcal_per_mol_kelvin: S_mix in kcal/(mol·K).
        mixing_entropy_cal_per_mol_kelvin: S_mix in cal/(mol·K).
        t_s_mix_kcal_per_mol: T·S_mix in kcal/mol.
        population_coverage: Cumulative weight of the table used for the
            mixing correction (1.0 for complete CENSO/xTB tables; <1.0 for
            the truncated DFT table of workflow 1).
        conformers: Per-conformer rows (conf_id / gibbs_hartree /
            delta_gibbs_kcal_mol / weight).
        censo_reference_gibbs_hartree: Optional CENSO-level total
            (gtot₁ + kT·ln p₁) quantifying the fine-DFT correction.
        censo_reference_gibbs_kcal_mol: Same, in kcal/mol.
    """

    method: str
    temperature_k: float
    total_gibbs_hartree: float
    total_gibbs_kcal_mol: float
    rank1_gibbs_hartree: float
    rank1_weight: float
    mixing_entropy_kcal_per_mol_kelvin: float
    mixing_entropy_cal_per_mol_kelvin: float
    t_s_mix_kcal_per_mol: float
    population_coverage: float
    conformers: list[dict[str, Any]] = field(default_factory=list)
    censo_reference_gibbs_hartree: float | None = None
    censo_reference_gibbs_kcal_mol: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict of the summary."""
        return asdict(self)

    def write_json(self, path: str | Path) -> None:
        """Serialize the summary to *path* as pretty-printed JSON."""
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")


__all__ = [
    "EnsembleThermoSummary",
    "ensemble_total_gibbs",
    "ensemble_total_gibbs_from_values",
    "mixing_entropy",
    "s_mix_cal_per_mol_kelvin",
    "s_mix_kcal_per_mol_kelvin",
    "t_s_mix_kcal_per_mol",
]
