"""Ensemble quality gate: real populations, recomputable weights, completeness,
sensitivity markers (todo 30 / gap G09).

Contract under test:

* ``_select_conformers`` enforces one energy definition per ensemble (no
  per-record G/E fallback, no missing-as-0), records
  preselection/selected/successful populations with per-conformer exclusion
  reasons and final weights (recomputable from the recorded Δ values);
* a cumulative-population gate plus the ``max_conformers`` resource cap
  decide the selected set; cap truncation must surface its uncovered mass;
* every conformer is accepted or rejected WHOLE on required-nuclei
  completeness — never per-atom renormalization (``averaging.py``);
* GIAO failure of a dominant conformer degrades the quality record and the
  report weights equal the weights the averaging actually used;
* leave-one-conformer-out / temperature sensitivity flags a winner that
  wobbles so the conclusion is marked for review.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.nmr.averaging import boltzmann_average_shieldings, incomplete_conformer_ids
from acp.nmr.models import ConformerShielding, NmrConfig
from cccp.utils.constants import HARTREE_TO_KCAL

SPECTRUM = "C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"

SYMBOLS = ["C", "H", "H", "H", "H"]

_BOLTZMANN_R = 0.001987204259  # kcal/(mol·K) — same constant as the workflow


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _structure(index: int, symbols: list[str] | None = None) -> Structure:
    syms = list(symbols or SYMBOLS)
    return Structure(
        id=f"cand{index}",
        charge=0,
        multiplicity=1,
        symbols=syms,
        coordinates=np.array([(index, 0.1 * i, 0.0) for i in range(len(syms))], dtype=float),
    )


def _record(
    index: int,
    *,
    free_energy_hartree: float | None = None,
    energy_hartree: float | None = None,
    symbols: list[str] | None = None,
) -> StructureRecord:
    return StructureRecord(
        structure=_structure(index, symbols),
        energy_hartree=energy_hartree,
        free_energy_hartree=free_energy_hartree,
        weight=1.0,
    )


def _shieldings(
    carbon: float = 40.0, hydrogens: tuple[float, float, float, float] = (4.0, 3.0, 1.0, 0.0)
) -> dict[int, dict[str, object]]:
    out: dict[int, dict[str, object]] = {
        0: {"symbol": "C", "isotropic": 188.452125 - carbon},
    }
    for offset, value in enumerate(hydrogens):
        out[1 + offset] = {"symbol": "H", "isotropic": 32.1243166667 - value}
    return out


def _failed_result(message: str = "mock GIAO failure") -> Any:
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import TaskResult

    return TaskResult(
        task=TaskKind.NMR_SHIELDING,
        status="failed",
        complete=False,
        errors=(message,),
    )


def _completed_result(shieldings: dict[int, dict[str, object]]) -> Any:
    from tests.test_acp_workflows_nmr import _shielding_result

    return _shielding_result(shieldings)


def _run_pipeline(
    tmp_path: Path,
    records: list[StructureRecord],
    side_effect: list[Any],
    spectrum: str = SPECTRUM,
    **overrides: Any,
) -> Any:
    structure = records[0].structure
    ensemble = StructureEnsemble(records=records)
    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch("acp.workflows.nmr.run_nmr_shielding", side_effect=side_effect),
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader

        from acp.workflows.nmr import run_nmr_analysis

        kwargs: dict[str, Any] = dict(
            input_sources=["CCO"],
            spectrum=spectrum,
            output_dir=str(tmp_path),
            skip_conformers=False,
            prebuilt_ensembles=[ensemble],
            error_model="placeholder-student-t",
        )
        kwargs.update(overrides)
        return run_nmr_analysis(**kwargs)


def _report_dict(result: Any) -> dict[str, Any]:
    return json.loads(Path(result.metadata["report_json"]).read_text(encoding="utf-8"))


def _expected_weights(deltas: list[float], temperature: float = 298.15) -> list[float]:
    kt = _BOLTZMANN_R * temperature / HARTREE_TO_KCAL
    exps = [math.exp(-d / kt) for d in deltas]
    total = sum(exps)
    return [e / total for e in exps]


# ---------------------------------------------------------------------------
# (1) workflow: dominant-conformer failure degrades quality
# ---------------------------------------------------------------------------


def test_dominant_conformer_failure_degrades_quality(tmp_path: Path) -> None:
    """A within-window dominant conformer whose GIAO fails must degrade the
    quality record — the run must never read like a normal conclusion."""
    records = [
        _record(0, free_energy_hartree=-1.0, energy_hartree=-1.0),
        _record(1, free_energy_hartree=-0.998, energy_hartree=-0.998),
    ]
    result = _run_pipeline(
        tmp_path,
        records,
        [_failed_result(), _completed_result(_shieldings())],
    )
    assert result.status == "completed", result.error

    data = _report_dict(result)
    cand = data["candidates"][0]
    quality = cand["ensemble_quality"]
    assert quality is not None, "workflow candidate carries no ensemble_quality record"

    # populations: both selected, only the tail succeeded
    weights = _expected_weights([0.0, 0.002])
    assert quality["n_discovered"] == 2
    assert quality["n_selected"] == 2
    assert quality["n_successful"] == 1
    assert quality["selected_population"] == pytest.approx(1.0, abs=1e-6)
    assert quality["successful_population"] == pytest.approx(weights[1], abs=1e-6)

    # the dominant conformer's failure is visible with its weight and reason
    by_id = {c["conformer_id"]: c for c in quality["conformers"]}
    failed_entry = by_id["conf_000"]
    assert failed_entry["exclusion_reason"] == "giao_failed"
    assert failed_entry["selected_weight"] == pytest.approx(weights[0], abs=1e-6)
    assert failed_entry["final_weight"] is None
    survived = by_id["conf_001"]
    assert survived["exclusion_reason"] is None
    assert survived["final_weight"] == pytest.approx(1.0, abs=1e-6)

    # quality degradation is explicit (dominant weight >= 0.5 failed)
    assert quality["quality_status"] == "degraded"
    assert "dominant_conformer_failed" in quality["quality_flags"]
    assert "successful_population_below_target" in quality["quality_flags"]

    # report weights == actual algorithm input: one surviving conformer is
    # fed with weight 1.0 (not the pre-GIAO 0.107), and coverage exposes the
    # pre-GIAO selected set.
    conformers = cand["conformers"]
    assert [(c["id"], c["boltzmann_weight"]) for c in conformers] == [("conf_001", 1.0)]
    cov = cand["coverage"]
    assert cov["n_conformers_selected"] == 2
    assert cov["selected_population"] == pytest.approx(1.0, abs=1e-6)
    assert cov["n_conformers_successful"] == 1
    assert cov["successful_population"] == pytest.approx(weights[1], abs=1e-6)


def test_low_weight_tail_failure_vs_high_weight_failure(tmp_path: Path) -> None:
    """A tail failure (w≈0.04) and a dominant failure (w≈0.89) must be
    distinguishable: both are recorded, only the dominant one degrades."""
    tail_records = [
        _record(0, free_energy_hartree=-1.0, energy_hartree=-1.0),
        _record(1, free_energy_hartree=-0.997, energy_hartree=-0.997),
    ]
    tail_result = _run_pipeline(
        tmp_path / "tail",
        tail_records,
        [_completed_result(_shieldings()), _failed_result()],
    )
    assert tail_result.status == "completed", tail_result.error
    tail_quality = _report_dict(tail_result)["candidates"][0]["ensemble_quality"]
    tail_weights = _expected_weights([0.0, 0.003])
    assert tail_quality["quality_status"] == "ok"
    assert "tail_conformer_failed" in tail_quality["quality_flags"]
    assert "dominant_conformer_failed" not in tail_quality["quality_flags"]
    assert tail_quality["successful_population"] == pytest.approx(tail_weights[0], abs=1e-6)

    dominant_records = [
        _record(0, free_energy_hartree=-1.0, energy_hartree=-1.0),
        _record(1, free_energy_hartree=-0.998, energy_hartree=-0.998),
    ]
    dominant_result = _run_pipeline(
        tmp_path / "dominant",
        dominant_records,
        [_failed_result(), _completed_result(_shieldings())],
    )
    assert dominant_result.status == "completed", dominant_result.error
    dominant_quality = _report_dict(dominant_result)["candidates"][0]["ensemble_quality"]
    assert dominant_quality["quality_status"] == "degraded"
    assert "dominant_conformer_failed" in dominant_quality["quality_flags"]
    # the raw populations make the two cases numerically comparable
    assert dominant_quality["successful_population"] < tail_quality["successful_population"]


def test_incomplete_shieldings_drop_whole_conformer_and_flag(tmp_path: Path) -> None:
    """A conformer with incomplete required-nuclei shieldings is excluded
    wholesale (reason ``incomplete_shieldings``), never per-atom averaged."""
    records = [
        _record(0, free_energy_hartree=-1.0, energy_hartree=-1.0),
        _record(1, free_energy_hartree=-0.998, energy_hartree=-0.998),
    ]
    incomplete = _shieldings()
    del incomplete[4]  # H4 missing from the first (dominant) conformer
    result = _run_pipeline(
        tmp_path,
        records,
        [_completed_result(incomplete), _completed_result(_shieldings())],
    )
    assert result.status == "completed", result.error
    data = _report_dict(result)
    quality = data["candidates"][0]["ensemble_quality"]
    by_id = {c["conformer_id"]: c for c in quality["conformers"]}
    assert by_id["conf_000"]["exclusion_reason"] == "incomplete_shieldings"
    assert quality["quality_status"] == "degraded"
    assert "dominant_conformer_failed" in quality["quality_flags"]


def test_all_incomplete_conformers_fail_the_run(tmp_path: Path) -> None:
    """When every conformer's shieldings are incomplete there is no valid
    ensemble to average — the run must fail instead of reporting a number."""
    records = [_record(0, free_energy_hartree=-1.0, energy_hartree=-1.0)]
    incomplete = _shieldings()
    del incomplete[4]
    result = _run_pipeline(tmp_path, records, [_completed_result(incomplete)])
    assert result.status == "failed"
    assert "complete" in (result.error or "")


# ---------------------------------------------------------------------------
# (2) selection: unified energy definition / populations / recomputable weights
# ---------------------------------------------------------------------------


def test_unified_energy_definition_no_g_e_mixing() -> None:
    """Records carrying both G and E are compared on ONE definition; the
    other field is ignored, never mixed per record."""
    from acp.workflows.nmr import _select_conformers

    ensemble = StructureEnsemble(
        records=[
            # A: G=-0.99 would win if mixed with the E column; its E is -1.000
            _record(0, free_energy_hartree=-0.99, energy_hartree=-1.000),
            _record(1, free_energy_hartree=None, energy_hartree=-1.004),
            _record(2, free_energy_hartree=None, energy_hartree=-1.002),
        ]
    )
    selection = _select_conformers(ensemble, NmrConfig())
    assert selection.definition == "energy"
    energies = {p.conformer_id: p.energy_hartree for p in selection.populations}
    assert energies == {"conf_000": -1.000, "conf_001": -1.004, "conf_002": -1.002}
    # E is the ensemble minimum for every record — G never entered the sort
    deltas = {p.conformer_id: p.delta_hartree for p in selection.populations}
    assert deltas["conf_001"] == pytest.approx(0.0)
    assert deltas["conf_000"] == pytest.approx(0.004)
    assert deltas["conf_002"] == pytest.approx(0.002)


def test_missing_energy_is_excluded_not_zeroed() -> None:
    """A record without the unified definition's energy is excluded with a
    reason; it is never assigned a 0.0 energy/weight."""
    from acp.workflows.nmr import _select_conformers

    ensemble = StructureEnsemble(
        records=[
            _record(0, free_energy_hartree=-1.0, energy_hartree=-1.0),
            _record(1, free_energy_hartree=None, energy_hartree=-1.002),
            _record(2, free_energy_hartree=None, energy_hartree=None),
        ]
    )
    selection = _select_conformers(ensemble, NmrConfig())
    # E covers every energy-bearing record (A also has G, but G is not
    # complete) → the ensemble is compared on E
    assert selection.definition == "energy"
    by_id = {p.conformer_id: p for p in selection.populations}
    missing = by_id["conf_002"]  # neither G nor E
    assert missing.exclusion_reason == "missing_energy"
    assert missing.energy_hartree is None
    assert missing.raw_weight is None
    assert missing.selected_weight is None
    assert selection.preselection_population == pytest.approx(2 / 3)
    assert {s.conformer_id for s in selection.selected} == {"conf_000", "conf_001"}


def test_incomplete_g_e_columns_pick_the_more_complete_definition() -> None:
    """A G-only record + E-only records cannot be compared on one definition
    (G available for 1/3, E for 2/3): E wins and the G-only record is
    excluded with ``missing_energy``."""
    from acp.workflows.nmr import _select_conformers

    ensemble = StructureEnsemble(
        records=[
            _record(0, free_energy_hartree=-1.0, energy_hartree=None),
            _record(1, free_energy_hartree=None, energy_hartree=-1.002),
            _record(2, free_energy_hartree=None, energy_hartree=-1.001),
        ]
    )
    selection = _select_conformers(ensemble, NmrConfig())
    assert selection.definition == "energy"
    by_id = {p.conformer_id: p for p in selection.populations}
    assert by_id["conf_000"].exclusion_reason == "missing_energy"
    assert {s.conformer_id for s in selection.selected} == {"conf_001", "conf_002"}


def test_all_missing_energy_fails_closed() -> None:
    """No usable energy anywhere → no selection. Equal weights on top of a
    fabricated 0.0 energy is the defect this gate removes."""
    from acp.workflows.nmr import _select_conformers

    ensemble = StructureEnsemble(
        records=[
            _record(0, free_energy_hartree=None, energy_hartree=None),
            _record(1, free_energy_hartree=None, energy_hartree=None),
        ]
    )
    selection = _select_conformers(ensemble, NmrConfig())
    assert selection.definition == "none"
    assert selection.selected == ()
    assert selection.preselection_population == 0.0
    assert all(p.exclusion_reason == "missing_energy" for p in selection.populations)


def test_weights_are_recomputable_from_recorded_deltas() -> None:
    """Final weights are a pure function of the recorded energies and
    temperature; the record round-trips as JSON."""
    from acp.workflows.nmr import _select_conformers

    cfg = NmrConfig(boltzmann_temp=298.15)
    ensemble = StructureEnsemble(
        records=[
            _record(0, free_energy_hartree=-1.0, energy_hartree=-1.0),
            _record(1, free_energy_hartree=-0.999, energy_hartree=-0.999),
            _record(2, free_energy_hartree=-0.997, energy_hartree=-0.997),
        ]
    )
    selection = _select_conformers(ensemble, cfg)
    by_id = {p.conformer_id: p for p in selection.populations}
    deltas = [by_id[f"conf_{i:03d}"].delta_hartree for i in range(3)]
    assert all(d is not None for d in deltas)
    expected_raw = _expected_weights([float(d) for d in deltas if d is not None])
    for i in range(3):
        entry = by_id[f"conf_{i:03d}"]
        assert entry.raw_weight == pytest.approx(expected_raw[i], rel=1e-12)
    total_selected = sum(
        p.raw_weight for p in selection.populations if p.selected_weight is not None
    )
    for p in selection.populations:
        if p.selected_weight is not None:
            assert p.selected_weight == pytest.approx(p.raw_weight / total_selected, rel=1e-12)
    assert sum(
        p.selected_weight for p in selection.populations if p.selected_weight is not None
    ) == pytest.approx(1.0)

    # JSON-safe record, frozen dataclass
    payload = json.loads(json.dumps([p.as_dict() for p in selection.populations]))
    assert payload[1]["raw_weight"] == pytest.approx(round(expected_raw[1], 6))
    with pytest.raises(AttributeError):
        selection.populations[0].raw_weight = 0.0  # type: ignore[misc]


def test_cumulative_population_gate_cuts_the_low_weight_tail() -> None:
    """The cumulative-population target drops a sub-1% tail before GIAO;
    the dropped mass is recorded (engineering target, not science)."""
    from acp.workflows.nmr import _select_conformers

    ensemble = StructureEnsemble(
        records=[
            _record(0, free_energy_hartree=-1.0, energy_hartree=-1.0),
            _record(1, free_energy_hartree=-0.9956, energy_hartree=-0.9956),  # Δ≈2.76 kcal
        ]
    )
    selection = _select_conformers(ensemble, NmrConfig())
    assert [s.conformer_id for s in selection.selected] == ["conf_000"]
    by_id = {p.conformer_id: p for p in selection.populations}
    assert by_id["conf_000"].selected_weight == pytest.approx(1.0)
    assert by_id["conf_001"].exclusion_reason == "population_threshold"
    assert selection.population_gate_dropped == pytest.approx(by_id["conf_001"].raw_weight)
    assert selection.population_gate_dropped < 0.01
    assert "population_gate_applied" in selection.flags


def test_resource_cap_records_uncovered_population() -> None:
    """The hard ``max_conformers`` cap truncates a flat population and its
    uncovered mass is recorded loudly."""
    from acp.workflows.nmr import _select_conformers

    ensemble = StructureEnsemble(
        records=[
            _record(0, free_energy_hartree=-1.0, energy_hartree=-1.0),
            _record(1, free_energy_hartree=-1.0, energy_hartree=-1.0),
            _record(2, free_energy_hartree=-1.0, energy_hartree=-1.0),
        ]
    )
    selection = _select_conformers(ensemble, NmrConfig(max_conformers=2))
    assert len(selection.selected) == 2
    assert selection.uncovered_population == pytest.approx(1 / 3, abs=1e-6)
    assert "resource_cap_truncated" in selection.flags
    by_id = {p.conformer_id: p for p in selection.populations}
    assert by_id["conf_002"].exclusion_reason == "resource_cap"
    assert by_id["conf_002"].selected_weight is None


def test_window_exclusion_reason_and_preselection_population() -> None:
    """Records outside the window keep their measured energy and a typed
    reason; preselection exposes how much of the discovered ensemble had a
    usable energy."""
    from acp.workflows.nmr import _select_conformers

    ensemble = StructureEnsemble(
        records=[
            _record(0, free_energy_hartree=-1.0, energy_hartree=-1.0),
            _record(1, free_energy_hartree=-0.99, energy_hartree=-0.99),  # 6.3 kcal
        ]
    )
    selection = _select_conformers(ensemble, NmrConfig())
    by_id = {p.conformer_id: p for p in selection.populations}
    assert by_id["conf_001"].exclusion_reason == "outside_energy_window"
    assert by_id["conf_001"].energy_hartree == pytest.approx(-0.99)
    assert by_id["conf_001"].raw_weight is not None  # measured, not selected
    assert selection.preselection_population == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# (3) averaging: whole-conformer completeness (no per-atom renormalization)
# ---------------------------------------------------------------------------


def _shift_for(iso: float, nucleus: str, cfg: NmrConfig) -> float:
    ref = cfg.tms_for(nucleus)
    assert ref is not None
    return (ref - iso) / (1.0 - ref / 1e6)


def test_incomplete_conformer_ids_uses_required_nuclei() -> None:
    cfg = NmrConfig()
    complete = ConformerShielding("c1", 0.5, _shieldings())
    missing_h = ConformerShielding(
        "c2", 0.5, {0: {"symbol": "C", "isotropic": 150.0}, 1: {"symbol": "H", "isotropic": 28.0}}
    )
    assert incomplete_conformer_ids([complete], SYMBOLS, cfg) == []
    assert incomplete_conformer_ids([complete, missing_h], SYMBOLS, cfg) == ["c2"]

    # an element with NO configured nucleus is not "required"
    symbols_n = ["C", "H", "N"]
    with_n = {
        0: {"symbol": "C", "isotropic": 150.0},
        1: {"symbol": "H", "isotropic": 28.0},
    }
    conf_n = ConformerShielding("c3", 1.0, with_n)
    assert incomplete_conformer_ids([conf_n], symbols_n, NmrConfig(nuclei=("1H", "13C"))) == []
    assert incomplete_conformer_ids(
        [conf_n], symbols_n, NmrConfig(nuclei=("1H", "13C", "15N"))
    ) == ["c3"]


def test_averaging_drops_incomplete_conformer_wholesale() -> None:
    """A conformer missing one required atom is excluded for EVERY atom —
    the old behavior renormalized per atom, so different atoms silently
    averaged different conformer subsets."""
    cfg = NmrConfig()
    complete = ConformerShielding(
        "c1", 0.25, _shieldings(carbon=40.0, hydrogens=(4.0, 3.0, 1.0, 0.0))
    )
    incomplete_payload = _shieldings(carbon=30.0, hydrogens=(30.0, 30.0, 30.0, 30.0))
    del incomplete_payload[4]
    incomplete = ConformerShielding("c2", 0.75, incomplete_payload)

    shifts = boltzmann_average_shieldings([complete, incomplete], SYMBOLS, cfg)
    by_index = {s.atom_index: s for s in shifts}
    assert set(by_index) == {0, 1, 2, 3, 4}
    # every atom comes from the complete conformer only
    assert by_index[0].shift_ppm == pytest.approx(_shift_for(188.452125 - 40.0, "13C", cfg))
    for atom_index, h_value in zip(range(1, 5), (4.0, 3.0, 1.0, 0.0), strict=True):
        assert by_index[atom_index].shift_ppm == pytest.approx(
            _shift_for(32.1243166667 - h_value, "1H", cfg)
        )


def test_averaging_renormalizes_weights_once_over_complete_set() -> None:
    cfg = NmrConfig()
    c1 = ConformerShielding("c1", 1.0, _shieldings(carbon=40.0))
    c2 = ConformerShielding("c2", 1.0, _shieldings(carbon=50.0))
    shifts = boltzmann_average_shieldings([c1, c2], SYMBOLS, cfg)
    by_index = {s.atom_index: s for s in shifts}
    # equal weights → mean shielding (40 and 50 ppm → mean 45)
    mean_iso_c = (188.452125 - 40.0 + 188.452125 - 50.0) / 2
    assert by_index[0].shift_ppm == pytest.approx(_shift_for(mean_iso_c, "13C", cfg))


# ---------------------------------------------------------------------------
# (4) sensitivity markers
# ---------------------------------------------------------------------------


def _sensitivity_inputs() -> tuple[
    list[Structure], list[Any], list[list[ConformerShielding]], Any, NmrConfig
]:
    from acp.nmr.io import parse_experimental_nmr
    from acp.workflows.nmr import _analyze_candidate

    experiment = parse_experimental_nmr(SPECTRUM)
    cfg = NmrConfig()
    structures: list[Structure] = []
    shieldings_by_candidate: list[list[ConformerShielding]] = []
    results: list[Any] = []

    # candidate A: one near-perfect conformer + one poor tail conformer
    good_a = _shieldings(carbon=40.0, hydrogens=(4.0, 3.0, 1.0, 0.0))
    bad_a = _shieldings(carbon=34.0, hydrogens=(8.0, 2.0, -2.0, -4.0))
    struct_a = _structure(0)
    confs_a = [
        ConformerShielding("a0", 0.9, good_a, delta_hartree=0.0),
        ConformerShielding("a1", 0.1, bad_a, delta_hartree=0.004),
    ]
    structures.append(struct_a)
    shieldings_by_candidate.append(confs_a)
    results.append(_analyze_candidate(0, struct_a, confs_a, experiment, cfg))

    # candidate B: flat mediocre pair
    mid_b = _shieldings(carbon=40.6, hydrogens=(4.5, 3.4, 1.4, 0.4))
    struct_b = _structure(1)
    confs_b = [
        ConformerShielding("b0", 0.5, mid_b, delta_hartree=0.0),
        ConformerShielding("b1", 0.5, mid_b, delta_hartree=0.001),
    ]
    structures.append(struct_b)
    shieldings_by_candidate.append(confs_b)
    results.append(_analyze_candidate(1, struct_b, confs_b, experiment, cfg))

    return structures, results, shieldings_by_candidate, experiment, cfg


def _sensitivity_robust_inputs() -> tuple[
    list[Structure], list[Any], list[list[ConformerShielding]], Any, NmrConfig
]:
    from acp.nmr.io import parse_experimental_nmr
    from acp.workflows.nmr import _analyze_candidate

    experiment = parse_experimental_nmr(SPECTRUM)
    cfg = NmrConfig()
    struct_a = _structure(0)
    confs_a = [
        ConformerShielding(
            "a0", 0.9, _shieldings(carbon=40.0, hydrogens=(4.0, 3.0, 1.0, 0.0)), delta_hartree=0.0
        ),
        ConformerShielding(
            "a1",
            0.1,
            _shieldings(carbon=34.0, hydrogens=(8.0, 2.0, -2.0, -4.0)),
            delta_hartree=0.004,
        ),
    ]
    struct_b = _structure(1)
    bad_b = _shieldings(carbon=25.0, hydrogens=(12.0, 11.0, 9.0, 8.0))
    confs_b = [
        ConformerShielding("b0", 0.5, bad_b, delta_hartree=0.0),
        ConformerShielding("b1", 0.5, bad_b, delta_hartree=0.001),
    ]
    results = [
        _analyze_candidate(0, struct_a, confs_a, experiment, cfg),
        _analyze_candidate(1, struct_b, confs_b, experiment, cfg),
    ]
    return [struct_a, struct_b], results, [confs_a, confs_b], experiment, cfg


def test_sensitivity_report_shape() -> None:
    """Sensitivity is always surfaced with a stable, JSON-safe shape."""
    from acp.nmr.error_model import GoodmanErrorModel
    from acp.workflows.nmr import _sensitivity_analysis

    structures, results, shieldings, experiment, cfg = _sensitivity_inputs()
    report = _sensitivity_analysis(
        structures, results, shieldings, experiment, cfg, GoodmanErrorModel()
    )
    assert report["status"] == "computed"
    assert set(report) >= {"flags", "requires_review", "leave_one_out", "temperature"}
    assert report["leave_one_out"]["n_cases"] >= 1
    assert isinstance(report["flags"], list)
    assert report["requires_review"] is (len(report["flags"]) > 0)
    json.dumps(report)


def test_sensitivity_robust_winner_no_review_flag() -> None:
    """A clearly separated winner survives both axes without a review flag."""
    from acp.nmr.error_model import GoodmanErrorModel
    from acp.workflows.nmr import _sensitivity_analysis

    structures, results, shieldings, experiment, cfg = _sensitivity_robust_inputs()
    report = _sensitivity_analysis(
        structures, results, shieldings, experiment, cfg, GoodmanErrorModel()
    )
    assert report["status"] == "computed"
    assert report["leave_one_out"]["n_flips"] == 0
    assert report["temperature"]["status"] == "computed"
    assert all(not entry["flips"] for entry in report["temperature"]["results"])
    assert report["flags"] == []
    assert report["requires_review"] is False


def test_sensitivity_leave_one_out_flip_marks_review() -> None:
    """Removing the conformer that carries a candidate's winner ranking must
    flip the winner and set the review marker."""
    from acp.workflows.nmr import _sensitivity_analysis

    structures, results, shieldings, experiment, cfg = _sensitivity_inputs()
    from acp.nmr.error_model import GoodmanErrorModel

    em = GoodmanErrorModel()
    report = _sensitivity_analysis(structures, results, shieldings, experiment, cfg, em)
    cases = report["leave_one_out"]["cases"]
    flips = [case for case in cases if case["flips"]]
    assert flips, f"expected a leave-one-out winner flip, got cases={cases}"
    assert "leave_one_out_winner_flip" in report["flags"]
    assert report["requires_review"] is True


def test_sensitivity_temperature_wobble_marks_review() -> None:
    """A winner that changes when the Boltzmann temperature moves ±10% is
    marked for review instead of being reported as a stable conclusion."""
    from acp.workflows.nmr import _sensitivity_analysis

    structures, results, shieldings, experiment, cfg = _sensitivity_inputs()
    from acp.nmr.error_model import GoodmanErrorModel

    report = _sensitivity_analysis(
        structures, results, shieldings, experiment, cfg, GoodmanErrorModel()
    )
    temperature = report["temperature"]
    assert temperature["status"] == "computed", temperature
    assert temperature["range_k"] == pytest.approx(
        [cfg.boltzmann_temp * 0.9, cfg.boltzmann_temp * 1.1]
    )
    assert any(entry["flips"] for entry in temperature["results"]), temperature
    assert "temperature_winner_flip" in report["flags"]


def test_workflow_report_carries_sensitivity_block(tmp_path: Path) -> None:
    """The sensitivity verdict reaches nmr_report.json + nmr_summary.json."""
    records = [
        _record(0, free_energy_hartree=-1.0, energy_hartree=-1.0),
        _record(1, free_energy_hartree=-0.997, energy_hartree=-0.997),
    ]
    result = _run_pipeline(
        tmp_path,
        records,
        [_completed_result(_shieldings()), _completed_result(_shieldings(carbon=35.0))],
    )
    assert result.status == "completed", result.error
    data = _report_dict(result)
    sensitivity = data["sensitivity"]
    assert sensitivity is not None
    assert sensitivity["status"] in {"computed", "skipped"}
    assert "requires_review" in sensitivity
    summary = json.loads(
        (Path(result.metadata["report_json"]).parent / "nmr_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["sensitivity"] == sensitivity
