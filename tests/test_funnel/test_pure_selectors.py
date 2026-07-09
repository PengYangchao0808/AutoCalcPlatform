"""Pure-function tests for the new funnel CandidateSet."""
from __future__ import annotations

import math
import pytest
from pathlib import Path

from conformer_search.ensemble.candidate_set import (
    FunnelRecord, FunnelRecordSet, GAS_CONSTANT_HARTREE,
)
from conformer_search.utils.constants import HARTREE_TO_KCAL


def make_record(conf_id: str, energy_key: str = "xtb_sp",
                delta_kcal: float = 0.0, base_hartree: float = -100.0,
                input_order: int = 0) -> FunnelRecord:
    return FunnelRecord(
        conformer_id=conf_id,
        xyz_path=Path(f"{conf_id}.xyz"),
        input_order=input_order,
        energies={energy_key: base_hartree + delta_kcal / HARTREE_TO_KCAL},
    )


class TestFunnelRecordSet:
    def test_relative_energies_sets_minimum_to_zero(self):
        records = FunnelRecordSet([
            make_record("conf_000", delta_kcal=0.0),
            make_record("conf_001", delta_kcal=2.5),
            make_record("conf_002", delta_kcal=5.0),
        ])
        records.relative_energies("xtb_sp")
        assert records[0].relative_kcal["xtb_sp"] == pytest.approx(0.0)
        assert records[1].relative_kcal["xtb_sp"] == pytest.approx(2.5)
        assert records[2].relative_kcal["xtb_sp"] == pytest.approx(5.0)

    def test_relative_energies_converts_hartree_to_kcal_correctly(self):
        r = make_record("conf_000", delta_kcal=3.5)
        rs = FunnelRecordSet([r, make_record("conf_001", delta_kcal=0.0)])
        rs.relative_energies("xtb_sp")
        assert rs[0].relative_kcal["xtb_sp"] == pytest.approx(3.5)

    def test_stable_sort_uses_input_order_as_tiebreaker(self):
        records = FunnelRecordSet([
            make_record("conf_A", delta_kcal=0.0, input_order=1),
            make_record("conf_B", delta_kcal=0.0, input_order=0),
        ])
        records.stable_sort("xtb_sp")
        assert records[0].input_order == 0  # Lower input_order wins ties
        assert records[1].input_order == 1

    def test_select_by_window_includes_exact_boundary(self):
        records = FunnelRecordSet([
            make_record("conf_000", delta_kcal=0.0),
            make_record("conf_001", delta_kcal=3.0),
            make_record("conf_002", delta_kcal=3.5),
        ])
        sel, rej = records.select_by_window("xtb_sp", 3.0)
        assert len(sel) == 2
        assert len(rej) == 1
        assert sel[0].conformer_id == "conf_000"
        assert rej[0].conformer_id == "conf_002"

    def test_select_by_window_returns_rejected_separately(self):
        records = FunnelRecordSet([
            make_record("conf_000", delta_kcal=0.0),
            make_record("conf_001", delta_kcal=4.1),
        ])
        sel, rej = records.select_by_window("xtb_sp", 4.0)
        assert len(sel) == 1
        assert len(rej) == 1
        assert rej[0].removal_reason and ">" in rej[0].removal_reason

    def test_select_top_n_returns_lowest_n(self):
        records = FunnelRecordSet([
            make_record("conf_000", delta_kcal=5.0),
            make_record("conf_001", delta_kcal=0.0),
            make_record("conf_002", delta_kcal=2.0),
        ])
        sel, _ = records.select_top_n("xtb_sp", 2)
        assert len(sel) == 2
        assert sel[0].conformer_id == "conf_001"
        assert sel[1].conformer_id == "conf_002"

    def test_select_top_n_when_n_exceeds_population_rejects_none(self):
        records = FunnelRecordSet([make_record("conf_000", delta_kcal=0.0)])
        sel, rej = records.select_top_n("xtb_sp", 5)
        assert len(sel) == 1
        assert len(rej) == 0

    def test_select_rank1(self):
        records = FunnelRecordSet([
            make_record("conf_000", delta_kcal=5.0),
            make_record("conf_001", delta_kcal=0.0),
        ])
        sel, _ = records.select_rank1("xtb_sp")
        assert len(sel) == 1
        assert sel[0].conformer_id == "conf_001"

    def test_select_by_boltzmann_cutoff_populates_weights(self):
        records = FunnelRecordSet([
            make_record("conf_000", delta_kcal=0.0),
            make_record("conf_001", delta_kcal=0.5),
        ])
        sel, _ = records.select_by_boltzmann_cutoff("xtb_sp", 0.95)
        assert sel[0].boltzmann_weight is not None
        assert sel[1].boltzmann_weight is not None
        total = sum(r.boltzmann_weight for r in sel)  # type: ignore[misc]
        assert total == pytest.approx(1.0)

    def test_select_by_boltzmann_cutoff_keeps_minimal_prefix(self):
        # conf_000 weight = 0.9, conf_001 weight = 0.1
        delta = math.log(9.0) * GAS_CONSTANT_HARTREE * 298.15 * HARTREE_TO_KCAL
        records = FunnelRecordSet([
            make_record("conf_000", delta_kcal=0.0),
            make_record("conf_001", delta_kcal=delta),
        ])
        sel, rej = records.select_by_boltzmann_cutoff("xtb_sp", 0.90)
        assert len(sel) == 1
        assert len(rej) == 1
        assert sel[0].conformer_id == "conf_000"

    def test_top2_if_gap_small_keeps_two_when_gap_equals_threshold(self):
        records = FunnelRecordSet([
            make_record("conf_000", delta_kcal=0.0),
            make_record("conf_001", delta_kcal=1.0),
            make_record("conf_002", delta_kcal=2.0),
        ])
        sel, _ = records.select_rank1_with_top2_fallback("xtb_sp", 1.0)
        assert len(sel) == 2

    def test_top2_if_gap_small_keeps_one_when_gap_above_threshold(self):
        records = FunnelRecordSet([
            make_record("conf_000", delta_kcal=0.0),
            make_record("conf_001", delta_kcal=1.1),
            make_record("conf_002", delta_kcal=2.0),
        ])
        sel, _ = records.select_rank1_with_top2_fallback("xtb_sp", 1.0)
        assert len(sel) == 1

    def test_empty_input_returns_empty(self):
        records = FunnelRecordSet()
        sel, rej = records.select_by_window("xtb_sp", 3.0)
        assert len(sel) == 0
        assert len(rej) == 0

    def test_single_candidate_survives_all_selections(self):
        records = FunnelRecordSet([make_record("conf_000", delta_kcal=0.0)])
        sel, _ = records.select_by_window("xtb_sp", 3.0)
        assert len(sel) == 1
        sel, _ = records.select_top_n("xtb_sp", 1)
        assert len(sel) == 1
        sel, _ = records.select_by_boltzmann_cutoff("xtb_sp", 0.95)
        assert len(sel) == 1

    def test_missing_energy_records_are_rejected(self):
        r = FunnelRecord(conformer_id="bad", xyz_path=Path("bad.xyz"))
        records = FunnelRecordSet([r, make_record("good", delta_kcal=0.0)])
        sel, rej = records.select_by_window("xtb_sp", 3.0)
        assert len(sel) == 1
        assert len(rej) == 1
        assert rej[0].conformer_id == "bad"

    def test_boltzmann_weights_sum_to_one(self):
        records = FunnelRecordSet([
            make_record("conf_000", delta_kcal=0.0),
            make_record("conf_001", delta_kcal=1.0),
            make_record("conf_002", delta_kcal=3.0),
        ])
        sel, _ = records.select_by_boltzmann_cutoff("xtb_sp", 0.999)
        total = sum(r.boltzmann_weight or 0.0 for r in sel)
        assert total == pytest.approx(1.0)
