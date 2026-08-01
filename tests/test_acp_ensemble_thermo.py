"""Tests for the ensemble total-Gibbs helpers (acp.run energy G_total).

Covers the rank1 form G_total = G₁ + RT·ln p₁, the full mixing formula
Σ p·G + RT·Σ p·ln p, their mathematical equivalence, unit conversions and
the boundary conditions (empty / degenerate weight tables).
"""

from __future__ import annotations

import json
import math

import pytest

from acp.core.models import HARTREE_TO_KCAL
from acp.workflows.ensemble_thermo import (
    EnsembleThermoSummary,
    ensemble_total_gibbs,
    ensemble_total_gibbs_from_values,
    mixing_entropy,
    s_mix_cal_per_mol_kelvin,
    s_mix_kcal_per_mol_kelvin,
    t_s_mix_kcal_per_mol,
)

_K_B = 3.166811563e-6
_T = 298.15
_KT_HARTREE = _K_B * _T
_KT_KCAL = _KT_HARTREE * HARTREE_TO_KCAL  # ≈ 0.5925 kcal/mol


# ---------------------------------------------------------------------------
# Rank1 form
# ---------------------------------------------------------------------------


def test_single_conformer_no_mixing_correction() -> None:
    assert ensemble_total_gibbs(-154.95, 1.0, _T) == pytest.approx(-154.95)


def test_two_equal_weights() -> None:
    g1 = -154.95
    expected = g1 + _KT_HARTREE * math.log(0.5)
    assert ensemble_total_gibbs(g1, 0.5, _T) == pytest.approx(expected)


def test_numeric_example_from_devdoc_section_14() -> None:
    """ΔG₂ = 1.0 kcal/mol → p₁ ≈ 0.844 → G_total − G₁ ≈ −0.101 kcal/mol.

    The devdoc example uses kT = 0.5925 kcal/mol (rounded), so tolerances
    are kept at 1e-3.
    """
    g1 = -154.95
    g2 = g1 + 1.0 / HARTREE_TO_KCAL
    p1 = 1.0 / (1.0 + math.exp(-1.0 / _KT_KCAL))
    assert p1 == pytest.approx(0.8442, abs=1e-3)
    total = ensemble_total_gibbs(g1, p1, _T)
    assert (total - g1) * HARTREE_TO_KCAL == pytest.approx(-0.101, abs=1e-3)
    # full-mixing form agrees
    assert ensemble_total_gibbs_from_values([g1, g2], _T) == pytest.approx(total, abs=1e-10)


# ---------------------------------------------------------------------------
# Full mixing formula
# ---------------------------------------------------------------------------


def test_full_formula_matches_rank1_form() -> None:
    g1 = -154.95
    values = [g1, g1 + 0.0005, g1 + 0.0032, g1 + 0.008]
    full = ensemble_total_gibbs_from_values(values, _T)
    z = sum(math.exp(-(g - g1) / _KT_HARTREE) for g in values)
    rank1 = ensemble_total_gibbs(g1, 1.0 / z, _T)
    assert full == pytest.approx(rank1, abs=1e-8)


def test_full_formula_equal_weights_halves() -> None:
    g1 = -154.95
    g2 = g1  # degenerate pair → weights 0.5/0.5
    g3 = g1 + 50.0 / HARTREE_TO_KCAL  # 50 kcal/mol away → negligible
    total = ensemble_total_gibbs_from_values([g1, g2, g3], _T)
    # p1 ≈ p2 ≈ 0.5 → G_total ≈ G1 + RT·ln 0.5
    expected = g1 + _KT_HARTREE * math.log(0.5)
    assert total == pytest.approx(expected, abs=1e-6)


def test_full_formula_empty_raises() -> None:
    with pytest.raises(ValueError):
        ensemble_total_gibbs_from_values([], _T)


def test_full_formula_all_nonfinite_raises() -> None:
    with pytest.raises(ValueError):
        ensemble_total_gibbs_from_values([float("nan"), float("inf")], _T)


# ---------------------------------------------------------------------------
# Mixing entropy
# ---------------------------------------------------------------------------


def test_mixing_entropy_units() -> None:
    w = [0.8, 0.15, 0.05]
    s_ha = mixing_entropy(w)
    assert s_ha == pytest.approx(-_K_B * sum(p * math.log(p) for p in w))
    assert s_mix_kcal_per_mol_kelvin(w) == pytest.approx(s_ha * HARTREE_TO_KCAL)
    # 1 Ha/K·molecule = 627.509 kcal/(mol·K) = 6.275e5 cal/(mol·K)
    assert s_mix_cal_per_mol_kelvin(w) == pytest.approx(s_ha * HARTREE_TO_KCAL * 1000.0)
    assert t_s_mix_kcal_per_mol(w, _T) == pytest.approx(s_mix_kcal_per_mol_kelvin(w) * _T)


def test_mixing_entropy_single_conformer_zero() -> None:
    assert mixing_entropy([1.0]) == pytest.approx(0.0)
    assert s_mix_cal_per_mol_kelvin([1.0]) == pytest.approx(0.0)


def test_mixing_entropy_ignores_nonpositive() -> None:
    assert mixing_entropy([0.5, 0.0, -0.2, 0.5]) == pytest.approx(mixing_entropy([0.5, 0.5]))
    assert mixing_entropy([]) == pytest.approx(0.0)
    assert mixing_entropy([0.0, 0.0]) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Boundary conditions
# ---------------------------------------------------------------------------


def test_rank1_form_p1_le_zero_returns_gibbs() -> None:
    g1 = -154.95
    assert ensemble_total_gibbs(g1, 0.0, _T) == pytest.approx(g1)
    assert ensemble_total_gibbs(g1, -1.0, _T) == pytest.approx(g1)
    assert ensemble_total_gibbs(g1, float("nan"), _T) == pytest.approx(g1)


def test_rank1_form_p1_above_one_clamped() -> None:
    g1 = -154.95
    assert ensemble_total_gibbs(g1, 1.7, _T) == pytest.approx(g1)


# ---------------------------------------------------------------------------
# EnsembleThermoSummary serialization
# ---------------------------------------------------------------------------


def test_summary_to_dict_and_write_json(tmp_path) -> None:
    summary = EnsembleThermoSummary(
        method="dft_table",
        temperature_k=_T,
        total_gibbs_hartree=-154.9506544,
        total_gibbs_kcal_mol=-154.9506544 * HARTREE_TO_KCAL,
        rank1_gibbs_hartree=-154.95,
        rank1_weight=0.8442,
        mixing_entropy_kcal_per_mol_kelvin=0.0011,
        mixing_entropy_cal_per_mol_kelvin=1.1,
        t_s_mix_kcal_per_mol=0.33,
        population_coverage=0.99,
        conformers=[{"conf_id": "CONF1", "weight": 0.8442}],
        censo_reference_gibbs_hartree=-154.9504,
        censo_reference_gibbs_kcal_mol=-154.9504 * HARTREE_TO_KCAL,
    )
    path = tmp_path / "ensemble_thermo.json"
    summary.write_json(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["method"] == "dft_table"
    assert data["rank1_weight"] == pytest.approx(0.8442)
    assert data["censo_reference_gibbs_hartree"] == pytest.approx(-154.9504)
    assert data["conformers"][0]["conf_id"] == "CONF1"
    assert data["population_coverage"] == pytest.approx(0.99)
