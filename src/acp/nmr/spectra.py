# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
"""Bruker raw-spectrum processing (DevDoc §5 stage 0a, P3).

Reads Bruker experiment directories (``fid``/``ser`` + ``acqus``), applies
the standard 1D processing chain — exponential apodization, zero-fill, FT,
automatic phase correction, polynomial baseline correction, peak picking —
and produces an *unassigned* :class:`ExperimentalNmr` peak list that feeds
the Hungarian matching path (stage 5).

Layout support (DevDoc §6.3):

* a single experiment directory (contains ``fid`` + ``acqus``);
* a root with ``Proton/`` / ``Carbon/`` subdirectories;
* a root with numbered ``<expno>`` experiment subdirectories;
* a ``.zip`` archive of any of the above (extracted to a work directory).

ppm calibration trusts the spectrometer referencing (``SR``) by default;
an optional manual reference (e.g. CDCl3 residual at 7.26 ppm for 1H)
shifts the picked peaks so the tallest peak inside a search window lands
exactly on the reference value — the documented fallback when automatic
processing is off (DevDoc §15.1).

nmrglue is an optional dependency (``pip install acp[nmr]``); it is
imported lazily so the rest of the NMR package works without it.
"""

from __future__ import annotations

import contextlib
import io as _io
import logging
import sys
import tempfile
import types
import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from acp.nmr.models import ExperimentalNmr, ExperimentalPeak, normalize_symbol

logger = logging.getLogger(__name__)


# Default exponential line broadening (Hz) per element.
_DEFAULT_LB_HZ: dict[str, float] = {"H": 0.3, "C": 1.0}
# Search window (ppm, ±) when locating the manual reference peak.
_DEFAULT_REF_WINDOW_PPM: dict[str, float] = {"H": 0.5, "C": 3.0}


@dataclass(frozen=True)
class ProcessedSpectrum:
    """One processed Bruker experiment: picked peaks + diagnostics.

    Attributes:
        nucleus: Nucleus label from ``acqus`` (e.g. ``"1H"``).
        element: Element symbol (``"H"`` / ``"C"``).
        peaks: Picked peaks (unassigned, multiplicity from integration).
        noise: Estimated noise level (edge MAD after baseline correction).
        reference_shift: ppm shift applied by manual referencing, if any.
        source_dir: Bruker experiment directory the spectrum came from.
    """

    nucleus: str
    element: str
    peaks: list[ExperimentalPeak]
    noise: float
    reference_shift: float | None = None
    source_dir: str = ""


@dataclass
class BrukerProcessResult:
    """Aggregate result of :func:`process_bruker_tree`."""

    experiment: ExperimentalNmr
    spectra: list[ProcessedSpectrum] = field(default_factory=list)
    extracted_dir: Path | None = None


# ---------------------------------------------------------------------------
# nmrglue import (optional dependency + numpy>=2 shim)
# ---------------------------------------------------------------------------


def _import_nmrglue():
    """Import nmrglue, working around the numpy>=2 tecmag incompatibility.

    nmrglue ≤ 0.11 imports ``nmrglue.fileio.tecmag`` unconditionally, which
    uses the ``'a8'`` dtype alias removed in numpy 2.0. We never read Tecmag
    files, so a stub module is injected before the first import. Raises
    :class:`ImportError` with an install hint when nmrglue is absent.
    """
    try:
        import nmrglue as ng  # noqa: PLC0415

        return ng
    except ModuleNotFoundError as exc:
        raise ImportError(
            "Bruker spectrum processing requires nmrglue "
            "(pip install 'acp[nmr]' or pip install nmrglue)"
        ) from exc
    except TypeError as exc:
        if "a8" not in str(exc):
            raise
        # numpy>=2 removed the 'a8' alias used by nmrglue.fileio.tecmag.
        # Purge partially-imported modules, stub tecmag, retry.
        for name in [m for m in sys.modules if m == "nmrglue" or m.startswith("nmrglue.")]:
            del sys.modules[name]
        sys.modules["nmrglue.fileio.tecmag"] = types.ModuleType("nmrglue.fileio.tecmag")
        import nmrglue as ng  # noqa: PLC0415

        return ng


# ---------------------------------------------------------------------------
# Experiment discovery
# ---------------------------------------------------------------------------


def _is_bruker_experiment(path: Path) -> bool:
    """A Bruker experiment dir holds a raw FID (``fid``/``ser``) + ``acqus``."""
    return (
        path.is_dir()
        and (path / "acqus").is_file()
        and ((path / "fid").is_file() or (path / "ser").is_file())
    )


def find_bruker_experiments(root: Path) -> list[Path]:
    """Find Bruker experiment directories under *root* (depth ≤ 2).

    Accepts the DevDoc §6.3 layouts: the root itself, one level of
    ``Proton/`` / ``Carbon/`` subdirectories, or numbered ``<expno>``
    subdirectories one level below a sample directory.
    """
    root = Path(root)
    if _is_bruker_experiment(root):
        return [root]
    found: list[Path] = []
    for child in sorted(root.iterdir()):
        if _is_bruker_experiment(child):
            found.append(child)
        elif child.is_dir():
            for grandchild in sorted(child.iterdir()):
                if _is_bruker_experiment(grandchild):
                    found.append(grandchild)
    return found


# ---------------------------------------------------------------------------
# Processing chain
# ---------------------------------------------------------------------------


def _acqus_params(dic: dict) -> dict:
    """Return the direct-dimension acquisition parameters."""
    acqus = dic.get("acqus") or {}
    sw_hz = acqus.get("SW_h") or (float(acqus["SW"]) * float(acqus["BF1"]))
    nucleus = str(acqus.get("NUC1") or "1H").strip()
    obs_mhz = float(acqus.get("SFO1") or acqus.get("BF1"))
    car_hz = float(acqus.get("O1") or 0.0)
    return {
        "sw_hz": float(sw_hz),
        "nucleus": nucleus,
        "obs_mhz": obs_mhz,
        "car_hz": car_hz,
    }


def _fft_pipeline(
    fid: np.ndarray,
    sw_hz: float,
    lb_hz: float,
) -> np.ndarray:
    """Apodize → first-point halve → zero-fill → FFT (pure numpy).

    nmrglue's ``proc_base.em`` treats ``lb`` as a *per-point* decay (not
    Hz), so the exponential window is applied explicitly here.
    """
    n = fid.shape[-1]
    t = np.arange(n) / sw_hz
    data = fid.astype(np.complex128) * np.exp(-np.pi * lb_hz * t)
    data = data.copy()
    # FT of a causal decay needs the first point halved, otherwise a
    # broad pedestal (Dirichlet kernel of the t=0 step) distorts peaks.
    data[0] *= 0.5
    size = 2 ** int(np.ceil(np.log2(2 * n)))
    data = np.concatenate([data, np.zeros(size - n, dtype=np.complex128)])
    return np.fft.fftshift(np.fft.fft(data))


def _auto_phase(spectrum: np.ndarray) -> np.ndarray:
    """Automatic phase correction via nmrglue (peak_minima → acme → none)."""
    ng = _import_nmrglue()
    from nmrglue.process import proc_autophase  # noqa: PLC0415

    _ = ng
    for method in ("peak_minima", "acme"):
        try:
            with contextlib.redirect_stdout(_io.StringIO()):
                phased = proc_autophase.autops(spectrum, method)
            if np.isfinite(phased.real).all() and phased.real.max() > 0:
                return np.asarray(phased, dtype=np.complex128)
        except Exception as exc:  # optimizer failure — try next method
            logger.debug("autops(%s) failed: %s", method, exc)
    logger.warning("Automatic phase correction failed; using unphased spectrum")
    return spectrum


def _baseline_correct(real: np.ndarray, window_fraction: float = 0.02) -> np.ndarray:
    """Morphological baseline correction (grey opening + smoothing).

    Robust for high-dynamic-range spectra: polynomial fits suffer edge
    (Runge) artefacts and ALS needs amplitude-dependent tuning, whereas a
    rolling minimum/maximum opening ignores peaks narrower than the
    window regardless of their height.
    """
    from scipy import ndimage  # noqa: PLC0415

    corrected = real.astype(np.float64, copy=True)
    n = corrected.shape[-1]
    window = max(int(n * window_fraction) | 1, 51)
    baseline = ndimage.grey_opening(corrected, size=window, mode="nearest")
    baseline = ndimage.uniform_filter1d(baseline, size=window, mode="nearest")
    return corrected - baseline


def _estimate_noise(real: np.ndarray, edge_fraction: float = 0.1) -> float:
    """Noise from the spectrum edges (MAD), robust to crowded regions."""
    n = real.shape[-1]
    edge = max(n // int(1 / edge_fraction), 16)
    samples = np.concatenate([real[:edge], real[-edge:]])
    med = float(np.median(samples))
    noise = 1.4826 * float(np.median(np.abs(samples - med)))
    return max(noise, 1e-12)


def _pick_peaks(
    real: np.ndarray,
    ppm_scale: np.ndarray,
    noise: float,
    snr_threshold: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Peak picking: local maxima above ``snr_threshold`` × noise."""
    from scipy.signal import find_peaks  # noqa: PLC0415

    indices, _ = find_peaks(
        real,
        height=noise * snr_threshold,
        prominence=noise * snr_threshold * 0.8,
    )
    return indices, ppm_scale[indices]


def _apply_reference(
    indices: np.ndarray,
    ppm_values: np.ndarray,
    heights: np.ndarray,
    reference_ppm: float,
    window_ppm: float,
) -> tuple[np.ndarray, float | None]:
    """Shift picked peaks so the tallest peak near *reference_ppm* lands on it.

    Returns ``(shifted_ppm_values, applied_shift_or_None)``. When no peak
    falls inside ``±window_ppm`` the peaks are returned unchanged and a
    warning is logged (manual-reference fallback, DevDoc §15.1).
    """
    in_window = np.abs(ppm_values - reference_ppm) <= window_ppm
    if not in_window.any():
        logger.warning(
            "Manual reference %.3f ppm: no picked peak within ±%.2f ppm; "
            "keeping spectrometer referencing",
            reference_ppm,
            window_ppm,
        )
        return ppm_values, None
    local = np.flatnonzero(in_window)
    anchor = local[int(np.argmax(heights[in_window]))]
    shift = float(reference_ppm - ppm_values[anchor])
    logger.info(
        "Manual reference: anchored peak %.4f → %.4f ppm (shift %+.4f ppm)",
        float(ppm_values[anchor]),
        reference_ppm,
        shift,
    )
    return ppm_values + shift, shift


def _integrate_multiplicities(
    real: np.ndarray,
    indices: np.ndarray,
    element: str,
) -> list[int]:
    """Derive integral multiplicities from peak-region areas (1H only).

    Each peak is integrated between the midpoints to its neighbours; the
    smallest area defines multiplicity 1. This is a heuristic — linewidth
    variation and overlapping peaks limit accuracy — so the result is
    only used as the Hungarian-matching intensity weight (DevDoc §8.3)
    and can be overridden by explicit multiplicity annotations.
    """
    if element != "H" or len(indices) <= 1:
        return [1] * len(indices)
    order = np.argsort(indices)
    sorted_idx = indices[order]
    areas = np.zeros(len(sorted_idx))
    n = real.shape[-1]
    for pos, idx in enumerate(sorted_idx):
        left = 0 if pos == 0 else (sorted_idx[pos - 1] + idx) // 2
        right = n - 1 if pos == len(sorted_idx) - 1 else (idx + sorted_idx[pos + 1]) // 2
        region = real[left : right + 1]
        areas[pos] = float(np.sum(np.clip(region, 0.0, None)))
    min_area = float(areas.min()) if areas.size else 1.0
    if min_area <= 0:
        return [1] * len(indices)
    mults = np.maximum(1, np.round(areas / min_area).astype(int))
    out = np.ones(len(indices), dtype=int)
    out[order] = mults
    return out.tolist()


def process_bruker_experiment(
    exp_dir: str | Path,
    reference_ppm: float | None = None,
    reference_window_ppm: float | None = None,
    lb_hz: float | None = None,
    snr_threshold: float = 8.0,
) -> ProcessedSpectrum:
    """Process one Bruker experiment directory into an unassigned peak list.

    Args:
        exp_dir: Directory containing ``fid``/``ser`` + ``acqus``.
        reference_ppm: Optional manual ppm reference (e.g. 7.26 for CDCl3
            residual in 1H). The tallest picked peak within the search
            window is anchored to this value.
        reference_window_ppm: Search window around *reference_ppm*
            (default 0.5 ppm for 1H, 3.0 ppm for 13C).
        lb_hz: Exponential line broadening (default 0.3 Hz 1H / 1.0 Hz 13C).
        snr_threshold: Peak-picking threshold in units of the edge-noise
            MAD (default 8 — ~5σ tails across a full 1D spectrum reach
            ~4σ, so 8 keeps white-noise spikes out).

    Raises:
        ImportError: When nmrglue is not installed.
        ValueError: When *exp_dir* is not a Bruker experiment.
    """
    exp_dir = Path(exp_dir)
    if not _is_bruker_experiment(exp_dir):
        raise ValueError(f"Not a Bruker experiment directory: {exp_dir}")

    ng = _import_nmrglue()
    with warnings.catch_warnings():
        # nmrglue notices we intentionally do not act on: the spectrometer
        # 'sr' referencing is either trusted or overridden by the manual
        # reference below. guess_udic also emits it (KeyError on procs).
        warnings.filterwarnings("ignore", message=".*not corrected for.*", category=UserWarning)
        dic, data = ng.fileio.bruker.read(exp_dir, read_pulseprogram=False, read_procs=False)
        udic = ng.fileio.bruker.guess_udic(dic, np.asarray(data))
    params = _acqus_params(dic)
    nucleus = params["nucleus"]
    element = normalize_symbol(nucleus.lstrip("0123456789"))
    if not element:
        raise ValueError(f"Cannot determine nucleus from acqus NUC1={nucleus!r}")

    sw_hz = params["sw_hz"]
    lb = lb_hz if lb_hz is not None else _DEFAULT_LB_HZ.get(element, 0.5)

    spectrum = _fft_pipeline(np.asarray(data), sw_hz, lb)
    spectrum = _auto_phase(spectrum)
    # Estimate noise BEFORE baseline correction: the morphological opening
    # tracks the lower noise envelope, so post-correction the noise floor
    # becomes strictly positive bumps and its MAD underestimates sigma.
    noise = _estimate_noise(spectrum.real)
    real = _baseline_correct(spectrum.real)

    dim = udic[0]
    uc = ng.fileio.fileiobase.unit_conversion(
        real.shape[-1], True, dim["sw"], dim["obs"], dim["car"]
    )
    ppm_scale = np.asarray(uc.ppm_scale())

    indices, ppm_values = _pick_peaks(real, ppm_scale, noise, snr_threshold)
    heights = real[indices] if len(indices) else np.zeros(0)

    reference_shift: float | None = None
    if reference_ppm is not None and len(indices):
        window = (
            reference_window_ppm
            if reference_window_ppm is not None
            else _DEFAULT_REF_WINDOW_PPM.get(element, 1.0)
        )
        ppm_values, reference_shift = _apply_reference(
            indices, ppm_values, heights, reference_ppm, window
        )

    multiplicities = _integrate_multiplicities(real, indices, element)

    peaks = [
        ExperimentalPeak(
            shift_ppm=round(float(ppm), 4),
            element=element,
            atom_label=None,
            multiplicity=int(mult),
        )
        for ppm, mult in zip(ppm_values, multiplicities)
    ]
    logger.info(
        "Bruker %s: picked %d peak(s) (%s, noise=%.3g, lb=%.2f Hz) from %s",
        nucleus,
        len(peaks),
        exp_dir.name,
        noise,
        lb,
        exp_dir,
    )
    return ProcessedSpectrum(
        nucleus=nucleus,
        element=element,
        peaks=peaks,
        noise=noise,
        reference_shift=reference_shift,
        source_dir=str(exp_dir),
    )


# ---------------------------------------------------------------------------
# Tree / zip entry point
# ---------------------------------------------------------------------------


def process_bruker_tree(
    path: str | Path,
    references: dict[str, float] | None = None,
    lb_hz: float | None = None,
    snr_threshold: float = 8.0,
    extract_dir: str | Path | None = None,
) -> BrukerProcessResult:
    """Process a Bruker directory tree (or zip archive) into an ExperimentalNmr.

    Args:
        path: Root directory (§6.3 layout, single experiment, or expno
            tree) or a ``.zip`` archive of one.
        references: Optional manual ppm references per nucleus label or
            element (``{"1H": 7.26}`` or ``{"H": 7.26}``).
        lb_hz / snr_threshold: Forwarded to :func:`process_bruker_experiment`.
        extract_dir: Where to extract zip archives (default: a fresh
            temporary directory — the caller is responsible for cleanup).

    Returns:
        :class:`BrukerProcessResult` with an unassigned
        :class:`ExperimentalNmr` (peaks grouped by element) plus per-
        experiment diagnostics.
    """
    path = Path(path)
    root = path
    extracted: Path | None = None
    if path.is_file() and path.suffix.lower() == ".zip":
        target = Path(extract_dir) if extract_dir else Path(tempfile.mkdtemp(prefix="acp_bruker_"))
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as zf:
            for member in zf.namelist():
                # path-traversal guard
                dest = (target / member).resolve()
                if not str(dest).startswith(str(target.resolve())):
                    raise ValueError(f"Unsafe path in zip archive: {member!r}")
            zf.extractall(target)
        extracted = target
        # a zip of a single top-level directory unwraps to that directory
        children = [c for c in target.iterdir()]
        root = children[0] if len(children) == 1 and children[0].is_dir() else target

    exp_dirs = find_bruker_experiments(root)
    if not exp_dirs:
        raise ValueError(
            f"No Bruker experiment (fid/ser + acqus) found under {path} "
            "— expected the §6.3 layout (Proton/ and/or Carbon/ subdirs, "
            "or numbered expno dirs)."
        )

    references = references or {}
    spectra: list[ProcessedSpectrum] = []
    peaks_by_element: dict[str, list[ExperimentalPeak]] = {}
    for exp_dir in exp_dirs:
        spectrum = process_bruker_experiment(
            exp_dir,
            reference_ppm=_reference_for(spectrum_probe_nucleus(exp_dir), references),
            lb_hz=lb_hz,
            snr_threshold=snr_threshold,
        )
        spectra.append(spectrum)
        if spectrum.peaks:
            peaks_by_element.setdefault(spectrum.element, []).extend(spectrum.peaks)

    if not peaks_by_element:
        raise ValueError(
            f"Bruker processing picked no peaks under {path} "
            f"({len(exp_dirs)} experiment(s) scanned) — check SNR/phase."
        )

    return BrukerProcessResult(
        experiment=ExperimentalNmr(
            peaks=peaks_by_element,
            equivalence_groups=[],
            omit_atoms=[],
            assigned=False,
        ),
        spectra=spectra,
        extracted_dir=extracted,
    )


def spectrum_probe_nucleus(exp_dir: str | Path) -> str:
    """Read just the ``NUC1`` nucleus label from an experiment's ``acqus``."""
    acqus = Path(exp_dir) / "acqus"
    try:
        for line in acqus.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.upper().startswith("##$NUC1"):
                return line.split("=", 1)[1].strip().strip("<>").strip()
    except OSError:
        pass
    return ""


def _reference_for(nucleus: str, references: dict[str, float]) -> float | None:
    """Look up a manual reference for a nucleus label (``1H`` or ``H``)."""
    if nucleus in references:
        return references[nucleus]
    element = normalize_symbol(nucleus.lstrip("0123456789")) if nucleus else ""
    return references.get(element)


def bruker_result_to_text(result: BrukerProcessResult) -> str:
    """Render picked peaks as DevDoc §6.2 text (unassigned, with multiplicities).

    Useful for transparency — the workflow writes this next to the report
    so the user can inspect / hand-correct the peak picking.
    """
    lines = [
        "# Auto-picked from Bruker raw data (stage 0a).",
        "# Review and hand-correct if peak picking missed/extra peaks.",
    ]
    for element in sorted(result.experiment.peaks):
        peaks = result.experiment.peaks[element]
        tokens = []
        for peak in peaks:
            token = f"{peak.shift_ppm:.4f}"
            if peak.multiplicity > 1:
                token += f"({peak.multiplicity})"
            tokens.append(token)
        lines.append(f"{element}: {', '.join(tokens)}")
    return "\n".join(lines) + "\n"


__all__ = [
    "BrukerProcessResult",
    "ProcessedSpectrum",
    "bruker_result_to_text",
    "find_bruker_experiments",
    "process_bruker_experiment",
    "process_bruker_tree",
    "spectrum_probe_nucleus",
]
