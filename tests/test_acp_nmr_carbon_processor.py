"""Carbon spectrum processor: fitting + solvent exclusion + merging (todo 43 / G10).

Gap G10: the Bruker chain picks local maxima and integrates between midpoints;
it never fits line shapes, never excludes the residual solvent peak and never
merges a splitting pattern into one resonance.  This module pins the carbon
processor contract:

* a documented line-shape model (Lorentzian default — carbon relaxation is
  exponential; Gaussian/pseudo-Voigt selectable) is fitted against the
  processed trace with ``scipy.optimize.least_squares``; a seed shifted by
  0.06 ppm must be refined back to the true position — this is the
  "not merely ``find_peaks`` + midpoint integration" teeth;
* large solvent peaks (CDCl3 triplet at 77.16 ppm) are excluded inside a
  configurable window and every decision is recorded as a
  :class:`SolventAssessment` — never a silent drop;
* splitting patterns / unresolved adjacent lines merge into resonance
  candidates with an explicit ``merge_basis`` and propagated uncertainty
  flags (low S/N, overlap); same-nucleus *multiple experiments* are never
  concatenated — the API only accepts a single :class:`ProcessedSpectrum`;
* precision/recall against manual annotations is computable
  (:func:`compare_resonances_to_annotations`).

Test data is a synthetic-but-faithful carbon spectrum (CDCl3 triplet + analyte
singlets + a 1:2:1 CF2 triplet + an unresolved pair + a weak low-S/N line,
all with known manual annotations).  The repository ships NO real Bruker
carbon dataset, so the real-data comparison layer is explicitly
``NOT_VERIFIED`` (skip) while the synthetic layer is binding.
"""

from __future__ import annotations

import json
import math
from dataclasses import fields, is_dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pytest
from scipy.integrate import quad

from acp.nmr.carbon_processor import (
    NUCLEUS,
    NUCLEUS_LABEL,
    PROCESSOR_ID,
    Annotation,
    CarbonOptions,
    CarbonProcessingError,
    CarbonProcessResult,
    FittedCurve,
    FittedLine,
    Resonance,
    SolventAssessment,
    SolventWindow,
    compare_resonances_to_annotations,
    evaluate_line_shape,
    get_processor,
    line_shape_area,
    process_carbon_spectrum,
    processor_descriptor,
    resolve_solvent_windows,
)
from acp.nmr.models import (
    AcquisitionSpectrum,
    ProcessedSpectrum,
    ProcessingAssessment,
    ProcessingQuality,
    SpectralLine,
)
from tests.conftest import NOT_VERIFIED

# ---------------------------------------------------------------------------
# Synthetic-but-faithful carbon fixture
# ---------------------------------------------------------------------------

_MHZ = 100.0
_NOISE = 0.001
# (position_ppm, height, fwhm_ppm) — trace truth, descending-ish (real Bruker
# ppm scales descend); seeds below are deliberately offset by +8 mppm to
# prove the fitter refines them.
_TRUE_LINES: tuple[tuple[float, float, float], ...] = (
    (76.84, 0.30, 0.02),  # CDCl3 residual triplet (1J_CD ~ 32 Hz = 0.32 ppm)
    (77.16, 0.60, 0.02),
    (77.48, 0.30, 0.02),
    (18.0, 0.80, 0.018),
    (45.0, 0.005, 0.02),  # weak line: S/N = 5 < min_snr 10 -> low_snr flag
    (57.2, 0.55, 0.02),
    (61.4, 0.45, 0.02),
    (117.6, 0.40, 0.02),  # CF2 1:2:1 triplet, J = 240 Hz = 2.4 ppm
    (120.0, 0.80, 0.02),
    (122.4, 0.40, 0.02),
    (131.00, 0.50, 0.12),  # unresolved pair: gap 0.06 < 0.12 FWHM
    (131.06, 0.50, 0.12),
)
_SEED_OFFSET_PPM = 0.008
_ANNOTATIONS: tuple[Annotation, ...] = (
    Annotation(18.0, multiplicity=1, label="C1"),
    Annotation(45.0, multiplicity=1, label="C2"),
    Annotation(57.2, multiplicity=1, label="C3"),
    Annotation(61.4, multiplicity=1, label="C4"),
    Annotation(120.0, multiplicity=3, label="C5"),
    Annotation(131.03, multiplicity=2, label="C6"),
)


def _acquisition(solvent: str | None = "CDCl3") -> AcquisitionSpectrum:
    return AcquisitionSpectrum(
        spectrometer="Bruker",
        nucleus="13C",
        frequency_mhz=_MHZ,
        solvent=solvent,
        temperature_k=298.0,
        source_dir="/synthetic/carbon/1",
    )


def _synthetic_trace(seed: int = 20261006) -> tuple[np.ndarray, np.ndarray]:
    ppm = np.linspace(205.0, -5.0, 105_001)
    intensity = np.zeros_like(ppm)
    for position, height, fwhm in _TRUE_LINES:
        intensity += evaluate_line_shape(ppm, position, height, fwhm, "lorentzian")
    intensity += np.random.default_rng(seed).normal(0.0, _NOISE, ppm.size)
    return ppm, intensity


def _synthetic_spectrum(*, solvent: str | None = "CDCl3") -> ProcessedSpectrum:
    lines = [
        SpectralLine(
            position_ppm=position + _SEED_OFFSET_PPM,
            intensity=height,
            width_hz=fwhm * _MHZ,
            integral=None,
            index=index,
        )
        for index, (position, height, fwhm) in enumerate(_TRUE_LINES)
    ]
    return ProcessedSpectrum(
        nucleus="13C",
        element="C",
        peaks=[],
        noise=_NOISE,
        source_dir="/synthetic/carbon/1",
        acquisition=_acquisition(solvent),
        quality=ProcessingQuality(snr=800.0, linewidth_hz=2.0, baseline_rms=0.001),
        lines=lines,
    )


@lru_cache(maxsize=8)
def _fixture_result(*, use_trace: bool, use_solvent_defaults: bool = True) -> CarbonProcessResult:
    spectrum = _synthetic_spectrum()
    trace = _synthetic_trace() if use_trace else None
    options = CarbonOptions(use_solvent_defaults=use_solvent_defaults)
    return process_carbon_spectrum(spectrum, trace=trace, options=options)


def _resonance_by_position(
    result: CarbonProcessResult, position: float, tol: float = 0.1
) -> Resonance:
    matches = [r for r in result.resonances if abs(r.shift_ppm - position) <= tol]
    assert len(matches) == 1, f"expected one resonance near {position}, got {matches!r}"
    return matches[0]


# ---------------------------------------------------------------------------
# Registration descriptor / processor identity (todo 45 handoff surface)
# ---------------------------------------------------------------------------


def test_processor_registration_descriptor() -> None:
    assert NUCLEUS == "C"
    assert NUCLEUS_LABEL == "13C"
    assert PROCESSOR_ID
    processor = get_processor()
    assert processor.nucleus == "C"
    assert processor.processor_id == PROCESSOR_ID
    assert callable(processor.process)
    descriptor = processor_descriptor()
    assert descriptor["nucleus"] == "C"
    assert descriptor["nucleus_label"] == "13C"
    assert descriptor["processor_id"] == PROCESSOR_ID
    assert descriptor["entry_point"].endswith("process_carbon_spectrum")
    assert get_processor() is processor


def test_processor_accepts_only_carbon() -> None:
    processor = get_processor()
    assert processor.accepts(_synthetic_spectrum())
    proton = ProcessedSpectrum(nucleus="1H", element="H", peaks=[], noise=1e-6)
    assert not processor.accepts(proton)


# ---------------------------------------------------------------------------
# Record hygiene: frozen, JSON-safe, strict from_dict
# ---------------------------------------------------------------------------


def test_result_records_are_frozen() -> None:
    samples = [
        get_processor(),
        CarbonOptions(),
        SolventWindow(solvent="CDCl3", center_ppm=77.16),
        FittedLine(position_ppm=1.0, intensity=1.0),
        FittedCurve(region_ppm=(0.0, 1.0), ppm=(0.0, 1.0), intensity=(0.0, 1.0)),
        SolventAssessment(
            solvent="CDCl3",
            center_ppm=77.16,
            window_ppm=1.2,
            status="not_detected",
            reason="no_lines_in_window",
        ),
        Resonance(shift_ppm=1.0),
        Annotation(position_ppm=1.0),
    ]
    for sample in samples:
        assert is_dataclass(sample)
        assert type(sample).__dataclass_params__.frozen, type(sample).__name__
        with pytest.raises(Exception):
            setattr(sample, "smuggled", True)


def _sample_result_fields() -> dict[str, object]:
    result = _fixture_result(use_trace=True)
    assert result.fitted_lines and result.curves and result.resonances
    report = compare_resonances_to_annotations(result, _ANNOTATIONS, tolerance_ppm=0.05)
    return {
        "FittedLine": result.fitted_lines[0],
        "FittedCurve": result.curves[0],
        "SolventAssessment": result.solvent_assessments[0],
        "Resonance": result.resonances[0],
        "CarbonProcessResult": result,
        "CarbonOptions": result.options,
        "SolventWindow": SolventWindow(
            solvent="CDCl3", center_ppm=77.16, window_ppm=1.2, multiplicity=3
        ),
        "Annotation": _ANNOTATIONS[0],
        "ResonanceMatchReport": report,
    }


def test_to_dict_covers_every_declared_field() -> None:
    for name, sample in _sample_result_fields().items():
        declared = {f.name for f in fields(type(sample))}
        assert set(sample.to_dict()) == declared, name


def test_json_round_trip_result_is_exact() -> None:
    result = _fixture_result(use_trace=True)
    payload = json.loads(json.dumps(result.to_dict()))
    restored = CarbonProcessResult.from_dict(payload)
    assert restored == result
    assert isinstance(restored.fitted_lines, tuple)
    assert isinstance(restored.resonances[0].line_indices, tuple)
    assert isinstance(restored.options.solvent_windows, tuple)
    assert isinstance(restored.curves[0].ppm, tuple)


def test_from_dict_rejects_missing_field() -> None:
    payload = _fixture_result(use_trace=True).to_dict()
    payload.pop("fit_status")
    with pytest.raises(ValueError, match="fit_status"):
        CarbonProcessResult.from_dict(payload)


def test_unknown_flag_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown"):
        Resonance(shift_ppm=1.0, flags=("bogus_flag",))


# ---------------------------------------------------------------------------
# Typed refusals (single spectrum only; carbon only; phase gate respected)
# ---------------------------------------------------------------------------


def test_refuses_non_carbon_spectrum() -> None:
    proton = ProcessedSpectrum(nucleus="1H", element="H", peaks=[], noise=1e-6)
    with pytest.raises(CarbonProcessingError, match="carbon"):
        process_carbon_spectrum(proton)


def test_refuses_sequence_of_spectra() -> None:
    """Same-nucleus experiments are selected upstream — never concatenated here."""
    spectrum = _synthetic_spectrum()
    with pytest.raises(CarbonProcessingError, match="single"):
        process_carbon_spectrum([spectrum, spectrum])  # type: ignore[arg-type]


def test_refuses_phase_failed_spectrum() -> None:
    spectrum = _synthetic_spectrum()
    failed = ProcessedSpectrum(
        nucleus=spectrum.nucleus,
        element=spectrum.element,
        peaks=spectrum.peaks,
        noise=spectrum.noise,
        acquisition=spectrum.acquisition,
        lines=spectrum.lines,
        assessment=ProcessingAssessment(status="failed", reasons=("phase_failed",)),
    )
    with pytest.raises(CarbonProcessingError, match="failed"):
        process_carbon_spectrum(failed)


# ---------------------------------------------------------------------------
# Line-shape model (documented choice + analytic areas)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", ["lorentzian", "gaussian", "pseudo-voigt"])
def test_line_shape_center_and_fwhm(shape: str) -> None:
    center, height, fwhm = 10.0, 2.0, 0.4
    apex = evaluate_line_shape(np.array([center]), center, height, fwhm, shape)[0]
    assert apex == pytest.approx(height)
    half = evaluate_line_shape(np.array([center + fwhm / 2.0]), center, height, fwhm, shape)[0]
    assert half == pytest.approx(height / 2.0, rel=1e-9)


@pytest.mark.parametrize("shape", ["lorentzian", "gaussian", "pseudo-voigt"])
def test_line_shape_area_matches_numeric_integral(shape: str) -> None:
    center, height, fwhm = 10.0, 2.0, 0.4

    def substituted(theta: float) -> float:
        # t = tan(theta) maps the whole line onto (-pi/2, pi/2); dt = sec^2 d(theta).
        t = math.tan(theta)
        value = evaluate_line_shape(np.array([center + fwhm * t]), center, height, fwhm, shape)[0]
        return float(value) * fwhm / math.cos(theta) ** 2

    numeric, _ = quad(substituted, -math.pi / 2.0, math.pi / 2.0, epsabs=1e-10)
    assert numeric == pytest.approx(line_shape_area(height, fwhm, shape), rel=1e-6)


def test_invalid_shape_rejected() -> None:
    with pytest.raises(ValueError, match="line_shape"):
        CarbonOptions(line_shape="lorentzian-plus")


def test_pseudo_voigt_eta_validated() -> None:
    with pytest.raises(ValueError, match="pseudo_voigt_eta"):
        CarbonOptions(pseudo_voigt_eta=1.5)


def test_pseudo_voigt_area_is_eta_mix() -> None:
    lorentzian = line_shape_area(1.0, 1.0, "lorentzian")
    gaussian = line_shape_area(1.0, 1.0, "gaussian")
    mixed = line_shape_area(1.0, 1.0, "pseudo-voigt", eta=0.25)
    assert mixed == pytest.approx(0.25 * lorentzian + 0.75 * gaussian)


# ---------------------------------------------------------------------------
# Solvent windows
# ---------------------------------------------------------------------------


def test_solvent_windows_defaults_and_overrides() -> None:
    defaults = resolve_solvent_windows("CDCl3")
    assert len(defaults) == 1
    assert defaults[0].center_ppm == pytest.approx(77.16)
    assert defaults[0].multiplicity == 3
    explicit = SolventWindow(solvent="cdcl3", center_ppm=77.0, window_ppm=0.6, multiplicity=3)
    overridden = resolve_solvent_windows("CDCl3", CarbonOptions(solvent_windows=(explicit,)))
    assert overridden == (explicit,)
    assert resolve_solvent_windows("unknown-solvent") == ()


# ---------------------------------------------------------------------------
# Solvent exclusion — recorded, never silent
# ---------------------------------------------------------------------------


def test_solvent_triplet_excluded_with_record() -> None:
    result = _fixture_result(use_trace=True)
    (assessment,) = result.solvent_assessments
    assert assessment.status == "excluded"
    assert assessment.reason == "solvent_pattern_match"
    assert assessment.solvent == "CDCl3"
    assert assessment.window_ppm == pytest.approx(1.2)
    assert len(assessment.line_indices) == 3
    assert not any(abs(r.shift_ppm - 77.16) < 1.0 for r in result.resonances)
    excluded = [line for line in result.fitted_lines if line.excluded_reason == "solvent"]
    assert len(excluded) == 3
    assert all("solvent_excluded" in line.flags for line in excluded)


def test_solvent_exclusion_improves_precision() -> None:
    kept = _fixture_result(use_trace=True, use_solvent_defaults=False)
    report = compare_resonances_to_annotations(kept, _ANNOTATIONS, tolerance_ppm=0.05)
    assert report.n_resonances == 7
    assert len(report.spurious_resonances) == 1
    assert report.precision == pytest.approx(6.0 / 7.0)
    assert report.recall == pytest.approx(1.0)


def test_solvent_pattern_mismatch_is_ambiguous_not_dropped() -> None:
    spectrum = ProcessedSpectrum(
        nucleus="13C",
        element="C",
        peaks=[],
        noise=_NOISE,
        source_dir="/synthetic/carbon/2",
        acquisition=_acquisition(),
        lines=[
            SpectralLine(position_ppm=76.84, intensity=0.30, width_hz=2.0, index=0),
            SpectralLine(position_ppm=77.48, intensity=0.30, width_hz=2.0, index=1),
        ],
    )
    result = process_carbon_spectrum(spectrum)
    (assessment,) = result.solvent_assessments
    assert assessment.status == "ambiguous"
    assert assessment.reason == "solvent_pattern_mismatch"
    assert assessment.line_indices == (0, 1)
    assert any("solvent_ambiguous" in r.flags for r in result.resonances)
    assert result.resonances, "ambiguous in-window lines are retained for review"


def test_custom_solvent_window_not_detected() -> None:
    spectrum = ProcessedSpectrum(
        nucleus="13C",
        element="C",
        peaks=[],
        noise=_NOISE,
        source_dir="/synthetic/carbon/2",
        acquisition=_acquisition(),
        lines=[
            SpectralLine(position_ppm=76.84, intensity=0.30, width_hz=2.0, index=0),
            SpectralLine(position_ppm=77.48, intensity=0.30, width_hz=2.0, index=1),
        ],
    )
    options = CarbonOptions(
        solvent_windows=(SolventWindow(solvent="CDCl3", center_ppm=60.0, window_ppm=0.3),)
    )
    result = process_carbon_spectrum(spectrum, options=options)
    (assessment,) = result.solvent_assessments
    assert assessment.status == "not_detected"
    assert assessment.reason == "no_lines_in_window"


def test_solvent_unknown_flag_without_acquisition() -> None:
    spectrum = ProcessedSpectrum(
        nucleus="13C",
        element="C",
        peaks=[],
        noise=_NOISE,
        lines=[SpectralLine(position_ppm=18.0, intensity=0.8, width_hz=2.0, index=0)],
    )
    result = process_carbon_spectrum(spectrum)
    assert result.solvent_assessments == ()
    assert "solvent_unknown" in result.flags


def test_all_solvent_lines_yields_no_resonances_flag() -> None:
    spectrum = ProcessedSpectrum(
        nucleus="13C",
        element="C",
        peaks=[],
        noise=_NOISE,
        source_dir="/synthetic/carbon/3",
        acquisition=_acquisition(),
        lines=[
            SpectralLine(position_ppm=76.84, intensity=0.30, width_hz=2.0, index=0),
            SpectralLine(position_ppm=77.16, intensity=0.60, width_hz=2.0, index=1),
            SpectralLine(position_ppm=77.48, intensity=0.30, width_hz=2.0, index=2),
        ],
    )
    result = process_carbon_spectrum(spectrum)
    assert result.resonances == ()
    assert "no_resonances" in result.flags
    assert result.solvent_assessments[0].status == "excluded"


# ---------------------------------------------------------------------------
# Fitting: shifted seeds are refined (the "not find_peaks" teeth)
# ---------------------------------------------------------------------------


def test_fitted_positions_refine_shifted_seeds() -> None:
    ppm = np.linspace(45.0, 35.0, 10_001)
    true_position, height, fwhm = 40.0, 0.5, 0.02
    intensity = evaluate_line_shape(ppm, true_position, height, fwhm, "lorentzian")
    intensity = intensity + np.random.default_rng(3).normal(0.0, 0.0005, ppm.size)
    spectrum = ProcessedSpectrum(
        nucleus="13C",
        element="C",
        peaks=[],
        noise=0.0005,
        source_dir="/synthetic/carbon/4",
        acquisition=_acquisition(),
        lines=[
            SpectralLine(
                position_ppm=true_position + 0.06,  # deliberate +60 mppm seed error
                intensity=height,
                width_hz=fwhm * _MHZ,
                index=0,
            )
        ],
    )
    result = process_carbon_spectrum(spectrum, trace=(ppm, intensity))
    (line,) = result.fitted_lines
    assert line.fit_rms is not None and line.fit_rms < 0.005
    # The fit window is centred on the SEED (40.06), and the fit refines the
    # position back to the truth (40.00) inside that window.
    assert line.region == pytest.approx((40.06 - 0.08, 40.06 + 0.08))
    assert abs(line.position_ppm - true_position) < 0.005
    assert abs(line.position_ppm - true_position) < abs(0.06) / 5.0
    (resonance,) = result.resonances
    assert resonance.shift_ppm == pytest.approx(true_position, abs=0.005)


# ---------------------------------------------------------------------------
# Resonance merging + uncertainty flags
# ---------------------------------------------------------------------------


def test_multiplet_pattern_merges_cf2_triplet() -> None:
    result = _fixture_result(use_trace=True)
    triplet = _resonance_by_position(result, 120.0)
    assert triplet.multiplicity == 3
    assert len(triplet.line_indices) == 3
    assert triplet.merge_basis == "multiplet_pattern"
    assert triplet.shift_ppm == pytest.approx(120.0, abs=0.01)
    assert "overlap" not in triplet.flags


def test_unresolved_overlap_pair_merges_with_flag() -> None:
    result = _fixture_result(use_trace=True)
    pair = _resonance_by_position(result, 131.03)
    assert pair.multiplicity == 2
    assert pair.merge_basis == "unresolved_overlap"
    assert "overlap" in pair.flags


def test_low_snr_line_flagged() -> None:
    result = _fixture_result(use_trace=True)
    weak = _resonance_by_position(result, 45.0)
    assert "low_snr" in weak.flags
    strong = _resonance_by_position(result, 57.2)
    assert "low_snr" not in strong.flags


def test_singlets_are_not_merged() -> None:
    result = _fixture_result(use_trace=True)
    for position in (18.0, 45.0, 57.2, 61.4):
        resonance = _resonance_by_position(result, position)
        assert resonance.multiplicity == 1
        assert resonance.merge_basis == "single"


# ---------------------------------------------------------------------------
# Precision / recall vs manual annotations
# ---------------------------------------------------------------------------


def test_resonances_align_with_manual_annotations() -> None:
    result = _fixture_result(use_trace=True)
    report = compare_resonances_to_annotations(
        result, _ANNOTATIONS, tolerance_ppm=0.05, require_multiplicity=True
    )
    assert report.n_annotations == 6
    assert report.n_resonances == 6
    assert report.precision == pytest.approx(1.0)
    assert report.recall == pytest.approx(1.0)
    assert report.f1 == pytest.approx(1.0)
    assert report.missed_annotations == ()
    assert report.spurious_resonances == ()
    assert len(report.matches) == 6
    # The weak line (index 1) and the unresolved pair (index 5) are uncertain.
    assert report.uncertain_resonances == (1, 5)
    assert report.uncertain_matches == (1, 5)


def test_curve_regions_retained_for_human_adjustment() -> None:
    result = _fixture_result(use_trace=True)
    assert result.curves
    for curve in result.curves:
        low, high = curve.region_ppm
        assert low < high
        assert len(curve.ppm) == len(curve.intensity) == 256
        assert curve.ppm[0] == pytest.approx(low)
        assert curve.ppm[-1] == pytest.approx(high)
        assert all(low <= value <= high for value in curve.ppm)
        assert curve.fit_rms is not None
        assert curve.line_indices, "each retained curve names its fitted lines"
        for index in curve.line_indices:
            position = result.fitted_lines[index].position_ppm
            assert low <= position <= high


# ---------------------------------------------------------------------------
# No-trace mode: explicitly unverified, never fabricated
# ---------------------------------------------------------------------------


def test_no_trace_mode_marks_unverified() -> None:
    result = _fixture_result(use_trace=False)
    assert result.fit_status == "trace_unavailable"
    assert "trace_unavailable" in result.flags
    assert result.curves == ()
    for line in result.fitted_lines:
        assert line.fit_rms is None
        assert line.region is None
        assert "unverified_fit" in line.flags
    assert len(result.resonances) == 6
    triplet = _resonance_by_position(result, 120.0)
    assert triplet.multiplicity == 3
    assert "unverified_fit" in triplet.flags


def test_processing_is_deterministic() -> None:
    spectrum = _synthetic_spectrum()
    trace = _synthetic_trace()
    first = process_carbon_spectrum(spectrum, trace=trace).to_dict()
    second = process_carbon_spectrum(spectrum, trace=trace).to_dict()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# ---------------------------------------------------------------------------
# Scoring function — unit-level behavior
# ---------------------------------------------------------------------------


def _resonance(
    position: float,
    *,
    n_lines: int = 1,
    intensity: float = 1.0,
    flags: tuple[str, ...] = (),
) -> Resonance:
    return Resonance(
        shift_ppm=position,
        line_indices=tuple(range(n_lines)),
        multiplicity=n_lines,
        intensity=intensity,
        flags=flags,
    )


def test_compare_greedy_one_to_one() -> None:
    report = compare_resonances_to_annotations(
        [_resonance(1.0), _resonance(2.0)],
        [Annotation(1.02), Annotation(2.03), Annotation(3.0)],
        tolerance_ppm=0.05,
    )
    assert [m.resonance_index for m in report.matches] == [0, 1]
    assert [m.annotation_index for m in report.matches] == [0, 1]
    assert report.missed_annotations == (2,)
    assert report.spurious_resonances == ()
    assert report.precision == pytest.approx(1.0)
    assert report.recall == pytest.approx(2.0 / 3.0)


def test_compare_spurious_and_missed() -> None:
    report = compare_resonances_to_annotations(
        [_resonance(1.0), _resonance(5.0)],
        [Annotation(1.01)],
        tolerance_ppm=0.05,
    )
    assert report.spurious_resonances == (1,)
    assert report.precision == pytest.approx(0.5)
    assert report.recall == pytest.approx(1.0)


def test_compare_multiplicity_criterion() -> None:
    resonances = [_resonance(1.0, n_lines=3)]
    annotations = [Annotation(1.0, multiplicity=1)]
    lenient = compare_resonances_to_annotations(resonances, annotations)
    assert len(lenient.matches) == 1
    strict = compare_resonances_to_annotations(resonances, annotations, require_multiplicity=True)
    assert strict.matches == ()
    assert strict.missed_annotations == (0,)
    assert strict.spurious_resonances == (0,)


def test_compare_intensity_criterion() -> None:
    resonances = [_resonance(1.0, intensity=1.0)]
    annotations = [Annotation(1.0, intensity=1.2)]
    inside = compare_resonances_to_annotations(
        resonances, annotations, require_intensity=True, intensity_rtol=0.25
    )
    assert len(inside.matches) == 1
    outside = compare_resonances_to_annotations(
        resonances, annotations, require_intensity=True, intensity_rtol=0.1
    )
    assert outside.matches == ()


def test_compare_uncertain_counts() -> None:
    report = compare_resonances_to_annotations(
        [_resonance(1.0, flags=("low_snr",)), _resonance(2.0)],
        [Annotation(1.0), Annotation(2.0)],
    )
    assert report.uncertain_resonances == (0,)
    assert report.uncertain_matches == (0,)


def test_compare_empty_edges() -> None:
    no_resonances = compare_resonances_to_annotations([], [Annotation(1.0)])
    assert no_resonances.precision == 0.0
    assert no_resonances.recall == 0.0
    assert no_resonances.f1 == 0.0
    no_annotations = compare_resonances_to_annotations([_resonance(1.0)], [])
    assert no_annotations.precision == 0.0
    assert no_annotations.recall == 0.0


def test_compare_accepts_bare_float_annotations() -> None:
    report = compare_resonances_to_annotations([_resonance(1.0)], [1.01], tolerance_ppm=0.05)
    assert len(report.matches) == 1


def test_compare_rejects_invalid_tolerance() -> None:
    with pytest.raises(ValueError, match="tolerance_ppm"):
        compare_resonances_to_annotations([_resonance(1.0)], [Annotation(1.0)], tolerance_ppm=0.0)


# ---------------------------------------------------------------------------
# Real-data layer: explicitly NOT_VERIFIED while no real dataset ships
# ---------------------------------------------------------------------------


def _find_real_bruker_carbon() -> Path | None:
    tests_root = Path(__file__).resolve().parent
    for acqus in sorted(tests_root.rglob("acqus")):
        text = acqus.read_text(encoding="utf-8", errors="replace")
        if "NUC1" not in text or "13C" not in text:
            continue
        directory = acqus.parent
        if (directory / "fid").is_file() or (directory / "ser").is_file():
            return directory
    return None


def test_real_bruker_carbon_fixture_precision_recall() -> None:
    """Real-data layer; the repo ships no Bruker carbon dataset -> NOT_VERIFIED."""
    real_dir = _find_real_bruker_carbon()
    if real_dir is None:
        pytest.skip(
            f"{NOT_VERIFIED}: no real Bruker 13C dataset (acqus + fid) ships under tests/; "
            "the synthetic fixture above is the binding layer — real-data precision/recall "
            "requires an operator-provided annotated dataset"
        )
    try:
        from acp.nmr.spectra import _import_nmrglue

        _import_nmrglue()
    except ImportError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"{NOT_VERIFIED}: nmrglue unavailable for real Bruker processing: {exc}")
    from acp.nmr.spectra import process_bruker_experiment

    spectrum = process_bruker_experiment(real_dir)
    result = process_carbon_spectrum(spectrum)
    assert math.isfinite(result.resonances[0].shift_ppm)
