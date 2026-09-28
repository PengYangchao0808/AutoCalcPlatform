"""Confsearch weight-provenance + screening join-key tests (plan todo 2, Metis B1).

Baseline pins (must stay green before AND after the change):
* manifest ``conf_id`` keeps the ``conf_NNNN`` format
* non-rank1 policies keep the historical sum≈1 Boltzmann gate

Red-first regressions (failed on pre-change code; see
``.omo/evidence/confsearch-weight-provenance-final-report/task-2-provenance.log``):
* ``source_conf_id`` joins the screening weight table (Metis B1)
* rank1 single entry carries the CENSO table p₁ (not 1.0) and G1 passes
* partial/incorrect table lookups degrade to ``weight_source="computed"``
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from acp.confsearch.contracts import ConformerEntry, ProtocolOutcome
from acp.confsearch.protocols._common import records_from_ensemble_result
from acp.confsearch.result_helpers import build_entries, quality_gates
from acp.confsearch.shared.boltzmann import boltzmann_weights


def _record(
    source_conf_id: str | None,
    energy: float,
    free_energy: float | None = None,
) -> dict[str, Any]:
    return {
        "conf_id": source_conf_id or "x",
        "source_conf_id": source_conf_id,
        "symbols": ["O", "H", "H"],
        "coordinates": [[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]],
        "energy_hartree": energy,
        "free_energy_hartree": energy if free_energy is None else free_energy,
        "weight": 0.123,  # must stay unconsumed by build_entries
    }


def _entry(conf_id: str, weight: float, source_conf_id: str | None = None) -> ConformerEntry:
    kwargs: dict[str, Any] = {"source_conf_id": source_conf_id} if source_conf_id else {}
    return ConformerEntry(
        conf_id=conf_id,
        geometry="",
        boltzmann_weight=weight,
        rank=int(conf_id.split("_")[1]),
        **kwargs,
    )


# --- baseline pins (green before the change) --------------------------------


def test_baseline_manifest_conf_id_format_unchanged() -> None:
    """conf_id REMAINS conf_NNNN regardless of the screening-table key."""
    outcome = ProtocolOutcome(
        records=[_record("CONF1", -154.0), _record("CONF2", -153.0)],
        temperature_k=298.15,
    )
    entries = build_entries(outcome)
    assert [e.conf_id for e in entries] == ["conf_0001", "conf_0002"]
    assert [e.rank for e in entries] == [1, 2]


def test_baseline_record_weight_stays_unconsumed() -> None:
    """build_entries must not start consuming record["weight"] (0.123 here)."""
    outcome = ProtocolOutcome(records=[_record("CONF1", -154.0)], temperature_k=298.15)
    entry = build_entries(outcome)[0]
    assert entry.boltzmann_weight != 0.123
    assert entry.boltzmann_weight == pytest.approx(1.0)


def test_baseline_non_rank1_sum_gate_unchanged() -> None:
    """Policies other than rank1 keep abs(sum-1) < 1e-3."""
    outcome = ProtocolOutcome(records=[], temperature_k=298.15, sampling={"method": "stub"})
    good = [_entry("conf_0001", 0.7), _entry("conf_0002", 0.3)]
    gates = quality_gates(good, outcome, [], protocol="censo-crest")
    assert gates["boltzmann_weights_valid"] is True
    assert gates["G1"] == "PASS"

    bad = [_entry("conf_0001", 0.7), _entry("conf_0002", 0.2)]
    gates_bad = quality_gates(bad, outcome, [], protocol="censo-crest")
    assert gates_bad["boltzmann_weights_valid"] is False
    assert gates_bad["G1"] == "FAIL"


def test_baseline_relative_energies_and_ordering_unchanged() -> None:
    outcome = ProtocolOutcome(
        records=[_record("CONF2", -153.0), _record("CONF1", -154.0)],
        temperature_k=298.15,
    )
    entries = build_entries(outcome)
    assert entries[0].relative_energy_kcal == pytest.approx(0.0, abs=1e-9)
    assert entries[1].relative_energy_kcal == pytest.approx(627.509474, rel=1e-6)
    assert entries[1].conf_id == "conf_0002"


# --- contracts: additive fields ----------------------------------------------


def test_conformer_entry_to_dict_serializes_provenance() -> None:
    entry = ConformerEntry(
        conf_id="conf_0001",
        geometry="",
        source_conf_id="CONF1",
        weight_source="censo",
        refined=True,
    )
    d = entry.to_dict()
    assert d["source_conf_id"] == "CONF1"
    assert d["weight_source"] == "censo"
    assert d["refined"] is True
    # existing keys unchanged
    assert set(d) == {
        "conf_id",
        "geometry",
        "energy_hartree",
        "free_energy_hartree",
        "relative_energy_kcal",
        "boltzmann_weight",
        "rank",
        "source_conf_id",
        "weight_source",
        "refined",
    }
    defaults = ConformerEntry(conf_id="c", geometry="").to_dict()
    assert defaults["source_conf_id"] is None
    assert defaults["weight_source"] is None
    assert defaults["refined"] is False


def test_protocol_outcome_provenance_defaults() -> None:
    outcome = ProtocolOutcome(records=[])
    assert outcome.weight_table is None
    assert outcome.weight_source is None
    assert outcome.weight_method is None
    assert outcome.population_coverage is None
    assert outcome.energy_kind is None


# --- join key propagation (Metis B1) ------------------------------------------


def test_records_from_ensemble_result_carries_source_conf_id() -> None:
    from acp.core.models import Structure, StructureEnsemble, StructureRecord

    def _structure(structure_id: str, metadata: dict[str, Any]) -> Structure:
        return Structure(
            id=structure_id,
            charge=0,
            multiplicity=1,
            symbols=["O", "H", "H"],
            coordinates=[[0.0, 0.0, 0.0], [0.9, 0.0, 0.0], [-0.3, 0.9, 0.0]],
            metadata=metadata,
        )

    ensemble = StructureEnsemble(
        records=[
            StructureRecord(
                structure=_structure("mol_conf001", {"source": "CONF1", "conf_id": "conf_001"}),
                energy_hartree=-154.0,
                free_energy_hartree=-154.1,
                weight=0.8442,
            ),
            StructureRecord(
                structure=_structure("mol_conf002", {"conf_id": "conf_002"}),
                energy_hartree=-153.0,
                free_energy_hartree=-153.1,
                weight=0.1558,
            ),
            StructureRecord(
                structure=_structure("mol_conf003", {}),
                energy_hartree=-152.0,
            ),
        ]
    )
    records = records_from_ensemble_result(SimpleNamespace(ensemble=ensemble))
    assert records[0]["source_conf_id"] == "CONF1"  # metadata["source"] wins
    assert records[1]["source_conf_id"] == "conf_002"  # conf_id fallback
    assert records[2]["source_conf_id"] is None
    # existing keys untouched
    assert records[0]["conf_id"] == "conf_001"
    assert records[0]["weight"] == 0.8442
    assert set(records[0]) >= {
        "conf_id",
        "symbols",
        "coordinates",
        "energy_hartree",
        "free_energy_hartree",
        "weight",
        "properties",
    }


# --- build_entries: table join + fallback -------------------------------------


def test_rank1_table_p1_flows_to_single_entry_and_gate_passes() -> None:
    """D1: rank1 keeps ONE entry whose weight is the CENSO table p₁ (0.8442)."""
    outcome = ProtocolOutcome(
        records=[_record("CONF1", -154.0)],
        temperature_k=298.15,
        refined_conf_ids=["CONF1"],
        sampling={"method": "censo"},
        weight_table={"CONF1": 0.8442},
        weight_source="censo",
        weight_method="censo_table_rank1",
        population_coverage=1.0,
        energy_kind="dft",
    )
    entries = build_entries(outcome)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.conf_id == "conf_0001"
    assert entry.source_conf_id == "CONF1"
    assert entry.boltzmann_weight == pytest.approx(0.8442, abs=1e-12)
    assert entry.weight_source == "censo"
    assert entry.refined is True

    gates = quality_gates(entries, outcome, ["conf_0001"], protocol="censo-crest", policy="rank1")
    assert gates["boltzmann_weights_valid"] is True
    assert gates["weight_source"] == "censo"
    assert gates["G1"] == "PASS"


def test_multi_entry_table_join_and_refined_flags() -> None:
    outcome = ProtocolOutcome(
        records=[_record("CONF1", -154.0), _record("CONF2", -153.0)],
        temperature_k=298.15,
        refined_conf_ids=["CONF2"],
        sampling={"method": "censo"},
        weight_table={"CONF1": 0.9, "CONF2": 0.1},
        weight_source="dft",
        weight_method="dft_table",
        population_coverage=0.97,
        energy_kind="dft",
    )
    entries = build_entries(outcome)
    assert [e.boltzmann_weight for e in entries] == [0.9, 0.1]
    assert [e.weight_source for e in entries] == ["dft", "dft"]
    assert [e.refined for e in entries] == [False, True]


def test_refined_flag_false_when_no_refined_ids() -> None:
    outcome = ProtocolOutcome(
        records=[_record("CONF1", -154.0)],
        temperature_k=298.15,
        refined_conf_ids=[],
        weight_table={"CONF1": 1.0},
        weight_source="censo",
    )
    assert build_entries(outcome)[0].refined is False


def test_partial_table_miss_falls_back_to_computed_for_all() -> None:
    """ANY lookup miss → recompute for ALL entries + weight_source="computed"."""
    outcome = ProtocolOutcome(
        records=[_record("CONF1", -154.0), _record("CONF2", -153.0)],
        temperature_k=298.15,
        sampling={"method": "censo"},
        weight_table={"CONF1": 0.9},  # CONF2 missing
        weight_source="censo",
    )
    entries = build_entries(outcome)
    expected = boltzmann_weights([-154.0, -153.0], 298.15)
    assert [e.boltzmann_weight for e in entries] == list(expected)
    assert all(e.weight_source == "computed" for e in entries)
    total = sum(e.boltzmann_weight for e in entries)
    assert total == pytest.approx(1.0, abs=1e-9)
    # no table fragment may leak
    assert all(e.boltzmann_weight != 0.9 for e in entries)


def test_malformed_table_keys_degrade_to_computed_without_crash() -> None:
    outcome = ProtocolOutcome(
        records=[_record("CONF1", -154.0)],
        temperature_k=298.15,
        refined_conf_ids=["CONF1"],
        sampling={"method": "stub"},
        weight_table={"WRONG_KEY": "not-a-number", 42: None},
        weight_source="censo",
    )
    entries = build_entries(outcome)  # must not raise
    assert entries[0].weight_source == "computed"
    assert entries[0].boltzmann_weight == pytest.approx(1.0)
    gates = quality_gates(entries, outcome, ["conf_0001"], protocol="censo-crest", policy="rank1")
    assert gates["G1"] == "PASS"  # degraded rank1: 0 < w <= 1 holds


def test_no_table_keeps_recompute_and_none_source() -> None:
    outcome = ProtocolOutcome(
        records=[_record("CONF1", -154.0), _record("CONF2", -153.0)],
        temperature_k=298.15,
        sampling={"method": "stub"},
    )
    entries = build_entries(outcome)
    expected = boltzmann_weights([-154.0, -153.0], 298.15)
    assert [e.boltzmann_weight for e in entries] == list(expected)
    assert all(e.weight_source is None for e in entries)
    assert all(e.refined is False for e in entries)


# --- quality_gates: policy-aware boltzmann gate --------------------------------


def test_rank1_gate_rejects_weight_off_table() -> None:
    outcome = ProtocolOutcome(
        records=[],
        weight_table={"CONF1": 0.8442},
        weight_source="censo",
        sampling={"method": "censo"},
    )
    entry = _entry("conf_0001", 0.5, source_conf_id="CONF1")
    gates = quality_gates([entry], outcome, ["conf_0001"], protocol="censo-crest", policy="rank1")
    assert gates["boltzmann_weights_valid"] is False
    assert gates["G1"] == "FAIL"


def test_rank1_gate_table_tolerance_nine_digits() -> None:
    outcome = ProtocolOutcome(
        records=[],
        weight_table={"CONF1": 0.8442},
        weight_source="censo",
        sampling={"method": "censo"},
    )
    within = quality_gates(
        [_entry("conf_0001", 0.8442 + 1e-9, source_conf_id="CONF1")],
        outcome,
        ["conf_0001"],
        protocol="censo-crest",
        policy="rank1",
    )
    assert within["boltzmann_weights_valid"] is True
    outside = quality_gates(
        [_entry("conf_0001", 0.8442 + 1e-8, source_conf_id="CONF1")],
        outcome,
        ["conf_0001"],
        protocol="censo-crest",
        policy="rank1",
    )
    assert outside["boltzmann_weights_valid"] is False


def test_rank1_degraded_gate_only_checks_range() -> None:
    outcome = ProtocolOutcome(
        records=[],
        refined_conf_ids=["CONF1"],
        weight_source="computed",
        sampling={"method": "censo"},
    )
    ok = quality_gates(
        [_entry("conf_0001", 1.0)],
        outcome,
        ["conf_0001"],
        protocol="censo-crest",
        policy="rank1",
    )
    assert ok["boltzmann_weights_valid"] is True
    assert ok["G1"] == "PASS"

    out_of_range = quality_gates(
        [_entry("conf_0001", 1.5)],
        outcome,
        ["conf_0001"],
        protocol="censo-crest",
        policy="rank1",
    )
    assert out_of_range["boltzmann_weights_valid"] is False

    non_finite = quality_gates(
        [_entry("conf_0001", math.inf)],
        outcome,
        ["conf_0001"],
        protocol="censo-crest",
        policy="rank1",
    )
    assert non_finite["boltzmann_weights_valid"] is False


def test_rank1_gate_rejects_entry_without_join_key_in_table_mode() -> None:
    outcome = ProtocolOutcome(
        records=[],
        weight_table={"CONF1": 0.8442},
        weight_source="censo",
        sampling={"method": "censo"},
    )
    orphan = _entry("conf_0001", 0.8442, source_conf_id=None)
    gates = quality_gates([orphan], outcome, ["conf_0001"], protocol="censo-crest", policy="rank1")
    assert gates["boltzmann_weights_valid"] is False


def test_non_rank1_sum_gate_holds_under_explicit_policy() -> None:
    outcome = ProtocolOutcome(records=[], temperature_k=298.15, sampling={"method": "stub"})
    entries = [_entry("conf_0001", 0.7), _entry("conf_0002", 0.2), _entry("conf_0003", 0.1)]
    for policy in ("screen", "cumulative-99", "all"):
        gates = quality_gates(entries, outcome, [], protocol="censo-crest", policy=policy)
        assert gates["boltzmann_weights_valid"] is True
        assert gates["G1"] == "PASS"
