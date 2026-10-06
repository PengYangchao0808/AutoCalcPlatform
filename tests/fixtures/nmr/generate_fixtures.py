#!/usr/bin/env python3.11
"""Deterministic generator for the labeled NMR spectra benchmark fixtures (todo 48).

Every synthetic fixture is a small, byte-deterministic Bruker-format
experiment tree (hand-written JCAMP ``acqus`` + int32-interleaved ``fid``) or
a JSON state fixture, together with manual annotations. The same script
writes ``manifest.json`` — the machine-readable annotation/provenance source
consumed by ``tests/nmr_spectra_benchmark.py``.

Usage (from the repository root)::

    PYTHONPATH=src python3.11 tests/fixtures/nmr/generate_fixtures.py
    PYTHONPATH=src python3.11 tests/fixtures/nmr/generate_fixtures.py --check

``--check`` regenerates every synthetic fixture into a temporary directory and
fails (exit 1) when any committed byte differs from regeneration, so the
committed binaries can never silently drift from their specification. The real
Bruker fixture (``../bruker_real_group_delay``) is not generated; ``--check``
only verifies its files are present.

The FID convention follows the repository's existing synthetic fixtures
(``tests/test_acp_nmr_spectra.py``): peaks are damped complex exponentials
``A * exp(2j*pi*nu*t) * exp(-pi*R2*t)`` with ``nu = (o1_ppm - ppm) * BF1`` and
nmrglue/Bruker endianness (``BYTORDA=0``, ``DTYPA=0``). These fixtures encode
the *pipeline's* convention by construction; the committed REAL fixture is the
independent instrument reference (see its README).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "src") not in sys.path:  # self-bootstrap: import acp from the repo
    sys.path.insert(0, str(REPO_ROOT / "src"))

FIXTURES_ROOT = Path(__file__).resolve().parent.parent
NMR_DIR_NAME = "nmr"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = "acp-nmr-spectra-fixture-manifest-v1"
REAL_FIXTURE_DIR = "bruker_real_group_delay"

#: Matching tolerances the benchmark harness passes to the processor compare
#: helpers (stated in the metrics JSON for Wave-7 consumers).
TOLERANCES: dict[str, float] = {
    "carbon_resonance_match_ppm": 0.05,
    "proton_multiplet_position_ppm": 0.05,
}

# ---------------------------------------------------------------------------
# waveform helpers
# ---------------------------------------------------------------------------


def _synth_fid(
    *,
    bf1_mhz: float,
    lines: Sequence[tuple[float, float, float]],
    sw_ppm: float,
    o1_ppm: float,
    td: int,
    noise: float,
    seed: int,
    ph0_deg: float = 0.0,
    ph1_deg: float = 0.0,
) -> np.ndarray:
    """Damped-exponential FID (complex) for ``(ppm, amplitude, r2_hz)`` lines."""
    sw_hz = sw_ppm * bf1_mhz
    t = np.arange(td) / sw_hz
    fid = np.zeros(td, dtype=complex)
    for ppm, amp, r2 in lines:
        nu = (o1_ppm - ppm) * bf1_mhz
        fid += amp * np.exp(2j * np.pi * nu * t) * np.exp(-np.pi * r2 * t)
    if ph0_deg or ph1_deg:
        fid *= np.exp(1j * np.deg2rad(ph0_deg + ph1_deg * t / t[-1]))
    rng = np.random.default_rng(seed)
    fid += rng.normal(0, noise, td) + 1j * rng.normal(0, noise, td)
    return fid * 1e6


def _acqus_text(
    *,
    nucleus: str,
    bf1_mhz: float,
    sw_ppm: float,
    o1_ppm: float,
    td: int,
    solvent: str,
    grpdly: float,
    dspfvs: int,
) -> str:
    return (
        f"##$TD= {2 * td}\n"
        f"##$SFO1= {bf1_mhz}\n"
        f"##$BF1= {bf1_mhz}\n"
        f"##$O1= {o1_ppm * bf1_mhz}\n"
        f"##$SW_h= {sw_ppm * bf1_mhz}\n"
        f"##$SW= {sw_ppm}\n"
        f"##$NUC1= <{nucleus}>\n"
        f"##$GRPDLY= {grpdly}\n"
        f"##$DSPFVS= {dspfvs}\n"
        f"##$SOLVENT= <{solvent}>\n"
        "##$TE= 298.0\n"
        "##$PULPROG= <zg30>\n"
        "##$NS= 1\n"
        "##$BYTORDA= 0\n"
        "##$DTYPA= 0\n"
        "##$AQ_mod= 1\n"
        "##$DECIM= 1\n"
        "##$PARMODE= 0\n"
        "##END=\n"
    )


def _write_experiment(
    root: Path,
    *,
    nucleus: str,
    bf1_mhz: float,
    lines: Sequence[tuple[float, float, float]],
    sw_ppm: float,
    o1_ppm: float,
    td: int,
    noise: float,
    seed: int,
    grpdly: float = 0.0,
    dspfvs: int = 0,
    ph0_deg: float = 0.0,
    ph1_deg: float = 0.0,
) -> None:
    fid = _synth_fid(
        bf1_mhz=bf1_mhz,
        lines=lines,
        sw_ppm=sw_ppm,
        o1_ppm=o1_ppm,
        td=td,
        noise=noise,
        seed=seed,
        ph0_deg=ph0_deg,
        ph1_deg=ph1_deg,
    )
    root.mkdir(parents=True, exist_ok=True)
    raw = np.empty(2 * td, dtype=np.int32)
    raw[0::2] = fid.real.astype(np.int32)
    raw[1::2] = fid.imag.astype(np.int32)
    raw.astype("<i4").tofile(root / "fid")
    (root / "acqus").write_text(
        _acqus_text(
            nucleus=nucleus,
            bf1_mhz=bf1_mhz,
            sw_ppm=sw_ppm,
            o1_ppm=o1_ppm,
            td=td,
            solvent="CDCl3",
            grpdly=grpdly,
            dspfvs=dspfvs,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# molecule templates (annotations match these truth values)
# ---------------------------------------------------------------------------

_PROTON_BF1 = 400.13
_PROTON_SW = 8.0
_PROTON_O1 = 4.0
_PROTON_J_PPM = 7.2 / _PROTON_BF1  # J = 7.2 Hz


def _proton_lines(
    *,
    weak_line: tuple[float, float] | None = None,
    triplet_center: float = 1.260,
    quartet_center: float = 4.100,
    och3: float = 3.800,
    arh: float = 7.400,
) -> list[tuple[float, float, float]]:
    """An ethyl/methoxy/aromatic 1H spin system (9 H) + optional weak singlet."""
    lines: list[tuple[float, float, float]] = []
    for k, amp in ((-1, 0.75), (0, 1.5), (1, 0.75)):
        lines.append((triplet_center + k * _PROTON_J_PPM, amp, 2.0))
    for k, amp in ((-1.5, 0.25), (-0.5, 0.75), (0.5, 0.75), (1.5, 0.25)):
        lines.append((quartet_center + k * _PROTON_J_PPM, amp, 2.0))
    lines.append((och3, 3.0, 2.0))
    lines.append((arh, 1.0, 2.0))
    if weak_line is not None:
        lines.append((weak_line[0], weak_line[1], 2.0))
    return lines


def _proton_annotations(include_weak: bool = False) -> dict[str, object]:
    multiplets = [
        {
            "center_ppm": 1.260,
            "line_positions_ppm": [1.242, 1.260, 1.278],
            "atom_count": 3,
            "label": "CH3",
        },
        {
            "center_ppm": 4.100,
            "line_positions_ppm": [4.073, 4.091, 4.109, 4.127],
            "atom_count": 2,
            "label": "CH2",
        },
        {
            "center_ppm": 3.800,
            "line_positions_ppm": [3.800],
            "atom_count": 3,
            "label": "OCH3",
        },
        {
            "center_ppm": 7.400,
            "line_positions_ppm": [7.400],
            "atom_count": 1,
            "label": "ArH",
        },
    ]
    if include_weak:
        multiplets.append(
            {
                "center_ppm": 6.200,
                "line_positions_ppm": [6.200],
                "atom_count": 1,
                "label": "weak",
            }
        )
    return {
        "kind": "proton_grouping",
        "multiplets": multiplets,
        "position_tolerance_ppm": TOLERANCES["proton_multiplet_position_ppm"],
    }


_CARBON_BF1 = 100.61
_CARBON_SW = 180.0
_CARBON_O1 = 95.0
_CARBON_SOLVENT_J_PPM = 32.0 / _CARBON_BF1  # 1J(13C-2H) = 32 Hz for CDCl3


def _carbon_lines(
    *,
    solvent_amp: float = 3.0,
    extra: Sequence[tuple[float, float, float]] = (),
) -> list[tuple[float, float, float]]:
    r2 = 5.0  # finite acquisition: keep FID truncation ringing below the picker
    lines: list[tuple[float, float, float]] = [
        (77.16 - _CARBON_SOLVENT_J_PPM, solvent_amp, r2),
        (77.16, solvent_amp, r2),
        (77.16 + _CARBON_SOLVENT_J_PPM, solvent_amp, r2),
        (18.0, 0.5, r2),
        (57.2, 0.7, r2),
        (128.4, 0.4, r2),
        (167.3, 0.55, r2),
    ]
    lines.extend(extra)
    return lines


def _carbon_annotations(extra_positions: Sequence[float] = ()) -> dict[str, object]:
    resonances = [
        {"position_ppm": 18.0, "label": "C1"},
        {"position_ppm": 57.2, "label": "C2"},
        {"position_ppm": 128.4, "label": "C3"},
        {"position_ppm": 167.3, "label": "C4"},
    ]
    resonances.extend({"position_ppm": pos, "label": "weak"} for pos in extra_positions)
    return {
        "kind": "resonance_precision_recall",
        "resonances": resonances,
        "tolerance_ppm": TOLERANCES["carbon_resonance_match_ppm"],
    }


# ---------------------------------------------------------------------------
# fixture builders
# ---------------------------------------------------------------------------


def _build_proton_experiment(root: Path, fixture: Mapping[str, object]) -> None:
    params = fixture["build"]
    assert isinstance(params, Mapping)
    _write_experiment(
        root,
        nucleus="1H",
        bf1_mhz=_PROTON_BF1,
        lines=_proton_lines(weak_line=params.get("weak_line")),
        sw_ppm=params.get("sw_ppm", _PROTON_SW),
        o1_ppm=params.get("o1_ppm", _PROTON_O1),
        td=params.get("td", 4096),
        noise=params.get("noise", 2e-4),
        seed=params.get("seed", 7),
        grpdly=params.get("grpdly", 0.0),
        dspfvs=params.get("dspfvs", 0),
        ph0_deg=params.get("ph0_deg", 0.0),
        ph1_deg=params.get("ph1_deg", 0.0),
    )


def _build_overlap_proton(root: Path, fixture: Mapping[str, object]) -> None:
    spacing = _PROTON_J_PPM
    lines: list[tuple[float, float, float]] = []
    for k, amp in ((-1.5, 0.25), (-0.5, 0.75), (0.5, 0.75), (1.5, 0.25)):
        lines.append((3.400 + k * spacing, amp, 2.0))
    lines.append((3.390, 3.0, 2.0))  # methoxy singlet inside the quartet
    lines.append((1.260, 3.0, 2.0))
    _write_experiment(
        root,
        nucleus="1H",
        bf1_mhz=_PROTON_BF1,
        lines=lines,
        sw_ppm=5.0,
        o1_ppm=2.5,
        td=4096,
        noise=2e-4,
        seed=13,
    )


def _build_carbon_experiment(root: Path, fixture: Mapping[str, object]) -> None:
    params = fixture["build"]
    assert isinstance(params, Mapping)
    extra = tuple(tuple(item) for item in params.get("extra", ()))  # type: ignore[arg-type]
    lines = _carbon_lines(solvent_amp=params.get("solvent_amp", 3.0), extra=extra)
    _write_experiment(
        root,
        nucleus="13C",
        bf1_mhz=_CARBON_BF1,
        lines=lines,
        sw_ppm=_CARBON_SW,
        o1_ppm=_CARBON_O1,
        td=8192,
        noise=2e-4,
        seed=5,
    )


def _build_duplicate_tree(root: Path, fixture: Mapping[str, object]) -> None:
    _write_experiment(
        root / "11",
        nucleus="1H",
        bf1_mhz=_PROTON_BF1,
        lines=[(7.12, 1.0, 5.0), (3.50, 2.0, 8.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
        td=4096,
        noise=2e-4,
        seed=1,
    )
    _write_experiment(
        root / "12",
        nucleus="1H",
        bf1_mhz=_PROTON_BF1,
        lines=[(8.02, 1.0, 5.0), (2.10, 1.0, 5.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
        td=4096,
        noise=2e-4,
        seed=2,
    )
    _write_experiment(
        root / "13",
        nucleus="13C",
        bf1_mhz=_CARBON_BF1,
        lines=[(160.0, 1.0, 10.0), (40.0, 1.0, 10.0)],
        sw_ppm=_CARBON_SW,
        o1_ppm=_CARBON_O1,
        td=8192,
        noise=2e-4,
        seed=3,
    )


def _build_not_1d(root: Path, fixture: Mapping[str, object]) -> None:
    """Mixed tree: one 2D ``ser`` experiment + one 1D companion.

    The companion lets the benchmark exercise both rejection paths from a
    single fixture: plan-level rejection of the 2D experiment (mixed tree) and
    the typed ``Not1DExperimentError`` when the 2D experiment is selected
    explicitly; the 2D directory alone is then refused outright.
    """
    ser_dir = root / "ser_experiment"
    ser_dir.mkdir(parents=True, exist_ok=True)
    (ser_dir / "acqus").write_text(
        "##$TD= 4096\n##$NUC1= <1H>\n##$PARMODE= 0\n##$SW= 10.0\n##END=\n",
        encoding="utf-8",
    )
    # A 2D serial file (contents irrelevant — presence is the signal).
    (ser_dir / "ser").write_bytes(bytes(4096))
    _write_experiment(
        root / "proton_1d",
        nucleus="1H",
        bf1_mhz=_PROTON_BF1,
        lines=[(7.12, 1.0, 5.0), (3.50, 2.0, 8.0)],
        sw_ppm=10.0,
        o1_ppm=5.0,
        td=2048,
        noise=2e-4,
        seed=17,
    )


def _build_processed_state(root: Path, fixture: Mapping[str, object]) -> None:
    from acp.nmr.models import (
        AcquisitionSpectrum,
        ExperimentalPeak,
        ProcessedSpectrum,
        ProcessingProvenance,
        ProcessingQuality,
        assess_processing,
    )

    processing = ProcessingProvenance(
        apodization="exponential",
        lb_hz=0.3,
        zero_fill_points=8192,
        zero_fill_factor=2.0,
        phase_method="unphased",
        baseline_method="morphological_grey_opening",
        baseline_window_fraction=0.02,
        reference_method="spectrometer_sr",
        digital_filter_compensation="none",
    )
    acquisition = AcquisitionSpectrum(
        spectrometer="Bruker",
        nucleus="1H",
        frequency_mhz=400.13,
        solvent="CDCl3",
        temperature_k=298.0,
        pulse_program="zg30",
        point_count=4096,
        spectral_width_hz=3201.04,
        carrier_ppm=4.0,
        group_delay_points=0.0,
        dspfvs=0,
        spectrometer_reference=4.0,
        source_dir=fixture["id"],
    )
    assessment = assess_processing(processing, acquisition)
    spectrum = ProcessedSpectrum(
        nucleus="1H",
        element="H",
        peaks=[ExperimentalPeak(shift_ppm=3.8, element="H", multiplicity=1, index=0)],
        noise=1.5e-4,
        source_dir=fixture["id"],
        acquisition=acquisition,
        processing=processing,
        quality=ProcessingQuality(snr=42.0, linewidth_hz=1.8, baseline_rms=2.0e-4),
        assessment=assessment,
    )
    target = root  # this fixture's manifest path IS the JSON file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(spectrum.to_dict(), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


_BUILDERS: dict[str, Callable[[Path, Mapping[str, object]], None]] = {
    "proton_experiment": _build_proton_experiment,
    "overlap_proton": _build_overlap_proton,
    "carbon_experiment": _build_carbon_experiment,
    "duplicate_tree": _build_duplicate_tree,
    "not_1d": _build_not_1d,
    "processed_state": _build_processed_state,
}

#: Synthetic fixture specifications. ``path`` is relative to ``tests/fixtures``.
SYNTHETIC_FIXTURES: tuple[dict[str, object], ...] = (
    {
        "id": "phase_deviation_proton",
        "category": "phase_deviation",
        "kind": "bruker_experiment",
        "layer": "synthetic",
        "path": "nmr/phase_deviation_proton",
        "nucleus": "1H",
        "requires": "nmrglue",
        "source": (
            "synthetic deterministic FID with a 120 deg zero-order receiver phase "
            "error (autophase must recover the lines)"
        ),
        "builder": "proton_experiment",
        "build": {"ph0_deg": 120.0, "td": 4096, "seed": 7},
        "annotations": _proton_annotations(),
        "processing_options": {},
        "processor_options": {},
    },
    {
        "id": "phase_unphased_gate",
        "category": "phase_deviation",
        "kind": "processed_spectrum",
        "layer": "synthetic",
        "path": "nmr/phase_unphased_gate.json",
        "nucleus": "1H",
        "requires": "none",
        "source": (
            "derived pipeline-state fixture: phase_method='unphased' + failed "
            "processing assessment (the todo-42 gate must refuse it)"
        ),
        "builder": "processed_state",
        "build": {},
        "annotations": {},
        "processing_options": {},
        "processor_options": {},
    },
    {
        "id": "digital_filter_proton",
        "category": "digital_filter",
        "kind": "bruker_experiment",
        "layer": "synthetic",
        "path": "nmr/digital_filter_proton",
        "nucleus": "1H",
        "requires": "nmrglue",
        "source": (
            "synthetic FID with real DSP firmware metadata GRPDLY=67.986 / DSPFVS=12; "
            "no compensation is applied (metadata-level degradation, effect unverified)"
        ),
        "builder": "proton_experiment",
        "build": {"grpdly": 67.986, "dspfvs": 12, "td": 4096, "seed": 7},
        "annotations": _proton_annotations(),
        "processing_options": {},
        "processor_options": {},
    },
    {
        "id": "solvent_large_carbon",
        "category": "solvent_large",
        "kind": "bruker_experiment",
        "layer": "synthetic",
        "path": "nmr/solvent_large_carbon",
        "nucleus": "13C",
        "requires": "nmrglue",
        "source": "synthetic 13C FID: large CDCl3 triplet (3x analyte) + four singlets",
        "builder": "carbon_experiment",
        "build": {"solvent_amp": 3.0},
        "annotations": _carbon_annotations(),
        "processing_options": {},
        "processor_options": {},
    },
    {
        "id": "impurity_carbon",
        "category": "impurity",
        "kind": "bruker_experiment",
        "layer": "synthetic",
        "path": "nmr/impurity_carbon",
        "nucleus": "13C",
        "requires": "nmrglue",
        "source": "synthetic 13C FID: analyte + two unannotated impurity peaks (29.7/23.5)",
        "builder": "carbon_experiment",
        "build": {"solvent_amp": 3.0, "extra": [[29.7, 0.4, 5.0], [23.5, 0.3, 5.0]]},
        "annotations": _carbon_annotations(),
        "processing_options": {},
        "processor_options": {},
    },
    {
        "id": "low_snr_carbon",
        "category": "low_snr",
        "kind": "bruker_experiment",
        "layer": "synthetic",
        "path": "nmr/low_snr_carbon",
        "nucleus": "13C",
        "requires": "nmrglue",
        "source": "synthetic 13C FID with one weak analyte line (S/N near the gate)",
        "builder": "carbon_experiment",
        "build": {"solvent_amp": 3.0, "extra": [[45.0, 0.012, 5.0]]},
        "annotations": _carbon_annotations(extra_positions=(45.0,)),
        "processing_options": {"snr_threshold": 6.0},
        "processor_options": {"min_snr": 2000.0},
    },
    {
        "id": "low_snr_proton",
        "category": "low_snr",
        "kind": "bruker_experiment",
        "layer": "synthetic",
        "path": "nmr/low_snr_proton",
        "nucleus": "1H",
        "requires": "nmrglue",
        "source": "synthetic 1H FID with one weak singlet at 6.20 ppm (S/N near the gate)",
        "builder": "proton_experiment",
        "build": {"weak_line": [6.2, 0.0015], "td": 4096, "seed": 11},
        "annotations": _proton_annotations(include_weak=True),
        "processing_options": {"snr_threshold": 6.0},
        "processor_options": {"snr_threshold": 50.0, "total_hydrogens": 10},
    },
    {
        "id": "overlap_proton",
        "category": "overlap",
        "kind": "bruker_experiment",
        "layer": "synthetic",
        "path": "nmr/overlap_proton",
        "nucleus": "1H",
        "requires": "nmrglue",
        "source": "synthetic 1H FID: methoxy singlet interleaved inside an ethyl quartet",
        "builder": "overlap_proton",
        "build": {},
        "annotations": {
            "kind": "multiplet_grouping",
            "multiplets": [
                {
                    "center_ppm": 3.400,
                    "line_positions_ppm": [3.373, 3.391, 3.409, 3.427],
                    "atom_count": 2,
                    "label": "CH2 quartet",
                },
                {
                    "center_ppm": 3.390,
                    "line_positions_ppm": [3.390],
                    "atom_count": 3,
                    "label": "OCH3 overlap",
                },
                {
                    "center_ppm": 1.260,
                    "line_positions_ppm": [1.260],
                    "atom_count": 3,
                    "label": "CH3",
                },
            ],
            "position_tolerance_ppm": TOLERANCES["proton_multiplet_position_ppm"],
        },
        "processing_options": {},
        "processor_options": {},
    },
    {
        "id": "same_nucleus_duplicates",
        "category": "same_nucleus_duplicates",
        "kind": "bruker_tree",
        "layer": "synthetic",
        "path": "nmr/same_nucleus_duplicates",
        "nucleus": "1H+13C",
        "requires": "nmrglue",
        "source": (
            "synthetic Bruker tree with two 1H experiments (11, 12) + one 13C (13): "
            "default selects one per nucleus; explicit selection may combine"
        ),
        "builder": "duplicate_tree",
        "build": {},
        "annotations": {},
        "processing_options": {},
        "processor_options": {},
    },
    {
        "id": "two_d_ser",
        "category": "not_1d",
        "kind": "not_1d",
        "layer": "synthetic",
        "path": "nmr/two_d_ser",
        "nucleus": "1H",
        "requires": "none",
        "source": (
            "non-1D experiment directory (acqus + ser serial file) next to a 1D "
            "companion: 2D rejection in a mixed tree + typed explicit-selection raise"
        ),
        "builder": "not_1d",
        "build": {},
        "annotations": {},
        "processing_options": {},
        "processor_options": {},
    },
)

#: The real instrument fixture (committed read-only; never regenerated).
REAL_FIXTURES: tuple[dict[str, object], ...] = (
    {
        "id": "real_avance_neo_group_delay_1h",
        "category": "real_acquisition",
        "kind": "bruker_experiment",
        "layer": "real",
        "path": REAL_FIXTURE_DIR,
        "nucleus": "1H",
        "requires": "nmrglue",
        "source": (
            "real AVANCE NEO 400 MHz 1H dataset (4-vinylbenzoic acid in CDCl3, "
            "2018-05-24; TopSpin 4.0.2); acqus + fid only, fid trimmed to 16384 "
            "complex points (see fixture README). Manual annotations derive from "
            "the TopSpin peak list + structure assignment."
        ),
        "annotations": {
            "kind": "proton_grouping",
            "multiplets": [
                {
                    "center_ppm": 8.080,
                    "line_positions_ppm": [8.0921, 8.0716],
                    "atom_count": 2,
                    "label": "ArH (ortho-COOH)",
                },
                {
                    "center_ppm": 7.500,
                    "line_positions_ppm": [7.5118, 7.4910],
                    "atom_count": 2,
                    "label": "ArH (ortho-vinyl)",
                },
                {
                    "center_ppm": 7.259,
                    "line_positions_ppm": [7.2586],
                    "atom_count": 1,
                    "label": "CHCl3 residual",
                },
                {
                    "center_ppm": 6.770,
                    "line_positions_ppm": [6.8106, 6.7834, 6.7666, 6.7394],
                    "atom_count": 1,
                    "label": "CH=",
                },
                {
                    "center_ppm": 5.900,
                    "line_positions_ppm": [5.9216, 5.8776],
                    "atom_count": 1,
                    "label": "=CH2 (trans)",
                },
                {
                    "center_ppm": 5.420,
                    "line_positions_ppm": [5.4329, 5.4183, 5.4056, 5.3913],
                    "atom_count": 1,
                    "label": "=CH2 (cis)",
                },
            ],
            "position_tolerance_ppm": TOLERANCES["proton_multiplet_position_ppm"],
        },
        "processing_options": {},
        "processor_options": {},
        "files": ["acqus", "fid"],
        "declared_verification": "not_verified",
        "verification_reasons": [
            (
                "instrument_storage_convention_unverified: the ACP raw-read path does "
                "not recover the TopSpin reference peak positions from this AVANCE NEO "
                "float64 FID (observed peak set is mirrored about the carrier and carries "
                "the uncompensated group-delay transient)"
            ),
            (
                "digital_filter_effect_unverified: no group-delay compensation is "
                "applied by the pipeline; the real filter effect is marked, not corrected "
                "(todo-42 three-state)"
            ),
        ],
    },
)


# ---------------------------------------------------------------------------
# file listing / hashing
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fixture_files(fixture: Mapping[str, object], fixtures_root: Path) -> list[str]:
    target = fixtures_root / str(fixture["path"])
    if target.is_file():
        return [str(fixture["path"]).removeprefix(f"{NMR_DIR_NAME}/")]
    return sorted(
        str(path.relative_to(target)).replace("\\", "/")
        for path in target.rglob("*")
        if path.is_file()
    )


def _manifest_entry(fixture: Mapping[str, object], fixtures_root: Path) -> dict[str, object]:
    entry = {
        key: fixture[key]
        for key in (
            "id",
            "category",
            "kind",
            "layer",
            "path",
            "nucleus",
            "requires",
            "source",
            "annotations",
            "processing_options",
            "processor_options",
        )
        if key in fixture
    }
    entry["files"] = (
        list(fixture["files"])  # explicit override (e.g. exclude a README)
        if "files" in fixture
        else _fixture_files(fixture, fixtures_root)
    )
    if "declared_verification" in fixture:
        entry["declared_verification"] = fixture["declared_verification"]
        entry["verification_reasons"] = fixture["verification_reasons"]
    return entry


def build_manifest(fixtures_root: Path) -> dict[str, object]:
    entries = [_manifest_entry(fixture, fixtures_root) for fixture in SYNTHETIC_FIXTURES]
    entries.extend(_manifest_entry(fixture, fixtures_root) for fixture in REAL_FIXTURES)
    return {
        "schema": MANIFEST_SCHEMA,
        "generator": "tests/fixtures/nmr/generate_fixtures.py",
        "tolerances": dict(TOLERANCES),
        "fixtures": entries,
    }


# ---------------------------------------------------------------------------
# generate / check
# ---------------------------------------------------------------------------


def _generate_synthetic(dest_root: Path) -> None:
    """Write every synthetic fixture under ``dest_root/nmr`` (to_dict manifest)."""
    for fixture in SYNTHETIC_FIXTURES:
        target = dest_root / str(fixture["path"])
        builder = _BUILDERS[str(fixture["builder"])]
        builder(target, fixture)
    manifest = build_manifest(FIXTURES_ROOT)
    manifest_path = dest_root / NMR_DIR_NAME / MANIFEST_NAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _committed_files(fixtures_root: Path) -> dict[Path, bytes]:
    files: dict[Path, bytes] = {}
    for fixture in (*SYNTHETIC_FIXTURES, *REAL_FIXTURES):
        target = fixtures_root / str(fixture["path"])
        if target.is_file():
            files[Path(str(fixture["path"]))] = target.read_bytes()
        elif target.is_dir():
            for path in sorted(target.rglob("*")):
                if path.is_file():
                    files[path.relative_to(fixtures_root)] = path.read_bytes()
        else:
            raise FileNotFoundError(f"fixture missing on disk: {target}")
    manifest_path = fixtures_root / NMR_DIR_NAME / MANIFEST_NAME
    files[Path(NMR_DIR_NAME) / MANIFEST_NAME] = manifest_path.read_bytes()
    return files


def check_fixtures() -> list[str]:
    """Return human-readable mismatch reports (empty = committed bytes match)."""
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="acp_nmr_fixture_check_") as tmp:
        staged_root = Path(tmp) / "fixtures"
        staged_root.mkdir(parents=True)
        _generate_synthetic(staged_root)
        committed = _committed_files(FIXTURES_ROOT)
        for relative, committed_bytes in sorted(committed.items()):
            staged = staged_root / relative
            if not staged.is_file():
                # Real fixture files are committed but never regenerated.
                continue
            if staged.read_bytes() != committed_bytes:
                problems.append(f"{relative}: committed bytes differ from regeneration")
        # every regenerated file must exist committed too
        for staged in sorted(staged_root.rglob("*")):
            if staged.is_file():
                relative = staged.relative_to(staged_root)
                if relative not in committed:
                    problems.append(f"{relative}: generated but not committed")
        # declared fixture paths must exist on disk
        for fixture in (*SYNTHETIC_FIXTURES, *REAL_FIXTURES):
            target = FIXTURES_ROOT / str(fixture["path"])
            if target.is_file():
                continue  # single-file fixture: the path itself is the file
            if not target.is_dir():
                problems.append(f"{fixture['id']}: fixture path missing: {target}")
                continue
            for name in _fixture_files(fixture, FIXTURES_ROOT):
                if not (target / name).exists():
                    problems.append(f"{fixture['id']}: declared file missing: {name}")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify committed fixtures/manifest match regeneration (no writes)",
    )
    args = parser.parse_args(argv)
    if args.check:
        problems = check_fixtures()
        if problems:
            print("FIXTURE CHECK FAILED")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("FIXTURE CHECK OK: committed bytes match regeneration")
        return 0
    _generate_synthetic(FIXTURES_ROOT)
    print(f"generated {len(SYNTHETIC_FIXTURES)} synthetic fixtures + manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
