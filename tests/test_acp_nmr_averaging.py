"""Tests for Boltzmann + equivalence averaging (DevDoc §5 stage 4 / §8.1).

Complements ``test_acp_nmr_ensemble_quality.py`` (conformer completeness gate)
with the numeric averaging contract and the todo-34 ``SignalGroup`` output:
every emitted ``AtomShift`` carries the signal definition actually used.
"""

from __future__ import annotations

import pytest

from acp.nmr.averaging import boltzmann_average_shieldings
from acp.nmr.equivalence import (
    EQ_BASIS_EXPLICIT,
    EQ_BASIS_TOPOLOGY,
    EQ_BASIS_UNKNOWN,
    EquivalenceGroup,
    EquivalenceResult,
)
from acp.nmr.models import ConformerShielding, NmrConfig

SYMBOLS = ["C", "H", "C", "H"]


def _conf(
    conformer_id: str, weight: float, c0: float, h1: float, c2: float, h2: float
) -> ConformerShielding:
    return ConformerShielding(
        conformer_id,
        weight,
        {
            0: {"symbol": "C", "isotropic": c0},
            1: {"symbol": "H", "isotropic": h1},
            2: {"symbol": "C", "isotropic": c2},
            3: {"symbol": "H", "isotropic": h2},
        },
    )


def _shift_for(shielding: float, nucleus: str, config: NmrConfig) -> float:
    ref = float(config.tms_for(nucleus))
    return (ref - shielding) / (1.0 - ref / 1e6)


def test_boltzmann_weighted_mean_over_conformers() -> None:
    cfg = NmrConfig()
    # weights 3:1 → carbon shielding mean = 0.75·100 + 0.25·108 = 102
    shifts = boltzmann_average_shieldings(
        [
            _conf("c0", 0.75, 100.0, 25.0, 100.0, 25.0),
            _conf("c1", 0.25, 108.0, 29.0, 108.0, 29.0),
        ],
        SYMBOLS,
        cfg,
    )
    by_index = {s.atom_index: s for s in shifts}
    assert by_index[0].shielding_ppm == pytest.approx(102.0)
    assert by_index[0].shift_ppm == pytest.approx(_shift_for(102.0, "13C", cfg))
    assert by_index[1].shielding_ppm == pytest.approx(26.0)
    assert by_index[1].shift_ppm == pytest.approx(_shift_for(26.0, "1H", cfg))


def test_weights_are_renormalized_once_over_the_complete_set() -> None:
    cfg = NmrConfig()
    shifts = boltzmann_average_shieldings(
        [
            _conf("c0", 2.0, 100.0, 25.0, 106.0, 27.0),
            _conf("c1", 2.0, 108.0, 29.0, 106.0, 27.0),
        ],
        SYMBOLS,
        cfg,
    )
    by_index = {s.atom_index: s for s in shifts}
    # equal weights after renormalization → plain mean
    assert by_index[0].shielding_ppm == pytest.approx(104.0)


def test_equivalence_group_is_averaged_and_carries_membership() -> None:
    cfg = NmrConfig()
    groups = EquivalenceResult(
        (
            EquivalenceGroup((0, 2), EQ_BASIS_EXPLICIT),
            EquivalenceGroup((1,), EQ_BASIS_TOPOLOGY),
            EquivalenceGroup((3,), EQ_BASIS_TOPOLOGY),
        )
    )
    shifts = boltzmann_average_shieldings(
        [_conf("c0", 1.0, 100.0, 25.0, 106.0, 26.0)],
        SYMBOLS,
        cfg,
        equivalence_groups=groups,
    )
    by_index = {s.atom_index: s for s in shifts}
    # one signal for the group, emitted at the lowest-index representative
    assert set(by_index) == {0, 1, 3}
    assert by_index[0].shielding_ppm == pytest.approx(103.0)
    assert by_index[0].signal_group is not None
    assert by_index[0].signal_group.atom_uids == ("C1", "C2")
    assert by_index[0].signal_group.coefficients == pytest.approx((0.5, 0.5))
    assert by_index[0].signal_group.equivalence_basis == EQ_BASIS_EXPLICIT
    # ungrouped atoms stay their own singleton signals
    assert by_index[1].signal_group is not None
    assert by_index[1].signal_group.atom_uids == ("H1",)
    assert by_index[1].signal_group.equivalence_basis == EQ_BASIS_TOPOLOGY
    assert by_index[3].signal_group.atom_uids == ("H2",)


def test_no_equivalence_input_still_yields_singleton_signal_groups() -> None:
    """Every emitted shift carries its (trivial) signal definition."""
    shifts = boltzmann_average_shieldings(
        [_conf("c0", 1.0, 100.0, 25.0, 106.0, 26.0)], SYMBOLS, NmrConfig()
    )
    assert len(shifts) == 4
    for shift in shifts:
        group = shift.signal_group
        assert group is not None
        assert len(group.atom_uids) == 1
        assert group.coefficients == (1.0,)
        # no topology was supplied: equivalence is never over-claimed
        assert group.equivalence_basis == EQ_BASIS_UNKNOWN


def test_plain_index_groups_are_treated_as_unknown_basis() -> None:
    shifts = boltzmann_average_shieldings(
        [_conf("c0", 1.0, 100.0, 25.0, 106.0, 26.0)],
        SYMBOLS,
        NmrConfig(),
        equivalence_groups=[[0, 2]],
    )
    carbon = next(s for s in shifts if s.symbol == "C")
    assert carbon.signal_group is not None
    assert carbon.signal_group.equivalence_basis == EQ_BASIS_UNKNOWN
    assert carbon.shielding_ppm == pytest.approx(103.0)


class _WeightedGroup(tuple):
    """Duck-typed group carrying explicit averaging coefficients."""

    def __new__(cls, indices: tuple[int, ...], coefficients: tuple[float, ...]) -> _WeightedGroup:
        instance = super().__new__(cls, indices)
        instance.coefficients = coefficients  # type: ignore[attr-defined]
        return instance


def test_explicit_group_coefficients_are_honored() -> None:
    group = _WeightedGroup((0, 2), (0.75, 0.25))
    shifts = boltzmann_average_shieldings(
        [_conf("c0", 1.0, 100.0, 25.0, 106.0, 26.0)],
        SYMBOLS,
        NmrConfig(),
        equivalence_groups=[group],
    )
    carbon = next(s for s in shifts if s.symbol == "C")
    assert carbon.signal_group is not None
    assert carbon.signal_group.coefficients == pytest.approx((0.75, 0.25))
    assert carbon.shielding_ppm == pytest.approx(0.75 * 100.0 + 0.25 * 106.0)


def test_unusable_group_coefficients_fall_back_to_equal_weights(
    caplog: pytest.LogCaptureFixture,
) -> None:
    group = _WeightedGroup((0, 2), (0.5,))  # wrong length for two members
    shifts = boltzmann_average_shieldings(
        [_conf("c0", 1.0, 100.0, 25.0, 106.0, 26.0)],
        SYMBOLS,
        NmrConfig(),
        equivalence_groups=[group],
    )
    carbon = next(s for s in shifts if s.symbol == "C")
    assert carbon.signal_group is not None
    assert carbon.signal_group.coefficients == pytest.approx((0.5, 0.5))
    assert carbon.shielding_ppm == pytest.approx(103.0)
    assert "using equal weights" in caplog.text


def test_omitted_atoms_are_not_group_members() -> None:
    groups = EquivalenceResult(
        (
            EquivalenceGroup((0, 2), EQ_BASIS_EXPLICIT),
            EquivalenceGroup((1,), EQ_BASIS_TOPOLOGY),
            EquivalenceGroup((3,), EQ_BASIS_TOPOLOGY),
        )
    )
    shifts = boltzmann_average_shieldings(
        [_conf("c0", 1.0, 100.0, 25.0, 106.0, 26.0)],
        SYMBOLS,
        NmrConfig(),
        equivalence_groups=groups,
        omit_atom_indices=[2],
    )
    by_index = {s.atom_index: s for s in shifts}
    assert set(by_index) == {0, 1, 3}
    # representative 0 keeps the group identity but only surviving members
    assert by_index[0].signal_group is not None
    assert by_index[0].signal_group.atom_uids == ("C1",)
    assert by_index[0].signal_group.coefficients == (1.0,)
    assert by_index[0].shielding_ppm == pytest.approx(100.0)


def test_no_complete_conformers_returns_empty() -> None:
    incomplete = ConformerShielding("c0", 1.0, {0: {"symbol": "C", "isotropic": 100.0}})
    assert boltzmann_average_shieldings([incomplete], SYMBOLS, NmrConfig()) == []
