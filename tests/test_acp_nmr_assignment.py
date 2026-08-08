"""Tests for assignment matching (DevDoc §5 stage 5 / §8.3)."""

from __future__ import annotations

from acp.nmr.assignment import collect_residual_inputs, match_assigned, match_unassigned
from acp.nmr.averaging import boltzmann_average_shieldings
from acp.nmr.io import parse_experimental_nmr
from acp.nmr.models import ConformerShielding, NmrConfig


def _shieldings(symbols_shielding: dict[int, tuple[str, float]]) -> dict[int, dict[str, object]]:
    return {idx: {"symbol": sym, "isotropic": iso} for idx, (sym, iso) in symbols_shielding.items()}


def _one_conformer_shieldings() -> dict[int, dict[str, object]]:
    return _shieldings(
        {
            0: ("C", 150.0),
            1: ("H", 28.0),
            2: ("H", 29.0),
            3: ("H", 31.0),
            4: ("H", 32.0),
        }
    )


def _shifts_for(symbols: list[str], shielding: dict[int, dict[str, object]]) -> list:
    cs = [ConformerShielding("c0", 1.0, shielding)]
    return boltzmann_average_shieldings(cs, symbols, NmrConfig())


def test_assigned_passthrough() -> None:
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    exp = parse_experimental_nmr("C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)")
    pairs = match_assigned(shifts, exp)
    assert set(pairs) == {"13C", "1H"}
    assert len(pairs["1H"]) == 4
    assert pairs["1H"][0][0].atom_label == "H1"


def test_assigned_omits_dropped_atoms() -> None:
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    exp = parse_experimental_nmr("C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)\nOMIT: H4")
    pairs = match_assigned(shifts, exp)
    labels = [s.atom_label for s, _ in pairs["1H"]]
    assert "H4" not in labels
    assert len(labels) == 3


def test_unassigned_hungarian_full_match() -> None:
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    # peaks deliberately shuffled to confirm Hungarian minimizes total cost
    exp = parse_experimental_nmr("C: 36.0\nH: 3.5, 2.5, 0.5, -0.5")
    pairs = match_unassigned(shifts, exp)
    h_group = pairs["1H"]
    assert len(h_group) == 4
    # each peak matched exactly once
    peaks = [p for _, p in h_group]
    assert len({p.shift_ppm for p in peaks}) == 4


def test_unassigned_intensity_weighting() -> None:
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    # one peak has multiplicity 3 (CH3-like)
    exp = parse_experimental_nmr("C: 36.0\nH: 3.5, 2.5, 0.5, -0.5(3)")
    pairs_weighted = match_unassigned(shifts, exp, use_intensity_weight=True)
    assert len(pairs_weighted["1H"]) == 4


def test_unassigned_more_signals_than_peaks() -> None:
    # 4 H signals, only 2 peaks → 2 dummies dropped, 2 real matches
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    exp = parse_experimental_nmr("H: 3.0, 1.0")
    pairs = match_unassigned(shifts, exp)
    assert len(pairs["1H"]) == 2


def test_collect_residual_inputs_shape() -> None:
    symbols = ["C", "H", "H", "H", "H"]
    shifts = _shifts_for(symbols, _one_conformer_shieldings())
    exp = parse_experimental_nmr("C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)")
    pairs = match_assigned(shifts, exp)
    ri = collect_residual_inputs(pairs)
    assert "1H" in ri
    assert len(ri["1H"]["calc"]) == 4
    assert len(ri["1H"]["exp"]) == 4
    assert ri["1H"]["labels"] == ["H1", "H2", "H3", "H4"]
