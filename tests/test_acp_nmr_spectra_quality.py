"""Processing quality gates: phase / digital filter / reference (todo 42 / G10).

Gap G10: the Bruker processing chain used to continue silently after a
failed auto-phase, silently keep spectrometer referencing when a requested
manual reference could not be anchored, and never made the digital-filter
(group delay) effect visible. This module pins the deterministic gate:

* verdict vocabulary ``ok | degraded | failed`` with closed reason codes;
* ``phase_failed`` is a HARD failure — an unphased spectrum never feeds the
  formal probability path (``process_bruker_tree`` excludes it, and a
  spectrum whose assessment is failed is never ``formal_usable``);
* a requested-but-unapplied manual reference is an explicit ``degraded``
  marker (``reference_not_applied``), no longer silence; the T41-flagged
  combination ``reference_method == "spectrometer_sr"`` + a requested
  ``reference_ppm`` is exactly this state;
* the digital-filter check reads ``group_delay_points``/``dspfvs`` and
  NEVER claims the filter effect was verified without a real instrument
  fixture (metadata-only => ``unverified`` / ``not_compensated``);
* the verdict + quality metrics surface additively in ``nmr_report.json``
  (``processing_quality`` key; no existing key renamed).

Gate logic is exercised WITHOUT nmrglue wherever possible; the real
pipeline cases are nmrglue-gated in-file (mirroring
``test_acp_nmr_spectra_layers.py``), and the real-instrument group-delay
validation is explicitly ``NOT_VERIFIED`` while no real dataset ships.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from acp.nmr.models import (
    DIGITAL_FILTER_STATUSES,
    PROCESSING_REASONS,
    PROCESSING_STATUSES,
    AcquisitionSpectrum,
    ExperimentalPeak,
    NmrReport,
    ProcessedSpectrum,
    ProcessingAssessment,
    ProcessingProvenance,
    ProcessingQuality,
    assess_processing,
    check_digital_filter,
)
from acp.nmr.report import (
    PROCESSING_QUALITY_KEY,
    processing_quality_records,
    write_json_report,
)
from tests.conftest import NOT_VERIFIED

# Bruker pipeline cases need the optional nmrglue capability; every
# gate-logic case below runs without it.
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
# Record builders (no nmrglue required)
# ---------------------------------------------------------------------------


def _processing(
    *,
    phase_method: str = "peak_minima",
    reference_method: str = "spectrometer_sr",
    reference_ppm: float | None = None,
    applied_shift_ppm: float | None = None,
    digital_filter_compensation: str | None = "none",
) -> ProcessingProvenance:
    return ProcessingProvenance(
        apodization="exponential",
        phase_method=phase_method,
        baseline_method="morphological_grey_opening",
        reference_method=reference_method,
        reference_ppm=reference_ppm,
        applied_shift_ppm=applied_shift_ppm,
        digital_filter_compensation=digital_filter_compensation,
    )


def _acquisition(
    *,
    group_delay_points: float | None = 0.0,
    dspfvs: int | None = 0,
) -> AcquisitionSpectrum:
    return AcquisitionSpectrum(
        spectrometer="Bruker",
        nucleus="1H",
        group_delay_points=group_delay_points,
        dspfvs=dspfvs,
    )


def _spectrum(
    *,
    assessment: ProcessingAssessment | None = None,
    processing: ProcessingProvenance | None = None,
    acquisition: AcquisitionSpectrum | None = None,
    quality: ProcessingQuality | None = None,
    source_dir: str = "/data/sample/1",
) -> ProcessedSpectrum:
    return ProcessedSpectrum(
        nucleus="1H",
        element="H",
        peaks=[ExperimentalPeak(shift_ppm=1.0, element="H", index=0)],
        noise=1e-04,
        source_dir=source_dir,
        acquisition=acquisition,
        processing=processing,
        quality=quality,
        assessment=assessment,
    )


# ---------------------------------------------------------------------------
# Verdict vocabulary + determinism
# ---------------------------------------------------------------------------


def test_verdict_vocabulary_is_closed() -> None:
    assert PROCESSING_STATUSES == ("ok", "degraded", "failed")
    assert "phase_failed" in PROCESSING_REASONS
    assert "reference_not_applied" in PROCESSING_REASONS
    assert "digital_filter_unverified" in PROCESSING_REASONS
    assert set(DIGITAL_FILTER_STATUSES) == {
        "not_applicable",
        "unverified",
        "not_compensated",
        "compensated",
    }


def test_assess_processing_returns_none_without_provenance() -> None:
    """No provenance recorded => no verdict invented (legacy spectra)."""
    assert assess_processing(None) is None
    legacy = _spectrum()
    assert legacy.assessment is None
    assert legacy.formal_usable is True


def test_unphased_phase_is_failed_and_not_formal_usable() -> None:
    """A failed auto-phase is a HARD failure, never a normal spectrum."""
    processing = _processing(phase_method="unphased")
    assessment = assess_processing(processing, _acquisition())
    assert assessment is not None
    assert assessment.status == "failed"
    assert assessment.reasons == ("phase_failed",)
    assert not assessment.is_ok
    assert assessment.is_failed
    assert not assessment.formal_usable
    spectrum = _spectrum(assessment=assessment, processing=processing)
    assert spectrum.formal_usable is False


def test_healthy_phase_is_ok_and_formal_usable() -> None:
    processing = _processing()
    assessment = assess_processing(processing, _acquisition())
    assert assessment is not None
    assert assessment.status == "ok"
    assert assessment.reasons == ()
    assert assessment.is_ok
    assert assessment.formal_usable
    assert _spectrum(assessment=assessment, processing=processing).formal_usable


def test_phase_method_alone_never_assumes_failure_or_success() -> None:
    """Only the explicit ``unphased`` sentinel fails; other methods pass."""
    for method in ("peak_minima", "acme", "manual", "none"):
        assessment = assess_processing(_processing(phase_method=method), _acquisition())
        assert assessment is not None and assessment.status == "ok", method


# ---------------------------------------------------------------------------
# Reference gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reference_ppm",
    [7.26, 0.0],
)
def test_reference_request_not_applied_is_degraded(reference_ppm: float) -> None:
    """Requested manual reference that was NOT anchored => explicit marker.

    This is the T41-noted combination: ``reference_method ==
    "spectrometer_sr"`` while a ``reference_ppm`` request exists — either no
    peak was picked at all or no peak fell inside the search window. Both
    are the same determinate state; silence is no longer an option.
    """
    processing = _processing(
        reference_method="spectrometer_sr",
        reference_ppm=reference_ppm,
        applied_shift_ppm=None,
    )
    assessment = assess_processing(processing, _acquisition())
    assert assessment is not None
    assert assessment.status == "degraded"
    assert assessment.reasons == ("reference_not_applied",)
    assert assessment.formal_usable  # peaks stay usable, but marked


def test_reference_applied_is_ok() -> None:
    processing = _processing(
        reference_method="manual_anchor",
        reference_ppm=7.26,
        applied_shift_ppm=0.14,
    )
    assessment = assess_processing(processing, _acquisition())
    assert assessment is not None
    assert assessment.status == "ok"
    assert assessment.reasons == ()


def test_no_reference_request_is_ok() -> None:
    processing = _processing(reference_method="spectrometer_sr", reference_ppm=None)
    assessment = assess_processing(processing, _acquisition())
    assert assessment is not None
    assert assessment.status == "ok"
    assert assessment.reasons == ()


# ---------------------------------------------------------------------------
# Digital filter / group delay check
# ---------------------------------------------------------------------------


def test_no_digital_filter_declared_is_not_applicable() -> None:
    check = check_digital_filter(_acquisition(group_delay_points=0.0, dspfvs=0), _processing())
    assert check is not None
    assert check.filter_present is False
    assert check.status == "not_applicable"
    assessment = assess_processing(_processing(), _acquisition(group_delay_points=0.0))
    assert assessment is not None and assessment.status == "ok"


def test_digital_filter_present_uncompensated_is_degraded() -> None:
    """GRPDLY > 0 with explicit no-compensation => visible degradation.

    The group-delay effect is NOT claimed to be absent: the pipeline never
    applied a compensation step, so the verdict is ``not_compensated``.
    """
    acquisition = _acquisition(group_delay_points=67.986, dspfvs=12)
    processing = _processing(digital_filter_compensation="none")
    check = check_digital_filter(acquisition, processing)
    assert check is not None
    assert check.filter_present is True
    assert check.status == "not_compensated"
    assert check.group_delay_points == pytest.approx(67.986)
    assert check.dspfvs == 12
    assert check.compensation == "none"
    assessment = assess_processing(processing, acquisition)
    assert assessment is not None
    assert assessment.status == "degraded"
    assert assessment.reasons == ("digital_filter_unverified",)
    assert assessment.formal_usable


def test_digital_filter_unknown_compensation_is_unverified() -> None:
    """Legacy provenance (no compensation record) is conservative: unverified."""
    acquisition = _acquisition(group_delay_points=67.986, dspfvs=12)
    processing = _processing(digital_filter_compensation=None)
    check = check_digital_filter(acquisition, processing)
    assert check is not None
    assert check.status == "unverified"
    assessment = assess_processing(processing, acquisition)
    assert assessment is not None
    assert assessment.reasons == ("digital_filter_unverified",)


def test_digital_filter_missing_grpdly_falls_back_to_dsp_firmware() -> None:
    """GRPDLY absent + DSP firmware => conservative unverified, not silence."""
    acquisition = _acquisition(group_delay_points=None, dspfvs=12)
    check = check_digital_filter(acquisition, _processing(digital_filter_compensation=None))
    assert check is not None
    assert check.filter_present is True
    assert check.status == "unverified"
    assessment = assess_processing(_processing(digital_filter_compensation=None), acquisition)
    assert assessment is not None
    assert assessment.status == "degraded"
    assert assessment.reasons == ("digital_filter_unverified",)


def test_digital_filter_missing_both_metadata_is_not_applicable() -> None:
    acquisition = _acquisition(group_delay_points=None, dspfvs=None)
    check = check_digital_filter(acquisition, _processing())
    assert check is not None
    assert check.filter_present is False
    assert check.status == "not_applicable"


def test_digital_filter_explicit_compensation_is_compensated() -> None:
    """A recorded compensation step is the only way to claim compensated."""
    acquisition = _acquisition(group_delay_points=67.986, dspfvs=12)
    processing = _processing(digital_filter_compensation="group_delay_removal")
    check = check_digital_filter(acquisition, processing)
    assert check is not None
    assert check.status == "compensated"
    assessment = assess_processing(processing, acquisition)
    assert assessment is not None
    assert assessment.status == "ok"


def test_digital_filter_unknown_compensation_string_is_unverified() -> None:
    acquisition = _acquisition(group_delay_points=67.986, dspfvs=12)
    processing = _processing(digital_filter_compensation="magic")
    check = check_digital_filter(acquisition, processing)
    assert check is not None
    assert check.status == "unverified"


def test_check_digital_filter_returns_none_without_acquisition() -> None:
    assert check_digital_filter(None, _processing()) is None
    assert check_digital_filter(None, None) is None


# ---------------------------------------------------------------------------
# Combined reasons + validation
# ---------------------------------------------------------------------------


def test_all_three_reasons_combine_with_canonical_order() -> None:
    processing = _processing(
        phase_method="unphased",
        reference_method="spectrometer_sr",
        reference_ppm=7.26,
        digital_filter_compensation=None,
    )
    acquisition = _acquisition(group_delay_points=67.986, dspfvs=12)
    assessment = assess_processing(processing, acquisition)
    assert assessment is not None
    assert assessment.status == "failed"
    assert assessment.reasons == (
        "phase_failed",
        "reference_not_applied",
        "digital_filter_unverified",
    )
    assert not assessment.formal_usable


def test_assessment_rejects_inconsistent_status() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        ProcessingAssessment(status="ok", reasons=("phase_failed",))
    with pytest.raises(ValueError, match="inconsistent"):
        ProcessingAssessment(status="failed", reasons=())
    with pytest.raises(ValueError, match="inconsistent"):
        ProcessingAssessment(status="degraded", reasons=())


def test_assessment_rejects_unknown_vocabulary() -> None:
    with pytest.raises(ValueError, match="unknown processing status"):
        ProcessingAssessment(status="maybe", reasons=())
    with pytest.raises(ValueError, match="unknown processing reason"):
        ProcessingAssessment(status="degraded", reasons=("mystery",))


def test_assessment_dedupes_and_canonicalizes_reasons() -> None:
    assessment = ProcessingAssessment(
        status="degraded",
        reasons=("digital_filter_unverified", "reference_not_applied", "reference_not_applied"),
    )
    assert assessment.reasons == ("reference_not_applied", "digital_filter_unverified")


# ---------------------------------------------------------------------------
# Serialization round-trips (additive fields keep T41 semantics)
# ---------------------------------------------------------------------------


def test_processing_assessment_to_dict_covers_fields_and_round_trips() -> None:
    assessment = ProcessingAssessment(status="failed", reasons=("phase_failed",))
    payload = json.loads(json.dumps(assessment.to_dict()))
    assert set(payload) == {"status", "reasons"}
    assert ProcessingAssessment.from_dict(payload) == assessment


def test_digital_filter_check_to_dict_covers_fields_and_round_trips() -> None:
    check = check_digital_filter(_acquisition(group_delay_points=67.986, dspfvs=12), _processing())
    assert check is not None
    payload = json.loads(json.dumps(check.to_dict()))
    assert set(payload) == {
        "status",
        "filter_present",
        "group_delay_points",
        "dspfvs",
        "compensation",
    }
    from acp.nmr.models import DigitalFilterCheck

    assert DigitalFilterCheck.from_dict(payload) == check


def test_processed_spectrum_round_trip_keeps_assessment() -> None:
    processing = _processing(phase_method="unphased")
    assessment = assess_processing(processing, _acquisition(group_delay_points=0.0))
    spectrum = _spectrum(
        assessment=assessment,
        processing=processing,
        quality=ProcessingQuality(snr=42.0),
    )
    restored = ProcessedSpectrum.from_dict(json.loads(json.dumps(spectrum.to_dict())))
    assert restored == spectrum
    assert restored.assessment is not None
    assert restored.assessment.status == "failed"
    assert restored.processing is not None
    assert restored.processing.digital_filter_compensation == "none"


def test_digital_filter_compensation_round_trips_through_provenance() -> None:
    processing = _processing(digital_filter_compensation="group_delay_removal")
    restored = ProcessingProvenance.from_dict(processing.to_dict())
    assert restored == processing
    assert restored.digital_filter_compensation == "group_delay_removal"


# ---------------------------------------------------------------------------
# Tree gate without nmrglue: failed spectra never reach the formal peak list
# ---------------------------------------------------------------------------


def _dummy_experiment(root: Path, nucleus: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "acqus").write_text(f"##$NUC1= <{nucleus}>\n##END=\n", encoding="utf-8")
    (root / "fid").write_bytes(b"")
    return root


def _crafted_spectrum(
    nucleus: str, element: str, source_dir: str, assessment: ProcessingAssessment
):
    return ProcessedSpectrum(
        nucleus=nucleus,
        element=element,
        peaks=[ExperimentalPeak(shift_ppm=1.0, element=element, index=0)],
        noise=1e-04,
        source_dir=source_dir,
        processing=_processing(),
        acquisition=_acquisition(),
        assessment=assessment,
    )


def test_process_tree_excludes_failed_spectra_from_formal_peaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed (unphased) experiment is kept for inspection but excluded."""
    import acp.nmr.spectra as spectra_mod

    proton = _dummy_experiment(tmp_path / "01_unphased", "1H")
    _dummy_experiment(tmp_path / "02_phased", "13C")
    failed = ProcessingAssessment(status="failed", reasons=("phase_failed",))
    ok = ProcessingAssessment(status="ok", reasons=())

    def fake_process(exp_dir, reference_ppm=None, lb_hz=None, snr_threshold=None, **_kw):
        path = Path(exp_dir)
        if path == proton:
            return _crafted_spectrum("1H", "H", str(path), failed)
        return _crafted_spectrum("13C", "C", str(path), ok)

    monkeypatch.setattr(spectra_mod, "process_bruker_experiment", fake_process)
    result = spectra_mod.process_bruker_tree(tmp_path)

    assert set(result.experiment.peaks) == {"C"}  # failed 1H excluded
    assert [s.formal_usable for s in result.spectra] == [False, True]
    assert [s.assessment.status for s in result.spectra if s.assessment] == ["failed", "ok"]
    assert [s.element for s in result.formal_spectra] == ["C"]


def test_process_tree_raises_when_every_spectrum_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import acp.nmr.spectra as spectra_mod

    exp = _dummy_experiment(tmp_path / "01_unphased", "1H")
    failed = ProcessingAssessment(status="failed", reasons=("phase_failed",))

    def fake_process(exp_dir, reference_ppm=None, lb_hz=None, snr_threshold=None, **_kw):
        return _crafted_spectrum("1H", "H", str(exp_dir), failed)

    monkeypatch.setattr(spectra_mod, "process_bruker_experiment", fake_process)
    with pytest.raises(ValueError, match="picked no peaks"):
        spectra_mod.process_bruker_tree(tmp_path)
    assert exp.exists()


# ---------------------------------------------------------------------------
# Real pipeline (nmrglue-gated)
# ---------------------------------------------------------------------------


def _write_bruker_experiment(
    root: Path,
    *,
    nucleus: str,
    bf1_mhz: float,
    peaks: list[tuple[float, float, float]],
    sw_ppm: float,
    o1_ppm: float,
    td: int = 16384,
    noise: float = 0.0002,
    seed: int = 7,
    grpdly: float = 0.0,
    dspfvs: int = 0,
) -> Path:
    """Synthetic Bruker experiment with explicit GRPDLY/DSPFVS metadata."""
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
        f"##$GRPDLY= {grpdly}\n"
        f"##$DSPFVS= {dspfvs}\n"
        "##$BYTORDA= 0\n"
        "##$DTYPA= 0\n"
        "##$AQ_mod= 1\n"
        "##$DECIM= 1\n"
        "##END=\n",
        encoding="utf-8",
    )
    return root


def _proton_dir(tmp_path: Path, name: str = "Proton", **kwargs) -> Path:
    return _write_bruker_experiment(
        tmp_path / name,
        nucleus="1H",
        bf1_mhz=500.13,
        peaks=[(7.12, 1.0, 5.0), (3.50, 2.0, 8.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
        **kwargs,
    )


@requires_nmrglue
def test_pipeline_healthy_spectrum_records_ok_assessment(tmp_path: Path) -> None:
    from acp.nmr.spectra import process_bruker_experiment

    result = process_bruker_experiment(_proton_dir(tmp_path))
    assert result.assessment is not None
    assert result.assessment.status == "ok"
    assert result.assessment.reasons == ()
    assert result.formal_usable is True
    assert result.processing is not None
    assert result.processing.digital_filter_compensation == "none"


@requires_nmrglue
def test_pipeline_phase_failure_marks_failed_not_silent(tmp_path: Path) -> None:
    """Forced autops failure => failed verdict, never a normal spectrum."""
    from acp.nmr.spectra import process_bruker_experiment

    exp = _proton_dir(tmp_path)
    with patch(
        "nmrglue.process.proc_autophase.autops",
        side_effect=RuntimeError("forced autops failure"),
    ):
        result = process_bruker_experiment(exp)

    assert result.processing is not None
    assert result.processing.phase_method == "unphased"
    assert result.assessment is not None
    assert result.assessment.status == "failed"
    assert result.assessment.reasons == ("phase_failed",)
    assert result.formal_usable is False


@requires_nmrglue
def test_pipeline_phase_failure_excluded_from_tree(tmp_path: Path) -> None:
    """All experiments unphased => the tree refuses a formal peak list."""
    from acp.nmr.spectra import process_bruker_tree

    _proton_dir(tmp_path)
    with patch(
        "nmrglue.process.proc_autophase.autops",
        side_effect=RuntimeError("forced autops failure"),
    ):
        with pytest.raises(ValueError, match="picked no peaks"):
            process_bruker_tree(tmp_path)


@requires_nmrglue
def test_pipeline_tree_excludes_only_failed_experiment(tmp_path: Path) -> None:
    """Mixed tree: unphased experiment excluded, healthy one still formal."""
    from acp.nmr.spectra import process_bruker_tree

    _proton_dir(tmp_path, name="01_unphased")
    _write_bruker_experiment(
        tmp_path / "02_phased",
        nucleus="13C",
        bf1_mhz=125.76,
        peaks=[(160.0, 1.0, 3.0), (40.0, 1.0, 4.0)],
        sw_ppm=200.0,
        o1_ppm=100.0,
    )

    from nmrglue.process import proc_autophase

    real_autops = proc_autophase.autops
    calls = {"n": 0}

    def fake_autops(data, method):
        calls["n"] += 1
        if calls["n"] <= 2:  # both methods of the first (sorted) experiment
            raise RuntimeError("forced autops failure")
        return real_autops(data, method)

    with patch("nmrglue.process.proc_autophase.autops", side_effect=fake_autops):
        result = process_bruker_tree(tmp_path)

    statuses = [(s.nucleus, s.assessment.status if s.assessment else None) for s in result.spectra]
    assert statuses == [("1H", "failed"), ("13C", "ok")]
    assert set(result.experiment.peaks) == {"C"}
    assert not result.experiment.assigned


@requires_nmrglue
def test_pipeline_reference_unapplied_is_degraded_not_silent(tmp_path: Path) -> None:
    from acp.nmr.spectra import process_bruker_experiment

    result = process_bruker_experiment(_proton_dir(tmp_path), reference_ppm=5.55)
    assert result.reference_shift is None
    assert result.processing is not None
    assert result.processing.reference_method == "spectrometer_sr"
    assert result.processing.reference_ppm == pytest.approx(5.55)
    assert result.assessment is not None
    assert result.assessment.status == "degraded"
    assert result.assessment.reasons == ("reference_not_applied",)
    assert result.formal_usable is True  # peaks stay usable, but marked


@requires_nmrglue
def test_pipeline_reference_applied_records_ok(tmp_path: Path) -> None:
    from acp.nmr.spectra import process_bruker_experiment

    result = process_bruker_experiment(_proton_dir(tmp_path), reference_ppm=7.26)
    assert result.reference_shift is not None
    assert result.assessment is not None
    assert result.assessment.status == "ok"


@requires_nmrglue
def test_group_delay_fixture_detects_filter_and_stays_honest(tmp_path: Path) -> None:
    """Synthetic GRPDLY/DSPFVS fixture: detection works, effect NOT claimed absent.

    The fixture below is a faithful synthetic ``acqus`` (real DSP firmware
    values: GRPDLY 67.986 / DSPFVS 12). The pipeline applies no group-delay
    compensation, so the verdict must be ``not_compensated``/degraded — the
    metadata-only check never asserts the real filter effect was verified.
    """
    from acp.nmr.spectra import process_bruker_experiment

    filtered = _proton_dir(tmp_path, name="01_filtered", grpdly=67.986, dspfvs=12)
    result = process_bruker_experiment(filtered)

    assert result.acquisition is not None
    assert result.acquisition.group_delay_points == pytest.approx(67.986)
    assert result.acquisition.dspfvs == 12
    check = check_digital_filter(result.acquisition, result.processing)
    assert check is not None
    assert check.filter_present is True
    assert check.status == "not_compensated"
    assert result.assessment is not None
    assert result.assessment.status == "degraded"
    assert result.assessment.reasons == ("digital_filter_unverified",)
    # Peaks are still picked (the effect is marked, not silently dropped)...
    shifts = sorted(p.shift_ppm for p in result.peaks)
    assert shifts == pytest.approx([3.50, 7.12], abs=0.05)
    # ...and the compensation provenance is explicit, not unknown.
    assert result.processing is not None
    assert result.processing.digital_filter_compensation == "none"


_GROUP_DELAY_REAL_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bruker_real_group_delay"


@requires_nmrglue
def test_real_instrument_group_delay_dataset_not_verified() -> None:
    """A REAL instrument dataset confirms group-delay detection (G10).

    A real AVANCE NEO dataset (GRPDLY=76 / DSPFVS=21) ships at
    ``fixtures/bruker_real_group_delay/``. The pipeline applies no
    group-delay compensation, so the verdict stays ``not_compensated`` and
    the real filter effect remains NOT_VERIFIED — never silently claimed as
    verified. When a stripped checkout has no real dataset the test skips
    explicitly.
    """
    if not _GROUP_DELAY_REAL_FIXTURE.is_dir():
        pytest.skip(
            f"{NOT_VERIFIED}: no real instrument group-delay dataset at "
            f"{_GROUP_DELAY_REAL_FIXTURE}; synthetic metadata only — the real "
            "digital-filter effect is NOT verified"
        )
    from acp.nmr.spectra import process_bruker_experiment

    result = process_bruker_experiment(_GROUP_DELAY_REAL_FIXTURE)
    check = check_digital_filter(result.acquisition, result.processing)
    assert result.assessment is not None
    assert check is not None
    assert check.status in ("not_compensated", "unverified")
    assert result.processing is not None
    assert result.processing.digital_filter_compensation == "none"


# ---------------------------------------------------------------------------
# Report surface (additive JSON keys only)
# ---------------------------------------------------------------------------


def test_processing_quality_records_carry_gate_and_metrics() -> None:
    processing = _processing(reference_method="spectrometer_sr", reference_ppm=7.26)
    acquisition = _acquisition(group_delay_points=0.0)
    assessment = assess_processing(processing, acquisition)
    spectra = [
        _spectrum(
            assessment=assessment,
            processing=processing,
            acquisition=acquisition,
            quality=ProcessingQuality(snr=120.5, linewidth_hz=5.3, baseline_rms=2.5e-05),
            source_dir="/data/a",
        ),
        ProcessedSpectrum(nucleus="13C", element="C", peaks=[], noise=1e-05, source_dir="/data/b"),
    ]
    records = processing_quality_records(spectra)
    assert [r["status"] for r in records] == ["degraded", "unknown"]
    assert records[0]["reasons"] == ["reference_not_applied"]
    assert records[0]["formal_usable"] is True
    assert records[0]["phase_method"] == "peak_minima"
    assert records[0]["reference_method"] == "spectrometer_sr"
    assert records[0]["reference_ppm"] == pytest.approx(7.26)
    assert records[0]["quality"] == {
        "snr": 120.5,
        "linewidth_hz": 5.3,
        "baseline_rms": 2.5e-05,
    }
    assert records[0]["digital_filter"] == {
        "status": "not_applicable",
        "filter_present": False,
        "group_delay_points": 0.0,
        "dspfvs": 0,
        "compensation": "none",
    }
    assert records[1]["quality"] is None
    assert records[1]["digital_filter"] is None
    json.dumps(records)  # JSON-safe


def test_report_json_carries_processing_quality_additively(tmp_path: Path) -> None:
    records = [
        {
            "nucleus": "1H",
            "element": "H",
            "status": "failed",
            "reasons": ["phase_failed"],
            "formal_usable": False,
        }
    ]
    report = NmrReport(metadata={PROCESSING_QUALITY_KEY: records})
    path = write_json_report(report, tmp_path / "nmr_report.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload[PROCESSING_QUALITY_KEY] == records
    # schema v2 top-level keys are untouched (additive only — no renames)
    assert payload["schema_version"] == 2
    for key in ("summary", "candidates", "config", "error_model", "dp5_mode", "provenance"):
        assert key in payload
    assert payload["config"] == report.config.to_dict()
    # and the read-only legacy note classifier still sees a v2 payload
    from acp.nmr.report import report_validation_note

    assert report_validation_note(payload) is None


def test_report_json_processing_quality_is_null_without_run_context(tmp_path: Path) -> None:
    report = NmrReport()
    assert report.as_dict()[PROCESSING_QUALITY_KEY] is None
    path = write_json_report(report, tmp_path / "nmr_report.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[PROCESSING_QUALITY_KEY] is None
