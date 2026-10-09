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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from acp.nmr.models import (
    DIGITAL_FILTER_COMPENSATION_NONE,
    AcquisitionSpectrum,
    ExperimentalNmr,
    ExperimentalPeak,
    ProcessedSpectrum,
    ProcessingProvenance,
    ProcessingQuality,
    SpectralLine,
    assess_processing,
    element_of_nucleus,
    normalize_symbol,
)
from acp.nmr.spectra_registry import NucleusProcessorError, lookup_processor

logger = logging.getLogger(__name__)


# Default exponential line broadening (Hz) per element.
_DEFAULT_LB_HZ: dict[str, float] = {"H": 0.3, "C": 1.0}
# Search window (ppm, ±) when locating the manual reference peak.
_DEFAULT_REF_WINDOW_PPM: dict[str, float] = {"H": 0.5, "C": 3.0}
# Morphological baseline window as a fraction of the spectrum length.
_BASELINE_WINDOW_FRACTION: float = 0.02
# SNR threshold default for peak picking (edge-noise MAD units).
_DEFAULT_SNR_THRESHOLD: float = 8.0
# This chain applies no digital-filter (group-delay) compensation. The
# decision is recorded explicitly so the quality gate reports what was
# actually done instead of silently assuming the filter had no effect (G10).
_DIGITAL_FILTER_COMPENSATION: str = DIGITAL_FILTER_COMPENSATION_NONE


@dataclass(frozen=True, eq=False)
class ProcessedTrace:
    """Dense processed trace of one experiment (todo 45 handoff).

    Threads the exact ``(ppm, intensity)`` pair the todo-43/44 processors
    consume (``process_carbon_spectrum(spectrum, trace=...)``). ``intensity``
    is the baseline-corrected real part from which the picked lines were
    measured; ``noise`` is the edge-MAD of the same trace estimated before
    baseline correction (the documented pipeline order, see
    :func:`_estimate_noise`). ``ppm`` keeps acquisition order (descending for
    Bruker); consumers sort as needed (the carbon processor already does).

    ``eq=False`` keeps numpy array semantics (no ambiguous truth value).
    """

    source_dir: str
    ppm: np.ndarray
    intensity: np.ndarray
    noise: float

    def as_pair(self) -> tuple[np.ndarray, np.ndarray]:
        """Return the ``(ppm, intensity)`` pair in processor call shape."""
        return (self.ppm, self.intensity)


@dataclass
class BrukerProcessResult:
    """Aggregate result of :func:`process_bruker_tree`."""

    experiment: ExperimentalNmr
    spectra: list[ProcessedSpectrum] = field(default_factory=list)
    extracted_dir: Path | None = None
    #: Per-nucleus selection record: which experiment(s) fed which nucleus,
    #: under which policy, and the registered processor (todo 45).
    selection: tuple[NucleusSelectionRecord, ...] = ()
    #: Per-experiment disposition, including rejected 2D experiments (never
    #: silently swallowed).
    experiments: tuple[ExperimentSelectionRecord, ...] = ()
    #: Dense processed traces for the SELECTED experiments (todo 45 handoff).
    traces: tuple[ProcessedTrace, ...] = ()

    @property
    def formal_spectra(self) -> list[ProcessedSpectrum]:
        """Spectra that passed the processing gate (failed ones excluded)."""
        return [spectrum for spectrum in self.spectra if spectrum.formal_usable]

    def trace_for(self, source: str | Path | ProcessedSpectrum) -> ProcessedTrace | None:
        """The dense processed trace recorded for *source*, or ``None``."""
        if isinstance(source, ProcessedSpectrum):
            key = source.source_dir
        else:
            key = str(Path(source))
        for trace in self.traces:
            if trace.source_dir == key:
                return trace
        return None


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
# 1D dimensionality gate (todo 45)
# ---------------------------------------------------------------------------

#: Closed vocabulary: why an experiment is NOT a 1D acquisition.
NOT_1D_REASONS: tuple[str, ...] = ("ser_file", "acqu2s_present", "parmode_not_1d")


class Not1DExperimentError(ValueError):
    """Typed rejection of a non-1D (2D/3D) Bruker experiment.

    The 1D processing chain must never consume multi-dimensional data: a
    ``ser`` serial file, ``acqu2s``/``acqu3s`` acquisition parameters, or an
    ``acqus`` ``PARMODE`` other than 0 all mean "not 1D" and are rejected
    before nmrglue (or any FID read) runs.
    """

    def __init__(self, exp_dir: str | Path, reason: str, detail: str = "") -> None:
        if reason not in NOT_1D_REASONS:
            raise ValueError(f"unknown not-1D reason {reason!r}; expected {NOT_1D_REASONS}")
        self.exp_dir = str(exp_dir)
        self.reason = reason
        self.detail = detail
        message = (
            f"Not a 1D Bruker experiment ({reason}): {exp_dir} — "
            "2D data must not be processed by the 1D chain"
        )
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)


def _probe_acqus_text(exp_dir: str | Path, parameter: str) -> str | None:
    """Read one ``##$PARAM=`` value from ``acqus``; ``None`` when absent/empty."""
    acqus = Path(exp_dir) / "acqus"
    prefix = f"##${parameter.upper()}"
    try:
        for line in acqus.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.upper().startswith(prefix):
                _, _, value = stripped.partition("=")
                return value.strip().strip("<>").strip() or None
    except OSError:
        pass
    return None


def not_1d_reason(exp_dir: str | Path) -> str | None:
    """Closed-vocabulary reason *exp_dir* is not 1D, or ``None`` when it is.

    Dependency-free detection order (no nmrglue, no FID read):

    1. a ``ser`` serial file (2D/3D raw data);
    2. ``acqu2s`` / ``acqu3s`` (acquisition parameters of dimensions 2/3);
    3. ``acqus`` ``PARMODE`` != 0 (0 = 1D).
    """
    directory = Path(exp_dir)
    if (directory / "ser").is_file():
        return "ser_file"
    if (directory / "acqu2s").is_file() or (directory / "acqu3s").is_file():
        return "acqu2s_present"
    parmode = _probe_acqus_text(directory, "PARMODE")
    if parmode is not None:
        try:
            if int(float(parmode)) != 0:
                return "parmode_not_1d"
        except ValueError:
            logger.warning("Unparseable PARMODE %r in %s; treated as 1D", parmode, directory)
    return None


# ---------------------------------------------------------------------------
# Experiment selection (todo 45)
# ---------------------------------------------------------------------------

#: Closed vocabulary: how one nucleus's experiment set was chosen.
SELECTION_POLICIES: tuple[str, ...] = (
    "single",
    "default_deterministic",
    "explicit_single",
    "explicit_multi",
)

#: Per-experiment disposition in the selection plan.
EXPERIMENT_SELECTION_STATUSES: tuple[str, ...] = ("selected", "not_selected", "rejected")

#: Closed vocabulary of per-experiment reasons (``None`` only for ``selected``).
EXPERIMENT_SELECTION_REASONS: tuple[str, ...] = (
    "not_requested",
    "default_deterministic",
    "nucleus_unreadable",
    *NOT_1D_REASONS,
)

#: Closed vocabulary of typed selection failures.
SELECTION_ERROR_REASONS: tuple[str, ...] = (
    "unknown_experiment",
    "ambiguous_experiment",
    "duplicate_experiment",
    "nucleus_mismatch",
    "unknown_nucleus",
    "nucleus_unreadable",
)


class ExperimentSelectionError(ValueError):
    """Typed user-selection failure (unknown/ambiguous/mismatched labels)."""

    def __init__(self, reason: str, detail: str) -> None:
        if reason not in SELECTION_ERROR_REASONS:
            raise ValueError(
                f"unknown selection error reason {reason!r}; expected {SELECTION_ERROR_REASONS}"
            )
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


@dataclass(frozen=True)
class ExperimentSelectionRecord:
    """Per-experiment disposition recorded by the tree selection plan.

    ``status`` is ``selected`` (feeds the formal peak list), ``not_selected``
    (a 1D experiment the policy did not choose; ``reason`` says why) or
    ``rejected`` (2D / unreadable nucleus — never processed).
    """

    label: str
    nucleus: str
    element: str
    status: str
    reason: str | None
    user_requested: bool

    def __post_init__(self) -> None:
        if self.status not in EXPERIMENT_SELECTION_STATUSES:
            raise ValueError(f"unknown experiment status {self.status!r}")
        if self.status == "selected":
            if self.reason is not None:
                raise ValueError("a selected experiment carries no reason")
        elif self.reason not in EXPERIMENT_SELECTION_REASONS:
            raise ValueError(
                f"unknown experiment selection reason {self.reason!r}; "
                f"expected {EXPERIMENT_SELECTION_REASONS}"
            )


@dataclass(frozen=True)
class NucleusSelectionRecord:
    """Which experiment(s) fed one nucleus, and how they were chosen.

    ``policy`` is one of :data:`SELECTION_POLICIES`: ``single`` (one 1D
    candidate), ``default_deterministic`` (several candidates, first by label
    kept), ``explicit_single`` / ``explicit_multi`` (user-requested).
    ``combined`` is true only when several experiments were explicitly
    combined into the nucleus; ``processor_id`` names the registered
    per-nucleus processor, or ``None`` when no processor is registered.
    """

    element: str
    nucleus_label: str
    selected_labels: tuple[str, ...]
    policy: str
    combined: bool
    processor_id: str | None

    def __post_init__(self) -> None:
        if self.policy not in SELECTION_POLICIES:
            raise ValueError(f"unknown selection policy {self.policy!r}")
        if not self.selected_labels:
            raise ValueError("a nucleus selection record needs at least one selected label")
        if self.combined != (len(self.selected_labels) > 1):
            raise ValueError(
                f"combined={self.combined!r} inconsistent with "
                f"{len(self.selected_labels)} selected label(s)"
            )

    def to_dict(self) -> dict[str, object]:
        """JSON-safe selection record."""
        return {
            "element": self.element,
            "nucleus_label": self.nucleus_label,
            "selected_labels": list(self.selected_labels),
            "policy": self.policy,
            "combined": self.combined,
            "processor_id": self.processor_id,
        }


@dataclass(frozen=True)
class ExperimentSelectionPlan:
    """Deterministic plan for which 1D experiment(s) feed which nucleus."""

    experiments: tuple[ExperimentSelectionRecord, ...]
    nuclei: tuple[NucleusSelectionRecord, ...]

    @property
    def selected_labels(self) -> tuple[str, ...]:
        """Labels of the experiments selected for processing."""
        return tuple(record.label for record in self.experiments if record.status == "selected")


def _experiment_label(exp_dir: Path, root: Path | None) -> str:
    """Tree-relative POSIX label for one experiment directory."""
    if root is not None:
        try:
            relative = exp_dir.relative_to(Path(root))
        except ValueError:
            relative = None
        if relative is not None:
            label = relative.as_posix()
            return label if label != "." else Path(root).name
    return exp_dir.name


def _selection_keys(
    select_experiments: Mapping[str, object],
) -> dict[str, tuple[str, ...]]:
    """Normalize selection keys to elements and validate token containers."""
    normalized: dict[str, tuple[str, ...]] = {}
    for key, raw in select_experiments.items():
        element = element_of_nucleus(str(key))
        if not element:
            raise ExperimentSelectionError(
                "unknown_nucleus", f"selection key {key!r} names no nucleus"
            )
        if isinstance(raw, str):
            tokens = (raw.strip(),)
        elif isinstance(raw, (list, tuple)):
            tokens = tuple(str(token).strip() for token in raw)
        else:
            raise ExperimentSelectionError(
                "unknown_experiment",
                f"selection for {key!r} must be a label or a list of labels, "
                f"got {type(raw).__name__}",
            )
        if not tokens or any(not token for token in tokens):
            raise ExperimentSelectionError("unknown_experiment", f"selection for {key!r} is empty")
        if element in normalized:
            raise ExperimentSelectionError(
                "duplicate_experiment",
                f"selection for element {element!r} was given more than once",
            )
        normalized[element] = tokens
    return normalized


def _match_experiment(
    token: str,
    entries: Sequence[_ExperimentEntry],
) -> _ExperimentEntry:
    """Resolve one selector token by directory name or tree-relative label."""
    matches = [
        entry for entry in entries if token == entry.label or token == Path(entry.label).name
    ]
    if not matches:
        available = sorted(entry.label for entry in entries)
        raise ExperimentSelectionError(
            "unknown_experiment", f"{token!r} matches no experiment; available: {available}"
        )
    if len(matches) > 1:
        raise ExperimentSelectionError(
            "ambiguous_experiment",
            f"{token!r} matches several experiments: {sorted(entry.label for entry in matches)}",
        )
    return matches[0]


@dataclass(frozen=True)
class _ExperimentEntry:
    """Internal probe result for one discovered experiment directory."""

    label: str
    directory: Path
    nucleus: str
    element: str
    reject_reason: str | None


def plan_experiment_selection(
    exp_dirs: Sequence[str | Path],
    *,
    root: str | Path | None = None,
    select_experiments: Mapping[str, str | Sequence[str]] | None = None,
) -> ExperimentSelectionPlan:
    """Choose which 1D experiment(s) feed which nucleus (todo 45 / G10).

    Default (``select_experiments=None``): ONE experiment per nucleus, picked
    deterministically as the first by label (``default_deterministic`` when
    several 1D experiments compete, ``single`` when only one exists) — peaks
    of same-nucleus experiments are never concatenated implicitly.

    Explicit mapping keys are nucleus labels or elements (``"1H"``/``"H"``);
    values select one experiment by directory name or tree-relative label, or
    several to combine explicitly (``explicit_multi``). Unknown labels,
    ambiguous basenames, nucleus mismatches and explicitly-selected 2D
    experiments raise typed errors (:class:`ExperimentSelectionError` /
    :class:`Not1DExperimentError`); 2D experiments are never processed as 1D.
    """
    entries: list[_ExperimentEntry] = []
    seen_labels: set[str] = set()
    for raw_dir in exp_dirs:
        directory = Path(raw_dir)
        label = _experiment_label(directory, Path(root) if root is not None else None)
        if label in seen_labels:
            raise ExperimentSelectionError(
                "duplicate_experiment", f"duplicate experiment label {label!r}"
            )
        seen_labels.add(label)
        nucleus = spectrum_probe_nucleus(directory)
        element = normalize_symbol(nucleus.lstrip("0123456789")) if nucleus else ""
        dimension_reason = not_1d_reason(directory)
        if not element:
            entries.append(
                _ExperimentEntry(
                    label=label,
                    directory=directory,
                    nucleus=nucleus,
                    element="",
                    reject_reason=dimension_reason or "nucleus_unreadable",
                )
            )
        elif dimension_reason is not None:
            entries.append(
                _ExperimentEntry(
                    label=label,
                    directory=directory,
                    nucleus=nucleus,
                    element=element,
                    reject_reason=dimension_reason,
                )
            )
        else:
            entries.append(
                _ExperimentEntry(
                    label=label,
                    directory=directory,
                    nucleus=nucleus,
                    element=element,
                    reject_reason=None,
                )
            )

    requested = _selection_keys(select_experiments or {})
    candidates_by_element: dict[str, list[_ExperimentEntry]] = {}
    for entry in entries:
        if entry.element and entry.reject_reason is None:
            candidates_by_element.setdefault(entry.element, []).append(entry)
    for key_element in requested:
        if key_element not in candidates_by_element:
            raise ExperimentSelectionError(
                "unknown_nucleus",
                f"selection key element {key_element!r} has no 1D experiment; "
                f"available: {sorted(candidates_by_element)}",
            )

    chosen_labels: set[str] = set()
    user_requested: set[str] = set()
    nuclei: list[NucleusSelectionRecord] = []
    for element in sorted(candidates_by_element):
        candidates = sorted(candidates_by_element[element], key=lambda entry: entry.label)
        tokens = requested.get(element)
        if tokens is None:
            chosen = [candidates[0]]
            policy = "single" if len(candidates) == 1 else "default_deterministic"
        else:
            chosen = []
            for token in tokens:
                match = _match_experiment(token, entries)
                if match.reject_reason is not None:
                    if match.reject_reason in NOT_1D_REASONS:
                        raise Not1DExperimentError(
                            match.directory,
                            match.reject_reason,
                            detail=f"explicitly selected via {token!r}",
                        )
                    raise ExperimentSelectionError(
                        match.reject_reason,
                        f"explicitly selected experiment {token!r} has no readable nucleus",
                    )
                if match.element != element:
                    raise ExperimentSelectionError(
                        "nucleus_mismatch",
                        f"selection for {element!r} names {token!r} which belongs to "
                        f"element {match.element!r}",
                    )
                if match.label in user_requested:
                    raise ExperimentSelectionError(
                        "duplicate_experiment", f"experiment {token!r} selected twice"
                    )
                chosen.append(match)
                user_requested.add(match.label)
            policy = "explicit_single" if len(chosen) == 1 else "explicit_multi"
        selected_labels = tuple(sorted(entry.label for entry in chosen))
        chosen_labels.update(selected_labels)
        descriptor = None
        try:
            descriptor = lookup_processor(element)
        except NucleusProcessorError as exc:
            logger.warning("processor registry lookup failed for %s: %s", element, exc)
        nuclei.append(
            NucleusSelectionRecord(
                element=element,
                nucleus_label=chosen[0].nucleus,
                selected_labels=selected_labels,
                policy=policy,
                combined=len(selected_labels) > 1,
                processor_id=descriptor.processor_id if descriptor is not None else None,
            )
        )

    records: list[ExperimentSelectionRecord] = []
    for entry in entries:
        if entry.label in chosen_labels:
            records.append(
                ExperimentSelectionRecord(
                    label=entry.label,
                    nucleus=entry.nucleus,
                    element=entry.element,
                    status="selected",
                    reason=None,
                    user_requested=entry.label in user_requested,
                )
            )
        elif entry.reject_reason is not None:
            records.append(
                ExperimentSelectionRecord(
                    label=entry.label,
                    nucleus=entry.nucleus,
                    element=entry.element,
                    status="rejected",
                    reason=entry.reject_reason,
                    user_requested=entry.label in user_requested,
                )
            )
        else:
            reason = "not_requested" if entry.element in requested else "default_deterministic"
            records.append(
                ExperimentSelectionRecord(
                    label=entry.label,
                    nucleus=entry.nucleus,
                    element=entry.element,
                    status="not_selected",
                    reason=reason,
                    user_requested=entry.label in user_requested,
                )
            )
    return ExperimentSelectionPlan(experiments=tuple(records), nuclei=tuple(nuclei))


# ---------------------------------------------------------------------------
# Processing chain
# ---------------------------------------------------------------------------


def _acqus_text(value: object) -> str | None:
    """Normalize an acqus string parameter (strip ``<>``); ``None`` when empty."""
    if value is None:
        return None
    text = str(value).strip().strip("<>").strip()
    return text or None


def _acqus_float(value: object) -> float | None:
    """Parse an acqus numeric parameter; ``None`` when absent/non-numeric."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _acqus_int(value: object) -> int | None:
    """Parse an acqus integer parameter; ``None`` when absent/non-numeric."""
    number = _acqus_float(value)
    return int(number) if number is not None else None


def _acqus_params(dic: dict) -> dict:
    """Return the direct-dimension acquisition parameters + metadata."""
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
        "solvent": _acqus_text(acqus.get("SOLVENT")),
        "temperature_k": _acqus_float(acqus.get("TE")),
        "pulse_program": _acqus_text(acqus.get("PULPROG")),
        "group_delay_points": _acqus_float(acqus.get("GRPDLY")),
        "dspfvs": _acqus_int(acqus.get("DSPFVS")),
        "sr_value": _acqus_float(acqus.get("SR")),
    }


def _fft_pipeline(
    fid: np.ndarray,
    sw_hz: float,
    lb_hz: float,
) -> tuple[np.ndarray, int]:
    """Apodize → first-point halve → zero-fill → FFT (pure numpy).

    nmrglue's ``proc_base.em`` treats ``lb`` as a *per-point* decay (not
    Hz), so the exponential window is applied explicitly here.

    Returns ``(spectrum, zero_filled_points)`` — the final complex point
    count is returned so the caller can record it in the provenance.
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
    return np.fft.fftshift(np.fft.fft(data)), int(size)


def _auto_phase(spectrum: np.ndarray) -> tuple[np.ndarray, str]:
    """Automatic phase correction via nmrglue (peak_minima → acme → none).

    Returns ``(phased_spectrum, method)`` — the method actually applied
    (``"peak_minima"`` / ``"acme"``), or ``"unphased"`` when every
    optimizer failed. The method is recorded in the provenance so a failed
    phase is visible (todo 42 gates on it instead of silently continuing).
    """
    ng = _import_nmrglue()
    from nmrglue.process import proc_autophase  # noqa: PLC0415

    _ = ng
    for method in ("peak_minima", "acme"):
        try:
            with contextlib.redirect_stdout(_io.StringIO()):
                phased = proc_autophase.autops(spectrum, method)
            if np.isfinite(phased.real).all() and phased.real.max() > 0:
                return np.asarray(phased, dtype=np.complex128), method
        except Exception as exc:  # optimizer failure — try next method
            logger.debug("autops(%s) failed: %s", method, exc)
    logger.warning("Automatic phase correction failed; using unphased spectrum")
    return spectrum, "unphased"


def _baseline_correct(
    real: np.ndarray, window_fraction: float = _BASELINE_WINDOW_FRACTION
) -> np.ndarray:
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


def _region_areas(real: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Area of each peak integrated between the midpoints to its neighbours.

    Returned in the original peak order (not sorted by position).
    """
    if len(indices) == 0:
        return np.zeros(0)
    order = np.argsort(indices)
    sorted_idx = indices[order]
    areas = np.zeros(len(sorted_idx))
    n = real.shape[-1]
    for pos, idx in enumerate(sorted_idx):
        left = 0 if pos == 0 else (sorted_idx[pos - 1] + idx) // 2
        right = n - 1 if pos == len(sorted_idx) - 1 else (idx + sorted_idx[pos + 1]) // 2
        region = real[left : right + 1]
        areas[pos] = float(np.sum(np.clip(region, 0.0, None)))
    out = np.zeros(len(indices))
    out[order] = areas
    return out


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
    areas = _region_areas(real, indices)
    min_area = float(areas.min()) if areas.size else 1.0
    if min_area <= 0:
        return [1] * len(indices)
    mults = np.maximum(1, np.round(areas / min_area).astype(int))
    return mults.tolist()


def _linewidth_hz(real: np.ndarray, index: int, sw_hz: float) -> float | None:
    """FWHM estimate (Hz) of one picked peak, or ``None`` when not measurable."""
    n = real.shape[-1]
    peak = float(real[index])
    if n < 3 or peak <= 0:
        return None
    half = peak / 2.0
    left = index
    while left > 0 and real[left - 1] > half:
        left -= 1
    right = index
    while right < n - 1 and real[right + 1] > half:
        right += 1
    return max(right - left, 1) * float(sw_hz) / n


def _estimate_quality(
    real: np.ndarray,
    noise: float,
    indices: np.ndarray,
    heights: np.ndarray,
    sw_hz: float,
) -> ProcessingQuality:
    """Derive the processing quality metrics from the corrected spectrum (G10).

    Baseline RMS comes from the spectrum edges (same edge fraction as the
    noise estimate); S/N and the linewidth estimate come from the tallest
    picked peak and stay ``None`` when no peak was picked.
    """
    n = real.shape[-1]
    edge = max(n // 10, 16)
    samples = np.concatenate([real[:edge], real[-edge:]])
    baseline_rms = float(np.sqrt(np.mean(np.square(samples))))
    if len(indices):
        tallest = int(indices[int(np.argmax(heights))])
        snr = float(heights.max() / noise)
        linewidth = _linewidth_hz(real, tallest, sw_hz)
    else:
        snr = None
        linewidth = None
    return ProcessingQuality(snr=snr, linewidth_hz=linewidth, baseline_rms=baseline_rms)


def process_bruker_experiment(
    exp_dir: str | Path,
    reference_ppm: float | None = None,
    reference_window_ppm: float | None = None,
    lb_hz: float | None = None,
    snr_threshold: float = _DEFAULT_SNR_THRESHOLD,
    *,
    trace_sink: list[ProcessedTrace] | None = None,
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
        trace_sink: Optional collector; when given, the dense processed
            :class:`ProcessedTrace` of this experiment is appended to it
            (the tree path threads it to the processors, todo 45 handoff).
            The collector keeps this the single processing seam.

    Raises:
        ImportError: When nmrglue is not installed.
        ValueError: When *exp_dir* is not a Bruker experiment.
        Not1DExperimentError: When the experiment is 2D/3D (``ser`` file,
            ``acqu2s`` or ``PARMODE`` != 0) — never processed as 1D.
    """
    exp_dir = Path(exp_dir)
    if not _is_bruker_experiment(exp_dir):
        raise ValueError(f"Not a Bruker experiment directory: {exp_dir}")
    dimension_reason = not_1d_reason(exp_dir)
    if dimension_reason is not None:
        raise Not1DExperimentError(exp_dir, dimension_reason)

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

    fid = np.asarray(data)
    spectrum, zero_fill_points = _fft_pipeline(fid, sw_hz, lb)
    spectrum, phase_method = _auto_phase(spectrum)
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
    areas = _region_areas(real, indices)

    peaks = [
        ExperimentalPeak(
            shift_ppm=round(float(ppm), 4),
            element=element,
            atom_label=None,
            multiplicity=int(mult),
        )
        for ppm, mult in zip(ppm_values, multiplicities)
    ]
    lines = [
        SpectralLine(
            position_ppm=round(float(ppm), 4),
            intensity=float(height),
            width_hz=_linewidth_hz(real, int(idx), sw_hz),
            integral=float(area),
            index=position,
        )
        for position, (idx, ppm, height, area) in enumerate(
            zip(indices, ppm_values, heights, areas)
        )
    ]

    obs_mhz = params["obs_mhz"]
    acquisition = AcquisitionSpectrum(
        spectrometer="Bruker",
        nucleus=nucleus,
        frequency_mhz=obs_mhz,
        solvent=params["solvent"],
        temperature_k=params["temperature_k"],
        pulse_program=params["pulse_program"],
        point_count=int(fid.shape[-1]),
        spectral_width_hz=sw_hz,
        carrier_ppm=(params["car_hz"] / obs_mhz) if obs_mhz else None,
        group_delay_points=params["group_delay_points"],
        dspfvs=params["dspfvs"],
        spectrometer_reference=params["sr_value"],
        source_dir=str(exp_dir),
    )
    processing = ProcessingProvenance(
        apodization="exponential",
        lb_hz=float(lb),
        zero_fill_points=zero_fill_points,
        zero_fill_factor=(
            float(zero_fill_points) / float(fid.shape[-1]) if fid.shape[-1] else None
        ),
        phase_method=phase_method,
        baseline_method="morphological_grey_opening",
        baseline_window_fraction=_BASELINE_WINDOW_FRACTION,
        reference_method=("manual_anchor" if reference_shift is not None else "spectrometer_sr"),
        reference_ppm=reference_ppm,
        applied_shift_ppm=reference_shift,
        digital_filter_compensation=_DIGITAL_FILTER_COMPENSATION,
    )
    quality = _estimate_quality(real, noise, indices, heights, sw_hz)
    assessment = assess_processing(processing, acquisition)
    if assessment is not None and not assessment.is_ok:
        logger.warning(
            "Bruker %s processing gate %s (%s) from %s",
            nucleus,
            assessment.status,
            ", ".join(assessment.reasons),
            exp_dir,
        )
    logger.info(
        "Bruker %s: picked %d peak(s) (%s, noise=%.3g, lb=%.2f Hz, phase=%s) from %s",
        nucleus,
        len(peaks),
        exp_dir.name,
        noise,
        lb,
        phase_method,
        exp_dir,
    )
    spectrum_record = ProcessedSpectrum(
        nucleus=nucleus,
        element=element,
        peaks=peaks,
        noise=noise,
        reference_shift=reference_shift,
        source_dir=str(exp_dir),
        acquisition=acquisition,
        processing=processing,
        quality=quality,
        lines=lines,
        assessment=assessment,
    )
    if trace_sink is not None:
        trace_ppm = ppm_scale if reference_shift is None else ppm_scale + reference_shift
        trace_sink.append(
            ProcessedTrace(
                source_dir=str(exp_dir),
                ppm=np.asarray(trace_ppm, dtype=np.float64),
                intensity=np.asarray(real, dtype=np.float64),
                noise=float(noise),
            )
        )
    return spectrum_record


# ---------------------------------------------------------------------------
# Tree / zip entry point
# ---------------------------------------------------------------------------


def process_bruker_tree(
    path: str | Path,
    references: dict[str, float] | None = None,
    lb_hz: float | None = None,
    snr_threshold: float = _DEFAULT_SNR_THRESHOLD,
    extract_dir: str | Path | None = None,
    select_experiments: Mapping[str, str | Sequence[str]] | None = None,
) -> BrukerProcessResult:
    """Process a Bruker directory tree (or zip archive) into an ExperimentalNmr.

    Same-nucleus experiments are never concatenated blindly: for each nucleus
    exactly one 1D experiment is selected by default (deterministically, the
    first by label), and several experiments combine only when
    *select_experiments* explicitly requests it — the combination policy and
    which experiments fed which nucleus are recorded in
    :attr:`BrukerProcessResult.selection`. 2D/non-1D experiments are rejected
    per experiment (:attr:`BrukerProcessResult.experiments` carries the
    closed reason) and never processed; explicitly selecting one raises
    :class:`Not1DExperimentError`.

    Args:
        path: Root directory (§6.3 layout, single experiment, or expno
            tree) or a ``.zip`` archive of one.
        references: Optional manual ppm references per nucleus label or
            element (``{"1H": 7.26}`` or ``{"H": 7.26}``).
        lb_hz / snr_threshold: Forwarded to :func:`process_bruker_experiment`.
        extract_dir: Where to extract zip archives (default: a fresh
            temporary directory — the caller is responsible for cleanup).
        select_experiments: Optional explicit per-nucleus experiment
            selection keyed by nucleus label or element (``"1H"``/``"H"``);
            values are one experiment directory name / tree-relative label
            or a sequence of labels to combine explicitly.

    Returns:
        :class:`BrukerProcessResult` with an unassigned
        :class:`ExperimentalNmr` (peaks grouped by element), per-nucleus
        selection records, per-experiment dispositions, and the dense
        processed traces of the selected experiments.
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

    plan = plan_experiment_selection(exp_dirs, root=root, select_experiments=select_experiments)
    if not plan.selected_labels:
        rejected = {
            record.label: record.reason
            for record in plan.experiments
            if record.status == "rejected"
        }
        not_1d = {label: reason for label, reason in rejected.items() if reason in NOT_1D_REASONS}
        if not_1d and len(not_1d) == len(plan.experiments):
            first_label = next(iter(not_1d))
            raise Not1DExperimentError(
                first_label,
                not_1d[first_label],
                detail=f"all {len(not_1d)} experiment(s) under {path} are non-1D",
            )
        raise ValueError(f"No usable 1D Bruker experiment under {path}: rejected {rejected}")

    dirs_by_label = {_experiment_label(Path(exp_dir), root): Path(exp_dir) for exp_dir in exp_dirs}
    selection_by_element = {record.element: record for record in plan.nuclei}

    references = references or {}
    spectra: list[ProcessedSpectrum] = []
    traces: list[ProcessedTrace] = []
    peaks_by_element: dict[str, list[ExperimentalPeak]] = {}
    for label in plan.selected_labels:
        exp_dir = dirs_by_label[label]
        trace_sink: list[ProcessedTrace] = []
        spectrum = process_bruker_experiment(
            exp_dir,
            reference_ppm=_reference_for(spectrum_probe_nucleus(exp_dir), references),
            lb_hz=lb_hz,
            snr_threshold=snr_threshold,
            trace_sink=trace_sink,
        )
        spectra.append(spectrum)
        traces.extend(trace_sink)
        if not spectrum.formal_usable:
            continue
        if spectrum.peaks:
            selection = selection_by_element.get(spectrum.element)
            if (
                selection is not None
                and selection.combined
                and peaks_by_element.get(spectrum.element)
            ):
                peaks_by_element[spectrum.element].extend(spectrum.peaks)
            else:
                peaks_by_element[spectrum.element] = list(spectrum.peaks)

    excluded = [spectrum for spectrum in spectra if not spectrum.formal_usable]
    if excluded:
        logger.warning(
            "Bruker processing gate excluded %d/%d spectrum(s) from the formal peak list: %s",
            len(excluded),
            len(spectra),
            {
                spectrum.source_dir: list(spectrum.assessment.reasons)
                for spectrum in excluded
                if spectrum.assessment is not None
            },
        )
    if not peaks_by_element:
        raise ValueError(
            f"Bruker processing picked no peaks under {path} "
            f"({len(plan.selected_labels)} experiment(s) selected of "
            f"{len(exp_dirs)} scanned, {len(excluded)} excluded "
            "by the processing gate) — check SNR/phase."
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
        selection=plan.nuclei,
        experiments=plan.experiments,
        traces=tuple(traces),
    )


def spectrum_probe_nucleus(exp_dir: str | Path) -> str:
    """Read just the ``NUC1`` nucleus label from an experiment's ``acqus``."""
    return _probe_acqus_text(exp_dir, "NUC1") or ""


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
    "EXPERIMENT_SELECTION_REASONS",
    "EXPERIMENT_SELECTION_STATUSES",
    "NOT_1D_REASONS",
    "SELECTION_ERROR_REASONS",
    "SELECTION_POLICIES",
    "AcquisitionSpectrum",
    "BrukerProcessResult",
    "ExperimentSelectionError",
    "ExperimentSelectionPlan",
    "ExperimentSelectionRecord",
    "Not1DExperimentError",
    "NucleusSelectionRecord",
    "ProcessedSpectrum",
    "ProcessedTrace",
    "ProcessingProvenance",
    "ProcessingQuality",
    "SpectralLine",
    "bruker_result_to_text",
    "find_bruker_experiments",
    "not_1d_reason",
    "plan_experiment_selection",
    "process_bruker_experiment",
    "process_bruker_tree",
    "spectrum_probe_nucleus",
]
