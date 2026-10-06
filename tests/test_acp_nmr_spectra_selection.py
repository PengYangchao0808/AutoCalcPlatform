"""Multi-experiment selection + 2D rejection + per-nucleus registry (todo 45 / G10).

Gap G10: ``process_bruker_tree`` used to concatenate every same-nucleus
experiment's peaks blindly, 2D ``ser`` data was accepted by the 1D chain, and
there was no per-nucleus processor registry. This module pins the todo-45
contract:

* ``plan_experiment_selection`` selects exactly one experiment per nucleus by
  default (deterministic, first by label) and only combines same-nucleus
  experiments when the caller explicitly asks (``explicit_multi`` policy,
  recorded in the result next to which experiments fed which nucleus);
* a non-1D experiment (``ser`` file, ``acqu2s``/``acqu3s``, ``PARMODE`` != 0)
  is rejected with the typed ``Not1DExperimentError`` / closed ``not_1d``
  reason — never processed as 1D — and a non-selected 2D experiment surfaces
  per experiment in ``BrukerProcessResult.experiments`` (rejected, reason);
* ``acp.nmr.spectra_registry`` resolves the todo-43/44 carbon/proton
  processor descriptors lazily (importing the registry never imports the
  processor modules) and rejects unknown nuclei with a clear typed reason;
* the dense processed trace ``(ppm, intensity)`` is threaded through
  ``BrukerProcessResult.traces`` so the processors can consume it.

Registry / not-1D / selection-planning cases run WITHOUT nmrglue; only the
end-to-end tree cases are nmrglue-gated.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from acp.nmr.carbon_processor import PROCESSOR_ID as CARBON_PROCESSOR_ID
from acp.nmr.models import ExperimentalPeak, ProcessedSpectrum
from acp.nmr.proton_processor import PROCESSOR_ID as PROTON_PROCESSOR_ID
from acp.nmr.spectra import (
    NOT_1D_REASONS,
    ExperimentSelectionError,
    Not1DExperimentError,
    not_1d_reason,
    plan_experiment_selection,
    process_bruker_experiment,
    process_bruker_tree,
    spectrum_probe_nucleus,
)
from acp.nmr.spectra_registry import (
    NucleusProcessorError,
    lookup_processor,
    processor_for,
    processor_registry,
)

# Bruker pipeline cases need the optional nmrglue capability; every
# registry / detection / selection-plan case below runs without it.
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
# Fixture writers (dependency-light; nmrglue never needed for stubs)
# ---------------------------------------------------------------------------


def _write_stub_experiment(
    root: Path,
    *,
    nucleus: str = "1H",
    data_file: str = "fid",
    parmode: int = 0,
    acqu2s: bool = False,
) -> Path:
    """Write the minimal ``acqus`` + raw file selection probes need."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "acqus").write_text(
        f"##$NUC1= <{nucleus}>\n##$PARMODE= {parmode}\n##END=\n",
        encoding="utf-8",
    )
    (root / data_file).write_bytes(b"\x00" * 16)
    if acqu2s:
        (root / "acqu2s").write_text("##$NUC1= <1H>\n##END=\n", encoding="utf-8")
    return root


def _write_2d_experiment(root: Path, *, nucleus: str = "1H", parmode: int = 1) -> Path:
    """Write a 2D-like experiment directory (``ser`` serial file)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "acqus").write_text(
        f"##$TD= 8\n##$NUC1= <{nucleus}>\n##$PARMODE= {parmode}\n##END=\n",
        encoding="utf-8",
    )
    (root / "ser").write_bytes(b"\x00" * 32)
    return root


def _write_synthetic_experiment(
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
) -> Path:
    """Synthetic Bruker experiment (int32 FID + JCAMP acqus), no real data.

    ``peaks`` are ``(ppm, amplitude, R2 decay / Hz)`` triples; the FT peaks
    land at the requested ppm values (mirrors test_acp_nmr_spectra.py).
    """
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
        f"##$PARMODE= 0\n"
        "##$BYTORDA= 0\n"
        "##$DTYPA= 0\n"
        "##$AQ_mod= 1\n"
        "##$DECIM= 1\n"
        "##$DSPFVS= 0\n"
        "##$GRPDLY= 0.0\n"
        "##END=\n",
        encoding="utf-8",
    )
    return root


def _carbon_spectrum() -> ProcessedSpectrum:
    return ProcessedSpectrum(
        nucleus="13C",
        element="C",
        peaks=[ExperimentalPeak(shift_ppm=40.0, element="C", index=0)],
        noise=1e-4,
    )


def _proton_spectrum() -> ProcessedSpectrum:
    return ProcessedSpectrum(
        nucleus="1H",
        element="H",
        peaks=[ExperimentalPeak(shift_ppm=3.5, element="H", index=0)],
        noise=1e-4,
    )


# ---------------------------------------------------------------------------
# Per-nucleus processor registry
# ---------------------------------------------------------------------------


def test_registry_resolves_carbon_and_proton_descriptors() -> None:
    registry = processor_registry()
    assert set(registry) == {"C", "H"}
    carbon = registry["C"]
    proton = registry["H"]
    assert (carbon.element, carbon.nucleus_label) == ("C", "13C")
    assert carbon.processor_id == CARBON_PROCESSOR_ID
    assert (proton.element, proton.nucleus_label) == ("H", "1H")
    assert proton.processor_id == PROTON_PROCESSOR_ID
    # Nucleus labels and elements resolve to the same descriptor.
    assert lookup_processor("13C") == carbon
    assert lookup_processor("c") == carbon
    assert lookup_processor("1H") == proton
    assert lookup_processor("h") == proton
    assert processor_for("13C") == carbon


def test_registry_unknown_nucleus_is_none_and_typed() -> None:
    assert lookup_processor("N") is None
    assert lookup_processor("29Si") is None
    assert lookup_processor("") is None
    with pytest.raises(NucleusProcessorError) as excinfo:
        processor_for("15N")
    assert excinfo.value.reason == "unknown_nucleus"
    assert isinstance(excinfo.value, ValueError)


def test_registry_module_is_lazy_about_processor_imports() -> None:
    """Importing the registry must not import the processor modules."""
    src_root = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_root}{os.pathsep}{existing}" if existing else str(src_root)
    code = (
        "import sys\n"
        "import acp.nmr.spectra_registry as registry\n"
        "assert 'acp.nmr.carbon_processor' not in sys.modules, 'carbon imported eagerly'\n"
        "assert 'acp.nmr.proton_processor' not in sys.modules, 'proton imported eagerly'\n"
        "descriptor = registry.lookup_processor('13C')\n"
        "assert descriptor is not None and descriptor.processor_id\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr


def test_descriptor_process_dispatches_trace_to_accepting_processors() -> None:
    carbon = lookup_processor("C")
    proton = lookup_processor("H")
    assert carbon is not None and proton is not None

    carbon_result = carbon.process(_carbon_spectrum())
    assert carbon_result.processor_id == CARBON_PROCESSOR_ID
    trace = (np.array([45.0, 40.0, 35.0]), np.array([1.0, 2.0, 1.0]))
    assert carbon.process(_carbon_spectrum(), trace=trace).processor_id == CARBON_PROCESSOR_ID

    proton_result = proton.process(_proton_spectrum())
    assert proton_result.processor_id == PROTON_PROCESSOR_ID
    with pytest.raises(NucleusProcessorError) as excinfo:
        proton.process(_proton_spectrum(), trace=trace)
    assert excinfo.value.reason == "trace_not_supported"


# ---------------------------------------------------------------------------
# 2D / non-1D detection
# ---------------------------------------------------------------------------


def test_not_1d_reason_vocabulary_is_closed() -> None:
    assert NOT_1D_REASONS == ("ser_file", "acqu2s_present", "parmode_not_1d")


def test_not_1d_reason_detects_ser_acqu2s_and_parmode(tmp_path: Path) -> None:
    assert not_1d_reason(_write_stub_experiment(tmp_path / "clean")) is None
    assert not_1d_reason(_write_stub_experiment(tmp_path / "ser", data_file="ser")) == "ser_file"
    assert not_1d_reason(_write_stub_experiment(tmp_path / "acq2", acqu2s=True)) == "acqu2s_present"
    assert (
        not_1d_reason(_write_stub_experiment(tmp_path / "parmode", parmode=1)) == "parmode_not_1d"
    )


def test_process_bruker_experiment_rejects_2d_before_nmrglue(tmp_path: Path) -> None:
    """The 2D gate fires before any nmrglue work (works without nmrglue)."""
    ser_dir = _write_2d_experiment(tmp_path / "2d")
    with pytest.raises(Not1DExperimentError) as excinfo:
        process_bruker_experiment(ser_dir)
    assert excinfo.value.reason == "ser_file"
    assert str(ser_dir) in str(excinfo.value)
    assert isinstance(excinfo.value, ValueError)


# ---------------------------------------------------------------------------
# Selection planning (no nmrglue required)
# ---------------------------------------------------------------------------


def test_default_selection_picks_one_deterministically_and_does_not_combine(
    tmp_path: Path,
) -> None:
    first = _write_stub_experiment(tmp_path / "1")
    second = _write_stub_experiment(tmp_path / "2")
    plan = plan_experiment_selection([first, second], root=tmp_path)
    assert plan.selected_labels == ("1",)
    assert len(plan.nuclei) == 1
    selection = plan.nuclei[0]
    assert selection.element == "H"
    assert selection.nucleus_label == "1H"
    assert selection.selected_labels == ("1",)
    assert selection.policy == "default_deterministic"
    assert selection.combined is False
    assert selection.processor_id == PROTON_PROCESSOR_ID
    by_label = {record.label: record for record in plan.experiments}
    assert by_label["1"].status == "selected"
    assert by_label["1"].user_requested is False
    assert by_label["2"].status == "not_selected"
    assert by_label["2"].reason == "default_deterministic"


def test_default_single_experiment_uses_single_policy(tmp_path: Path) -> None:
    _write_stub_experiment(tmp_path / "Proton")
    plan = plan_experiment_selection([tmp_path / "Proton"], root=tmp_path)
    assert plan.nuclei[0].policy == "single"
    assert plan.nuclei[0].selected_labels == ("Proton",)


def test_explicit_single_selection_marks_user_requested(tmp_path: Path) -> None:
    _write_stub_experiment(tmp_path / "1")
    _write_stub_experiment(tmp_path / "2")
    plan = plan_experiment_selection(
        [tmp_path / "1", tmp_path / "2"], root=tmp_path, select_experiments={"1H": "2"}
    )
    assert plan.selected_labels == ("2",)
    assert plan.nuclei[0].policy == "explicit_single"
    assert plan.nuclei[0].combined is False
    by_label = {record.label: record for record in plan.experiments}
    assert by_label["2"].status == "selected"
    assert by_label["2"].user_requested is True
    assert by_label["1"].status == "not_selected"
    assert by_label["1"].reason == "not_requested"


def test_explicit_multi_selection_combines_and_records_policy(tmp_path: Path) -> None:
    _write_stub_experiment(tmp_path / "1")
    _write_stub_experiment(tmp_path / "2")
    plan = plan_experiment_selection(
        [tmp_path / "1", tmp_path / "2"], root=tmp_path, select_experiments={"H": ["1", "2"]}
    )
    assert plan.selected_labels == ("1", "2")
    selection = plan.nuclei[0]
    assert selection.policy == "explicit_multi"
    assert selection.combined is True
    assert selection.selected_labels == ("1", "2")
    assert all(record.status == "selected" for record in plan.experiments)


def test_explicit_selection_unknown_label_raises_typed(tmp_path: Path) -> None:
    _write_stub_experiment(tmp_path / "1")
    _write_stub_experiment(tmp_path / "2")
    with pytest.raises(ExperimentSelectionError) as excinfo:
        plan_experiment_selection(
            [tmp_path / "1", tmp_path / "2"],
            root=tmp_path,
            select_experiments={"1H": "nope"},
        )
    assert excinfo.value.reason == "unknown_experiment"
    assert "'1'" in str(excinfo.value) and "'2'" in str(excinfo.value)


def test_selection_for_absent_nucleus_raises_typed(tmp_path: Path) -> None:
    _write_stub_experiment(tmp_path / "1")
    with pytest.raises(ExperimentSelectionError) as excinfo:
        plan_experiment_selection([tmp_path / "1"], root=tmp_path, select_experiments={"13C": "1"})
    assert excinfo.value.reason == "unknown_nucleus"


def test_selection_of_other_nucleus_experiment_raises_nucleus_mismatch(tmp_path: Path) -> None:
    _write_stub_experiment(tmp_path / "proton", nucleus="1H")
    _write_stub_experiment(tmp_path / "carbon", nucleus="13C")
    with pytest.raises(ExperimentSelectionError) as excinfo:
        plan_experiment_selection(
            [tmp_path / "proton", tmp_path / "carbon"],
            root=tmp_path,
            select_experiments={"13C": "proton"},
        )
    assert excinfo.value.reason == "nucleus_mismatch"


def test_selection_duplicate_element_keys_raise_typed(tmp_path: Path) -> None:
    _write_stub_experiment(tmp_path / "1")
    _write_stub_experiment(tmp_path / "2")
    with pytest.raises(ExperimentSelectionError) as excinfo:
        plan_experiment_selection(
            [tmp_path / "1", tmp_path / "2"],
            root=tmp_path,
            select_experiments={"1H": "1", "H": "2"},
        )
    assert excinfo.value.reason == "duplicate_experiment"


def test_ambiguous_basename_selection_raises_typed(tmp_path: Path) -> None:
    _write_stub_experiment(tmp_path / "a" / "1")
    _write_stub_experiment(tmp_path / "b" / "1")
    with pytest.raises(ExperimentSelectionError) as excinfo:
        plan_experiment_selection(
            [tmp_path / "a" / "1", tmp_path / "b" / "1"],
            root=tmp_path,
            select_experiments={"1H": "1"},
        )
    assert excinfo.value.reason == "ambiguous_experiment"


def test_2d_experiment_surfaces_rejected_in_plan(tmp_path: Path) -> None:
    _write_2d_experiment(tmp_path / "1")
    _write_stub_experiment(tmp_path / "2")
    plan = plan_experiment_selection([tmp_path / "1", tmp_path / "2"], root=tmp_path)
    assert plan.selected_labels == ("2",)
    by_label = {record.label: record for record in plan.experiments}
    assert by_label["1"].status == "rejected"
    assert by_label["1"].reason == "ser_file"
    assert by_label["2"].status == "selected"
    assert plan.nuclei[0].policy == "single"


def test_all_2d_tree_raises_typed_before_processing(tmp_path: Path) -> None:
    _write_2d_experiment(tmp_path / "2d")
    with pytest.raises(Not1DExperimentError) as excinfo:
        process_bruker_tree(tmp_path)
    assert excinfo.value.reason == "ser_file"


def test_explicit_selection_of_2d_raises_typed(tmp_path: Path) -> None:
    _write_2d_experiment(tmp_path / "1")
    _write_stub_experiment(tmp_path / "2")
    with pytest.raises(Not1DExperimentError) as excinfo:
        plan_experiment_selection(
            [tmp_path / "1", tmp_path / "2"], root=tmp_path, select_experiments={"1H": "1"}
        )
    assert excinfo.value.reason == "ser_file"


# ---------------------------------------------------------------------------
# End-to-end tree processing (nmrglue-gated)
# ---------------------------------------------------------------------------


@requires_nmrglue
def test_tree_default_does_not_combine_same_nucleus(tmp_path: Path) -> None:
    _write_synthetic_experiment(
        tmp_path / "1",
        nucleus="1H",
        bf1_mhz=500.13,
        peaks=[(7.12, 1.0, 5.0), (3.50, 2.0, 8.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
    )
    _write_synthetic_experiment(
        tmp_path / "2",
        nucleus="1H",
        bf1_mhz=500.13,
        peaks=[(2.10, 2.0, 5.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
    )
    result = process_bruker_tree(tmp_path)
    shifts = sorted(peak.shift_ppm for peak in result.experiment.peaks["H"])
    assert len(shifts) == 2
    assert shifts[0] == pytest.approx(3.50, abs=0.02)
    assert shifts[1] == pytest.approx(7.12, abs=0.02)
    assert len(result.spectra) == 1
    selection = result.selection[0]
    assert selection.selected_labels == ("1",)
    assert selection.policy == "default_deterministic"
    assert selection.combined is False
    assert selection.processor_id == PROTON_PROCESSOR_ID
    by_label = {record.label: record for record in result.experiments}
    assert by_label["2"].status == "not_selected"
    assert by_label["2"].reason == "default_deterministic"


@requires_nmrglue
def test_tree_explicit_multi_combines_and_records_policy(tmp_path: Path) -> None:
    _write_synthetic_experiment(
        tmp_path / "1",
        nucleus="1H",
        bf1_mhz=500.13,
        peaks=[(7.12, 1.0, 5.0), (3.50, 2.0, 8.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
    )
    _write_synthetic_experiment(
        tmp_path / "2",
        nucleus="1H",
        bf1_mhz=500.13,
        peaks=[(2.10, 2.0, 5.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
    )
    result = process_bruker_tree(tmp_path, select_experiments={"1H": ["1", "2"]})
    shifts = sorted(peak.shift_ppm for peak in result.experiment.peaks["H"])
    assert len(shifts) == 3
    assert shifts[0] == pytest.approx(2.10, abs=0.02)
    assert shifts[1] == pytest.approx(3.50, abs=0.02)
    assert shifts[2] == pytest.approx(7.12, abs=0.02)
    assert len(result.spectra) == 2
    selection = result.selection[0]
    assert selection.policy == "explicit_multi"
    assert selection.combined is True
    assert selection.selected_labels == ("1", "2")


@requires_nmrglue
def test_tree_surfaces_2d_rejection_and_processes_only_1d(tmp_path: Path) -> None:
    _write_2d_experiment(tmp_path / "1")
    _write_synthetic_experiment(
        tmp_path / "2",
        nucleus="1H",
        bf1_mhz=500.13,
        peaks=[(3.50, 2.0, 8.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
    )
    result = process_bruker_tree(tmp_path)
    assert len(result.spectra) == 1
    assert result.spectra[0].element == "H"
    by_label = {record.label: record for record in result.experiments}
    assert by_label["1"].status == "rejected"
    assert by_label["1"].reason == "ser_file"
    assert by_label["2"].status == "selected"
    assert set(result.experiment.peaks) == {"H"}


@requires_nmrglue
def test_tree_threads_processed_traces_for_selected_experiments(tmp_path: Path) -> None:
    _write_synthetic_experiment(
        tmp_path / "1",
        nucleus="1H",
        bf1_mhz=500.13,
        peaks=[(7.12, 1.0, 5.0), (3.50, 2.0, 8.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
    )
    _write_synthetic_experiment(
        tmp_path / "2",
        nucleus="1H",
        bf1_mhz=500.13,
        peaks=[(2.10, 2.0, 5.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
    )
    result = process_bruker_tree(tmp_path, select_experiments={"1H": ["1", "2"]})
    assert len(result.traces) == 2
    for spectrum in result.spectra:
        trace = result.trace_for(spectrum)
        assert trace is not None
        assert trace.source_dir == spectrum.source_dir
        ppm = np.asarray(trace.ppm, dtype=np.float64)
        intensity = np.asarray(trace.intensity, dtype=np.float64)
        assert ppm.shape == intensity.shape
        assert ppm.size > 0
        assert np.isfinite(ppm).all()
        assert np.isfinite(intensity).all()
        assert trace.noise == pytest.approx(spectrum.noise)
        pair = trace.as_pair()
        assert len(pair) == 2
        assert np.asarray(pair[0]).shape == ppm.shape
    assert result.trace_for(tmp_path / "missing") is None


@requires_nmrglue
def test_tree_records_spectrum_probe_nucleus_for_rejected_2d(tmp_path: Path) -> None:
    """A rejected 2D experiment still carries its probed nucleus in the plan."""
    _write_2d_experiment(tmp_path / "1", nucleus="1H")
    _write_synthetic_experiment(
        tmp_path / "2",
        nucleus="1H",
        bf1_mhz=500.13,
        peaks=[(3.50, 2.0, 8.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
    )
    result = process_bruker_tree(tmp_path)
    by_label = {record.label: record for record in result.experiments}
    assert by_label["1"].nucleus == "1H"
    assert by_label["1"].element == "H"
    assert spectrum_probe_nucleus(tmp_path / "1") == "1H"
