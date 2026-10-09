"""Four-layer spectrum model with processing provenance/quality (G10, todo 41).

The spectra layer stack is explicit and round-trippable:

1. :class:`AcquisitionSpectrum` — what the spectrometer recorded (never a
   processing decision);
2. :class:`ProcessedSpectrum` — processed data + an explicit
   :class:`ProcessingProvenance` block (apodization/filter, zero-fill,
   phase, baseline, referencing) and :class:`ProcessingQuality` metrics;
3. :class:`SpectralLine` — fitted/observed lines of the processed data;
4. :class:`ResonanceSignal` — chemically meaningful assigned signals,
   created ONLY through assignment: a raw unmatched peak is never
   auto-promoted to a resonance.

Guards pinned here:

* every layer ``to_dict`` covers every declared dataclass field and a JSON
  round-trip preserves the record exactly (floats included);
* ``from_dict`` rejects a payload missing a provenance field (explicit
  failure, no silent defaults);
* the legacy ``ExperimentalNmr`` construction/read path is unchanged and a
  legacy hand-built ``ProcessedSpectrum`` (no layer records) still works;
* the real Bruker pipeline populates the layers from the actual processing
  chain (nmrglue-gated) and the ``bruker_peaks.txt`` text path is untouched.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import numpy as np
import pytest

from acp.nmr.models import (
    AcquisitionSpectrum,
    ExperimentalNmr,
    ExperimentalPeak,
    ProcessedSpectrum,
    ProcessingProvenance,
    ProcessingQuality,
    ResonanceSignal,
    SpectralLine,
    resonance_signals_from_peaks,
)

# Bruker pipeline tests need the optional nmrglue capability; the model-level
# tests below never do, so this file does not skip as a whole (unlike
# test_acp_nmr_spectra.py, whose every case needs the pipeline).
try:
    from acp.nmr.spectra import _import_nmrglue

    _import_nmrglue()
    _NMRGLUE_AVAILABLE = True
    _NMRGLUE_REASON = ""
except ImportError as _nmrglue_exc:  # pragma: no cover - environment dependent
    _NMRGLUE_AVAILABLE = False
    _NMRGLUE_REASON = f"nmrglue capability unavailable (acp[nmr] extra): {_nmrglue_exc}"

requires_nmrglue = pytest.mark.skipif(not _NMRGLUE_AVAILABLE, reason=_NMRGLUE_REASON)


# ---------------------------------------------------------------------------
# Sample layer records (full provenance, exact values)
# ---------------------------------------------------------------------------


def _sample_acquisition() -> AcquisitionSpectrum:
    return AcquisitionSpectrum(
        spectrometer="Bruker",
        nucleus="1H",
        frequency_mhz=500.13,
        solvent="CDCl3",
        temperature_k=298.0,
        pulse_program="zg30",
        point_count=16384,
        spectral_width_hz=5001.3,
        carrier_ppm=5.0,
        group_delay_points=0.0,
        dspfvs=0,
        spectrometer_reference=0.0,
        source_dir="/data/sample/1",
    )


def _sample_processing() -> ProcessingProvenance:
    return ProcessingProvenance(
        apodization="exponential",
        lb_hz=0.3,
        gb=None,
        sb=None,
        zero_fill_points=65536,
        zero_fill_factor=4.0,
        phase_method="peak_minima",
        phase_p0_deg=None,
        phase_p1_deg=None,
        baseline_method="morphological_grey_opening",
        baseline_window_fraction=0.02,
        reference_method="manual_anchor",
        reference_ppm=7.26,
        applied_shift_ppm=0.14,
    )


def _sample_quality() -> ProcessingQuality:
    return ProcessingQuality(snr=123.456789, linewidth_hz=5.3, baseline_rms=2.5e-05)


def _sample_line() -> SpectralLine:
    return SpectralLine(
        position_ppm=7.12,
        intensity=12345.678,
        width_hz=5.3,
        integral=9876.5,
        index=0,
    )


def _sample_resonance() -> ResonanceSignal:
    return ResonanceSignal(
        shift_ppm=7.12,
        element="H",
        multiplicity=2,
        atom_refs=("H1", "H2"),
        group_refs=("sg:H1,H2",),
    )


def _sample_processed() -> ProcessedSpectrum:
    return ProcessedSpectrum(
        nucleus="1H",
        element="H",
        peaks=[
            ExperimentalPeak(
                shift_ppm=7.12,
                element="H",
                atom_label="H1",
                multiplicity=1,
                label_candidates=("H1",),
                index=0,
            ),
            ExperimentalPeak(shift_ppm=3.5, element="H", multiplicity=2, index=1),
            ExperimentalPeak(
                shift_ppm=5.0,
                element="H",
                label_candidates=("H32", "H33"),
                index=2,
            ),
        ],
        noise=0.00025,
        reference_shift=0.14,
        source_dir="/data/sample/1",
        acquisition=_sample_acquisition(),
        processing=_sample_processing(),
        quality=_sample_quality(),
        lines=[_sample_line()],
    )


# ---------------------------------------------------------------------------
# Serialization completeness + exact round-trip
# ---------------------------------------------------------------------------


def _json_round_trip(obj):
    payload = json.loads(json.dumps(obj.to_dict()))
    return type(obj).from_dict(payload)


_LAYER_SAMPLES = [
    _sample_acquisition(),
    _sample_processing(),
    ProcessingQuality(snr=None, linewidth_hz=None, baseline_rms=None),
    _sample_quality(),
    _sample_line(),
    SpectralLine(position_ppm=1.0, intensity=2.0, width_hz=None, integral=None, index=None),
    _sample_resonance(),
    ResonanceSignal(shift_ppm=1.0, element="C", multiplicity=1),
    _sample_processed(),
]


@pytest.mark.parametrize("instance", _LAYER_SAMPLES, ids=lambda o: type(o).__name__)
def test_to_dict_covers_every_declared_field(instance: object) -> None:
    declared = {f.name for f in fields(type(instance))}
    assert set(instance.to_dict()) == declared


@pytest.mark.parametrize("instance", _LAYER_SAMPLES, ids=lambda o: type(o).__name__)
def test_layer_json_round_trip_is_exact(instance: object) -> None:
    restored = _json_round_trip(instance)
    assert restored == instance


def test_processed_spectrum_round_trip_keeps_layers_and_tuples() -> None:
    sample = _sample_processed()
    restored = _json_round_trip(sample)
    assert restored == sample
    assert restored.acquisition == sample.acquisition
    assert restored.processing == sample.processing
    assert restored.quality == sample.quality
    assert restored.lines == sample.lines
    ambiguous = restored.peaks[2]
    assert ambiguous.label_candidates == ("H32", "H33")
    assert ambiguous.atom_label is None


def test_processed_spectrum_legacy_fields_only_still_works() -> None:
    """Pre-todo-41 construction path: no layer records at all."""
    legacy = ProcessedSpectrum(nucleus="13C", element="C", peaks=[], noise=1e-06)
    assert legacy.acquisition is None
    assert legacy.processing is None
    assert legacy.quality is None
    assert legacy.lines == []
    assert legacy.reference_shift is None
    assert legacy.source_dir == ""
    assert _json_round_trip(legacy) == legacy


def test_backward_compatible_import_paths() -> None:
    import acp.nmr
    import acp.nmr.spectra

    assert acp.nmr.spectra.ProcessedSpectrum is ProcessedSpectrum
    assert acp.nmr.ProcessedSpectrum is ProcessedSpectrum


# ---------------------------------------------------------------------------
# Missing provenance fails explicitly
# ---------------------------------------------------------------------------


def test_processing_provenance_from_dict_rejects_missing_field() -> None:
    payload = _sample_processing().to_dict()
    payload.pop("apodization")
    with pytest.raises(ValueError, match="apodization"):
        ProcessingProvenance.from_dict(payload)


def test_acquisition_from_dict_rejects_missing_field() -> None:
    payload = _sample_acquisition().to_dict()
    payload.pop("spectrometer")
    with pytest.raises(ValueError, match="spectrometer"):
        AcquisitionSpectrum.from_dict(payload)


def test_processed_spectrum_from_dict_rejects_missing_provenance_layer() -> None:
    payload = _sample_processed().to_dict()
    payload.pop("processing")
    with pytest.raises(ValueError, match="processing"):
        ProcessedSpectrum.from_dict(payload)


def test_pipeline_provenance_blocks_are_independent_records() -> None:
    """acquiring ≠ processing: the acquisition record never carries
    processing decisions and vice versa (single definition per concept)."""
    acquisition_fields = {f.name for f in fields(AcquisitionSpectrum)}
    processing_fields = {f.name for f in fields(ProcessingProvenance)}
    assert "lb_hz" not in acquisition_fields
    assert "reference_method" not in acquisition_fields
    assert "spectrometer" not in processing_fields
    assert "temperature_k" not in processing_fields


# ---------------------------------------------------------------------------
# Resonances exist only through assignment
# ---------------------------------------------------------------------------


def test_unmatched_raw_peak_never_becomes_resonance() -> None:
    raw = ExperimentalPeak(shift_ppm=1.0, element="H", atom_label=None, index=0)
    with pytest.raises(ValueError, match="unassigned"):
        ResonanceSignal.from_experimental_peak(raw)
    assert resonance_signals_from_peaks([raw]) == []


def test_ambiguous_peak_never_becomes_resonance() -> None:
    ambiguous = ExperimentalPeak(
        shift_ppm=5.0,
        element="H",
        label_candidates=("H32", "H33"),
        index=0,
    )
    assert not ambiguous.assigned
    assert resonance_signals_from_peaks([ambiguous]) == []


def test_assigned_peak_becomes_resonance_with_refs() -> None:
    assigned = ExperimentalPeak(
        shift_ppm=1.0,
        element="H",
        atom_label="H1",
        label_candidates=("H1",),
        multiplicity=3,
        index=1,
    )
    (signal,) = resonance_signals_from_peaks([assigned])
    assert signal.shift_ppm == 1.0
    assert signal.element == "H"
    assert signal.multiplicity == 3
    assert signal.atom_refs == ("H1",)
    assert signal.group_refs == ()


def test_resonance_creation_mixed_peaks_keeps_only_assigned() -> None:
    raw = ExperimentalPeak(shift_ppm=9.0, element="H", index=0)
    assigned = ExperimentalPeak(shift_ppm=1.0, element="H", atom_label="H1", index=1)
    ambiguous = ExperimentalPeak(
        shift_ppm=5.0, element="H", label_candidates=("H32", "H33"), index=2
    )
    signals = resonance_signals_from_peaks([raw, assigned, ambiguous])
    assert [s.shift_ppm for s in signals] == [1.0]


def test_resonance_group_refs_are_carried() -> None:
    assigned = ExperimentalPeak(shift_ppm=1.0, element="H", atom_label="H1")
    signal = ResonanceSignal.from_experimental_peak(assigned, group_refs=("sg:H1,H2",))
    assert signal.group_refs == ("sg:H1,H2",)


# ---------------------------------------------------------------------------
# Legacy ExperimentalNmr read path unchanged
# ---------------------------------------------------------------------------


def test_legacy_experimental_nmr_construction_unchanged() -> None:
    exp = ExperimentalNmr(
        peaks={"H": [ExperimentalPeak(shift_ppm=1.0, element="H")]},
        equivalence_groups=[["H1", "H2"]],
        omit_atoms=["H3"],
        assigned=False,
    )
    assert exp.nuclei() == ["H"]
    assert exp.peaks_for("H")[0].shift_ppm == 1.0
    assert exp.assignment_counts() == {"H": (0, 1)}
    assert exp.assigned_nuclei == []
    assert exp.parse_errors == []
    # Positional legacy signature (peaks, equivalence_groups, omit_atoms, assigned).
    legacy = ExperimentalNmr({}, [], [], False)
    assert legacy.peaks == {}
    assert legacy.assigned is False


# ---------------------------------------------------------------------------
# Real pipeline: layers populated from the actual processing chain
# ---------------------------------------------------------------------------


def _write_bruker_experiment(
    root: Path,
    nucleus: str,
    bf1_mhz: float,
    peaks: list[tuple[float, float, float]],
    sw_ppm: float,
    o1_ppm: float,
    td: int = 16384,
    noise: float = 0.0002,
    seed: int = 7,
    solvent: str = "CDCl3",
    temperature: float = 298.0,
    pulse_program: str = "zg30",
) -> Path:
    """Synthetic Bruker experiment with full acqus provenance fields."""
    sw_hz = sw_ppm * bf1_mhz
    t = np.arange(td) / sw_hz
    fid = np.zeros(td, dtype=complex)
    for ppm, amp, r2 in peaks:
        nu = (o1_ppm - ppm) * bf1_mhz  # nmrglue/Bruker sign convention
        fid += amp * np.exp(2j * np.pi * nu * t) * np.exp(-np.pi * r2 * t)
    rng = np.random.default_rng(seed)
    fid += rng.normal(0, noise, td) + 1j * rng.normal(0, noise, td)
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
        f"##$NUC1= <{nucleus}>\n"
        f"##$SOLVENT= <{solvent}>\n"
        f"##$TE= {temperature}\n"
        f"##$PULPROG= <{pulse_program}>\n"
        "##$GRPDLY= 0.0\n"
        "##$SR= 0.0\n"
        "##$BYTORDA= 0\n"
        "##$DTYPA= 0\n"
        "##$AQ_mod= 1\n"
        "##$DECIM= 1\n"
        "##$DSPFVS= 0\n"
        "##END=\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture()
def proton_dir(tmp_path: Path) -> Path:
    return _write_bruker_experiment(
        tmp_path / "Proton",
        nucleus="1H",
        bf1_mhz=500.13,
        peaks=[(7.12, 1.0, 5.0), (3.50, 2.0, 8.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
    )


@requires_nmrglue
def test_pipeline_populates_acquisition_layer(proton_dir: Path) -> None:
    from acp.nmr.spectra import process_bruker_experiment

    result = process_bruker_experiment(proton_dir, reference_ppm=7.26)
    acquisition = result.acquisition
    assert acquisition is not None
    assert acquisition.spectrometer == "Bruker"
    assert acquisition.nucleus == "1H"
    assert acquisition.frequency_mhz == pytest.approx(500.13)
    assert acquisition.solvent == "CDCl3"
    assert acquisition.temperature_k == pytest.approx(298.0)
    assert acquisition.pulse_program == "zg30"
    assert acquisition.point_count == 16384
    assert acquisition.spectral_width_hz == pytest.approx(5001.3)
    assert acquisition.carrier_ppm == pytest.approx(5.0)
    assert acquisition.group_delay_points == pytest.approx(0.0)
    assert acquisition.spectrometer_reference == pytest.approx(0.0)
    assert acquisition.source_dir == str(proton_dir)


@requires_nmrglue
def test_pipeline_populates_processing_provenance(proton_dir: Path) -> None:
    from acp.nmr.spectra import process_bruker_experiment

    result = process_bruker_experiment(proton_dir, reference_ppm=7.26)
    processing = result.processing
    assert processing is not None
    assert processing.apodization == "exponential"
    assert processing.lb_hz == pytest.approx(0.3)
    assert processing.zero_fill_points is not None
    assert processing.zero_fill_points > 16384
    assert processing.zero_fill_factor == pytest.approx(processing.zero_fill_points / 16384)
    assert processing.phase_method in ("peak_minima", "acme", "unphased")
    assert processing.baseline_method == "morphological_grey_opening"
    assert processing.baseline_window_fraction == pytest.approx(0.02)
    assert processing.reference_method == "manual_anchor"
    assert processing.reference_ppm == pytest.approx(7.26)
    assert processing.applied_shift_ppm == pytest.approx(0.14, abs=0.02)


@requires_nmrglue
def test_pipeline_reference_method_without_manual_reference(proton_dir: Path) -> None:
    from acp.nmr.spectra import process_bruker_experiment

    result = process_bruker_experiment(proton_dir)
    assert result.processing is not None
    assert result.processing.reference_method == "spectrometer_sr"
    assert result.processing.reference_ppm is None
    assert result.processing.applied_shift_ppm is None


@requires_nmrglue
def test_pipeline_populates_quality_metrics_and_lines(proton_dir: Path) -> None:
    from acp.nmr.spectra import process_bruker_experiment

    result = process_bruker_experiment(proton_dir)
    quality = result.quality
    assert quality is not None
    assert quality.snr is not None and quality.snr > 5.0
    assert quality.linewidth_hz is not None and 1.0 < quality.linewidth_hz < 20.0
    assert quality.baseline_rms is not None and quality.baseline_rms > 0.0
    assert len(result.lines) == len(result.peaks)
    assert [line.position_ppm for line in result.lines] == [peak.shift_ppm for peak in result.peaks]
    assert all(line.intensity > 0.0 for line in result.lines)
    assert all(line.integral is not None for line in result.lines)


@requires_nmrglue
def test_pipeline_product_round_trips_exactly(proton_dir: Path) -> None:
    from acp.nmr.spectra import process_bruker_experiment

    result = process_bruker_experiment(proton_dir, reference_ppm=7.26)
    assert _json_round_trip(result) == result


@requires_nmrglue
def test_bruker_peaks_text_consumer_unchanged(proton_dir: Path) -> None:
    from acp.nmr.spectra import bruker_result_to_text, process_bruker_tree

    result = process_bruker_tree(proton_dir)
    text = bruker_result_to_text(result)
    assert "H:" in text
    assert len(result.experiment.peaks["H"]) == 2
    assert not result.experiment.assigned
