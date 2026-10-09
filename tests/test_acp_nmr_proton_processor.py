"""Tests for the proton-spectrum multiplet processor (todo 44 / G10).

Layers (three-state rule, plan todo 49):

* **synthetic hand-built spectra** (binding) — a faithful ethyl + methoxy +
  weak-OH fixture exercises BIC-driven multiplet grouping, methyl (3H) and
  total-H constraints, overlap uncertainty and the peak-capacity export;
* **synthetic-but-faithful end-to-end** (binding when nmrglue is installed) —
  a synthetic Bruker FID (triplet/quartet/methoxy/OH) goes through the real
  ``process_bruker_experiment`` chain and then the processor;
* **real instrument data** — ``NOT_VERIFIED``: no real proton Bruker dataset
  ships in this repo (searched ``tests/fixtures/``); the skip reason carries
  the literal token and the layer must never be counted as a pass.

The fixture is deliberately designed so the *old* per-line midpoint heuristic
fails the constraints: per-line multiplicities for the 1:2:1 triplet are
``[1, 2, 1]`` (sum 4 != 3H) and for the 1:3:3:1 quartet ``[1, 3, 3, 1]``
(sum 8 != 2H), while the processor's multiplets must sum to the structure's
total H count exactly.
"""

from __future__ import annotations

import dataclasses
import json
import zipfile
from pathlib import Path

import pytest

from acp.nmr.models import (
    AcquisitionSpectrum,
    ExperimentalPeak,
    ProcessedSpectrum,
    ProcessingAssessment,
    SpectralLine,
)
from acp.nmr.proton_processor import (
    NUCLEUS,
    PROCESSOR_ID,
    ProtonAnnotation,
    ProtonProcessorOptions,
    compare_proton_grouping,
    process_proton_spectrum,
)
from tests.conftest import NOT_VERIFIED

_FREQUENCY_MHZ = 500.13
_J_HZ = 7.2
_J_PPM = _J_HZ / _FREQUENCY_MHZ

# Fixture line source indices (insertion order).
_TRIPLET_INDICES = (0, 1, 2)
_QUARTET_INDICES = (3, 4, 5, 6)
_METHOXY_INDEX = 7
_OH_INDEX = 8

_TRIPLET_POSITIONS = (1.2 - _J_PPM, 1.2, 1.2 + _J_PPM)
_QUARTET_POSITIONS = tuple(3.59 + offset * _J_PPM for offset in (-1.5, -0.5, 0.5, 1.5))
_METHOXY_POSITION = 3.59
_OH_POSITION = 4.8


def _line(index: int, position_ppm: float, intensity: float, integral: float) -> SpectralLine:
    return SpectralLine(
        position_ppm=position_ppm,
        intensity=intensity,
        width_hz=1.5,
        integral=integral,
        index=index,
    )


def _ethyl_lines(*, integral_scale: float = 1.0) -> list[SpectralLine]:
    """Faithful ethyl + methoxy + weak OH line list (multiplet totals ∝ H)."""
    lines: list[SpectralLine] = []
    for index, (position, intensity, integral) in enumerate(
        zip(_TRIPLET_POSITIONS, (1.0, 2.0, 1.0), (0.75, 1.5, 0.75))
    ):
        lines.append(_line(index, position, intensity, integral * integral_scale))
    for offset, (position, intensity, integral) in enumerate(
        zip(_QUARTET_POSITIONS, (1.0, 3.0, 3.0, 1.0), (0.25, 0.75, 0.75, 0.25))
    ):
        lines.append(_line(3 + offset, position, intensity, integral * integral_scale))
    lines.append(_line(_METHOXY_INDEX, _METHOXY_POSITION, 3.0, 3.0 * integral_scale))
    lines.append(_line(_OH_INDEX, _OH_POSITION, 0.3, 1.0 * integral_scale))
    return lines


def _ethyl_peaks(*, index_offset: int | None = None) -> list[ExperimentalPeak]:
    """Naive per-line multiplicities (the old midpoint heuristic's view)."""
    multiplicities = (1, 2, 1, 1, 3, 3, 1, 3, 1)
    peaks = []
    for position, multiplicity in zip(
        [*_TRIPLET_POSITIONS, *_QUARTET_POSITIONS, _METHOXY_POSITION, _OH_POSITION],
        multiplicities,
    ):
        index = None if index_offset is None else index_offset + len(peaks)
        peaks.append(
            ExperimentalPeak(
                shift_ppm=position,
                element="H",
                multiplicity=multiplicity,
                index=index,
            )
        )
    return peaks


def _spectrum(
    lines: list[SpectralLine],
    peaks: list[ExperimentalPeak],
    *,
    noise: float = 0.05,
    element: str = "H",
    nucleus: str = "1H",
    assessment: ProcessingAssessment | None = None,
    frequency_mhz: float | None = _FREQUENCY_MHZ,
) -> ProcessedSpectrum:
    return ProcessedSpectrum(
        nucleus=nucleus,
        element=element,
        peaks=peaks,
        noise=noise,
        source_dir="synthetic",
        acquisition=AcquisitionSpectrum(
            spectrometer="synthetic",
            nucleus=nucleus,
            frequency_mhz=frequency_mhz,
        ),
        lines=lines,
        assessment=assessment,
    )


def _ethyl_spectrum(**kwargs: object) -> ProcessedSpectrum:
    return _spectrum(_ethyl_lines(), _ethyl_peaks(), **kwargs)  # type: ignore[arg-type]


def _multiplet_with(multiplets: tuple, line_index: int):
    matches = [multiplet for multiplet in multiplets if line_index in multiplet.line_indices]
    assert len(matches) == 1, f"line {line_index} is in {len(matches)} multiplets"
    return matches[0]


# ---------------------------------------------------------------------------
# Registry / nucleus contract (todo 45 handoff)
# ---------------------------------------------------------------------------


def test_module_exposes_registry_contract() -> None:
    assert NUCLEUS == "H"
    assert PROCESSOR_ID == "proton_processor_v1"
    assert callable(process_proton_spectrum)


# ---------------------------------------------------------------------------
# Naive midpoint heuristic fails the constraints (TDD premise)
# ---------------------------------------------------------------------------


def test_naive_per_line_multiplicity_violates_methyl_and_total_h() -> None:
    spectrum = _ethyl_spectrum()
    naive_total = sum(peak.multiplicity for peak in spectrum.peaks)
    assert naive_total == 16  # 1:2:1 + 1:3:3:1 + 3 + 1 per-line heuristic
    result = process_proton_spectrum(spectrum, ProtonProcessorOptions(total_hydrogens=9))
    assert sum(m.atom_count for m in result.multiplets) == 9
    triplet = _multiplet_with(result.multiplets, 0)
    assert triplet.methyl is True
    assert triplet.atom_count == 3  # never 4 from summing naive per-line values


def test_regular_triplet_groups_into_one_multiplet() -> None:
    result = process_proton_spectrum(_ethyl_spectrum())
    triplet = _multiplet_with(result.multiplets, 0)
    assert triplet.line_indices == _TRIPLET_INDICES
    assert triplet.spacing_ppm == pytest.approx(_J_PPM, abs=2e-4)
    assert triplet.coupling_hz == pytest.approx(_J_HZ, abs=0.2)


def test_irregular_line_spacing_is_not_forced_into_one_multiplet() -> None:
    lines = [
        _line(0, 1.00, 1.0, 1.0),
        _line(1, 1.02, 1.0, 1.0),
        _line(2, 1.05, 1.0, 1.0),
    ]
    peaks = [ExperimentalPeak(shift_ppm=line.position_ppm, element="H") for line in lines]
    result = process_proton_spectrum(
        _spectrum(lines, peaks), ProtonProcessorOptions(total_hydrogens=3)
    )
    assert len(result.multiplets) == 2
    groups = {multiplet.line_indices for multiplet in result.multiplets}
    assert groups in [{(0, 1), (2,)}, {(0,), (1, 2)}]  # not forced into one multiplet


def test_noise_level_spacing_perturbation_stays_one_multiplet() -> None:
    lines = [
        _line(0, 1.1856, 1.0, 1.0),
        _line(1, 1.2, 2.0, 1.0),
        _line(2, 1.2146, 1.0, 1.0),
    ]
    peaks = [ExperimentalPeak(shift_ppm=line.position_ppm, element="H") for line in lines]
    result = process_proton_spectrum(_spectrum(lines, peaks))
    assert len(result.multiplets) == 1


# ---------------------------------------------------------------------------
# Ethyl + methoxy + weak OH fixture: grouping, methyl, total-H, overlap
# ---------------------------------------------------------------------------


def test_ethyl_fixture_multiplet_line_sets() -> None:
    result = process_proton_spectrum(_ethyl_spectrum(), ProtonProcessorOptions(total_hydrogens=9))
    triplet = _multiplet_with(result.multiplets, 0)
    quartet = _multiplet_with(result.multiplets, 3)
    methoxy = _multiplet_with(result.multiplets, _METHOXY_INDEX)
    oh = _multiplet_with(result.multiplets, _OH_INDEX)
    assert triplet.line_indices == _TRIPLET_INDICES
    assert quartet.line_indices == _QUARTET_INDICES  # AP-extracted across the methoxy
    assert methoxy.line_indices == (_METHOXY_INDEX,)
    assert oh.line_indices == (_OH_INDEX,)
    assert quartet.spacing_ppm == pytest.approx(_J_PPM, abs=2e-4)
    assert quartet.center_ppm == pytest.approx(3.59, abs=1e-3)
    assert methoxy.center_ppm == pytest.approx(3.59, abs=1e-3)


def test_ethyl_fixture_methyl_and_total_h_constraints() -> None:
    result = process_proton_spectrum(_ethyl_spectrum(), ProtonProcessorOptions(total_hydrogens=9))
    triplet = _multiplet_with(result.multiplets, 0)
    quartet = _multiplet_with(result.multiplets, 3)
    methoxy = _multiplet_with(result.multiplets, _METHOXY_INDEX)
    oh = _multiplet_with(result.multiplets, _OH_INDEX)
    assert (triplet.methyl, triplet.atom_count) == (True, 3)
    assert (methoxy.methyl, methoxy.atom_count) == (True, 3)
    assert (quartet.methyl, quartet.atom_count) == (False, 2)
    assert (oh.methyl, oh.atom_count) == (False, 1)
    assert sum(m.atom_count for m in result.multiplets) == 9
    report = result.constraints
    assert report.total_hydrogens == 9
    assert report.total_constraint_applied is True
    assert report.total_hydrogens_resolved == 9
    assert set(report.methyl_multiplet_ids) == {triplet.multiplet_id, methoxy.multiplet_id}
    assert report.conflicts == ()


def test_total_h_constraint_scales_arbitrary_integral_units() -> None:
    spectrum = _spectrum(_ethyl_lines(integral_scale=10.0), _ethyl_peaks(), noise=0.05)
    result = process_proton_spectrum(spectrum, ProtonProcessorOptions(total_hydrogens=9))
    by_index = {m.line_indices: m for m in result.multiplets}
    assert by_index[_TRIPLET_INDICES].atom_count == 3
    assert by_index[_QUARTET_INDICES].atom_count == 2
    assert by_index[(_METHOXY_INDEX,)].atom_count == 3
    assert by_index[(_OH_INDEX,)].atom_count == 1
    assert sum(m.atom_count for m in result.multiplets) == 9


def test_total_h_absent_reports_unconstrained_and_uses_integral_ratio() -> None:
    result = process_proton_spectrum(_ethyl_spectrum())
    assert result.constraints.total_hydrogens is None
    assert result.constraints.total_constraint_applied is False
    by_index = {m.line_indices: m for m in result.multiplets}
    assert by_index[_TRIPLET_INDICES].atom_count == 3
    assert by_index[_QUARTET_INDICES].atom_count == 2
    assert by_index[(_METHOXY_INDEX,)].atom_count == 3
    assert by_index[(_OH_INDEX,)].atom_count == 1
    assert result.constraints.notes  # the missing constraint is stated, never silent


def test_methyl_integral_detection_uses_three_times_reference() -> None:
    lines = [_line(0, 2.1, 3.0, 3.0), _line(1, 4.1, 1.0, 1.0)]
    peaks = [ExperimentalPeak(shift_ppm=line.position_ppm, element="H") for line in lines]
    result = process_proton_spectrum(_spectrum(lines, peaks))
    methyl = _multiplet_with(result.multiplets, 0)
    reference = _multiplet_with(result.multiplets, 1)
    assert methyl.methyl is True
    assert methyl.methyl_source == "integration"
    assert reference.methyl is False


def test_explicit_methyl_annotation_pins_three_hydrogens() -> None:
    lines = [_line(0, 1.00, 1.0, 2.8), _line(1, 1.60, 1.0, 2.2)]
    peaks = [ExperimentalPeak(shift_ppm=line.position_ppm, element="H") for line in lines]
    options = ProtonProcessorOptions(
        total_hydrogens=5,
        methyl_line_indices=(0,),
        methyl_integral_tolerance=0.1,
    )
    result = process_proton_spectrum(_spectrum(lines, peaks), options)
    methyl = _multiplet_with(result.multiplets, 0)
    other = _multiplet_with(result.multiplets, 1)
    assert (methyl.methyl, methyl.methyl_source, methyl.atom_count) == (True, "explicit", 3)
    assert other.atom_count == 2
    assert sum(m.atom_count for m in result.multiplets) == 5


def test_explicit_methyl_via_peak_index_annotation() -> None:
    lines = [_line(0, 1.00, 1.0, 2.8), _line(1, 1.60, 1.0, 2.2)]
    peaks = [ExperimentalPeak(shift_ppm=line.position_ppm, element="H") for line in lines]
    options = ProtonProcessorOptions(
        total_hydrogens=5,
        methyl_peak_indices=(0,),
        methyl_integral_tolerance=0.1,
    )
    result = process_proton_spectrum(_spectrum(lines, peaks), options)
    assert _multiplet_with(result.multiplets, 0).methyl_source == "explicit"


def test_total_h_conflict_with_pinned_methyls_is_reported_not_crashed() -> None:
    lines = [_line(0, 2.10, 3.0, 3.0), _line(1, 3.30, 3.0, 3.0)]
    peaks = [ExperimentalPeak(shift_ppm=line.position_ppm, element="H") for line in lines]
    result = process_proton_spectrum(
        _spectrum(lines, peaks), ProtonProcessorOptions(total_hydrogens=5)
    )
    assert sum(m.atom_count for m in result.multiplets) == 5
    assert result.constraints.conflicts
    assert all(m.methyl for m in result.multiplets)  # recognition is not erased by the conflict


def test_total_h_below_group_count_is_reported() -> None:
    lines = [_line(0, 1.0, 1.0, 1.0), _line(1, 2.0, 1.0, 1.0), _line(2, 3.0, 1.0, 1.0)]
    peaks = [ExperimentalPeak(shift_ppm=line.position_ppm, element="H") for line in lines]
    result = process_proton_spectrum(
        _spectrum(lines, peaks), ProtonProcessorOptions(total_hydrogens=2)
    )
    assert result.constraints.conflicts
    assert all(m.atom_count >= 1 for m in result.multiplets)


# ---------------------------------------------------------------------------
# Overlap uncertainty
# ---------------------------------------------------------------------------


def test_interleaved_quartet_and_methoxy_region_flags_overlap_with_alternatives() -> None:
    result = process_proton_spectrum(_ethyl_spectrum(), ProtonProcessorOptions(total_hydrogens=9))
    assert result.overlap_regions
    region = max(result.overlap_regions, key=lambda r: len(r.multiplet_ids))
    quartet = _multiplet_with(result.multiplets, 3)
    methoxy = _multiplet_with(result.multiplets, _METHOXY_INDEX)
    assert set(region.multiplet_ids) == {quartet.multiplet_id, methoxy.multiplet_id}
    assert region.kind in ("interleaved", "near_overlap")
    assert region.alternatives
    assert quartet.overlap is True
    assert methoxy.overlap is True
    assert "overlap" in quartet.uncertainty_reasons or "interleaved" in quartet.uncertainty_reasons


def test_unresolved_two_singlet_region_carries_alternative_grouping_and_slots() -> None:
    lines = [_line(0, 2.000, 1.0, 1.0), _line(1, 2.012, 1.0, 1.0)]
    peaks = [ExperimentalPeak(shift_ppm=line.position_ppm, element="H") for line in lines]
    result = process_proton_spectrum(
        _spectrum(lines, peaks), ProtonProcessorOptions(total_hydrogens=2)
    )
    assert len(result.multiplets) == 1  # fewer-multiplet explanation wins BIC
    multiplet = result.multiplets[0]
    assert multiplet.overlap is True
    assert multiplet.slots == 2
    assert multiplet.atom_count == 2
    region = result.overlap_regions[0]
    assert region.resolved is False
    assert region.kind == "ambiguous_split"
    split = region.alternatives[0]
    assert split.line_groups == ((0,), (1,))
    assert split.delta_bic > 0


def test_ambiguity_margin_option_controls_resolution() -> None:
    lines = [_line(0, 2.000, 1.0, 1.0), _line(1, 2.012, 1.0, 1.0)]
    peaks = [ExperimentalPeak(shift_ppm=line.position_ppm, element="H") for line in lines]
    result = process_proton_spectrum(
        _spectrum(lines, peaks),
        ProtonProcessorOptions(total_hydrogens=2, ambiguity_margin=0.2),
    )
    # the split evidence (delta_bic 1.39) is decisive at margin 0.2: no
    # ambiguity region and no shared-peak capacity
    assert result.overlap_regions == ()
    assert result.multiplets[0].slots == 1


def test_well_separated_singlets_are_not_an_overlap_region() -> None:
    lines = [_line(0, 2.00, 1.0, 1.0), _line(1, 4.00, 1.0, 1.0)]
    peaks = [ExperimentalPeak(shift_ppm=line.position_ppm, element="H") for line in lines]
    result = process_proton_spectrum(_spectrum(lines, peaks))
    assert len(result.multiplets) == 2
    assert result.overlap_regions == ()


def test_low_snr_line_is_flagged_not_dropped() -> None:
    result = process_proton_spectrum(_ethyl_spectrum(), ProtonProcessorOptions(total_hydrogens=9))
    oh = _multiplet_with(result.multiplets, _OH_INDEX)
    assert oh.low_snr is True
    assert "low_snr" in oh.uncertainty_reasons
    assert oh.atom_count == 1  # flagged, never silently discarded


# ---------------------------------------------------------------------------
# Peak-capacity export (todo 46 PeakConstraint handoff)
# ---------------------------------------------------------------------------


def test_capacities_export_matches_multiplets_and_peak_positions() -> None:
    result = process_proton_spectrum(_ethyl_spectrum(), ProtonProcessorOptions(total_hydrogens=9))
    capacities = result.capacities()
    assert len(capacities) == len(result.multiplets)
    by_id = {capacity.multiplet_id: capacity for capacity in capacities}
    quartet = _multiplet_with(result.multiplets, 3)
    methoxy = _multiplet_with(result.multiplets, _METHOXY_INDEX)
    assert by_id[quartet.multiplet_id].atom_capacity == 2
    assert by_id[methoxy.multiplet_id].atom_capacity == 3
    assert by_id[methoxy.multiplet_id].index == _METHOXY_INDEX  # peak list position fallback
    assert by_id[methoxy.multiplet_id].element == "H"


def test_capacity_to_peak_constraint_is_assignment_consumable() -> None:
    from acp.nmr.iterative_assignment import PeakConstraint

    spectrum = _spectrum(_ethyl_lines(), _ethyl_peaks(index_offset=100))
    result = process_proton_spectrum(spectrum, ProtonProcessorOptions(total_hydrogens=9))
    capacity = next(
        c for c in result.capacities() if c.atom_capacity == 3 and c.index == 100 + _METHOXY_INDEX
    )
    constraint = capacity.to_peak_constraint()
    assert isinstance(constraint, PeakConstraint)
    assert constraint.element == "H"
    assert constraint.index == 100 + _METHOXY_INDEX
    assert constraint.atom_capacity == 3
    assert constraint.slots == 1
    override = capacity.to_peak_constraint(index_override=7)
    assert override.index == 7


def test_unresolved_overlap_capacity_carries_slots_two() -> None:
    lines = [_line(0, 2.000, 1.0, 1.0), _line(1, 2.012, 1.0, 1.0)]
    peaks = [ExperimentalPeak(shift_ppm=line.position_ppm, element="H") for line in lines]
    result = process_proton_spectrum(
        _spectrum(lines, peaks), ProtonProcessorOptions(total_hydrogens=2)
    )
    assert result.capacities()[0].slots == 2


# ---------------------------------------------------------------------------
# Serialization / determinism
# ---------------------------------------------------------------------------


def _assert_to_dict_covers_fields(instance: object) -> None:
    payload = instance.to_dict()  # type: ignore[attr-defined]
    assert set(payload) == {field.name for field in dataclasses.fields(instance)}
    json.dumps(payload)  # JSON-safe


def test_result_to_dict_is_json_safe_and_covers_every_field() -> None:
    result = process_proton_spectrum(_ethyl_spectrum(), ProtonProcessorOptions(total_hydrogens=9))
    _assert_to_dict_covers_fields(result)
    _assert_to_dict_covers_fields(result.constraints)
    for multiplet in result.multiplets:
        _assert_to_dict_covers_fields(multiplet)
    for region in result.overlap_regions:
        _assert_to_dict_covers_fields(region)
        for alternative in region.alternatives:
            _assert_to_dict_covers_fields(alternative)
    for capacity in result.capacities():
        _assert_to_dict_covers_fields(capacity)
    json.dumps(result.to_dict())


def test_same_input_produces_identical_outcome() -> None:
    options = ProtonProcessorOptions(total_hydrogens=9)
    first = process_proton_spectrum(_ethyl_spectrum(), options)
    second = process_proton_spectrum(_ethyl_spectrum(), options)
    assert first.to_dict() == second.to_dict()


def test_shuffled_input_line_order_does_not_change_the_grouping() -> None:
    lines = _ethyl_lines()
    shuffled = [lines[i] for i in (7, 0, 8, 3, 5, 1, 6, 2, 4)]
    options = ProtonProcessorOptions(total_hydrogens=9)
    baseline = process_proton_spectrum(_spectrum(lines, _ethyl_peaks()), options)
    shuffled_result = process_proton_spectrum(_spectrum(shuffled, _ethyl_peaks()), options)
    assert baseline.to_dict() == shuffled_result.to_dict()


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_failed_processing_gate_spectrum_is_rejected() -> None:
    spectrum = _ethyl_spectrum(
        assessment=ProcessingAssessment(status="failed", reasons=("phase_failed",))
    )
    with pytest.raises(ValueError, match="failed|gate"):
        process_proton_spectrum(spectrum)


def test_non_proton_spectrum_is_rejected() -> None:
    spectrum = _spectrum(_ethyl_lines(), _ethyl_peaks(), element="C", nucleus="13C")
    with pytest.raises(ValueError, match="proton|H"):
        process_proton_spectrum(spectrum)


def test_spectrum_without_lines_or_peaks_is_rejected() -> None:
    spectrum = _spectrum([], [])
    with pytest.raises(ValueError, match="lines|peaks"):
        process_proton_spectrum(spectrum)


def test_peaks_only_fallback_uses_multiplicity_as_integral() -> None:
    spectrum = _spectrum([], _ethyl_peaks())
    result = process_proton_spectrum(spectrum, ProtonProcessorOptions(total_hydrogens=9))
    assert sum(m.atom_count for m in result.multiplets) == 9


def test_options_validation() -> None:
    with pytest.raises(ValueError):
        ProtonProcessorOptions(total_hydrogens=0)
    with pytest.raises(ValueError):
        ProtonProcessorOptions(max_coupling_hz=-1.0)
    with pytest.raises(ValueError):
        ProtonProcessorOptions(ambiguity_margin=-0.5)
    with pytest.raises(ValueError):
        ProtonProcessorOptions(min_residual_ppm=0.0)
    with pytest.raises(ValueError):
        ProtonProcessorOptions(methyl_line_indices=(-1,))


# ---------------------------------------------------------------------------
# Manual-annotation comparison metrics
# ---------------------------------------------------------------------------


def _ethyl_annotations() -> list[ProtonAnnotation]:
    return [
        ProtonAnnotation(
            center_ppm=1.2,
            line_positions_ppm=_TRIPLET_POSITIONS,
            atom_count=3,
            label="CH3 triplet",
        ),
        ProtonAnnotation(
            center_ppm=3.59,
            line_positions_ppm=_QUARTET_POSITIONS,
            atom_count=2,
            label="CH2 quartet",
        ),
        ProtonAnnotation(
            center_ppm=_METHOXY_POSITION,
            line_positions_ppm=(_METHOXY_POSITION,),
            atom_count=3,
            label="OCH3",
        ),
        ProtonAnnotation(
            center_ppm=_OH_POSITION,
            line_positions_ppm=(_OH_POSITION,),
            atom_count=1,
            label="OH",
        ),
    ]


def test_metrics_perfect_against_manual_annotation() -> None:
    result = process_proton_spectrum(_ethyl_spectrum(), ProtonProcessorOptions(total_hydrogens=9))
    metrics = compare_proton_grouping(result, _ethyl_annotations())
    assert metrics.n_predicted == 4
    assert metrics.n_annotated == 4
    assert metrics.matched == 4
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 1.0
    assert metrics.pairwise_accuracy == 1.0
    assert metrics.atom_count_accuracy == 1.0
    assert metrics.unmatched_predicted == ()
    assert metrics.unmatched_annotated == ()
    json.dumps(metrics.to_dict())


def test_metrics_penalize_a_missing_multiplet() -> None:
    result = process_proton_spectrum(_ethyl_spectrum(), ProtonProcessorOptions(total_hydrogens=9))
    trimmed = dataclasses.replace(
        result,
        multiplets=tuple(m for m in result.multiplets if _METHOXY_INDEX not in m.line_indices),
    )
    metrics = compare_proton_grouping(trimmed, _ethyl_annotations())
    assert metrics.matched == 3
    assert metrics.precision == 1.0
    assert metrics.recall == pytest.approx(0.75)
    assert metrics.unmatched_annotated  # the OCH3 annotation is reported unmatched
    assert metrics.pairwise_accuracy is not None and metrics.pairwise_accuracy < 1.0


def test_metrics_penalize_a_merged_prediction() -> None:
    lines = [_line(0, 2.000, 1.0, 1.0), _line(1, 2.012, 1.0, 1.0)]
    peaks = [ExperimentalPeak(shift_ppm=line.position_ppm, element="H") for line in lines]
    result = process_proton_spectrum(
        _spectrum(lines, peaks), ProtonProcessorOptions(total_hydrogens=2)
    )
    annotations = [
        ProtonAnnotation(center_ppm=2.000, line_positions_ppm=(2.000,), atom_count=1, label="A"),
        ProtonAnnotation(center_ppm=2.012, line_positions_ppm=(2.012,), atom_count=1, label="B"),
    ]
    metrics = compare_proton_grouping(result, annotations)
    assert metrics.matched == 1
    assert metrics.recall == pytest.approx(0.5)
    assert metrics.pairwise_accuracy == 0.0


def test_metrics_center_matching_when_annotations_have_no_line_positions() -> None:
    result = process_proton_spectrum(_ethyl_spectrum(), ProtonProcessorOptions(total_hydrogens=9))
    annotations = [ProtonAnnotation(center_ppm=1.2, atom_count=3, label="CH3")]
    metrics = compare_proton_grouping(result, annotations)
    assert metrics.matched == 1
    assert metrics.precision == pytest.approx(0.25)
    assert metrics.pairwise_accuracy is None


# ---------------------------------------------------------------------------
# Synthetic-but-faithful end-to-end (real nmrglue processing chain)
# ---------------------------------------------------------------------------


def _write_proton_fid(
    root: Path, peaks: list[tuple[float, float, float]], *, td: int = 16384
) -> Path:
    """Write a synthetic Bruker 1H experiment (int32 FID + acqus)."""
    import numpy as np

    bf1_mhz, sw_ppm, o1_ppm = _FREQUENCY_MHZ, 10.0, 5.0
    sw_hz = sw_ppm * bf1_mhz
    t = np.arange(td) / sw_hz
    fid = np.zeros(td, dtype=complex)
    for ppm, amp, r2 in peaks:
        nu = (o1_ppm - ppm) * bf1_mhz
        fid += amp * np.exp(2j * np.pi * nu * t) * np.exp(-np.pi * r2 * t)
    rng = np.random.default_rng(7)
    fid += rng.normal(0, 0.0002, td) + 1j * rng.normal(0, 0.0002, td)
    fid *= 1e6

    root.mkdir(parents=True, exist_ok=True)
    raw = np.empty(2 * td, dtype=np.int32)
    raw[0::2] = fid.real.astype(np.int32)
    raw[1::2] = fid.imag.astype(np.int32)
    raw.astype("<i4").tofile(root / "fid")
    (root / "acqus").write_text(
        f"##$TD= {2 * td}\n"
        f"##$SFO1= {bf1_mhz}\n"
        f"##$BF1= {bf1_mhz}\n"
        f"##$O1= {o1_ppm * bf1_mhz}\n"
        f"##$SW_h= {sw_hz}\n"
        f"##$SW= {sw_ppm}\n"
        "##$NUC1= <1H>\n"
        "##$BYTORDA= 0\n"
        "##$DTYPA= 0\n"
        "##$AQ_mod= 1\n"
        "##$DECIM= 1\n"
        "##$DSPFVS= 0\n"
        "##$GRPDLY= 0.0\n"
        "##$SOLVENT= <CDCl3>\n"
        "##END=\n",
        encoding="utf-8",
    )
    return root


def test_end_to_end_synthetic_fid_through_real_processing_chain(tmp_path: Path) -> None:
    try:
        from acp.nmr.spectra import _import_nmrglue, process_bruker_experiment
    except ImportError as exc:  # pragma: no cover - environment gate
        pytest.skip(f"{NOT_VERIFIED}: nmrglue unavailable for the real processing chain: {exc}")
    try:
        _import_nmrglue()
    except ImportError as exc:  # pragma: no cover - environment gate
        pytest.skip(f"{NOT_VERIFIED}: nmrglue unavailable for the real processing chain: {exc}")

    peaks = [
        (1.1856, 0.75, 1.5),
        (1.2, 1.5, 1.5),
        (1.2144, 0.75, 1.5),
        (3.5684, 0.25, 1.5),
        (3.5828, 0.75, 1.5),
        (3.5972, 0.75, 1.5),
        (3.6116, 0.25, 1.5),
        (3.59, 3.0, 1.5),
        (4.8, 0.5, 2.0),
    ]
    exp_dir = _write_proton_fid(tmp_path / "Proton", peaks)
    spectrum = process_bruker_experiment(exp_dir)
    assert spectrum.formal_usable

    result = process_proton_spectrum(spectrum, ProtonProcessorOptions(total_hydrogens=9))
    assert sum(m.atom_count for m in result.multiplets) == 9
    triplet_candidates = [m for m in result.multiplets if 1.15 < m.center_ppm < 1.25]
    assert len(triplet_candidates) == 1
    triplet = triplet_candidates[0]
    assert len(triplet.line_indices) == 3
    assert triplet.methyl is True
    assert triplet.atom_count == 3
    assert triplet.coupling_hz == pytest.approx(_J_HZ, abs=0.4)


def test_real_instrument_proton_fixture_is_not_verified() -> None:
    candidates = [
        Path(__file__).parent / "fixtures" / "proton_real",
        Path(__file__).parent / "fixtures" / "bruker_proton_real",
        Path(__file__).parent / "fixtures" / "bruker_proton",
    ]
    existing = [path for path in candidates if path.exists()]
    if not existing:
        pytest.skip(
            f"{NOT_VERIFIED}: no real proton Bruker dataset in tests/fixtures/ "
            "(searched proton_real / bruker_proton_real / bruker_proton); the "
            "real-instrument multiplet-vs-annotation layer is not executed — "
            "synthetic hand-built + synthetic-FID layers remain binding"
        )
    # If a dataset is ever added, the real layer must run the processor and
    # compare against a manual annotation file shipped next to it.
    annotations_file = existing[0] / "annotations.json"
    if not annotations_file.exists():
        pytest.skip(
            f"{NOT_VERIFIED}: real proton dataset present at {existing[0]} but no "
            "annotations.json manual ground truth; metrics cannot be computed"
        )
    pytest.fail("real proton fixture present: wire the annotated comparison before landing")


def test_zip_archived_synthetic_fid_end_to_end(tmp_path: Path) -> None:
    """The processor consumes whatever the tree/zip processing chain produced."""
    try:
        from acp.nmr.spectra import _import_nmrglue, process_bruker_tree
    except ImportError as exc:  # pragma: no cover - environment gate
        pytest.skip(f"{NOT_VERIFIED}: nmrglue unavailable for the real processing chain: {exc}")
    try:
        _import_nmrglue()
    except ImportError as exc:  # pragma: no cover - environment gate
        pytest.skip(f"{NOT_VERIFIED}: nmrglue unavailable for the real processing chain: {exc}")

    exp_dir = _write_proton_fid(tmp_path / "bundle" / "Proton", [(2.1, 3.0, 2.0), (1.2, 1.0, 2.0)])
    zip_path = tmp_path / "nmr.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for member in exp_dir.iterdir():
            zf.write(member, f"Proton/{member.name}")
    bundle = process_bruker_tree(zip_path, extract_dir=tmp_path / "extract")
    proton = next(s for s in bundle.spectra if s.element == "H")
    result = process_proton_spectrum(proton, ProtonProcessorOptions(total_hydrogens=4))
    assert sum(m.atom_count for m in result.multiplets) == 4
