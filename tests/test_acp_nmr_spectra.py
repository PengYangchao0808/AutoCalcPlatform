"""Tests for Bruker raw-spectrum processing (DevDoc §5 stage 0a, P3).

Synthetic Bruker experiments are written directly (int32 interleaved FID +
hand-written JCAMP ``acqus``), so no real spectrometer data is needed.
The FID is a sum of damped complex exponentials whose FT peaks land at
known ppm values (nmrglue/Bruker sign convention: ``nu = (O1_ppm − ppm) ·
BF1``).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pytest

from acp.nmr.io import parse_experimental_nmr
from acp.nmr.spectra import (
    bruker_result_to_text,
    find_bruker_experiments,
    process_bruker_experiment,
    process_bruker_tree,
    spectrum_probe_nucleus,
)


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
) -> Path:
    """Write a minimal synthetic Bruker experiment (fid + acqus).

    Args:
        peaks: ``(ppm, amplitude, R2 decay rate / Hz)`` triples.
    """
    sw_hz = sw_ppm * bf1_mhz
    t = np.arange(td) / sw_hz
    fid = np.zeros(td, dtype=complex)
    for ppm, amp, r2 in peaks:
        nu = (o1_ppm - ppm) * bf1_mhz  # nmrglue/Bruker sign convention
        fid += amp * np.exp(2j * np.pi * nu * t) * np.exp(-np.pi * r2 * t)
    rng = np.random.default_rng(seed)
    fid += (rng.normal(0, noise, td) + 1j * rng.normal(0, noise, td))
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


@pytest.fixture()
def proton_dir(tmp_path: Path) -> Path:
    """1H experiment: peaks at 7.12 (amp 1) and 3.50 ppm (amp 2)."""
    return _write_bruker_experiment(
        tmp_path / "Proton",
        nucleus="1H",
        bf1_mhz=500.13,
        peaks=[(7.12, 1.0, 5.0), (3.50, 2.0, 8.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
    )


@pytest.fixture()
def carbon_dir(tmp_path: Path) -> Path:
    """13C experiment: peaks at 160 and 40 ppm."""
    return _write_bruker_experiment(
        tmp_path / "Carbon",
        nucleus="13C",
        bf1_mhz=125.76,
        peaks=[(160.0, 1.0, 3.0), (40.0, 1.0, 4.0)],
        sw_ppm=200.0,
        o1_ppm=100.0,
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_find_bruker_experiments_flat(proton_dir: Path) -> None:
    assert find_bruker_experiments(proton_dir) == [proton_dir]


def test_find_bruker_experiments_proton_carbon_layout(
    proton_dir: Path, carbon_dir: Path
) -> None:
    root = proton_dir.parent
    found = find_bruker_experiments(root)
    assert set(found) == {proton_dir, carbon_dir}


def test_find_bruker_experiments_expno_layout(tmp_path: Path) -> None:
    exp = _write_bruker_experiment(
        tmp_path / "sample" / "11",
        nucleus="1H",
        bf1_mhz=500.13,
        peaks=[(2.0, 1.0, 5.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
    )
    assert find_bruker_experiments(tmp_path / "sample") == [exp]


def test_find_bruker_experiments_empty(tmp_path: Path) -> None:
    assert find_bruker_experiments(tmp_path) == []


# ---------------------------------------------------------------------------
# Single-experiment processing
# ---------------------------------------------------------------------------


def test_process_proton_peaks_and_multiplicity(proton_dir: Path) -> None:
    result = process_bruker_experiment(proton_dir)
    assert result.element == "H"
    assert result.nucleus == "1H"
    shifts = sorted(p.shift_ppm for p in result.peaks)
    assert len(shifts) == 2
    assert shifts[0] == pytest.approx(3.50, abs=0.02)
    assert shifts[1] == pytest.approx(7.12, abs=0.02)
    # amp ratio 2:1 → multiplicity 2 for the 3.5 ppm peak
    mult = {round(p.shift_ppm, 1): p.multiplicity for p in result.peaks}
    assert mult[3.5] == 2
    assert mult[7.1] == 1


def test_process_carbon_peaks(carbon_dir: Path) -> None:
    result = process_bruker_experiment(carbon_dir)
    assert result.element == "C"
    shifts = sorted(p.shift_ppm for p in result.peaks)
    assert shifts == pytest.approx([40.0, 160.0], abs=0.5)
    # 13C multiplicities are always 1 (no integration heuristic)
    assert all(p.multiplicity == 1 for p in result.peaks)


def test_process_rejects_non_experiment_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Not a Bruker experiment"):
        process_bruker_experiment(tmp_path)


def test_reference_calibration_anchors_peak(proton_dir: Path) -> None:
    result = process_bruker_experiment(proton_dir, reference_ppm=7.26)
    assert result.reference_shift is not None
    assert result.reference_shift == pytest.approx(0.14, abs=0.02)
    shifts = sorted(p.shift_ppm for p in result.peaks)
    assert shifts[1] == pytest.approx(7.26, abs=1e-6)
    assert shifts[0] == pytest.approx(3.64, abs=0.02)


def test_reference_fallback_when_no_peak_in_window(
    proton_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Manual reference with no nearby peak keeps spectrometer referencing."""
    with caplog.at_level("WARNING"):
        result = process_bruker_experiment(proton_dir, reference_ppm=5.55)
    assert result.reference_shift is None
    assert any("keeping spectrometer referencing" in r.message for r in caplog.records)
    shifts = sorted(p.shift_ppm for p in result.peaks)
    assert shifts[1] == pytest.approx(7.12, abs=0.02)


# ---------------------------------------------------------------------------
# Tree / zip processing
# ---------------------------------------------------------------------------


def test_process_tree_merges_nuclei(proton_dir: Path, carbon_dir: Path) -> None:
    result = process_bruker_tree(proton_dir.parent)
    assert set(result.experiment.peaks) == {"H", "C"}
    assert not result.experiment.assigned
    assert len(result.spectra) == 2


def test_process_tree_zip(proton_dir: Path, carbon_dir: Path, tmp_path: Path) -> None:
    zip_path = tmp_path / "nmr_bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for exp_dir in (proton_dir, carbon_dir):
            for f in exp_dir.iterdir():
                zf.write(f, f"{exp_dir.name}/{f.name}")
    result = process_bruker_tree(zip_path, extract_dir=tmp_path / "extract")
    assert set(result.experiment.peaks) == {"H", "C"}
    assert result.extracted_dir is not None


def test_process_tree_zip_traversal_guard(tmp_path: Path) -> None:
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escape.txt", "boom")
    with pytest.raises(ValueError, match="Unsafe path"):
        process_bruker_tree(evil, extract_dir=tmp_path / "extract")


def test_process_tree_no_experiment_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No Bruker experiment"):
        process_bruker_tree(tmp_path)


def test_process_tree_no_peaks_raises(tmp_path: Path) -> None:
    """An FID of pure noise below the SNR threshold yields no peaks."""
    exp = _write_bruker_experiment(
        tmp_path / "Proton",
        nucleus="1H",
        bf1_mhz=500.13,
        peaks=[],
        sw_ppm=10.0,
        o1_ppm=5.0,
    )
    with pytest.raises(ValueError, match="no peaks|picked no peaks"):
        process_bruker_tree(exp.parent)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_spectrum_probe_nucleus(proton_dir: Path, carbon_dir: Path) -> None:
    assert spectrum_probe_nucleus(proton_dir) == "1H"
    assert spectrum_probe_nucleus(carbon_dir) == "13C"
    assert spectrum_probe_nucleus(proton_dir.parent) == ""


def test_bruker_result_to_text_roundtrip(proton_dir: Path, carbon_dir: Path) -> None:
    result = process_bruker_tree(proton_dir.parent)
    text = bruker_result_to_text(result)
    reparsed = parse_experimental_nmr(text)
    assert set(reparsed.peaks) == {"H", "C"}
    h_peaks = sorted(reparsed.peaks["H"], key=lambda p: p.shift_ppm)
    assert h_peaks[0].shift_ppm == pytest.approx(3.50, abs=0.02)
    assert h_peaks[0].multiplicity == 2
    assert not reparsed.assigned


def test_nmrglue_import_shim_active() -> None:
    """nmrglue ≤ 0.11 + numpy ≥ 2 loads through the tecmag stub shim."""
    import sys

    assert "nmrglue.fileio.tecmag" in sys.modules
    import nmrglue  # noqa: F401
